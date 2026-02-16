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
    ALPHA_VANTAGE_API_KEY,
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
        logger.warning(f"[REDIS] Failed to connect: {e}. Falling back to in-memory cache")
        redis_client = None
else:
    logger.info("[CACHE] Redis disabled, using in-memory cache")
# Fallback in-memory caches (used if Redis is unavailable)
weather_cache = {}
news_cache = {}
finance_cache = {}
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


def refresh_weather_cache_async(city, cache_ttl_minutes):
    """Refresh weather cache asynchronously"""
    background_executor.submit(_refresh_weather_cache, city, cache_ttl_minutes)


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


def refresh_news_cache_async(city, cache_ttl_minutes):
    """Refresh news cache asynchronously"""
    background_executor.submit(_refresh_news_cache, city, cache_ttl_minutes)


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


def refresh_finance_cache_async(symbol, asset_type, cache_ttl_minutes):
    """Refresh finance cache asynchronously"""
    background_executor.submit(_refresh_finance_cache, symbol, asset_type, cache_ttl_minutes)


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
        if "завтра" in day_word:
            target_date = (now + timedelta(days=1)).date()
        elif "послезавтра" in day_word:
            target_date = (now + timedelta(days=2)).date()
        else:
            target_date = now.date()
        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")
    # Проверяем дни недели
    weekdays = {
        'понедельник': 0, 'вторник': 1, 'среда': 2, 'четверг': 3,
        'пятница': 4, 'суббота': 5, 'воскресенье': 6
    }
    weekday_match = re.search(r"(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)(?:\s+(?:в\s+)?(\d{1,2}):(\d{2}))?", time_text)
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
    # Проверяем просто "в HH:MM"
    simple_time_match = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
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
        r"сейчас\s+вызову",
        r"буду\s+вызывать",
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
    
    # Удаляем «Отлично» в начале ответа ТОЛЬКО когда это пустая фраза-заполнитель
    # НЕ удаляем если после "Отлично" идёт осмысленное продолжение (поздравления/позитив)
    # "Отлично!" → удаляем (пустое), "Отлично, давай..." → удаляем (филлер)
    # "Отличная работа!" → НЕ удаляем (это поздравление)
    text = re.sub(r'^\s*(?:🚀|🎯|✅|💡|📊|⚡|🔥)?\s*Отлично[!,.]?\s*(?=\n|$)', '', text, flags=re.IGNORECASE).strip()
    # Удаляем "Отлично," только в начале когда это filler перед обычным текстом
    text = re.sub(r'^\s*Отлично[,!]\s+(?=давай|я |сейчас|вот|так)', '', text, flags=re.IGNORECASE).strip()
    
    # AI теперь может использовать эмодзи для структуры
    technical_emojis = ['🔧', '⚙️', '🛠️']
    for emoji in technical_emojis:
        text = text.replace(emoji, '')
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
                    refresh_weather_cache_async(city, cache_ttl_minutes)
        except Exception as e:
            logger.warning(f"[WEATHER CACHE] Failed to check TTL for {cache_key}: {e}")
        return cached_data
    # Проверяем in-memory fallback
    cached_data = _memory_get(weather_cache, cache_key)
    if cached_data:
        logger.info(f"[WEATHER CACHE] Using memory cached weather for {city}")
        refresh_weather_cache_async(city, cache_ttl_minutes)
        return cached_data
    # Нет данных в кэше - загружаем синхронно (только при первом запросе)
    logger.info(f"[WEATHER] No cache for {city}, loading synchronously")
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
                    refresh_news_cache_async(city, cache_ttl_minutes)
        except Exception as e:
            logger.warning(f"[NEWS CACHE] Failed to check TTL for {cache_key}: {e}")
        return cached_data
    # Проверяем in-memory fallback
    cached_data = _memory_get(news_cache, cache_key)
    if cached_data:
        logger.info(f"[NEWS CACHE] Using memory cached news for {cache_key}")
        refresh_news_cache_async(city, cache_ttl_minutes)
        return cached_data
    # Нет данных в кэше - загружаем синхронно (только при первом запросе)
    logger.info(f"[NEWS] No cache for {cache_key}, loading synchronously")
    return _load_news_sync(city)


def _load_news_sync(city=None):
    """Load news data synchronously from API"""
    try:
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
            logger.warning(f"[NEWS] Failed to fetch news: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"[NEWS] Error fetching news: {e}")
        return None


def get_finance_info(symbol, asset_type='stock', cache_ttl_minutes=15):
    """Get finance information with caching.
    
    DEPRECATED: Используйте api_client.get_stock() (async).
    """
    cache_key = f"{asset_type}_{symbol.lower()}"
    ttl_seconds = cache_ttl_minutes * 60
    # Проверяем Redis кеш
    cached_data = _redis_get(cache_key)
    if cached_data:
        logger.info(f"[FINANCE CACHE] Using Redis cached data for {symbol} ({asset_type})")
        # Запускаем фоновое обновление если данные старше половины TTL
        try:
            if redis_client:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left > 0 and ttl_left < ttl_seconds / 2:  # ttl_left > 0 проверяет что ключ существует
                    refresh_finance_cache_async(symbol, asset_type, cache_ttl_minutes)
        except Exception as e:
            logger.warning(f"[FINANCE CACHE] Failed to check TTL for {cache_key}: {e}")
        return cached_data
    # Проверяем in-memory fallback
    cached_data = _memory_get(finance_cache, cache_key)
    if cached_data:
        logger.info(f"[FINANCE CACHE] Using memory cached data for {symbol} ({asset_type})")
        refresh_finance_cache_async(symbol, asset_type, cache_ttl_minutes)
        return cached_data
    # Нет данных в кэше - загружаем синхронно (только при первом запросе)
    logger.info(f"[FINANCE] No cache for {symbol} ({asset_type}), loading synchronously")
    return _load_finance_sync(symbol, asset_type)


def _load_finance_sync(symbol, asset_type='stock'):
    """Load finance data synchronously from API"""
    try:
        if asset_type == 'stock':
            api_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
        elif asset_type == 'crypto':
            api_url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={symbol}&to_currency=USD&apikey={ALPHA_VANTAGE_API_KEY}"
        else:
            logger.warning(f"[FINANCE] Unsupported asset type: {asset_type}")
            return None
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Кешируем результат
            cache_key = f"{asset_type}_{symbol.lower()}"
            _redis_set(cache_key, data, 15 * 60)  # 15 minutes TTL
            _memory_set(finance_cache, cache_key, data)
            logger.info(f"[FINANCE] Fetched data for {symbol} ({asset_type})")
            return data
        else:
            logger.warning(f"[FINANCE] Failed to fetch data for {symbol} ({asset_type}): {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"[FINANCE] Error fetching data for {symbol} ({asset_type}): {e}")
        return None
def preload_common_data():
    """
    Предварительно загружает данные для популярных городов и общие новости.
    Вызывается при старте бота для заполнения кэша.
    """
    logger.info("[CACHE] Starting preload of common data")
    # Популярные города для предварительной загрузки
    common_cities = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань"]
    # Загружаем погоду для популярных городов
    for city in common_cities:
        try:
            logger.info(f"[CACHE] Preloading weather for {city}")
            get_weather_info(city)
        except Exception as e:
            logger.warning(f"[CACHE] Failed to preload weather for {city}: {e}")
    # Загружаем общие новости
    try:
        logger.info("[CACHE] Preloading general news")
        get_news_info()
    except Exception as e:
        logger.warning(f"[CACHE] Failed to preload general news: {e}")
    logger.info("[CACHE] Preload completed")


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
