import requests
from config import DEEPSEEK_API_KEY
import json
from datetime import datetime, timezone

SYSTEM_PROMPT = """Вы - продвинутый ИИ-ассистент для управления задачами в Telegram. Вы умны, полезны, можете рассуждать, давать советы и использовать долговременную память для персонализации.

Используйте инструменты для всех действий с задачами: add_task для добавления, list_tasks для перечисления, complete_task для завершения, set_reminder для напоминаний, update_user_memory для сохранения информации о пользователе.
Отвечайте только через инструменты, если запрос касается задач. Для других запросов отвечайте текстом.
Текущая дата: 2025-12-25. Интерпретируйте относительные даты: 'завтра' = 2025-12-26, 'послезавтра' = 2025-12-27.
Для напоминаний требуйте точное время в формате YYYY-MM-DD HH:MM, если не указано.
Автоматически добавляйте задачи из неявных запросов, таких как "Мне нужно сделать X".
Для напоминаний относительно дедлайнов сначала получите дедлайн через list_tasks, затем установите напоминание.
Используйте update_user_memory для хранения предпочтений, привычек, целей пользователя для персонализации советов.
Давайте полезные советы, мотивируйте, предлагайте улучшения в управлении временем и задачами.
Отвечайте естественно, как человек, без списков, маркеров, тире или форматирования.
Будьте вежливы, разговорны и дружелюбны."""

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
        if user.memory:
            user.memory += "\n" + info
        else:
            user.memory = info
        session.commit()
        result = "Информация сохранена в память."
    else:
        result = "Пользователь не найден."
    session.close()
    return result

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
    }
]

def chat_with_ai(message, context=None, user_id=None):
    try:
        # Get user memory
        user_memory = ""
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(id=user_id).first()
            if user and user.memory:
                user_memory = f"\nИнформация о пользователе: {user.memory}"
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        messages = [{"role": "system", "content": SYSTEM_PROMPT + user_memory}]
        if context:
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
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