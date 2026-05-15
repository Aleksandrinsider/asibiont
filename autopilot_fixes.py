"""
autopilot_fixes.py — Целевые исправления автопилота
====================================================

Универсальные фиксы: работают для любых целей, интеграций и языков.
Безопасны: не меняют существующую логику, только добавляют защиту.

Использование:
    from autopilot_fixes import safe_get, call_ai_safe, detect_user_locale, CycleCache

Автор: автоматический анализ автопилота, май 2026
"""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 1. БЕЗОПАСНЫЙ ДОСТУП К DICT
# ═══════════════════════════════════════════════════════════════
# Исправляет: 'str' object has no attribute 'get'
# Причина: AI координатор иногда возвращает строку вместо JSON
# Применение: заменить ВСЕ .get() в _run_coordinator_dispatch

def safe_get(obj, key: str, default=None):
    """
    Никогда не падает на type mismatch.
    Для любого не-dict возвращает default.

    Пример:
        # Было:  plan.get('agent')
        # Стало: safe_get(plan, 'agent')
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    logger.warning(
        "safe_get: expected dict for '%s', got %s: %.100s",
        key, type(obj).__name__, str(obj)
    )
    return default


def validate_coordinator_response(raw: Any) -> dict:
    """
    Парсит ответ AI координатора.
    Принимает: str, dict, list, None, JSON-строку.
    Всегда возвращает dict с ключом 'assignments'.

    Применение:
        response = validate_coordinator_response(await _quick_ai_call_raw(...))
        assignments = response.get('assignments', [])
    """
    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        raw_stripped = raw.strip()
        # Пробуем разные обёртки
        for wrapper, expected_type in [
            (raw_stripped, dict),
            (f'{{{raw_stripped}}}', dict),
            (f'[{raw_stripped}]', list),
        ]:
            try:
                parsed = json.loads(wrapper)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {'assignments': parsed}
            except (json.JSONDecodeError, TypeError):
                continue
        # Если JSON не парсится — пытаемся вытащить из текста
        logger.warning("validate: AI response is not valid JSON: %.200s", raw_stripped)
        return _extract_assignments_from_text(raw_stripped)

    if isinstance(raw, list):
        return {'assignments': raw}

    return {'assignments': []}


def _extract_assignments_from_text(text: str) -> dict:
    """Пытается извлечь назначения агентов из свободного текста."""
    import re
    assignments = []
    # Ищем строки вида: "agent: имя" или "Agent: имя" или "— имя:"
    patterns = [
        r'(?:agent|агент)\s*[:—]\s*(\w+)',
        r'(?:назначить|assign|отправить|send)\s+(\w+)',
        r'["\']?agent["\']?\s*:\s*["\']?(\w+)',
    ]
    seen = set()
    for p in patterns:
        for match in re.finditer(p, text, re.IGNORECASE):
            name = match.group(1).strip().lower()
            if name and name not in seen:
                seen.add(name)
                assignments.append({'agent': name, 'action': 'auto_extracted'})
    return {'assignments': assignments} if assignments else {'assignments': []}


# ═══════════════════════════════════════════════════════════════
# 2. GRACEFUL AI TIMEOUT — 3 УРОВНЯ FALLBACK
# ═══════════════════════════════════════════════════════════════
# Исправляет: TimeoutError в любом AI-вызове
# Причина: AI провайдер медленный, сетевые проблемы
# Применение: обернуть ВСЕ вызовы _quick_ai_call_raw

async def call_ai_safe(
    ai_func,
    prompt_or_messages,
    max_tokens: int = 2000,
    timeout: int = 60,
    locale: str = 'en',
    **kwargs
) -> Optional[str]:
    """
    Вызов AI с 3 уровнями деградации.
    НИКОГДА не падает на таймауте.
    Принимает как строку, так и список сообщений (messages list).

    Уровни:
    1. Полный запрос (timeout=60s, max_tokens=2000)
    2. Урезанный до 4000 символов (timeout=30s, max_tokens=1000)
    3. Минимальный аварийный запрос (timeout=15s, max_tokens=500)

    Применение:
        result = await call_ai_safe(_quick_ai_call_raw, prompt, max_tokens=2000)
        result = await call_ai_safe(_quick_ai_call_raw, messages_list, max_tokens=2000)
        if result is None:
            # graceful fallback — обработка уже сделана в цикле
    """
    emergency_prompts = {
        'en': [{"role": "user", "content": "Answer in one sentence: what is the next logical step?"}],
        'ru': [{"role": "user", "content": "Ответь одним предложением на русском: какой следующий шаг?"}],
        'es': [{"role": "user", "content": "Responde en una frase: ¿cuál es el próximo paso lógico?"}],
        'pt': [{"role": "user", "content": "Responda em uma frase: qual é o próximo passo lógico?"}],
        'de': [{"role": "user", "content": "Antworte in einem Satz: Was ist der nächste logische Schritt?"}],
        'fr': [{"role": "user", "content": "Réponds en une phrase : quelle est la prochaine étape logique ?"}],
        'zh': [{"role": "user", "content": "用一句话回答：下一步该怎么做？"}],
    }

    # Определяем тип входа: строка или список сообщений
    _is_messages = isinstance(prompt_or_messages, list)
    _prompt_str = prompt_or_messages if not _is_messages else (
        prompt_or_messages[-1].get('content', '') if prompt_or_messages else ''
    )

    # Fallback-варианты: (промпт, таймаут, макс_токенов)
    if _is_messages:
        # Для списка сообщений — урезаем только content последнего сообщения
        _truncated_msgs = list(prompt_or_messages)
        if _truncated_msgs and isinstance(_truncated_msgs[-1], dict):
            _orig = _truncated_msgs[-1].get('content', '')
            _truncated_msgs[-1] = dict(_truncated_msgs[-1])
            _truncated_msgs[-1]['content'] = _orig[:4000] + "\n\n[Context truncated for speed]"

        fallbacks = [
            (prompt_or_messages, timeout, max_tokens),                          # Уровень 1: полный
            (_truncated_msgs,                                                    # Уровень 2: урезанный
             max(15, timeout // 2), max(500, max_tokens // 2)),
            (emergency_prompts.get(locale, emergency_prompts['en']),            # Уровень 3: аварийный
             15, 500),
        ]
    else:
        fallbacks = [
            (prompt_or_messages, timeout, max_tokens),                          # Уровень 1: полный
            (prompt_or_messages[:4000] + "\n\n[Context truncated for speed]",   # Уровень 2: урезанный
             max(15, timeout // 2), max(500, max_tokens // 2)),
            (emergency_prompts.get(locale, emergency_prompts['en']),            # Уровень 3: аварийный
             15, 500),
        ]

    for level, (fb_payload, fb_timeout, fb_tokens) in enumerate(fallbacks, 1):
        try:
            result = await asyncio.wait_for(
                ai_func(fb_payload, max_tokens=fb_tokens, **kwargs),
                timeout=fb_timeout
            )
            if level > 1:
                logger.info("call_ai_safe: level %d succeeded (%.0fs timeout)", level, fb_timeout)
            return result
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("call_ai_safe: level %d timed out (%.0fs)", level, fb_timeout)
            continue
        except Exception as e:
            logger.error("call_ai_safe: level %d error: %s", level, e)
            continue

    logger.error("call_ai_safe: ALL levels exhausted (input len=%d chars)", len(_prompt_str))
    return None


# ═══════════════════════════════════════════════════════════════
# 3. АВТООПРЕДЕЛЕНИЕ ЯЗЫКА ПОЛЬЗОВАТЕЛЯ
# ═══════════════════════════════════════════════════════════════
# Исправляет: смешение RU/EN в ответах пользователю
# Принцип: как на лендинге — один язык на пользователя
# Применение: перед любым user-facing текстом

def detect_user_locale(user) -> str:
    """
    Определяет язык пользователя по приоритету:
    1. Явная настройка в профиле (user.language_code)
    2. Локаль из модели User (user.locale)
    3. Fallback: 'en'

    Пример:
        locale = detect_user_locale(user)
        greeting = messages[locale]['header']
    """
    if hasattr(user, 'language_code') and user.language_code:
        lang = user.language_code[:2].lower()
        if lang in ('ru', 'en', 'es', 'pt', 'de', 'fr', 'zh', 'ar', 'tr'):
            return lang
        return 'en'

    if hasattr(user, 'locale') and user.locale:
        lang = user.locale[:2].lower()
        if lang in ('ru', 'en', 'es', 'pt', 'de', 'fr', 'zh', 'ar', 'tr'):
            return lang
        return 'en'

    return 'en'


def get_localized_text(locale: str, strings: dict, key: str, default: str = '') -> str:
    """
    Возвращает локализованный текст для ключа.
    Если для locale нет — ищет 'en', если нет 'en' — default.

    Пример:
        MSG = {
            'en': {'hello': 'Hello'},
            'ru': {'hello': 'Привет'},
        }
        text = get_localized_text('ru', MSG, 'hello')  # 'Привет'
    """
    lang = locale[:2].lower() if locale else 'en'
    lang_dict = strings.get(lang, strings.get('en', {}))
    return lang_dict.get(key, default)


# ═══════════════════════════════════════════════════════════════
# 4. КЭШ РЕЗУЛЬТАТОВ ИНСТРУМЕНТОВ (CYCLE CACHE)
# ═══════════════════════════════════════════════════════════════
# Исправляет: дублирование вызовов одного API с теми же параметрами
# Причина: нет памяти что уже вызывали в этом цикле
# Применение: перед любым внешним API-вызовом

class CycleCache:
    """
    TTL-кэш для ЛЮБОГО инструмента.
    Ключ = tool_name + SHA256(json-параметров).
    Не привязан к конкретным инструментам или API.

    Пример:
        cache = CycleCache(ttl_seconds=600)
        key = ('rss_fetch', {'url': 'https://...'})
        cached = cache.get('rss_fetch', {'url': 'https://...'})
        if cached:
            return cached
        result = await fetch_rss(url)
        cache.set('rss_fetch', {'url': 'https://...'}, result)
    """

    def __init__(self, ttl_seconds: int = 600):
        self._cache: dict[str, tuple[float, Any]] = {}
        self.ttl = ttl_seconds

    def _make_key(self, tool_name: str, params: dict) -> str:
        """Генерирует уникальный ключ из имени инструмента и параметров."""
        param_str = json.dumps(params, sort_keys=True, default=str)
        param_hash = hashlib.sha256(param_str.encode()).hexdigest()[:16]
        return f"{tool_name}:{param_hash}"

    def get(self, tool_name: str, params: dict) -> Optional[Any]:
        """
        Возвращает кэшированный результат или None.
        Автоматически удаляет просроченные записи.
        """
        key = self._make_key(tool_name, params)
        if key in self._cache:
            ts, result = self._cache[key]
            if time.time() - ts < self.ttl:
                return result
            # Просрочено — удаляем
            del self._cache[key]
        return None

    def set(self, tool_name: str, params: dict, result: Any):
        """Сохраняет результат в кэш."""
        key = self._make_key(tool_name, params)
        self._cache[key] = (time.time(), result)

    def clear(self):
        """Очищает весь кэш (в начале нового цикла)."""
        self._cache.clear()

    def size(self) -> int:
        """Количество записей в кэше."""
        return len(self._cache)

    def hit_rate(self) -> float:
        """Процент попаданий в кэш (трекинг)."""
        if not hasattr(self, '_hits'):
            self._hits = 0
            self._misses = 0
        total = self._hits + self._misses
        return self._hits / max(total, 1)


# ═══════════════════════════════════════════════════════════════
# 5. УНИВЕРСАЛЬНЫЙ CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════
# Исправляет: бесконечные ретраи в упавшие API (502, timeout)
# Причина: нет отслеживания состояния внешних сервисов
# Применение: обернуть любой внешний API-вызов

class CircuitBreakerOpen(Exception):
    """Сервис временно недоступен."""
    pass


class CircuitBreaker:
    """
    Circuit Breaker для ЛЮБОГО внешнего сервиса.
    Состояния: closed → open (после N ошибок) → half-open (после recovery_timeout)
    Поддерживает DB-персистентность через circuit_breaker_state таблицу.

    Пример:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=300)
        try:
            result = await cb.call('rss_finance', fetch_rss, url)
        except CircuitBreakerOpen:
            result = await fallback_source(url)  # альтернативный источник
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 300):
        self._services: dict[str, dict] = {}
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._db_session = None
        self._db_user_id = 0

    def _get_svc(self, service_name: str) -> dict:
        if service_name not in self._services:
            self._services[service_name] = {
                'state': 'closed',
                'failures': 0,
                'last_failure': 0,
                'total_failures': 0,
                'total_calls': 0,
                'dirty': False,
            }
        return self._services[service_name]

    def set_db_session(self, session, user_id: int = 0):
        self._db_session = session
        self._db_user_id = user_id

    async def persist_to_db(self, session=None):
        _sess = session or self._db_session
        if _sess is None:
            return
        try:
            from models import CircuitBreakerState as _CBS
            _now = datetime.now(timezone.utc)
            for svc_name, svc in self._services.items():
                if not svc.get('dirty') and svc.get('_persisted'):
                    continue
                _row = _sess.query(_CBS).filter(
                    _CBS.user_id == self._db_user_id,
                    _CBS.key == f'cb:{svc_name}',
                ).first()
                _state_json = json.dumps({
                    'state': svc['state'],
                    'failures': svc['failures'],
                    'last_failure': svc['last_failure'],
                    'total_failures': svc['total_failures'],
                    'total_calls': svc['total_calls'],
                })
                if _row:
                    _row.fail_count = svc['failures']
                    _row.last_fail_context = _state_json
                    if svc['state'] == 'open':
                        _row.cooldown_until = _now + timedelta(seconds=self.recovery_timeout)
                    elif svc['state'] == 'closed':
                        _row.cooldown_until = None
                    _row.updated_at = _now
                else:
                    _cooldown = None
                    if svc['state'] == 'open':
                        _cooldown = _now + timedelta(seconds=self.recovery_timeout)
                    _sess.add(_CBS(
                        user_id=self._db_user_id,
                        key=f'cb:{svc_name}',
                        fail_count=svc['failures'],
                        first_fail_at=_now if svc['failures'] > 0 else None,
                        cooldown_until=_cooldown,
                        last_fail_context=_state_json,
                        created_at=_now,
                        updated_at=_now,
                    ))
                svc['_persisted'] = True
                svc['dirty'] = False
            _sess.commit()
            logger.debug("[CB] persisted %d service states to DB", len(self._services))
        except Exception as _cb_persist_err:
            logger.warning("[CB] persist_to_db failed: %s", _cb_persist_err)
            try:
                _sess.rollback()
            except Exception:
                pass

    def load_from_db(self, session=None):
        _sess = session or self._db_session
        if _sess is None:
            return
        try:
            from models import CircuitBreakerState as _CBS
            _rows = _sess.query(_CBS).filter(
                _CBS.user_id == self._db_user_id,
                _CBS.key.like('cb:%'),
            ).all()
            _now_ts = time.time()
            for _row in _rows:
                _svc_name = _row.key[3:]
                _failures = _row.fail_count or 0
                try:
                    _state_data = json.loads(_row.last_fail_context or '{}')
                except (json.JSONDecodeError, TypeError):
                    _state_data = {}
                _saved_state = _state_data.get('state', 'closed')
                _last_failure_ts = _state_data.get('last_failure', 0) or 0
                _total_failures = _state_data.get('total_failures', _failures) or _failures
                _total_calls = _state_data.get('total_calls', 0) or 0
                if _saved_state == 'open' and _row.cooldown_until:
                    if _now_ts > _row.cooldown_until.timestamp():
                        _saved_state = 'half-open'
                self._services[_svc_name] = {
                    'state': _saved_state,
                    'failures': _failures,
                    'last_failure': _last_failure_ts,
                    'total_failures': _total_failures,
                    'total_calls': _total_calls,
                    '_persisted': True,
                    'dirty': False,
                }
            logger.info("[CB] loaded %d service states from DB", len(_rows))
        except Exception as _cb_load_err:
            logger.warning("[CB] load_from_db failed: %s", _cb_load_err)

    async def call(self, service_name: str, func, *args, **kwargs) -> Any:
        svc = self._get_svc(service_name)
        svc['total_calls'] += 1
        svc['dirty'] = True
        if svc['state'] == 'open':
            if time.time() - svc['last_failure'] > self.recovery_timeout:
                svc['state'] = 'half-open'
                logger.info("CircuitBreaker: %s half-open → testing recovery", service_name)
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker open for '{service_name}' "
                    f"(failures: {svc['failures']}, remaining: "
                    f"{int(self.recovery_timeout - (time.time() - svc['last_failure']))}s)"
                )
        try:
            result = await func(*args, **kwargs)
            if svc['state'] == 'half-open':
                svc['state'] = 'closed'
                svc['failures'] = 0
                logger.info("CircuitBreaker: %s recovered (half-open → closed)", service_name)
            return result
        except Exception as e:
            svc['failures'] += 1
            svc['total_failures'] += 1
            svc['last_failure'] = time.time()
            if svc['failures'] >= self.failure_threshold:
                svc['state'] = 'open'
                logger.warning(
                    "CircuitBreaker: %s closed→open (%d failures/%d threshold)",
                    service_name, svc['failures'], self.failure_threshold
                )
            raise

    def health_report(self) -> str:
        if not self._services:
            return "No services registered."
        lines = ["Service Health:"]
        for name, svc in sorted(self._services.items()):
            icon = {'closed': 'OK', 'open': 'XX', 'half-open': '??'}.get(svc['state'], '??')
            rate = f"{svc['total_failures']}/{svc['total_calls']}"
            lines.append(f"  [{icon}] {name}: {svc['state']} ({rate} failures)")
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 6. АДАПТИВНЫЙ ИНТЕРВАЛ АГЕНТА
# ═══════════════════════════════════════════════════════════════
# Исправляет: фиксированный 240-min цикл для всех агентов
# Причина: email-агентам нужно чаще, RSS-агентам — реже
# Применение: в _run_coordinator_dispatch

def compute_agent_interval(
    agent_name: str,
    agent_caps: list,
    base_interval: int = 240,
    email_queue_depth: int = 0,
    consecutive_empty_cycles: int = 0,
) -> int:
    """
    Динамически вычисляет интервал запуска агента (в минутах).

    Правила:
    - Email-агенты с непустой очередью: base / 2 (чаще)
    - Агенты с пустыми циклами: base * 2 (реже)
    - RSS-агенты: base (без изменений)
    - Остальные: base

    Универсально: не привязано к именам агентов, только к их capabilities.

    Пример:
        interval = compute_agent_interval(
            agent_name='Beatrice',
            agent_caps=['email', 'outreach'],
            email_queue_depth=15,
            consecutive_empty_cycles=0
        )  # → 120 (чаще, т.к. есть очередь)
    """
    caps_lower = [c.lower() for c in (agent_caps or [])]

    # Email-агенты с непустой очередью — чаще
    if any('email' in c for c in caps_lower) and email_queue_depth > 0:
        return max(base_interval // 2, 60)  # минимум 60 мин

    # Email-агенты без очереди — реже
    if any('email' in c for c in caps_lower):
        return base_interval  # стандарт

    # Пустые циклы — реже
    if consecutive_empty_cycles >= 3:
        return min(base_interval * 2, 480)  # макс 8 часов

    # RSS / поиск — стандарт
    if any(c in ('rss', 'search', 'research') for c in caps_lower):
        return base_interval

    return base_interval


# ═══════════════════════════════════════════════════════════════
# 7. ЧТЕНИЕ И АНАЛИЗ ЦЕЛЕЙ ПОЛЬЗОВАТЕЛЯ
# ═══════════════════════════════════════════════════════════════
# Исправляет: агенты не видят цели пользователя
# Причина: цели не попадают в prompt координатора
# Применение: в _build_autopilot_prompt

def build_goal_block(goals: list, locale: str = 'en') -> str:
    """
    Строит блок целей для prompt'а координатора.
    Каждое действие агента должно быть привязано к цели.

    Универсально: работает для ЛЮБЫХ типов целей.
    Не захардкожено под конкретные категории.

    Пример:
        prompt += build_goal_block(goals, locale='ru')
    """
    if not goals:
        return ""

    labels = {
        'en': {
            'header': 'User Goals',
            'rule': 'CRITICAL: Every action MUST serve at least one goal above.',
        },
        'ru': {
            'header': 'Цели пользователя',
            'rule': 'ВАЖНО: Каждое действие должно служить хотя бы одной цели выше.',
        },
    }
    l = labels.get(locale, labels['en'])

    block = f"\n\n## {l['header']}\n"
    for i, g in enumerate(goals, 1):
        title = (g.get('title') or '')[:100]
        progress = g.get('progress_percentage', g.get('progress', 0)) or 0
        priority = g.get('priority', 5) or 5
        block += f"  #{i}: [{priority}] {title} — {progress}%\n"

    block += f"\n⚠️ {l['rule']}\n"
    block += "If you cannot determine which goal an action serves — skip the action.\n"

    return block


# ═══════════════════════════════════════════════════════════════
# ИНИЦИАЛИЗАЦИЯ ГЛОБАЛЬНЫХ ЭКЗЕМПЛЯРОВ
# ═══════════════════════════════════════════════════════════════
# Эти объекты живут весь цикл координатора

cycle_cache = CycleCache(ttl_seconds=600)  # общий кэш для всего цикла
circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300)  # общий breaker
