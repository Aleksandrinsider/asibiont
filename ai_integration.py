import requests
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY
import json
from datetime import datetime, timezone, timedelta
import re
from cryptography.fernet import Fernet
from models import User

cipher = Fernet(ENCRYPTION_KEY.encode())

def encrypt_data(data):
    if data:
        return cipher.encrypt(data.encode()).decode()
    return data

def decrypt_data(data):
    if data:
        return cipher.decrypt(data.encode()).decode()
    return data

class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return await generate_reminder(user_id, task_title)
    
    async def generate_result_check(self, user_id, task_title):
        return await generate_result_check(user_id, task_title)
    
    async def generate_proactive_message(self, user_id):
        return await generate_proactive_message(user_id)
    
    async def generate_daily_report(self, user_id):
        return await generate_daily_report(user_id)
    
    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return await generate_overdue_reminder(user_id, overdue_tasks)

def parse_relative_time(message):
    now = datetime.now(timezone.utc)
    # Паттерны для русского языка
    patterns = [
        (r'через (\d+) минут', lambda m: now + timedelta(minutes=int(m.group(1)))),
        (r'через (\d+) час', lambda m: now + timedelta(hours=int(m.group(1)))),
        (r'через (\d+) часа', lambda m: now + timedelta(hours=int(m.group(1)))),
        (r'через (\d+) часов', lambda m: now + timedelta(hours=int(m.group(1)))),
        (r'завтра в (\d{1,2}):(\d{2})', lambda m: (now + timedelta(days=1)).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)),
        (r'послезавтра в (\d{1,2}):(\d{2})', lambda m: (now + timedelta(days=2)).replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)),
    ]
    for pattern, func in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            absolute_time = func(match)
            time_str = absolute_time.strftime("%Y-%m-%d %H:%M")
            # Заменить относительное на абсолютное в сообщении
            message = re.sub(pattern, f'в {time_str}', message, flags=re.IGNORECASE)
            break
    return message

def get_system_prompt():
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_time = datetime.now(timezone.utc).strftime("%H:%M")
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    return f"""Ты дружелюбный ИИ-помощник для управления задачами в Telegram. Твоя основная роль — помогать с организацией дел: добавлять задачи, просматривать список, завершать их, устанавливать напоминания. Используй инструменты: add_task(title, description='', reminder_time=None, due_date=None) для добавления задачи, list_tasks() для просмотра списка, complete_task(task_id) для завершения по ID, set_reminder(task_id, reminder_time) для напоминаний. Также доступны социальные функции: find_partners() для поиска партнеров, update_profile(skills, interests, goals) для обновления профиля, update_user_memory(info) для сохранения информации о пользователе.

Отвечай естественно, как в живом разговоре. СТРОГО ЗАПРЕЩЕНО использовать любые списки (нумерованные или маркированные), жирный шрифт, курсив, заголовки или любое Markdown-форматирование. Никогда не используй списки, даже если кажется удобным — всегда перечисляй повествовательно, например 'у вас задачи A, B и C'. Будь вежливым, позитивным, используй эмодзи 😊. Мотивируй на продуктивность, но не навязчиво. Будь честным: если пользователь пропускает задачи или не следует плану, мягко укажи на это, чтобы помочь улучшить привычки, но не будь грубым. Не повторяйся: избегай повторения одних и тех же фраз, тем или предложений в диалоге. Фокусируйся на текущем запросе пользователя и новых аспектах. Не ссылайся на конкретные предыдущие сообщения пользователя. Отвечай естественно, без упоминания 'вы сказали' или 'в вашем сообщении'.

Текущая дата: {current_date}, время: {current_time}. 'Завтра' — {tomorrow}, 'послезавтра' — {day_after}. Автоматически добавляй задачи из фраз вроде 'Мне нужно X'. Для дедлайнов сначала проверь через list_tasks, затем установи напоминание.

Будь proactive: если пользователь говорит о планах или целях, предложи добавить задачу. Для социальных функций: интегрируй естественно в разговор. Если сообщение содержит слова, связанные с хобби, интересами, навыками, бизнесом, знакомствами или подобными темами (например, спорт, хобби, дизайн, бизнес, знакомства, программирование), немедленно вызови find_partners и включи 1-2 найденных пользователей с контактами в первый абзац ответа. Всегда используй результаты инструментов в ответе — например, если find_partners нашел единомышленников, упомяни их в разговоре. Если данных недостаточно для точных рекомендаций (например, город, время, уровень), уточни у пользователя, чтобы советы были релевантными. Также предлагай советы о релевантных событиях или планах других пользователей в том же городе, основываясь на их профилях.

Используй информацию о пользователе из памяти для персонализированных советов и предложений. Если пользователь делится предпочтениями или важной информацией, сохраняй её через update_user_memory для будущих взаимодействий. После завершения задачи уточни результат: спроси, что было сделано, как прошло, чтобы учесть в будущем планировании и мотивации.

Активно изучай пользователя: задавай вопросы о его интересах, целях, городе, текущих планах, чтобы персонализировать советы и рекомендации. Используй полученную информацию для поиска партнеров, предложений задач и мотивации. Например, если узнал о хобби, предложи связанные задачи или единомышленников; если о городе, предлагай локальные события. Всегда сохраняй новую информацию через update_user_memory.

Вовлекай пользователя в диалог: задавай открытые вопросы, чтобы продолжить разговор, не предлагай готовые примеры в виде списков или перечислений. Вместо 'Хотите добавить задачу A, B или C?' скажи 'Расскажите, что у вас на уме сегодня? Может, что-то нужно сделать?' или 'Что планируете на ближайшее время?'. Никогда не предлагай варианты в форме 'A или B или C' — всегда задавай вопросы. Делай ответы короткими, но вовлекающими, чтобы пользователь чувствовал себя в разговоре, а не получал инструкции. Если пользователь просто приветствует, ответь тепло и спроси о планах, без списков или примеров.

Будь социально ориентированным: если в памяти есть информация о предыдущих контактах или взаимодействиях с другими пользователями, предлагай продолжить общение или присоединиться к их проектам. Например, 'Вы недавно связывались с @user по дизайну, он сегодня работает над проектом — не хотите присоединиться?' или 'Помните, вы обсуждали сайт с @partner, может, стоит написать ему?'. Если пользователь просит не показывать кого-то (например, 'не показывать @user'), сохрани это в памяти через update_user_memory, чтобы в будущем не предлагать этого пользователя.

ВАЖНО: Всегда вызывай соответствующий инструмент для выполнения действий, не симулируй ответы текстом. Если пользователь просит добавить задачу, завершить, обновить профиль, найти партнеров, сохранить в память и т.д., ОБЯЗАТЕЛЬНО сначала вызови инструмент (add_task, complete_task, update_profile и т.д.), затем используй его результат в ответе. Не говори 'я добавил' или 'я обновил', если не вызвал инструмент — сначала инструмент, потом ответ на основе результата. Для редактирования задач используй edit_task, для удаления — delete_task, для приоритетов — set_priority, для деталей — get_task_details, для напоминаний — set_reminder. Если запрос подразумевает обновление планов в профиле, вызови update_profile с current_plans.

Строго запрещено использовать нумерованные или маркированные списки (1., 2., -, •). Вместо этого перечисляй повествовательно: 'Во-первых, расскажите о городе. Во-вторых, поделитесь планами. В-третьих, уточните навыки.' Всегда следуй этому, даже если кажется удобным. НИКОГДА не предлагай примеры в виде списков или перечислений — всегда задавай открытые вопросы. Не говори 'Можете сделать A, B или C' — вместо этого спроси 'Что у вас на уме?' или 'Расскажите о своих планах?'.

Дополнительные запросы: Если пользователь хочет изменить задачу ('измени задачу X на Y'), вызови edit_task. Для удаления ('удали задачу X') — delete_task. Для приоритета ('сделай задачу высокой') — set_priority. Для деталей ('покажи задачу X') — get_task_details. Для новых напоминаний — set_reminder. Если делится планами ('сегодня планирую Z'), сохрани через update_profile(current_plans=Z). Для мотивации: если много невыполненных задач, мягко предложи завершить. Для справки: расскажи о функциях повествовательно, без списков."""

def add_task(title, description="", reminder_time=None, due_date=None, user_id=None):
    from models import Session, Task, User
    from datetime import datetime
    session = Session()
    # Проверить, существует ли пользователь
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()
    task = Task(user_id=user.id, title=title, description=description)
    if reminder_time:
        try:
            task.reminder_time = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            pass  # Игнорировать неверный формат
    if due_date:
        try:
            task.due_date = datetime.strptime(due_date, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    session.add(task)
    session.commit()
    task_id = task.id
    session.close()
    return f"Задача добавлена: {title} (ID: {task_id})"

def list_tasks(user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    if tasks:
        task_descriptions = []
        for t in tasks:
            desc = f"Задача '{t.title}' со статусом {t.status}"
            if t.due_date:
                desc += f", дедлайн {t.due_date.strftime('%Y-%m-%d %H:%M')}"
            if t.reminder_time:
                desc += f", напоминание {t.reminder_time.strftime('%Y-%m-%d %H:%M')}"
            task_descriptions.append(desc)
        return f"У вас {len(tasks)} задач: " + "; ".join(task_descriptions) + "."
    return "У вас нет задач."

def complete_task(task_id, user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        task.status = "completed"
        session.commit()
        result = f"Задача выполнена: {task.title}"
    else:
        result = "Задача не найдена."
    session.close()
    return result

def set_reminder(task_id, reminder_time, user_id=None):
    from models import Session, Task
    from datetime import datetime
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        try:
            reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            task.reminder_time = reminder_time_parsed
            session.commit()
            result = f"Напоминание установлено для {task.title} на {reminder_time_parsed}."
        except ValueError:
            result = "Неверный формат времени."
    else:
        result = "Задача не найдена."
    session.close()
    return result

def update_user_memory(info, user_id=None):
    from models import Session, User
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        # Дешифруем существующую память
        existing_decrypted = ""
        if user.memory:
            try:
                existing_decrypted = decrypt_data(user.memory)
            except:
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
        result = "Информация сохранена в память."
    else:
        result = "Пользователь не найден."
    session.close()
    return result

def edit_task(task_id, title=None, description=None, user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        if title:
            task.title = title
        if description:
            task.description = description
        session.commit()
        result = f"Задача обновлена: {task.title}"
    else:
        result = "Задача не найдена."
    session.close()
    return result

def delete_task(task_id, user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        session.delete(task)
        session.commit()
        result = f"Задача удалена: {task.title}"
    else:
        result = "Задача не найдена."
    session.close()
    return result

def set_priority(task_id, priority, user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        if priority in ['high', 'medium', 'low']:
            task.priority = priority
            session.commit()
            result = f"Приоритет задачи '{task.title}' установлен на {priority}."
        else:
            result = "Неверный приоритет. Используйте high, medium или low."
    else:
        result = "Задача не найдена."
    session.close()
    return result

def get_task_details(task_id, user_id=None):
    from models import Session, Task
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    session.close()
    if task:
        details = f"ID: {task.id}\nНазвание: {task.title}\nОписание: {task.description or 'Нет'}\nСтатус: {task.status}\nПриоритет: {task.priority}\nДедлайн: {task.due_date}\nНапоминание: {task.reminder_time}\nСоздано: {task.created_at}"
        return details
    return "Задача не найдена."

def find_partners(user_id=None):
    from models import Session, UserProfile, User
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    # Остальной код...
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    # Получить память для исключения заблокированных
    blocked = []
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            # Ищем паттерны вроде "не показывать @user" или "заблокировать @user"
            import re
            matches = re.findall(r'не показывать @(\w+)|заблокировать @(\w+)', decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])
        except:
            pass
    partners = []
    tips = []
    if user_profile:
        # Сначала фильтруем по городу, если указан
        if user_profile.city:
            city_profiles = [p for p in profiles if p.city and p.city.lower() == user_profile.city.lower()]
            if city_profiles:
                profiles = city_profiles  # Используем только профили из того же города
        for p in profiles:
            # Исключаем заблокированных и себя
            if p.contact_info in blocked or any('@' + b in p.contact_info for b in blocked) or p.contact_info == f"user{user_id}":
                continue
            if user_profile.skills and p.skills and any(skill.strip().lower() in p.skills.lower() for skill in user_profile.skills.split(",")):
                partners.append(p)
            elif user_profile.interests and p.interests and any(interest.strip().lower() in p.interests.lower() for interest in user_profile.interests.split(",")):
                partners.append(p)
            # Проверяем планы на релевантность
            if p.current_plans and user_profile.interests:
                for interest in user_profile.interests.split(","):
                    interest_words = interest.strip().lower().split()
                    if any(word in p.current_plans.lower() for word in interest_words):
                        tips.append(f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} — это может быть интересно для тебя с твоими интересами в {interest.strip()}.")
                        break
    else:
        # Если профиля нет, вернуть тестовых партнеров для демонстрации
        partners = profiles[:2] if profiles else []
    session.close()
    response = ""
    if partners:
        response += "Есть единомышленники с похожими интересами: "
        for p in partners[:2]:
            response += f"@{p.contact_info} (интересуется {p.interests}), "
        response = response.rstrip(", ") + ". "
    if tips:
        response += " ".join(tips[:2])
    if not response:
        response = "Единомышленники не найдены. Попробуйте обновить профиль."
    return response

def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, user_id=None):
    from models import Session, User, UserProfile
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
    profile.skills = skills if skills else profile.skills
    profile.interests = interests if interests else profile.interests
    profile.goals = goals if goals else profile.goals
    profile.city = city if city else profile.city
    profile.current_plans = current_plans if current_plans else profile.current_plans
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.close()
    return "Профиль обновлён!"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить новую задачу с обязательным временем напоминания и опциональным дедлайном",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Описание задачи"},
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM"},
                    "due_date": {"type": "string", "description": "Дедлайн в формате YYYY-MM-DD HH:MM, опционально"}
                },
                "required": ["title", "reminder_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать список задач",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Завершить задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"]
            }
        }
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
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM"}
                },
                "required": ["task_id", "reminder_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Сохранить информацию о пользователе в долговременную память для персонализации",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {"type": "string", "description": "Информация для сохранения, например предпочтения, привычки, цели"}
                },
                "required": ["info"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить название или описание задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "title": {"type": "string", "description": "Новое название, опционально"},
                    "description": {"type": "string", "description": "Новое описание, опционально"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Удалить задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"]
            }
        }
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
                    "priority": {"type": "string", "description": "Приоритет: high, medium, low"}
                },
                "required": ["task_id", "priority"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Получить полную информацию о задаче",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти потенциальных единомышленников на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя с навыками, интересами, целями, городом и текущими планами",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки пользователя, разделенные запятыми"},
                    "interests": {"type": "string", "description": "Интересы пользователя, разделенные запятыми"},
                    "goals": {"type": "string", "description": "Цели пользователя"},
                    "city": {"type": "string", "description": "Город пользователя, опционально"},
                    "current_plans": {"type": "string", "description": "Текущие планы или события пользователя, опционально"}
                }
            }
        }
    }
]

def chat_with_ai(message, context=None, user_id=None):
    try:
        # Get user memory and all tasks for extended context
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""  # If decryption fails, skip
            # Get all tasks for extended memory
            all_tasks = list_tasks(user_id=user_id)
            user_memory += f"\nВсе задачи пользователя: {all_tasks}"
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        messages = [{"role": "system", "content": get_system_prompt() + user_memory}]
        if context:
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        message = parse_relative_time(message)
        messages.append({"role": "user", "content": message})
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto"
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            message_response = result["choices"][0]["message"]
            if "tool_calls" in message_response:
                # Выполнить tool calls
                tool_messages = []
                # Добавить assistant message с tool_calls
                messages.append(message_response)
                for tool_call in message_response["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    args = json.loads(tool_call["function"]["arguments"])
                    if func_name == "add_task":
                        result_text = add_task(**args, user_id=user_id)
                    elif func_name == "list_tasks":
                        result_text = list_tasks(user_id=user_id)
                    elif func_name == "complete_task":
                        result_text = complete_task(**args, user_id=user_id)
                    elif func_name == "set_reminder":
                        result_text = set_reminder(**args, user_id=user_id)
                    elif func_name == "update_user_memory":
                        result_text = update_user_memory(**args, user_id=user_id)
                    elif func_name == "edit_task":
                        result_text = edit_task(**args, user_id=user_id)
                    elif func_name == "delete_task":
                        result_text = delete_task(**args, user_id=user_id)
                    elif func_name == "set_priority":
                        result_text = set_priority(**args, user_id=user_id)
                    elif func_name == "get_task_details":
                        result_text = get_task_details(**args, user_id=user_id)
                    elif func_name == "find_partners":
                        result_text = find_partners(user_id=user_id)
                    elif func_name == "update_profile":
                        result_text = update_profile(**args, user_id=user_id)
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": result_text
                    })
                # Отправить результат tools обратно ИИ для финального ответа
                messages.extend(tool_messages)
                data = {
                    "model": "deepseek-chat",
                    "messages": messages
                }
                response = requests.post(url, headers=headers, json=data)
                if response.status_code == 200:
                    final_message = response.json()["choices"][0]["message"]
                    return final_message["content"]
                else:
                    return "Извините, не могу ответить сейчас."
            else:
                return message_response["content"]
        else:
            return "Извините, не могу ответить сейчас."
    except Exception as e:
        print(f"Error in chat_with_ai: {e}")
        return "Извините, произошла ошибка."

async def generate_reminder(user_id, task_title):
    """Генерирует текст напоминания о задаче"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        system_prompt = f"""Ты — строгий ассистент по управлению задачами. Создай краткое напоминание о задаче '{task_title}'.
Будь мотивирующим, но строгим. Напомни о важности выполнения задачи вовремя. Если пользователь часто откладывает, укажи на это.
Не используй форматирование, будь краток (1-2 предложения).{user_memory}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о задаче: {task_title}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Не удалось сгенерировать напоминание. Попробуйте позже."
    except Exception as e:
        print(f"Error in generate_reminder: {e}")
        return f"Напоминание: {task_title}. Выполните задачу вовремя!"

async def generate_result_check(user_id, task_title):
    """Генерирует вопрос о результате выполнения задачи"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        system_prompt = f"""Ты — строгий ассистент по управлению задачами. Задай вопрос о результате выполнения задачи '{task_title}'.
Спроси: сколько времени заняло, были ли сложности, что можно улучшить. Будь строгим, если задача была просрочена.
Не используй форматирование, будь краток.{user_memory}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Спроси о результате задачи: {task_title}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Не удалось сгенерировать вопрос о результате. Попробуйте позже."
    except Exception as e:
        print(f"Error in generate_result_check: {e}")
        return f"Задача '{task_title}' выполнена? Сколько времени заняло? Были сложности?"

async def generate_proactive_message(user_id):
    """Генерирует проактивное сообщение, если нет задач на ближайший час"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        system_prompt = f"""Ты — строгий ассистент по управлению задачами. Создай проактивное сообщение для пользователя, у которого нет задач на ближайший час.
Предложи добавить новую задачу или проанализировать текущие. Будь мотивирующим и строгим, напомни о важности продуктивности.
Не используй форматирование, будь краток (1-2 предложения).{user_memory}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Создай проактивное сообщение"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Не удалось сгенерировать проактивное сообщение. Попробуйте позже."
    except Exception as e:
        print(f"Error in generate_proactive_message: {e}")
        return "У вас нет задач на ближайший час. Хотите добавить новую задачу для поддержания продуктивности?"

async def generate_daily_report(user_id):
    """Генерирует ежедневный отчет о задачах"""
    try:
        # Получить задачи пользователя
        from models import Session, Task
        session = Session()
        tasks = session.query(Task).filter_by(user_id=user_id).all()
        session.close()
        
        completed = [t for t in tasks if t.status == 'completed']
        pending = [t for t in tasks if t.status in ['pending', 'in_progress']]
        
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        system_prompt = f"""Ты — ассистент по управлению задачами. Создай краткий ежедневный отчет на основе задач пользователя.
Выполнено задач: {len(completed)}
Ожидающих задач: {len(pending)}
Будь позитивным, мотивирующим, без форматирования.{user_memory}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Не удалось сгенерировать ежедневный отчет. Попробуйте позже."
    except Exception as e:
        print(f"Error in generate_daily_report: {e}")
        return "Не удалось сгенерировать отчет."

async def generate_overdue_reminder(user_id, overdue_tasks):
    """Генерирует напоминание о просроченных задачах"""
    try:
        task_titles = [t.title for t in overdue_tasks]
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        system_prompt = f"""Ты — строгий ассистент по управлению задачами. Создай напоминание о просроченных задачах: {', '.join(task_titles)}.
Будь строгим, мотивирующим, напомни о последствиях. Не используй форматирование.{user_memory}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о просроченных задачах: {', '.join(task_titles)}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Не удалось сгенерировать напоминание о просроченных задачах. Попробуйте позже."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "У вас есть просроченные задачи. Выполните их!"