import logging
import re
from datetime import datetime, timedelta, timezone
import pytz
from models import Session, User, UserProfile, Task, Interaction
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL
)
import json
import requests
import hashlib

logger = logging.getLogger(__name__)


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
    
    prompt = f"""
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

    if is_greeting and len(str(ai_response_content).strip()) < 50:  # Ответ AI слишком короткий
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
    time_match = re.search(r"(завтра|послезавтра|сегодня)\s+(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
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
            recommendations.append("При стрессе: разбить задачи на микрошаги по 15-20 минут")
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


def post_process_tool_calls(intent, tool_calls, message):
    """
    Пост-обработка tool calls для коррекции ошибок AI.
    Возвращает исправленные tool_calls или None если коррекция не нужна.
    """
    corrected_calls = []
    function_name = None

    # СПЕЦИАЛЬНАЯ ОБРАБОТКА UPDATE_PROFILE: фильтруем и корректируем неправильные calls
    if intent.get("type") == "update_profile" and tool_calls:
        update_profile_calls = [call for call in tool_calls if call.get("function", {}).get("name") == "update_profile"]

        if update_profile_calls:
            # Анализируем сообщение для определения правильных полей
            message_lower = message.lower().strip()

            # Определяем, какие поля должны быть обновлены
            correct_args = {}

            # Навыки (skills)
            if any(word in message_lower for word in ["навык", "умею", "знаю", "могу", "опыт", "специалист"]):
                # Извлекаем навыки из сообщения
                skills_text = message
                # Удаляем слова-триггеры
                for trigger in ["добавь навыки", "добавь навык", "обнови навыки", "навыки"]:
                    skills_text = skills_text.replace(trigger, "").strip()
                if skills_text:
                    correct_args["skills"] = skills_text

            # Город (city)
            if any(word in message_lower for word in ["город", "живу в", "из города"]):
                city_text = message
                # Удаляем слова-триггеры
                for trigger in ["обнови город на", "добавь город", "город"]:
                    city_text = city_text.replace(trigger, "").strip()
                if city_text:
                    correct_args["city"] = city_text

            # Компания (company)
            if any(word in message_lower for word in ["компания", "работаю в", "фирма", "работодатель"]):
                company_text = message
                # Удаляем слова-триггеры
                for trigger in ["добавь компанию", "обнови компанию", "компания", "работаю в"]:
                    company_text = company_text.replace(trigger, "").strip()
                if company_text:
                    correct_args["company"] = company_text

            # Если не нашли специфических полей, используем interests по умолчанию
            if not correct_args:
                correct_args["interests"] = message

            # Создаем один корректный call вместо всех неправильных
            corrected_calls.append({
                "index": 0,
                "id": "call_corrected_profile",
                "type": "function",
                "function": {
                    "name": "update_profile",
                    "arguments": json.dumps(correct_args)
                }
            })

            # Добавляем остальные calls (не update_profile)
            for call in tool_calls:
                if call.get("function", {}).get("name") != "update_profile":
                    call_copy = call.copy()
                    call_copy["index"] = len(corrected_calls)
                    corrected_calls.append(call_copy)

            return corrected_calls

    # Обычная обработка для других случаев
    corrected_calls = tool_calls.copy() if tool_calls else []
    function_name = None

    for call in tool_calls:
        args = call.get("function", {}).get("arguments", "{}")

        try:
            args_dict = json.loads(args) if isinstance(args, str) else args
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse tool call arguments: {e}")
            args_dict = {}

        # 1. ЭМОЦИИ: если intent эмоция, но нет list_tasks - добавляем
        if intent["type"].startswith("emotion_") and function_name != "list_tasks":
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "arguments": "{}"
                }
            })

        # 2. ДОБАВЛЕНИЕ ЗАДАЧ: если intent add_task, но нет add_task - добавляем
        elif intent["type"] == "add_task" and function_name != "add_task":
            # Извлекаем задачу из сообщения
            task_title = message
            
            # Удаляем команды в начале
            task_title = re.sub(r'^(напомни(?:ть)?|добавь|запомни|создай задачу|новая задача)\s+', '', task_title, flags=re.IGNORECASE)
            
            # Удаляем временные указания с контекстом ("через 5 минут", "завтра в 10:00" и т.д.)
            task_title = re.sub(r'\bчерез\s+\d+\s*(?:мин(?:ут)?|час(?:а|ов)?|дн(?:я|ей)?|недел(?:ю|и|ь)?|месяц(?:а|ев)?|год(?:а)?)', '', task_title, flags=re.IGNORECASE)
            task_title = re.sub(r'\b(?:завтра|сегодня|послезавтра)(?:\s+в\s+\d{1,2}:\d{2})?', '', task_title, flags=re.IGNORECASE)
            task_title = re.sub(r'\bв\s+\d{1,2}:\d{2}', '', task_title, flags=re.IGNORECASE)
            task_title = re.sub(r'\bна\s+\d{1,2}:\d{2}', '', task_title, flags=re.IGNORECASE)
            
            # Очищаем от лишних пробелов
            task_title = ' '.join(task_title.split()).strip()
            
            # Если title пустой или слишком короткий, используем оригинальное сообщение
            if not task_title or len(task_title) < 3:
                task_title = message
            
            time_indicators = ["завтра", "сегодня", "через", "в", "на", "к", "до"]
            for indicator in time_indicators:
                if indicator in message.lower():
                    # Сначала попробуем найти абсолютное время
                    time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})", message)
                    if time_match:
                        args_dict["reminder_time"] = time_match.group(1)
                    else:
                        # Если абсолютного нет, попробуем извлечь относительное время
                        relative_patterns = [
                            r"через\s+(\d+)\s*мин",
                            r"через\s+(\d+)\s*минут",
                            r"через\s+(\d+)\s*час",
                            r"через\s+(\d+)\s*часа",
                            r"через\s+(\d+)\s*часов"
                        ]
                        for pattern in relative_patterns:
                            rel_match = re.search(pattern, message, re.IGNORECASE)
                            if rel_match:
                                # Извлекаем всю фразу относительного времени
                                full_match = re.search(r"(через\s+\d+\s*(?:мин|минут|час|часа|часов))", message, re.IGNORECASE)
                                if full_match:
                                    args_dict["reminder_time"] = full_match.group(1)
                                break
                    break

            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "add_task",
                    "arguments": json.dumps({
                        "title": task_title,
                        "reminder_time": args_dict.get("reminder_time")
                    })
                }
            })

        # 3. ЗАВЕРШЕНИЕ: если intent complete_task, но нет complete_task - добавляем
        elif intent["type"] == "complete_task" and function_name != "complete_task":
            task_title = intent.get("params", {}).get("task_title", "")
            if task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({"title": task_title})
                    }
                })

        # 4. ПРОФИЛЬ: если intent update_profile, но нет update_profile - добавляем
        elif intent["type"] == "update_profile" and function_name != "update_profile":
            field = intent.get("params", {}).get("field", "interests")
            value = message
            # Обработка команд типа "оставь только спорт" - очистить и оставить только указанное
            if "только" in message.lower():
                parts = message.lower().split("только")
                if len(parts) > 1:
                    remaining = parts[1].strip()
                    # Извлекаем интерес после "только"
                    # Предполагаем, что после "только" идет список интересов
                    value = f"только {remaining}"
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "update_profile",
                    "arguments": json.dumps({field: value})
                }
            })

        # 5. ДЕЛЕГИРОВАНИЕ: если intent delegate_task, но нет delegate_task - добавляем
        elif intent["type"] == "delegate_task" and function_name != "delegate_task":
            delegated_to = intent.get("params", {}).get("delegated_to", "")
            task_title = intent.get("params", {}).get("task_title", "")
            reminder_time = intent.get("params", {}).get("reminder_time")

            if delegated_to and task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "arguments": json.dumps({
                            "title": task_title,
                            "delegated_to": delegated_to,
                            "reminder_time": reminder_time
                        })
                    }
                })

        # Если коррекция не нужна, оставляем оригинальный call
        else:
            corrected_calls.append(call)

    # Handle cases where no tool calls but intent requires action
    if not tool_calls:
        if intent["type"].startswith("emotion_"):
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "list_tasks",
                    "arguments": "{}"
                }
            })
        elif intent["type"] == "add_task":
            # Извлекаем задачу из сообщения
            task_title = message.strip()
            if task_title:
                # Ищем время в сообщении
                args_dict = {}
                time_match = re.search(r"(?:напоминание|напомни|в|через)\s+(.+)", message, re.IGNORECASE)
                if time_match:
                    time_str = time_match.group(1).strip()
                    args_dict["reminder_time"] = time_str
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "add_task",
                        "arguments": json.dumps({
                            "title": task_title,
                            "reminder_time": args_dict.get("reminder_time")
                        })
                    }
                })
        elif intent["type"] == "complete_task":
            task_title = intent.get("params", {}).get("task_title", "")
            if task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "complete_task",
                        "arguments": json.dumps({"title": task_title})
                    }
                })
        elif intent["type"] == "update_profile":
            field = intent.get("params", {}).get("field", "interests")
            value = message
            # Обработка команд типа "оставь только спорт" - очистить и оставить только указанное
            if "только" in message.lower():
                parts = message.lower().split("только")
                if len(parts) > 1:
                    remaining = parts[1].strip()
                    value = f"только {remaining}"
            corrected_calls.append({
                "index": len(corrected_calls),
                "id": f"call_corrected_{len(corrected_calls)}",
                "type": "function",
                "function": {
                    "name": "update_profile",
                    "arguments": json.dumps({field: value})
                }
            })
        elif intent["type"] == "delegate_task":
            delegated_to = intent.get("params", {}).get("delegated_to", "")
            task_title = intent.get("params", {}).get("task_title", "")
            reminder_time = intent.get("params", {}).get("reminder_time")
            if delegated_to and task_title:
                corrected_calls.append({
                    "index": len(corrected_calls),
                    "id": f"call_corrected_{len(corrected_calls)}",
                    "type": "function",
                    "function": {
                        "name": "delegate_task",
                        "arguments": json.dumps({
                            "title": task_title,
                            "delegated_to": delegated_to,
                            "reminder_time": reminder_time
                        })
                    }
                })

    return corrected_calls if corrected_calls != tool_calls else None


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
        
        prompt = f"""Проанализируй задачу и дай 2-3 КРАТКИХ рекомендации (максимум 3-4 слова).

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
        prompt = f"""Извлеки КОРОТКОЕ название задачи (2-5 слов) из сообщения пользователя.

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
