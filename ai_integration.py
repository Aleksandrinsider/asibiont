import requests
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY
import json
from datetime import datetime, timezone, timedelta
import re
from cryptography.fernet import Fernet

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
    return f"""Вы - ИИ-ассистент для управления задачами в Telegram. Следуйте этим правилам строго:

1. Используйте инструменты для всех действий с задачами: add_task для добавления, list_tasks для перечисления, complete_task для завершения, set_reminder для напоминаний.
2. Отвечайте только через инструменты, если запрос касается задач. Для других запросов отвечайте текстом.
3. Текущая дата и время: {current_date} {current_time}. Интерпретируйте относительные даты: 'завтра' = {tomorrow}, 'послезавтра' = {day_after}.
4. Для напоминаний требуйте точное время в формате YYYY-MM-DD HH:MM, если не указано.
5. Автоматически добавляйте задачи из неявных запросов, таких как "Мне нужно сделать X".
6. Для напоминаний относительно дедлайнов сначала получите дедлайн через list_tasks, затем установите напоминание.
7. Отвечайте естественно, как человек, без списков, маркеров, тире или форматирования.
8. Будьте вежливы, разговорны и дружелюбны.
9. Не ссылайтесь на предыдущие сообщения или повторения. Отвечайте только на текущий запрос."""

def add_task(title, description="", reminder_time=None, due_date=None, user_id=None):
    from models import Session, Task, User
    from datetime import datetime
    session = Session()
    # Проверить, существует ли пользователь
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        user = User(id=user_id, telegram_id=user_id)  # Предполагаем, что user_id = telegram_id
        session.add(user)
        session.commit()
    task = Task(user_id=user_id, title=title, description=description)
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
    tasks = session.query(Task).filter_by(user_id=user_id).all()
    session.close()
    if tasks:
        task_list = "\n".join([f"{t.id}: {t.title} - {t.status} - Дедлайн: {t.due_date} - Напоминание: {t.reminder_time}" for t in tasks])
        return f"Ваши задачи:\n{task_list}"
    return "У вас нет задач."

def complete_task(task_id, user_id=None):
    from models import Session, Task
    session = Session()
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
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
    user = session.query(User).filter_by(id=user_id).first()
    if user:
        encrypted_info = encrypt_data(info)
        if user.memory:
            user.memory += "\n" + encrypted_info
        else:
            user.memory = encrypted_info
        session.commit()
        result = "Информация сохранена в память."
    else:
        result = "Пользователь не найден."
    session.close()
    return result

def edit_task(task_id, title=None, description=None, user_id=None):
    from models import Session, Task
    session = Session()
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
    session.close()
    if task:
        details = f"ID: {task.id}\nНазвание: {task.title}\nОписание: {task.description or 'Нет'}\nСтатус: {task.status}\nПриоритет: {task.priority}\nДедлайн: {task.due_date}\nНапоминание: {task.reminder_time}\nСоздано: {task.created_at}"
        return details
    return "Задача не найдена."

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
    }
]

def chat_with_ai(message, context=None, user_id=None):
    try:
        # Get user memory and all tasks for extended context
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