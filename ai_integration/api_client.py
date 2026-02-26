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
    SERPER_API_KEY,
    OPENWEATHERMAP_API_KEY,
    NEWSAPI_API_KEY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
)

logger = logging.getLogger(__name__)

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
            'serper': (100, 86400),         # 100/день (Serper Free)
            'newsapi': (90, 86400),          # 100/день (NewsAPI Free), оставляем запас
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
        for api in ['serper', 'newsapi', 'openweathermap', 'deepseek']:
            stats[api] = {
                'total_calls': self._api_call_count.get(api, 0),
                'rate_limit': self.rate_limiter.get_usage(api)
            }
        return stats

    # ========================================================================
    # SERPER (Google Search)
    # ========================================================================
    
    async def serper_search(
        self,
        query: str,
        num: int = 10,
        gl: str = "ru",
        hl: str = "ru",
        cache_ttl: int = 1800  # 30 минут
    ) -> Optional[List[dict]]:
        """
        Поиск через Serper API с кэшированием.
        
        Returns:
            Список результатов [{'title': ..., 'snippet': ..., 'link': ...}] или None
        """
        if not SERPER_API_KEY:
            logger.warning("[SERPER] API key not set")
            return None
        
        cache_params = {'q': query, 'num': num, 'gl': gl}
        
        # Проверяем кэш
        cached = await self.cache.get('serper', cache_params)
        if cached is not None:
            return cached
        
        # Rate-limit
        if not await self.rate_limiter.acquire('serper'):
            logger.warning(f"[SERPER] Rate limit exceeded for query: {query}")
            return None
        
        session = await self._get_session()
        
        try:
            async with session.post(
                'https://google.serper.dev/search',
                headers={
                    'X-API-KEY': SERPER_API_KEY,
                    'Content-Type': 'application/json'
                },
                json={
                    "q": query,
                    "num": num,
                    "gl": gl,
                    "hl": hl
                }
            ) as response:
                self._track_call('serper')
                
                if response.status == 200:
                    data = await response.json()
                    results = []
                    for item in data.get('organic', [])[:num]:
                        results.append({
                            'title': item.get('title', ''),
                            'snippet': item.get('snippet', ''),
                            'link': item.get('link', '')
                        })
                    
                    # Кэшируем
                    await self.cache.set('serper', cache_params, results, cache_ttl)
                    logger.info(f"[SERPER] Found {len(results)} results for: {query[:50]}")
                    return results
                else:
                    logger.warning(f"[SERPER] API error {response.status} for: {query[:50]}")
                    return None
                    
        except asyncio.TimeoutError:
            logger.warning(f"[SERPER] Timeout for: {query[:50]}")
            return None
        except Exception as e:
            logger.error(f"[SERPER] Error: {e}")
            return None
    
    async def serper_multi_search(
        self,
        queries: List[str],
        num_per_query: int = 5,
        gl: str = "ru",
        hl: str = "ru",
        cache_ttl: int = 1800
    ) -> List[dict]:
        """
        Параллельный поиск нескольких запросов через Serper.
        Вместо 5 последовательных запросов — все параллельно.
        
        Returns:
            Объединённый список уникальных результатов
        """
        tasks = [
            self.serper_search(q, num=num_per_query, gl=gl, hl=hl, cache_ttl=cache_ttl)
            for q in queries
        ]
        
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Объединяем и дедуплицируем по URL
        seen_urls = set()
        all_results = []
        
        for i, results in enumerate(results_lists):
            if isinstance(results, Exception):
                logger.warning(f"[SERPER_MULTI] Query '{queries[i]}' failed: {results}")
                continue
            if results:
                for r in results:
                    url = r.get('link', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        r['query_source'] = queries[i]
                        all_results.append(r)
        
        logger.info(f"[SERPER_MULTI] {len(queries)} queries → {len(all_results)} unique results")
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
                    
                    await self.cache.set('weather', cache_params, result, cache_ttl)
                    return result
                else:
                    logger.warning(f"[WEATHER] API error {response.status} for: {city}")
                    return None
                    
        except Exception as e:
            logger.error(f"[WEATHER] Error: {e}")
            return None
    
    # ========================================================================
    # NewsAPI
    # ========================================================================
    
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
            return None
        
        cache_params = {'topic': topic, 'lang': language, 'sort': sort_by, 'from': from_date}
        
        # Кэш
        cached = await self.cache.get('news', cache_params)
        if cached is not None:
            return cached
        
        # Backoff: если сервер вернул 429 — ждём 12 ч прежде чем снова дёргать API
        blocked_until = self._backoff_until.get('newsapi', 0)
        if time.time() < blocked_until:
            remaining = int((blocked_until - time.time()) / 60)
            logger.info(f"[NEWS] Rate-limited by NewsAPI, backoff {remaining} min remaining")
            return None

        # Rate-limit
        if not await self.rate_limiter.acquire('newsapi'):
            return None
        
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
                        return []
                    
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
                    
                    await self.cache.set('news', cache_params, articles, cache_ttl)
                    logger.info(f"[NEWS] Found {len(articles)} articles for: {topic or 'headlines'}")
                    return articles
                else:
                    if response.status == 429:
                        # 429 Too Many Requests — блокируем запросы на 12 часов
                        self._backoff_until['newsapi'] = time.time() + 43200
                        logger.warning(
                            f"[NEWS] 429 received — NewsAPI dev quota exhausted. "
                            f"Backoff 12h set. Body: {await response.text()[:200]}"
                        )
                    else:
                        logger.warning(f"[NEWS] API error {response.status} for: {topic}")
                    return None
                    
        except Exception as e:
            logger.error(f"[NEWS] Error: {e}")
            return None
    
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
            timeout = 60 if max_tokens > 1000 else 30
        
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
                    logger.error(f"[DEEPSEEK] API error: {response.status}")
                    return None
                    
        except asyncio.TimeoutError:
            logger.warning("[DEEPSEEK] Timeout")
            return None
        except Exception as e:
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
        # 1. Поиск
        results = await self.serper_search(query, num=num_results, cache_ttl=cache_ttl)
        
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
        cache_ttl: int = 900
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
            max_tokens=1500,
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
            
            # Добавляем ссылки на источники из поисковых результатов
            if results:
                sources = []
                for r in results[:5]:
                    title = r.get('title', '')
                    link = r.get('link', '')
                    if link:
                        sources.append(f"{title}: {link}")
                if sources:
                    parts.append("Источники: " + ", ".join(sources))
            
            return f"Анализ по теме '{query}': " + ". ".join(parts)
        elif isinstance(analysis, str):
            # Добавляем ссылки к текстовому анализу
            if results:
                sources = [f"{r.get('title', '')}: {r.get('link', '')}" for r in results[:5] if r.get('link')]
                if sources:
                    analysis += ". Источники: " + ", ".join(sources)
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
