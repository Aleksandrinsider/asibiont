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


def _redis_get(cache_key):
    """Получить данные из Redis"""
    if redis_client:
        try:
            data = redis_client.get(cache_key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning(f"[REDIS] Failed to get {cache_key}: {e}")
    return None


def _redis_set(cache_key, data, ttl_seconds):
    """Сохранить данные в Redis с TTL"""
    if redis_client:
        try:
            redis_client.setex(cache_key, ttl_seconds, json.dumps(data))
            return True
        except Exception as e:
            logger.warning(f"[REDIS] Failed to set {cache_key}: {e}")
    return False


def _memory_get(cache_dict, cache_key):
    """Получить данные из in-memory кеша"""
    if cache_key in cache_dict:
        cached = cache_dict[cache_key]
        if time.time() - cached['timestamp'] < 3600:  # 1 hour fallback TTL
            return cached['data']
        else:
            del cache_dict[cache_key]  # Remove expired
    return None


def _memory_set(cache_dict, cache_key, data):
    """Сохранить данные в in-memory кеш"""
    cache_dict[cache_key] = {
        'data': data,
        'timestamp': time.time()
    }


def refresh_weather_cache_async(city, cache_ttl_minutes=30):
    """
    Асинхронно обновляет кэш погоды в фоне.
    Не блокирует основной поток.
    """
    def _refresh():
        try:
            get_weather_info(city, cache_ttl_minutes=0)  # Принудительное обновление
            logger.info(f"[WEATHER] Background refresh completed for {city}")
        except Exception as e:
            logger.error(f"[WEATHER] Background refresh failed for {city}: {e}")

    background_executor.submit(_refresh)


def refresh_news_cache_async(city=None, cache_ttl_minutes=120):  # Уменьшил до 2 часов для актуальности
    """
    Асинхронно обновляет кэш новостей в фоне.
    Не блокирует основной поток.
    """
    def _refresh():
        try:
            get_news_info(city, cache_ttl_minutes=0)  # Принудительное обновление
            logger.info(f"[NEWS] Background refresh completed for {city or 'general'}")
        except Exception as e:
            logger.error(f"[NEWS] Background refresh failed for {city or 'general'}: {e}")

    background_executor.submit(_refresh)


def refresh_finance_cache_async(symbol, asset_type, cache_ttl_minutes=15):
    """
    Асинхронно обновляет кэш финансовых данных в фоне.
    Не блокирует основной поток.
    """
    def _refresh():
        try:
            get_finance_info(symbol, asset_type, cache_ttl_minutes=0)  # Принудительное обновление
            logger.info(f"[FINANCE] Background refresh completed for {symbol} ({asset_type})")
        except Exception as e:
            logger.error(f"[FINANCE] Background refresh failed for {symbol} ({asset_type}): {e}")

    background_executor.submit(_refresh)


def analyze_interaction_for_profile_update(user_id, message, ai_response):
    """
    Анализирует взаимодействие пользователя для предложения обновления профиля.
    Возвращает предложение обновления профиля или None.
    """
    from models import Session, UserProfile
    
    if not user_id or not message:
        return None
    
    session = Session()
    try:
        # Получаем текущий профиль
        profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not profile:
            # Профиль не существует - предложить создать
            return "Чтобы лучше помогать тебе, давай заполним профиль. Расскажи о себе: где живешь, чем занимаешься, какие у тебя интересы?"
        
        # Проверяем, какие поля профиля пустые
        empty_fields = []
        suggestions = []
        
        if not profile.city or profile.city.strip() == "":
            empty_fields.append("city")
            # Ищем упоминание города в сообщении
            city_keywords = ["москва", "питер", "спб", "екатеринбург", "новосибирск", "казань", "нижний новгород", "челябинск", "омск", "самара", "ростов", "уфа", "красноярск", "воронеж", "пермь", "волгоград"]
            for city in city_keywords:
                if city.lower() in message.lower():
                    suggestions.append(f"Вижу, ты упомянул {city.title()}. Добавить в профиль как твой город?")
                    break
        
        if not profile.interests or profile.interests.strip() == "":
            empty_fields.append("interests")
            # Ищем интересы в сообщении
            interest_keywords = {
                "спорт": ["бег", "фитнес", "тренировка", "спорт", "йога", "плавание"],
                "программирование": ["код", "программирование", "python", "js", "разработка", "проект"],
                "путешествия": ["путешествие", "отпуск", "туризм", "поездка"],
                "музыка": ["музыка", "концерт", "гитара", "пение"],
                "искусство": ["картина", "выставка", "театр", "кино"],
                "чтение": ["книга", "читать", "литература"],
                "кухня": ["готовить", "рецепт", "кухня", "еда"]
            }
            for interest, keywords in interest_keywords.items():
                for keyword in keywords:
                    if keyword.lower() in message.lower():
                        suggestions.append(f"Вижу интерес к {interest}. Добавить '{interest}' в твои интересы?")
                        break
        
        if not profile.skills or profile.skills.strip() == "":
            empty_fields.append("skills")
            # Ищем навыки в сообщении
            skill_keywords = ["умею", "знаю", "могу", "опыт в", "работаю с", "специалист", "разработчик"]
            for keyword in skill_keywords:
                if keyword in message.lower():
                    # Извлекаем навык из сообщения - улучшенная логика
                    # Ищем паттерны типа "умею X", "знаю Y", "работаю с Z"
                    patterns = [
                        rf"{keyword}\s+(.+?)(?:\s|$|[.,!?;])",
                        rf"{keyword}\s+(.+?)(?:\s+и\s+|$|[.,!?;])",
                        rf"{keyword}\s+(.+?)(?:\s+на\s+|$|[.,!?;])"
                    ]
                    for pattern in patterns:
                        skill_match = re.search(pattern, message.lower())
                        if skill_match:
                            skill = skill_match.group(1).strip()
                            # Фильтруем разумные навыки
                            if (len(skill) > 3 and len(skill) < 50 and 
                                not any(word in skill.lower() for word in ["что", "как", "где", "когда", "почему"])):
                                suggestions.append(f"Вижу, у тебя есть навык '{skill}'. Добавить в профиль?")
                                break
                    if suggestions and "skills" in [s.split()[-1] for s in suggestions]:
                        break
        
        if not profile.company or profile.company.strip() == "":
            empty_fields.append("company")
            # Ищем упоминание компании - улучшенная логика
            company_indicators = ["работаю в", "компания", "фирма", "организация", "работодатель"]
            for indicator in company_indicators:
                if indicator in message.lower():
                    # Ищем название компании после индикатора
                    patterns = [
                        rf"{indicator}\s+(.+?)(?:\s|$|[.,!?;])",
                        rf"{indicator}\s+(.+?)(?:\s+как\s+|$|[.,!?;])",
                        rf"{indicator}\s+(.+?)(?:\s+на\s+|$|[.,!?;])"
                    ]
                    for pattern in patterns:
                        company_match = re.search(pattern, message.lower())
                        if company_match:
                            company = company_match.group(1).strip()
                            # Фильтруем разумные названия компаний
                            if (len(company) > 2 and len(company) < 100 and 
                                not any(word in company.lower() for word in ["большой", "маленькой", "своей", "другой", "этой"])):
                                suggestions.append(f"Вижу, ты работаешь в '{company}'. Добавить компанию в профиль?")
                                break
                    if suggestions and "профиль?" in [s.split()[-1] for s in suggestions]:
                        break
        
        # Если есть пустые поля и предложения, возвращаем первое подходящее
        if empty_fields and suggestions:
            return suggestions[0]
        
        # Если профиль почти пустой, но мы не нашли конкретных предложений
        filled_fields = 0
        if profile.city and profile.city.strip():
            filled_fields += 1
        if profile.interests and profile.interests.strip():
            filled_fields += 1
        if profile.skills and profile.skills.strip():
            filled_fields += 1
        if profile.company and profile.company.strip():
            filled_fields += 1
        
        # Если нет предложений от ключевых слов, но профиль неполный и сообщение длинное - используем ИИ
        if not suggestions and empty_fields and len(message.split()) > 5:
            ai_suggestion = analyze_with_ai(profile, message)
            if ai_suggestion:
                return ai_suggestion
        
        if filled_fields < 2 and len(message.split()) > 5:  # Длинное сообщение
            return "Чтобы лучше подбирать для тебя партнеров и рекомендации, заполни профиль. Что тебя интересует или чем ты занимаешься?"
        
        return None
        
    except Exception as e:
        logger.error(f"Error in analyze_interaction_for_profile_update: {e}")
        return None
    finally:
        session.close()


def analyze_with_ai(profile, message):
    """
    Анализирует сообщение с помощью ИИ для предложения обновления профиля.
    """
    import requests
    
    empty_fields = []
    if not profile.city or profile.city.strip() == "":
        empty_fields.append("город")
    if not profile.interests or profile.interests.strip() == "":
        empty_fields.append("интересы")
    if not profile.skills or profile.skills.strip() == "":
        empty_fields.append("навыки")
    if not profile.company or profile.company.strip() == "":
        empty_fields.append("компания")
    
    if not empty_fields:
        return None
    
    prompt = """
    Проанализируй сообщение пользователя и предложи обновление профиля.
    Пустые поля профиля: {', '.join(empty_fields)}
    
    Сообщение: "{message}"
    
    Если в сообщении есть информация, относящаяся к пустым полям, предложи конкретное обновление.
    Формат ответа: "Вижу, [что-то]. Добавить '[значение]' в [поле]?"
    Если ничего подходящего нет, ответь только "None".
    
    Примеры:
    - Для навыков: "Вижу, у тебя есть навык 'программирование на Python'. Добавить в профиль?"
    - Для компании: "Вижу, ты работаешь в 'Google'. Добавить компанию в профиль?"
    - Для города: "Вижу, ты упомянул 'Москва'. Добавить в профиль как твой город?"
    - Для интересов: "Вижу интерес к 'спорту'. Добавить 'спорт' в твои интересы?"
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.1
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            if content and "None" not in content and len(content) > 10:
                return content
        return None
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return None


def smart_fallback_handler(message, mentions_str, user_id, ai_response_content=""):
    """
    Умный fallback-обработчик: пытается выполнить действие, если AI не справился.
    Анализирует намерение пользователя и выполняет соответствующие действия напрямую.
    """
    fallback_actions = []
    
    # Распознавание приветствий
    greeting_words = ["привет", "здравствуй", "хай", "hello", "hi", "добрый", "здравствуйте"]
    is_greeting = len(message.strip()) <= 20 and any(  # Короткое сообщение
        word in message.lower() for word in greeting_words
    )  # Содержит слово приветствия

    if is_greeting and len(str(ai_response_content).strip()) < 200:  # Ответ AI слишком короткий для приветствия (минимум 4-6 предложений)
        logger.info("[SMART FALLBACK] Greeting detected, enhancing response")
        # Получаем список задач для подробного ответа
        from models import Session
        from ai_integration.chat import list_tasks

        db_session = Session()
        try:
            tasks_result = list_tasks(user_id=user_id, session=db_session)

            # Создаем подробное приветствие
            enhanced_greeting = f"Привет! {tasks_result}"

            fallback_actions.append(
                {
                    "function": "enhanced_greeting",
                    "result": enhanced_greeting,
                    "reason": "AI ответ слишком краток для приветствия"
                }
            )
        except Exception as e:
            logger.error(f"Error enhancing greeting: {e}")
        finally:
            db_session.close()

    # Высокая уверенность AI уже обработал
    ai_confidence = 0.8  # AI уже проанализировал запрос

    return fallback_actions


def determine_timezone_from_time(user_time_str, user_id):
    """Определяет timezone пользователя на основе введенного времени"""
    import re
    from datetime import datetime
    import pytz

    # Парсим время из строки (HH:MM)
    time_match = re.search(r"(\d{1,2}):(\d{2})", user_time_str)
    if not time_match:
        return None

    user_hour = int(time_match.group(1))
    # user_minute = int(time_match.group(2))

    # Текущее UTC время
    now_utc = datetime.now(pytz.UTC)

    # Создаем datetime объект для пользователя
    # user_now = now_utc.replace(hour=user_hour, minute=user_minute)

    # Вычисляем разницу в часах
    hour_diff = user_hour - now_utc.hour

    # Обрабатываем переход через сутки
    if hour_diff > 12:
        hour_diff -= 24
    elif hour_diff < -12:
        hour_diff += 24

    # Определяем timezone на основе разницы
    timezone_map = {
        -12: "Pacific/Kwajalein",  # UTC-12
        -11: "Pacific/Midway",  # UTC-11
        -10: "Pacific/Honolulu",  # UTC-10
        -9: "America/Anchorage",  # UTC-9
        -8: "America/Los_Angeles",  # UTC-8
        -7: "America/Denver",  # UTC-7
        -6: "America/Chicago",  # UTC-6
        -5: "America/New_York",  # UTC-5
        -4: "America/Halifax",  # UTC-4
        -3: "America/Sao_Paulo",  # UTC-3
        -2: "Atlantic/South_Georgia",  # UTC-2
        -1: "Atlantic/Azores",  # UTC-1
        0: "Europe/London",  # UTC+0
        1: "Europe/Paris",  # UTC+1
        2: "Europe/Kiev",  # UTC+2
        3: "Europe/Moscow",  # UTC+3
        4: "Asia/Dubai",  # UTC+4
        5: "Asia/Karachi",  # UTC+5
        6: "Asia/Dhaka",  # UTC+6
        7: "Asia/Bangkok",  # UTC+7
        8: "Asia/Shanghai",  # UTC+8
        9: "Asia/Tokyo",  # UTC+9
        10: "Australia/Sydney",  # UTC+10
        11: "Pacific/Noumea",  # UTC+11
        12: "Pacific/Auckland",  # UTC+12
    }

    # Находим ближайший timezone
    closest_diff = min(timezone_map.keys(), key=lambda x: abs(x - hour_diff))
    return timezone_map[closest_diff]


def parse_time_to_datetime(time_text, user_id):
    """Парсит время из текста пользователя"""
    import re
    from datetime import datetime, timedelta
    import pytz
    from models import Session, User

    # Получаем timezone пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
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


def parse_relative_time(message, current_time):
    """Parse relative time expressions like 'через 5 минут', 'через 2 часа' and return datetime.
    
    Args:
        message: String containing relative time expression
        current_time: Current datetime in user's local timezone (not UTC!)
    
    Returns:
        Datetime object in the same timezone as current_time, or None if parsing failed
    """
    from datetime import datetime, timedelta
    import re

    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")
    if not current_time or not isinstance(current_time, datetime):
        raise ValueError("Current time must be a datetime object")

    # Patterns for Russian time expressions
    patterns = [
        (r"через\s+(\d+)\s*мин", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"через\s+(\d+)\s*минут", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"на\s+(\d+)\s*мин", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"на\s+(\d+)\s*минут", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"через\s+(\d+)\s*час", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часа", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часов", lambda m: timedelta(hours=int(m.group(1)))),
        (r"на\s+(\d+)\s*час", lambda m: timedelta(hours=int(m.group(1)))),
        (r"на\s+(\d+)\s*часа", lambda m: timedelta(hours=int(m.group(1)))),
        (r"на\s+(\d+)\s*часов", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+час", lambda m: timedelta(hours=1)),  # без числа
        (r"через\s+минут", lambda m: timedelta(minutes=1)),  # без числа, но редко
    ]

    for pattern, delta_func in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            delta = delta_func(match)
            # Возвращаем время в той же timezone что и current_time
            return current_time + delta

    return None


def parse_absolute_time(message):
    """Parse absolute time expressions like 'сейчас 12:18', 'время 15:30' and return HH:MM"""
    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")

    import re

    # Patterns for absolute time
    patterns = [
        r"сейчас\s+(\d{1,2}):(\d{2})",
        r"время\s+(\d{1,2}):(\d{2})",
        r"(\d{1,2}):(\d{2})",  # Just HH:MM
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"

    return None


def parse_natural_time(time_str, current_time):
    """Parse natural time expressions like 'завтра в 10 утра', 'вечером в 8', etc.
    
    Args:
        time_str: String like 'завтра в 10 утра'
        current_time: Current datetime in user's timezone
    
    Returns:
        Datetime object in user's timezone or None
    """
    import re
    from datetime import datetime, timedelta
    
    if not time_str or not isinstance(time_str, str):
        return None
    
    time_str = time_str.lower().strip()
    
    # Extract time part (HH:MM or natural like '10 утра')
    time_match = None
    
    # Patterns for time (order matters - more specific first)
    time_patterns = [
        (r'(\d{1,2}):(\d{2})', lambda h, m: (int(h), int(m))),  # 10:30
        (r'к\s*(\d{1,2})\s*часам?', lambda h, m: (int(h), 0)),  # к 10 часам
        (r'до\s*(\d{1,2})\s*часов?', lambda h, m: (int(h), 0)),  # до 10 часов
        (r'после\s*(\d{1,2})\s*часов?', lambda h, m: (int(h), 0)),  # после 10 часов
        (r'в\s*(\d{1,2})\s*часов?', lambda h, m: (int(h), 0)),  # в 10 часов
        (r'около\s*(\d{1,2})\s*часов?', lambda h, m: (int(h), 0)),  # около 10 часов
        (r'примерно\s*(\d{1,2})\s*часов?', lambda h, m: (int(h), 0)),  # примерно в 10 часов
        (r'(\d{1,2})\s*утра', lambda h, m: (int(h) if int(h) <= 12 else int(h) - 12, 0)),  # 10 утра
        (r'(\d{1,2})\s*вечера', lambda h, m: ((int(h) + 12) if int(h) < 12 else int(h), 0)),  # 8 вечера
        (r'(\d{1,2})\s*ночи', lambda h, m: (int(h), 0)),  # 2 ночи
        (r'(\d{1,2})\s*дня', lambda h, m: (int(h), 0)),  # 2 дня
        (r'полдень', lambda h, m: (12, 0)),  # полдень
        (r'полночь', lambda h, m: (0, 0)),  # полночь
    ]
    
    for pattern, converter in time_patterns:
        match = re.search(pattern, time_str)
        if match:
            if len(match.groups()) == 2:
                h, m = converter(match.group(1), match.group(2))
            elif len(match.groups()) == 1:
                h, m = converter(match.group(1), '0')
            else:
                # No groups (e.g., "полдень", "полночь")
                h, m = converter(None, None)
            time_match = (h, m)
            break
    
    if not time_match:
        return None
    
    h, m = time_match
    
    # Determine date
    date = current_time.date()
    
    if 'завтра' in time_str:
        date = current_time.date() + timedelta(days=1)
    elif 'послезавтра' in time_str:
        date = current_time.date() + timedelta(days=2)
    elif 'вечером' in time_str and h < 12:
        h += 12  # Assume evening means PM
    elif 'утром' in time_str and h >= 12:
        h -= 12  # Assume morning means AM
    
    # Create datetime
    try:
        result = current_time.replace(year=date.year, month=date.month, day=date.day, hour=h, minute=m, second=0, microsecond=0)
        
        # If the time has already passed today and no explicit date was mentioned, schedule for tomorrow
        if result <= current_time and not any(word in time_str for word in ['завтра', 'послезавтра', 'вчера']):
            result = result + timedelta(days=1)
        
        return result
    except ValueError:
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
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|cancel_delegation|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)\s*\([^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
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

    # Удаляем эмодзи - ТОЛЬКО технические, оставляем подходящие для общения
    # (AI теперь может использовать 1-2 подходящих эмодзи согласно промпту)
    # Удаляем только технические эмодзи, которые могут мешать
    technical_emojis = ['🚀', '✅', '📝', '🎯', '⚠️', '💡', '📋', '⏳', '🟡', '🔧', '📋', '📊', '🔍', '⚙️', '🛠️']
    for emoji in technical_emojis:
        text = text.replace(emoji, '')

    # КРИТИЧЕСКАЯ ПРОВЕРКА: если после очистки ничего не осталось,
    # значит AI вернул только технические детали, вернуть оригинал
    if not text.strip():
        logger.warning(f"[CLEAN] Content was completely cleaned, returning original: '{original_text}'")
        return original_text.strip()

    if original_text != text:
        logger.warning(f"[CLEAN] Original: '{original_text[:100]}...' -> Cleaned: '{text[:100]}...'")

    return text.strip()


# Alias for backward compatibility
clean_content = clean_technical_details


def enrich_response_with_engagement(content, user_id=None, original_message=""):
    """
    Автоматически обогащает короткие ответы вовлекающими элементами:
    - Вопросы
    - Рекомендации
    - Предложения действий
    Работает естественно, без шаблонных фраз - просто добавляет общий призыв к действию
    """
    # Проверяем длину ответа (в предложениях)
    sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]

    # Если ответ достаточно развёрнутый (3+ предложения) или уже содержит вопрос - не трогаем
    if len(sentences) >= 3 or "?" in content:
        return content

    # Добавляем лёгкое вовлечение только для очень коротких ответов (1-2 предложения)
    # AI сам должен генерировать контекстные вопросы, мы только подстраховываемся
    import random

    # Минималистичные варианты, которые не повторяются
    minimal_engagement = [" Что дальше?", " Чем ещё помочь?", " Какие планы?"]

    # Только для самых коротких ответов (1 предложение)
    if len(sentences) <= 1:
        enrichment = random.choice(minimal_engagement)
        return content + enrichment

    return content


def analyze_user_context_for_advice(user_id, message, context=None):
    """
    ПОЛНЫЙ глубокий анализ контекста пользователя для генерации МАКСИМАЛЬНО РЕЛЕВАНТНЫХ советов.
    Анализирует ВСЁ: задачи, контакты, профиль, паттерны, текущую ситуацию.
    Возвращает детальный анализ для использования в промпте.
    """
    from models import Session, User, UserProfile, Task
    from datetime import datetime, timedelta
    import pytz

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {"error": "Пользователь не найден"}

        now = datetime.now(pytz.UTC)
        analysis = {
            "profile": {},
            "tasks": {},
            "patterns": {},
            "context_insights": {},
            "recommendations": {},
            "relevant_contacts": []
        }

        # 1. ДЕТАЛЬНЫЙ АНАЛИЗ ПРОФИЛЯ
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            analysis["profile"] = {
                "city": profile.city or "не указан",
                "company": profile.company or "не указана",
                "position": profile.position or "не указана",
                "bio": profile.bio or "не указано",
                "languages": profile.languages or "не указаны",
                "skills": profile.skills or "не указаны",
                "interests": profile.interests or "не указаны",
                "goals": profile.goals or "не указаны",
                "filled_fields": sum([1 for field in [profile.city, profile.company, profile.position, profile.bio, profile.languages, profile.skills, profile.interests, profile.goals] if field]),
                "raw": profile  # Сохраняем объект для дальнейшего анализа
            }

        # 2. ГЛУБОКИЙ АНАЛИЗ ЗАДАЧ С ДЕТАЛЯМИ
        all_tasks = session.query(Task).filter_by(user_id=user.id).all()
        pending_tasks = [t for t in all_tasks if t.status == "pending"]
        completed_tasks = [t for t in all_tasks if t.status == "completed"]
        
        # Разделяем задачи по временным категориям
        overdue_tasks = [t for t in pending_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time) < now]
        today_tasks = [t for t in pending_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time).date() == now.date() and t not in overdue_tasks]
        upcoming_tasks = [t for t in pending_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time) > now and t not in today_tasks]

        analysis["tasks"] = {
            "total": len(all_tasks),
            "pending": len(pending_tasks),
            "completed": len(completed_tasks),
            "completion_rate": len(completed_tasks) / max(len(all_tasks), 1),
            "overdue": len(overdue_tasks),
            "overdue_list": [{"title": t.title, "time": t.reminder_time} for t in overdue_tasks[:5]],
            "today": len(today_tasks),
            "today_list": [{"title": t.title, "time": t.reminder_time} for t in today_tasks[:5]],
            "upcoming": len(upcoming_tasks),
            "upcoming_list": [{"title": t.title, "time": t.reminder_time} for t in upcoming_tasks[:5]],
            "delegated": len([t for t in all_tasks if t.delegated_to_username]),
            "delegated_list": [{"title": t.title, "to": t.delegated_to_username} for t in all_tasks if t.delegated_to_username][:3]
        }

        # 3. РАСШИРЕННЫЙ АНАЛИЗ ПАТТЕРНОВ
        # Анализ тем задач
        task_titles = [t.title.lower() for t in all_tasks]
        themes = {
            "development": sum(1 for title in task_titles if any(word in title for word in ["разработка", "код", "программирование", "dev", "backend", "frontend", "api", "база данных"])),
            "meetings": sum(1 for title in task_titles if any(word in title for word in ["встреча", "совещание", "митинг", "meeting", "созвон"])),
            "documents": sum(1 for title in task_titles if any(word in title for word in ["документ", "отчет", "презентация", "документация", "составить"])),
            "communication": sum(1 for title in task_titles if any(word in title for word in ["звонок", "позвонить", "написать", "ответить", "связаться"])),
            "learning": sum(1 for title in task_titles if any(word in title for word in ["изучить", "обучить", "курс", "тренинг", "прочитать", "освоить"])),
            "business": sum(1 for title in task_titles if any(word in title for word in ["инвестор", "стартап", "бизнес", "продажа", "клиент", "договор"])),
            "health": sum(1 for title in task_titles if any(word in title for word in ["спорт", "зал", "тренировка", "здоровье", "врач"])),
            "personal": sum(1 for title in task_titles if any(word in title for word in ["купить", "заказать", "забрать", "оплатить", "оформить"]))
        }

        # Частота выполнения задач
        days_active = max((now.date() - user.created_at.replace(tzinfo=None).date()).days, 1)
        
        analysis["patterns"] = {
            "main_themes": sorted(themes.items(), key=lambda x: x[1], reverse=True)[:3],
            "task_frequency": len(all_tasks) / days_active,
            "delegation_ratio": len([t for t in all_tasks if t.delegated_to_username]) / max(len(all_tasks), 1),
            "overdue_ratio": len(overdue_tasks) / max(len(pending_tasks), 1),
            "completion_rate_percent": int((len(completed_tasks) / max(len(all_tasks), 1)) * 100),
            "avg_tasks_per_week": (len(all_tasks) / days_active) * 7,
            "most_productive_time": "утро" if sum(1 for t in completed_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time).hour < 12) > len(completed_tasks) / 2 else "вечер"
        }

        # 4. ГЛУБОКИЙ АНАЛИЗ КОНТЕКСТА СООБЩЕНИЯ И СИТУАЦИИ
        message_lower = message.lower()
        
        # Определяем эмоциональное состояние более точно
        stress_words = ["стресс", "давление", "проблема", "застрял", "сложно", "не получается", "устал", "выгорание"]
        motivated_words = ["хочу", "заинтересован", "готов", "вдохновлен", "цель", "мечта", "амбиции"]
        
        analysis["context_insights"] = {
            "urgency_level": "high" if any(word in message_lower for word in ["срочно", "дедлайн", "завтра", "сегодня", "немедленно", "важно"]) else "normal",
            "emotional_state": "stressed" if any(word in message_lower for word in stress_words) else
                            "motivated" if any(word in message_lower for word in motivated_words) else "neutral",
            "request_type": "advice" if any(word in message_lower for word in ["как", "что делать", "совет", "помоги", "подскажи"]) else
                          "action" if any(word in message_lower for word in ["сделай", "добавь", "удали", "обнови", "напомни"]) else "info",
            "seeks_help": any(word in message_lower for word in ["помоги", "помощь", "подскажи", "как", "не знаю"]),
            "wants_optimization": any(word in message_lower for word in ["быстрее", "эффективнее", "автоматизировать", "упростить", "оптимизировать"]),
            "mentions_time_pressure": any(word in message_lower for word in ["времени нет", "успеть", "дедлайн", "горит"])
        }

        # 5. РАСШИРЕННЫЕ ПЕРСОНАЛИЗИРОВАННЫЕ РЕКОМЕНДАЦИИ
        recommendations = []

        # На основе профиля и навыков
        if analysis["profile"].get("skills"):
            skills_lower = analysis["profile"]["skills"].lower()
            if "python" in skills_lower or "программирование" in skills_lower:
                recommendations.append("Автоматизировать рутинные задачи через Python-скрипты")
            if "менеджмент" in skills_lower or "управление" in skills_lower:
                recommendations.append("Применить agile-методологии для эффективного управления задачами")
            if "дизайн" in skills_lower:
                recommendations.append("Визуализировать планы и цели через mind maps и kanban-доски")

        # На основе компании и позиции
        if analysis["profile"].get("company") and analysis["profile"].get("position"):
            company_lower = analysis["profile"]["company"].lower()
            position_lower = analysis["profile"]["position"].lower()
            if "tech" in company_lower or "it" in company_lower:
                recommendations.append("Внедрить инструменты DevOps для автоматизации процессов")
            if "менеджер" in position_lower or "руководитель" in position_lower:
                recommendations.append("Делегировать до 30% задач для фокуса на стратегических целях")

        # На основе паттернов задач
        if analysis["patterns"]["overdue_ratio"] > 0.3:
            recommendations.append("СРОЧНО: внедрить систему Eisenhower Matrix для приоритизации - более 30% задач просрочено")
        elif analysis["patterns"]["overdue_ratio"] > 0.15:
            recommendations.append("Пересмотреть планирование - часть задач регулярно просрочивается")

        if analysis["patterns"]["delegation_ratio"] < 0.1 and len(all_tasks) > 10:
            recommendations.append("Начать делегировать задачи - сейчас всё на тебе, это неэффективно")

        if analysis["patterns"]["completion_rate_percent"] < 50:
            recommendations.append("Ставить реалистичные дедлайны - процент выполнения низкий")
        elif analysis["patterns"]["completion_rate_percent"] > 80:
            recommendations.append("Отличная продуктивность! Можно брать более амбициозные задачи")

        # На основе тем
        main_theme = analysis["patterns"]["main_themes"][0][0] if analysis["patterns"]["main_themes"] else None
        if main_theme == "development":
            recommendations.append("Внедрить code review и CI/CD для повышения качества кода")
        elif main_theme == "business":
            recommendations.append("Создать CRM-систему для отслеживания клиентов и сделок")
        elif main_theme == "learning":
            recommendations.append("Использовать технику Pomodoro для эффективного обучения")

        # На основе текущей ситуации
        if len(overdue_tasks) > 0:
            recommendations.append(f"ВАЖНО: {len(overdue_tasks)} просроченных задач требуют внимания")
        if len(today_tasks) > 5:
            recommendations.append(f"Сегодня {len(today_tasks)} задач - стоит пересмотреть приоритеты")

        # На основе эмоционального состояния
        if analysis["context_insights"]["emotional_state"] == "stressed":
            recommendations.append("Сфокусируйся на одной критичной задаче прямо сейчас")
        if analysis["context_insights"]["wants_optimization"]:
            recommendations.append("Проанализировать повторяющиеся задачи для автоматизации")

        analysis["recommendations"] = recommendations[:7]  # Увеличили до 7 наиболее релевантных

        # 6. ПОИСК РЕЛЕВАНТНЫХ КОНТАКТОВ НА ОСНОВЕ ТЕКУЩЕГО КОНТЕКСТА
        # Анализируем текущую ситуацию и ищем подходящих людей
        if profile:
            all_profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
            contact_matches = []
            
            for contact_profile in all_profiles:
                relevance_score = 0
                reasons = []
                
                # Совпадение по интересам
                if profile.interests and contact_profile.interests:
                    user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                    contact_interests = set(i.strip().lower() for i in contact_profile.interests.split(','))
                    common_interests = user_interests & contact_interests
                    if common_interests:
                        relevance_score += len(common_interests) * 2
                        reasons.append(f"общие интересы: {', '.join(list(common_interests)[:2])}")
                
                # Совпадение по навыкам (кто может помочь)
                if profile.goals and contact_profile.skills:
                    goals_lower = profile.goals.lower()
                    skills_lower = contact_profile.skills.lower()
                    if any(skill in goals_lower for skill in skills_lower.split(',')):
                        relevance_score += 3
                        reasons.append("может помочь с текущими целями")
                
                # Совпадение по целям контакта и навыкам пользователя (кому пользователь может помочь)
                if profile.skills and contact_profile.goals:
                    user_skills_lower = profile.skills.lower()
                    contact_goals_lower = contact_profile.goals.lower()
                    if any(skill in contact_goals_lower for skill in user_skills_lower.split(',')):
                        relevance_score += 3
                        reasons.append("нуждается в твоей помощи")
                
                # Совпадение по задачам контакта и навыкам пользователя
                if profile.skills and contact_profile.current_plans:
                    user_skills_lower = profile.skills.lower()
                    contact_plans_lower = contact_profile.current_plans.lower()
                    if any(skill in contact_plans_lower for skill in user_skills_lower.split(',')):
                        relevance_score += 2
                        reasons.append("работает над тем, в чем ты силен")
                
                # Город (локальные связи)
                if profile.city and contact_profile.city and profile.city.lower() == contact_profile.city.lower():
                    relevance_score += 1
                    reasons.append("из того же города")
                
                # Компания/индустрия
                if profile.company and contact_profile.company:
                    if profile.company.lower() == contact_profile.company.lower():
                        relevance_score += 2
                        reasons.append("работает в той же компании")
                
                # Темы задач и навыки контакта
                if contact_profile.skills and main_theme:
                    skills_lower = contact_profile.skills.lower()
                    if (main_theme == "development" and any(word in skills_lower for word in ["python", "javascript", "программирование"])) or \
                       (main_theme == "business" and any(word in skills_lower for word in ["продажи", "маркетинг", "бизнес"])) or \
                       (main_theme == "learning" and any(word in skills_lower for word in ["преподавание", "коучинг", "менторство"])):
                        relevance_score += 2
                        reasons.append(f"эксперт в {main_theme}")
                
                if relevance_score > 0:
                    contact_matches.append({
                        "username": contact_profile.contact_info or f"user_{contact_profile.user_id}",
                        "score": relevance_score,
                        "reasons": reasons,
                        "profile": contact_profile
                    })
            
            # Сортируем по релевантности
            contact_matches.sort(key=lambda x: x["score"], reverse=True)
            analysis["relevant_contacts"] = contact_matches[:3]  # Топ-3 наиболее релевантных

        return analysis

    finally:
        session.close()


def parse_multiple_tasks(message):
    """
    Parse multiple tasks from a message.
    В AI-first подходе парсинг задач происходит через AI tools.
    """
    return []


def post_process_tool_calls(intent, tool_calls, message):
    """
    Пост-обработка tool calls - УПРОЩЕНА: AI-first подход, минимальная коррекция.
    """
    # В AI-first подходе полагаемся на AI, без сложной пост-обработки
    return tool_calls

def validate_response_compliance(response_text, intent_type=None):
    """
    Проверяет соответствие ответа правилам главного промпта
    Возвращает (is_compliant, issues_list)
    """
    issues = []

    # Проверка на запрещенные элементы (кроме list_tasks)
    if intent_type != "list_tasks":
        # Запрещенные технические эмодзи
        forbidden_emojis = ["🚀", "✅", "📝", "🎯", "⚠️", "💡", "📋", "⏳", "🟡", "🔧", "📊", "🔍", "⚙️", "🛠️"]
        if any(emoji in response_text for emoji in forbidden_emojis):
            issues.append("Присутствуют запрещенные технические эмодзи")
        
        # Разрешаем 1-2 подходящих эмодзи для общения
        allowed_emojis = ["👍", "👌", "✨", "🎉", "💪", "😊", "🙂", "😄", "👏", "🔥"]
        emoji_count = sum(1 for emoji in allowed_emojis if emoji in response_text)
        if emoji_count > 2:
            issues.append("Больше 2 разрешенных эмодзи в сообщении")
            
        if "**" in response_text:
            issues.append("Присутствует жирный текст")

    if re.search(r"^\s*[-•*]\s+", response_text, re.MULTILINE) and intent_type != "list_tasks":
        issues.append("Присутствуют маркированные списки")

    if re.search(r"^\s*\d+\.\s+", response_text, re.MULTILINE):
        issues.append("Присутствует нумерация")

    # Специфические проверки для разных типов intent - адаптивные правила
    if intent_type == "list_tasks":
        # Для просмотра задач - подробный анализ, но не слишком длинный
        if len(response_text) > 800:
            issues.append("Ответ на list_tasks слишком длинный")
        if len(response_text) < 100:
            issues.append("Ответ на list_tasks слишком короткий для анализа")
        if "Ваши задачи:" in response_text or "Список задач:" in response_text:
            issues.append("Шаблонный ответ вместо анализа")

    return len(issues) == 0, issues


def parse_tool_arguments(arguments_str):
    """Parse tool arguments from string, fallback to empty dict if parsing fails"""
    if arguments_str is None:
        return {}
    if not isinstance(arguments_str, str):
        raise ValueError("Arguments must be a string")

    try:
        return json.loads(arguments_str)
    except (json.JSONDecodeError, ValueError):
        return {}


def generate_task_recommendations(title, description, user_id):
    """Генерируем 2-3 краткие рекомендации для задачи (без лишней информации)"""
    try:
        import requests
        from config import DEEPSEEK_API_KEY
        
        prompt = """Проанализируй задачу и дай 2-3 КРАТКИХ рекомендации (максимум 3-4 слова).

Задача: {title}

Формат: только конкретные действия, без лишних слов.

Примеры:
- Составьте список заранее
- Уточните слот доставки
- Проверьте результат"""

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.5
            },
            timeout=8
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Парсим рекомендации
            recommendations = []
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('•'):
                    rec = line.lstrip('-•').strip()
                    if rec and len(rec) <= 50:  # Максимум 50 символов
                        recommendations.append(rec)
            
            return recommendations[:3]  # Максимум 3 рекомендации
        else:
            return []
    except Exception as e:
        import logging
        logging.warning(f"Error generating recommendations: {e}")
        return []


def analyze_user_context_for_advice(user_id, db_session=None):
    """
    Анализирует контекст пользователя для проактивных советов и предложений контактов.
    Возвращает словарь с рекомендациями, включая обратные связи (кого пользователь может помочь).
    """
    if not user_id:
        return {}
    
    if db_session is None:
        from models import Session
        db_session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {}
        
        profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            return {}
        
        recommendations = {
            'contact_suggestions': [],
            'reverse_contacts': [],  # Кто может помочь пользователю
            'helpful_contacts': [],  # Кого пользователь может помочь
            'task_suggestions': [],
            'profile_improvements': []
        }
        
        # Анализ всех профилей для поиска потенциальных контактов
        all_profiles = db_session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
        
        user_skills = set((profile.skills or "").lower().split(", ")) if profile.skills else set()
        user_interests = set((profile.interests or "").lower().split(", ")) if profile.interests else set()
        user_goals = set((profile.goals or "").lower().split(", ")) if profile.goals else set()
        
        for other_profile in all_profiles:
            if not other_profile.contact_info or other_profile.contact_info == f"user{user_id}":
                continue
                
            other_skills = set((other_profile.skills or "").lower().split(", ")) if other_profile.skills else set()
            other_interests = set((other_profile.interests or "").lower().split(", ")) if other_profile.interests else set()
            other_goals = set((other_profile.goals or "").lower().split(", ")) if other_profile.goals else set()
            
            # Проверяем, кому пользователь может помочь (reverse contacts)
            skills_match = user_skills & other_goals  # Навыки пользователя совпадают с целями другого
            if skills_match and len(recommendations['helpful_contacts']) < 3:
                contact_name = other_profile.contact_info.split('@')[-1] if '@' in other_profile.contact_info else other_profile.contact_info
                recommendations['helpful_contacts'].append({
                    'contact': contact_name,
                    'reason': f"можешь помочь с {', '.join(list(skills_match)[:2])}",
                    'match_type': 'skills_to_goals'
                })
            
            # Проверяем, кто может помочь пользователю
            goals_match = user_goals & other_skills  # Цели пользователя совпадают с навыками другого
            if goals_match and len(recommendations['reverse_contacts']) < 3:
                contact_name = other_profile.contact_info.split('@')[-1] if '@' in other_profile.contact_info else other_profile.contact_info
                recommendations['reverse_contacts'].append({
                    'contact': contact_name,
                    'reason': f"может помочь с {', '.join(list(goals_match)[:2])}",
                    'match_type': 'goals_to_skills'
                })
            
            # Общие интересы для networking
            interest_match = user_interests & other_interests
            if interest_match and len(recommendations['contact_suggestions']) < 2:
                contact_name = other_profile.contact_info.split('@')[-1] if '@' in other_profile.contact_info else other_profile.contact_info
                recommendations['contact_suggestions'].append({
                    'contact': contact_name,
                    'reason': f"общие интересы: {', '.join(list(interest_match)[:2])}",
                    'match_type': 'shared_interests'
                })
        
        # Анализ задач для предложений
        pending_tasks = db_session.query(Task).filter_by(user_id=user.id, status="pending").limit(5).all()
        for task in pending_tasks:
            if "встреча" in task.title.lower() or "звонок" in task.title.lower():
                if profile.city and len(recommendations['task_suggestions']) < 2:
                    recommendations['task_suggestions'].append(f"Возможно, стоит найти партнера в {profile.city} для этой встречи?")
            elif any(skill in task.title.lower() for skill in user_skills):
                recommendations['task_suggestions'].append("Эта задача использует твои навыки - может, делегировать часть работы?")
        
        return recommendations
        
    except Exception as e:
        logger.error(f"Error in analyze_user_context_for_advice: {e}")
        return {}
    finally:
        if close_session:
            db_session.close()


def post_process_response(content, user_id, db_session=None):
    """
    Пост-обработка ответа AI для улучшения качества:
    - Удаление форматирования
    - Добавление проактивных предложений контактов
    - Улучшение естественности
    """
    if not content or not user_id:
        return content
    
    # Удаляем форматирование
    content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)  # Убираем жирный текст
    content = re.sub(r'\*(.*?)\*', r'\1', content)     # Убираем курсив
    content = re.sub(r'`(.*?)`', r'\1', content)       # Убираем inline code
    content = re.sub(r'```.*?```', '', content, flags=re.DOTALL)  # Убираем code blocks
    content = re.sub(r'#+\s*', '', content)            # Убираем заголовки
    content = content.replace('🚫', '')                # Убираем запрещающий знак
    
    # СТРОГО ЗАПРЕЩЕННЫЕ ЭЛЕМЕНТЫ ФОРМАТИРОВАНИЯ (требования проекта)
    # Удаляем маркеры списков в начале строк
    content = re.sub(r'^\s*[-*•]\s+', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*\d+\.\s+(?=[А-ЯA-Z])', '', content, flags=re.MULTILINE)  # Только если после точки заглавная буква
    content = re.sub(r'^\s*[a-zA-Z]\.\s+', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*\(\d+\)\s+', '', content, flags=re.MULTILINE)
    
    # Удаляем маркеры списков в середине текста (после переносов строк)
    content = re.sub(r'\n\s*[-*•]\s+', '\n', content)
    content = re.sub(r'\n\s*\d+\.\s+(?=[А-ЯA-Z])', '\n', content)  # Только если после точки заглавная буква
    content = re.sub(r'\n\s*[a-zA-Z]\.\s+', '\n', content)
    content = re.sub(r'\n\s*\(\d+\)\s*', '\n', content)
    
    # Преобразуем перечисления через "или" в естественный текст
    content = re.sub(r'(\w+)\s*или\s*(\w+)\s*или\s*(\w+)', r'\1, \2 или \3', content)
    content = re.sub(r'(\w+)\s*или\s*(\w+)', r'\1 или \2', content)
    
    # Удаляем все конструкции типа "Что хочешь сделать: задача или партнер" - заменяем на естественный текст
    # ИСКЛЮЧАЕМ время в формате HH:MM - упрощаем логику
    # Временно отключаем проблемные regex чтобы избежать ошибок типа "Можешь 48"
    # content = re.sub(r'(?!\d{1,2}:\d{2})(\w+.*?):\s*([^.?]*?или[^.?]*?)\?', r'Можешь \2?', content, flags=re.IGNORECASE)
    # content = re.sub(r'(?!\d{1,2}:\d{2})(\w+.*?):\s*([^.?]*?или[^.?]*?)\.', r'Можешь \2.', content, flags=re.IGNORECASE)
    
    # Удаляем оставшиеся двоеточия с любыми перечислениями (упрощаем, убираем проблемные lookbehind)
    content = re.sub(r':\s*([^.]*?или[^.]*?)\?', r' \1?', content)
    content = re.sub(r':\s*([^.]*?или[^.]*?)\.', r' \1.', content)
    
    # Получаем рекомендации по контактам
    advice = analyze_user_context_for_advice(user_id, db_session)
    
    # Добавляем проактивные предложения контактов, если ответ не слишком длинный
    if len(content) < 300:  # Только для коротких ответов
        additions = []
        
        # Предложения кого пользователь может помочь
        if advice.get('helpful_contacts') and len(additions) < 1:
            contact = advice['helpful_contacts'][0]
            additions.append(f"Кстати, {contact['contact']} работает над тем, с чем ты {contact['reason']}.")
        
        # Предложения кто может помочь пользователю
        elif advice.get('reverse_contacts') and len(additions) < 1:
            contact = advice['reverse_contacts'][0]
            additions.append(f"Может, {contact['contact']} {contact['reason']}?")
        
        # Общие рекомендации по задачам
        elif advice.get('task_suggestions') and len(additions) < 1:
            additions.append(advice['task_suggestions'][0])
        
        # Добавляем дополнение естественным образом
        if additions:
            content = content.rstrip('?!.') + '. ' + additions[0]
    
    return content.strip()


async def extract_short_title_from_message(message, current_title):
    """
    Извлекает короткое название задачи из длинного текста пользователя.
    Использует DeepSeek API для умного извлечения сути.
    
    Args:
        message: Оригинальное сообщение пользователя
        current_title: Неправильный title, который нужно исправить
        
    Returns:
        Короткое название задачи (2-5 слов) или None если не удалось
    """
    import aiohttp
    
    try:
        prompt = """Извлеки КОРОТКОЕ название задачи (2-5 слов) из сообщения пользователя.

ПРИМЕРЫ:
"давай запланируем пробежку завтра утром в парке" → "Пробежка"
"напомни мне позвонить Сидорову обсудить договор" → "Позвонить Сидорову"
"нужно отправить отчёт по проекту Солар клиенту" → "Отправить отчёт Солар"
"добавь задачу подготовить презентацию к встрече" → "Подготовить презентацию"
"создай задачу купить молоко и хлеб в магазине" → "Купить молоко и хлеб"

СООБЩЕНИЕ: "{message}"
НЕПРАВИЛЬНЫЙ ВАРИАНТ: "{current_title[:100]}"

Ответь ТОЛЬКО коротким названием задачи (2-5 слов), без кавычек и пояснений."""

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 30,
            "temperature": 0.3  # Низкая температура для точности
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    result = await response.json()
                    extracted_title = result["choices"][0]["message"]["content"].strip()
                    
                    # Очищаем от кавычек, точек и лишних символов
                    extracted_title = extracted_title.strip('"\'.,!?')
                    
                    # Проверяем что результат разумный (2-10 слов, не больше 60 символов)
                    word_count = len(extracted_title.split())
                    if 2 <= word_count <= 10 and len(extracted_title) <= 60:
                        logger.info(f"[EXTRACT_TITLE] Successfully extracted: '{extracted_title}' from message: '{message[:80]}'")
                        return extracted_title
                    else:
                        logger.warning(f"[EXTRACT_TITLE] Extracted title invalid: {word_count} words, {len(extracted_title)} chars")
                        return None
                else:
                    logger.error(f"[EXTRACT_TITLE] API error: {response.status}")
                    return None
                    
    except Exception as e:
        logger.error(f"[EXTRACT_TITLE] Error: {e}")
        return None


def extract_time_from_message(message):
    """
    Извлекает время из сообщения пользователя используя регулярные выражения.
    Возвращает найденное время в виде строки или None.
    
    Args:
        message: Сообщение пользователя
        
    Returns:
        Строка со временем или None
    """
    import re
    
    # Паттерны для поиска времени
    patterns = [
        (r'(?:на|в)\s+(\d{1,2}):(\d{2})', 'exact'),  # "на 10:30", "в 14:00"
        (r'(?:на|в)\s+(\d{1,2})\s+(?:час|утра|вечера|дня)', 'hour'),  # "в 10 утра", "на 15 часов"
        (r'(\d{1,2}):(\d{2})', 'exact'),  # просто "10:30"
        (r'(?:завтра|сегодня)\s+(?:в|на)\s+(\d{1,2}):(\d{2})', 'exact'),  # "завтра в 10:30"
        (r'(?:завтра|сегодня)\s+(?:в|на)\s+(\d{1,2})\s+(?:час|утра|вечера)', 'hour'),  # "завтра в 10 утра"
    ]
    
    for pattern, time_type in patterns:
        match = re.search(pattern, message.lower())
        if match:
            if time_type == 'exact':
                hour = int(match.group(1))
                minute = int(match.group(2)) if len(match.groups()) > 1 else 0
                return f"{hour:02d}:{minute:02d}"
            elif time_type == 'hour':
                hour = int(match.group(1))
                return f"{hour:02d}:00"
    
    return None


async def post_process_profile_update(user_id, message, db_session):
    """
    Пост-обработка сообщения для автоматического обновления профиля.
    Анализирует сообщение и извлекает информацию о профиле, затем обновляет.
    
    Args:
        user_id: ID пользователя
        message: Сообщение пользователя
        db_session: Сессия БД
    
    Returns:
        None - обновление происходит автоматически
    """
    from .handlers import update_profile
    import logging
    
    logger = logging.getLogger(__name__)
    
    if not message or not user_id:
        return
    
    message_lower = message.lower().strip()
    
    # Простой анализ на наличие profile info
    profile_indicators = [
        'я', 'мне', 'у меня', 'работаю', 'занимаюсь', 'люблю', 'интересует',
        'умею', 'знаю', 'специалист', 'опыт', 'навыки', 'интересы', 'цели',
        'москва', 'питер', 'екатеринбург', 'новосибирск', 'казань', 'ростов',
        'программирование', 'дизайн', 'маркетинг', 'бизнес', 'ии', 'ai', 'бот'
    ]
    
    has_profile_info = any(indicator in message_lower for indicator in profile_indicators)
    
    if not has_profile_info:
        return
    
    # Извлекаем данные профиля простым парсингом
    profile_data = {}
    
    # Город
    cities = {
        'москва': 'Москва',
        'москве': 'Москва', 
        'питер': 'Санкт-Петербург',
        'спб': 'Санкт-Петербург',
        'екатеринбург': 'Екатеринбург',
        'екб': 'Екатеринбург',
        'новосибирск': 'Новосибирск',
        'нск': 'Новосибирск',
        'казань': 'Казань',
        'ростов': 'Ростов-на-Дону',
        'уфа': 'Уфа',
        'челябинск': 'Челябинск',
        'пермь': 'Пермь',
        'красноярск': 'Красноярск',
        'воронеж': 'Воронеж',
        'волгоград': 'Волгоград',
        'ярославль': 'Ярославль',
        'омск': 'Омск',
        'тюмень': 'Тюмень',
        'иркутск': 'Иркутск'
    }
    
    for city_key, city_name in cities.items():
        if city_key in message_lower:
            profile_data['city'] = city_name
            break
    
    # Навыки
    skill_patterns = [
        r'работаю\s+с\s+(.+?)(?:\s|$|[.,!?;])',
        r'занимаюсь\s+(.+?)(?:\s|$|[.,!?;])',
        r'умею\s+(.+?)(?:\s|$|[.,!?;])',
        r'знаю\s+(.+?)(?:\s|$|[.,!?;])',
        r'специалист\s+(.+?)(?:\s|$|[.,!?;])',
        r'разработал\s+(.+?)(?:\s|$|[.,!?;])',
        r'создал\s+(.+?)(?:\s|$|[.,!?;])'
    ]
    
    for pattern in skill_patterns:
        import re
        match = re.search(pattern, message_lower)
        if match:
            skill = match.group(1).strip()
            if len(skill) > 2 and len(skill) < 50:
                profile_data['skills'] = skill
                break
    
    # Интересы
    interest_patterns = [
        r'люблю\s+(.+?)(?:\s|$|[.,!?;])',
        r'интересует\s+(.+?)(?:\s|$|[.,!?;])',
        r'увлекаюсь\s+(.+?)(?:\s|$|[.,!?;])',
        r'нравится\s+(.+?)(?:\s|$|[.,!?;])'
    ]
    
    for pattern in interest_patterns:
        match = re.search(pattern, message_lower)
        if match:
            interest = match.group(1).strip()
            if len(interest) > 2 and len(interest) < 50:
                profile_data['interests'] = interest
                break
    
    # Цели
    goal_patterns = [
        r'хочу\s+(.+?)(?:\s|$|[.,!?;])',
        r'планирую\s+(.+?)(?:\s|$|[.,!?;])',
        r'мечтаю\s+(.+?)(?:\s|$|[.,!?;])',
        r'цель\s+(.+?)(?:\s|$|[.,!?;])'
    ]
    
    for pattern in goal_patterns:
        match = re.search(pattern, message_lower)
        if match:
            goal = match.group(1).strip()
            if len(goal) > 2 and len(goal) < 100:
                profile_data['goals'] = goal
                break
    
    # Компания
    company_patterns = [
        r'работаю\s+в\s+(.+?)(?:\s|$|[.,!?;])',
        r'компания\s+(.+?)(?:\s|$|[.,!?;])'
    ]
    
    for pattern in company_patterns:
        match = re.search(pattern, message_lower)
        if match:
            company = match.group(1).strip()
            if len(company) > 2 and len(company) < 50:
                profile_data['company'] = company.title()
                break
    
    # Если есть данные для обновления - обновляем
    if profile_data:
        try:
            logger.info(f"[PROFILE POST-PROCESS] Updating profile for user {user_id} with data: {profile_data}")
            result = update_profile(
                user_id=user_id,
                city=profile_data.get('city'),
                interests=profile_data.get('interests'),
                skills=profile_data.get('skills'),
                goals=profile_data.get('goals'),
                company=profile_data.get('company'),
                session=db_session
            )
            if result:
                logger.info(f"[PROFILE POST-PROCESS] Profile updated: {result}")
        except Exception as e:
            logger.error(f"[PROFILE POST-PROCESS] Error updating profile: {e}")


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
    """
    Получает информацию о погоде для города с умным кешированием.
    Если кэш устарел - запускает фоновое обновление, но возвращает старые данные немедленно.
    Возвращает строку с описанием погоды или None при ошибке.
    """
    if not city or not OPENWEATHERMAP_API_KEY:
        return None

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
        if redis_client:
            try:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left < ttl_seconds / 2:
                    refresh_weather_cache_async(city, cache_ttl_minutes)
            except:
                pass
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
    """
    Синхронно загружает погоду (используется только при первом запросе или принудительном обновлении).
    """
    try:
        # Запрашиваем погоду
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


def get_news_info(city=None, cache_ttl_minutes=120):  # Уменьшил TTL до 2 часов для актуальности
    """
    Получает новости с умным кешированием.
    Если кэш устарел - запускает фоновое обновление, но возвращает старые данные немедленно.
    Возвращает строку с кратким описанием новостей или None при ошибке.
    """
    if not NEWSAPI_API_KEY:
        return None

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
        if redis_client:
            try:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left < ttl_seconds / 2:
                    refresh_news_cache_async(city, cache_ttl_minutes)
            except:
                pass
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
    """
    Синхронно загружает новости (используется только при первом запросе или принудительном обновлении).
    """
    try:
        # Определяем параметры запроса
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


def get_finance_info(symbol, asset_type, cache_ttl_minutes=15):
    """
    Получает финансовую информацию с умным кешированием.
    Если кэш устарел - запускает фоновое обновление, но возвращает старые данные немедленно.
    Возвращает словарь с данными или None при ошибке.
    """
    if not ALPHA_VANTAGE_API_KEY:
        return None

    cache_key = f"{asset_type}_{symbol.lower()}"
    ttl_seconds = cache_ttl_minutes * 60

    # Проверяем Redis кеш
    cached_data = _redis_get(cache_key)
    if cached_data:
        logger.info(f"[FINANCE CACHE] Using Redis cached data for {symbol} ({asset_type})")
        # Запускаем фоновое обновление если данные старше половины TTL
        if redis_client:
            try:
                ttl_left = redis_client.ttl(cache_key)
                if ttl_left < ttl_seconds / 2:
                    refresh_finance_cache_async(symbol, asset_type, cache_ttl_minutes)
            except:
                pass
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


def _load_finance_sync(symbol, asset_type):
    """
    Синхронно загружает финансовые данные (используется только при первом запросе или принудительном обновлении).
    """
    try:
        # Определяем API URL в зависимости от типа актива
        if asset_type == 'stock':
            api_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol.upper()}&apikey={ALPHA_VANTAGE_API_KEY}"
        elif asset_type == 'commodity' and symbol.upper() in ['WTI', 'BRENT']:
            if symbol.upper() == 'WTI':
                api_url = f"https://www.alphavantage.co/query?function=WTI&interval=monthly&apikey={ALPHA_VANTAGE_API_KEY}"
            else:
                api_url = f"https://www.alphavantage.co/query?function=BRENT&interval=monthly&apikey={ALPHA_VANTAGE_API_KEY}"
        elif asset_type == 'currency':
            # Для валют предполагаем формат FROM/TO
            if '/' in symbol:
                from_curr, to_curr = symbol.split('/', 1)
                api_url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={from_curr}&to_currency={to_curr}&apikey={ALPHA_VANTAGE_API_KEY}"
            else:
                logger.error(f"Invalid currency format: {symbol}. Use FROM/TO format")
                return None
        else:
            logger.error(f"Unsupported asset type: {asset_type} for symbol {symbol}")
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
