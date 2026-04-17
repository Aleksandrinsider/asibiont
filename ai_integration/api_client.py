"""
Единый API-клиент для всех внешних сервисов.

Преимущества:
- Одна aiohttp.ClientSession вместо 20+ создаваемых ad-hoc
- Встроенный кэш (Redis + in-memory fallback)
- Rate-limiting для каждого API
- Retry с экспоненциальным backoff
- Логирование расхода API-вызовов
- Единая точка конфигурации
"""

import asyncio
import aiohttp
import json
import logging
import time
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from config import (
    OPENWEATHERMAP_API_KEY,
    NEWSAPI_API_KEY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
)

logger = logging.getLogger(__name__)

# Пул User-Agent строк для ротации — ищем как живой браузер, избегаем bot-detection
import random as _random
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
]

def _rand_ua() -> str:
    return _random.choice(_UA_POOL)

# Запись ошибок сервисов (ленивая загрузка — избегаем циклических импортов)
try:
    from .service_health import record_error as _rec_err, clear_error as _clr_err
except ImportError:
    def _rec_err(*a, **kw): pass
    def _clr_err(*a, **kw): pass

# ============================================================================
# КЭШИРОВАНИЕ
# ============================================================================

class APICache:
    """Async-совместимый кэш с TTL (in-memory + Redis fallback)"""
    
    def __init__(self):
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._redis = None
        self._init_redis()
    
    def _init_redis(self):
        """Подключаемся к Redis из уже инициализированного клиента в utils"""
        try:
            from .utils import redis_client
            self._redis = redis_client
        except Exception:
            self._redis = None
    
    def _cache_key(self, prefix: str, params: dict) -> str:
        """Генерирует уникальный ключ кэша"""
        raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
        h = hashlib.md5(raw.encode()).hexdigest()[:12]
        return f"apicache:{prefix}:{h}"
    
    async def get(self, prefix: str, params: dict) -> Optional[Any]:
        """Получить данные из кэша"""
        key = self._cache_key(prefix, params)
        
        # 1. In-memory
        entry = self._memory.get(key)
        if entry and entry['expires'] > time.time():
            logger.debug(f"[CACHE HIT] memory:{key}")
            return entry['data']
        
        # 2. Redis
        if self._redis:
            try:
                raw = self._redis.get(key)
                if raw:
                    logger.debug(f"[CACHE HIT] redis:{key}")
                    data = json.loads(raw)
                    # Обновляем in-memory
                    self._memory[key] = {'data': data, 'expires': time.time() + 300}
                    return data
            except Exception as e:
                logger.warning(f"[CACHE] Redis get error: {e}")
        
        return None
    
    async def set(self, prefix: str, params: dict, data: Any, ttl_seconds: int = 300):
        """Сохранить данные в кэш"""
        key = self._cache_key(prefix, params)
        
        # 1. In-memory
        self._memory[key] = {
            'data': data,
            'expires': time.time() + ttl_seconds
        }
        
        # 2. Redis
        if self._redis:
            try:
                self._redis.setex(key, ttl_seconds, json.dumps(data, ensure_ascii=False))
            except Exception as e:
                logger.warning(f"[CACHE] Redis set error: {e}")
    
    def cleanup(self):
        """Удалить просроченные записи из in-memory кэша"""
        now = time.time()
        expired = [k for k, v in self._memory.items() if v['expires'] < now]
        for k in expired:
            del self._memory[k]


# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    """Простой rate-limiter на основе скользящего окна"""
    
    def __init__(self):
        self._calls: Dict[str, List[float]] = {}
        self._limits: Dict[str, tuple] = {
            # (max_calls, window_seconds)
            'ddg': (200, 86400),             # DuckDuckGo — бесплатный
            'newsapi': (40, 86400),          # 100/день (NewsAPI Free), большой запас чтобы не 429
            'openweathermap': (55, 60),      # 60/мин (OWM Free)
            'deepseek': (50, 60),            # DeepSeek — мягкий лимит
        }
    
    async def acquire(self, api_name: str) -> bool:
        """Проверяет, можно ли сделать запрос. Возвращает True если ОК."""
        if api_name not in self._limits:
            return True
        
        max_calls, window = self._limits[api_name]
        now = time.time()
        
        if api_name not in self._calls:
            self._calls[api_name] = []
        
        # Чистим старые записи
        self._calls[api_name] = [t for t in self._calls[api_name] if t > now - window]
        
        if len(self._calls[api_name]) >= max_calls:
            logger.warning(f"[RATE_LIMIT] {api_name}: {len(self._calls[api_name])}/{max_calls} calls in {window}s window")
            return False
        
        self._calls[api_name].append(now)
        return True
    
    def get_usage(self, api_name: str) -> dict:
        """Получить статистику использования"""
        if api_name not in self._limits:
            return {'used': 0, 'limit': 'unlimited'}
        
        max_calls, window = self._limits[api_name]
        now = time.time()
        
        calls = self._calls.get(api_name, [])
        recent = [t for t in calls if t > now - window]
        
        return {
            'used': len(recent),
            'limit': max_calls,
            'window_seconds': window,
            'remaining': max_calls - len(recent)
        }


# ============================================================================
# ЕДИНЫЙ API КЛИЕНТ
# ============================================================================

class ExternalAPIClient:
    """
    Единый клиент для всех внешних API с кэшированием,
    rate-limiting и переиспользованием соединений.
    """
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self.cache = APICache()
        self.rate_limiter = RateLimiter()
        self._api_call_count: Dict[str, int] = {}
        # Backoff-timestamps after 429 (api_name -> unix timestamp "blocked until")
        self._backoff_until: Dict[str, float] = {}
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Ленивая инициализация единой сессии"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20, connect=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        """Закрыть сессию (вызвать при завершении приложения)"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    def _track_call(self, api_name: str):
        """Счётчик вызовов для мониторинга"""
        self._api_call_count[api_name] = self._api_call_count.get(api_name, 0) + 1
    
    def get_stats(self) -> dict:
        """Общая статистика по API"""
        stats = {}
        for api in ['ddg', 'newsapi', 'openweathermap', 'deepseek']:
            stats[api] = {
                'total_calls': self._api_call_count.get(api, 0),
                'rate_limit': self.rate_limiter.get_usage(api)
            }
        return stats

    # ========================================================================
    # GITHUB API (бесплатный поиск пользователей, до 60 req/h без токена)
    # ========================================================================

    async def github_search_emails(
        self,
        query: str,
        max_users: int = 30,
        cache_ttl: int = 3600,
        page: int = 1,
        github_token: str = None,
    ) -> List[dict]:
        """
        Поиск пользователей GitHub по теме и сбор их email из коммитов + профилей.
        
        Стратегия:
        1. Поиск пользователей через GitHub Search API
        2. Для каждого пользователя: проверяем events/public → PushEvent → commit email
        3. Fallback на profile email
        
        GitHub API Rate Limits (бесплатно):
        - Без токена: 60 req/h (поиск: 10 req/min)
        - С GITHUB_TOKEN: 5000 req/h (поиск: 30 req/min)
        
        Returns:
            Список [{email, name, company, bio, url, repos}]
        """
        import os
        cache_params = {'q': query, 'max': max_users, 'engine': 'github_v2', 'page': page}
        
        cached = await self.cache.get('github', cache_params)
        if cached is not None:
            return cached
        
        # github_token: явно переданный (из user_api_keys агента) > env переменная
        github_token = github_token or os.environ.get('GITHUB_TOKEN', '')
        headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'ASI-Biont-LeadFinder/1.0',
        }
        if github_token:
            headers['Authorization'] = f'Bearer {github_token}'
        
        # Без токена ограничиваем до 20 пользователей (экономим rate limit, но берём больше для результативности)
        effective_max = max_users if github_token else min(max_users, 20)
        
        session = await self._get_session()
        found_leads = []
        seen_emails = set()
        _rate_limited = False
        
        try:
            # 1. Поиск пользователей
            search_url = 'https://api.github.com/search/users'
            params = {
                'q': query,
                'per_page': min(effective_max, 30),
                'sort': 'followers',
                'order': 'desc',
                'page': page,
            }
            
            async with session.get(search_url, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                self._track_call('github')
                if resp.status == 403:
                    logger.warning(f"[GITHUB] Rate limited on search for '{query[:50]}' "
                                   f"(token: {'yes' if github_token else 'NO — add GITHUB_TOKEN for 5000 req/hr'})")
                    return []
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"[GITHUB] Search error {resp.status}: {body[:200]}")
                    return []
                data = await resp.json()
            
            users = data.get('items', [])[:effective_max]
            # Фильтруем собственный аккаунт платформы из результатов
            _OWN_GITHUB_LOGINS = {'aleksandrinsider', 'asibiont'}
            users = [u for u in users if u.get('login', '').lower() not in _OWN_GITHUB_LOGINS]
            logger.info(f"[GITHUB] Search '{query[:50]}' → {len(users)} users "
                       f"(total: {data.get('total_count', 0)}, token: {'yes' if github_token else 'no'})")
            
            if not users:
                await self.cache.set('github', cache_params, [], cache_ttl)
                return []
            
            # 2. Для каждого user: fetch events/public → извлекаем commit email
            import asyncio as _aio_gh
            
            async def _get_user_email(login: str) -> Optional[dict]:
                """Получить email из events (commit email) или профиля."""
                commit_email = None
                profile_data = {}
                
                try:
                    # 2a. Events API → PushEvent → commit author email
                    events_url = f'https://api.github.com/users/{login}/events/public'
                    async with session.get(events_url, headers=headers,
                                           params={'per_page': 10},
                                           timeout=aiohttp.ClientTimeout(total=8)) as r:
                        self._track_call('github')
                        if r.status == 403:
                            return {'_rate_limited': True}
                        if r.status == 200:
                            events = await r.json()
                            for event in events:
                                if event.get('type') == 'PushEvent':
                                    commits = event.get('payload', {}).get('commits', [])
                                    for commit in commits:
                                        author = commit.get('author', {})
                                        em = (author.get('email') or '').lower().strip()
                                        if (em and '@' in em 
                                            and 'noreply' not in em 
                                            and '@users.noreply.github.com' not in em
                                            and not em.endswith('@github.com')):
                                            commit_email = em
                                            profile_data['name'] = author.get('name', '')
                                            break
                                    if commit_email:
                                        break
                except Exception as e:
                    logger.debug(f"[GITHUB] Events fetch failed for {login}: {e}")
                
                # 2b. Если нет commit email, пробуем профиль
                if not commit_email:
                    try:
                        user_url = f'https://api.github.com/users/{login}'
                        async with session.get(user_url, headers=headers,
                                               timeout=aiohttp.ClientTimeout(total=8)) as r:
                            self._track_call('github')
                            if r.status == 403:
                                return {'_rate_limited': True}
                            if r.status == 200:
                                profile = await r.json()
                                profile_data = {
                                    'name': profile.get('name') or login,
                                    'company': (profile.get('company') or '').strip().lstrip('@'),
                                    'bio': (profile.get('bio') or '')[:200],
                                    'html_url': profile.get('html_url', ''),
                                    'blog': profile.get('blog', ''),
                                    'repos': profile.get('public_repos', 0),
                                    'followers': profile.get('followers', 0),
                                    'location': profile.get('location', ''),
                                }
                                em = (profile.get('email') or '').lower().strip()
                                if em and '@' in em and 'noreply' not in em:
                                    commit_email = em
                    except Exception:
                        pass
                
                if not commit_email:
                    return None
                
                return {
                    'email': commit_email,
                    'name': profile_data.get('name', login),
                    'company': profile_data.get('company', ''),
                    'bio': profile_data.get('bio', ''),
                    'url': profile_data.get('html_url', f'https://github.com/{login}'),
                    'blog': profile_data.get('blog', ''),
                    'repos': profile_data.get('repos', 0),
                    'followers': profile_data.get('followers', 0),
                    'location': profile_data.get('location', ''),
                }
            
            # Обрабатываем батчами по 5 (каждый user = 1-2 API calls)
            batch_size = 5
            for batch_start in range(0, len(users), batch_size):
                if _rate_limited:
                    break
                    
                batch = users[batch_start:batch_start + batch_size]
                results = await _aio_gh.gather(
                    *[_get_user_email(u['login']) for u in batch],
                    return_exceptions=True
                )
                
                for result in results:
                    if isinstance(result, Exception) or not result:
                        continue
                    if isinstance(result, dict) and result.get('_rate_limited'):
                        _rate_limited = True
                        break
                    em = result.get('email', '').lower()
                    if em and em not in seen_emails:
                        seen_emails.add(em)
                        found_leads.append(result)
                
                # Пауза между батчами
                if not _rate_limited and batch_start + batch_size < len(users):
                    await _aio_gh.sleep(2)
            
            logger.info(f"[GITHUB] Found {len(found_leads)} emails (commit+profile) for: {query[:50]}"
                       f"{' (rate limited)' if _rate_limited else ''}")
            await self.cache.set('github', cache_params, found_leads, cache_ttl)
            return found_leads
            
        except Exception as e:
            logger.error(f"[GITHUB] Error: {e}")
            return []

    async def github_multi_search(
        self,
        queries: List[str],
        max_users_per_query: int = 20,
        cache_ttl: int = 3600,
        page: int = 1,
        github_token: str = None,
    ) -> List[dict]:
        """
        Параллельный поиск по нескольким запросам GitHub.
        Дедуплицирует по email.
        """
        import asyncio as _aio_gm
        
        # Последовательно, чтобы не превысить rate limit
        all_leads = []
        seen_emails = set()
        
        for q in queries:
            results = await self.github_search_emails(q, max_users=max_users_per_query, cache_ttl=cache_ttl, page=page, github_token=github_token)
            for lead in results:
                email = lead['email'].lower()
                if email not in seen_emails:
                    seen_emails.add(email)
                    all_leads.append(lead)
            # Пауза между запросами (GitHub rate limit: 10 search/min)
            await _aio_gm.sleep(6)
        
        logger.info(f"[GITHUB_MULTI] {len(queries)} queries → {len(all_leads)} unique leads")
        return all_leads

    # ========================================================================
    # DUCKDUCKGO (бесплатный поиск, без API ключа)
    # ========================================================================
    # Глобальный семафор: не более 1 параллельного DDG-запроса (против rate limit)
    _ddg_semaphore: asyncio.Semaphore = None  # инициализируется лениво
    _ddg_last_request: float = 0.0  # timestamp последнего запроса
    _DDG_MIN_INTERVAL: float = 0.0  # без искусственной задержки — retry logic обрабатывает rate limit

    # Circuit breaker: Railway IP системно блокируется Yahoo (бэкенд DDG).
    # После _DDG_FAIL_THRESHOLD подряд идущих ошибок — не дёргаем Yahoo _DDG_SKIP_SECONDS.
    _ddg_fail_streak: int = 0
    _ddg_skip_until: float = 0.0
    _DDG_FAIL_THRESHOLD: int = 4
    _DDG_SKIP_SECONDS: float = 300.0  # 5 минут

    def _get_ddg_semaphore(self) -> asyncio.Semaphore:
        if self._ddg_semaphore is None:
            ExternalAPIClient._ddg_semaphore = asyncio.Semaphore(1)
        return self._ddg_semaphore

    def _ddg_available(self) -> bool:
        import time as _t
        return _t.time() >= ExternalAPIClient._ddg_skip_until

    def _ddg_record_fail(self) -> None:
        import time as _t
        ExternalAPIClient._ddg_fail_streak += 1
        if ExternalAPIClient._ddg_fail_streak >= ExternalAPIClient._DDG_FAIL_THRESHOLD:
            ExternalAPIClient._ddg_skip_until = _t.time() + ExternalAPIClient._DDG_SKIP_SECONDS
            logger.info(
                "[DDG] Circuit breaker открыт: пропускаем DDG/Yahoo на %.0fs "
                "после %d ошибок подряд",
                ExternalAPIClient._DDG_SKIP_SECONDS, ExternalAPIClient._ddg_fail_streak,
            )
            ExternalAPIClient._ddg_fail_streak = 0

    def _ddg_record_success(self) -> None:
        ExternalAPIClient._ddg_fail_streak = 0
        ExternalAPIClient._ddg_skip_until = 0.0

    async def duckduckgo_search(
        self,
        query: str,
        num: int = 10,
        region: str = "ru-ru",
        cache_ttl: int = 1800
    ) -> Optional[List[dict]]:
        """
        Бесплатный поиск через DuckDuckGo (без API ключа).

        Returns:
            Список результатов [{'title': ..., 'snippet': ..., 'link': ...}] или None
        """
        cache_params = {'q': query, 'num': num, 'region': region, 'engine': 'ddg'}

        # Проверяем кэш
        cached = await self.cache.get('ddg', cache_params)
        if cached is not None:
            return cached

        # Circuit breaker: DDG/Yahoo системно блокируется — не дёргаем пока открыт
        if not self._ddg_available():
            return None

        try:
            try:
                from ddgs import DDGS
            except ImportError:
                try:
                    from duckduckgo_search import DDGS  # legacy fallback
                except ImportError:
                    raise ImportError("ddgs package not installed. Run: pip install ddgs")
            import asyncio as _aio

            def _sync_search():
                import logging as _lg
                # Suppress ConnectError INFO lines from ddgs internals
                _lg.getLogger('ddgs').setLevel(_lg.WARNING)
                _lg.getLogger('ddgs.ddgs').setLevel(_lg.WARNING)
                try:
                    with DDGS(timeout=10) as ddgs:
                        raw = list(ddgs.text(query, region=region, max_results=num))
                        return [{
                            'title': r.get('title', ''),
                            'snippet': r.get('body', ''),
                            'link': r.get('href', ''),
                        } for r in raw]
                except Exception:
                    raise  # propagate to outer handler

            loop = _aio.get_running_loop()
            sem = self._get_ddg_semaphore()

            # Retry с экспоненциальным backoff при rate limit
            max_retries = 2
            base_delay = 2.0  # секунды
            for attempt in range(max_retries):
                async with sem:
                    try:
                        results = await _aio.wait_for(
                            loop.run_in_executor(None, _sync_search),
                            timeout=12.0
                        )
                        self._track_call('ddg')
                        await self.cache.set('ddg', cache_params, results, cache_ttl)
                        logger.info(f"[DDG] Found {len(results)} results for: {query[:50]}")
                        _clr_err('ddg')
                        self._ddg_record_success()
                        return results
                    except _aio.TimeoutError:
                        logger.warning(f"[DDG] Search timeout (12s) for: {query[:50]}")
                        self._ddg_record_fail()
                        if attempt < max_retries - 1:
                            await _aio.sleep(base_delay * (2 ** attempt))
                            continue
                        return None
                    except Exception as e:
                        err_str = str(e)
                        # 202 Ratelimit или DuckDuckGoRatelimitException
                        _is_ratelimit = '202' in err_str or 'atelimit' in err_str or 'Ratelimit' in err_str
                        # Транзиентные ошибки: декодирование, соединение, таймаут сети
                        _is_transient = 'DecodeError' in err_str or 'decode' in err_str.lower() or 'Body collection' in err_str or 'body' in err_str.lower() or 'RequestError' in err_str or 'ConnectError' in err_str or 'error sending request' in err_str.lower()
                        if (_is_ratelimit or _is_transient) and attempt < max_retries - 1:
                            wait = base_delay * (2 ** attempt) + (2.0 if _is_ratelimit else 1.0)
                            logger.warning(f"[DDG] {'Rate limit' if _is_ratelimit else 'Transient error'} on attempt {attempt+1}, retry in {wait:.1f}s: {err_str[:80]}")
                            await _aio.sleep(wait)
                            continue
                        self._ddg_record_fail()
                        _rec_err('ddg', f"Search failed: {e}")
                        logger.debug(f"[DDG] failed, circuit_breaker streak={ExternalAPIClient._ddg_fail_streak}: {err_str[:80]}")
                        return None
            return None

        except Exception as e:
            self._ddg_record_fail()
            _rec_err('ddg', f"Search failed: {e}")
            logger.debug(f"[DDG] outer exception: {e}")
            return None

    async def bing_search(
        self,
        query: str,
        num: int = 10,
        region: str = "ru-RU",
        cache_ttl: int = 1800,
    ) -> Optional[List[dict]]:
        """Поиск через Bing HTML (без API ключа, как fallback для DDG)."""
        import re as _re_bing
        cache_params = {'q': query, 'num': num, 'region': region, 'engine': 'bing_html'}
        cached = await self.cache.get('ddg', cache_params)
        if cached is not None:
            return cached
        try:
            s = await self._get_session()
            _lang = region.split('-')[0] if '-' in region else 'ru'
            _headers = {
                'User-Agent': _rand_ua(),
                'Accept-Language': f'{_lang},{_lang}-{_lang.upper()};q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
            }
            _q_enc = query.replace(' ', '+')
            _count = min(num, 20)
            _url = f'https://www.bing.com/search?q={_q_enc}&count={_count}&setlang={_lang}'
            async with s.get(
                _url, headers=_headers,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False, allow_redirects=True,
            ) as resp:
                if resp.status in (429, 403):
                    logger.debug(f'[BING] blocked ({resp.status}), skip')
                    return None
                if resp.status != 200:
                    return None
                html = await resp.text(errors='replace')

            results = []
            # Extract <li class="b_algo"> blocks
            _blocks = _re_bing.findall(
                r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>',
                html, _re_bing.DOTALL
            )
            for block in _blocks[:num]:
                # Title + URL from <h2><a href="...">...</a></h2>
                _link_m = _re_bing.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, _re_bing.DOTALL)
                _snippet_m = _re_bing.search(r'<p[^>]*>(.*?)</p>', block, _re_bing.DOTALL)
                if not _link_m:
                    continue
                _link = _link_m.group(1)
                _title = _re_bing.sub(r'<[^>]+>', '', _link_m.group(2)).strip()
                _snippet = _re_bing.sub(r'<[^>]+>', '', _snippet_m.group(1)).strip() if _snippet_m else ''
                if _link and not _link.startswith('https://www.bing.com'):
                    results.append({'title': _title, 'snippet': _snippet, 'link': _link})

            if results:
                await self.cache.set('ddg', cache_params, results, cache_ttl)
                logger.info(f"[BING] Found {len(results)} results for: {query[:60]}")
            return results or None
        except Exception as e:
            logger.warning(f"[BING] Search error: {e}")
            return None

    async def duckduckgo_lite_search(
        self,
        query: str,
        num: int = 10,
        cache_ttl: int = 1800,
    ) -> Optional[List[dict]]:
        """Fallback DDG через HTML-версию lite.duckduckgo.com (без JS/API)."""
        import re as _re_ddgl
        cache_params = {'q': query, 'num': num, 'engine': 'ddg_lite'}
        cached = await self.cache.get('ddg', cache_params)
        if cached is not None:
            return cached
        try:
            s = await self._get_session()
            _headers = {
                'User-Agent': _rand_ua(),
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            }
            async with s.post(
                'https://lite.duckduckgo.com/lite/',
                data={'q': query, 'kl': 'ru-ru'},
                headers=_headers,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False, allow_redirects=True,
            ) as resp:
                if resp.status in (429, 403):
                    logger.debug(f'[DDG_LITE] blocked ({resp.status}), skip')
                    return None
                if resp.status != 200:
                    return None
                html = await resp.text(errors='replace')

            results = []
            # DDG Lite: результаты в таблице, ссылки с rel="nofollow"
            links = _re_ddgl.findall(
                r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                html, _re_ddgl.DOTALL
            )
            snippets = _re_ddgl.findall(
                r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>',
                html, _re_ddgl.DOTALL
            )
            for i, (url, title_raw) in enumerate(links[:num]):
                title = _re_ddgl.sub(r'<[^>]+>', '', title_raw).strip()
                snip = ''
                if i < len(snippets):
                    snip = _re_ddgl.sub(r'<[^>]+>', '', snippets[i]).strip()
                if url and not url.startswith('https://lite.duckduckgo.com'):
                    results.append({'title': title, 'snippet': snip, 'link': url})

            if results:
                await self.cache.set('ddg', cache_params, results, cache_ttl)
                logger.info(f"[DDG_LITE] Found {len(results)} results for: {query[:60]}")
            return results or None
        except Exception as e:
            logger.warning(f"[DDG_LITE] Search error: {e}")
            return None

    async def google_html_search(
        self,
        query: str,
        num: int = 10,
        region: str = "ru",
        cache_ttl: int = 1800,
    ) -> Optional[List[dict]]:
        """Fallback поиск через Google HTML (без API ключа)."""
        import re as _re_g
        cache_params = {'q': query, 'num': num, 'engine': 'google_html'}
        cached = await self.cache.get('ddg', cache_params)
        if cached is not None:
            return cached
        try:
            s = await self._get_session()
            _headers = {
                'User-Agent': _rand_ua(),
                'Accept-Language': f'{region},{region}-{region.upper()};q=0.9,en;q=0.8',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Referer': 'https://www.google.com/',
            }
            import urllib.parse as _ulp
            _q_enc = _ulp.quote_plus(query)
            _url = f'https://www.google.com/search?q={_q_enc}&num={min(num, 20)}&hl={region}'
            async with s.get(
                _url, headers=_headers,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False, allow_redirects=True,
            ) as resp:
                if resp.status in (429, 403):
                    logger.debug(f'[GOOGLE] blocked ({resp.status}), skip')
                    return None
                if resp.status != 200:
                    return None
                html = await resp.text(errors='replace')
            # Проверяем на captcha / bot detection
            if 'sorry/index' in html or 'g-recaptcha' in html or 'detected unusual traffic' in html:
                logger.debug('[GOOGLE] captcha detected, skip')
                return None

            results = []
            # Google: результаты в <div class="g"> блоках
            # Ссылка: <a href="/url?q=REAL_URL&..."> или прямая <a href="https://...">
            # Также пробуем <div class="yuRUbf"> / <div class="tF2Cxc">

            # Способ 1: /url?q= redirect links
            for m in _re_g.finditer(r'<a[^>]+href="/url\?q=(https?://[^&"]+)[^"]*"[^>]*>(.*?)</a>', html, _re_g.DOTALL):
                url_raw = m.group(1)
                title = _re_g.sub(r'<[^>]+>', '', m.group(2)).strip()
                if not title or 'google.com' in url_raw:
                    continue
                # Ищем сниппет рядом (следующий <span> или <div> с текстом)
                pos = m.end()
                snip_m = _re_g.search(r'<(?:span|div)[^>]*>((?:(?!<(?:span|div)).){20,300})</(?:span|div)>', html[pos:pos+2000], _re_g.DOTALL)
                snip = _re_g.sub(r'<[^>]+>', '', snip_m.group(1)).strip() if snip_m else ''
                results.append({'title': title, 'snippet': snip, 'link': url_raw})

            # Способ 2: прямые ссылки в <div class="g">
            if not results:
                blocks = _re_g.findall(r'<div[^>]+class="[^"]*\bg\b[^"]*"[^>]*>(.*?)</div>\s*</div>', html, _re_g.DOTALL)
                for block in blocks[:num]:
                    link_m = _re_g.search(r'<a[^>]+href="(https?://(?!www\.google\.com)[^"]+)"[^>]*>(.*?)</a>', block, _re_g.DOTALL)
                    if not link_m:
                        continue
                    url_raw = link_m.group(1)
                    title = _re_g.sub(r'<[^>]+>', '', link_m.group(2)).strip()
                    snip_m = _re_g.search(r'<(?:span|em)[^>]*>(.{20,300}?)</(?:span|em)>', block, _re_g.DOTALL)
                    snip = _re_g.sub(r'<[^>]+>', '', snip_m.group(1)).strip() if snip_m else ''
                    if url_raw and title:
                        results.append({'title': title, 'snippet': snip, 'link': url_raw})

            results = results[:num]
            if results:
                await self.cache.set('ddg', cache_params, results, cache_ttl)
                logger.info(f"[GOOGLE] Found {len(results)} results for: {query[:60]}")
            return results or None
        except Exception as e:
            logger.warning(f"[GOOGLE] Search error: {e}")
            return None

    @staticmethod
    def _simplify_search_query(query: str) -> str:
        """Упрощаем сложный запрос: убираем site:, filetype:, кавычки, урезаем до 6 слов."""
        import re as _re
        q = _re.sub(r'\b(?:site|filetype|intitle|inurl|before|after):[^\s]+', '', query)
        q = q.replace('"', '').replace("'", '')
        q = _re.sub(r'\s+', ' ', q).strip()
        # Обрезаем до первых 6 значимых слов
        words = q.split()
        if len(words) > 6:
            q = ' '.join(words[:6])
        return q

    async def web_search(
        self,
        query: str,
        num: int = 10,
        gl: str = "ru",
        hl: str = "ru",
        cache_ttl: int = 1800
    ) -> Optional[List[dict]]:
        """Универсальный поиск: DDG API → DDG Lite → Bing → Google fallback.
        Если все движки вернули пусто, а запрос сложный (site: / длинный) — повтор с упрощённым запросом.
        """
        import re as _re_ws
        region = f"{hl}-{gl}" if gl and hl else "ru-ru"
        _has_site_op = bool(_re_ws.search(r'\bsite:', query))
        # site: операторы DDG не умеет (Yahoo их игнорирует) → сразу Bing/Google
        if not _has_site_op:
            results = await self.duckduckgo_search(query, num=num, region=region, cache_ttl=cache_ttl)
            if not results:
                results = await self.duckduckgo_lite_search(query, num=num, cache_ttl=cache_ttl)
        else:
            results = None
        if not results:
            results = await self.bing_search(query, num=num, region=f"{hl}-{gl.upper()}", cache_ttl=cache_ttl)
        if not results:
            results = await self.google_html_search(query, num=num, region=hl, cache_ttl=cache_ttl)
        # ── Fallback: упростить запрос и повторить, если сложный/специфичный ──
        if not results:
            _is_long = len(query.split()) > 6
            if _has_site_op or _is_long:
                simplified = self._simplify_search_query(query)
                if simplified and simplified.lower() != query.lower():
                    logger.info(f"[WEB_SEARCH] Empty → retry simplified: '{simplified[:60]}'")
                    results = await self.duckduckgo_search(simplified, num=num, region=region, cache_ttl=cache_ttl)
                    if not results:
                        results = await self.duckduckgo_lite_search(simplified, num=num, cache_ttl=cache_ttl)
                    if not results:
                        results = await self.bing_search(simplified, num=num, region=f"{hl}-{gl.upper()}", cache_ttl=cache_ttl)
        return results

    async def web_multi_search(
        self,
        queries: List[str],
        num_per_query: int = 5,
        gl: str = "ru",
        hl: str = "ru",
        cache_ttl: int = 1800
    ) -> List[dict]:
        """Последовательный поиск DDG → DDG Lite → Bing → Google fallback.
        НАМЕРЕННО последовательный — parallel DDG вызывает rate limit 202.
        """
        region = f"{hl}-{gl}" if gl and hl else "ru-ru"
        seen_urls: set = set()
        all_results: List[dict] = []

        for i, q in enumerate(queries):
            if i > 0:
                cache_params = {'q': q, 'num': num_per_query, 'region': region, 'engine': 'ddg'}
                cached = await self.cache.get('ddg', cache_params)
                if cached is None:
                    await asyncio.sleep(2.0)

            results = await self.duckduckgo_search(q, num=num_per_query, region=region, cache_ttl=cache_ttl)
            if not results:
                results = await self.duckduckgo_lite_search(q, num=num_per_query, cache_ttl=cache_ttl)
            if not results:
                logger.info(f"[WEB_MULTI] DDG empty for '{q[:50]}' → trying Bing")
                results = await self.bing_search(q, num=num_per_query, region=f"{hl}-{gl.upper()}", cache_ttl=cache_ttl)
            if not results:
                logger.info(f"[WEB_MULTI] Bing empty for '{q[:50]}' → trying Google")
                results = await self.google_html_search(q, num=num_per_query, region=hl, cache_ttl=cache_ttl)

            if not results:
                logger.warning(f"[WEB_MULTI] All engines empty for: '{q[:50]}'")
                continue
            for r in results:
                url = r.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    r['query_source'] = q
                    all_results.append(r)

        logger.info(f"[WEB_MULTI] {len(queries)} queries → {len(all_results)} unique results")
        return all_results
    
    # ========================================================================
    # OpenWeatherMap
    # ========================================================================
    
    async def get_weather(
        self,
        city: str,
        cache_ttl: int = 1800  # 30 минут
    ) -> Optional[dict]:
        """
        Получить погоду для города.
        
        Returns:
            {'temp': float, 'feels_like': float, 'description': str,
             'humidity': int, 'wind_speed': float, 'city_name': str}
        """
        if not OPENWEATHERMAP_API_KEY:
            return None
        
        city_norm = city.strip().lower()
        cache_params = {'city': city_norm}
        
        # Кэш
        cached = await self.cache.get('weather', cache_params)
        if cached is not None:
            return cached
        
        # Rate-limit
        if not await self.rate_limiter.acquire('openweathermap'):
            return None
        
        session = await self._get_session()
        
        try:
            url = f"http://api.openweathermap.org/data/2.5/weather"
            params = {
                'q': city,
                'appid': OPENWEATHERMAP_API_KEY,
                'units': 'metric',
                'lang': 'ru'
            }
            
            async with session.get(url, params=params) as response:
                self._track_call('openweathermap')
                
                if response.status == 200:
                    data = await response.json()
                    result = {
                        'temp': data['main']['temp'],
                        'feels_like': data['main']['feels_like'],
                        'description': data['weather'][0]['description'].capitalize(),
                        'humidity': data['main']['humidity'],
                        'wind_speed': data['wind']['speed'],
                        'city_name': data['name']
                    }
                    _clr_err('openweathermap')
                    await self.cache.set('weather', cache_params, result, cache_ttl)
                    return result
                else:
                    _rec_err('openweathermap', f'API error {response.status}', code=response.status)
                    logger.warning(f"[WEATHER] API error {response.status} for: {city}")
                    return None
                    
        except Exception as e:
            _rec_err('openweathermap', f'Exception: {e}')
            logger.error(f"[WEATHER] Error: {e}")
            return None
    
    # ========================================================================
    # NewsAPI  (с DDG-fallback — бесплатно, без лимита)
    # ========================================================================

    async def _get_news_ddg(
        self,
        topic: str,
        max_results: int = 10,
        cache_ttl: int = 3600,
    ) -> Optional[List[dict]]:
        """Получить новости через DuckDuckGo (бесплатно, без API-ключа).

        Returns тот же формат, что и get_news():
            [{'title', 'description', 'url', 'published', 'source'}]
        """
        cache_params = {'topic': topic, 'n': max_results, 'src': 'ddg_news'}
        cached = await self.cache.get('ddg', cache_params)
        if cached is not None:
            return cached

        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # legacy fallback
            import asyncio as _aio_n

            def _sync_news():
                with DDGS(timeout=10) as ddgs:
                    raw = list(ddgs.news(topic, max_results=max_results))
                return [
                    {
                        'title': r.get('title', ''),
                        'description': r.get('body', '') or r.get('excerpt', ''),
                        'url': r.get('url', ''),
                        'published': r.get('date', ''),
                        'source': r.get('source', '') or r.get('publisher', ''),
                    }
                    for r in raw
                    if r.get('title')
                ]

            loop = _aio_n.get_running_loop()
            results = await loop.run_in_executor(None, _sync_news)

            self._track_call('ddg')
            await self.cache.set('ddg', cache_params, results, cache_ttl)
            if results:
                logger.info(f"[DDG_NEWS] Found {len(results)} articles for: {topic[:50]}")
            else:
                logger.info(f"[DDG_NEWS] No results for: {topic[:50]}")
            _clr_err('ddg')
            return results

        except Exception as e:
            _rec_err('ddg', f"DDG news failed: {e}")
            _ddg_err_str = str(e)
            _is_transient_ddg = 'DecodeError' in _ddg_err_str or 'No results' in _ddg_err_str or 'body' in _ddg_err_str.lower()
            if _is_transient_ddg:
                logger.debug(f"[DDG_NEWS] Transient error (suppressed): {e}")
            else:
                logger.warning(f"[DDG_NEWS] Error: {e}")
            return None

    async def get_news(
        self,
        topic: Optional[str] = None,
        language: str = "ru",
        sort_by: str = "publishedAt",
        page_size: int = 10,
        from_date: Optional[str] = None,
        cache_ttl: int = 21600  # 6 часов (Developer: 100 req/day)
    ) -> Optional[List[dict]]:
        """
        Поиск новостей через NewsAPI.
        
        Returns:
            Список [{'title': ..., 'description': ..., 'url': ..., 
                      'published': ..., 'source': ...}]
        """
        if not NEWSAPI_API_KEY:
            # Нет ключа NewsAPI → сразу используем DDG
            return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
        
        cache_params = {'topic': topic, 'lang': language, 'sort': sort_by, 'from': from_date}
        
        # Кэш
        cached = await self.cache.get('news', cache_params)
        if cached is not None:
            return cached
        
        # Backoff: если сервер вернул 429 — ждём 12 ч, пуская трафик на DDG
        blocked_until = self._backoff_until.get('newsapi', 0)
        if time.time() < blocked_until:
            remaining = int((blocked_until - time.time()) / 60)
            logger.info(f"[NEWS] NewsAPI backoff {remaining} min → fallback to DDG news")
            return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)

        # Rate-limit
        if not await self.rate_limiter.acquire('newsapi'):
            # Мягкий rate-limit — тоже уходим на DDG
            return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
        
        session = await self._get_session()
        
        try:
            # Выбираем endpoint
            if not topic or topic.lower() in ['общие', 'главные', 'главное', 'новости']:
                url = 'https://newsapi.org/v2/top-headlines'
                params = {
                    'country': 'ru',
                    'apiKey': NEWSAPI_API_KEY,
                    'pageSize': page_size
                }
            else:
                url = 'https://newsapi.org/v2/everything'
                params = {
                    'q': topic,
                    'language': language,
                    'sortBy': sort_by,
                    'apiKey': NEWSAPI_API_KEY,
                    'pageSize': page_size
                }
                if from_date:
                    params['from'] = from_date
            
            async with session.get(url, params=params) as response:
                self._track_call('newsapi')
                
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('status') != 'ok' or not data.get('articles'):
                        logger.info(f"[NEWS] NewsAPI empty/error status → DDG fallback")
                        return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
                    
                    articles = []
                    for article in data['articles'][:page_size]:
                        title = (article.get('title') or '').strip()
                        if not title or title == '[Removed]':
                            continue
                        
                        articles.append({
                            'title': title,
                            'description': (article.get('description') or '').strip(),
                            'url': article.get('url', ''),
                            'published': article.get('publishedAt', ''),
                            'source': article.get('source', {}).get('name', '')
                        })
                    
                    if articles:
                        await self.cache.set('news', cache_params, articles, cache_ttl)
                        logger.info(f"[NEWS] Found {len(articles)} articles for: {topic or 'headlines'}")
                        _clr_err('newsapi')
                        return articles
                    else:
                        # NewsAPI вернул 200 но без статей → DDG
                        logger.info(f"[NEWS] NewsAPI returned 0 articles → DDG fallback")
                        return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
                else:
                    if response.status == 429:
                        # 429 Too Many Requests — блокируем запросы на 12 часов
                        blocked_ts = time.time() + 43200
                        self._backoff_until['newsapi'] = blocked_ts
                        _body = await response.text()
                        logger.warning(
                            f"[NEWS] 429 received — NewsAPI dev quota exhausted. "
                            f"Backoff 12h set. Switching to DDG news. Body: {_body[:200]}"
                        )
                        _rec_err('newsapi', 'Исчерпан дневной лимит запросов', code=429,
                                   detail=_body[:200], blocked_until=blocked_ts)
                        return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
                    else:
                        _rec_err('newsapi', f'API error {response.status}', code=response.status)
                        logger.warning(f"[NEWS] API error {response.status} for: {topic} → DDG fallback")
                        return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
                    
        except Exception as e:
            logger.error(f"[NEWS] Error: {e} → DDG fallback")
            return await self._get_news_ddg(topic or 'новости', max_results=page_size, cache_ttl=cache_ttl)
    
    # ========================================================================
    # DeepSeek AI
    # ========================================================================
    
    async def deepseek_analyze(
        self,
        prompt: str,
        system_prompt: str = "Ты эксперт-аналитик.",
        temperature: float = 0.5,
        max_tokens: int = 600,
        parse_json: bool = False,
        timeout: Optional[int] = None
    ) -> Optional[str | dict]:
        """
        Отправить запрос в DeepSeek для анализа.
        
        Args:
            prompt: Текст запроса
            system_prompt: Системный промпт
            temperature: Креативность (0-1)
            max_tokens: Максимум токенов
            parse_json: Если True, пытается распарсить JSON из ответа
            timeout: Тайм-аут в секундах (None = auto: 20s для <=1000 токенов, 60s для >1000)
            
        Returns:
            str с ответом, или dict если parse_json=True и парсинг успешен
        """
        if not DEEPSEEK_API_KEY:
            return None
        
        # Rate-limit
        if not await self.rate_limiter.acquire('deepseek'):
            return None
        
        # Адаптивный тайм-аут: больше токенов = больше времени
        if timeout is None:
            timeout = 60 if max_tokens > 1000 else (15 if max_tokens <= 300 else 25)
        
        req_timeout = aiohttp.ClientTimeout(total=timeout, connect=5)
        session = await self._get_session()
        
        try:
            async with session.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens
                },
                timeout=req_timeout
            ) as response:
                self._track_call('deepseek')
                
                if response.status == 200:
                    data = await response.json()
                    content = data['choices'][0]['message']['content'].strip()
                    _clr_err('deepseek')
                    
                    if parse_json:
                        try:
                            # Извлекаем JSON из ответа
                            start = content.find('{')
                            end = content.rfind('}') + 1
                            if start != -1 and end > start:
                                return json.loads(content[start:end])
                            else:
                                logger.warning("[DEEPSEEK] No JSON found in response")
                                return content
                        except json.JSONDecodeError as e:
                            logger.warning(f"[DEEPSEEK] JSON parse error: {e}")
                            return content
                    
                    return content
                else:
                    _body_ds = ''
                    try:
                        _body_ds = (await response.text())[:300]
                    except Exception:
                        pass
                    _rec_err('deepseek', f'API error {response.status}', code=response.status, detail=_body_ds)
                    logger.error(f"[DEEPSEEK] API error: {response.status}")
                    return None
                    
        except asyncio.TimeoutError:
            _rec_err('deepseek', 'Тайм-аут запроса к AI-модели')
            logger.warning("[DEEPSEEK] Timeout")
            return None
        except Exception as e:
            _rec_err('deepseek', f'Exception: {e}')
            logger.error(f"[DEEPSEEK] Error: {e}")
            return None
    
    # ========================================================================
    # ВЫСОКОУРОВНЕВЫЕ СОСТАВНЫЕ МЕТОДЫ
    # ========================================================================
    
    async def search_and_analyze(
        self,
        query: str,
        num_results: int = 10,
        analysis_prompt: Optional[str] = None,
        max_tokens: int = 600,
        cache_ttl: int = 3600  # 1 час
    ) -> dict:
        """
        Поиск + AI-анализ в одном вызове (замена дублированного паттерна).
        
        Returns:
            {
                'success': bool,
                'results': List[dict],  # Результаты поиска
                'analysis': str | dict, # AI-анализ
                'message': str          # Готовое сообщение для пользователя
            }
        """
        # 1. Поиск через DuckDuckGo
        results = await self.web_search(query, num=num_results, cache_ttl=cache_ttl)
        
        if not results:
            return {
                'success': False,
                'results': [],
                'analysis': None,
                'message': f"🔍 По запросу '{query}' не найдено результатов"
            }
        
        # 2. Если нет кастомного промпта — возвращаем только результаты
        if not analysis_prompt:
            return {
                'success': True,
                'results': results,
                'analysis': None,
                'message': self._format_search_results(query, results[:5])
            }
        
        # 3. AI-анализ
        context = "\n\n".join([
            f"**{r['title']}**\n{r['snippet']}\nИсточник: {r['link']}"
            for r in results
        ])
        
        full_prompt = analysis_prompt.replace("{context}", context).replace("{query}", query)
        
        analysis = await self.deepseek_analyze(
            prompt=full_prompt,
            system_prompt="Ты эксперт-аналитик. Извлекай конкретику, цифры и практические выводы.",
            max_tokens=max_tokens,
            parse_json=True
        )
        
        return {
            'success': True,
            'results': results,
            'analysis': analysis,
            'message': self._format_analysis(query, analysis, results)
        }
    
    async def news_and_analyze(
        self,
        topic: str,
        period: str = "week",
        focus: str = "trends",
        max_articles: int = 10,
        cache_ttl: int = 21600
    ) -> dict:
        """
        Новости + AI-анализ трендов.
        
        Returns:
            {'success': bool, 'articles': list, 'analysis': str, 'message': str}
        """
        # Определяем период
        now = datetime.now()
        period_map = {
            'today': (1, 'publishedAt'),
            'week': (7, 'publishedAt'),
            'month': (30, 'popularity')
        }
        days, sort_by = period_map.get(period, (7, 'publishedAt'))
        from_date = (now - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Определяем язык
        is_russian = any(c in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя' for c in topic.lower())
        language = 'ru' if is_russian else 'en'
        
        articles = await self.get_news(
            topic=topic,
            language=language,
            sort_by=sort_by,
            page_size=max_articles,
            from_date=from_date,
            cache_ttl=cache_ttl
        )
        
        if not articles:
            return {
                'success': False,
                'articles': [],
                'analysis': None,
                'message': f"🔍 По запросу '{topic}' не найдено свежих новостей за {period}."
            }
        
        # Для фокуса "news" — просто список
        if focus == "news":
            return {
                'success': True,
                'articles': articles,
                'analysis': None,
                'message': self._format_news_list(topic, articles[:5])
            }
        
        # AI-анализ
        articles_text = "\n\n".join([
            f"**{a['title']}**\n{a['description']}"
            for a in articles[:10]
        ])
        
        focus_prompts = {
            "trends": f"""Проанализируй новости по теме: "{topic}" и выдели ключевые тренды.

НОВОСТИ:
{articles_text}

Напиши СПЛОШНЫМ ТЕКСТОМ (без bullets, без emoji-заголовков, без списков):
Главные тренды — что происходит, о чём говорят, какие ключевые события. 3-5 предложений с конкретикой.""",
            
            "opportunities": f"""Проанализируй новости по теме: "{topic}" и найди возможности и выводы.

НОВОСТИ:
{articles_text}

Напиши СПЛОШНЫМ ТЕКСТОМ (без bullets, без emoji-заголовков, без списков):
Какие возможности открываются, на что обратить внимание, какие рекомендации. 3-5 предложений с конкретикой."""
        }
        
        prompt = focus_prompts.get(focus, focus_prompts["trends"])
        
        analysis = await self.deepseek_analyze(
            prompt=prompt,
            max_tokens=700,
            temperature=0.7
        )
        
        message = f"Анализ новостей по теме '{topic}': {analysis}" if analysis else \
                  f"По запросу '{topic}' не найдено свежих новостей."
        
        return {
            'success': True,
            'articles': articles,
            'analysis': analysis,
            'message': message
        }
    
    # ========================================================================
    # ФОРМАТИРОВАНИЕ (вспомогательные)
    # ========================================================================
    
    def _format_search_results(self, query: str, results: list) -> str:
        """Форматирует результаты поиска для пользователя"""
        text = f"🔍 **Результаты поиска**: {query}\n\n"
        for i, r in enumerate(results, 1):
            text += f"{i}. **{r['title']}**\n"
            if r['snippet']:
                snippet = r['snippet'][:150] + '...' if len(r['snippet']) > 150 else r['snippet']
                text += f"   {snippet}\n"
            text += f"   🔗 [Читать далее]({r['link']})\n\n"
        return text
    
    def _format_news_list(self, topic: str, articles: list) -> str:
        """Форматирует список новостей"""
        text = f"📰 **Новости по теме**: {topic}\n\n"
        for i, a in enumerate(articles, 1):
            text += f"{i}. **{a['title']}**\n"
            if a['description']:
                text += f"   {a['description'][:150]}...\n"
            if a['source']:
                text += f"   ➡️ {a['source']}\n"
            text += "\n"
        return text
    
    def _format_analysis(self, query: str, analysis, results: list) -> str:
        """Форматирует AI-анализ в ЧИСТЫЙ текст (без bullets и emoji-заголовков).
        AI сам переработает в живой ответ."""
        if isinstance(analysis, dict):
            parts = []
            if analysis.get('summary'):
                parts.append(analysis['summary'])
            if analysis.get('key_insights'):
                insights = ", ".join(analysis['key_insights'][:4])
                parts.append(f"Ключевые выводы: {insights}")
            if analysis.get('opportunities'):
                opps = ", ".join(
                    o if isinstance(o, str) else str(o)
                    for o in analysis['opportunities'][:3]
                )
                parts.append(f"Возможности: {opps}")
            steps = analysis.get('actionable_steps') or analysis.get('action_plan', [])
            if steps:
                steps_text = ", ".join(steps[:3])
                parts.append(f"Рекомендации: {steps_text}")
            
            return f"Анализ по теме '{query}': " + ". ".join(parts)
        elif isinstance(analysis, str):
            return f"Анализ по теме '{query}': {analysis}"
        else:
            return self._format_search_results(query, results[:5])


# ============================================================================
# ГЛОБАЛЬНЫЙ СИНГЛТОН
# ============================================================================

_client: Optional[ExternalAPIClient] = None


def get_api_client() -> ExternalAPIClient:
    """Получить глобальный экземпляр API-клиента"""
    global _client
    if _client is None:
        _client = ExternalAPIClient()
    return _client


async def close_api_client():
    """Закрыть API-клиент при завершении приложения"""
    global _client
    if _client:
        await _client.close()
        _client = None
