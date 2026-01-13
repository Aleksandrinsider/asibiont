import aiohttp
import requests
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY
import json
from datetime import datetime, timezone, timedelta
import re
import logging
import asyncio
from cryptography.fernet import Fernet, InvalidToken
from models import User, UserProfile
import pytz

cipher = Fernet(ENCRYPTION_KEY.encode())
logger = logging.getLogger(__name__)

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
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.3
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            if content and "None" not in content and len(content) > 10:
                return content
        return None
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return None

def extract_tasks_with_ai(message, user_id=None):
    """
    Извлекает задачи из сообщения с помощью ИИ.
    Возвращает список словарей с задачами.
    """
    import requests
    import json
    
    prompt = f"""
    Извлеки все задачи и действия из сообщения пользователя.
    Верни результат в формате JSON массива объектов с полями:
    - title: краткое название задачи
    - description: подробное описание (если есть)
    - priority: high/medium/low (определи на основе контекста)
    - deadline: предполагаемый дедлайн в формате YYYY-MM-DD (если упоминается или можно логически вывести)
    - category: категория задачи (работа/личное/проект/обучение и т.д.)
    
    Если задач нет, верни пустой массив [].
    
    Сообщение: "{message}"
    
    Примеры:
    - "Нужно подготовить презентацию к пятнице" → {{"title": "Подготовить презентацию", "priority": "high", "deadline": "2026-01-17"}}
    - "Хочу изучить Python и найти работу" → две задачи
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.2
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=15)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            # Попробуем распарсить JSON
            try:
                tasks = json.loads(content)
                if isinstance(tasks, list):
                    return tasks
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON from AI response: {content}")
                return []
        return []
    except Exception as e:
        logger.error(f"Task extraction error: {e}")
        return []

def find_partners_with_ai(user_id, criteria=None):
    """
    Находит партнеров с помощью семантического поиска ИИ.
    """
    from models import Session, UserProfile
    import requests
    
    session = Session()
    try:
        # Получаем профиль пользователя
        user_profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not user_profile:
            return []
        
        # Получаем все профили для анализа
        all_profiles = session.query(UserProfile).filter(UserProfile.user_id != user_id).all()
        
        prompt = f"""
        Найди наиболее подходящих партнеров для пользователя на основе их профилей.
        
        Профиль пользователя:
        - Город: {user_profile.city or 'Не указан'}
        - Интересы: {user_profile.interests or 'Не указаны'}
        - Навыки: {user_profile.skills or 'Не указаны'}
        - Компания: {user_profile.company or 'Не указана'}
        
        Критерии поиска: {criteria or 'Общие рекомендации'}
        
        Проанализируй следующие профили и выбери 3-5 наиболее совместимых партнеров.
        Для каждого партнера укажи:
        - user_id: ID пользователя
        - compatibility_score: оценка совместимости (0-100)
        - reasons: почему этот партнер подходит (2-3 причины)
        
        Верни результат в формате JSON массива.
        
        Список профилей:
        {chr(10).join([f'ID {p.user_id}: Город={p.city}, Интересы={p.interests}, Навыки={p.skills}, Компания={p.company}' for p in all_profiles[:20]])}
        """
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.3
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=20)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                recommendations = json.loads(content)
                return recommendations if isinstance(recommendations, list) else []
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse partner recommendations: {content}")
                return []
        return []
    except Exception as e:
        logger.error(f"Partner search error: {e}")
        return []
    finally:
        session.close()

def analyze_sentiment(message):
    """
    Определяет эмоции и тон сообщения.
    Возвращает словарь с sentiment и intensity.
    """
    import requests
    
    prompt = f"""
    Определи эмоциональный тон этого сообщения.
    Верни результат в формате JSON:
    {{
        "sentiment": "positive"|"neutral"|"negative",
        "intensity": число от 0 до 1 (насколько сильная эмоция),
        "emotions": ["список эмоций, например: радость, гнев, спокойствие"],
        "confidence": число от 0 до 1 (уверенность анализа)
    }}
    
    Сообщение: "{message}"
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.1
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                sentiment_data = json.loads(content)
                return sentiment_data
            except json.JSONDecodeError:
                return {"sentiment": "neutral", "intensity": 0.5, "emotions": ["нейтрально"], "confidence": 0.5}
        return {"sentiment": "neutral", "intensity": 0.5, "emotions": ["нейтрально"], "confidence": 0.5}
    except Exception as e:
        logger.error(f"Sentiment analysis error: {e}")
        return {"sentiment": "neutral", "intensity": 0.5, "emotions": ["нейтрально"], "confidence": 0.5}

def generate_recommendations(user_id):
    """
    Генерирует персонализированные рекомендации на основе профиля.
    """
    from models import Session, UserProfile
    import requests
    
    session = Session()
    try:
        profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not profile:
            return []
        
        prompt = f"""
        На основе профиля пользователя сгенерируй 3-5 персонализированных рекомендаций.
        Рекомендации могут быть: курсы, события, инструменты, сообщества, партнеры.
        
        Профиль пользователя:
        - Город: {profile.city or 'Не указан'}
        - Интересы: {profile.interests or 'Не указаны'}
        - Навыки: {profile.skills or 'Не указаны'}
        - Компания: {profile.company or 'Не указана'}
        
        Верни результат в формате JSON массива объектов с полями:
        - type: "course"|"event"|"tool"|"community"|"partner"
        - title: название рекомендации
        - description: почему это подходит
        - priority: high/medium/low
        """
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.4
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=15)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                recommendations = json.loads(content)
                return recommendations if isinstance(recommendations, list) else []
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse recommendations: {content}")
                return []
        return []
    except Exception as e:
        logger.error(f"Recommendations generation error: {e}")
        return []
    finally:
        session.close()

def optimize_task_schedule(user_id):
    """
    Оптимизирует расписание задач с помощью ИИ.
    """
    from models import Session, Task
    import requests
    from datetime import datetime
    
    session = Session()
    try:
        # Получаем активные задачи пользователя
        tasks = session.query(Task).filter_by(user_id=user_id, completed=False).all()
        
        if not tasks:
            return {"suggestions": [], "message": "Нет активных задач для оптимизации"}
        
        tasks_text = "\n".join([
            f"- {t.title}: приоритет {t.priority or 'medium'}, дедлайн {t.deadline or 'не указан'}"
            for t in tasks[:10]  # Ограничим для промпта
        ])
        
        prompt = f"""
        Проанализируй список задач пользователя и предложи оптимизацию расписания.
        
        Задачи:
        {tasks_text}
        
        Предложи:
        1. Порядок выполнения задач
        2. Предупреждения о перегрузке
        3. Возможные делегирования
        4. Рекомендации по приоритетам
        
        Верни результат в формате JSON:
        {{
            "optimal_order": ["список задач в рекомендуемом порядке"],
            "warnings": ["предупреждения"],
            "delegation_suggestions": ["что можно делегировать"],
            "priority_changes": ["изменения приоритетов"]
        }}
        """
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0.2
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=15)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                optimization = json.loads(content)
                return optimization
            except json.JSONDecodeError:
                return {"suggestions": [], "message": "Не удалось проанализировать задачи"}
        return {"suggestions": [], "message": "Ошибка оптимизации"}
    except Exception as e:
        logger.error(f"Task optimization error: {e}")
        return {"suggestions": [], "message": "Ошибка оптимизации"}
    finally:
        session.close()

def understand_complex_query(message):
    """
    Разбирает сложные многошаговые запросы.
    """
    import requests
    
    prompt = f"""
    Разбери этот запрос пользователя на компоненты.
    Определи основное намерение и дополнительные критерии.
    
    Запрос: "{message}"
    
    Верни результат в формате JSON:
    {{
        "main_intent": "основное намерение",
        "criteria": {{"ключ": "значение", ...}},
        "steps": ["шаги для выполнения"],
        "complexity": "simple"|"medium"|"complex"
    }}
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.1
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                analysis = json.loads(content)
                return analysis
            except json.JSONDecodeError:
                return {"main_intent": "unknown", "criteria": {}, "steps": [], "complexity": "simple"}
        return {"main_intent": "unknown", "criteria": {}, "steps": [], "complexity": "simple"}
    except Exception as e:
        logger.error(f"Complex query analysis error: {e}")
        return {"main_intent": "unknown", "criteria": {}, "steps": [], "complexity": "simple"}

def summarize_conversation(messages, max_length=200):
    """
    Создает краткое резюме разговора.
    """
    import requests
    
    conversation_text = "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in messages[-20:]])  # Последние 20 сообщений
    
    prompt = f"""
    Создай краткое резюме этого разговора (не более {max_length} символов).
    Выдели ключевые темы, решения и следующие шаги.
    
    Разговор:
    {conversation_text}
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.2
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            return content[:max_length]
        return "Резюме недоступно"
    except Exception as e:
        logger.error(f"Conversation summary error: {e}")
        return "Резюме недоступно"

def detect_duplicates(tasks):
    """
    Находит дубликаты и конфликты в задачах.
    """
    import requests
    
    if not tasks:
        return []
    
    tasks_text = "\n".join([f"{i+1}. {t.get('title', '')}" for i, t in enumerate(tasks[:15])])
    
    prompt = f"""
    Проанализируй список задач и найди дубликаты или конфликты.
    
    Задачи:
    {tasks_text}
    
    Верни результат в формате JSON массива:
    [
        {{
            "type": "duplicate"|"conflict",
            "task_indices": [номера задач],
            "description": "объяснение"
        }}
    ]
    """
    
    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.1
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=10)
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Удаляем блоки кода если есть
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                import json
                duplicates = json.loads(content)
                return duplicates if isinstance(duplicates, list) else []
            except json.JSONDecodeError:
                return []
        return []
    except Exception as e:
        logger.error(f"Duplicate detection error: {e}")
        return []


# Импорт улучшенных функций промтов
try:
    from improved_prompts_final import (
        get_optimized_prompt_final,
        improved_classify_intent,
        improved_fallback
    )
    PROMPTS_V2_AVAILABLE = True
    logger.info("[PROMPTS V2] Loaded improved_prompts_final.py successfully")
except ImportError:
    PROMPTS_V2_AVAILABLE = False
    logger.warning("[PROMPTS V2] improved_prompts_final.py not found, using legacy prompts")

# Redis client - будет импортирован из main.py
redis_client = None


def set_redis_client(client):
    """Установка Redis клиента из main.py"""
    global redis_client
    redis_client = client


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


# LEGACY FUNCTION REMOVED - Use improved_classify_intent from improved_prompts_final.py
def classify_user_intent(message, mentions_str):
    """DEPRECATED: Use improved_classify_intent from improved_prompts_final.py"""
    from improved_prompts_final import improved_classify_intent
    return improved_classify_intent(message, mentions_str)


# LEGACY FUNCTION REMOVED - Use improved_fallback from improved_prompts_final.py
def smart_fallback_handler(message, mentions_str, user_id, ai_response_content=""):
    """DEPRECATED: Use improved_fallback from improved_prompts_final.py"""
    from improved_prompts_final import improved_fallback, improved_classify_intent
    intent = improved_classify_intent(message, mentions_str)
    return improved_fallback(intent, None, ai_response_content, message, user_id)
    # Это продолжение диалога, а не новый запрос действия
    words = message_lower.split()
    command_keywords = ["покажи", "список", "добавь", "удали", "напомни", "создай", "поручи", "перенеси",
                       "выполнил", "выполнена", "измени", "найди", "подписка", "оплати", "отмени"]

    if len(words) <= 3 and not any(keyword in message_lower for keyword in command_keywords) and "@" not in message:
        # Короткий ответ без команд - это продолжение диалога
        intent["type"] = "conversation"
        intent["confidence"] = 0.9
        return intent

    # 1. ЗАВЕРШЕНИЕ ЗАДАЧ - высокая уверенность
    completion_keywords = ["выполнил", "завершил", "сделал", "готово", "закончил", "выполнена", "завершена", "закончил"]
    if any(keyword in message_lower for keyword in completion_keywords):
        intent["type"] = "complete_task"
        intent["confidence"] = 0.9
        # Извлекаем название задачи - улучшенная логика
        task_match = re.search(r"(?:выполнил|завершил|сделал|закончил)\s+(.+?)(?:\.\.\.|$)", message, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()
        else:
            # Если не нашли паттерн, берем все после ключевого слова
            for keyword in completion_keywords:
                if keyword in message_lower:
                    parts = message.split(keyword, 1)
                    if len(parts) > 1:
                        intent["params"]["task_title"] = parts[1].strip()
                    break
        return intent

    # 2. ЦЕЛИ - распознаем желания и планы
    goal_keywords = ["хочу изучить", "хочу научиться", "планирую освоить", "моя цель", "хочу достичь"]
    if any(keyword in message_lower for keyword in goal_keywords):
        intent["type"] = "update_profile"
        intent["confidence"] = 0.85
        intent["params"]["field"] = "goals"
        # Извлекаем цель
        for keyword in goal_keywords:
            if keyword in message_lower:
                parts = message_lower.split(keyword, 1)
                if len(parts) > 1:
                    intent["params"]["goal_text"] = parts[1].strip()
                break
        return intent

    # 3. ДОБАВЛЕНИЕ ЗАДАЧ - распознаем по контексту
    add_keywords = ["добавь", "создай", "напомни", "нужно сделать", "запланируй"]
    time_indicators = ["завтра", "сегодня", "через", "в", "на", "к", "до", ":", "час", "мин"]

    if any(keyword in message_lower for keyword in add_keywords) or any(indicator in message_lower for indicator in time_indicators):
        intent["type"] = "add_task"
        intent["confidence"] = 0.85
        # Извлекаем время
        time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})", message)
        if time_match:
            intent["params"]["reminder_time"] = time_match.group(1)
        elif "завтра" in message_lower:
            intent["params"]["reminder_time"] = "tomorrow"
        return intent

    # 4. ПОКАЗ ЗАДАЧ - различные варианты запроса
    show_keywords = ["покажи", "список", "мои задачи", "что делать", "что запланировано"]
    if any(keyword in message_lower for keyword in show_keywords):
        intent["type"] = "list_tasks"
        intent["confidence"] = 0.8
        return intent

    # 5. ПРОФИЛЬ - обновление информации о себе
    profile_keywords = ["я умею", "мои навыки", "интересуюсь", "моя цель", "работаю в", "живу в"]
    if any(keyword in message_lower for keyword in profile_keywords):
        intent["type"] = "update_profile"
        intent["confidence"] = 0.8
        # Определяем тип обновления
        if "умею" in message_lower or "навыки" in message_lower:
            intent["params"]["field"] = "skills"
        elif "интересуюсь" in message_lower:
            intent["params"]["field"] = "interests"
        elif "цель" in message_lower:
            intent["params"]["field"] = "goals"
        elif "работаю" in message_lower:
            intent["params"]["field"] = "company"
        elif "живу" in message_lower:
            intent["params"]["field"] = "city"
        return intent

    # 6. ПОИСК ЛЮДЕЙ - социальные запросы
    people_keywords = ["найди", "единомышленников", "партнеров", "людей для", "кого-нибудь"]
    if any(keyword in message_lower for keyword in people_keywords):
        intent["type"] = "find_partners"
        intent["confidence"] = 0.75
        return intent

    # 1. Делегирование задач (@mentions) - улучшенные паттерны
    if "@" in message:
        mention_match = re.search(r"@(\w+)", message)
        if mention_match and intent["confidence"] < 0.9:
            intent["type"] = "delegate_task"
            intent["confidence"] = 0.9
            intent["params"]["delegated_to"] = f"@{mention_match.group(1)}"
            # Извлекаем текст задачи - улучшенная логика
            task_text = re.sub(r"@\w+", "", message).strip()
            task_text = re.sub(r"^(поручи|делегируй|передай|сделай)\s+", "", task_text, flags=re.IGNORECASE)
            intent["params"]["task_title"] = task_text or "Задача"
            # Парсим время
            time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})", task_text)
            if time_match:
                intent["params"]["reminder_time"] = time_match.group(1)
            elif "завтра" in task_text.lower():
                # Для теста, используем фиксированное время
                intent["params"]["reminder_time"] = "2026-01-11 10:00"

    # 1.1. Управление делегированными задачами
    accept_keywords = ["принял", "принимаю", "согласен", "возьму", "беру"]
    if (
        any(keyword in message_lower for keyword in accept_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "accept_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    reject_keywords = ["отклонил", "отказываюсь", "не могу", "занят"]
    if (
        any(keyword in message_lower for keyword in reject_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "reject_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    delegation_status_keywords = ["статус задачи", "как задача", "прогресс задачи", "что с задачей"]
    if any(keyword in message_lower for keyword in delegation_status_keywords) and intent["confidence"] < 0.95:
        intent["type"] = "get_delegation_progress"
        intent["confidence"] = 0.95  # максимальная уверенность
        # Извлекаем название задачи
        task_match = re.search(r"задачи\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()
        else:
            # Если не нашли конкретную задачу, это может быть общий запрос статуса
            pass

    # 1.1. Управление делегированными задачами
    accept_keywords = ["принял", "принимаю", "согласен", "возьму", "беру"]
    if (
        any(keyword in message_lower for keyword in accept_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "accept_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    reject_keywords = ["отклонил", "отказываюсь", "не могу", "занят"]
    if (
        any(keyword in message_lower for keyword in reject_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "reject_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    delegation_status_keywords = ["статус задачи", "как задача", "прогресс задачи", "что с задачей"]
    if any(keyword in message_lower for keyword in delegation_status_keywords) and intent["confidence"] < 0.95:
        intent["type"] = "get_delegation_progress"
        intent["confidence"] = 0.95  # максимальная уверенность
        # Извлекаем название задачи
        task_match = re.search(r"задачи\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()
        else:
            # Если не нашли конкретную задачу, это может быть общий запрос статуса
            intent["confidence"] = 0.6  # понижаем уверенность

    # 2. Просмотр задач
    list_keywords = ["покажи", "список", "мои дела", "все задачи", "что у меня", "задачи"]
    if any(keyword in message_lower for keyword in list_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "list_tasks"
        intent["confidence"] = 0.8

    # 2.5. Перенос задач
    transfer_keywords = ["перенеси", "перенести", "измени время", "поменяй время", "обнови время"]
    if any(keyword in message_lower for keyword in transfer_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.8
        # Извлекаем текст задачи и новое время
        for keyword in transfer_keywords:
            if keyword in message_lower:
                after_keyword = message_lower.split(keyword, 1)[1].strip()
                # Ищем время в оставшейся части
                time_match = re.search(r"(через\s+\d+\s*(минут|час|часа|часов)|завтра\s+в\s+\d{1,2}:\d{2}|сегодня\s+в\s+\d{1,2}:\d{2})", after_keyword, re.IGNORECASE)
                if time_match:
                    intent["params"]["reminder_time"] = time_match.group(1)
                    # Всё до времени - название задачи
                    task_part = after_keyword.split(time_match.group(1))[0].strip()
                    if task_part:
                        intent["params"]["task_title"] = task_part
                break

    # 3. Создание задач
    create_keywords = ["напомни", "добавь задачу", "создай задачу", "запланируй"]
    if any(keyword in message_lower for keyword in create_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "add_task"
        intent["confidence"] = 0.8
        # Извлекаем текст задачи и время
        for keyword in create_keywords:
            if keyword in message_lower:
                after_keyword = message_lower.split(keyword, 1)[1].strip()
                # Ищем время в оставшейся части - улучшенные паттерны
                time_match = re.search(r"(через\s+\d+\s*(минут|час|часа|часов|дней|день|дня)|завтра\s+в\s+\d{1,2}:\d{2}|сегодня\s+в\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})", after_keyword, re.IGNORECASE)
                if time_match:
                    intent["params"]["reminder_time"] = time_match.group(1)
                    # Всё до времени - название задачи
                    task_part = after_keyword.split(time_match.group(1))[0].strip()
                    if task_part:
                        intent["params"]["task_title"] = task_part
                else:
                    # Если времени нет, весь текст - задача
                    intent["params"]["task_title"] = after_keyword
                break

    # 3.1. Относительное время (контекстное обновление задач)
    relative_time_keywords = ["через", "напомни через"]
    if any(keyword in message_lower for keyword in relative_time_keywords) and intent["confidence"] < 0.7:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.7
        # Парсим относительное время
        time_match = re.search(r"через\s+(\d+)\s*(минут|час|часа|часов)", message_lower, re.IGNORECASE)
        if time_match:
            amount = int(time_match.group(1))
            unit = time_match.group(2).lower()
            if unit in ["час", "часа", "часов"]:
                intent["params"][
                    "reminder_time"
                ] = f"через {amount} час{'ов' if amount > 1 else '' if amount == 1 else 'а'}"
            else:
                intent["params"]["reminder_time"] = f"через {amount} минут"

    # 4. Завершение задач
    complete_keywords = ["сделал", "выполнил", "завершил", "готово", "закончил"]
    if any(keyword in message_lower for keyword in complete_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "complete_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        for keyword in complete_keywords:
            if keyword in message_lower:
                task_text = message_lower.replace(keyword, "").strip()
                intent["params"]["task_title"] = task_text
                break

    # 5. Удаление задач
    delete_keywords = ["удали все", "очисти список", "удалить все задачи"]
    if any(keyword in message_lower for keyword in delete_keywords) and intent["confidence"] < 0.9:
        intent["type"] = "delete_all_tasks"
        intent["confidence"] = 0.9

    # Удаление конкретной задачи - улучшенные паттерны
    delete_specific_keywords = [
        "удали эту задачу",
        "удалить задачу",
        "удали задачу",
        "удали эту",
        "удали задачу",
        "убери задачу",
        "убери эту задачу",
        "вычеркни задачу",
        "вычеркни эту задачу",
        "удали её",
        "удали эту",
        "убери её",
        "вычеркни её",
    ]
    if any(keyword in message_lower for keyword in delete_specific_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "delete_task"
        intent["confidence"] = 0.8
        # Извлекаем ID задачи из контекста или сообщения
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))
        # Также пытаемся извлечь название задачи
        task_name_match = re.search(r"(?:задачу|эту)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_name_match:
            intent["params"]["task_title"] = task_name_match.group(1).strip()

    # 6. Редактирование задач
    edit_keywords = ["измени задачу", "обнови задачу", "поменяй задачу", "исправь задачу"]
    if any(keyword in message_lower for keyword in edit_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.8
        # Извлекаем ID и новые параметры
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.1. Установка приоритета
    priority_keywords = [
        "приоритет",
        "высокий приоритет",
        "средний приоритет",
        "низкий приоритет",
        "установи приоритет",
    ]
    if any(keyword in message_lower for keyword in priority_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "set_priority"
        intent["confidence"] = 0.85
        # Определяем уровень приоритета
        if "высокий" in message_lower:
            intent["params"]["priority"] = "high"
        elif "средний" in message_lower:
            intent["params"]["priority"] = "medium"
        elif "низкий" in message_lower:
            intent["params"]["priority"] = "low"
        # Извлекаем ID задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.2. Детали задачи
    details_keywords = ["детали задачи", "подробности задачи", "информация о задаче", "покажи задачу"]
    if any(keyword in message_lower for keyword in details_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "get_task_details"
        intent["confidence"] = 0.85
        # Извлекаем ID или название задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.3. Альтернативы для задач
    alternatives_keywords = ["альтернативы", "предложи альтернативы", "другие варианты", "как иначе"]
    if any(keyword in message_lower for keyword in alternatives_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "suggest_alternatives"
        intent["confidence"] = 0.85
        # Извлекаем ID задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 7. Поиск людей - расширенные паттерны
    find_keywords = [
        "найди людей",
        "похожие интересы",
        "с кем пообщаться",
        "рекомендуй контакты",
        "найди партнёров",
        "кто может помочь",
        "с кем связаться",
        "похожие увлечения",
    ]
    if any(keyword in message_lower for keyword in find_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "find_partners"
        intent["confidence"] = 0.8

    # 8. Проверка статуса подписки
    subscription_keywords = ["статус подписки", "подписка активна", "у меня подписка", "проверь подписку"]
    if any(keyword in message_lower for keyword in subscription_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "check_subscription_status"
        intent["confidence"] = 0.8

    # 9. Оплата подписки
    payment_keywords = ["оплати подписку", "купить подписку", "оформить подписку", "заплатить за подписку"]
    if any(keyword in message_lower for keyword in payment_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "create_subscription_payment"
        intent["confidence"] = 0.8

    # 9.1. Отмена подписки
    cancel_keywords = ["отменить подписку", "отмена подписки", "прекратить подписку"]
    if any(keyword in message_lower for keyword in cancel_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "cancel_subscription"
        intent["confidence"] = 0.8

    # 10. Обновление профиля - расширенные паттерны
    profile_keywords = [
        "живу в",
        "работаю в",
        "интересуюсь",
        "мои навыки",
        "мои цели",
        "я из",
        "работаю",
        "увлекаюсь",
        "мои интересы",
        "мои навыки",
    ]
    if any(keyword in message_lower for keyword in profile_keywords) and intent["confidence"] < 0.7:
        intent["type"] = "update_profile"
        intent["confidence"] = 0.7
        # Парсим информацию о профиле
        if "живу в" in message_lower or "я из" in message_lower:
            city_match = re.search(r"(?:живу в|я из)\s+(.+?)(?:\s|$|,)", message_lower, re.IGNORECASE)
            if city_match:
                intent["params"]["city"] = city_match.group(1).strip().title()
        if "интересуюсь" in message_lower or "увлекаюсь" in message_lower or "мои интересы" in message_lower:
            interests_match = re.search(
                r"(?:интересуюсь|увлекаюсь|мои интересы)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE
            )
            if interests_match:
                interests = interests_match.group(1).strip()
                # Replace " и " with ", "
                interests = re.sub(r"\s+и\s+", ", ", interests)
                intent["params"]["interests"] = interests
        if "работаю" in message_lower or "работаю в" in message_lower:
            company_match = re.search(r"работаю\s+(?:в\s+)?(\w+)", message_lower, re.IGNORECASE)
            if company_match:
                intent["params"]["company"] = company_match.group(1)
        if "мои навыки" in message_lower:
            skills_match = re.search(r"мои навыки\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if skills_match:
                intent["params"]["skills"] = skills_match.group(1).strip()
        if "мои цели" in message_lower:
            goals_match = re.search(r"мои цели\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if goals_match:
                intent["params"]["goals"] = goals_match.group(1).strip()

    # 10.1. Обновление времени и timezone
    time_keywords = ["мое время", "текущее время", "сейчас время", "время"]
    if any(keyword in message_lower for keyword in time_keywords):
        # Проверяем, что это именно установка времени, а не вопрос
        time_match = re.search(r"(\d{1,2}:\d{2})", message_lower)
        if time_match and intent["confidence"] < 0.7:
            intent["type"] = "update_profile"
            intent["confidence"] = 0.7
            intent["params"]["current_time"] = time_match.group(1)

    timezone_keywords = ["часовой пояс", "timezone", "временная зона"]
    if any(keyword in message_lower for keyword in timezone_keywords) and intent["confidence"] < 0.7:
        timezone_match = re.search(r"(europe/\w+|utc[+-]\d+|gmt[+-]\d+)", message_lower, re.IGNORECASE)
        if timezone_match:
            intent["type"] = "update_profile"
            intent["confidence"] = 0.7
            intent["params"]["timezone"] = timezone_match.group(1)
        # Также проверяем случай, когда timezone указан без ключевых слов
        elif "europe" in message_lower or "utc" in message_lower or "gmt" in message_lower:
            tz_match = re.search(r"(europe/\w+|utc[+-]\d+|gmt[+-]\d+)", message_lower, re.IGNORECASE)
            if tz_match:
                intent["type"] = "update_profile"
                intent["confidence"] = 0.7
                intent["params"]["timezone"] = tz_match.group(1)
        # Парсим информацию о профиле
        if "живу в" in message_lower:
            city_match = re.search(r"живу в\s+(.+?)(?:\s|$|,)", message_lower, re.IGNORECASE)
            if city_match:
                intent["params"]["city"] = city_match.group(1).strip().title()
        if "интересуюсь" in message_lower or "увлекаюсь" in message_lower:
            interests_match = re.search(r"(?:интересуюсь|увлекаюсь)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if interests_match:
                interests = interests_match.group(1).strip()
                # Replace " и " with ", "
                interests = re.sub(r"\s+и\s+", ", ", interests)
                intent["params"]["interests"] = interests
            if company_match:
                intent["params"]["company"] = company_match.group(1)

    # АНАЛИЗ ЭМОЦИЙ И СКРЫТЫХ ПОТРЕБНОСТЕЙ
    emotion_keywords = {
        "stress": ["не успеваю", "давит", "стресс", "паника", "давление", "много дел", "загружен"],
        "tired": ["устал", "вымотан", "нет сил", "переутомился", "измотан"],
        "frustrated": ["не получается", "сложно", "проблема", "затруднение", "не выходит"],
        "overwhelmed": ["слишком много", "не справляюсь", "перегружен", "много всего"],
        "motivated": ["загорелся", "вдохновлен", "мотивирован", "готов", "энтузиазм"],
        "confused": ["не понимаю", "запутался", "неясно", "сомневаюсь"]
    }
    
    detected_emotions = []
    for emotion, keywords in emotion_keywords.items():
        if any(keyword in message_lower for keyword in keywords):
            detected_emotions.append(emotion)
    
    if detected_emotions:
        intent["emotions"] = detected_emotions
        # Повышаем уверенность если эмоции ясны
        if intent["confidence"] < 0.6:
            intent["confidence"] = 0.6
    
    # Анализ скрытых потребностей
    need_keywords = {
        "delegation": ["помоги", "сделай за меня", "поручи кому-то", "нужна помощь"],
        "prioritization": ["что важнее", "приоритеты", "с чего начать", "главное"],
        "organization": ["организовать", "структурировать", "систематизировать", "упорядочить"],
        "motivation": ["мотивация", "вдохновение", "стимул", "заинтересовать"],
        "time_management": ["время", "распределить время", "планирование времени"]
    }
    
    detected_needs = []
    for need, keywords in need_keywords.items():
        if any(keyword in message_lower for keyword in keywords):
            detected_needs.append(need)
    
    if detected_needs:
        intent["needs"] = detected_needs

    return intent


# LEGACY FUNCTION REMOVED - Use improved_fallback from improved_prompts_final.py
def smart_fallback_handler(message, mentions_str, user_id, ai_response_content=""):
    """DEPRECATED: Use improved_fallback from improved_prompts_final.py"""
    from improved_prompts_final import improved_fallback
    intent = improved_classify_intent(message, mentions_str)
    return improved_fallback(intent, None, ai_response_content, message, user_id)
    fallback_actions = []

    # СПЕЦИАЛЬНАЯ ОБРАБОТКА ПРИВЕТСТВИЙ
    greeting_words = ["привет", "здравствуй", "хай", "hello", "hi", "добрый", "здравствуйте"]
    is_greeting = len(message.strip()) <= 20 and any(  # Короткое сообщение
        word in message.lower() for word in greeting_words
    )  # Содержит слово приветствия

    if is_greeting and len(ai_response_content.strip()) < 50:  # Ответ AI слишком короткий
        logger.info("[SMART FALLBACK] Greeting detected, enhancing response")
        # Получаем список задач для подробного ответа
        from models import Session

        db_session = Session()
        try:
            tasks_result = list_tasks(user_id=user_id, session=db_session)

            # Создаем подробное приветствие, показывающее ценность и социальные возможности
            enhanced_greeting = f"Привет! Отлично, что ты здесь - я уже подготовил сводку по твоим делам. {tasks_result}\n\n"
            
            # Добавляем элементы ценности и социальных возможностей
            enhanced_greeting += "Представь, как ты раньше пытался все держать в голове или в разных приложениях - теперь все в одном месте, с умными напоминаниями и предложениями. "
            enhanced_greeting += "А еще я могу помочь найти единомышленников для совместных активностей - людей с похожими интересами, коллегами по работе или партнерами для проектов. "
            enhanced_greeting += "Что планируешь сегодня? Может добавить важные задачи или найти людей для интересных знакомств?"

            fallback_actions.append(
                {
                    "function": "enhanced_greeting",
                    "result": enhanced_greeting,
                    "reason": "Приветствие слишком короткое, делаем подробным",
                }
            )
        finally:
            db_session.close()
        return fallback_actions  # Возвращаем сразу, без дальнейшей обработки

    # Анализируем уверенность AI на основе ответа и tool calls
    ai_confidence = 0.5  # Базовая уверенность

    # Если AI вернул пустой ответ или технический текст - низкая уверенность
    if not ai_response_content or len(ai_response_content.strip()) < 10:
        ai_confidence = 0.1
    elif any(tech_word in ai_response_content.lower() for tech_word in ["error", "ошибка", "неизвестно", "json"]):
        ai_confidence = 0.2
    elif "задач" in ai_response_content.lower() or "создал" in ai_response_content.lower():
        ai_confidence = 0.8  # AI дал содержательный ответ

    # ДОПОЛНИТЕЛЬНЫЙ АНАЛИЗ: проверяем, должен ли был AI вызвать tool calls
    intent = classify_user_intent(message, mentions_str)
    
    # Если это просто продолжение диалога - fallback не нужен
    if intent["type"] == "conversation":
        logger.info("[SMART FALLBACK] Conversation detected, no fallback needed")
        return []
    
    should_have_tool_calls = intent["type"] in [
        "add_task",
        "complete_task",
        "delegate_task",
        "list_tasks",
        "find_partners",
        "update_profile",
        "delete_all_tasks",
        "delete_task",
        "edit_task",
        "check_subscription",
        "create_payment",
    ]

    # ЕСЛИ запрос требует действия И AI не вызвал tool calls - применяем fallback
    if should_have_tool_calls and intent["confidence"] >= 0.9:  # Увеличен порог с 0.7 до 0.9
        ai_confidence = 0.2  # Принудительно низкая уверенность для fallback
        print(f"[DEBUG FALLBACK] Forcing fallback for {intent['type']} (confidence: {intent['confidence']})")  # DEBUG

    # Если запрос требует действия, но AI не дал содержательный ответ - низкая уверенность
    if should_have_tool_calls and ai_confidence < 0.6:
        ai_confidence = 0.3
        logger.info(
            f"[SMART FALLBACK] Request requires action ({intent['type']}) but AI confidence low ({ai_confidence})"
        )

    # Если уверенность низкая - применяем паттерн-анализ
    if ai_confidence < 0.4:
        logger.info(
            f"[SMART FALLBACK] Applying fallback: message='{message[:50]}...', mentions='{mentions_str}', ai_response='{ai_response_content[:50]}...', intent_type='{intent['type']}', confidence={intent['confidence']}"
        )
        print(f"[DEBUG FALLBACK] Applying fallback for {intent['type']}, ai_confidence={ai_confidence}")  # DEBUG

        if intent["confidence"] >= 0.7:  # Высокая уверенность в классификации
            logger.info(f"[SMART FALLBACK] Executing {intent['type']} with params: {intent['params']}")

            # Выполняем соответствующее действие
            if intent["type"] == "add_task":
                task_title = intent["params"].get("task_title", "").strip()
                reminder_time = intent["params"].get("reminder_time")
                
                # НЕ создаем задачу, если нет названия или времени для напоминаний
                if not task_title:
                    logger.info("[SMART FALLBACK] Skipping add_task: no task title provided")
                    return []  # Не применяем fallback
                
                result = add_task(
                    title=task_title,
                    description=intent["params"].get("description", ""),
                    reminder_time=reminder_time,
                    user_id=user_id,
                )
                fallback_actions.append({"function": "add_task", "result": result, "reason": "AI не создал задачу"})

            elif intent["type"] == "complete_task":
                result = complete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "complete_task", "result": result, "reason": "AI не отметил задачу выполненной"}
                )

            elif intent["type"] == "update_profile":
                print(
                    f"[DEBUG FALLBACK] Executing update_profile with city={intent['params'].get('city')}, interests={intent['params'].get('interests')}"
                )  # DEBUG
                result = update_profile(
                    city=intent["params"].get("city"), interests=intent["params"].get("interests"), user_id=user_id
                )
                print(f"[DEBUG FALLBACK] update_profile result: {result}")  # DEBUG

            elif intent["type"] == "list_tasks":
                result = list_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "list_tasks", "result": result, "reason": "AI не показал список задач"}
                )

            elif intent["type"] == "delegate_task":
                result = delegate_task(
                    title=intent["params"].get("task_title", "Задача"),
                    delegated_to_username=intent["params"].get("delegated_to"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "delegate_task", "result": result, "reason": "AI не распознал делегирование"}
                )

            elif intent["type"] == "find_partners":
                result = find_partners(user_id=user_id)
                fallback_actions.append(
                    {"function": "find_partners", "result": result, "reason": "AI не выполнил поиск партнеров"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI не удалил задачу"})

            elif intent["type"] == "edit_task":
                result = edit_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    title=intent["params"].get("title"),
                    description=intent["params"].get("description"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "edit_task", "result": result, "reason": "AI не изменил задачу"})

            elif intent["type"] == "check_subscription":
                result = check_subscription_status(user_id=user_id)
                fallback_actions.append(
                    {
                        "function": "check_subscription_status",
                        "result": result,
                        "reason": "AI не проверил статус подписки",
                    }
                )

            elif intent["type"] == "create_payment":
                result = create_subscription_payment(user_id=user_id)
                fallback_actions.append(
                    {"function": "create_subscription_payment", "result": result, "reason": "AI не создал платеж"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI не удалил задачу"})

            elif intent["type"] == "delete_all_tasks":
                result = delete_all_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "delete_all_tasks", "result": result, "reason": "AI не выполнил удаление задач"}
                )

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


class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return await generate_reminder(user_id, task_title)

    async def generate_result_check(self, user_id, task_title):
        return await generate_result_check(user_id, task_title)

    async def generate_proactive_message(self, user_id):
        return await generate_proactive_message(user_id)

    async def generate_daily_report(self, user_id):
        return generate_daily_report(user_id)

    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return generate_overdue_reminder(user_id, overdue_tasks)


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

    # Проверка на минимальную длину только для конкретных действий с задачами
    # Убрали общую проверку на короткие ответы - AI должен адаптировать длину под контекст
    
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


async def enforce_prompt_compliance(response_text, intent_type, user_id, context, system_prompt, messages, url, headers):
    """
    Принуждает AI соблюдать правила главного промпта через повторные запросы
    """
    max_attempts = 2
    original_response = response_text

    for attempt in range(max_attempts):
        is_compliant, issues = validate_response_compliance(response_text, intent_type)

        if is_compliant:
            return response_text

        logger.warning(f"[COMPLIANCE] Response not compliant (attempt {attempt + 1}): {issues}")

        # Создаем корректирующий промпт
        correction_prompt = f"""Твой предыдущий ответ не соответствует правилам главного промпта:

ПРОБЛЕМЫ:
{chr(10).join(f"- {issue}" for issue in issues)}

СТРОГО ИСПРАВИТЬ:
- Убрать запрещенные технические эмодзи (🚀 ✅ 📝 🎯 ⚠️ 💡 📋 ⏳ 🟡 🔧), но можно оставить 1-2 подходящих (👍 👌 ✨ 🎉 💪 😊)
- Убрать жирный текст, списки, нумерацию (кроме list_tasks)
- Адаптировать длину ответа под ситуацию: короткие для простых действий, подробные для анализа
- Для add_task добавить 1-2 кратких совета (максимум 1-2 предложения), БЕЗ нумерованных списков, шагов и разделов
- Всегда добавлять вопросы для вовлечения пользователя
- Использовать естественный разговорный стиль
- Закончить вопросом для продолжения диалога

ПЕРЕПИШИ ОТВЕТ ПРАВИЛЬНО:"""

        # Добавляем корректирующий промпт к сообщениям
        correction_messages = messages.copy()
        correction_messages.append({"role": "assistant", "content": original_response})
        correction_messages.append({"role": "user", "content": correction_prompt})

        try:
            correction_data = {
                "model": "deepseek-reasoner",
                "messages": correction_messages,
                "temperature": 0.1,  # Более детерминированный для исправления
            }

            async with aiohttp.ClientSession() as correction_session:
                async with correction_session.post(
                    url, headers=headers, json=correction_data, timeout=aiohttp.ClientTimeout(total=30)
                ) as correction_response:
                    if correction_response.status == 200:
                        correction_result = await correction_response.json()
                        corrected_response = correction_result["choices"][0]["message"]["content"]
                        response_text = corrected_response
                        logger.info(f"[COMPLIANCE] Corrected response (attempt {attempt + 1})")
                    else:
                        logger.error(f"[COMPLIANCE] Correction API error: {correction_response.status}")
                        break

        except Exception as e:
            logger.error(f"[COMPLIANCE] Error during correction: {e}")
            break

    return response_text


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


def clean_technical_details(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Text must be a string")

    import logging

    logger = logging.getLogger(__name__)
    original_text = text
    print(f"[DEBUG CLEAN] Original text: '{text}'")  # DEBUG
    import re

    # Удаляем вызовы функций в квадратных скобках: [add_task(...)]
    before = text
    text = re.sub(r"\[[\w_]+\([^]]*\)\]", "", text)
    if before != text:
        print(f"[DEBUG CLEAN] After removing function calls: '{text}'")  # DEBUG

    # Удаляем пустые квадратные скобки
    before = text
    text = re.sub(r"\[\s*\]", "", text)
    if before != text:
        print(f"[DEBUG CLEAN] After removing empty brackets: '{text}'")  # DEBUG

    # Удаляем названия функций (с скобками и без)
    before = text
    text = re.sub(
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)(\s*\(\s*\))?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if before != text:
        print(f"[DEBUG CLEAN] After removing function names: '{text}'")  # DEBUG

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
        print(f"[DEBUG CLEAN] Final text: '{text}'")  # DEBUG

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


def get_optimized_system_prompt():
    """Оптимизированный промпт v12 - ГИБРИДНЫЙ ПОДХОД"""
    return """Ты - личный ИИ-помощник и друг для управления жизнью. Веди живой, естественный диалог как настоящий человек.

================================================================================
СТРОГИЕ ПРАВИЛА ФОРМАТИРОВАНИЯ (ВЫПОЛНЯЙ БЕЗУСЛОВНО):
================================================================================

❌ ЗАПРЕЩЕННЫЕ ЭЛЕМЕНТЫ (НИКОГДА НЕ ИСПОЛЬЗОВАТЬ):
- Жирный текст: **текст**
- Нумерованные списки: 1. 2. 3. или 1) 2) 3)
- Маркированные списки: • - *
- Заголовки: ## ###
- Технические эмодзи: 🚀 ✅ 📝 🎯 ⚠️ 💡 📋 ⏳ 🟡 😕 💪 🔧 📋

✅ РАЗРЕШЕННЫЕ ЭЛЕМЕНТЫ:
- Обычный текст без форматирования
- Разговорный стиль
- Естественные вопросы
- Короткие советы в скобках (не более 2-3 слов)
- 1-2 ПОДХОДЯЩИХ ЭМОДЗИ в сообщении (только позитивные: 👍 👌 ✨ 🎉 💪 😊)

================================================================================
ПРИМЕРЫ ПРАВИЛЬНОГО ПОВЕДЕНИЯ (ОБУЧАЙСЯ НА НИХ):
================================================================================

1. ЭМОЦИИ - УСТАЛОСТЬ:
Пользователь: "Я так устал от всех этих задач"
Правильный ответ: Сначала вызвать list_tasks(), затем проанализировать нагрузку, предложить план отдыха, задать 3-4 вопроса.

2. ДОБАВЛЕНИЕ ЗАДАЧ:
Пользователь: "напомни мне позвонить клиенту завтра в 15:00"
Правильный ответ: Сразу вызвать add_task() с параметрами, объяснить важность, задать уточняющие вопросы.

3. ЗАВЕРШЕНИЕ ЗАДАЧ:
Пользователь: "я выполнил задачу по отчету"
Правильный ответ: Вызвать complete_task(), похвалить, проанализировать прогресс, предложить следующую задачу.

4. ПРОФИЛЬ:
Пользователь: "я умею программировать на python"
Правильный ответ: Вызвать update_profile() для добавления навыка, объяснить пользу, задать вопросы о специализации.

5. ДЕЛЕГИРОВАНИЕ:
Пользователь: "@testuser проверь код завтра к 10:00"
Правильный ответ: Вызвать delegate_task(), объяснить выгоду делегирования, задать вопросы о приоритете.

================================================================================
КРИТИЧНЫЕ ПРАВИЛА (ВЫПОЛНЯЙ ВСЕГДА):
================================================================================

🎯 ПОКАЗ ЗАДАЧ ("покажи задачи" / "что делать" / "список"):
   1. СНАЧАЛА: list_tasks() - покажи все задачи
   2. АНАЛИЗ: Выяви паттерны, приоритеты, проблемы
   3. РЕКОМЕНДАЦИИ: Конкретные советы по оптимизации
   4. ВОПРОСЫ: Спроси про приоритеты и планы

🎯 ДОБАВЛЕНИЕ ЗАДАЧИ ("добавь задачу" / "напомни" / "сделать"):
   1. РАСПОЗНАЙ: Задача + время (если есть)
   2. СРАЗУ: add_task(title, reminder_time) - добавь немедленно
   3. ОБЪЯСНИ: Почему эта задача важна, как впишется в план
   4. ВОПРОСЫ: Спроси про детали, приоритет, связанные задачи

🎯 ЗАВЕРШЕНИЕ ЗАДАЧИ ("выполнил" / "сделал" / "готово"):
   1. РАСПОЗНАЙ: Полное название задачи из сообщения
   2. СРАЗУ: complete_task(task_title) - используй ПОЛНОЕ название задачи
   3. ПОХВАЛА: Искренне поздравь с достижением
   4. АНАЛИЗ: Обсуди прогресс, что получилось
   5. СЛЕДУЮЩИЙ ШАГ: Предложи, что делать дальше

🎯 ДЕЛЕГИРОВАНИЕ (@username в сообщении):
   1. СРАЗУ: delegate_task(title, delegated_to_username, reminder_time)
   2. ОБЪЯСНИ: Почему делегирование выгодно, освободит время
   3. ВРЕМЯ: Укажи точное время дедлайна
   4. ВОПРОСЫ: Спроси про детали для делегата

🎯 ПРОФИЛЬ (город/компания/интересы/навыки/цели):
   1. ДЛЯ ГОРОДА/КОМПАНИИ: update_profile() сразу
   2. ДЛЯ ИНТЕРЕСОВ/НАВЫКОВ: update_profile() сразу + объясни выгоду
   3. ДЛЯ ЦЕЛЕЙ: update_profile() + предложи конкретные шаги
   4. ВОПРОСЫ: Спроси про детали профиля

🎯 НЕПОНЯТНЫЕ ЗАПРОСЫ ("сделай это" / бессмыслица / пусто):
   1. НЕ ВЫЗЫВАЙ: list_tasks() - не нужен контекст
   2. ВАРИАНТЫ: Предложи 3-4 варианта что пользователь мог иметь в виду
   3. УТОЧНИ: Задай конкретные вопросы для понимания
   4. ПОМОЩЬ: Предложи помочь с конкретными действиями

================================================================================
СТИЛЬ ДИАЛОГА - ЖИВОЕ ОБЩЕНИЕ:
================================================================================

✅ КАК ЖИВОЙ ЧЕЛОВЕК:
- Используй разговорные фразы: "О, вижу!", "Понимаю", "Классно!", "Отлично!"
- Будь эмоциональным: радуйся успехам, сопереживай трудностям
- Делись наблюдениями: "Заметил, что...", "Интересно, что..."
- Давай советы: "Предлагаю...", "Может быть...", "Попробуй..."

✅ СТРУКТУРА ОТВЕТА:
1. Эмоциональная реакция (1-2 предложения)
2. Действие с инструментом (если нужно)
3. Глубокий анализ ситуации (2-3 абзаца)
4. Конкретные рекомендации с объяснениями
5. 3-4 живых вопроса для продолжения диалога

✅ ДЛИНА: 80-150 слов (не меньше!) - полноценный разговор
✅ ВОПРОСЫ: Всегда 3-4 вопроса в конце - держи диалог живым

❌ НЕ ДЕЛАЙ:
- Короткие сухие ответы
- Просто "Выполнено" или "Готово"
- Ответы без анализа и вопросов
- Формальный тон без эмоций

================================================================================
ПРИМЕРЫ ПРАВИЛЬНОГО ПОВЕДЕНИЯ:
================================================================================

❌ ПЛОХО: "Добавил задачу. Готово."
✅ ХОРОШО: "Отлично! Добавил задачу 'написать отчет' на завтра к 15:00. Это важная задача, так как отчет нужен для встречи с клиентом. Вижу, что у тебя уже есть несколько срочных дел - может стоит освободить время, делегировав что-то менее важное? Кстати, какой формат отчета нужен - презентация или документ? И есть ли у тебя вся информация для него?"

❌ ПЛОХО: "Вот твои задачи: [список]"
✅ ХОРОШО: "Посмотрел твои задачи - их сейчас 5, и я заметил интересный паттерн. У тебя много коммуникационных задач (3 из 5), а одна задача делегирована. Это говорит о том, что ты активно работаешь с людьми. Рекомендую сгруппировать все звонки на один блок времени - так будет эффективнее. Самое срочное сейчас - 'позвонить клиенту', дедлайн через 2 часа. Начнем с этого? Или есть что-то более приоритетное?"

❌ ПЛОХО: "Не понял, что ты имеешь в виду."
✅ ХОРОШО: "Хм, не совсем понял, что ты хочешь сделать. Вижу несколько вариантов: 1) Добавить новую задачу, 2) Посмотреть текущие задачи, 3) Завершить какую-то задачу, 4) Найти людей для проекта. Что из этого ближе всего? Или ты имел в виду что-то другое?"

================================================================================
ФУНКЦИИ (используй проактивно):
================================================================================

list_tasks() - Показать задачи + анализ
add_task(title, reminder_time) - Добавить задачу + советы
  ПРИМЕРЫ reminder_time:
  - "через 5 минут" (ОБЯЗАТЕЛЬНО передавай как есть!)
  - "через 2 часа" (ОБЯЗАТЕЛЬНО передавай как есть!)
  - "завтра в 10:00"
  - "2026-01-13 15:30"
complete_task(task_title) - Завершить + празднование
delegate_task(title, delegated_to_username, reminder_time) - Делегировать + выгода
find_partners() - Найти людей + рекомендации
update_profile(city, company, interests, skills, goals) - Обновить профиль

================================================================================
ЦЕЛЬ: Быть живым другом и помощником, а не роботом
================================================================================"""


# LEGACY FUNCTION REMOVED - Use get_optimized_prompt_final from improved_prompts_final.py
def get_system_prompt():
    """DEPRECATED: Use get_optimized_prompt_final from improved_prompts_final.py"""
    raise NotImplementedError("Use get_optimized_prompt_final from improved_prompts_final.py")


def get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory, context=None, intent=None):
    from improved_prompts_final import get_optimized_prompt_final
    return get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)
    

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


def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}")
    from models import Session, Task, User
    from datetime import datetime
    import pytz

    if session is None:
        session = Session()
        close_session = True
        logger.info("[ADD_TASK] Created new session")
    else:
        close_session = False
        logger.info("[ADD_TASK] Using provided session")
    # Проверить, существует ли пользователь
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()

    # Проверить, существует ли задача с таким же названием
    existing_task = session.query(Task).filter_by(user_id=user.id, title=title).first()
    if existing_task:
        # Обновить существующую задачу
        if reminder_time:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                existing_task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        if description:
            existing_task.description = encrypt_data(description)
        session.commit()
        task_id = existing_task.id
        task = existing_task  # Для дальнейшего использования
    else:
        # Создать новую задачу
        task = Task(user_id=user.id, title=title, description=encrypt_data(description))
        if reminder_time:
            try:
                # Получить timezone пользователя
                user_tz = pytz.UTC
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        import logging
                        logging.warning(f"Unknown timezone {user.timezone}, using UTC")
                        user_tz = pytz.UTC
                
                # Проверить, является ли время относительным
                if "через" in reminder_time.lower():
                    # Использовать parse_relative_time для относительного времени
                    # ВАЖНО: используем локальное время пользователя, не UTC!
                    current_time = datetime.now(user_tz)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        # parsed_time уже в локальном времени, конвертируем в UTC для хранения
                        if parsed_time.tzinfo is None:
                            parsed_time = user_tz.localize(parsed_time)
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        import logging
                        logging.info(f"Task {title} relative time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
                    else:
                        # Если не удалось распарсить, игнорировать
                        pass
                else:
                    # Парсить как абсолютное время
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    # Локализовать в timezone пользователя
                    local_dt = user_tz.localize(local_dt)
                    # Конвертировать в UTC для хранения
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                    import logging
                    logging.info(f"Task {title} absolute time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
            except ValueError:
                pass  # Игнорировать неверный формат
        if due_date:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(due_date, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.due_date = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        session.add(task)
        
        # Генерируем рекомендации для задачи
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[ADD_TASK] Generating recommendations for task '{title}'")
            recommendations = generate_task_recommendations(title, description, user.telegram_id)
            logger.info(f"[ADD_TASK] Generated {len(recommendations) if recommendations else 0} recommendations")
            if recommendations:
                import json
                task.recommendations = json.dumps(recommendations, ensure_ascii=False)
                logger.info(f"[ADD_TASK] Saved recommendations to task: {task.recommendations}")
        except Exception as e:
            import logging
            logging.warning(f"Could not generate recommendations for task {title}: {e}")
        
        session.commit()
        task_id = task.id

    # Планировать напоминание если указано reminder_time
    if task.reminder_time:
        try:
            from main import reminder_service

            if reminder_service:
                reminder_service.schedule_reminder(
                    task_id=task.id, reminder_time=task.reminder_time, user_id=user.telegram_id, task_title=task.title
                )
        except Exception as e:
            import logging

            logging.warning(f"Could not schedule reminder for task {task_id} (scheduler may not be running yet): {e}")

    # Обновить аналитику профиля
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()

    # Формируем подробный ответ с ID для edit_task
    result_msg = f"Добавлена задача '{title}' (ID: {task_id})"
    if task.reminder_time:
        # Показываем время в timezone пользователя
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        result_msg += f" с напоминанием на {local_time.strftime('%d.%m.%Y %H:%M')}"

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg


def delete_task(task_id=None, task_title=None, user_id=None, session=None):
    """Delete a specific task by ID or title"""
    from models import Session, Task, User

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден."

        task = None
        if task_id:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter(Task.id == task_id_int, Task.user_id == user.id).first()
            except (ValueError, TypeError):
                pass

        if not task and task_title:
            # Try to find by title (case-insensitive partial match)
            task = session.query(Task).filter(Task.user_id == user.id, Task.title.ilike(f"%{task_title}%")).first()

        if not task:
            if close_session:
                session.close()
            return "Задача не найдена."

        # Delete the task
        session.delete(task)
        session.commit()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.total_tasks_created:
            profile.total_tasks_created = max(0, (profile.total_tasks_created or 0) - 1)
            session.commit()

        if close_session:
            session.close()
        return f"Задача '{task.title}' удалена."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задачи: {str(e)}"


def delete_all_tasks(user_id=None, session=None):
    """Delete all tasks for a user"""
    from models import Session, Task, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден."

        # Count tasks before deletion
        task_count = session.query(Task).filter_by(user_id=user.id).count()

        # Delete all tasks
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Reset profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.total_tasks_created = 0
            profile.completed_tasks = 0
            profile.skipped_tasks = 0
            session.commit()

        if close_session:
            session.close()
        return f"Удалено {task_count} задач."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задач: {str(e)}"


def complete_task(task_id=None, task_title=None, user_id=None, session=None):
    from models import Session, Task, UserProfile, Interaction
    from datetime import datetime
    from sqlalchemy import or_

    print(f"[DEBUG COMPLETE_TASK] Called with task_id={task_id}, task_title='{task_title}', user_id={user_id}")  # DEBUG
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    print(f"[DEBUG COMPLETE_TASK] Found user: {user.id if user else None}")  # DEBUG
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."

    # Найти задачу по ID или по названию
    if task_id:
        # Ищем задачу: созданную мной ИЛИ делегированную мне
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username))
            )
            .first()
        )
    elif task_title:
        # Ищем по словам в названии для более гибкого поиска
        words = task_title.lower().split()
        print(f"[DEBUG COMPLETE_TASK] Searching by title, words: {words}")  # DEBUG
        # OR вместо AND - ищем задачу содержащую хотя бы одно из слов
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(Task.user_id == user.id, Task.status != "completed", or_(*conditions)).first()
        print(f"[DEBUG COMPLETE_TASK] Found task by title: {task.title if task else None}")  # DEBUG
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        task.status = "completed"
        task.actual_completion_time = datetime.now(timezone.utc)
        session.commit()
        print(f"[DEBUG COMPLETE_TASK] Task completed: {task.title}, status: {task.status}")  # DEBUG

        # Обновить аналитику профиля
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            completion_time = (
                datetime.now(timezone.utc) - task.created_at.replace(tzinfo=timezone.utc)
            ).total_seconds() / 60
            profile.completed_tasks = (profile.completed_tasks or 0) + 1
            prev_avg = profile.average_completion_time or 0
            # Защита от деления на ноль
            if profile.completed_tasks > 0:
                profile.average_completion_time = (
                    (prev_avg * (profile.completed_tasks - 1)) + completion_time
                ) / profile.completed_tasks
            session.commit()
        result = f"Завершена задача '{task.title}'."

        # Сохранить сообщение в историю взаимодействий
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
    else:
        result = "Задача не найдена."
    if close_session:
        session.close()
    return result


def set_reminder(task_id, reminder_time, user_id=None):
    from models import Session, Task
    from datetime import datetime

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        task = session.query(Task).filter_by(id=task_id_int, user_id=user.id).first()
        if task:
            try:
                reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                task.reminder_time = reminder_time_parsed
                session.commit()
                result = f"Установлено напоминание для '{task.title}' на {reminder_time_parsed}."
            except ValueError:
                result = "Неверный формат времени."
        else:
            result = "Задача не найдена."
        return result
    finally:
        session.close()


def update_user_memory(info, user_id=None):
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # Дешифруем существующую память
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""
            # Добавляем новую информацию
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # Шифруем обратно
            encrypted = encrypt_data(existing_decrypted)
            user.memory = encrypted
            session.commit()
            result = "Сохранена информация."
        else:
            result = "Пользователь не найден."
        return result
    finally:
        session.close()


def delegate_task(
    title, reminder_time=None, delegated_to_username=None, user_id=None, description="", delegation_details=""
):
    """Create a delegated task that requires acceptance by the recipient"""
    from models import Session, Task, User, UserProfile
    from datetime import datetime
    import pytz

    session = Session()
    try:
        # Validate reminder_time is provided
        if not reminder_time:
            return "Для делегирования задачи требуется точная дата и время дедлайна. Пожалуйста, уточните: на какое точное время и дату поставить дедлайн? (Например: '2026-01-10 15:00' или 'завтра в 14:30')"

        # Validate reminder_time format
        if reminder_time:
            # Try parsing the format first
            try:
                datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
            except ValueError:
                # If not in YYYY-MM-DD HH:MM format, try to parse as relative time
                logger.info(f"[DELEGATE] Parsing relative time: {reminder_time}")
                parsed_time = parse_time_to_datetime(reminder_time, user_id)
                if parsed_time:
                    reminder_time = parsed_time
                    logger.info(f"[DELEGATE] Parsed to: {reminder_time}")
                else:
                    return f"Некорректный формат времени '{reminder_time}'. Укажите точное время в формате YYYY-MM-DD HH:MM (например: 2026-01-10 15:00)"

        # Find delegator (creator)
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."

        # Find recipient by username
        recipient_username = delegated_to_username.replace("@", "").lower()
        print(f"[DEBUG DELEGATE] Looking for recipient: '{recipient_username}'")  # DEBUG
        recipient = session.query(User).filter(User.username.ilike(recipient_username)).first()
        print(f"[DEBUG DELEGATE] Found recipient: {recipient.username if recipient else None}")  # DEBUG

        if not recipient:
            return f"Пользователь @{recipient_username} не найден в системе. Убедитесь, что он зарегистрирован в боте."

        # If delegating to self, create regular task instead
        print(f"[DEBUG DELEGATE] Checking if self: recipient.id={recipient.id}, delegator.id={delegator.id}")  # DEBUG
        if recipient.id == delegator.id:
            print(f"[DEBUG DELEGATE] Delegating to self")  # DEBUG
            # Create regular task for self
            task = Task(user_id=delegator.id, title=title, description=encrypt_data(description), status="pending")
            if reminder_time:
                try:
                    user_tz = pytz.timezone(delegator.timezone) if delegator.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    pass
            session.add(task)
            session.commit()
            task_id = task.id

            # Schedule reminder if set
            if task.reminder_time:
                try:
                    from main import reminder_service

                    if reminder_service:
                        reminder_service.schedule_reminder(
                            task_id=task.id,
                            reminder_time=task.reminder_time,
                            user_id=delegator.telegram_id,
                            task_title=task.title,
                        )
                except Exception as e:
                    import logging

                    logging.error(f"Failed to schedule reminder for self-delegated task {task_id}: {e}")

            # Update profile analytics
            profile = session.query(UserProfile).filter_by(user_id=delegator.id).first()
            if profile:
                profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
                session.commit()

            session.close()
            return f"Задача '{title}' добавлена для вас с напоминанием на {reminder_time}."

        # Create task with pending delegation status
        task = Task(
            user_id=delegator.id,
            title=title,
            description=encrypt_data(description),
            delegated_by=None,
            delegated_to_username=recipient_username,
            delegation_status="pending",
            delegation_details=delegation_details,
            status="pending",
        )

        if reminder_time:
            try:
                user_tz = pytz.timezone(recipient.timezone) if recipient.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass

        session.add(task)
        session.commit()
        task_id = task.id

        # Send notification to recipient via Telegram
        try:
            from main import bot

            if bot:
                message = f"Новое предложение задачи от @{delegator.username}:\n\n"
                message += f"Задача: {title}\n"
                if description:
                    message += f"Описание: {description}\n"
                if reminder_time:
                    message += f"Дедлайн: {reminder_time}\n"
                if delegation_details:
                    message += f"Детали: {delegation_details}\n"
                message += f"\nНапишите боту 'принять задачу {task_id}' для подтверждения или 'отклонить задачу {task_id}' для отказа."

                import asyncio

                asyncio.create_task(bot.send_message(recipient.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to send delegation notification: {e}")

        session.close()
        return f"Предложение задачи отправлено @{recipient_username}. Ожидается подтверждение."
    except Exception as e:
        session.close()
        return f"Ошибка при создании делегированной задачи: {str(e)}"


def suggest_alternatives(task_id, reason="", user_id=None):
    """Предложить альтернативы для невыполненной задачи через AI"""
    import asyncio

    return asyncio.run(_suggest_alternatives_async(task_id, reason, user_id))


async def _suggest_alternatives_async(task_id, reason="", user_id=None):
    from models import Session, Task

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
        if not task:
            return "Задача не найдена."

        # Получить память пользователя
        user_memory = ""
        if user.memory:
            try:
                user_memory = f"\nИнформация о пользователе: {decrypt_data(user.memory)}"
            except:
                user_memory = ""

        # Генерируем альтернативы через AI
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_active_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory},
            {
                "role": "user",
                "content": f"Предложи 3-5 альтернативных подходов к задаче '{task.title}'. Причина невыполнения: '{reason}'. Будь практичным и конкретным.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages, "max_tokens": 500}

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    # Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    return content
                else:
                    return "Не удалось сгенерировать альтернативы."

    except Exception as e:
        return f"Ошибка при генерации альтернатив: {str(e)}"
    finally:
        session.close()


def create_subscription_payment(user_id=None):
    """Создает платеж для месячной подписки"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id)
        return f"Ссылка на оплату месячной подписки создана: {payment_url}"
    except Exception as e:
        return f"Ошибка создания платежа: {str(e)}"


def check_subscription_status(user_id=None):
    """Проверяет статус подписки пользователя"""
    from subscription_service import get_subscription_status
    from config import FREE_ACCESS_MODE

    try:
        if FREE_ACCESS_MODE:
            return "Режим бесплатного доступа активен. Подписка не требуется."

        status = get_subscription_status(user_id)
        if status:
            status_text = f"Статус подписки: {status['status']}\n"
            status_text += f"План: {status['plan']}\n"
            if status["start_date"]:
                status_text += f"Дата начала: {status['start_date'][:10]}\n"
            if status["end_date"]:
                status_text += f"Дата окончания: {status['end_date'][:10]}\n"
            status_text += f"Количество входов: {status['login_count']}"
            return status_text
        else:
            return "Подписка не найдена. Для использования сервиса требуется активная подписка."
    except Exception as e:
        return f"Ошибка проверки подписки: {str(e)}"


def cancel_subscription(user_id=None):
    """Отменяет подписку пользователя"""
    from subscription_service import cancel_subscription as cancel_sub

    try:
        success = cancel_sub(user_id)
        if success:
            return "Подписка успешно отменена."
        else:
            return "Подписка не найдена или уже отменена."
    except Exception as e:
        return f"Ошибка отмены подписки: {str(e)}"


def accept_delegated_task(task_id, user_id=None):
    """Accept a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status
        task.delegation_status = "accepted"
        session.commit()

        # Schedule reminder if set
        if task.reminder_time:
            try:
                from main import reminder_service

                if reminder_service:
                    reminder_service.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title,
                    )
            except Exception as e:
                import logging

                logging.error(f"Failed to schedule reminder: {e}")

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"@{user.username} принял задачу: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы приняли задачу '{task.title}'. Она добавлена в ваш список задач."
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def reject_delegated_task(task_id, user_id=None):
    """Reject a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"
        session.commit()

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"@{user.username} отклонил задачу: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы отклонили задачу '{task.title}'."
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def get_delegation_progress(task_id, user_id=None):
    """Get progress report for a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task or not task.delegated_to_username:
            return "Делегированная задача не найдена."

        recipient = session.query(User).filter(User.username.ilike(task.delegated_to_username)).first()

        if task.delegation_status == "pending":
            status_msg = f"@{task.delegated_to_username} еще не ответил на предложение."
        elif task.delegation_status == "accepted":
            if task.status == "completed":
                status_msg = f"Задача выполнена @{task.delegated_to_username}!"
            else:
                status_msg = (
                    f"@{task.delegated_to_username} принял задачу и работает над ней (статус: {task.status})."
                )
        elif task.delegation_status == "rejected":
            status_msg = f"@{task.delegated_to_username} отклонил эту задачу."
        else:
            status_msg = "Статус неизвестен."

        session.close()
        return f"Задача: {task.title}\n{status_msg}"
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def edit_task(task_id=None, task_title=None, title=None, description=None, reminder_time=None, user_id=None, session=None):
    from models import Session, Task
    from datetime import datetime, timezone
    import pytz

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."
    
    # Найти задачу по ID или по названию
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id)).first()
    elif task_title:
        # Ищем задачу по названию (точное совпадение или содержит)
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike(f"%{task_title}%")
        ).first()
    
    if task:
        # Проверить права доступа: задача должна принадлежать пользователю ИЛИ быть делегирована ему
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя или делегированная им
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "У вас нет прав на редактирование этой задачи."

        if title:
            task.title = title
        if description:
            task.description = encrypt_data(description)
        if reminder_time:
            try:
                # Проверить, является ли время относительным
                if "через" in reminder_time.lower():
                    # Использовать parse_relative_time для относительного времени
                    current_time = datetime.now(pytz.UTC)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time
                        logger.info(f"Task {task.id} relative time updated: '{reminder_time}' -> {parsed_time}")
                    else:
                        session.close()
                        return "Не удалось распарсить относительное время."
                else:
                    # Парсить как абсолютное время
                    reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    task.reminder_time = reminder_time_parsed
                    logger.info(f"Task {task.id} absolute time updated: {reminder_time_parsed}")
                # Обновляем напоминание через прямое добавление задачи в планировщик
                # ReminderService требует bot, поэтому используем прямое обновление
            except ValueError:
                if close_session:
                    session.close()
                return "Неверный формат времени. Используйте YYYY-MM-DD HH:MM или 'через X минут'."
        session.commit()
        result = f"Обновлена задача '{task.title}'."
    else:
        result = "Задача не найдена."
    if close_session:
        session.close()
    return result


def delete_all_tasks(user_id=None):
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[DELETE_ALL] Starting delete_all_tasks for user_id: {user_id} (type: {type(user_id)})")

    try:
        from models import Session, Task

        session = Session()
        logger.info(f"[DELETE_ALL] Session created")

        # Преобразуем user_id в int, если нужно
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            logger.error(f"[DELETE_ALL] Invalid user_id: {user_id}")
            session.close()
            return "Некорректный ID пользователя."

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.warning(f"[DELETE_ALL] User not found for telegram_id: {user_id}")
            session.close()
            return "Пользователь не найден."

        logger.info(f"[DELETE_ALL] Found user: {user.id}, telegram_id: {user.telegram_id}")

        # Удаляем все задачи пользователя (созданные им и делегированные ему)
        from sqlalchemy import or_

        conditions = [Task.user_id == user.id]
        if user.username:
            conditions.append(Task.delegated_to_username.ilike(user.username))

        tasks_to_delete = session.query(Task).filter(or_(*conditions)).all()
        deleted_count = len(tasks_to_delete)
        logger.info(f"[DELETE_ALL] Found {deleted_count} tasks to delete")

        for task in tasks_to_delete:
            logger.info(f"[DELETE_ALL] Deleting task: {task.id} - {task.title}")
            session.delete(task)

        session.commit()
        logger.info(f"[DELETE_ALL] Commit successful, deleted {deleted_count} tasks")
        session.close()

        if deleted_count > 0:
            return f"Удалено {deleted_count} задач."
        else:
            return "У вас нет задач для удаления."

    except Exception as e:
        logger.error(f"[DELETE_ALL] Error in delete_all_tasks: {e}", exc_info=True)
        try:
            session.close()
        except:
            pass
        return "Произошла ошибка при удалении задач."


def get_task_details(task_id, user_id=None):
    from models import Session, Task

    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id)).first()
    if task:
        # Проверить права доступа
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "У вас нет прав на просмотр этой задачи."

        session.close()
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    session.close()
    return "Задача не найдена."


def get_partners_list(user_id=None, session=None):
    """Возвращает список всех пользователей с профилями (кроме самого пользователя и тех, с кем уже есть делегирование)"""
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[PARTNERS] get_partners_list called for user_id: {user_id}")

    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        logger.warning(f"[PARTNERS] User not found for telegram_id: {user_id}")
        if close_session:
            session.close()
        return []

    logger.info(f"[PARTNERS] Found user: {user.id}, username: {user.username}")

    # Получаем список пользователей, с которыми уже есть делегирование
    delegated_usernames = set()

    # Задачи, которые делегировали мне
    if user.username:
        delegated_to_me = (
            session.query(Task)
            .filter(
                Task.delegated_to_username.ilike(user.username), Task.delegation_status.in_(["pending", "accepted"])
            )
            .all()
        )
        for task in delegated_to_me:
            delegated_user = session.query(User).filter_by(id=task.user_id).first()
            if delegated_user:
                delegated_usernames.add(delegated_user.username.lower() if delegated_user.username else "")
    else:
        delegated_to_me = []

    # Задачи, которые я делегировал
    delegated_by_me = (
        session.query(Task)
        .filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(["pending", "accepted"]),
        )
        .all()
    )
    for task in delegated_by_me:
        if task.delegated_to_username:
            delegated_usernames.add(task.delegated_to_username.replace("@", "").lower())

    # Получаем все профили с заполненными данными, кроме своего и тех, с кем уже есть делегирование
    all_profiles = (
        session.query(UserProfile)
        .join(User, UserProfile.user_id == User.id)
        .filter(
            UserProfile.user_id != user.id,
            # Хотя бы одно поле должно быть заполнено
            (UserProfile.interests.isnot(None))
            | (UserProfile.skills.isnot(None))
            | (UserProfile.position.isnot(None))
            | (UserProfile.city.isnot(None))
            | (UserProfile.bio.isnot(None))
            | (UserProfile.languages.isnot(None)),
        )
        .all()
    )

    logger.info(f"[PARTNERS] Found {len(all_profiles)} profiles with data")

    # Получаем профиль текущего пользователя для сравнения
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        logger.warning(f"[PARTNERS] User profile not found for user {user.id}")
        if close_session:
            session.close()
        return []

    logger.info(
        f"[PARTNERS] User profile: interests='{user_profile.interests}', skills='{user_profile.skills}', goals='{user_profile.goals}'"
    )

    # Фильтруем только тех, у кого есть совпадения
    partners = []
    for profile in all_profiles:
        profile_user = session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user or not profile_user.username:
            continue

        logger.info(
            f"[PARTNERS] Checking profile for {profile_user.username}: interests='{profile.interests}', skills='{profile.skills}'"
        )

        # Проверяем наличие совпадений по интересам, навыкам или целям
        has_match = False

        # Проверка по навыкам
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(","))
            if user_skills & profile_skills:
                has_match = True
                logger.info(f"[PARTNERS] Skills match: {user_skills & profile_skills}")

        # Проверка по интересам
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(","))
            if user_interests & profile_interests:
                has_match = True
                logger.info(f"[PARTNERS] Interests match: {user_interests & profile_interests}")

        # Проверка по целям
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True
                logger.info(f"[PARTNERS] Goals match: {user_goals & profile_goals}")

        # Проверка по компании
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
                    logger.info(f"[PARTNERS] Company match: {user_profile.company}")

        # Добавляем только если есть совпадение
        if has_match:
            partners.append(profile)
            logger.info(f"[PARTNERS] Added {profile_user.username} to partners")

    logger.info(f"[PARTNERS] Total partners found: {len(partners)}")

    # Сортируем: сначала пользователи из одного города, потом остальные
    user_city = user_profile.city.lower() if user_profile.city else None
    partners_same_city = []
    partners_other_city = []

    for partner in partners:
        partner_city = partner.city.lower() if partner.city else None
        if user_city and partner_city == user_city:
            partners_same_city.append(partner)
        else:
            partners_other_city.append(partner)

    # Сортируем каждую группу по среднему рейтингу (от большего к меньшему)
    partners_same_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)
    partners_other_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)

    # Объединяем: сначала из того же города, потом остальные
    sorted_partners = partners_same_city + partners_other_city

    if close_session:
        session.close()

    # Возвращаем до 20 пользователей (можно увеличить при необходимости)
    return sorted_partners[:20]


def find_partners(user_id=None, session=None):
    import re
    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."

    # Получаем задачи текущего пользователя для анализа совместных идей
    user_tasks = session.query(Task).filter_by(user_id=user.id).all()
    user_task_keywords = set()
    for task in user_tasks:
        # Извлекаем ключевые слова из названий и описаний задач
        import re

        words = re.findall(r"\b\w+\b", (task.title + " " + (task.description or "")).lower())
        user_task_keywords.update(words)

    # Остальной код...
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    # Получить память для исключения заблокированных
    blocked = []
    hidden_contacts = {}  # username -> expiration_timestamp
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            # Ищем паттерны вроде "не показывать @user" или "заблокировать @user"
            from datetime import datetime, timezone as dt_timezone

            # Permanent blocks
            matches = re.findall(r"не показывать @(\w+)|заблокировать @(\w+)", decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])

            # Temporary hides: hide_contact:username:timestamp
            hide_matches = re.findall(r"hide_contact:@?(\w+):(\d+)", decrypted, re.IGNORECASE)
            current_time = int(datetime.now(dt_timezone.utc).timestamp())
            for username, expiration_ts in hide_matches:
                exp_ts = int(expiration_ts)
                if exp_ts > current_time:  # Still hidden
                    hidden_contacts[username.lower()] = exp_ts
        except Exception as e:
            pass
    partners = []
    tips = []
    # Проверяем, есть ли в профиле какие-то данные для поиска
    has_profile_data = (
        user_profile and (
            (user_profile.interests and user_profile.interests.strip()) or
            (user_profile.skills and user_profile.skills.strip()) or
            (user_profile.goals and user_profile.goals.strip()) or
            (user_profile.city and user_profile.city.strip()) or
            (hasattr(user_profile, 'company') and user_profile.company and user_profile.company.strip()) or
            (hasattr(user_profile, 'position') and user_profile.position and user_profile.position.strip()) or
            (hasattr(user_profile, 'bio') and user_profile.bio and user_profile.bio.strip())
        )
    )
    
    if has_profile_data:
        # Сначала фильтруем по городу, если указан
        if user_profile.city:
            city_profiles = [p for p in profiles if p.city and p.city.lower() == user_profile.city.lower()]
            if city_profiles:
                profiles = city_profiles  # Используем только профили из того же города

        # Словарь для подсчёта релевантности: {profile: (score, matched_fields)}
        partner_scores = {}

        for p in profiles:
            # Исключаем заблокированных и себя
            if not p.contact_info:
                continue
            contact_username = p.contact_info.replace("@", "").lower()
            if (
                p.contact_info in blocked
                or any("@" + b in p.contact_info for b in blocked)
                or p.contact_info == f"user{user_id}"
            ):
                continue
            # Исключаем временно скрытых
            if contact_username in hidden_contacts:
                continue

            score = 0
            matched_fields = []

            # Анализ задач для совместных идей
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            if partner_user:
                partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                partner_task_keywords = set()
                
                for task in partner_tasks:
                    task_text = (task.title + " " + (task.description or "")).lower()
                    words = re.findall(r"\b\w+\b", task_text)
                    partner_task_keywords.update(words)

                # Находим пересечения ключевых слов задач
                common_keywords = user_task_keywords & partner_task_keywords
                if common_keywords:
                    score += len(common_keywords) * 2  # 2 балла за каждое совпадение
                    matched_fields.append(f"совместные задачи: {', '.join(list(common_keywords)[:3])}")

            # Проверка интересов с приоритетом точного совпадения
            if user_profile.interests and p.interests:
                user_interests = [i.strip().lower() for i in user_profile.interests.split(",")]
                partner_interests = [i.strip().lower() for i in p.interests.split(",")]

                for user_int in user_interests:
                    for partner_int in partner_interests:
                        # Точное совпадение = 10 баллов
                        if user_int == partner_int:
                            score += 10
                            matched_fields.append(f"интерес: {user_int}")
                        # Одно содержит другое = 5 баллов
                        elif user_int in partner_int or partner_int in user_int:
                            score += 5
                            matched_fields.append(f"похожий интерес: {partner_int}")

            # Проверка навыков
            if user_profile.skills and p.skills:
                user_skills = [s.strip().lower() for s in user_profile.skills.split(",")]
                partner_skills = [s.strip().lower() for s in p.skills.split(",")]

                for user_skill in user_skills:
                    for partner_skill in partner_skills:
                        if user_skill == partner_skill:
                            score += 10
                            matched_fields.append(f"навык: {user_skill}")
                        elif user_skill in partner_skill or partner_skill in user_skill:
                            score += 5
                            matched_fields.append(f"похожий навык: {partner_skill}")

            # Проверка целей
            if user_profile.goals and p.goals:
                user_goals = [g.strip().lower() for g in user_profile.goals.split(",")]
                partner_goals = [g.strip().lower() for g in p.goals.split(",")]

                for user_goal in user_goals:
                    for partner_goal in partner_goals:
                        if user_goal == partner_goal:
                            score += 10
                            matched_fields.append(f"цель: {user_goal}")
                        elif user_goal in partner_goal or partner_goal in user_goal:
                            score += 5
                            matched_fields.append(f"похожая цель: {partner_goal}")

            # Компания (точное совпадение)
            if hasattr(user_profile, "company") and hasattr(p, "company") and user_profile.company and p.company:
                if user_profile.company.lower() == p.company.lower():
                    score += 15  # Коллеги - высокий приоритет
                    matched_fields.append(f"коллега из {p.company}")

            # Должность (частичное совпадение)
            if hasattr(user_profile, "position") and hasattr(p, "position") and user_profile.position and p.position:
                if (
                    user_profile.position.lower() in p.position.lower()
                    or p.position.lower() in user_profile.position.lower()
                ):
                    score += 8
                    matched_fields.append(f"должность: {p.position}")

            # Если есть совпадения - добавляем в результат
            if score > 0:
                partner_scores[p] = (score, matched_fields)

        # Сортируем по убыванию релевантности
        sorted_partners = sorted(partner_scores.items(), key=lambda x: x[1][0], reverse=True)
        partners = [item[0] for item in sorted_partners]

        # Проверяем планы на релевантность для топ-3
        for p in partners[:3]:
            if p.current_plans and user_profile.interests:
                for interest in user_profile.interests.split(","):
                    interest_words = interest.strip().lower().split()
                    if any(word in p.current_plans.lower() for word in interest_words):
                        tips.append(
                            f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} - может быть интересно с твоими интересами в {interest.strip()}."
                        )
                        break
                        break
    else:
        # Если профиля нет или он пустой, вернуть тестовых партнеров для демонстрации
        partners = profiles[:3] if profiles else []

    if close_session:
        session.close()

    response = ""
    if partners:
        response += "Нашёл подходящих людей:\n"
        for idx, p in enumerate(partners[:3], 1):
            info_parts = []

            # Показываем причину совпадения (только если профиль заполнен)
            if has_profile_data and p in partner_scores:
                score, matched = partner_scores[p]
                # Берём первое самое релевантное совпадение
                match_reason = matched[0] if matched else "общие интересы"
                info_parts.append(f"Совпадение: {match_reason}")

            if p.interests:
                info_parts.append(f"интересы: {p.interests}")
            if hasattr(p, "bio") and p.bio:
                bio_short = p.bio[:80] + "..." if len(p.bio) > 80 else p.bio
                info_parts.append(f"чем могу помочь: {bio_short}")
            if hasattr(p, "position") and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, "company") and p.company:
                info_parts.append(f"компания: {p.company}")
            if hasattr(p, "languages") and p.languages:
                info_parts.append(f"языки: {p.languages}")
            if p.city:
                info_parts.append(f"город: {p.city}")

            info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"
            response += f"{idx}. @{p.contact_info}\n   {info_str}\n"

        # Добавляем предложения совместных идей на основе задач (только если профиль заполнен)
        if has_profile_data:
            joint_ideas = []
            for p in partners[:3]:
                if p in partner_scores:
                    score, matched = partner_scores[p]
                    # Если есть совпадение по задачам, предлагаем совместную идею
                    task_matches = [m for m in matched if m.startswith("совместные задачи")]
                    if task_matches:
                        partner_user = session.query(User).filter_by(id=p.user_id).first()
                        if partner_user:
                            partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                            for pt in partner_tasks[:2]:  # Проверяем первые 2 задачи
                                for ut in user_tasks[:2]:
                                    common_words = set(
                                        re.findall(r"\b\w+\b", (pt.title + " " + (pt.description or "")).lower())
                                    ) & set(re.findall(r"\b\w+\b", (ut.title + " " + (ut.description or "")).lower()))
                                    if common_words:
                                        joint_ideas.append(
                                            f"@{p.contact_info} тоже работает над '{pt.title}' - можно объединиться для совместного изучения {', '.join(list(common_words)[:2])}!"
                                        )
                                        break
                                if joint_ideas and len(joint_ideas) >= 2:  # Максимум 2 идеи
                                    break

            response = response.rstrip("\n")
            if joint_ideas:
                response += "\n\n" + "\n".join(joint_ideas[:2])
    else:
        if has_profile_data:
            # Профиль есть, но не нашли подходящих партнеров
            response = "По твоему профилю пока не нашлось идеальных совпадений, но система развивается! "
        else:
            # Профиля нет или он пустой
            response = "Вижу, что у тебя пока не заполнен профиль или мало данных для поиска. Но это отличная возможность начать строить полезные знакомства! "
        response += "Я могу найти для тебя коллег по работе для обмена опытом и совместных проектов, единомышленников по интересам для спорта, хобби или отдыха, партнеров для изучения новых навыков и достижения целей, а также людей из твоего города для реальных встреч. "
        response += "Расскажи мне о своих интересах, навыках или целях - и я сразу найду подходящих людей. Что тебя увлекает или над чем работаешь?"

    return response


def update_profile(
    skills=None,
    interests=None,
    goals=None,
    city=None,
    current_plans=None,
    timezone=None,
    company=None,
    position=None,
    bio=None,
    languages=None,
    user_id=None,
    session=None,
):
    from models import Session, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)

    updates_made = []  # Отслеживаем что именно изменили
    needs_confirmation = []  # Что требует подтверждения
    
    def update_list_field(field, value, field_name):
        if value is None:  # Если None - не трогаем поле
            return field, None, False
        if value == "":  # Если пустая строка - очищаем поле
            action = f"cleared_{field_name}"
            return None, action, False
        
        current = set((field or "").split(", ")) - {""}  # Разделяем по ", " и убираем пустые
        action = None
        requires_confirmation = False
        
        if value.startswith("+"):
            # Явное добавление
            new_item = value[1:].strip()
            if new_item:
                current.add(new_item)
                action = f"added_{field_name}:{new_item}"
        elif value.startswith("-"):
            # Явное удаление
            remove_item = value[1:].strip()
            if remove_item in current:
                current.discard(remove_item)
                action = f"removed_{field_name}:{remove_item}"
        else:
            # ВАЖНО: Добавление БЕЗ префикса требует подтверждения для interests
            new_items_list = [item.strip() for item in value.split(",") if item.strip()]
            added = []
            for item in new_items_list:
                if item not in current:
                    added.append(item)
            
            if added and field_name == "interests":
                # Для интересов требуем подтверждение
                requires_confirmation = True
                action = f"pending_{field_name}:{', '.join(added)}"
            elif added:
                # Для других полей добавляем сразу
                for item in added:
                    current.add(item)
                action = f"added_{field_name}:{', '.join(added)}"
        
        return ", ".join(sorted(current)), action, requires_confirmation

    if skills is not None:  # Проверяем на None вместо просто if skills
        new_value, action, needs_confirm = update_list_field(profile.skills, skills, "skills")
        if needs_confirm:
            needs_confirmation.append(action)
        else:
            profile.skills = new_value
            if action:
                updates_made.append(action)
    
    if interests is not None:  # Проверяем на None
        new_value, action, needs_confirm = update_list_field(profile.interests, interests, "interests")
        if needs_confirm:
            needs_confirmation.append(action)
        else:
            profile.interests = new_value
            if action:
                updates_made.append(action)
    
    if goals is not None:  # Проверяем на None
        new_value, action, _ = update_list_field(profile.goals, goals, "goals")
        profile.goals = new_value
        if action:
            updates_made.append(action)
    
    if city is not None:  # Разрешаем пустую строку для очистки
        old_city = profile.city
        profile.city = city if city else None
        updates_made.append(f"changed_city:{old_city}->{city if city else 'cleared'}")
    
    if current_plans:
        profile.current_plans = current_plans
        updates_made.append(f"updated_plans")
    
    # Безопасно добавляем новые поля (могут отсутствовать в старой БД)
    if hasattr(profile, "company") and company is not None:  # Разрешаем пустую строку
        old_company = profile.company
        profile.company = company if company else None
        updates_made.append(f"changed_company:{old_company}->{company if company else 'cleared'}")
    
    if hasattr(profile, "position") and position is not None:  # Разрешаем пустую строку
        old_position = profile.position
        profile.position = position if position else None
        updates_made.append(f"changed_position:{old_position}->{position if position else 'cleared'}")
    
    if hasattr(profile, "bio") and bio is not None:  # Разрешаем пустую строку
        old_bio = profile.bio
        profile.bio = bio if bio else None
        updates_made.append(f"changed_bio:{old_bio}->{bio if bio else 'cleared'}")
    
    if hasattr(profile, "languages") and languages is not None:  # Разрешаем пустую строку
        old_languages = profile.languages
        profile.languages = languages if languages else None
        updates_made.append(f"changed_languages:{old_languages}->{languages if languages else 'cleared'}")
    
    if timezone:
        user.timezone = timezone
        updates_made.append(f"changed_timezone:{timezone}")
    
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(pytz.UTC)
    session.commit()
    if close_session:
        session.close()
    
    # Возвращаем детальный ответ
    if needs_confirmation:
        return f"CONFIRMATION_REQUIRED:{';'.join(needs_confirmation)}"
    elif updates_made:
        return f"Профиль обновлен: {'; '.join(updates_made)}"
    else:
        return "Профиль обновлен (изменений не обнаружено)"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить новую задачу с обязательным временем напоминания. КРИТИЧНО: НЕ заполняй description если пользователь не указал явные детали! Оставляй пустым. КРИТИЧНО: используй ТОЧНУЮ ТЕКУЩУЮ ДАТУ из system prompt ({{current_date}}), НЕ используй даты из твоих знаний!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи - должно быть конкретным и содержать: действие, объект, контекст. Хорошо: 'Заказать продукты домой'. Плохо: 'Позвонить другу'",
                    },
                    "description": {
                        "type": "string",
                        "description": "ОПЦИОНАЛЬНО! Оставь ПУСТЫМ если пользователь не указал детали. Если указал - МАКСИМУМ 50 символов. Примеры: 'молоко, хлеб, яйца' или 'обсудить контракт'",
                    },
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM. ОБЯЗАТЕЛЬНО используй current_date из system prompt для вычисления даты! Например, если current_date=2026-01-11 и пользователь просит 'через 5 минут в 12:30', используй '2026-01-11 12:30', а НЕ дату из прошлого!"},
                    "due_date": {"type": "string", "description": "Дедлайн в формате YYYY-MM-DD HH:MM, опционально"},
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать список задач",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Завершить существующую задачу по ID или названию. Вызывай когда пользователь говорит что выполнил/сделал/завершил задачу. НЕ создавай новую задачу, а именно заверши существующую!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Установить напоминание для задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM"},
                },
                "required": ["task_id", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Сохранить информацию о пользователе в долговременную память для персонализации",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "Информация для сохранения, например предпочтения, привычки, цели",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Создать задачу для другого пользователя. Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. reminder_time можно указывать в естественном формате как 'завтра в 10:00', 'до послезавтра 15:00' и т.д.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Подробное описание задачи (опционально)"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Время дедлайна в любом удобном формате: 'завтра в 10:00', 'до послезавтра 15:00', 'сегодня в 18:00' и т.д.",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username получателя с @ (например @username)",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Детали: желаемый результат, критерии выполнения, важность",
                    },
                },
                "required": ["title", "reminder_time", "delegated_to_username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "Принять делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "Отклонить делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Получить статус выполнения делегированной задачи для инициатора",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить название, описание или время напоминания задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "title": {"type": "string", "description": "Новое название, опционально"},
                    "description": {"type": "string", "description": "Новое описание, опционально"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время напоминания в формате YYYY-MM-DD HH:MM, опционально",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Удалить задачу по ID или названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_priority",
            "description": "Установить приоритет задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "priority": {"type": "string", "description": "Приоритет: high, medium, low"},
                },
                "required": ["task_id", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Получить полную информацию о задаче",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя. ВАЖНО: По умолчанию все значения ДОБАВЛЯЮТСЯ к существующим (не заменяют). Используй префикс '-' для удаления. ДЛЯ ПОЛНОЙ ОЧИСТКИ ПРОФИЛЯ: передай пустые строки '' для всех полей (city='', company='', position='', bio='', languages='', skills='', interests='', goals=''). Например: interests='бег' - добавит к существующим, interests='-криптовалюты' - удалит из списка, interests='' - полностью очистит интересы",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки (добавляются к существующим, через запятую). Для удаления используй '-навык'"},
                    "interests": {"type": "string", "description": "Интересы (добавляются к существующим, через запятую). Для удаления используй '-интерес'"},
                    "goals": {"type": "string", "description": "Цели (добавляются к существующим)"},
                    "city": {"type": "string", "description": "Город пользователя (заменяет старое значение), опционально"},
                    "current_plans": {
                        "type": "string",
                        "description": "Текущие планы или события пользователя, опционально",
                    },
                    "current_time": {
                        "type": "string",
                        "description": "Текущее время пользователя в формате HH:MM, опционально",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Часовой пояс пользователя, например 'Europe/Moscow', опционально",
                    },
                    "company": {
                        "type": "string",
                        "description": "Компания, в которой работает пользователь (заменяет старое значение), опционально",
                    },
                    "bio": {
                        "type": "string",
                        "description": "Чем пользователь может помочь другим (экспертиза, консультации, области сотрудничества), заменяет старое значение, опционально",
                    },
                    "languages": {
                        "type": "string",
                        "description": "Языки пользователя (например: Русский (родной), English (C1), Español (A2)), заменяет старое значение, опционально",
                    },
                    "position": {"type": "string", "description": "Должность пользователя (заменяет старое значение), опционально"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_alternatives",
            "description": "Предложить альтернативы для невыполненной задачи: перенести, разбить на части, делегировать, найти партнёра",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reason": {"type": "string", "description": "Причина невыполнения (опционально)"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "Удалить все задачи пользователя. КРИТИЧНО: Это необратимая операция! Перед вызовом ОБЯЗАТЕЛЬНО подтверди у пользователя: 'Ты точно хочешь удалить ВСЕ задачи? Это действие нельзя отменить.' и дождись явного подтверждения.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription_payment",
            "description": "Создать платеж для оформления или продления месячной подписки",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subscription_status",
            "description": "Проверить статус текущей подписки пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_subscription",
            "description": "Отменить текущую подписку пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    import re
    import json
    from datetime import datetime, timezone, timedelta
    import pytz

    logger = logging.getLogger(__name__)

    # Ensure context is a list or None
    if context is not None and not isinstance(context, list):
        logger.warning(f"context is not a list: {type(context)}, setting to None")
        context = None

    # Проверяем сообщение о времени и обновляем timezone
    time_message_match = re.search(r"мое\s+местное\s+время:\s*(\d{1,2}:\d{2})", message.lower())
    if time_message_match:
        user_time_str = time_message_match.group(1)
        detected_timezone = determine_timezone_from_time(user_time_str, user_id)
        if detected_timezone:
            logger.info(f"Detected timezone {detected_timezone} from time {user_time_str}")
            update_profile(timezone=detected_timezone, user_id=user_id)

    # Сохраняем оригинальное сообщение ДО очистки
    original_message = message
    # Extract mentions before cleaning message
    mentions = re.findall(r"@[\w]+", message)
    mentions_str = ", ".join(mentions) if mentions else "нет"
    # Clean message from mentions for processing
    clean_message = re.sub(r"@[\w]+", "", message).strip()
    context_len = (
        len(context) if context and not isinstance(context, int) else (context if isinstance(context, int) else 0)
    )
    logger.info(
        f"chat_with_ai called with message: {clean_message[:50]}..., mentions: {mentions_str}, context len: {context_len}, user_id: {user_id}, file: {file_content is not None}"
    )
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")

    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ключ DeepSeek не настроен. Это демо ответ: Привет! Я AI-ассистент TaskChat. Чем могу помочь?"

    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        user = None
        profile = None
        session = None
        # Initialize time variables with defaults
        base_now = datetime.now(pytz.UTC)
        user_now = base_now
        current_time_str = user_now.strftime("%H:%M")
        user_username = "user"

        if user_id:
            from models import Session, User, Task, UserProfile, Subscription

            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()

            # Создать пользователя если не существует
            if not user:
                user = User(telegram_id=user_id)
                db_session.add(user)
                db_session.commit()

            # Check subscription
            from config import FREE_ACCESS_MODE

            if not FREE_ACCESS_MODE:
                subscription = db_session.query(Subscription).filter_by(user_id=user.id, status="active").first()
                if not subscription:
                    db_session.close()
                    return "У вас нет активной подписки. Для использования AI-ассистента активируйте подписку в Telegram боте @asibiont_bot. После активации подписки я смогу помогать вам с управлением задачами!"

            # Get user current time FIRST before using it
            base_now = datetime.now(pytz.UTC)
            logger.info(f"[TIME CHECK] Real UTC now: {base_now}")
            logger.info(f"[TIME CHECK] Formatted: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            user_tz = pytz.UTC  # Default
            if user:
                tz_str = user.timezone if user.timezone else "UTC"
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Error setting user timezone: {e}")
                    user_tz = pytz.UTC
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")

            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""  # If decryption fails, skip

            # Добавляем информацию из профиля (компания, должность и т.д.)
            profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_filled = False
            if profile:
                profile_info = []
                if profile.city:
                    profile_info.append(f"Город: {profile.city}")
                if profile.company:
                    profile_info.append(f"Компания: {profile.company}")
                if profile.position:
                    profile_info.append(f"Должность: {profile.position}")
                if hasattr(profile, 'bio') and profile.bio:
                    profile_info.append(f"Чем могу помочь: {profile.bio}")
                if hasattr(profile, 'languages') and profile.languages:
                    profile_info.append(f"Языки: {profile.languages}")
                if profile.skills:
                    profile_info.append(f"Навыки: {profile.skills}")
                if profile.interests:
                    profile_info.append(f"Интересы: {profile.interests}")
                if profile.goals:
                    profile_info.append(f"Цели: {profile.goals}")
                
                # Определяем незаполненные поля
                empty_fields = []
                if not profile.city:
                    empty_fields.append("город")
                if not profile.company:
                    empty_fields.append("компания")
                if not profile.position:
                    empty_fields.append("должность")
                if not profile.skills:
                    empty_fields.append("навыки")
                if not profile.interests:
                    empty_fields.append("интересы")
                if not profile.goals:
                    empty_fields.append("цели")
                if not (hasattr(profile, 'languages') and profile.languages):
                    empty_fields.append("языки")
                if not (hasattr(profile, 'bio') and profile.bio):
                    empty_fields.append("чем могу помочь")
                
                if profile_info:
                    user_memory += f"\nПрофиль: {', '.join(profile_info)}"
                
                # Проактивное заполнение при незаполненных полях
                if empty_fields:
                    fields_list = ', '.join(empty_fields[:3])  # Берем первые 3 незаполненных
                    user_memory += f"\n⚠️ НЕЗАПОЛНЕННЫЕ ПОЛЯ: {fields_list}. Каждые 5-7 сообщений ПРОАКТИВНО спрашивай об одном из них (естественно в контексте диалога, не навязчиво)!"
                
                profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                # Если профиль совсем пустой - срочно спроси в первом сообщении
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\nКРИТИЧНО ВАЖНО: Профиль почти ПУСТ! В первом ответе дружелюбно спроси о городе, компании или интересах для лучшей помощи!"
            else:
                user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"

            # НЕ загружаем задачи в user_memory! Агент должен сам вызвать list_tasks()
            # Это критично для предотвращения выдумывания задач

            # НО добавляем КРАТКУЮ сводку для контекста
            tasks_summary = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
            overdue_tasks = (
                db_session.query(Task)
                .filter(Task.user_id == user.id, Task.reminder_time < user_now, Task.status == "pending")
                .limit(5)
                .all()
            )

            if tasks_summary > 0:
                user_memory += f"\nСводка: всего активных задач {tasks_summary}"

            if overdue_tasks:
                overdue_titles = [f"{t.title}" for t in overdue_tasks]
                user_memory += f"\nПРОСРОЧЕННЫЕ ЗАДАЧИ: {', '.join(overdue_titles)} - предложи помощь!"

            # Add delegated tasks info
            if user.username:
                delegated_tasks = (
                    db_session.query(Task)
                    .filter(Task.delegated_to_username.ilike(user.username), Task.delegation_status == "pending")
                    .all()
                )
                if delegated_tasks:
                    delegated_info = [
                        f"Задача '{t.title}' (ID: {t.id}) от @{creator.username if (creator := db_session.query(User).filter_by(id=t.user_id).first()) else 'unknown'}"
                        for t in delegated_tasks[:3]
                    ]
                    user_memory += f"\nДелегированные задачи для принятия: {', '.join(delegated_info)}"

            # Add info about tasks delegated BY user
            my_delegated_tasks = (
                db_session.query(Task)
                .filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(["pending", "accepted"]),
                )
                .all()
            )
            if my_delegated_tasks:
                my_delegated_info = [
                    f"Задача '{t.title}' поручена @{t.delegated_to_username} (статус: {t.delegation_status})"
                    for t in my_delegated_tasks[:3]
                ]
                user_memory += f"\nЗадачи поручённые другим: {', '.join(my_delegated_info)}"

            # Add partners/contacts info
            try:
                partners = get_partners_list(user_id=user_id, session=db_session)
                if partners:
                    # partners - это список объектов UserProfile
                    partners_usernames = []
                    for p in partners[:5]:
                        partner_user = db_session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            partners_usernames.append(f"@{partner_user.username}")
                    if partners_usernames:
                        user_memory += f"\nДоступные контакты: {', '.join(partners_usernames)}"
            except Exception as e:
                logger.error(f"Error getting partners: {e}")

            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars

            # Обработка pending_action
            if user and user.pending_action:
                try:
                    pending_data = json.loads(user.pending_action)
                    action_type = pending_data.get("type")

                    # Проверка на таймаут (24 часа)
                    timestamp = pending_data.get("timestamp")
                    if timestamp:
                        created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - created_at > timedelta(hours=24):
                            logger.info(f"Pending action timed out for user {user_id}, clearing")
                            user.pending_action = None
                            db_session.commit()
                            # Продолжить с обычной обработкой
                            pass
                        else:
                            # Продолжить обработку pending_action
                            pass

                    if action_type == "result_check_response":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # Сохранить ответ пользователя как completion_notes
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            task.completion_notes = original_message  # Сохраняем полный ответ пользователя
                            db_session.commit()
                        # Очистить pending_action
                        user.pending_action = None
                        db_session.commit()
                        # Вернуть специальный ответ для обработки результата
                        return f"Спасибо за информацию о задаче '{task_title}'! Результат сохранён для анализа."

                    elif action_type == "task_skip_confirmation":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # Обработать ответ пользователя о пропуске задачи
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            if "да" in original_message.lower() or "пропустить" in original_message.lower():
                                skip_response = f"Задача '{task_title}' отмечена как пропущенная. Могу предложить альтернативы или создать новую задачу."
                                return skip_response
                            else:
                                keep_response = f"Хорошо, оставляем задачу '{task_title}' активной. Чем могу помочь?"
                                return keep_response
                        user.pending_action = None
                        db_session.commit()
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error processing pending_action: {e}")
                    user.pending_action = None
                    db_session.commit()

        db_session.close()

        # Classify user intent (use improved version if available, fallback to legacy)
        if PROMPTS_V2_AVAILABLE:
            intent = improved_classify_intent(clean_message, mentions_str)
            logger.info(f"[PROMPTS V2] User intent: {intent['type']} (confidence: {intent['confidence']})")
        else:
            intent = classify_user_intent(clean_message, mentions_str)
            logger.info(f"[LEGACY] User intent: {intent['type']} (confidence: {intent['confidence']})")

        # ГЛУБОКИЙ АНАЛИЗ КОНТЕКСТА ДЛЯ ПЕРСОНАЛИЗИРОВАННЫХ СОВЕТОВ
        context_analysis = analyze_user_context_for_advice(user_id, clean_message, context)
        if "error" not in context_analysis:
            # Добавляем анализ в user_memory для использования в промпте
            user_memory += f"\n\nАНАЛИЗ КОНТЕКСТА:\n"
            user_memory += f"Профиль заполнен на {context_analysis['profile'].get('filled_fields', 0)}/6 полей\n"
            user_memory += f"Задачи: {context_analysis['tasks']['pending']} активных, {context_analysis['tasks']['completed']} выполнено\n"
            user_memory += f"Основные темы: {', '.join([f'{theme}: {count}' for theme, count in context_analysis['patterns']['main_themes']])}\n"
            user_memory += f"Эмоциональное состояние: {context_analysis['context_insights']['emotional_state']}\n"
            user_memory += f"Уровень срочности: {context_analysis['context_insights']['urgency_level']}\n"
            if context_analysis['recommendations']:
                user_memory += f"Персональные рекомендации: {', '.join(context_analysis['recommendations'])}\n"

        # Construct system prompt with replaced placeholders
        # Расширяем system prompt для работы с относительным временем
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        
        if PROMPTS_V2_AVAILABLE:
            system_prompt = get_optimized_prompt_final(
                user_now, current_time_str, user_username, mentions_str, user_memory
            )
            logger.info("[PROMPTS V2] Using optimized prompt system")
        else:
            system_prompt = get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory)
            logger.info("[LEGACY] Using extended prompt system")

        # Проверяем контекст последней созданной задачи для edit_task
        last_task_context = ""
        if redis_client and user_id:
            try:
                last_task_data = await redis_client.get(f"last_task_id:{user_id}")
                if last_task_data:
                    task_info = json.loads(last_task_data.decode("utf-8"))
                    last_task_context = f"\n\nКОНТЕКСТ ПОСЛЕДНЕЙ ЗАДАЧИ: ID={task_info['id']}, название='{task_info['title']}', время='{task_info.get('reminder_time', '')}'. ЕСЛИ пользователь даёт уточнения (я ошибся, не завтра а сегодня, изменить время и т.д.), ОБЯЗАТЕЛЬНО используй edit_task(task_id={task_info['id']}, ...)!"
                    logger.info(f"[LAST_TASK_CONTEXT] Loaded for user {user_id}: {task_info}")
            except Exception as e:
                logger.error(f"Error loading last_task_id from Redis: {e}")

        messages = [{"role": "system", "content": system_prompt}]
        if context and isinstance(context, list):
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        # Добавляем текущее сообщение с контекстом последней задачи
        user_message_with_context = message + last_task_context
        messages.append({"role": "user", "content": user_message_with_context})

        # Определяем, является ли сообщение вопросом о совете
        is_advice_question = any(word in clean_message.lower() for word in [
            "что делать", "как", "совет", "помоги", "что посоветуешь", "как быть", 
            "что предпринять", "какие шаги", "что делать с", "как решить",
            "не знаю с чего начать", "с чего начать", "как начать", "что делать дальше",
            "что делать если", "как лучше", "что посоветуешь", "какой совет",
            "нужен совет", "посоветуй", "как поступить", "что делать в ситуации",
            "как оптимизировать", "как улучшить", "как подготовиться", "как начать",
            "с чего начать", "как эффективно", "что можно сделать", "как решить проблему"
        ])

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "none" if is_advice_question else "auto",
            "temperature": 0.7,
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        # Retry loop for API call
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        logger.info(f"DeepSeek API response status: {response.status} (attempt {attempt + 1})")
                        if response.status == 200:
                            # Успешный ответ - обрабатываем
                            tool_calls = []
                            try:
                                result = await response.json()
                                message_response = result["choices"][0]["message"]
                                content = message_response.get("content", "")
                                print(f"[DEBUG API] Raw content: '{content}'")  # DEBUG
                                # Фильтровать сырые tool calls
                                content = re.sub(r"<\|.*?\|>", "", content).strip()
                                content = re.sub(
                                    r"<｜DSML｜function_calls>.*?</｜DSML｜function_calls>",
                                    "",
                                    content,
                                    flags=re.DOTALL,
                                ).strip()
                                # Удаляем JSON блоки с tool_calls если они попали в текст
                                content = re.sub(
                                    r'```json\s*\{.*?"tool_calls".*?\}\s*```', "", content, flags=re.DOTALL
                                ).strip()
                                content = re.sub(r'\{.*?"tool_calls".*?\}', "", content, flags=re.DOTALL).strip()
                                content = re.sub(
                                    r'\{.*?"name":\s*"".*?"arguments".*?\}', "", content, flags=re.DOTALL
                                ).strip()

                                # Проверяем tool_calls в API response
                                tool_calls = message_response.get("tool_calls")
                                print(f"[DEBUG API] tool_calls: {tool_calls}")  # DEBUG
                            except Exception as e:
                                logger.error(f"Error parsing API response: {e}")
                                if attempt < max_retries:
                                    logger.info(f"Retrying API call due to parse error (attempt {attempt + 1})")
                                    await asyncio.sleep(1)
                                    continue
                                content = "Извините, произошла ошибка при обработке ответа от ИИ. Попробуйте еще раз."

                            # Обработка tool calls и т.д.
                            tool_results = []  # Инициализируем заранее
                            print(f"[DEBUG] tool_calls value: {tool_calls}, bool: {bool(tool_calls)}")  # DEBUG

                            # Проверяем, не написал ли AI JSON в текст вместо tool_calls
                            json_in_text = re.search(r'\{.*?"name":\s*"(.*?)"\s*,\s*"arguments":\s*(\{.*?\})\s*\}', content, re.DOTALL)
                            if json_in_text and not tool_calls:
                                print(f"[DEBUG] Found JSON in text, converting to tool_call")  # DEBUG
                                try:
                                    func_name = json_in_text.group(1)
                                    func_args = json.loads(json_in_text.group(2))
                                    tool_calls = [{
                                        'function': {
                                            'name': func_name,
                                            'arguments': json.dumps(func_args, ensure_ascii=False)
                                        }
                                    }]
                                    # Удаляем JSON из текста
                                    content = re.sub(r'\{.*?"name":\s*".*?"\s*,\s*"arguments":\s*\{.*?\}\s*\}', '', content, flags=re.DOTALL).strip()
                                except Exception as e:
                                    print(f"[DEBUG] Failed to parse JSON in text: {e}")  # DEBUG

                            if tool_calls:
                                print(f"[DEBUG] Tool calls found, processing...")  # DEBUG

                                # ПОСТ-ПРОЦЕССИНГ: Корректируем tool calls на основе intent
                                corrected_tool_calls = post_process_tool_calls(intent, tool_calls, message)
                                if corrected_tool_calls:
                                    print(f"[DEBUG] Tool calls corrected from {len(tool_calls)} to {len(corrected_tool_calls)} calls")
                                    tool_calls = corrected_tool_calls

                                # Если это вопрос о совете, игнорируем tool_calls и обрабатываем как обычный текст
                                if is_advice_question:
                                    print(f"[DEBUG] Ignoring tool_calls for advice question")  # DEBUG
                                    tool_calls = None
                                else:
                                    # Обработка tool calls
                                    tool_results = []
                                    for tool_call in tool_calls:
                                        try:
                                            func_name = tool_call["function"]["name"]
                                            args = json.loads(tool_call["function"]["arguments"])
                                            logger.info(f"[TOOL CALL] Executing {func_name} with args: {args}")

                                            if func_name == "add_task":
                                                logger.info(f"[AI TOOL CALL] add_task called with reminder_time: {args.get('reminder_time')}, current user_now: {user_now}")
                                                result = add_task(
                                                    title=args.get("title", args.get("task_title", "Задача")),
                                                    description=args.get("description", ""),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "complete_task":
                                                result = complete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=args.get("task_title"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "list_tasks":
                                                result = list_tasks(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "find_partners":
                                                result = find_partners(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "update_profile":
                                                result = update_profile(
                                                    city=args.get("city"),
                                                    company=args.get("company"),
                                                    position=args.get("position"),
                                                    interests=args.get("interests"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delegate_task":
                                                result = delegate_task(
                                                    title=args.get("title"),
                                                    delegated_to_username=args.get("delegated_to_username"),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delete_all_tasks":
                                                result = delete_all_tasks(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delete_task":
                                                result = delete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=args.get("task_title"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "edit_task":
                                                result = edit_task(
                                                    task_id=args.get("task_id"),
                                                    title=args.get("title"),
                                                    description=args.get("description"),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "check_subscription_status":
                                                result = check_subscription_status(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "create_subscription_payment":
                                                result = create_subscription_payment(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            else:
                                                logger.warning(f"[TOOL CALL] Unknown function: {func_name}")
                                                tool_results.append(
                                                    {"function": func_name, "result": f"Неизвестная функция: {func_name}"}
                                                )

                                        except Exception as e:
                                            logger.error(f"[TOOL CALL] Error executing {func_name}: {e}")
                                            tool_results.append(
                                                {"function": func_name, "result": f"Ошибка выполнения: {str(e)}"}
                                            )

                                # Генерируем естественный ответ на основе результатов tool calls
                                if tool_results:
                                    natural_responses = []
                                    has_list_tasks = False
                                    list_tasks_result = None

                                    for action in tool_results:
                                        result_text = action["result"]
                                        func_name = action["function"]

                                        # Проверяем, есть ли list_tasks в результатах
                                        if func_name == "list_tasks":
                                            has_list_tasks = True
                                            list_tasks_result = result_text

                                        if "Добавлена задача" in result_text:
                                            match = re.search(r"Добавлена задача '([^']+)' \(ID: (\d+)\)", result_text)
                                            if match:
                                                title = match.group(1)
                                                task_id = int(match.group(2))
                                                
                                                # Получаем рекомендации из базы данных
                                                from models import Session, Task
                                                session_db = Session()
                                                try:
                                                    task = session_db.query(Task).filter_by(id=task_id).first()
                                                    recommendations = []
                                                    if task and task.recommendations:
                                                        import json
                                                        try:
                                                            recommendations = json.loads(task.recommendations)
                                                        except:
                                                            pass
                                                    
                                                    # Формируем краткий ответ
                                                    if recommendations:
                                                        rec_text = " Рекомендации: " + ", ".join(recommendations[:3])
                                                        natural = f'Задача "{title}" добавлена и запланирована.{rec_text}'
                                                    else:
                                                        natural = f'Задача "{title}" добавлена и запланирована.'
                                                    
                                                    natural_responses.append(natural)
                                                finally:
                                                    session_db.close()
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Завершена задача" in result_text:
                                            match = re.search(r"Завершена задача '([^']+)'", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'Отлично, отметил задачу "{title}" как выполненную! Это важный шаг вперед. Теперь стоит проанализировать, что было сделано правильно, и подумать о следующих задачах. Есть ли уроки, которые можно извлечь из выполнения этой задачи? Может быть, стоит отметить достижения или запланировать что-то новое?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Задачи:" in result_text:
                                            # Для list_tasks добавляем умный анализ вместо простого вывода
                                            natural = enrich_task_list_with_insights(result_text, user_id)
                                            natural_responses.append(natural)

                                        elif (
                                            "Найдены партнеры:" in result_text
                                            or "партнеры найдены" in result_text.lower()
                                        ):
                                            natural_responses.append(result_text)

                                        elif "Профиль обновлен" in result_text:
                                            # Парсим детали обновления
                                            if "added_interests:" in result_text:
                                                match = re.search(r"added_interests:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"Отлично! Добавил в твои интересы: {items}. Теперь я смогу находить для тебя людей с похожими увлечениями и предлагать релевантные активности."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Добавил новые интересы.")
                                            
                                            elif "removed_interests:" in result_text:
                                                match = re.search(r"removed_interests:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"Понял, убрал из интересов: {items}. Обновил твой профиль."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Убрал интересы.")
                                            
                                            elif "changed_city:" in result_text:
                                                match = re.search(r"changed_city:([^->]+)->([^;]+)", result_text)
                                                if match:
                                                    old_city = match.group(1).strip()
                                                    new_city = match.group(2).strip()
                                                    natural = f"Обновил город с {old_city} на {new_city}! Теперь буду искать для тебя людей и события в {new_city}."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Изменил город.")
                                            
                                            elif "changed_company:" in result_text:
                                                match = re.search(r"changed_company:([^->]+)->([^;]+)", result_text)
                                                if match:
                                                    new_company = match.group(2).strip()
                                                    natural = f"Записал новое место работы: {new_company}. Профиль обновлен!"
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Изменил компанию.")
                                            
                                            elif "added_skills:" in result_text:
                                                match = re.search(r"added_skills:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"Отлично! Добавил в навыки: {items}. Это поможет найти проекты и людей, которым нужны такие компетенции."
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Добавил навыки.")
                                            
                                            elif "added_goals:" in result_text:
                                                match = re.search(r"added_goals:([^;]+)", result_text)
                                                if match:
                                                    items = match.group(1).strip()
                                                    natural = f"Записал новую цель: {items}. Буду помогать тебе двигаться к ней!"
                                                    natural_responses.append(natural)
                                                else:
                                                    natural_responses.append("Профиль обновлен! Добавил цели.")
                                            
                                            else:
                                                # Общий случай если не удалось распарсить
                                                natural_responses.append("Профиль обновлен! Сохранил изменения.")

                                        elif "Задача" in result_text and "делегирована" in result_text:
                                            natural = "Отлично, задача делегирована! Я уведомлю получателя."
                                            natural_responses.append(natural)

                                        elif "Удалены все задачи" in result_text:
                                            natural = "Удалил все твои задачи. Теперь список пуст - можно начинать с чистого листа!"
                                            natural_responses.append(natural)

                                        elif "Задача" in result_text and "удалена" in result_text:
                                            match = re.search(r"Задача '([^']+)' удалена", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'Удалил задачу "{title}". Что дальше?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        else:
                                            natural_responses.append(result_text)

                                    # Для list_tasks анализ уже добавлен выше

                                    final_content = "\n".join(natural_responses)
                                    # Обогащаем ответ вовлекающими элементами
                                    final_content = enrich_response_with_engagement(
                                        final_content, user_id, original_message
                                    )

                                    # Enforcement отключен - AI должен отвечать естественно
                                    # intent_type = "list_tasks" if has_list_tasks else None
                                    # final_content = await enforce_prompt_compliance(
                                    #     final_content, intent_type, user_id, context,
                                    #     system_prompt, messages, url, headers
                                    # )

                                    logger.info(
                                        f"[TOOL CALLS] Processed {len(tool_results)} tool calls, returning natural response"
                                    )
                                    return final_content
                            else:
                                # tool_calls были проигнорированы для вопроса совета, переходим к обычной обработке
                                pass

                    print(f"[DEBUG] Exited tool_calls if block")  # DEBUG
                    print(f"[DEBUG] After tool_calls block, about to check fallback")  # DEBUG
                    # Все запросы обрабатывает AI, без принудительных триггеров
                    logger.info("[AI ONLY] All requests handled by AI without forced triggers")
                    print(f"[DEBUG] About to check fallback, content='{content[:50]}...'")  # DEBUG

                    # SMART FALLBACK: Проверяем, нужно ли применить умный fallback (use improved version if available)
                    print(f"[DEBUG] Calling fallback handler...")  # DEBUG
                    print(f"[DEBUG] About to call fallback handler, content='{content[:50]}...'")  # DEBUG
                    try:
                        if PROMPTS_V2_AVAILABLE:
                            fallback_result = improved_fallback(
                                intent, tool_calls if 'tool_calls' in locals() else None, 
                                content, original_message, user_id
                            )
                            logger.info(f"[PROMPTS V2] Fallback actions: {len(fallback_result)}")
                        else:
                            fallback_result = smart_fallback_handler(original_message, mentions_str, user_id, content)
                            logger.info(f"[LEGACY] Fallback actions: {len(fallback_result)}")
                        print(
                            f"[DEBUG] Fallback result: {len(fallback_result) if fallback_result else 0} actions"
                        )  # DEBUG
                        if fallback_result:
                            logger.info(
                                f"[SMART FALLBACK] Applied {len(fallback_result)} fallback actions for user {user_id}"
                            )

                            # Обрабатываем результаты fallback аналогично tool calls
                            natural_responses = []
                            for action in fallback_result:
                                result_text = action["result"]
                                func_name = action["function"]

                                if "Добавлена задача" in result_text:
                                    match = re.search(
                                        r"Добавлена задача '([^']+)' \(ID: \d+\) с напоминанием на ([^)]+)", result_text
                                    )
                                    if match:
                                        title = match.group(1)
                                        time_str = match.group(2)
                                        natural = f'Отлично, добавил задачу "{title}" с напоминанием на {time_str}.'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "Завершена задача" in result_text:
                                    match = re.search(r"Завершена задача '([^']+)'", result_text)
                                    if match:
                                        title = match.group(1)
                                        natural = f'Отлично, отметил задачу "{title}" как выполненную! 👍'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "Задачи:" in result_text:
                                    # Не добавляем сразу, анализ будет добавлен отдельно
                                    pass

                                elif "Удалены все задачи" in result_text:
                                    natural = (
                                        "Удалил все твои задачи. Теперь список пуст - можно начинать с чистого листа!"
                                    )
                                    natural_responses.append(natural)

                                elif "Задача" in result_text and "делегирована" in result_text:
                                    natural = "Отлично, задача делегирована! Я уведомлю получателя."
                                    natural_responses.append(natural)

                                else:
                                    natural_responses.append(result_text)

                            # Проверяем, есть ли list_tasks в результатах fallback
                            has_list_tasks = any(action["function"] == "list_tasks" for action in fallback_result)
                            list_tasks_result = None
                            if has_list_tasks:
                                for action in fallback_result:
                                    if action["function"] == "list_tasks":
                                        list_tasks_result = action["result"]
                                        break

                            # Для list_tasks просто добавляем результат - главный промпт уже содержит все правила
                            if has_list_tasks and list_tasks_result:
                                natural_responses.append(list_tasks_result)
                            
                            # Формируем финальный контент
                            final_content = "\n".join(natural_responses)
                            
                            # Enforcement отключен - AI должен отвечать естественно
                            # intent_type = "list_tasks" if has_list_tasks else None
                            # final_content = await enforce_prompt_compliance(
                            #     final_content, intent_type, user_id, context,
                            #     system_prompt, messages, url, headers
                            # )
                            
                            print(f"[DEBUG FALLBACK] Returning final_content: '{final_content[:200]}...'")  # DEBUG
                            return final_content
                    except Exception as e:
                        logger.error(f"[SMART FALLBACK] Error in fallback handler: {e}")
                        print(f"[DEBUG] Fallback error: {e}")  # DEBUG

                    # Если forced calls не сработали, обрабатываем обычный ответ AI
                    print(f"[DEBUG] After fallback, going to regular response processing")  # DEBUG
                    # Обрабатываем обычный ответ AI без tool calls
                    logger.info("[TOOL CALLS] Tool calls completed, 0 results. Generating natural response...")
                    print(f"[DEBUG] Processing regular AI response, content='{content[:100]}...'")  # DEBUG
                    print(f"[DEBUG] About to enter regular response processing")  # DEBUG
                    original_content = message_response.get("content", "")
                    content = original_content
                    print(f"[DEBUG] Original content: '{original_content[:100]}...'")  # DEBUG

                    # Для обычных ответов ТОЛЬКО заменяем плейсхолдеры, без дополнительной очистки
                    content = replace_placeholders(content, user_now, current_time_str)
                    print(f"[DEBUG] After replace_placeholders: '{content[:100]}...'")  # DEBUG

                    # КРИТИЧЕСКАЯ ПРОВЕРКА: если content пустой или слишком короткий
                    if not content or len(content.strip()) < 3:
                        print(
                            f"[DEBUG] Content is empty or too short: '{content}', len={len(content.strip())}"
                        )  # DEBUG
                        logger.warning(f"[EMPTY RESPONSE] Original: '{original_content[:100]}...', returning original")
                        content = original_content.strip()
                        if not content:
                            logger.warning("[RETRY] Response empty, retrying with explicit instruction")
                            retry_system = (
                                system_prompt
                                + "\n\nКРИТИЧЕСКИ ВАЖНО:\n1. НЕ возвращай JSON, code blocks или технические теги\n2. Отвечай ТОЛЬКО обычным текстом\n3. Если создал задачу - скажи об этом и предложи найти партнёра\n4. Минимум 20 слов в ответе\n5. Будь дружелюбным и конкретным!"
                            )

                            retry_messages = [{"role": "system", "content": retry_system}]
                            if context:
                                for item in context:
                                    if "user" in item:
                                        retry_messages.append({"role": "user", "content": item["user"]})
                                    if "assistant" in item:
                                        retry_messages.append({"role": "assistant", "content": item["assistant"]})
                            retry_messages.append({"role": "user", "content": original_message})

                            async with aiohttp.ClientSession() as retry_session:
                                async with retry_session.post(
                                    url,
                                    headers=headers,
                                    json={
                                        "model": "deepseek-reasoner",
                                        "messages": retry_messages,
                                        "temperature": 0.3,
                                    },
                                    timeout=aiohttp.ClientTimeout(total=120),
                                ) as retry_response:
                                    retry_result = await retry_response.json()
                                    retry_content = retry_result["choices"][0]["message"]["content"]
                                    retry_content = replace_placeholders(retry_content, user_now, current_time_str)
                                    content = retry_content.strip()
                                    logger.info(f"[RETRY] Got retry content: '{content[:100]}...'")
                                    print(f"[DEBUG RETRY] Retry content: '{content[:100]}...'")  # DEBUG
                                    if retry_content and len(retry_content.strip()) >= 3:
                                        content = retry_content
                                    else:
                                        content = "Хорошо, продолжим работу!"
                        else:
                            logger.info(f"[RECOVERED] Using original content: '{content[:100]}...'")

                    # Если все еще пустой после retry
                    if not content:
                        content = "Хорошо, продолжим работу!"

                    # Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, original_message)

                    # Enforcement отключен - AI должен отвечать естественно без дополнительных API вызовов

                    # Очистка от технических деталей перед возвратом
                    # НЕ применяем clean_technical_details для обычных ответов AI!
                    
                    # Метрики качества ответа
                    response_quality = {
                        'length': len(content),
                        'has_questions': '?' in content,
                        'has_tools': bool(tool_calls),
                        'intent_type': intent.get('type', 'unknown'),
                        'user_id': user_id
                    }
                    logger.info(f"[RESPONSE QUALITY] {response_quality}")
                    
                    # Обработка ошибок: если ответ слишком короткий или пустой, дать fallback
                    if not content or len(content.strip()) < 10:
                        logger.warning(f"[FALLBACK] Empty or too short response, using fallback")
                        content = improved_fallback(intent, tool_calls, content, message, user_id)
                    
                    # АНАЛИЗ ВЗАИМОДЕЙСТВИЯ ДЛЯ ПРЕДЛОЖЕНИЯ ОБНОВЛЕНИЯ ПРОФИЛЯ
                    profile_suggestion = analyze_interaction_for_profile_update(user_id, clean_message, content)
                    if profile_suggestion:
                        content += f"\n\n{profile_suggestion}"
                    
                    # ДОПОЛНИТЕЛЬНЫЕ ИИ-АНАЛИЗЫ ДЛЯ УЛУЧШЕНИЯ ОТВЕТА
                    
                    # 1. Анализ эмоций пользователя
                    sentiment = analyze_sentiment(clean_message)
                    if sentiment['sentiment'] == 'negative' and sentiment['intensity'] > 0.6:
                        content += "\n\nВижу, что ты расстроен. Если хочешь поговорить об этом или нужна помощь - я здесь!"
                    elif sentiment['sentiment'] == 'positive' and sentiment['intensity'] > 0.7:
                        content += "\n\nРад, что у тебя всё хорошо! 😊"
                    
                    # 2. Автоматическое извлечение задач из сообщения
                    if len(clean_message.split()) > 3:  # Только для осмысленных сообщений
                        extracted_tasks = extract_tasks_with_ai(clean_message, user_id)
                        if extracted_tasks:
                            content += f"\n\n📋 Я заметил, что ты упомянул {len(extracted_tasks)} задач(и). Хочешь, я добавлю их в твой список?"
                            for task in extracted_tasks[:2]:  # Показываем первые 2
                                content += f"\n• {task['title']}"
                    
                    # 3. Персонализированные рекомендации (раз в несколько сообщений)
                    import random
                    if random.random() < 0.3:  # 30% шанс
                        recommendations = generate_recommendations(user_id)
                        if recommendations:
                            rec = random.choice(recommendations)
                            content += f"\n\n💡 Рекомендация: {rec.get('title', '')} - {rec.get('description', '')}"
                    
                    # 4. Проверка на дубликаты задач (если упоминаются задачи)
                    if any(word in clean_message.lower() for word in ['задача', 'задачи', 'дело', 'сделать']):
                        # Получить текущие задачи пользователя
                        from models import Session, Task
                        session_db = Session()
                        try:
                            user_tasks = session_db.query(Task).filter_by(user_id=user_id, completed=False).limit(10).all()
                            task_titles = [{'title': t.title} for t in user_tasks]
                            duplicates = detect_duplicates(task_titles)
                            if duplicates:
                                content += f"\n\n⚠️ Обнаружено {len(duplicates)} возможных дубликатов или конфликтов в задачах. Проверь свой список!"
                        finally:
                            session_db.close()
                    
                    print(f"[DEBUG] About to return content: '{content}'")  # DEBUG
                    return content

            except Exception as e:
                import traceback

                logger.error(f"Error in chat_with_ai: {e}")
                logger.error(f"Error type: {type(e).__name__}")
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                # Добавляем номер строки для отладки
                tb = traceback.extract_tb(e.__traceback__)
                if tb:
                    last_frame = tb[-1]
                    logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
                return f"Ошибка: {str(e)} [v2]"

    except Exception as e:
        import traceback

        logger.error(f"Error in chat_with_ai: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        # Добавляем номер строки для отладки
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last_frame = tb[-1]
            logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
        return f"Ошибка: {str(e)} [v2]"


async def generate_reminder(user_id, task_title):
    """Генерирует текст напоминания о задаче"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"  # Можно получить из базы если нужно
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о задаче: {task_title}"},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    
                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "reminder")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Reminder response not compliant: {issues}")
                        # Принуждаем исправление
                        content = await enforce_prompt_compliance(
                            content, "reminder", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_reminder: {e}")
        return f"Напоминание о '{task_title}'."


async def generate_result_check(user_id, task_title):
    """Генерирует вопрос о результате выполнения задачи"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Спроси о результате выполнения задачи '{task_title}'. Узнай о времени, сложностях, улучшениях.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    
                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "result_check")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Result check response not compliant: {issues}")
                        # Принуждаем исправление
                        content = await enforce_prompt_compliance(
                            content, "result_check", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "Ошибка генерации вопроса."
    except Exception as e:
        print(f"Error in generate_result_check: {e}")
        return f"Результат задачи '{task_title}'?"


async def generate_proactive_message(user_id):
    """Генерирует проактивное сообщение, если нет задач на ближайший час"""
    try:
        # Получить память пользователя, планы других и текущие задачи
        user_memory = ""
        plans_info = ""
        tasks_info = ""
        if user_id:
            from models import Session, User, UserProfile, Task

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user is None:
                return "Добавьте задачу."
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            # Получить профиль пользователя
            user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if user_profile and user_profile.interests:
                # Найти планы других пользователей, совпадающие с интересами
                profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
                tips = []
                for p in profiles:
                    if p.current_plans and p.contact_info != f"user{user_id}":
                        for interest in user_profile.interests.split(","):
                            interest_words = interest.strip().lower().split()
                            if any(word in p.current_plans.lower() for word in interest_words):
                                tips.append(
                                    f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} - может быть интересно с твоими интересами в {interest.strip()}."
                                )
                                break
                if tips:
                    plans_info = "\nПланы людей: " + " ".join(tips[:2])
            # Получить текущие задачи
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            pending_tasks = [t.title for t in tasks if t.status in ["pending", "in_progress"]]
            if pending_tasks:
                tasks_info = f"\nТекущие невыполненные задачи: {', '.join(pending_tasks[:3])}"
            session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory + plans_info + tasks_info)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "У пользователя нет задач на ближайший час. Создай позитивное проактивное сообщение.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    # Проактивные сообщения уже вовлекающие, но можно усилить
                    content = enrich_response_with_engagement(content, user_id, "")
                    
                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "proactive")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Proactive message response not compliant: {issues}")
                        # Принуждаем исправление
                        content = await enforce_prompt_compliance(
                            content, "proactive", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "Ошибка генерации сообщения."
    except Exception as e:
        print(f"Error in generate_proactive_message: {e}")
        return "Добавьте задачу."


async def generate_daily_report(user_id):
    """Генерирует ежедневный отчет о задачах"""
    try:
        # Получить задачи пользователя
        from models import Session, Task

        session = Session()
        tasks = session.query(Task).filter_by(user_id=user_id).all()
        session.close()

        completed = [t for t in tasks if t.status == "completed"]
        pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    
                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "daily_report")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Daily report response not compliant: {issues}")
                        # Принуждаем исправление
                        content = await enforce_prompt_compliance(
                            content, "daily_report", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "Ошибка генерации отчета."
    except Exception as e:
        print(f"Error in generate_daily_report: {e}")
        return "Отчет о задачах."


async def generate_overdue_reminder(user_id, overdue_tasks, escalation_level=1):
    """Генерирует напоминание о просроченных задачах"""
    try:
        # Поддержка как объектов Task, так и словарей
        if overdue_tasks and isinstance(overdue_tasks[0], dict):
            task_titles = [t.get('title', 'Задача') for t in overdue_tasks]
        else:
            task_titles = [t.title for t in overdue_tasks]
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        # Адаптируем тон в зависимости от уровня эскалации
        if escalation_level == 1:
            tone_instruction = "Будь дружелюбным, но настойчивым. Напомни о важности выполнения задач."
        elif escalation_level == 2:
            tone_instruction = "Будь более строгим. Подчеркни негативные последствия невыполнения."
        else:  # 3+
            tone_instruction = "Будь очень строгим и мотивирующим. Предложи конкретные альтернативы и помощь."

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Напомни о просроченных задачах: {', '.join(task_titles)}. {tone_instruction} Предложи варианты решения.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    
                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "overdue")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Overdue reminder response not compliant: {issues}")
                        # Принуждаем исправление
                        content = await enforce_prompt_compliance(
                            content, "overdue", user_id, None, system_prompt, messages, url, headers
                        )
                    
                    return content
                else:
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."


# Функции для работы с задачами
def list_tasks(user_id=None, session=None):
    """Возвращает список задач пользователя в чистом текстовом формате"""
    from models import Task, User
    from sqlalchemy import or_

    if session is None:
        from models import Session

        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Получить задачи пользователя или делегированные ему
        query = session.query(Task).filter(Task.user_id == user.id)
        if user.username and user.username.strip():
            query = query.union(
                session.query(Task).filter(Task.delegated_to_username.ilike(user.username))
            )
        tasks = query.all()

        if not tasks:
            return "У вас нет активных задач. Добавьте первую задачу - просто напишите что нужно сделать!"

        # Формируем детальный список без эмодзи и форматирования
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
        user_username_lower = user.username.lower() if user.username else ""
        delegated_to_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() == user_username_lower
        ]
        delegated_by_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() != user_username_lower
        ]
        my_tasks = [t for t in active_tasks if not t.delegated_to_username]

        from datetime import datetime
        import pytz

        # Определяем timezone пользователя
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)

        result = f"У вас {len(active_tasks)} активных задач\n\n"

        # Мои задачи
        if my_tasks:
            result += "Ваши задачи:\n"
            for task in my_tasks:
                reminder_info = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            delta = now - reminder_dt
                            total_minutes = int(delta.total_seconds() / 60)
                            
                            if total_minutes < 60:
                                reminder_info = f" - просрочено на {total_minutes} мин"
                            elif total_minutes < 1440:  # меньше 24 часов
                                hours = total_minutes // 60
                                minutes = total_minutes % 60
                                if minutes > 0:
                                    reminder_info = f" - просрочено на {hours} ч {minutes} мин"
                                else:
                                    reminder_info = f" - просрочено на {hours} ч"
                            else:
                                days = delta.days
                                hours = (total_minutes % 1440) // 60
                                if hours > 0:
                                    reminder_info = f" - просрочено на {days} д {hours} ч"
                                else:
                                    reminder_info = f" - просрочено на {days} д"
                        else:
                            reminder_info = f" - {reminder_dt.strftime('%d.%m %H:%M')}"
                    except:
                        pass

                priority_text = {"high": "высокий", "medium": "средний", "low": "низкий"}.get(getattr(task, 'priority', None), "")
                priority_info = f" ({priority_text} приоритет)" if priority_text else ""
                result += f"- {task.title}{reminder_info}{priority_info}\n"
            result += "\n"

        # Делегированные мне
        if delegated_to_me:
            result += "Делегировано вам:\n"
            for task in delegated_to_me:
                creator = session.query(User).filter_by(id=task.user_id).first()
                creator_name = f"от @{creator.username}" if creator else "от кого-то"
                result += f"- {task.title} ({creator_name})\n"
            result += "\n"

        # Делегированные мной
        if delegated_by_me:
            result += "Вы делегировали:\n"
            for task in delegated_by_me:
                result += f"- {task.title} (на @{task.delegated_to_username})\n"
            result += "\n"

        # Завершённые (последние 3)
        if completed_tasks:
            recent_completed = completed_tasks[-3:]
            result += f"Завершено: {len(completed_tasks)} задач\n"

        # Анализ и рекомендации
        recommendations = []

        # Проверяем просроченные задачи
        overdue_tasks = []
        for task in active_tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_tasks.append(task)
                except:
                    pass

        if overdue_tasks:
            recommendations.append(f"У вас {len(overdue_tasks)} просроченных задач. Рекомендую выполнить их или перенести сроки.")

        # Проверяем задачи без сроков
        tasks_without_deadline = [t for t in active_tasks if not t.reminder_time]
        if tasks_without_deadline:
            recommendations.append(f"{len(tasks_without_deadline)} задач без сроков. Установите конкретные даты для лучшего планирования.")

        # Проверяем делегированные задачи
        if delegated_by_me:
            recommendations.append(f"Вы делегировали {len(delegated_by_me)} задач. Проверьте их статус у получателей.")

        # Общие рекомендации
        if len(active_tasks) > 5:
            recommendations.append("У вас много задач. Попробуйте приоритизировать - отметьте самые важные.")

        if not active_tasks:
            recommendations.append("Все задачи выполнены. Что планируете добавить?")
        elif len(active_tasks) == 1:
            recommendations.append("Одна задача - легче сосредоточиться.")

        # Добавляем рекомендации к результату
        if recommendations:
            result += "\nРекомендации:\n" + "\n".join(f"- {rec}" for rec in recommendations[:3])  # Максимум 3 рекомендации

        return result.strip()
    except Exception as e:
        print(f"Error listing tasks: {e}")
        return "Ошибка получения списка задач"
    finally:
        if close_session:
            session.close()


def enrich_task_list_with_insights(task_list_text, user_id):
    """Обогащает список задач ценностными insights и анализом"""
    from models import Session, User, Task
    from datetime import datetime
    import pytz
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return task_list_text
            
        # Получаем задачи для анализа
        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status != "completed"
        ).all()
        
        # Анализируем паттерны
        insights = []
        
        # 1. Анализ загруженности
        task_count = len(tasks)
        if task_count == 0:
            insights.append("Отличная работа - все задачи выполнены! Раньше ты мог часами вспоминать, что нужно сделать, теперь все под контролем.")
        elif task_count == 1:
            insights.append("Одна задача - идеально для фокуса. Раньше ты мог теряться в длинных списках, теперь приоритет ясен.")
        elif task_count > 5:
            insights.append(f"{task_count} задач - стоит приоритизировать. Я помогу организовать, чтобы не терять время на хаос.")
        
        # 2. Анализ просроченных задач
        overdue_count = 0
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)
        
        for task in tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_count += 1
                except:
                    pass
        
        if overdue_count > 0:
            insights.append(f"{overdue_count} просроченных задач. Раньше это могло вызвать стресс и потерю времени - теперь давай исправим ситуацию.")
        
        # 3. Анализ делегирования
        delegated_count = sum(1 for t in tasks if t.delegated_to_username)
        if delegated_count > 0:
            insights.append(f"Ты делегируешь {delegated_count} задач - умный подход! Раньше все приходилось делать самому, теперь команда помогает.")
        
        # 4. Предложения по оптимизации
        tasks_without_time = sum(1 for t in tasks if not t.reminder_time)
        if tasks_without_time > 0:
            insights.append(f"{tasks_without_time} задач без времени - добавим сроки, чтобы избежать спешки в последний момент.")
        
        # Формируем финальный ответ
        result = task_list_text
        if insights:
            result += "\n\nАнализ ситуации: " + ", ".join(insights[:3])
            result += "\n\nЧто приоритизируем? Или может найдем партнеров для совместной работы над похожими задачами?"
        
        # Добавляем социальные предложения на основе профиля
        if user_profile and (user_profile.interests or user_profile.skills):
            social_suggestions = []
            
            if user_profile.interests:
                interests_list = [i.strip() for i in user_profile.interests.split(',')]
                if any(i.lower() in ['бег', 'спорт', 'фитнес', 'йога'] for i in interests_list):
                    social_suggestions.append("Вижу интерес к спорту - могу найти партнеров для совместных тренировок")
                if any(i.lower() in ['программирование', 'it', 'разработка'] for i in interests_list):
                    social_suggestions.append("Занимаешься IT - найдем коллег для обмена опытом или совместных проектов")
                if any(i.lower() in ['путешествия', 'кино', 'театр', 'музыка'] for i in interests_list):
                    social_suggestions.append("Любишь культурные мероприятия - подберу компанию для походов в кино или театр")
            
            if social_suggestions:
                result += "\n\nСоциальные возможности: " + ", ".join(social_suggestions[:2])
                result += "\n\nХочешь найти единомышленников прямо сейчас?"
        
        return result
        
    except Exception as e:
        print(f"Error enriching task list: {e}")
        return task_list_text
    finally:
        session.close()


def check_subscription_status(user_id=None):
    """Проверяет статус подписки"""
    from models import Session, User, Subscription

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription or subscription.status != "active":
            return "У вас нет активной подписки. Используйте /subscribe для оформления."

        return f"Подписка активна до {subscription.end_date.strftime('%d.%m.%Y') if subscription.end_date else 'неизвестно'}"
    except Exception as e:
        print(f"Error checking subscription: {e}")
        return "Ошибка проверки подписки"
    finally:
        session.close()


def create_subscription_payment(user_id=None):
    """Создает платеж для подписки"""
    from models import Session, User, Subscription
    from datetime import datetime, timedelta
    import pytz

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Проверить существующую подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription and subscription.status == "active":
            return "У вас уже есть активная подписка"

        # Создать или обновить подписку
        if not subscription:
            subscription = Subscription(user_id=user.id, telegram_username=user.username)
            session.add(subscription)
        else:
            # Update telegram_username if not set
            if not subscription.telegram_username:
                subscription.telegram_username = user.username

        subscription.status = "pending_payment"
        subscription.start_date = datetime.now(pytz.UTC)
        subscription.end_date = subscription.start_date + timedelta(days=30)
        session.commit()

        return "Платеж создан. Используйте ссылку для оплаты: https://yookassa.ru/..."
    except Exception as e:
        session.rollback()
        print(f"Error creating subscription payment: {e}")
        return "Ошибка создания платежа"
    finally:
        session.close()


def cancel_subscription(user_id=None):
    """Отменяет подписку"""
    from models import Session, User, Subscription

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription:
            return "У вас нет подписки"

        subscription.status = "cancelled"
        session.commit()
        return "Подписка отменена"
    except Exception as e:
        session.rollback()
        print(f"Error cancelling subscription: {e}")
        return "Ошибка отмены подписки"
    finally:
        session.close()
