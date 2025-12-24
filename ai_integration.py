import requests
from config import DEEPSEEK_API_KEY
import json

SYSTEM_PROMPT = "Вы - полезный ИИ-ассистент для управления задачами в Telegram. Вы можете добавлять, перечислять, завершать задачи, устанавливать напоминания и общаться с пользователями. Помните контекст, будьте вежливы и помогайте с запросами, связанными с задачами. Используйте инструменты для выполнения действий."

def add_task(title, description="", user_id=None):
    from models import Session, Task
    session = Session()
    task = Task(user_id=user_id, title=title, description=description)
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
        task_list = "\n".join([f"{t.id}: {t.title} - {t.status}" for t in tasks])
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

def set_reminder(task_id, time_str, user_id=None):
    from models import Session, Task
    from datetime import datetime
    session = Session()
    task = session.query(Task).filter_by(id=int(task_id), user_id=user_id).first()
    if task:
        try:
            reminder_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            task.reminder_time = reminder_time
            session.commit()
            result = f"Напоминание установлено для {task.title} на {reminder_time}."
        except ValueError:
            result = "Неверный формат времени."
    else:
        result = "Задача не найдена."
    session.close()
    return result

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить новую задачу",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Описание задачи"}
                },
                "required": ["title"]
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
                    "time_str": {"type": "string", "description": "Время в формате YYYY-MM-DD HH:MM"}
                },
                "required": ["task_id", "time_str"]
            }
        }
    }
]

def chat_with_ai(message, context=None, user_id=None):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "assistant", "content": context})
    messages.append({"role": "user", "content": message})
    
    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "tools": TOOLS
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        result = response.json()
        message = result["choices"][0]["message"]
        if "tool_calls" in message:
            # Выполнить tool calls
            tool_messages = []
            for tool_call in message["tool_calls"]:
                func_name = tool_call["function"]["name"]
                args = json.loads(tool_call["function"]["arguments"])
                if func_name == "add_task":
                    result = add_task(**args, user_id=user_id)
                elif func_name == "list_tasks":
                    result = list_tasks(user_id=user_id)
                elif func_name == "complete_task":
                    result = complete_task(**args, user_id=user_id)
                elif func_name == "set_reminder":
                    result = set_reminder(**args, user_id=user_id)
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result
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
            return message["content"]
    else:
        return "Извините, не могу ответить сейчас."