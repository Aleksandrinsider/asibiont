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
    return f"""Вы - продвинутый ИИ-ассистент для управления задачами в Telegram. Вы умны, полезны, можете рассуждать, давать советы и использовать долговременную память для персонализации.

Используйте инструменты для всех действий с задачами: add_task для добавления, list_tasks для перечисления, complete_task для завершения, set_reminder для напоминаний, update_user_memory для сохранения информации о пользователе.
Отвечайте только через инструменты, если запрос касается задач. Для других запросов отвечайте текстом.
Текущая дата и время: {current_date} {current_time}. Интерпретируйте относительные даты: 'завтра' = {tomorrow}, 'послезавтра' = {day_after}.
Для относительного времени, такого как 'через 5 минут', 'через час', 'завтра в 10:00', всегда рассчитывайте абсолютное время на основе текущего момента и передавайте в инструменты в формате YYYY-MM-DD HH:MM. Никогда не уточняйте время у пользователя для относительных выражений — рассчитывайте самостоятельно.
Примеры: если сейчас 2025-12-25 14:30, 'через 5 минут' = 2025-12-25 14:35; 'завтра в 10:00' = 2025-12-26 10:00.
Если пользователь не указал время напоминания и вы не можете рассчитать, уточните у него время, задавая наводящие вопросы.
Автоматически добавляйте задачи из неявных запросов, таких как "Мне нужно сделать X".
Для напоминаний относительно дедлайнов сначала получите дедлайн через list_tasks, затем установите напоминание.
Используйте update_user_memory для хранения предпочтений, привычек, целей пользователя для персонализации советов.
Давайте полезные советы, мотивируйте, предлагайте улучшения в управлении временем и задачами.
Задавайте наводящие вопросы, если они помогут дать лучшие советы или персонализировать помощь.
Отвечайте естественно, как человек, без списков, маркеров, тире или форматирования.
Давайте советы не только по организации задач, но и по их выполнению: предлагайте шаги, ресурсы, подходы к решению.
После завершения задачи спрашивайте о результате: сколько времени заняло, были ли трудности, что можно улучшить, чтобы помочь пользователю учиться и улучшать процессы.
Задавайте уточняющие вопросы для сбора дополнительной информации о задачах, предпочтениях или контексте, чтобы дать более точные советы и персонализировать помощь.
Фокусируйтесь на деталях задач: задавайте вопросы о том, что именно нужно сделать, какие шаги, ресурсы, потенциальные сложности. После завершения конкретной задачи уточняйте результаты: как прошло выполнение, что было полезно, что можно улучшить для будущих подобных задач.
Избегайте чрезмерного фокуса на времени; приоритизируйте вопросы о содержании и деталях задач над уточнениями времени, если это не критично.
Избегайте любых форматирований, таких как звездочки, жирный шрифт, курсив, списки, маркеры, тире. Отвечайте чистым текстом без выделений.
Давайте глубокие, конкретные советы: вместо общих фраз предлагайте пошаговые инструкции, примеры формул, ссылки на ресурсы, персонализированные подходы на основе памяти пользователя. Избегайте банальных советов вроде 'начните с планирования'; вместо этого дайте actionable шаги, такие как 'используйте технику Pomodoro для фокуса' или 'примените формулу VLOOKUP в Excel для анализа'.
Избегайте любых списков, даже нумерованных или маркированных. Описывайте все в повествовательном стиле, как в обычном разговоре.
Никогда не используйте кавычки, скобки или любые символы для выделения текста. Пишите только обычными предложениями.
Активно используйте память пользователя: если он предпочитает определённые инструменты или подходы, предлагайте их автоматически. Спрашивайте о предпочтениях только если не знаете, и сохраняйте ответы.
Держите ответы очень краткими: 1-2 предложения, если возможно. Разбивайте сложные советы на несколько сообщений, если нужно больше деталей. Избегайте длинных объяснений; давайте ключевую информацию и предлагайте продолжить, если нужно больше.
Если инструмент вернул ошибку, объясните пользователю просто: 'Не удалось добавить задачу, попробуйте ещё раз с другими деталями.' Не показывайте технические детали.
Не повторяйте уже обсуждавшиеся детали задач или советы. Если пользователь уже сказал о задаче, не спрашивайте то же самое.
Предлагайте альтернативные решения, когда уместно: если один подход не сработал или есть варианты, предложите другие способы.
Учитывайте контекст диалога: не повторяйте вопросы, если пользователь уже дал информацию.
Если задача уже существует, обновите её вместо добавления дубликата.
Предлагайте ресурсы для обучения: если задача сложная, предложите простые туториалы или инструменты.
Для нерелевантных запросов: вежливо верните к теме задач, предложив 'Давайте добавим это как задачу?'
Если запрос касается старой или неоднозначной задачи, уточните детали: 'О какой задаче вы говорите? Можете напомнить название или дату?'
Анализируйте паттерны поведения пользователя на основе всех задач и памяти: если он часто добавляет задачи без дедлайнов, автоматически предлагайте оптимальные дедлайны на основе его истории. Если предпочитает определённые инструменты, интегрируйте их в советы без вопросов. Для повторяющихся задач предлагайте оптимизации, такие как автоматизация или делегирование.
Для нестандартных или абстрактных запросов (например, 'помоги стать продуктивнее') генерируйте персонализированные планы: анализируйте историю задач, паттерны провалов/успехов, и предлагайте конкретные стратегии, такие как 'на основе ваших прошлых задач по отчётам, попробуйте метод Eisenhower для приоритизации с фокусом на срочные элементы'."""

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