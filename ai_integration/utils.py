import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import logging
import re
from datetime import datetime, timedelta, timezone
import pytz
from models import Session, User, UserProfile, Task, Interaction
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    OPENWEATHERMAP_API_KEY,
    NEWSAPI_API_KEY,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_USERNAME,
    REDIS_PASSWORD,
    REDIS_ENABLED
)
import json
import requests
import hashlib
import time
import redis
logger = logging.getLogger(__name__)
# Redis client initialization
redis_client = None
if REDIS_ENABLED:
    try:
        # Prepare connection parameters
        redis_kwargs = {
            'host': REDIS_HOST,
            'port': REDIS_PORT,
            'decode_responses': True,
            'password': REDIS_PASSWORD,
        }
        # Only add username if it's not empty (Railway Redis doesn't use username)
        if REDIS_USERNAME and REDIS_USERNAME.strip():
            redis_kwargs['username'] = REDIS_USERNAME
        redis_client = redis.Redis(**redis_kwargs)
        # Test connection
        redis_client.ping()
        logger.info("[REDIS] Connected successfully")
    except Exception as e:
        logger.debug(f"[REDIS] Failed to connect: {e}. Falling back to in-memory cache")
        redis_client = None
else:
    logger.info("[CACHE] Redis disabled, using in-memory cache")
# Fallback in-memory caches (used if Redis is unavailable)
weather_cache = {}
news_cache = {}
finance_cache = {}
# 429 backoff for NewsAPI: unix timestamp "blocked until"
_news_backoff_until: float = 0.0
# Executor для фоновых задач
background_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="api_cache")


def _redis_get(key):
    """Get data from Redis cache"""
    if redis_client:
        try:
            return redis_client.get(key)
        except Exception as e:
            logger.warning(f"[REDIS] Failed to get {key}: {e}")
    return None


def _redis_set(key, value, ttl_seconds):
    """Set data in Redis cache with TTL"""
    if redis_client:
        try:
            redis_client.setex(key, ttl_seconds, value)
        except Exception as e:
            logger.warning(f"[REDIS] Failed to set {key}: {e}")


def _memory_get(cache_dict, key):
    """Get data from in-memory cache"""
    return cache_dict.get(key)


def _memory_set(cache_dict, key, value):
    """Set data in in-memory cache"""
    cache_dict[key] = value


def _refresh_weather_cache(city, cache_ttl_minutes):
    """Internal function to refresh weather cache"""
    try:
        weather_data = _load_weather_sync(city)
        if weather_data:
            cache_key = f"weather_{city.lower()}"
            _redis_set(cache_key, weather_data, cache_ttl_minutes * 60)
            _memory_set(weather_cache, cache_key, weather_data)
            logger.info(f"[WEATHER CACHE] Refreshed cache for {city}")
    except Exception as e:
        logger.error(f"[WEATHER CACHE] Failed to refresh cache for {city}: {e}")


def _refresh_news_cache(city, cache_ttl_minutes):
    """Internal function to refresh news cache"""
    try:
        news_data = _load_news_sync(city)
        if news_data:
            if city and city.strip():
                cache_key = f"news_{city.lower().strip()}"
            else:
                cache_key = "russian_news_general"
            _redis_set(cache_key, news_data, cache_ttl_minutes * 60)
            _memory_set(news_cache, cache_key, news_data)
            logger.info(f"[NEWS CACHE] Refreshed cache for {cache_key}")
    except Exception as e:
        logger.error(f"[NEWS CACHE] Failed to refresh cache for {city}: {e}")


def _refresh_finance_cache(symbol, asset_type, cache_ttl_minutes):
    """Internal function to refresh finance cache"""
    try:
        finance_data = _load_finance_sync(symbol, asset_type)
        if finance_data:
            cache_key = f"{asset_type}_{symbol.lower()}"
            _redis_set(cache_key, finance_data, cache_ttl_minutes * 60)
            _memory_set(finance_cache, cache_key, finance_data)
            logger.info(f"[FINANCE CACHE] Refreshed cache for {symbol} ({asset_type})")
    except Exception as e:
        logger.error(f"[FINANCE CACHE] Failed to refresh cache for {symbol} ({asset_type}): {e}")


def parse_time_to_datetime(time_text, user_id):
    """Парсит время из текста пользователя"""
    import re
    from datetime import datetime, timedelta
    import pytz
    from models import Session, User
    # Получаем timezone пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.timezone('Europe/Moscow')
    session.close()
    now = datetime.now(user_tz)

    # Проверяем ISO-формат: «2026-12-15 10:00:00» / «2026-12-15 10:00» / «2026-12-15»
    _iso_text = (time_text or '').strip()
    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            _dt = datetime.strptime(_iso_text, _fmt)
            _dt = user_tz.localize(_dt)
            return _dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass

    time_text = time_text.lower().strip()
    # Проверяем "через X минут/часов"
    through_time_match = re.search(r"через\s+(\d+)\s+(минут|час)", time_text)
    if through_time_match:
        amount = int(through_time_match.group(1))
        unit = through_time_match.group(2).lower()
        if "минут" in unit:
            target_dt = now + timedelta(minutes=amount)
        else:  # час/часов
            target_dt = now + timedelta(hours=amount)
        return target_dt.strftime("%Y-%m-%d %H:%M")
    # Проверяем "завтра/сегодня в XX:XX"
    time_match = re.search(r"(завтра|послезавтра|сегодня)\s+(?:в\s+|к\s+)?(\d{1,2}):(\d{2})", time_text)
    if time_match:
        day_word = time_match.group(1).lower()
        hour = int(time_match.group(2))
        minute = int(time_match.group(3))
        if day_word == "послезавтра":
            target_date = (now + timedelta(days=2)).date()
        elif day_word == "завтра":
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()
        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")
    # Проверяем дни недели (винительный + именительный падеж)
    weekdays = {
        'понедельник': 0, 'вторник': 1, 'среда': 2, 'среду': 2,
        'четверг': 3, 'пятница': 4, 'пятницу': 4,
        'суббота': 5, 'субботу': 5, 'воскресенье': 6,
    }
    weekday_match = re.search(
        r"(понедельник|вторник|среду|среда|четверг"
        r"|пятницу|пятница|субботу|суббота|воскресенье)"
        r"(?:\s+(?:в\s+)?(\d{1,2}):(\d{2}))?",
        time_text,
    )
    if weekday_match:
        weekday_name = weekday_match.group(1).lower()
        target_weekday = weekdays[weekday_name]
        current_weekday = now.weekday()  # 0 = понедельник, 6 = воскресенье
        # Вычисляем сколько дней добавить
        days_ahead = (target_weekday - current_weekday) % 7
        if days_ahead == 0:  # Если это тот же день недели
            days_ahead = 7  # Следующая неделя
        target_date = (now + timedelta(days=days_ahead)).date()
        # Если указано время
        if weekday_match.group(2) and weekday_match.group(3):
            hour = int(weekday_match.group(2))
            minute = int(weekday_match.group(3))
            target_time = datetime.min.time().replace(hour=hour, minute=minute)
        else:
            # Если время не указано, ставим 9:00
            target_time = datetime.min.time().replace(hour=9, minute=0)
        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")
    # Проверяем просто "в HH:MM" или "HH:MM"
    simple_time_match = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
    if simple_time_match:
        hour = int(simple_time_match.group(1))
        minute = int(simple_time_match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Если указанное время уже прошло сегодня — ставим на завтра
            if target_dt <= now:
                target_dt += timedelta(days=1)
            return target_dt.strftime("%Y-%m-%d %H:%M")
    return None


def replace_placeholders(content, user_now=None, current_time_str=None):
    """Заменяет плейсхолдеры типа {{current_time}} на реальные значения"""
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("Content must be a string")
    if not user_now:
        user_now = datetime.now(pytz.UTC)
    if not current_time_str:
        current_time_str = user_now.strftime("%H:%M")
    # Форматируем дату по-русски
    months = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
    content = content.replace("{{current_time}}", current_time_str)
    content = content.replace("{{current_date}}", current_date_str)
    content = content.replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d"))
    content = content.replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))
    return content


# ═══════════════════════════════════════════════════════════════════════════════
# Единый нормализатор title/description для задач (агентских и пользовательских)
# ═══════════════════════════════════════════════════════════════════════════════

_TASK_GARBAGE_PATS = [
    r'^\[РОЛЬ\]\s*',
    r'^ТВОЯ РОЛЬ:\s*',
    r'^\[АВТОПИЛОТ[^\]]*\]\s*',
    r'^📌\s*Правила[^\n]*\n?',
    r'^ПРАВИЛА И ПРЕДПОЧТЕНИЯ[^\n]*\n?',
    r'^ТЫ[:\s]',
    r'^\[?(РОЛЬ|АВТОПИЛОТ|ЦЕЛИ|КОНКРЕТНЫЙ ПЛАН|SYSTEM|ROLE|CONTEXT)\]?\s*:?\s*',
    r'^(Твоя роль|Ты агент|Ты являешься|Ты специалист)[^\n]*\n?',
    r'^[📌🎯✅]\s*(Правила|Цели|Инструкц)[^\n]*\n?',
    r'^ОТВЕТЬ НА ВОПРОС[^:]*:\s*',
    r'^Твои интеграции:[^\n]*\n?',
    # Координаторские/делегационные паттерны
    r'^Твоё задание:\s*',
    r'^Поручено\s+\S+:\s*',
    r'^\S+ только что сохранил[аи]? новые контакты\.\s*Результат \S+ работы:\s*',
    r'^Результат \S+ работы:\s*',
    r'^\S+,\s*передаю тебе задачу:\s*',
    r'^Контекст — уже сделано командой:[^\n]*\n?',
]
_TASK_PERSONALITY_MARKERS = (
    'специалист в команде', 'циник,', 'pr служба', 'координатор команды',
    'выгоревший', 'любит читать', 'агент пишет первым', 'пишет первым',
    'Твои интеграции:', 'твои интеграции:',
)

# Паттерн для обрезки внутреннего координационного контекста из description
_TASK_DESC_STRIP_PATS = [
    r'(?:\n|^)Контекст — уже сделано командой:.*',
    r'(?:\n|^)Твоя задача: немедленно отправь outreach-письма.*',
    r'(?:\n|^)Параметры: recipient_email=.*',
]


def normalize_task_title(raw_title: str, agent_name: str = None, max_len: int = 200) -> tuple:
    """Нормализует заголовок задачи. Возвращает (short_title, overflow_for_description).

    - Удаляет мусор (system prompt, [АВТОПИЛОТ], имя агента)
    - Берёт первое предложение
    - Сокращает до max_len на границе слов
    - Остаток текста возвращает для description

    Args:
        raw_title: исходный текст задачи
        agent_name: имя агента для удаления из начала
        max_len: максимальная длина title (по умолчанию 200)

    Returns:
        (title, overflow) — title: str до max_len, overflow: str остаток (может быть пустым)
    """
    if not raw_title or not raw_title.strip():
        return (f"Задача агента {agent_name}" if agent_name else 'Задача'), ''

    text = raw_title.strip()

    # 1. Удаляем мусорные системные префиксы (повторяем до стабилизации)
    for _pass in range(3):
        prev = text
        for pat in _TASK_GARBAGE_PATS:
            m = re.match(pat, text, re.IGNORECASE)
            if m:
                text = text[m.end():].lstrip()
        if text == prev:
            break

    # 2. Удаляем имя агента из начала ("Кристина: Кристина, ..." → "...")
    if agent_name:
        for _ in range(2):
            cleaned = re.sub(
                r'^' + re.escape(agent_name) + r'[\s,:.!]*',
                '', text, flags=re.IGNORECASE,
            ).strip()
            if cleaned:
                text = cleaned

    # 3. Убираем personality-маркеры (если AI утёк описание характера)
    text_lower = text.lower()
    if any(m in text_lower for m in _TASK_PERSONALITY_MARKERS):
        # Берём первое предложение БЕЗ маркера
        sentences = re.split(r'[.!]\s+', text)
        clean_sentences = [s.strip() for s in sentences
                           if s.strip() and not any(m in s.lower() for m in _TASK_PERSONALITY_MARKERS)]
        text = clean_sentences[0] if clean_sentences else ''

    # 3b. Если после чистки осталось только generic-слово — fallback
    _gen_only = text.strip().lower() in (
        'выполнено', 'готово', 'done', 'ok', 'ок', 'задача', 'нет данных',
        'результат', 'принято', 'сделано', 'задание',
    )
    if len(text.strip()) < 5 or _gen_only:
        fallback = f"Задача агента {agent_name}" if agent_name else 'Задача'
        return fallback, ''

    # 4. Берём первое предложение (до первой точки, ;, или перевода строки)
    first_sentence = text
    overflow = ''
    split_match = re.search(r'[.;]\s|\n', text)
    if split_match and split_match.start() > 10:
        first_sentence = text[:split_match.start()].strip()
        overflow = text[split_match.end():].strip()

    # 5. Сокращаем если длиннее max_len — обрезаем на границе слов
    if len(first_sentence) > max_len:
        if not overflow:
            overflow = first_sentence
        # Обрезаем на границе слов, сохраняя читаемость
        words = first_sentence.split()
        short = ''
        for w in words:
            candidate = (short + ' ' + w).strip() if short else w
            if len(candidate) > max_len:
                break
            short = candidate
        first_sentence = short or first_sentence[:max_len]

    return first_sentence.strip(), overflow.strip()


def clean_technical_details(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Text must be a string")
    import logging
    logger = logging.getLogger(__name__)
    original_text = text
    import re
    
    # КРИТИЧЕСКИ ВАЖНО: Удаляем DeepSeek DSML теги (вызовы функций через спец. формат)
    # Стратегия: удаляем всё после первого DSML тега до конца текста
    if '<｜DSML｜' in text or '<|DSML|' in text or '</｜DSML｜' in text or '</|DSML|' in text:
        # Находим первый DSML тег и обрезаем всё после него
        dsml_patterns = [
            r'<｜DSML｜.*',
            r'<\|DSML\|.*',
            r'</｜DSML｜.*',
            r'</\|DSML\|.*'
        ]
        for pattern in dsml_patterns:
            match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
            if match:
                # Обрезаем текст до начала DSML тега
                text = text[:match.start()].strip()
                logger.info(f"[CLEAN] Removed DSML content, kept: '{text[:100]}...'")
                break
    
    # Если весь текст - это DSML, возвращаем пустую строку
    if text.strip().startswith('<｜DSML｜') or text.strip().startswith('<|DSML|'):
        logger.warning("[CLEAN] Entire response was DSML, returning empty")
        return ""
    
    # Дополнительная очистка оставшихся DSML следов
    text = re.sub(r'<｜DSML｜[^>]*>', "", text, flags=re.DOTALL)
    text = re.sub(r'<\|DSML\|[^>]*>', "", text, flags=re.DOTALL)
    text = re.sub(r'</｜DSML｜[^>]*>', "", text, flags=re.DOTALL)
    text = re.sub(r'</\|DSML\|[^>]*>', "", text, flags=re.DOTALL)
    text = re.sub(r'DSML.*?>', "", text, flags=re.DOTALL)
    
    # Удаляем XML-подобные вызовы функций (DeepSeek иногда генерирует их)
    text = re.sub(r'<function_calls>.*?</function_calls>', '', text, flags=re.DOTALL)
    text = re.sub(r'<function_call>.*?</function_call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<invoke\s+name="[^"]*">.*?</invoke>', '', text, flags=re.DOTALL)
    text = re.sub(r'<call\s+function="[^"]*">.*?</call>', '', text, flags=re.DOTALL)
    text = re.sub(r'<arg\s+name="[^"]*">[^<]*</arg>', '', text, flags=re.DOTALL)
    text = re.sub(r'<parameter\s+name="[^"]*">[^<]*</parameter>', '', text, flags=re.DOTALL)
    
    # Удаляем <thinking> блоки
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    # Удаляем незакрытые <thinking> теги
    text = re.sub(r'<thinking>.*', '', text, flags=re.DOTALL)
    
    # Удаляем вызовы функций в квадратных скобках: [add_task(...)]
    before = text
    text = re.sub(r"\[[\w_]+\([^]]*\)\]", "", text)
    if before != text:
        pass
    # Удаляем пустые квадратные скобки
    before = text
    text = re.sub(r"\[\s*\]", "", text)
    if before != text:
        pass
    # Удаляем названия функций (с скобками и без, включая аргументы)
    before = text
    text = re.sub(
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|cancel_delegation|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details|research_topic|find_partners)\s*\([^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if before != text:
        pass
    # Заменяем НАЗВАНИЯ инструментов на человеко-понятные описания
    # AI иногда упоминает tool names в тексте — заменяем чтобы текст оставался связным
    _TOOL_HUMAN_NAMES = {
        'web_search': 'поиск в интернете',
        'quick_topic_search': 'быстрый поиск',
        'research_topic': 'исследование темы',
        'research_and_plan': 'исследование и планирование',
        'find_and_message_relevant_users': 'поиск контактов',
        'find_relevant_contacts_for_task': 'поиск контактов',
        'find_partners': 'поиск партнёров',
        'search_users': 'поиск людей',
        'send_email': 'отправка письма',
        'send_outreach_email': 'отправка письма',
        'reply_to_outreach_email': 'ответ на письмо',
        'send_follow_up_email': 'отправка напоминания',
        'check_emails': 'проверка почты',
        'negotiate_by_email': 'переписка по email',
        'delegate_task': 'делегирование задачи',
        'add_task': 'создание задачи',
        'complete_task': 'завершение задачи',
        'delete_task': 'удаление задачи',
        'edit_task': 'редактирование задачи',
        'list_tasks': 'список задач',
        'get_task_details': 'детали задачи',
        'create_goal': 'создание цели',
        'update_goal': 'обновление цели',
        'update_goal_progress': 'обновление прогресса',
        'complete_goal': 'завершение цели',
        'list_goals': 'список целей',
        'delete_goal': 'удаление цели',
        'get_goal_progress': 'прогресс цели',
        'create_post': 'создание поста',
        'publish_to_telegram': 'публикация в Telegram',
        'publish_to_discord': 'публикация в Discord',
        'generate_image': 'генерация изображения',
        'generate_marketing_content': 'создание контента',
        'run_agent_action': 'действие агента',
        'set_reminder': 'напоминание',
        'get_weather_info': 'прогноз погоды',
        'get_news_trends': 'анализ новостей',
        'analyze_situation_and_suggest_tasks': 'анализ ситуации',
        'analyze_group_opportunities': 'анализ возможностей',
        'start_delegation_campaign': 'запуск кампании делегирования',
        'start_content_campaign': 'запуск контент-кампании',
        'start_email_campaign': 'запуск email-кампании',
        'manage_content_campaign': 'управление контент-кампанией',
        'manage_delegation_campaign': 'управление кампанией',
        'get_delegation_progress': 'статус делегирования',
        'send_message_to_user': 'сообщение пользователю',
        'save_email_contact': 'сохранение контакта',
        'list_email_contacts': 'список контактов',
        'update_profile': 'обновление профиля',
        'update_user_memory': 'обновление заметок',
    }
    # Сначала заменяем tool names с вызовом (...) — полный формат
    for _tname, _thuman in _TOOL_HUMAN_NAMES.items():
        text = re.sub(r'\b' + re.escape(_tname) + r'\s*\([^)]*\)', _thuman, text, flags=re.IGNORECASE)
    # Затем заменяем plain tool names (без скобок) на человеко-понятные
    for _tname, _thuman in _TOOL_HUMAN_NAMES.items():
        text = re.sub(r'\b' + re.escape(_tname) + r'\b', _thuman, text, flags=re.IGNORECASE)
    # Оставшиеся tool names без человеко-понятного аналога — удаляем вместе с предлогом
    _ALL_TOOL_NAMES = (
        r'start_delegation_campaign|start_content_campaign|'
        r'schedule_background_task|'
        r'set_contact_alert|'
        r'check_time_conflicts|cancel_delegation|'
        r'get_message_status|reschedule_task|'
        r'restore_task|accept_delegated_task|reject_delegated_task|'
        r'set_content_strategy|edit_post|get_posts|delete_post|'
        r'list_marketplace|get_system_status|'
        r'get_incoming_messages|reply_to_user_message|'
        r'add_email_leads|update_email_campaign|'
        r'decrypt_token|encrypt_token|install_script|skip_task|'
        r'run_user_script|toggle_autonomous_feature|save_user_rule|'
        r'get_email_campaign_status|pause_email_campaign|resume_email_campaign|'
        r'set_goal_deadline|assign_task_to_agent'
    )
    _tool_re = r'\b(?:' + _ALL_TOOL_NAMES + r')\b'
    text = re.sub(r'(?:через|используя|использую|использовать|запущу|запускаю|инструмент|функцию?|вызову?)\s+' + _tool_re, '', text, flags=re.IGNORECASE)
    text = re.sub(_tool_re, '', text, flags=re.IGNORECASE)
    # Удаляем оставшиеся ТЕХНИЧЕСКИЕ вызовы функций (только snake_case с минимум 2 частями)
    # НЕ трогаем обычный текст вроде "Python (язык)" или "AI (artificial intelligence)"
    before = text
    text = re.sub(r'\b[a-z]+_[a-z_]+\([^)]*\)', '', text)
    if before != text:
        pass
    # Удаляем фразы о вызове функций
    patterns_to_remove = [
        r"вызываю\s+\w+(\(\))?",
        r"вызову\s+\w+(\(\))?",
        r"вызови\s+(инструмент\s+)?\w+(\(\))?",
        r"используй\s+(инструмент\s+)?\w+(\(\))?",
        r"сейчас\s+вызову",
        r"буду\s+вызывать",
        r"нужно\s+вызвать\s+\w+",
        r"можно\s+вызвать\s+\w+",
        r"Args for.*?(?=\n|$)",
        r"🔧\s*ВЫПОЛНЕННЫЕ ФУНКЦИИ:.*?(?=\n\n|\Z)",
        r"🔧\s*\*\*Выполняю:\*\*.*?(?=\n|$)",
        r"📋\s*\*\*Результат:\*\*.*?(?=\n\n|\Z)",
        r"ВЫПОЛНЕННЫЕ ФУНКЦИИ.*?(?=\n\n|\Z)",
    ]
    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)
    # Удаляем блоки кода Python - ТОЛЬКО если они содержат техническую информацию
    # Не удаляем json блоки, которые могут содержать полезные данные
    text = re.sub(r"```python.*?```", "", text, flags=re.DOTALL)
    # Удаляем пустые блоки кода
    text = re.sub(r"```\s*```", "", text)
    # КРИТИЧЕСКИ ВАЖНО: Удаляем JSON блоки с tool_calls - они не должны попадать в ответ пользователю
    # Удаляем полные JSON блоки с tool_calls
    text = re.sub(r'```json\s*\{[^}]*"tool_calls"[^}]*\}```', "", text, flags=re.DOTALL)
    text = re.sub(r"```json.*?tool_calls.*?(```|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Удаляем любые оставшиеся JSON блоки с tool_calls
    text = re.sub(r'\{[^}]*"tool_calls"[^}]*\}', "", text, flags=re.DOTALL)
    text = re.sub(r'"tool_calls"\s*:\s*\[.*?\]', "", text, flags=re.DOTALL)
    # Удаляем любые JSON блоки в кодовых блоках, если они содержат tool_calls
    text = re.sub(r"```json[\s\S]*?tool_calls[\s\S]*?```", "", text, flags=re.IGNORECASE)
    # Удаляем любые оставшиеся ```json блоки
    text = re.sub(r"```json[\s\S]*?```", "", text, flags=re.IGNORECASE)
    # Удаляем эмодзи - ТОЛЬКО технические, оставляем подходящие для общения и форматирования
    # Удаляем markdown-форматирование (**жирный**, *курсив*, ### заголовки) — Telegram не рендерит
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **bold** → bold
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'\1', text)  # *italic* → italic
    text = re.sub(r'^#{1,3}\s+', '', text, flags=re.MULTILINE)  # ### Header → Header
    
    # Удаляем маркеры списков (-, *, •, 1.) в начале строк — мессенджер-стиль
    text = re.sub(r'^\s*[-\*•–—]\s+', '', text, flags=re.MULTILINE)  # - item → item
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)  # 1. item → item
    text = re.sub(r'^\s*[a-zа-яё][.)]\s+', '', text, flags=re.MULTILINE)  # a) item → item
    
    # Удаляем фразы-филлеры в начале ответа: «Отлично!», «О, отлично!», «Супер!» и т.д.
    # НЕ удаляем если это часть осмысленного слова: «Отличная работа»
    # Ловит: "Отлично! Нашла...", "О, отлично! Вижу...", "Супер, начинаю..."
    _filler_words = r'(?:Отлично|Супер|Прекрасно|Замечательно|Понятно|Окей|Ок|Класс|Ура|Принято)'
    # Удаляем филлер + знак препинания + опц. пробел в начале строки
    text = re.sub(
        r'^\s*(?:🚀|🎯|✅|💡|📊|⚡|🔥|О,?\s+)?\s*' + _filler_words + r'[!,.]?\s+',
        '', text, flags=re.IGNORECASE
    ).strip()
    # Если весь текст — одно слово-филлер, удаляем тоже
    text = re.sub(
        r'^\s*(?:🚀|🎯|✅|💡|📊|⚡|🔥|О,?\s+)?\s*' + _filler_words + r'[!.]?\s*$',
        '', text, flags=re.IGNORECASE
    ).strip()
    
    # Эмодзи НЕ удаляем — агент может использовать их когда уместно
    
    # Удаляем аннотации инструментов если LLM скопировал формат из истории: [Действия: ...]
    text = re.sub(r'^\[Действия:[^\]]*\]\s*', '', text)

    # Удаляем технические префиксы tool-результатов если они протекли в финальный ответ
    text = re.sub(r'^TASK_UPDATED:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^TASK_DELETED:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^TASK_COMPLETED:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^ERROR:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^SELF_DELEGATION_ERROR:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^NO TIME SPECIFIED\..*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^ВРЕМЯ НЕ УКАЗАНО\..*$', '', text, flags=re.MULTILINE)
    # Удаляем ведущие ": " или ": " артефакты (после clean срезается слово перед двоеточием)
    text = re.sub(r'^:\s+', '', text)
    # Убираем висячие предлоги/артефакты после удаления tool names: "через ." → "."
    text = re.sub(r'\b(через|используя|использую|инструмент|функцию?)\s+([.!?,;:\n])', r'\2', text, flags=re.IGNORECASE)
    # Убираем двойные пробелы после замен
    text = re.sub(r'  +', ' ', text)
    # Нормализуем отступы: \n\n\n+ → \n\n, а \n\n → \n (мессенджер-стиль, без лишних пустых строк)
    text = re.sub(r'[ \t]+\n', '\n', text)   # trailing whitespace на строках
    text = re.sub(r'\n[ \t]+\n', '\n', text) # строки из одних пробелов/tab → пустые
    text = re.sub(r'\n{3,}', '\n\n', text)   # тройные+ → двойные
    text = re.sub(r'\n\n', '\n', text)        # двойные → одинарные
    
    # Декодируем HTML entities ПЕРЕД очисткой email-артефактов
    text = re.sub(r'&(?:nbsp|amp|lt|gt|quot|#\d+);?', ' ', text)
    
    # Также убираем HTML-артефакты из email/IMAP которые могут пролезть через агентов
    # Полные mailto-ссылки: <a href="mailto:email">text</a> → email
    text = re.sub(r'<a[^>]*href=["\']mailto:([^"\'>\s]+)["\'][^>]*>[^<]*</a>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    # Незакрытые mailto: <a href="mailto:email">text → email
    text = re.sub(r'<a[^>]*href=["\']mailto:([^"\'>\s]+)["\'][^>]*>[^<]*', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>', r'\1', text)
    text = re.sub(r'</?[a-zA-Z][^>]*>', '', text, flags=re.DOTALL)
    # Артефакт разорванного mailto: @domain.com">email@domain.com → email@domain.com
    text = re.sub(r'@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', text)
    # То же с mailto: mailto:email@domain.com">email → email
    text = re.sub(r'mailto:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', text)
    # Тот же паттерн в скобках — > ОБЯЗАТЕЛЕН чтобы не ломать (email@domain.com)
    text = re.sub(r'\(@?[a-zA-Z0-9.-]*\.[a-zA-Z]{2,}["\']?\s*>\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\)', r'(\1)', text)
    # Паттерн (mailto:email">email) → (email) — > ОБЯЗАТЕЛЕН
    text = re.sub(r'\(mailto:[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\)', r'(\1)', text)
    text = re.sub(r'["\']?\s*/?\s*>(?=\S)', '', text)
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: если после очистки ничего не осталось,
    # значит AI вернул ТОЛЬКО технические детали — НЕ возвращать оригинал!
    if not text.strip():
        logger.debug(f"[CLEAN] Content was completely cleaned, returning fallback. Original: '{original_text[:200]}'")
        return ""
    if original_text != text:
        logger.debug(f"[CLEAN] Original: '{original_text[:100]}...' -> Cleaned: '{text[:100]}...'")
    return text.strip()
# Alias for backward compatibility
clean_content = clean_technical_details


def sanitize_live_team_chat_text(
    text,
    *,
    anchor_type: str = '',
    speaker_name: str = '',
    target_name: str = '',
    max_chars: int | None = None,
):
    """Универсальная финальная нормализация «живого» текста для чата команды.

    Применяется перед сохранением в Interaction, чтобы убирать структурные блоки
    вроде «Данные для работы:», markdown-списки и избыточно длинные техничные
    формулировки.
    """
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)

    import re

    cleaned = clean_technical_details(text).strip()
    if not cleaned:
        return ''

    cleaned = cleaned.replace('\r\n', '\n').replace('\r', '\n')
    # Срезаем текст начиная со структурных секций ТЗ
    cleaned = re.split(
        r'(?i)\b(?:данные для работы|ключевые данные|детали|описание|задача|шаги|план|ожидание в отч[её]те|каналы)\s*:',
        cleaned,
        maxsplit=1,
    )[0]

    # Убираем markdown-оформление и списки
    cleaned = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', cleaned)
    cleaned = re.sub(r'^\s*#{1,4}\s*', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*[•\-\*]\s+', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'^\s*\d+[.)\]]\s+', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'\n{2,}', '\n', cleaned)

    # Убираем строки-заголовки вида "Раздел:"
    lines = []
    for ln in cleaned.split('\n'):
        s = ln.strip()
        if not s:
            continue
        if s.endswith(':') and len(s) <= 70 and not re.search(r'[.!?]', s[:-1]):
            break
        lines.append(s)
    cleaned = ' '.join(lines).strip()
    cleaned = re.sub(r'\s{2,}', ' ', cleaned)

    if speaker_name:
        cleaned = re.sub(
            rf'^\s*{re.escape(speaker_name)}\s*,?\s*',
            '',
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

    _a = (anchor_type or '').lower().strip()
    if max_chars is None:
        if _a in ('agent_delegation', 'coordinator_assignment', 'goal_autopilot_assignment'):
            max_chars = 520
        elif _a == 'coordinator_result':
            max_chars = 900
        else:
            max_chars = 1100

    if len(cleaned) > max_chars:
        cut = cleaned[:max_chars].rsplit(' ', 1)[0].strip()
        cleaned = (cut or cleaned[:max_chars]).rstrip(' ,;:.-')

    # Runtime-guard без потери содержания: если после очистки остались явные
    # префиксы брифа, мягко убираем их, но НЕ заменяем весь текст шаблоном.
    _lower = cleaned.lower()
    if _a in ('agent_delegation', 'coordinator_assignment', 'goal_autopilot_assignment') and any(
        p in _lower for p in ('на основе анализа', 'ключевые данные', 'данные для работы', 'задача:')
    ):
        cleaned = re.sub(
            r'(?i)^(?:на\s+основе\s+анализа[^.?!]*[.?!]\s*)+',
            '',
            cleaned,
        ).strip()
        cleaned = re.sub(r'\s{2,}', ' ', cleaned)

    return cleaned.strip()

def get_context_from_db(user_id, limit=10):
    """Get chat context from Interaction table"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return []
        # Get history_cleared_at timestamp
        cleared_at = user.history_cleared_at
        # Get last N interactions after clear timestamp
        query = session.query(Interaction).filter(Interaction.user_id == user.id)
        if cleared_at:
            query = query.filter(Interaction.created_at > cleared_at)
        interactions = query.order_by(Interaction.created_at.asc()).limit(limit * 2).all()
        # Convert to context format - group by user-ai pairs
        context = []
        i = 0
        while i < len(interactions) - 1:
            # Find next user message
            while i < len(interactions) and interactions[i].message_type != 'user':
                i += 1
            if i >= len(interactions):
                break
            user_msg = interactions[i]
            i += 1
            # Find next ai message after user
            while i < len(interactions) and interactions[i].message_type != 'ai':
                i += 1
            if i >= len(interactions):
                break
            ai_msg = interactions[i]
            i += 1
            context.append({
                'user': user_msg.content,
                'agent': ai_msg.content
            })
        return context
    except Exception as e:
        logger.error(f"Error getting context from DB: {e}")
        return []
    finally:
        session.close()


def get_weather_info(city, cache_ttl_minutes=30):
    """Get weather information for a city with caching.
    
    DEPRECATED: Используйте api_client.get_weather() (async) — не блокирует event loop,
    единый кэш, rate-limiting. Эта функция оставлена для обратной совместимости.
    """
    import warnings
    warnings.warn(
        "get_weather_info() is sync and blocks event loop. "
        "Use api_client.get_api_client().get_weather() instead.",
        DeprecationWarning, stacklevel=2
    )
    # Нормализуем город
    city = city.strip()
    if not city:
        return None
    cache_key = f"weather_{city.lower()}"
    ttl_seconds = cache_ttl_minutes * 60
    # Проверяем Redis кеш
    cached_data = _redis_get(cache_key)
    if cached_data:
        logger.info(f"[WEATHER CACHE] Using Redis cached weather for {city}")
        # Запускаем фоновое обновление если данные старше половины TTL
        try:
            if redis_client:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left > 0 and ttl_left < ttl_seconds / 2:  # ttl_left > 0 проверяет что ключ существует
                    pass  # background refresh removed (use api_client.get_weather() instead)
        except Exception as e:
            logger.warning(f"[WEATHER CACHE] Failed to check TTL for {cache_key}: {e}")
        return cached_data
    # Проверяем in-memory fallback
    cached_data = _memory_get(weather_cache, cache_key)
    if cached_data:
        logger.info(f"[WEATHER CACHE] Using memory cached weather for {city}")
        return cached_data
    # Нет данных в кэше - загружаем синхронно (только при первом запросе, deprecated path)
    logger.info(f"[WEATHER] No cache for {city}, loading synchronously (deprecated)")
    return _load_weather_sync(city)


def _load_weather_sync(city):
    """Load weather data synchronously from API"""
    try:
        api_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHERMAP_API_KEY}&units=metric&lang=ru"
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            temp = data['main']['temp']
            weather_desc = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind_speed = data['wind']['speed']
            # Формируем строку погоды
            weather_str = f"{city}: {temp}°C, {weather_desc}, влажность {humidity}%, ветер {wind_speed} м/с"
            # Кешируем результат
            cache_key = city.lower()
            redis_key = f"weather_{cache_key}"
            _redis_set(redis_key, weather_str, 30 * 60)  # 30 minutes TTL
            _memory_set(weather_cache, redis_key, weather_str)
            logger.info(f"[WEATHER] Fetched weather for {city}: {weather_str}")
            return weather_str
        else:
            logger.warning(f"[WEATHER] Failed to fetch weather for {city}: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"[WEATHER] Error fetching weather for {city}: {e}")
        return None


def get_news_info(city=None, cache_ttl_minutes=120):
    """Get news information with caching.
    
    DEPRECATED: Используйте api_client.get_news() (async) — не блокирует event loop,
    единый кэш, rate-limiting. Эта функция оставлена для обратной совместимости.
    """
    import warnings
    warnings.warn(
        "get_news_info() is sync and blocks event loop. "
        "Use api_client.get_api_client().get_news() instead.",
        DeprecationWarning, stacklevel=2
    )
    # Ключ кеша зависит от города
    if city and city.strip():
        cache_key = f"news_{city.lower().strip()}"
        search_query = f"{city} Россия"
    else:
        cache_key = "russian_news_general"
        search_query = "Россия"
    ttl_seconds = cache_ttl_minutes * 60
    # Проверяем Redis кеш
    cached_data = _redis_get(cache_key)
    if cached_data:
        logger.info(f"[NEWS CACHE] Using Redis cached news for {cache_key}")
        # Запускаем фоновое обновление если данные старше половины TTL
        try:
            if redis_client:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left > 0 and ttl_left < ttl_seconds / 2:  # ttl_left > 0 проверяет что ключ существует
                    pass  # background refresh removed (use api_client.get_news() instead)
        except Exception as e:
            logger.warning(f"[NEWS CACHE] Failed to check TTL for {cache_key}: {e}")
        return cached_data
    # Проверяем in-memory fallback
    cached_data = _memory_get(news_cache, cache_key)
    if cached_data:
        logger.info(f"[NEWS CACHE] Using memory cached news for {cache_key}")
        return cached_data
    # Нет данных в кэше - загружаем синхронно (только при первом запросе, deprecated path)
    logger.info(f"[NEWS] No cache for {cache_key}, loading synchronously (deprecated)")
    return _load_news_sync(city)


def _load_news_sync(city=None):
    """Load news data synchronously from API"""
    global _news_backoff_until
    try:
        # 429 backoff check
        if time.time() < _news_backoff_until:
            remaining = int((_news_backoff_until - time.time()) / 60)
            logger.info(f"[NEWS] NewsAPI backoff active, {remaining} min remaining — skipping request")
            return None

        # Ключ кеша зависит от города
        if city and city.strip():
            cache_key = f"news_{city.lower().strip()}"
            search_query = f"{city} Россия"
        else:
            cache_key = "russian_news_general"
            search_query = "Россия"
        # Запрашиваем новости
        api_url = f"https://newsapi.org/v2/everything?q={search_query}&language=ru&sortBy=publishedAt&apiKey={NEWSAPI_API_KEY}&pageSize=5"
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'ok' and data.get('articles'):
                articles = data['articles']
                news_items = []
                for article in articles[:3]:  # Берем только 3 новости для краткости
                    title = article.get('title', '').strip()
                    if title and title != '[Removed]':
                        news_items.append(f"• {title}")
                if news_items:
                    if city and city.strip():
                        news_str = f"Новости {city}:\n" + "\n".join(news_items)
                    else:
                        news_str = "Свежие новости России:\n" + "\n".join(news_items)
                else:
                    news_str = "Новости временно недоступны"
                # Кешируем результат
                _redis_set(cache_key, news_str, 120 * 60)  # 2 hours TTL
                _memory_set(news_cache, cache_key, news_str)
                logger.info(f"[NEWS] Fetched {len(news_items)} news items for {cache_key}")
                return news_str
            else:
                logger.warning(f"[NEWS] No articles in response: {data}")
                return None
        else:
            if response.status_code == 429:
                _news_backoff_until = time.time() + 43200  # 12 часов
                logger.warning(
                    f"[NEWS] 429 from NewsAPI — dev quota exhausted. "
                    f"Backoff 12h. Body: {response.text[:200]}"
                )
            else:
                logger.warning(f"[NEWS] Failed to fetch news: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"[NEWS] Error fetching news: {e}")
        return None


def _load_finance_sync(symbol, asset_type='stock'):
    """DEPRECATED: Alpha Vantage removed. Returns None."""
    return None

def preload_common_data():
    """
    Предварительно загружает данные для популярных городов и общие новости.
    Вызывается при старте бота для заполнения кэша.
    """
    logger.info("[CACHE] Starting preload of common data")
    common_cities = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань"]
    from ai_integration.api_client import get_api_client
    import asyncio
    client = get_api_client()

    async def _run_preload():
        for city in common_cities:
            try:
                logger.info(f"[CACHE] Preloading weather for {city}")
                await client.get_weather(city)
            except Exception as e:
                logger.warning(f"[CACHE] Failed to preload weather for {city}: {e}")
        # Properly close the session so asyncio.run() doesn't leave unclosed connectors
        await client.close()

    try:
        asyncio.run(_run_preload())
    except RuntimeError:
        # Already inside an event loop (e.g. tests) — skip preload
        logger.debug("[CACHE] Skipping weather preload — event loop already running")
    except Exception as e:
        logger.warning(f"[CACHE] Preload failed: {e}")
    finally:
        # Ensure _session is reset so _get_session() re-creates it in the main app loop
        client._session = None

    # NewsAPI preload removed — dev quota too small (100/day),
    # news loaded lazily with 6h cache via api_client.get_news()
    logger.info("[CACHE] Preload completed (weather only, news lazy)")


def generate_unified_recommendations(context_type, user_id=None, title=None, description=None, task_count=None, overdue_count=None, profile=None, weather_info=None, partner_recommendations=None, tasks_list=None):
    """
    Универсальная функция генерации рекомендаций, объединяющая все типы

    Args:
        context_type: Тип контекста ('task_creation', 'personalized', 'fallback')
        user_id: Telegram ID пользователя (для personalized)
        title: Название задачи (для task_creation)
        description: Описание задачи (для task_creation)
        task_count: Количество задач (для fallback)
        overdue_count: Количество просроченных (для fallback)
        profile: Профиль пользователя (для fallback/personalized)
        weather_info: Информация о погоде (для fallback)
        partner_recommendations: Рекомендации партнеров (для fallback)
        tasks_list: Список задач (для fallback)

    Returns:
        List[str]: Список рекомендаций
    """
    recommendations = []

    if context_type == 'task_creation':
        # Базовые рекомендации на основе ключевых слов в задаче
        title_lower = (title or "").lower()
        desc_lower = (description or "").lower()

        if any(word in title_lower + desc_lower for word in ['встреча', 'митап', 'конференция']):
            recommendations.extend([
                "Подготовьте презентацию или вопросы для обсуждения",
                "Проверьте время и место за день до события"
            ])

        if any(word in title_lower + desc_lower for word in ['спорт', 'тренировка', 'бег']):
            recommendations.extend([
                "Возьмите с собой воду и полотенце",
                "Сделайте разминку перед началом"
            ])

        if any(word in title_lower + desc_lower for word in ['работа', 'проект', 'задача']):
            recommendations.extend([
                "Разбейте задачу на маленькие шаги",
                "Установите таймер для работы без отвлечений"
            ])

        return recommendations[:3]

    elif context_type == 'personalized':
        # Рекомендации на основе истории поиска и интересов
        if not user_id:
            return []

        from models import Session, User
        import json
        from .memory import decrypt_data

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.long_term_memory:
                ltm = json.loads(decrypt_data(user.long_term_memory))

                search_history = ltm.get('search_history', [])
                interests = ltm.get('interests', {})

                if not search_history:
                    return []

                # Топ тем по частоте поиска
                top_topics = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:3]
                for topic, count in top_topics:
                    if count >= 2:
                        recommendations.append(f"Продолжить изучение {topic}")

                # Недавние поиски для углубления
                recent_searches = search_history[-3:]
                for search in recent_searches:
                    query = search['query']
                    if len(query.split()) > 1:
                        recommendations.append(f"Углубить исследование: {query}")

                return recommendations[:5]
        finally:
            session.close()

        return []

    elif context_type == 'fallback':
        # Персонализированные fallback сообщения
        if not profile:
            return ["Чем могу помочь сегодня?"]

        # Компоненты сообщения
        weather_part = ""
        if weather_info:
            weather_part = f"🌤 {weather_info.split(':')[1].split(',')[0].strip()} сегодня. "

        partner_part = ""
        if partner_recommendations and "@" in partner_recommendations:
            partner_match = partner_recommendations.split("@")[1].split()[0]
            if partner_match:
                partner_part = f" Кстати, @{partner_match} может быть интересен для твоих целей."

        # Анализ профиля
        goals_mention = ""
        interests_mention = ""
        skills_mention = ""

        if profile.goals:
            goals = [g.strip() for g in profile.goals.split(',') if g.strip()]
            if goals:
                goals_mention = f" Учитывая твои цели ({goals[0]}),"

        if profile.interests:
            interests = [i.strip() for i in profile.interests.split(',') if i.strip()]
            if interests:
                interests_mention = f" {interests[0]} может вдохновить на новые идеи."

        if profile.skills:
            skills = [s.strip() for s in profile.skills.split(',') if s.strip()]
            if skills:
                skills_mention = f" Твои навыки в {skills[0]} могут пригодиться."

        # Контекст задач
        task_context = ""
        if tasks_list and len(tasks_list) > 0:
            first_task = tasks_list[0]
            if hasattr(first_task, 'title') and first_task.title:
                task_context = f" Например, задача '{first_task.title[:30]}...'"

        # Генерация сообщения
        context = "no_tasks" if task_count == 0 else "few_tasks"

        if context == "no_tasks":
            if goals_mention:
                message = f"{weather_part}Отличное время для движения к целям!{goals_mention} что можем сделать сегодня?{partner_part}"
            elif interests_mention:
                message = f"{weather_part}Чистый список задач - возможность для творчества.{interests_mention} Что вдохновляет тебя?{partner_part}"
            else:
                message = f"{weather_part}Вижу свободное время для роста.{skills_mention} Какие проекты заинтересуют?{partner_part}"
        else:
            message = f"{weather_part}Ты в продуктивном темпе с {task_count} задачами!{task_context} Как продвигается?{partner_part}"

        return [message]

    return []



# clean_technical_details — единственная версия определена выше
# Дубликат удалён при рефакторинге


def _normalize_position_case(position: str) -> str:
    """Нормализует должность: творительный/предложный → именительный падеж.
    Гибридный подход: словарь исключений + суффиксные правила.
    Примеры: маркетологом → Маркетолог, программистом → Программист,
             руководителем → Руководитель, дизайнером → Дизайнер
    """
    if not position:
        return position
    val = position.strip()
    # Аббревиатуры (CTO, CEO, COO, CFO, HR) — не трогаем
    if val.isupper() and len(val) <= 5:
        return val
    # Если содержит дефис и первая часть — аббревиатура (SMM-специалист, IT-директор) — не трогаем
    if '-' in val:
        parts = val.split('-', 1)
        if parts[0].isupper() and len(parts[0]) <= 5:
            return val
    low = val.lower()

    # --- Словарь исключений (неправильные / сложные формы) ---
    _exceptions = {
        'руководителем': 'Руководитель',
        'учителем': 'Учитель',
        'водителем': 'Водитель',
        'строителем': 'Строитель',
        'писателем': 'Писатель',
        'преподавателем': 'Преподаватель',
        'воспитателем': 'Воспитатель',
        'председателем': 'Председатель',
        'основателем': 'Основатель',
        'предпринимателем': 'Предприниматель',
        'управляющим': 'Управляющий',
        'ведущим': 'Ведущий',
        'старшим': 'Старший',
        'младшим': 'Младший',
        'главным': 'Главный',
    }
    exc = _exceptions.get(low)
    if exc:
        return exc

    # --- Суффиксные правила ---
    # -стом → -ст (программистом → Программист, аналитистом — нет, но аналистом тоже нет)
    # -ком → -к (физиком → Физик) — но не все!
    # -чиком → -чик (лётчиком → Лётчик)
    # -щиком → -щик (каменщиком → Каменщик)
    # -тором → -тор (директором → Директор, редактором → Редактор)
    # -ером → -ер (дизайнером → Дизайнер, инженером → Инженер)
    # -ёром → -ёр (стажёром → Стажёр)
    # -логом → -лог (маркетологом → Маркетолог, технологом → Технолог)
    # -ником → -ник (начальником → Начальник, чиновником → Чиновник)
    # -ом (generic) → try removing -ом

    suffix_rules = [
        ('чиком', 'чик'),    # лётчиком → лётчик
        ('щиком', 'щик'),    # каменщиком → каменщик
        ('тором', 'тор'),    # директором → директор
        ('сором', 'сор'),    # профессором → профессор
        ('ёром', 'ёр'),      # стажёром → стажёр
        ('ером', 'ер'),      # дизайнером → дизайнер, инженером → инженер
        ('логом', 'лог'),    # маркетологом → маркетолог
        ('ником', 'ник'),    # начальником → начальник
        ('стом', 'ст'),      # программистом → программист, специалистом → специалист
        ('дом', 'д'),        # методом — нет, но координатороМ уже выше
        ('тером', 'тер'),    # мастером → мастер
    ]
    for old_suf, new_suf in suffix_rules:
        if low.endswith(old_suf):
            base = val[:-len(old_suf)] + new_suf
            return base.capitalize()

    # Generic -ом removal (маркетологом already handled above)
    if low.endswith('ом') and len(low) > 4:
        base = val[:-2]
        return base.capitalize()
    
    # -ем окончание (менеджером уже покрыто через -ером)
    if low.endswith('ем') and len(low) > 4:
        base = val[:-2]
        return base.capitalize()

    # Если ничего не подошло — просто capitalize
    return val.capitalize()


def _normalize_skill_word(word: str) -> str:
    """Нормализует одно слово навыка/интереса: предложный → именительный.
    таргете → таргет, маркетинге → маркетинг, дизайне → дизайн, аналитике → аналитика
    """
    w = word.strip()
    if not w or len(w) < 4:
        return w
    low = w.lower()
    
    # Словарь исключений
    _exc = {
        'контент-маркетинге': 'контент-маркетинг',
        'программировании': 'программирование',
        'проектировании': 'проектирование',
        'тестировании': 'тестирование',
        'моделировании': 'моделирование',
        'управлении': 'управление',
        'продвижении': 'продвижение',
        'аналитике': 'аналитика',
        'логистике': 'логистика',
        'робототехнике': 'робототехника',
        'математике': 'математика',
        'физике': 'физика',
        'статистике': 'статистика',
        'экономике': 'экономика',
    }
    exc = _exc.get(low)
    if exc:
        return exc
    
    # Суффиксные правила: предложный → именительный (мужской род)
    # -ии → -ие (управлении → управление, программировании → программирование)
    if low.endswith('ии') and len(low) > 4:
        return w[:-1] + 'е'
    
    # -ике → -ика (аналитике → аналитика, физике → физика)
    if low.endswith('ике') and len(low) > 4:
        return w[:-1] + 'а'
    
    # Generic: if ends in согласная+е → remove е
    if low.endswith('е') and len(low) > 3:
        base = w[:-1]
        # Проверяем что перед -е стоит согласная (не гласная)
        vowels = set('аеёиоуыэюя')
        if base[-1].lower() not in vowels:
            return base
    
    return w


def _normalize_skills_text(skills_str: str) -> str:
    """Нормализует строку навыков/интересов: разделяет, нормализует каждое слово."""
    import re
    # Разделяем по , и " и "
    parts = re.split(r'\s*,\s*|\s+и\s+', skills_str)
    normalized = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Нормализуем каждое слово в фразе
        words = part.split()
        norm_words = [_normalize_skill_word(w) for w in words]
        normalized.append(' '.join(norm_words))
    return ', '.join(normalized)


def _normalize_company_name(company: str) -> str:
    """Нормализует название компании: косвенные падежи → именительный.
    'маркетинговом агентстве' → 'Маркетинговое агентство'
    'Яндексе' → 'Яндекс'
    """
    if not company:
        return company
    val = company.strip()
    low = val.lower()
    
    # Если это латиница (имя компании на англ.) — не трогаем
    import re
    if re.match(r'^[A-Za-z0-9\s\-\.]+$', val):
        return val
    
    # Словарь: косвенные формы существительных → именительный
    _noun_fixes = {
        'агентстве': 'агентство', 'агентства': 'агентство',
        'компании': 'компания',
        'студии': 'студия',
        'лаборатории': 'лаборатория',
        'корпорации': 'корпорация',
        'организации': 'организация',
        'фирме': 'фирма', 'фирмы': 'фирма',
        'банке': 'банк', 'банка': 'банк',
        'группе': 'группа', 'группы': 'группа',
        'холдинге': 'холдинг',
    }
    
    # Определяем род по существительному
    _neuter_nouns = {'агентство', 'бюро', 'ателье', 'издательство', 'предприятие'}
    _fem_nouns = {'компания', 'студия', 'лаборатория', 'корпорация', 'организация', 'фирма', 'группа'}
    _masc_nouns = {'банк', 'холдинг', 'фонд', 'центр', 'клуб'}
    
    words = val.split()
    
    # Однословные компании: "Яндексе" → "Яндекс", "Сбере" → "Сбер"
    if len(words) == 1:
        w = words[0]
        wl = w.lower()
        # Если заканчивается на -е после согласной — это предложный падеж
        vowels = set('аеёиоуыэюя')
        if wl.endswith('е') and len(wl) > 3 and wl[-2] not in vowels:
            return w[:-1]
        # -ии → -ия
        if wl.endswith('ии') and len(wl) > 4:
            return w[:-1] + 'я'
        return val
    
    # Многословные: нормализуем последнее слово (существительное)
    last_low = words[-1].lower()
    noun_fix = _noun_fixes.get(last_low)
    if noun_fix:
        words[-1] = noun_fix
        noun_lower = noun_fix.lower()
        
        # Нормализуем прилагательные перед существительным
        for i in range(len(words) - 1):
            w = words[i]
            wl = w.lower()
            if noun_lower in _neuter_nouns:
                if wl.endswith('ом') and len(wl) > 3:
                    words[i] = w[:-2] + 'ое'
                elif wl.endswith('ем') and len(wl) > 3:
                    words[i] = w[:-2] + 'ее'
            elif noun_lower in _fem_nouns:
                if wl.endswith('ой') and len(wl) > 3:
                    words[i] = w[:-2] + 'ая'
                elif wl.endswith('ей') and len(wl) > 3:
                    words[i] = w[:-2] + 'яя'
            elif noun_lower in _masc_nouns:
                if wl.endswith('ом') and len(wl) > 3:
                    words[i] = w[:-2] + 'ый'
                elif wl.endswith('ем') and len(wl) > 3:
                    words[i] = w[:-2] + 'ий'
    
    result = ' '.join(words)
    # Capitalize только первую букву всей строки
    if result and result[0].islower():
        result = result[0].upper() + result[1:]
    return result


# =============================================
# Cross-language profile normalization
# =============================================

# Fields to normalize for cross-language matching
_NORMALIZE_FIELDS = [
    'skills', 'interests', 'goals', 'city', 'country', 'company',
    'position', 'bio', 'status_text', 'current_plans'
]


def _is_ascii_only(text: str) -> bool:
    """Check if text contains only ASCII characters (likely already English)."""
    try:
        text.encode('ascii')
        return True
    except UnicodeEncodeError:
        return False


def _has_cyrillic(text: str) -> bool:
    """Check if text contains Cyrillic characters (likely Russian)."""
    return bool(re.search(r'[а-яА-ЯёЁ]', text))


async def normalize_profile_fields(profile) -> bool:
    """
    Normalize all profile text fields bidirectionally:
    - _normalized (EN) for cross-language matching
    - _normalized_ru (RU) for displaying to Russian users
    
    Sends two DeepSeek API calls: one for EN, one for RU.
    Skips translation if text is already in the target language.
    
    Returns True if normalization was performed, False otherwise.
    """
    # Collect non-empty fields
    fields = {}
    for field in _NORMALIZE_FIELDS:
        value = getattr(profile, field, None)
        if value and value.strip():
            fields[field] = value.strip()

    if not fields:
        for field in _NORMALIZE_FIELDS:
            setattr(profile, f'{field}_normalized', None)
            setattr(profile, f'{field}_normalized_ru', None)
        return False

    # Split fields by language
    ru_fields = {}  # Need EN translation
    en_fields = {}  # Need RU translation
    mixed_fields = {}  # Need both

    for field, value in fields.items():
        is_ascii = _is_ascii_only(value)
        has_cyr = _has_cyrillic(value)
        if is_ascii and not has_cyr:
            en_fields[field] = value
        elif has_cyr and not is_ascii:
            ru_fields[field] = value
        else:
            mixed_fields[field] = value

    # For pure EN fields: _normalized = lowercase, _normalized_ru needs translation
    for field, value in en_fields.items():
        setattr(profile, f'{field}_normalized', value.lower())

    # For pure RU fields: _normalized_ru = original, _normalized needs translation
    for field, value in ru_fields.items():
        setattr(profile, f'{field}_normalized_ru', value)

    # For mixed: need both translations
    for field, value in mixed_fields.items():
        pass  # Will be handled by API calls

    import aiohttp
    if not DEEPSEEK_API_KEY:
        # Fallback: just lowercase
        for field, value in fields.items():
            setattr(profile, f'{field}_normalized', value.lower())
            setattr(profile, f'{field}_normalized_ru', value)
        return True

    # Translate RU+mixed → EN
    to_translate_en = {**ru_fields, **mixed_fields}
    if to_translate_en:
        en_result = await _translate_fields(to_translate_en, target_lang='en')
        if en_result:
            for field, translated in en_result.items():
                setattr(profile, f'{field}_normalized', str(translated).lower().strip())
        else:
            for field, value in to_translate_en.items():
                setattr(profile, f'{field}_normalized', value.lower())

    # Translate EN+mixed → RU
    to_translate_ru = {**en_fields, **mixed_fields}
    if to_translate_ru:
        ru_result = await _translate_fields(to_translate_ru, target_lang='ru')
        if ru_result:
            for field, translated in ru_result.items():
                setattr(profile, f'{field}_normalized_ru', str(translated).strip())
        else:
            for field, value in to_translate_ru.items():
                setattr(profile, f'{field}_normalized_ru', value)

    # Clear fields that are None in original
    for field in _NORMALIZE_FIELDS:
        if field not in fields:
            setattr(profile, f'{field}_normalized', None)
            setattr(profile, f'{field}_normalized_ru', None)

    logger.info(f"[NORMALIZE] Profile {profile.user_id} normalized: EN={list(to_translate_en.keys()) if to_translate_en else 'skip'}, RU={list(to_translate_ru.keys()) if to_translate_ru else 'skip'}")
    return True


async def _translate_fields(fields: dict, target_lang: str) -> dict | None:
    """
    Translate profile fields to target language via DeepSeek.
    Returns dict with translated values, or None on failure.
    """
    if target_lang == 'en':
        prompt = (
            "Translate these user profile fields to English. "
            "Return ONLY a valid JSON object with the same keys. "
            "For comma-separated lists, translate each item individually and keep commas. "
            "For semicolon-separated lists, translate each item individually and keep semicolons. "
            "Lowercase everything EXCEPT proper nouns, brand names, and company names — keep their original casing. "
            "Do NOT transliterate brand/company names — keep them exactly as written (e.g. 'АСИ Бионт' stays 'АСИ Бионт'). "
            "Translate common words: cities (Пермь→perm, Москва→moscow), professions, skills, interests. "
            "Do not add or remove items. "
            "If a value is already in English, keep it as-is but lowercase (except proper nouns).\n\n"
            + json.dumps(fields, ensure_ascii=False)
        )
        system = "You are a translator. Return only valid JSON with translated fields. Lowercase common words, preserve brand/company names exactly as written. No markdown, no explanation."
    else:
        prompt = (
            "Переведи эти поля профиля пользователя на русский язык. "
            "Верни ТОЛЬКО валидный JSON объект с теми же ключами. "
            "Для списков через запятую — переведи каждый элемент отдельно, сохрани запятые. "
            "Для списков через точку с запятой — переведи каждый элемент, сохрани точки с запятой. "
            "Не добавляй и не удаляй элементы. "
            "НЕ переводи и НЕ транслитерируй названия компаний и брендов — оставь как есть (например 'ASI Biont' остаётся 'ASI Biont'). "
            "Если значение уже на русском — оставь как есть. Сохраняй естественный регистр (с заглавной буквы для городов).\n\n"
            + json.dumps(fields, ensure_ascii=False)
        )
        system = "Ты переводчик. Верни только валидный JSON с переведёнными полями. Не переводи названия компаний и брендов. Без markdown, без пояснений."

    import aiohttp as _aio_tr
    try:
        timeout = _aio_tr.ClientTimeout(total=25, connect=5)
        connector = _aio_tr.TCPConnector(force_close=True)
        async with _aio_tr.ClientSession(timeout=timeout, connector=connector) as session_tr:
            async with session_tr.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 800
                }
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data['choices'][0]['message']['content'].strip()
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    if start != -1 and end > start:
                        return json.loads(content[start:end])
                    else:
                        logger.warning(f"[TRANSLATE] No JSON in response for {target_lang}: {content[:200]}")
                else:
                    logger.warning(f"[TRANSLATE] API returned {response.status} for {target_lang}")
    except Exception as e:
        logger.error(f"[TRANSLATE] Error translating to {target_lang}: {type(e).__name__}: {e!r}")
    return None


async def normalize_profile_background(internal_user_id: int):
    """
    Background task: normalize profile fields for a given internal user_id.
    Creates its own DB session.
    """
    session = None
    try:
        session = Session()
        profile = session.query(UserProfile).filter_by(user_id=internal_user_id).first()
        if profile:
            success = await normalize_profile_fields(profile)
            if success:
                session.commit()
                logger.info(f"[NORMALIZE BG] Profile {internal_user_id} normalized successfully")
    except Exception as e:
        logger.error(f"[NORMALIZE BG] Error for user {internal_user_id}: {e}")
        if session:
            session.rollback()
    finally:
        if session:
            session.close()
