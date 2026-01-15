import logging
import redis
import re
from datetime import datetime, timedelta, timezone
import pytz
from models import Session, User, UserProfile, Task, Interaction
from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    ENCRYPTION_KEY
)
from cryptography.fernet import Fernet, InvalidToken
import json
import requests
import hashlib

logger = logging.getLogger(__name__)
cipher = Fernet(ENCRYPTION_KEY.encode())

# Redis client - будет импортирован из main.py
redis_client = None


def set_redis_client(client):
    """Установка Redis клиента из main.py"""
    global redis_client
    redis_client = client


def analyze_interaction_for_profile_update(user_id, message, ai_response):
    """
    Анализирует взаимодействие пользователя для предложения обновления профиля.
    Возвращает предложение обновления профиля или None.
    """
    from models import Session, UserProfile
    import re
    
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

    if is_greeting and len(ai_response_content.strip()) < 50:  # Ответ AI слишком короткий
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

    # Перепроверка метода: проверяем, правда ли AI создал tool calls
    from improved_prompts_final import improved_classify_intent
    intent = improved_classify_intent(message, mentions_str)

    # Если это просто дружеское общение - fallback не нужен
    if intent["type"] == "conversation":
        return fallback_actions

    return fallback_actions


def encrypt_data(data):
    if data:
        return cipher.encrypt(data.encode()).decode()
    return data


def decrypt_data(data):
    if data is None:
        return None
    if not isinstance(data, str):
        raise ValueError("Data must be a string")
    if data:
        try:
            return cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            # If decryption fails, assume it's plain text (for backward compatibility)
            return data
    return data


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

    # Проверяем просто "в HH:MM"
    simple_time_match = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
    if simple_time_match:
        hour = int(simple_time_match.group(1))
        minute = int(simple_time_match.group(2))

        # Если время уже прошло сегодня - ставим на завтра
        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    # Проверяем "утром", "вечером", "днем"
    time_word_match = re.search(r"(утром|вечером|днем)", time_text)
    if time_word_match:
        time_word = time_word_match.group(1).lower()
        if "утром" in time_word:
            hour, minute = 8, 0
        elif "вечером" in time_word:
            hour, minute = 18, 0
        elif "днем" in time_word:
            hour, minute = 12, 0

        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        # Если время уже прошло сегодня - ставим на завтра
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

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
        (r"через\s+(\d+)\s*час", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часа", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часов", lambda m: timedelta(hours=int(m.group(1)))),
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

    # Удаляем названия функций (с скобками и без)
    before = text
    text = re.sub(
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)(\s*\(\s*\))?",
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
    Глубокий анализ контекста пользователя для генерации персонализированных советов.
    Возвращает структурированный анализ для использования в промпте.
    """
    from models import Session, User, UserProfile, Task
    from datetime import datetime, timedelta
    import pytz

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {"error": "Пользователь не найден"}

        analysis = {
            "profile": {},
            "tasks": {},
            "patterns": {},
            "context_insights": {},
            "recommendations": {}
        }

        # 1. АНАЛИЗ ПРОФИЛЯ
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
                "filled_fields": sum([1 for field in [profile.city, profile.company, profile.position, profile.bio, profile.languages, profile.skills, profile.interests, profile.goals] if field])
            }

        # 2. АНАЛИЗ ЗАДАЧ
        all_tasks = session.query(Task).filter_by(user_id=user.id).all()
        pending_tasks = [t for t in all_tasks if t.status == "pending"]
        completed_tasks = [t for t in all_tasks if t.status == "completed"]

        analysis["tasks"] = {
            "total": len(all_tasks),
            "pending": len(pending_tasks),
            "completed": len(completed_tasks),
            "completion_rate": len(completed_tasks) / max(len(all_tasks), 1),
            "overdue": len([t for t in pending_tasks if t.reminder_time and (t.reminder_time.replace(tzinfo=pytz.UTC) if t.reminder_time.tzinfo is None else t.reminder_time) < datetime.now(pytz.UTC)]),
            "delegated": len([t for t in all_tasks if t.delegated_to_username])
        }

        # 3. АНАЛИЗ ПАТТЕРНОВ
        # Анализ тем задач
        task_titles = [t.title.lower() for t in all_tasks]
        themes = {
            "development": sum(1 for title in task_titles if any(word in title for word in ["разработка", "код", "программирование", "dev", "backend", "frontend"])),
            "meetings": sum(1 for title in task_titles if any(word in title for word in ["встреча", "совещание", "митинг", "meeting"])),
            "documents": sum(1 for title in task_titles if any(word in title for word in ["документ", "отчет", "презентация", "документация"])),
            "communication": sum(1 for title in task_titles if any(word in title for word in ["звонок", "позвонить", "написать", "ответить"])),
            "learning": sum(1 for title in task_titles if any(word in title for word in ["изучить", "обучить", "курс", "тренинг"])),
            "business": sum(1 for title in task_titles if any(word in title for word in ["инвестор", "стартап", "бизнес", "продажа", "клиент"]))
        }

        analysis["patterns"] = {
            "main_themes": sorted(themes.items(), key=lambda x: x[1], reverse=True)[:3],
            "task_frequency": len(all_tasks) / max((datetime.now() - user.created_at.replace(tzinfo=None)).days, 1),
            "delegation_ratio": len([t for t in all_tasks if t.delegated_to_username]) / max(len(all_tasks), 1),
            "overdue_ratio": analysis["tasks"]["overdue"] / max(analysis["tasks"]["pending"], 1)
        }

        # 4. АНАЛИЗ КОНТЕКСТА СООБЩЕНИЯ
        message_lower = message.lower()
        analysis["context_insights"] = {
            "urgency_level": "high" if any(word in message_lower for word in ["срочно", "дедлайн", "завтра", "сегодня", "немедленно"]) else "normal",
            "emotional_state": "stressed" if any(word in message_lower for word in ["стресс", "давление", "проблема", "застрял", "сложно"]) else
                            "motivated" if any(word in message_lower for word in ["хочу", "заинтересован", "готов", "вдохновлен"]) else "neutral",
            "request_type": "advice" if any(word in message_lower for word in ["как", "что делать", "совет", "помоги"]) else
                          "action" if any(word in message_lower for word in ["сделай", "добавь", "удали", "обнови"]) else "info"
        }

        # 5. ПЕРСОНАЛИЗИРОВАННЫЕ РЕКОМЕНДАЦИИ
        recommendations = []

        # На основе профиля
        if analysis["profile"].get("skills") and "python" in analysis["profile"]["skills"].lower():
            recommendations.append("Использовать Python-библиотеки для автоматизации рутинных задач")

        if analysis["profile"].get("company") and "tech" in analysis["profile"]["company"].lower():
            recommendations.append("Внедрить agile-методологии в командную работу")

        # На основе паттернов задач
        if analysis["patterns"]["overdue_ratio"] > 0.3:
            recommendations.append("Внедрить систему приоритизации задач (Eisenhower matrix)")

        if analysis["patterns"]["delegation_ratio"] < 0.1:
            recommendations.append("Начать делегировать рутинные задачи для фокуса на стратегических")

        # На основе тем
        main_theme = analysis["patterns"]["main_themes"][0][0] if analysis["patterns"]["main_themes"] else None
        if main_theme == "development":
            recommendations.append("Внедрить code review процесс и автоматизированное тестирование")
        elif main_theme == "business":
            recommendations.append("Создать систему отслеживания метрик бизнеса и регулярные отчеты")

        analysis["recommendations"] = recommendations[:5]  # Ограничить до 5 рекомендаций

        return analysis

    finally:
        session.close()


def post_process_tool_calls(intent, tool_calls, message):
    """
    Пост-обработка tool calls для коррекции ошибок AI.
    Возвращает исправленные tool_calls или None если коррекция не нужна.
    """
    if not tool_calls:
        return None

    corrected_calls = []

    for call in tool_calls:
        function_name = call.get("function", {}).get("name", "")
        args = call.get("function", {}).get("arguments", "{}")

        try:
            args_dict = json.loads(args) if isinstance(args, str) else args
        except:
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
                "model": "deepseek-reasoner",
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
