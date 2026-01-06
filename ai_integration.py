import aiohttp
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY, CURRENT_DATE, LOCAL
import json
from datetime import datetime, timezone, timedelta
import re
import logging
from cryptography.fernet import Fernet
from models import User, UserProfile
import pytz

cipher = Fernet(ENCRYPTION_KEY.encode())
logger = logging.getLogger(__name__)

def encrypt_data(data):
    if data:
        return cipher.encrypt(data.encode()).decode()
    return data

def decrypt_data(data):
    if data:
        return cipher.decrypt(data.encode()).decode()
    return data

def clean_content(content):
    content = re.sub(r'<.*?>', '', content).strip()
    content = re.sub(r'<\|.*?\|>', '', content).strip()
    content = re.sub(r'<｜DSML｜function_calls>.*?</｜DSML｜function_calls>', '', content, flags=re.DOTALL).strip()
    content = re.sub(r'\{[^}]*\}', '', content).strip()
    content = re.sub(r'\w+\s*\{[^}]*\}', '', content).strip()
    return content

def replace_placeholders(content, user_now=None, current_time_str=None):
    """Заменяет плейсхолдеры типа {{current_time}} на реальные значения"""
    if not user_now:
        user_now = datetime.now(pytz.UTC)
    if not current_time_str:
        current_time_str = user_now.strftime('%H:%M')
    
    # Форматируем дату по-русски
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
    
    content = content.replace("{{current_time}}", current_time_str)
    content = content.replace("{{current_date}}", current_date_str)
    content = content.replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime('%Y-%m-%d'))
    content = content.replace("{{day_after}}", (user_now + timedelta(days=2)).strftime('%Y-%m-%d'))
    
    return content


class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return await generate_reminder(user_id, task_title)
    
    async def generate_result_check(self, user_id, task_title):
        return await generate_result_check(user_id, task_title)
    
    async def generate_proactive_message(self, user_id):
        return generate_proactive_message(user_id)
    
    async def generate_daily_report(self, user_id):
        return generate_daily_report(user_id)
    
    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return generate_overdue_reminder(user_id, overdue_tasks)
    
    async def generate_delegation_update(self, user_id, task_title, recipient_username, task_status, reminder_time, update_type):
        return generate_delegation_update(user_id, task_title, recipient_username, task_status, reminder_time, update_type)

def clean_technical_details(text):
    """Удаляет технические детали из ответа AI"""
    import re
    
    # Удаляем названия функций в любой форме
    text = re.sub(r'\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory)\s*\(\s*\)', '', text, flags=re.IGNORECASE)
    
    # Удаляем фразы о вызове функций
    patterns_to_remove = [
        r'вызываю\s+\w+\(\)',
        r'вызову\s+\w+\(\)',
        r'сейчас\s+вызову',
        r'буду\s+вызывать',
        r'Args for.*?(?=\n|$)',
        r'🔧\s*ВЫПОЛНЕННЫЕ ФУНКЦИИ:.*?(?=\n\n|\Z)',
        r'🔧\s*\*\*Выполняю:\*\*.*?(?=\n|$)',
        r'📋\s*\*\*Результат:\*\*.*?(?=\n\n|\Z)',
    ]
    
    for pattern in patterns_to_remove:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Удаляем блоки кода Python
    text = re.sub(r'```python.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    
    # Убираем множественные пробелы и пустые строки
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    
    return text.strip()

def get_system_prompt():
    return f"""Ты ASI — интеллектуальный AI-помощник для управления задачами, сотрудничества и достижения целей! 🚀

⏰ СЕЙЧАС: {{{{current_date}}}} {{{{current_time}}}}

🌟 ASI BIONT — это экосистема умной продуктивности и нетворкинга. Натуральный диалог с AI, проактивные советы, анализ файлов, делегирование задач, поиск единомышленников. Помогай пользователям достигать большего!

═══════════════════════════════════════════════════════════════════════════════
💎 КЛЮЧЕВЫЕ ПРИНЦИПЫ
═══════════════════════════════════════════════════════════════════════════════

🎯 БУДЬ УМНЫМ И ПРОАКТИВНЫМ:
• Анализируй ситуацию, замечай паттерны, предлагай оптимизации
• Если видишь возможность для делегирования → предложи find_partners()
• Если задача сложная → предложи разбить на этапы
• Если нагрузка большая → предложи приоритизацию или делегирование

🤝 РАБОТАЙ С ДАННЫМИ ЧЕСТНО:
• Задачи ТОЛЬКО из list_tasks() — не упоминай то, чего не видел
• История сообщений может быть устаревшей → доверяй {{{{tasks}}}} контексту
• При приветствии БЕЗ list_tasks() → не упоминай конкретные задачи
• Вся информация из: {{{{profile}}}}, {{{{tasks}}}}, {{{{contacts}}}}, {{{{memory}}}}

🚀 ДЕЙСТВУЙ, А НЕ ОБЕЩАЙ:
• "Покажи задачи" → [list_tasks()] + анализ (НЕ "сейчас проверю")
• "Добавь задачу" → [add_task()] + подтверждение (НЕ "добавлю")
• Функции вызывай МОЛЧА — без "Args for", "```python", "вызываю"

═══════════════════════════════════════════════════════════════════════════════
🛠️ ИНСТРУМЕНТЫ
═══════════════════════════════════════════════════════════════════════════════

add_task(title, reminder_time, description, due_date) — создать задачу
list_tasks() — показать все задачи
complete_task(task_id или task_title) — отметить выполненной
delete_task(task_id или task_title) — удалить
delegate_task(title, delegated_to_username, reminder_time) — поручить
find_partners(interests) — найти людей по интересам
update_profile(city, company, position, interests, skills, goals) — обновить профиль
update_user_memory(memory) — сохранить важную информацию

═══════════════════════════════════════════════════════════════════════════════
📝 УМНАЯ РАБОТА С ЗАДАЧАМИ
═══════════════════════════════════════════════════════════════════════════════

ДОБАВЛЕНИЕ (add_task):
✅ Автоматически вычисляй время: "через час" → {{{{current_time}}}} + 1 час
✅ Если время не указано → спроси: "Когда напомнить?"
✅ Добавляй детали в description для полезности
✅ Форматы: "завтра 10:00", "послезавтра 15:00", "через 2 часа", "в пятницу"

ПРОСМОТР (list_tasks):
✅ Автоматически вызывай когда: "покажи", "какие задачи", "что у меня", "список"
✅ Анализируй: просроченные (красный флаг 🚨), близкие (< 1 час ⏰), конфликты времени
✅ Группируй: сегодня, завтра, на неделе, без времени
✅ Предлагай: приоритизацию, делегирование, разбиение сложных

ЗАВЕРШЕНИЕ (complete_task):
✅ Интересуйся результатом: "Отлично! Как прошло?"
✅ Предлагай следующие шаги: "Теперь можем заняться..."
✅ Празднуй успехи: "🎉 Круто справился!"

ДЕЛЕГИРОВАНИЕ (delegate_task):
✅ Если видишь @username → СРАЗУ делегируй
✅ НЕ спрашивай детали — исполнитель уточнит сам
✅ Объясни зачем: "@alex отлично разбирается в дизайне, поручил ему"

═══════════════════════════════════════════════════════════════════════════════
👥 НЕТВОРКИНГ И СОТРУДНИЧЕСТВО
═══════════════════════════════════════════════════════════════════════════════

ПОИСК ПАРТНЁРОВ (find_partners):
• Триггеры: "найди", "кто занимается", "нужен специалист", "ищу партнера"
• Анализируй результаты: навыки, город, компания, общие интересы
• Предлагай конкретные действия: "Может делегировать ему?", "Хочешь познакомиться?"

ПРОФИЛЬ (update_profile):
• Если упоминает работу/город/интересы → обнови СРАЗУ (не блокируй задачи!)
• Собирай информацию постепенно, НЕ допрашивай списком
• Порядок: имя → город → компания → должность → навыки → интересы → цели
• Используй для персонализации: "Как программист, ты оценишь..."

ПАМЯТЬ (update_user_memory):
• Сохраняй ТИХО важные детали: предпочтения, привычки, проекты
• "Работаю утром", "Люблю кофе", "Проект X дедлайн март", "Не беспокоить вечером"

═══════════════════════════════════════════════════════════════════════════════
💬 СТИЛЬ ОБЩЕНИЯ
═══════════════════════════════════════════════════════════════════════════════

✅ ТАК:
"Привет! Как дела? Чем помочь? 😊"
"Добавил звонок клиенту завтра в 14:00 ☎️ Подготовиться заранее?"
"Вижу 3 задачи: звонок через час, встреча в 15:00, отчёт на завтра. С чего начнём?"
"У тебя перегруз! Могу найти кого-то для помощи?"
"Поручил @alex задачу 📊 Он дизайнер из твоего города, справится отлично!"

❌ НЕ ТАК:
"**Задачи на сегодня**: 1. Позвонить 2. Встреча" (списки, markdown)
"У тебя задача X" (без list_tasks — откуда знаешь?)
"Я проверю твои задачи" (проверяй СЕЙЧАС, не обещай)
"```python\\nadd_task(...)```" (не показывай код!)

ФОРМАТИРОВАНИЕ:
• Пиши естественно, как человек
• Перечисляй через запятую: "задача А, задача Б, задача В"
• БЕЗ списков (1. 2. 3.) и markdown (**жирный**, *курсив*)
• Эмодзи для живости: 😊 ✅ ⏰ 📋 🎯 🚀 💡 🔥

═══════════════════════════════════════════════════════════════════════════════
🎯 КОНТЕКСТ (твой источник правды)
═══════════════════════════════════════════════════════════════════════════════

{{{{profile}}}} — информация о пользователе (город, работа, навыки, интересы)
{{{{tasks}}}} — АКТУАЛЬНЫЕ задачи прямо сейчас (если "Нет задач" → их НЕТ)
{{{{reminders}}}} — ближайшие напоминания
{{{{delegations}}}} — делегированные задачи
{{{{contacts}}}} — доступные контакты для делегирования
{{{{memory}}}} — сохранённые предпочтения и важная информация

Твой username: {{{{current_username}}}}
Даты: Завтра {{{{tomorrow}}}}, Послезавтра {{{{day_after}}}}

⚠️ История сообщений показывает прошлые диалоги, но {{{{tasks}}}} — это правда СЕЙЧАС!

═══════════════════════════════════════════════════════════════════════════════
💡 ПРИМЕРЫ ДИАЛОГОВ
═══════════════════════════════════════════════════════════════════════════════

1) Приветствие:
   User: "Привет!"
   AI: "Привет! Как дела? Что планируешь сегодня? 😊"
   [Не упоминай задачи без list_tasks!]

2) Показ задач:
   User: "Что у меня?"
   AI: [list_tasks()] → "Вижу 2 задачи: звонок клиенту в 14:00 и встреча в 16:00. Звонок скоро, подготовился?"

3) Добавление:
   User: "Добавь позвонить маме завтра в 10"
   AI: [add_task(title="позвонить маме", reminder_time="2026-01-07 10:00")]
   → "Готово! Напомню завтра в 10:00 ☎️"

4) Проактивность:
   User: "Добавь ещё 5 задач на сегодня..."
   AI: "Вижу у тебя уже много! Может что-то делегировать? Могу найти помощника 🤝"

5) Делегирование:
   User: "Поручи @alex сделать презентацию"
   AI: [delegate_task(delegated_to_username="@alex", title="сделать презентацию")]
   → "Поручил @alex презентацию 📊 Он получит уведомление!"

6) Нетворкинг:
   User: "Найди программиста Python"
   AI: [find_partners(interests="программирование Python")]
   → "Нашёл Алексея! Питонист из Москвы, 5 лет опыта. Хочешь делегировать ему задачу?"

═══════════════════════════════════════════════════════════════════════════════
🧠 ИНТЕЛЛЕКТУАЛЬНАЯ ПОМОЩЬ
═══════════════════════════════════════════════════════════════════════════════

АНАЛИЗИРУЙ И ПРЕДЛАГАЙ:
• Много задач → "Может приоритезируем?"
• Сложная задача → "Разобьём на этапы?"
• Перегруз → "Давай что-то делегируем?"
• Повторяющиеся → "Может автоматизировать?"
• Нет времени → "Когда напомнить?"

УЧИТЫВАЙ КОНТЕКСТ:
• Профессия из profile → "Как разработчик, ты..."
• Город → "Ищу специалиста из твоего города"
• Время дня → "Уже поздно, может завтра?"
• Прошлые диалоги → "Помню, ты работал над..."

БУДЬ ЭКСПЕРТОМ:
• Предлагай лучшие практики
• Делись инсайтами
• Замечай паттерны
• Мотивируй и поддерживай

Твоя суперсила — быть умным ассистентом, который ПОМОГАЕТ достигать большего! 🚀
    """


def parse_relative_time(message, current_time):
    """Parse relative time expressions like 'через 5 минут', 'через 2 часа' and return datetime"""
    import re
    from datetime import datetime, timedelta
    
    # Patterns for Russian time expressions
    patterns = [
        (r'через\s+(\d+)\s*мин', lambda m: timedelta(minutes=int(m.group(1)))),
        (r'через\s+(\d+)\s*минут', lambda m: timedelta(minutes=int(m.group(1)))),
        (r'через\s+(\d+)\s*час', lambda m: timedelta(hours=int(m.group(1)))),
        (r'через\s+(\d+)\s*часа', lambda m: timedelta(hours=int(m.group(1)))),
        (r'через\s+(\d+)\s*часов', lambda m: timedelta(hours=int(m.group(1)))),
    ]
    
    for pattern, delta_func in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            delta = delta_func(match)
            return current_time + delta
    
    return None

def parse_absolute_time(message):
    """Parse absolute time expressions like 'сейчас 12:18', 'время 15:30' and return HH:MM"""
    import re
    
    # Patterns for absolute time
    patterns = [
        r'сейчас\s+(\d{1,2}):(\d{2})',
        r'время\s+(\d{1,2}):(\d{2})',
        r'(\d{1,2}):(\d{2})',  # Just HH:MM
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
    try:
        return json.loads(arguments_str)
    except:
        return {}

def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    from models import Session, Task, User
    from datetime import datetime
    import pytz
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
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
            existing_task.description = description
        session.commit()
        task_id = existing_task.id
        task = existing_task  # Для дальнейшего использования
    else:
        # Создать новую задачу
        task = Task(user_id=user.id, title=title, description=description)
        if reminder_time:
            try:
                # Получить timezone пользователя
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                # Парсить как локальное время пользователя
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                # Локализовать в timezone пользователя
                local_dt = user_tz.localize(local_dt)
                # Конвертировать в UTC для хранения
                task.reminder_time = local_dt.astimezone(pytz.UTC)
                import logging
                logging.info(f"Task {title} reminder_time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
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
        session.commit()
        task_id = task.id
    
    # Планировать напоминание если указано reminder_time
    if task.reminder_time:
        try:
            from main import reminder_service
            if reminder_service:
                reminder_service.schedule_reminder(
                    task_id=task.id,
                    reminder_time=task.reminder_time,
                    user_id=user.telegram_id,
                    task_title=task.title
                )
        except Exception as e:
            import logging
            logging.error(f"Failed to schedule reminder for task {task_id}: {e}")
    
    # Обновить аналитику профиля
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()
    if close_session:
        session.close()
    return f"Добавлена задача '{title}' с ID {task_id}."

def list_tasks(user_id=None, session=None):
    from models import Session, Task
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
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    
    # Get user timezone
    user_tz = pytz.UTC
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
        except:
            user_tz = pytz.UTC
    
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    
    if tasks:
        task_list = []
        for t in tasks:
            title = t.title
            # Add delegation context to title
            if t.delegated_by and t.delegated_by != user.id:
                delegator = session.query(User).filter_by(id=t.delegated_by).first()
                if delegator:
                    title = f"{t.title} от @{delegator.username}"
            elif t.delegated_to_username:
                title = f"{t.title} для @{t.delegated_to_username}"
            
            # Add time info and overdue status
            task_info = f"{t.id}. {title} ({t.status}"
            if t.reminder_time:
                if t.reminder_time.tzinfo is None:
                    reminder_utc = pytz.UTC.localize(t.reminder_time)
                else:
                    reminder_utc = t.reminder_time
                reminder_local = reminder_utc.astimezone(user_tz)
                task_info += f", напоминание {reminder_local.strftime('%d.%m %H:%M')}"
                
                # Check if overdue
                if reminder_local < user_now and t.status == 'pending':
                    delta = user_now - reminder_local
                    minutes = int(delta.total_seconds() / 60)
                    hours = minutes // 60
                    if hours > 0:
                        task_info += f", просрочена на {hours}ч {minutes % 60}мин"
                    else:
                        task_info += f", просрочена на {minutes}мин"
                elif reminder_local > user_now and t.status == 'pending':
                    delta = reminder_local - user_now
                    minutes = int(delta.total_seconds() / 60)
                    hours = minutes // 60
                    if hours > 0:
                        task_info += f", через {hours}ч {minutes % 60}мин"
                    else:
                        task_info += f", через {minutes}мин"
            task_info += ")"
            task_list.append(task_info)
        
        if close_session:
            session.close()
        return f"Задачи: {', '.join(task_list)}."
    
    if close_session:
        session.close()
    return "Нет задач."

def complete_task(task_id=None, task_title=None, user_id=None, session=None):
    from models import Session, Task, UserProfile
    from datetime import datetime
    from sqlalchemy import or_
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
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    elif task_title:
        # Ищем по словам в названии для более гибкого поиска
        words = task_title.lower().split()
        # OR вместо AND - ищем задачу содержащую хотя бы одно из слов
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status != 'completed',
            or_(*conditions)
        ).first()
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."
    
    if task:
        task.status = "completed"
        session.commit()
        
        # If this is a delegated task, notify the delegator
        if task.delegated_by and task.delegation_status == 'accepted':
            try:
                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                if delegator:
                    from main import bot, reminder_service
                    if bot and reminder_service:
                        import asyncio
                        # Используем AI для генерации уведомления
                        asyncio.create_task(
                            reminder_service.send_delegation_progress_update(task.id, update_type="completed")
                        )
            except Exception as e:
                import logging
                logging.error(f"Failed to notify delegator about task completion: {e}")
        
        # Обновить аналитику профиля
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            completion_time = (datetime.now(timezone.utc) - task.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
            profile.completed_tasks = (profile.completed_tasks or 0) + 1
            prev_avg = profile.average_completion_time or 0
            profile.average_completion_time = ((prev_avg * (profile.completed_tasks - 1)) + completion_time) / profile.completed_tasks
            session.commit()
        result = f"Завершена задача '{task.title}'."
    else:
        result = "Задача не найдена."
    if close_session:
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
            result = f"Установлено напоминание для '{task.title}' на {reminder_time_parsed}."
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
            except Exception as e:
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
    session.close()
    return result

def delegate_task(title, description="", reminder_time=None, delegated_to_username=None, delegation_details="", user_id=None):
    """Create a delegated task that requires acceptance by the recipient"""
    from models import Session, Task, User
    from datetime import datetime
    import pytz
    
    session = Session()
    try:
        # Validate reminder_time is provided
        if not reminder_time:
            return "Ошибка: Дата и время дедлайна обязательны для делегированных задач. Укажите точное время в формате YYYY-MM-DD HH:MM."
        
        # Find delegator (creator)
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."
        
        # Find recipient by username
        recipient_username = delegated_to_username.replace('@', '').lower()
        recipient = session.query(User).filter(User.username.ilike(recipient_username)).first()
        
        if not recipient:
            return f"Пользователь @{recipient_username} не найден в системе. Убедитесь, что он зарегистрирован в боте."
        
        # If delegating to self, create regular task instead
        if recipient.id == delegator.id:
            # Create regular task for self
            task = Task(
                user_id=delegator.id,
                title=title,
                description=description,
                status='pending'
            )
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
                            task_title=task.title
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
            user_id=recipient.id,
            title=title,
            description=description,
            delegated_by=delegator.id,
            delegated_to_username=recipient_username,
            delegation_status='pending',
            delegation_details=delegation_details,
            status='pending'
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
                message = f"🔔 Новое предложение задачи от @{delegator.username}:\n\n"
                message += f"📋 Задача: {title}\n"
                if description:
                    message += f"📝 Описание: {description}\n"
                if reminder_time:
                    message += f"⏰ Дедлайн: {reminder_time}\n"
                if delegation_details:
                    message += f"ℹ️ Детали: {delegation_details}\n"
                message += f"\n💬 Напишите боту 'принять задачу {task_id}' для подтверждения или 'отклонить задачу {task_id}' для отказа."
                
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

def accept_delegated_task(task_id, user_id=None):
    """Accept a delegated task"""
    from models import Session, Task, User
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."
        
        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id, delegation_status='pending').first()
        if not task:
            return "Задача не найдена или уже обработана."
        
        # Update delegation status
        task.delegation_status = 'accepted'
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
                        task_title=task.title
                    )
            except Exception as e:
                import logging
                logging.error(f"Failed to schedule reminder: {e}")
        
        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator:
                from main import bot
                if bot:
                    message = f"✅ @{user.username} принял задачу: {task.title}"
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
        
        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id, delegation_status='pending').first()
        if not task:
            return "Задача не найдена или уже обработана."
        
        # Update delegation status
        task.delegation_status = 'rejected'
        task.status = 'rejected'
        session.commit()
        
        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator:
                from main import bot
                if bot:
                    message = f"❌ @{user.username} отклонил задачу: {task.title}"
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
        
        task = session.query(Task).filter_by(id=int(task_id), delegated_by=user.id).first()
        if not task:
            return "Делегированная задача не найдена."
        
        recipient = session.query(User).filter_by(id=task.user_id).first()
        
        if task.delegation_status == 'pending':
            status_msg = f"⏳ @{task.delegated_to_username} еще не ответил на предложение."
        elif task.delegation_status == 'accepted':
            if task.status == 'completed':
                status_msg = f"✅ Задача выполнена @{task.delegated_to_username}!"
            else:
                status_msg = f"📌 @{task.delegated_to_username} принял задачу и работает над ней (статус: {task.status})."
        elif task.delegation_status == 'rejected':
            status_msg = f"❌ @{task.delegated_to_username} отклонил эту задачу."
        else:
            status_msg = "Статус неизвестен."
        
        session.close()
        return f"Задача: {task.title}\n{status_msg}"
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"

def edit_task(task_id, title=None, description=None, reminder_time=None, user_id=None):
    from models import Session, Task
    from datetime import datetime
    from reminder_service import ReminderService
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id)).first()
    if task:
        # Проверить права доступа: задача должна принадлежать пользователю ИЛИ быть делегирована ему
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace('@', '').lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True
            # Также проверить, не является ли пользователь отправителем (для случаев когда user_id != delegator_id)
            elif task.delegated_by == user.id:
                has_access = True
        
        if not has_access:
            session.close()
            return "У вас нет прав на редактирование этой задачи."
        
        if title:
            task.title = title
        if description:
            task.description = description
        if reminder_time:
            try:
                reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                task.reminder_time = reminder_time_parsed
                # Обновляем напоминание через прямое добавление задачи в планировщик
                # ReminderService требует bot, поэтому используем прямое обновление
                logger.info(f"Обновлено время напоминания для задачи {task.id} на {reminder_time_parsed}")
            except ValueError:
                session.close()
                return "Неверный формат времени. Используйте YYYY-MM-DD HH:MM."
        session.commit()
        result = f"Обновлена задача '{task.title}'."
    else:
        result = "Задача не найдена."
    session.close()
    return result

def delete_task(task_id=None, task_title=None, user_id=None):
    from models import Session, Task
    from sqlalchemy import or_
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    
    # Найти задачу по ID или по названию
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id)).first()
        if task:
            # Проверить права доступа
            has_access = False
            if task.user_id == user.id:
                has_access = True
            elif task.delegated_to_username:
                recipient_username = task.delegated_to_username.replace('@', '').lower()
                if user.username and user.username.lower() == recipient_username:
                    has_access = True
                elif task.delegated_by == user.id:
                    has_access = True
            
            if not has_access:
                session.close()
                return "У вас нет прав на удаление этой задачи."
    elif task_title:
        # Ищем по словам в названии для более гибкого поиска (OR вместо AND)
        words = task_title.lower().split()
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(
            Task.user_id == user.id,
            or_(*conditions)
        ).first()
    else:
        session.close()
        return "Не указан ни task_id, ни task_title."
    
    if task:
        title = task.title
        session.delete(task)
        session.commit()
        result = f"Удалена задача '{title}'."
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
    
    # Поддержка частичного совпадения названия задачи
    try:
        task_id_int = int(task_id)
        task = session.query(Task).filter_by(id=task_id_int).first()
    except (ValueError, TypeError):
        # Если task_id не число, ищем по названию с частичным совпадением
        tasks = session.query(Task).filter(Task.user_id == user.id).all()
        task = None
        task_id_lower = str(task_id).lower()
        for t in tasks:
            if task_id_lower in t.title.lower():
                task = t
                break
    
    if task:
        # Проверить права доступа
        has_access = False
        if task.user_id == user.id:
            has_access = True
        elif task.delegated_to_username:
            recipient_username = task.delegated_to_username.replace('@', '').lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True
            elif task.delegated_by == user.id:
                has_access = True
        
        if not has_access:
            session.close()
            return "У вас нет прав на изменение приоритета этой задачи."
        
        if priority in ['high', 'medium', 'low']:
            task.priority = priority
            session.commit()
            result = f"Установлен приоритет '{priority}' для '{task.title}'."
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
    task = session.query(Task).filter_by(id=int(task_id)).first()
    if task:
        # Проверить права доступа
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace('@', '').lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True
            # Также проверить, не является ли пользователь отправителем
            elif task.delegated_by == user.id:
                has_access = True
        
        if not has_access:
            session.close()
            return "У вас нет прав на просмотр этой задачи."
        
        session.close()
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    session.close()
    return "Задача не найдена."

def get_partners_list(user_id=None, session=None):
    from models import Session, UserProfile, User, Interaction
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return []
    
    # Получить взаимодействия пользователя для определения кого AI рекомендовал
    user_interactions = session.query(Interaction).filter_by(user_id=user.id).all()
    
    # Ищем упоминания @username в сообщениях AI (тип agent)
    recommended_usernames = set()
    import re
    for interaction in user_interactions:
        if interaction.message_type == 'agent':  # Только сообщения от AI
            mentions = re.findall(r'@(\w+)', interaction.content)
            recommended_usernames.update(mentions)
    
    # Если AI никого не рекомендовал, возвращаем пустой список
    if not recommended_usernames:
        if close_session:
            session.close()
        return []
    
    # Получаем профили только рекомендованных пользователей
    partners = []
    for username in recommended_usernames:
        # Ищем по contact_info (который содержит username)
        profile = session.query(UserProfile).filter(
            UserProfile.contact_info.ilike(f'%{username}%')
        ).first()
        if profile and profile.user_id != user.id:
            partners.append(profile)
    
    if close_session:
        session.close()
    return partners[:10]

def find_partners(user_id=None, session=None):
    from models import Session, UserProfile, User
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
        except Exception as e:
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
            elif user_profile.goals and p.goals and any(goal.strip().lower() in p.goals.lower() for goal in user_profile.goals.split(",")):
                partners.append(p)
            # Безопасная проверка новых полей
            elif hasattr(user_profile, 'company') and hasattr(p, 'company') and user_profile.company and p.company and user_profile.company.lower() == p.company.lower():
                partners.append(p)
            elif hasattr(user_profile, 'position') and hasattr(p, 'position') and user_profile.position and p.position and user_profile.position.lower() in p.position.lower():
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
    if close_session:
        session.close()
    response = ""
    if partners:
        response += "Есть люди с похожими интересами: "
        for p in partners[:2]:
            info_parts = []
            if p.interests:
                info_parts.append(f"интересуется {p.interests}")
            if hasattr(p, 'position') and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, 'company') and p.company:
                info_parts.append(f"работает в {p.company}")
            info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"
            response += f"@{p.contact_info} ({info_str}), "
        response = response.rstrip(", ") + ". "
    if tips:
        response += " ".join(tips[:2])
    if not response:
        response = "Люди не найдены. Попробуйте обновить профиль с более подробной информацией о интересах. Или пригласите друзей и знакомых присоединиться к сообществу ASI Biont — так у вас появится больше возможностей для общения и совместных проектов! 😊"
    return response

def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, timezone=None, company=None, position=None, user_id=None, session=None):
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
    
    def update_list_field(field, value):
        if not value:
            return field
        current = set((field or "").split(", ")) - {""}  # Разделяем по ", " и убираем пустые
        if value.startswith("+"):
            new_item = value[1:].strip()
            if new_item:
                current.add(new_item)
        elif value.startswith("-"):
            remove_item = value[1:].strip()
            current.discard(remove_item)
        else:
            # Замена целиком
            current = set(value.split(", ")) - {""}
        return ", ".join(sorted(current))
    
    profile.skills = update_list_field(profile.skills, skills)
    profile.interests = update_list_field(profile.interests, interests)
    profile.goals = update_list_field(profile.goals, goals)
    profile.city = city if city else profile.city
    profile.current_plans = current_plans if current_plans else profile.current_plans
    # current_time removed - should not persist in DB
    # Безопасно добавляем новые поля (могут отсутствовать в старой БД)
    if hasattr(profile, 'company'):
        profile.company = company if company else profile.company
    if hasattr(profile, 'position'):
        profile.position = position if position else profile.position
    if timezone:
        user.timezone = timezone
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(pytz.UTC)
    session.commit()
    if close_session:
        session.close()
    return "Профиль обновлен."

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
            "description": "Завершить задачу по ID или названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {"type": "string", "description": "Название задачи или его часть (опционально если указан task_id)"}
                },
                "required": []
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
            "name": "delegate_task",
            "description": "Создать задачу для другого пользователя, которая требует его подтверждения. Сначала уточни все детали включая точную дату и время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Подробное описание задачи (опционально)"},
                    "reminder_time": {"type": "string", "description": "ОБЯЗАТЕЛЬНО: Дедлайн в формате YYYY-MM-DD HH:MM"},
                    "delegated_to_username": {"type": "string", "description": "Username получателя с @ (например @username)"},
                    "delegation_details": {"type": "string", "description": "Детали: желаемый результат, критерии выполнения, важность"}
                },
                "required": ["title", "reminder_time", "delegated_to_username"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "Принять делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "Отклонить делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"}
                },
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Получить статус выполнения делегированной задачи для инициатора",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"}
                },
                "required": ["task_id"]
            }
        }
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
                    "reminder_time": {"type": "string", "description": "Новое время напоминания в формате YYYY-MM-DD HH:MM, опционально"}
                },
                "required": ["task_id"]
            }
        }
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
                    "task_title": {"type": "string", "description": "Название задачи или его часть (опционально если указан task_id)"}
                },
                "required": []
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
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя с навыками, интересами, целями, городом, текущими планами, текущим временем, часовым поясом, компанией и должностью",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки пользователя, разделенные запятыми"},
                    "interests": {"type": "string", "description": "Интересы пользователя, разделенные запятыми"},
                    "goals": {"type": "string", "description": "Цели пользователя"},
                    "city": {"type": "string", "description": "Город пользователя, опционально"},
                    "current_plans": {"type": "string", "description": "Текущие планы или события пользователя, опционально"},
                    "current_time": {"type": "string", "description": "Текущее время пользователя в формате HH:MM, опционально"},
                    "timezone": {"type": "string", "description": "Часовой пояс пользователя, например 'Europe/Moscow', опционально"},
                    "company": {"type": "string", "description": "Компания, в которой работает пользователь, опционально"},
                    "position": {"type": "string", "description": "Должность пользователя, опционально"}
                }
            }
        }
    }
]

def force_tool_calls(message, content, mentions_str, user_id):
    """
    Анализирует ответ модели и принудительно вызывает tools,
    если модель их описала, но не вызвала формально.
    
    Возвращает список результатов вызовов функций или None.
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[FORCE] force_tool_calls called: message_len={len(message)}, mentions_str='{mentions_str}', has_@={('@' in message)}")
    
    forced_calls = []
    message_lower = message.lower()
    
    # 1. Проверка на list_tasks триггеры
    list_triggers = ["покажи", "список", "какие задач", "что у меня", "что там", "мои дела", "все задачи"]
    if any(trigger in message_lower for trigger in list_triggers):
        # Проверяем, что в content нет признаков вызова list_tasks
        if "list_tasks" not in content.lower() and "Args for list_tasks" not in content:
            logger.info("[FORCE] Triggering list_tasks() - detected request but no tool call")
            result = list_tasks(user_id=user_id)
            forced_calls.append({"function": "list_tasks", "result": result})
    
    # 2. Проверка на delegate_task (упоминание @username)
    logger.info(f"[FORCE] Checking delegate: mentions_str != 'нет' = {mentions_str != 'нет'}, '@' in message = {'@' in message}")
    if mentions_str != 'нет' and '@' in message:
        logger.info(f"[FORCE] Detected @mention: mentions_str={mentions_str}, message has @")
        # Проверяем, что в content нет вызова delegate_task
        if "delegate_task" not in content.lower() and "Args for delegate_task" not in content:
            logger.info(f"[FORCE] No delegate_task in content")
            # Извлекаем @username
            mention_match = re.search(r'@(\w+)', message)
            if mention_match:
                logger.info(f"[FORCE] Mention found: {mention_match.group(0)}")
                delegated_to = f"@{mention_match.group(1)}"
                # Пытаемся извлечь описание задачи из сообщения
                task_title = re.sub(r'@\w+', '', message).strip()
                task_title = re.sub(r'^(поручи|делегируй|передай)\s+', '', task_title, flags=re.IGNORECASE).strip()
                # Убираем "до завтра 15:00" и т.д. из названия
                task_title = re.sub(r'\s+до\s+(завтра|послезавтра|сегодня)(\s+\d{1,2}:\d{2})?', '', task_title, flags=re.IGNORECASE).strip()
                
                logger.info(f"[FORCE] Extracted: delegated_to={delegated_to}, title={task_title}")
                
                # Извлекаем deadline если есть: "до завтра 15:00"
                reminder_time = None
                deadline_match = re.search(r'до\s+(завтра|послезавтра|сегодня)\s+(\d{1,2}):(\d{2})', message, re.IGNORECASE)
                if deadline_match:
                    day_word = deadline_match.group(1).lower()
                    hour = deadline_match.group(2)
                    minute = deadline_match.group(3)
                    
                    # Вычисляем дату
                    from datetime import datetime, timedelta
                    import pytz
                    
                    # Используем timezone пользователя или UTC
                    from models import Session, User
                    temp_session = Session()
                    temp_user = temp_session.query(User).filter_by(telegram_id=user_id).first()
                    user_tz = pytz.timezone(temp_user.timezone) if temp_user and temp_user.timezone else pytz.UTC
                    temp_session.close()
                    now = datetime.now(user_tz)
                    
                    if day_word == 'завтра':
                        target_date = (now + timedelta(days=1)).date()
                    elif day_word == 'послезавтра':
                        target_date = (now + timedelta(days=2)).date()
                    else:  # сегодня
                        target_date = now.date()
                    
                    # Формируем строку "YYYY-MM-DD HH:MM"
                    reminder_time = f"{target_date.strftime('%Y-%m-%d')} {hour.zfill(2)}:{minute}"
                    logger.info(f"[FORCE] Deadline parsed: {deadline_match.group(0)} → {reminder_time}")
                
                logger.info(f"[FORCE] Triggering delegate_task() - found @mention {delegated_to}, title: '{task_title}'")
                result = delegate_task(
                    title=task_title if task_title else "Задача",
                    delegated_to_username=delegated_to,
                    reminder_time=reminder_time,
                    user_id=user_id
                )
                forced_calls.append({"function": "delegate_task", "result": result})
            else:
                logger.info(f"[FORCE] No @username match in message: {message}")
        else:
            logger.info(f"[FORCE] Skipping delegate_task - already in content")
    else:
        if mentions_str == 'нет':
            logger.info(f"[FORCE] No mentions detected (mentions_str='нет')")
        elif '@' not in message:
            logger.info(f"[FORCE] No @ symbol in message")
    
    # 3. Проверка на update_profile (упоминание работы, города)
    profile_triggers = [
        (r'(переехал|живу|нахожусь)\s+в\s+(\w+)', 'city'),
        (r'работаю\s+(?:в\s+)?(\w+)(?:\s+как\s+)?(.*)', 'company'),
        (r'должность\s+(.*)', 'position')
    ]
    
    profile_updates = {}
    for pattern, field in profile_triggers:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            if field == 'city':
                profile_updates['city'] = match.group(2)
            elif field == 'company':
                profile_updates['company'] = match.group(1)
                if len(match.groups()) > 1 and match.group(2):
                    profile_updates['position'] = match.group(2).strip()
            elif field == 'position':
                profile_updates['position'] = match.group(1)
    
    if profile_updates and "update_profile" not in content.lower():
        logger.info(f"[FORCE] Triggering update_profile() - detected profile info: {profile_updates}")
        result = update_profile(user_id=user_id, **profile_updates)
        forced_calls.append({"function": "update_profile", "result": result})
    
    # 4. Проверка на add_task (добавление задачи)
    add_triggers = ["добавь", "добавить", "создай", "создать", "напомни", "поставь задачу"]
    if any(trigger in message_lower for trigger in add_triggers):
        logger.info(f"[FORCE] Add task trigger detected in message")
        # Проверяем, что AI не вызвал add_task, но говорит что собирается это сделать
        if "add_task" not in content.lower() and ("добавлю" in content.lower() or "создам" in content.lower() or "поставлю" in content.lower()):
            logger.info(f"[FORCE] AI responded with text instead of calling add_task - forcing tool call")
            
            # Извлекаем название задачи из кавычек
            title_match = re.search(r'["«"]([^"»"]+)["»"]', message)
            if not title_match:
                # Пробуем без кавычек: "добавь задачу X на завтра"
                title_match = re.search(r'(?:добавь|создай|напомни)\s+(?:задачу\s+)?([^на]+)', message, re.IGNORECASE)
            
            if title_match:
                title = title_match.group(1).strip()
                logger.info(f"[FORCE] Extracted title: {title}")
                
                # Извлекаем время напоминания
                reminder_time = None
                time_match = re.search(r'(завтра|послезавтра|сегодня|через\s+\d+\s+(?:минут|час))\s+в\s+(\d{1,2}):(\d{2})', message, re.IGNORECASE)
                
                if time_match:
                    day_word = time_match.group(1).lower()
                    hour = int(time_match.group(2))
                    minute = int(time_match.group(3))
                    
                    from datetime import datetime, timedelta
                    import pytz
                    # Используем timezone пользователя или UTC
                    from models import Session, User
                    session = Session()
                    user = session.query(User).filter_by(telegram_id=user_id).first()
                    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
                    session.close()
                    now = datetime.now(user_tz)
                    
                    if 'завтра' in day_word:
                        target_date = (now + timedelta(days=1)).date()
                    elif 'послезавтра' in day_word:
                        target_date = (now + timedelta(days=2)).date()
                    else:
                        target_date = now.date()
                    
                    target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
                    target_dt = user_tz.localize(target_dt)
                    reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                    logger.info(f"[FORCE] Extracted reminder_time: {reminder_time}")
                
                if reminder_time:
                    logger.info(f"[FORCE] Triggering add_task() - title='{title}', reminder_time={reminder_time}")
                    result = add_task(title=title, reminder_time=reminder_time, user_id=user_id)
                    forced_calls.append({"function": "add_task", "result": result})
                else:
                    logger.info(f"[FORCE] No valid reminder_time found, skipping add_task")
            else:
                logger.info(f"[FORCE] Could not extract task title from message")
    
    # 5. Проверка на delete_task (удаление задачи)
    delete_triggers = ["удали", "удалить", "убери", "убрать", "delete"]
    if any(trigger in message_lower for trigger in delete_triggers):
        logger.info(f"[FORCE] Delete trigger detected in message")
        if "delete_task" not in content.lower() and "complete_task" not in content.lower():
            logger.info(f"[FORCE] No delete_task in content, proceeding...")
            # Пытаемся извлечь позицию (первую, вторую и т.д.)
            task_id_match = re.search(r'(первую|первая|вторую|вторая|третью|третья|\d+)\s+(задач|task)', message_lower)
            if task_id_match:
                logger.info(f"[FORCE] Found position match: {task_id_match.group(0)}")
                position = task_id_match.group(1)
                # Преобразуем в число
                position_map = {"первую": 1, "первая": 1, "вторую": 2, "вторая": 2, "третью": 3, "третья": 3}
                position_num = position_map.get(position, int(position) if position.isdigit() else 1)
                
                # Получаем список задач пользователя
                tasks_result = list_tasks(user_id=user_id)
                logger.info(f"[FORCE] Got tasks ({len(tasks_result)} chars):")
                logger.info(f"[FORCE] tasks_result preview: {tasks_result[:200]}")
                if tasks_result:
                    # Парсим список задач из результата
                    # Формат: "Задачи: 2. Название..., 3. Название..."
                    # Извлекаем все ID в порядке появления
                    task_ids = []
                    for match in re.finditer(r'(\d+)\.\s+([^(]+)', tasks_result):
                        task_id = int(match.group(1))
                        task_ids.append(task_id)
                    
                    logger.info(f"[FORCE] Parsed task IDs (in order): {task_ids}")
                    # Берём N-ую задачу по порядку
                    if position_num <= len(task_ids):
                        task_id = task_ids[position_num - 1]  # -1 потому что индекс с 0
                        logger.info(f"[FORCE] Triggering delete_task() - deleting task #{position_num} (ID={task_id})")
                        result = delete_task(task_id=task_id, user_id=user_id)
                        forced_calls.append({"function": "delete_task", "result": result})
                    else:
                        logger.info(f"[FORCE] Cannot delete task #{position_num} - only {len(task_ids)} tasks available")
            else:
                logger.info(f"[FORCE] No position match found in message")
        else:
            logger.info(f"[FORCE] Skipping delete - already in content")
    
    
    return forced_calls if forced_calls else None

async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    import logging
    logger = logging.getLogger(__name__)
    # Сохраняем оригинальное сообщение ДО очистки
    original_message = message
    # Extract mentions before cleaning message
    mentions = re.findall(r'@[\w]+', message)
    mentions_str = ', '.join(mentions) if mentions else 'нет'
    # Clean message from mentions for processing
    clean_message = re.sub(r'@[\w]+', '', message).strip()
    logger.info(f"chat_with_ai called with message: {clean_message[:50]}..., mentions: {mentions_str}, context len: {len(context) if context else 0}, user_id: {user_id}, file: {file_content is not None}")
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")
    
    # Препроцессинг: форсим list_tasks() для триггерных фраз
    list_triggers = ["покажи", "список", "какие задач", "что у меня", "что там", "мои дела", "все задачи"]
    should_force_list = any(trigger in message.lower() for trigger in list_triggers)
    
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ключ DeepSeek не настроен. Это демо ответ: Привет! Я AI-ассистент TaskChat. Чем могу помочь?"
    
    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        if user_id:
            from models import Session, User, Task, UserProfile, Subscription
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            
            # Создать пользователя если не существует
            if not user:
                user = User(telegram_id=user_id)
                session.add(user)
                session.commit()
            
            # Check subscription
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                subscription = session.query(Subscription).filter_by(user_id=user.id, status='active').first()
                if not subscription:
                    session.close()
                    return "У вас нет активной подписки. Для использования AI-ассистента активируйте подписку в Telegram боте @asibiont_bot. После активации подписки я смогу помогать вам с управлением задачами!"
            
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""  # If decryption fails, skip
            
            # Добавляем информацию из профиля (компания, должность и т.д.)
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_filled = False
            if profile:
                profile_info = []
                if profile.city:
                    profile_info.append(f"Город: {profile.city}")
                if profile.company:
                    profile_info.append(f"Компания: {profile.company}")
                if profile.position:
                    profile_info.append(f"Должность: {profile.position}")
                if profile.skills:
                    profile_info.append(f"Навыки: {profile.skills}")
                if profile.interests:
                    profile_info.append(f"Интересы: {profile.interests}")
                if profile.goals:
                    profile_info.append(f"Цели: {profile.goals}")
                if profile_info:
                    user_memory += f"\nПрофиль: {', '.join(profile_info)}"
                    profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                else:
                    user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: имя, город, компанию, должность, навыки, интересы, цели)"
            else:
                user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: имя, город, компанию, должность, навыки, интересы, цели)"
            
            # НЕ загружаем задачи в user_memory! Агент должен сам вызвать list_tasks()
            # Это критично для предотвращения выдумывания задач
            
            # Add delegated tasks info
            delegated_tasks = session.query(Task).filter(
                Task.delegated_to_username == user.username,
                Task.delegation_status == 'pending'
            ).all()
            if delegated_tasks:
                delegated_info = [f"Задача '{t.title}' (ID: {t.id}) от @{delegator.username if (delegator := session.query(User).filter_by(id=t.delegated_by).first()) else 'unknown'}" for t in delegated_tasks[:3]]
                user_memory += f"\nДелегированные задачи для принятия: {', '.join(delegated_info)}"
            
            # Add info about tasks delegated BY user
            my_delegated_tasks = session.query(Task).filter(
                Task.delegated_by == user.id,
                Task.delegation_status.in_(['pending', 'accepted'])
            ).all()
            if my_delegated_tasks:
                my_delegated_info = [f"Задача '{t.title}' поручена @{t.delegated_to_username} (статус: {t.delegation_status})" for t in my_delegated_tasks[:3]]
                user_memory += f"\nЗадачи поручённые другим: {', '.join(my_delegated_info)}"
            
            # Add partners/contacts info
            try:
                partners = get_partners_list(user_id=user_id, session=session)
                if partners:
                    partners_usernames = [p['contact_info'] for p in partners[:5] if 'contact_info' in p]
                    if partners_usernames:
                        user_memory += f"\nДоступные контакты: {', '.join(partners_usernames)}"
            except Exception as e:
                logger.error(f"Error getting partners: {e}")
            
            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars
            # Get user current time for relative time parsing and prompt
            # Always use real current time in production
            base_now = datetime.now(pytz.UTC)
            logger.info(f"[TIME CHECK] Real UTC now: {base_now}")
            logger.info(f"[TIME CHECK] Formatted: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            user_tz = pytz.UTC  # Default
            if user:
                tz_str = user.timezone if user.timezone else 'UTC'
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                    
                    # Always use real current time - removed profile.current_time override
                    # Current time is dynamic and should not persist in profile
                except Exception as e:
                    user_tz = pytz.UTC
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")
                    logger.error(f"[TIME CHECK] Timezone error: {e}")
            
            # Get upcoming reminders for context
            upcoming_reminders = []
            try:
                # Get user's tasks from database
                tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
                for task in tasks:
                    if task.reminder_time and task.status == 'pending':
                        if task.reminder_time.tzinfo is None:
                            task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                        task_time_local = task.reminder_time.astimezone(user_tz)
                        # Include tasks within next 7 days
                        if task_time_local > user_now - timedelta(days=1) and task_time_local < user_now + timedelta(days=7):
                            reminder_time_local = task_time_local.strftime("%H:%M")
                            date_str = ""
                            if task_time_local.date() == user_now.date():
                                date_str = "сегодня"
                            elif task_time_local.date() == (user_now + timedelta(days=1)).date():
                                date_str = "завтра"
                            else:
                                date_str = task_time_local.strftime("%d.%m")
                            upcoming_reminders.append(f"{task.title} {date_str} в {reminder_time_local}")
                if upcoming_reminders:
                    user_memory += f"\nБлижайшие напоминания: {', '.join(upcoming_reminders[:5])}"
            except Exception as e:
                logger.error(f"Error getting upcoming reminders: {e}")
                # Continue without reminders
            session.close()
        
        # Parse relative time from message
        parsed_time = parse_relative_time(message, user_now)
        time_hint = ""
        if parsed_time:
            time_hint = f"\nОБНАРУЖЕНО ОТНОСИТЕЛЬНОЕ ВРЕМЯ: '{message}' содержит указание времени. Рассчитанное время напоминания: {parsed_time.strftime('%Y-%m-%d %H:%M')}. Используй это время для reminder_time в add_task."
        
        # Parse absolute time from message
        parsed_absolute_time = parse_absolute_time(message)
        if parsed_absolute_time:
            logger.info(f"Detected absolute time in message: {parsed_absolute_time}")
            # Don't save to profile - just update user_now for current context
            # update_profile(current_time=parsed_absolute_time, user_id=user_id)  # REMOVED
            # Update user_now for subsequent processing
            try:
                time_obj = datetime.strptime(parsed_absolute_time, '%H:%M').time()
                user_now = datetime.combine(user_now.date(), time_obj, tzinfo=user_tz)
                current_time_str = parsed_absolute_time
                logger.info(f"Updated user_now to: {user_now}")
            except Exception as e:
                logger.error(f"Failed to update user_now: {e}")
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        # Расширяем system prompt для работы с относительным временем
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        system_prompt = get_system_prompt().replace("{{current_date}}", user_now.strftime("%Y-%m-%d")).replace("{{current_time}}", current_time_str).replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d")).replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d")).replace("{{current_username}}", user_username)
        system_prompt += f"\n\nВАЖНО ПРИ РАБОТЕ С ВРЕМЕНЕМ:\n- Текущее время: {current_time_str}\n- Если пользователь говорит 'через X минут', добавь X минут к текущему времени {current_time_str}\n- Если пользователь говорит 'через X часов', добавь X часов к текущему времени\n- Всегда используй формат времени reminder_time в виде 'YYYY-MM-DD HH:MM' в параметрах tool call\n- Например: 'через 5 минут' от {current_time_str} = {(user_now + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M')}"
        system_prompt += f"\n\nОБНАРУЖЕННЫЕ @MENTIONS В СООБЩЕНИИ: {mentions_str}\nЕСЛИ ПОЛЬЗОВАТЕЛЬ ПРОСИТ ПОРУЧИТЬ/ДЕЛЕГИРОВАТЬ ЗАДАЧУ, ИСПОЛЬЗУЙ delegate_task С delegated_to_username ИЗ ЭТИХ MENTIONS!"
        
        # Если обнаружены триггеры для list_tasks, добавляем в промпт принудительное требование
        if should_force_list:
            system_prompt += "\n\n🚨 КРИТИЧЕСКИ ВАЖНО: ПОЛЬЗОВАТЕЛЬ ПРОСИТ ПОКАЗАТЬ ЗАДАЧИ - ОБЯЗАТЕЛЬНО ВЫЗОВИ list_tasks() ПЕРВЫМ ДЕЛОМ, ДАЖЕ ЕСЛИ В КОНТЕКСТЕ УЖЕ ЕСТЬ ИНФОРМАЦИЯ О ЗАДАЧАХ!"
        
        system_prompt += user_memory
        system_prompt += time_hint
        
        messages = [{"role": "system", "content": system_prompt}]
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
            "temperature": 0.1
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                logger.info(f"DeepSeek API response status: {response.status}")
                if response.status == 200:
                    result = await response.json()
                    message_response = result["choices"][0]["message"]
                    content = message_response.get("content", "")
                    # Фильтровать сырые tool calls
                    content = clean_content(content)
                    content = re.sub(r'<\|.*?\|>', '', content).strip()
                    content = re.sub(r'<｜DSML｜function_calls>.*?</｜DSML｜function_calls>', '', content, flags=re.DOTALL).strip()
                    
                    # Проверяем tool_calls в API response
                    tool_calls = message_response.get("tool_calls")
                    
                    # ВСЕГДА проверяем триггеры принудительного вызова
                    logger.info("[FORCE CHECK] Checking for forced tool call triggers...")
                    forced = force_tool_calls(original_message, content, mentions_str, user_id)
                    
                    if forced:
                        # После принудительных tool calls нужно сгенерировать НОРМАЛЬНЫЙ ответ через AI
                        logger.info(f"[FORCE] Forced {len(forced)} tool calls, generating AI response based on results")
                        
                        # Собираем результаты для контекста
                        tool_results_summary = []
                        for fc in forced:
                            func_name = fc['function']
                            result = fc['result']
                            tool_results_summary.append(f"{func_name}() вернул: {result[:200]}")
                        
                        # Делаем повторный запрос к AI с результатами tool calls для генерации естественного ответа
                        system_prompt_with_results = system_prompt + f"\n\nВЫПОЛНЕННЫЕ ФУНКЦИИ (НЕ ПОКАЗЫВАЙ ЭТО ПОЛЬЗОВАТЕЛЮ, МОЛЧА ИСПОЛЬЗУЙ ДАННЫЕ):\n" + "\n".join(tool_results_summary) + "\n\nСформулируй естественный ответ на основе ТОЛЬКО этих данных. СТРОГО ЗАПРЕЩЕНО показывать названия функций, код или технические детали!"
                        
                        messages_with_results = [{"role": "system", "content": system_prompt_with_results}]
                        if context:
                            for item in context:
                                if "user" in item:
                                    messages_with_results.append({"role": "user", "content": item["user"]})
                                if "agent" in item:
                                    messages_with_results.append({"role": "assistant", "content": item["agent"]})
                        messages_with_results.append({"role": "user", "content": original_message})
                        
                        data_retry = {
                            "model": "deepseek-chat",
                            "messages": messages_with_results,
                            "temperature": 0.1
                        }
                        
                        async with session.post(url, headers=headers, json=data_retry, timeout=aiohttp.ClientTimeout(total=60)) as retry_response:
                            if retry_response.status == 200:
                                retry_result = await retry_response.json()
                                content = retry_result["choices"][0]["message"].get("content", "")
                                content = clean_content(content)
                                content = replace_placeholders(content, user_now, current_time_str)
                                content = clean_technical_details(content)  # Очистка от технических деталей
                                
                                # Сохраняем взаимодействие
                                if user_id and content:
                                    try:
                                        from models import Session, User, Interaction
                                        save_session = Session()
                                        user_obj = save_session.query(User).filter_by(telegram_id=user_id).first()
                                        if user_obj:
                                            user_interaction = Interaction(user_id=user_obj.id, message_type='user', content=original_message)
                                            save_session.add(user_interaction)
                                            ai_interaction = Interaction(user_id=user_obj.id, message_type='agent', content=content)
                                            save_session.add(ai_interaction)
                                            save_session.commit()
                                            logger.info(f"Saved interaction to DB for user {user_id}")
                                        save_session.close()
                                    except Exception as e:
                                        logger.error(f"Failed to save interaction: {e}")
                                
                                return content
                            else:
                                # Если повторный запрос неудачен, вернем простой результат
                                return tool_results_summary[0] if tool_results_summary else "Выполнено"
                
                # Обрабатываем обычный ответ AI без tool calls
                content = message_response.get("content", "")
                content = clean_content(content)
                content = replace_placeholders(content, user_now, current_time_str)
                content = clean_technical_details(content)  # Очистка от технических деталей
                
                # Если после очистки ответ пустой - повторный запрос
                if not content or len(content.strip()) < 3:
                    logger.warning("[RETRY] Response empty after cleaning, retrying with explicit instruction")
                    retry_system = system_prompt + "\n\nВАЖНО: Пользователь ждет ПОЛНОГО ответа, не просто 'Готово'. Отвечай развернуто и профессионально!"
                    
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
                                "model": "deepseek-chat",
                                "messages": retry_messages,
                                "temperature": 0.3,
                            },
                            timeout=aiohttp.ClientTimeout(total=120)
                        ) as retry_response:
                            retry_result = await retry_response.json()
                            content = retry_result['choices'][0]['message']['content']
                            content = clean_content(content)
                            content = clean_technical_details(content)
                    
                    if not content:
                        content = "Хорошо, продолжим работу!"
                
                # Сохраняем взаимодействие в базу данных для отображения в панели
                if user_id:
                    try:
                        from models import Session, User, Interaction
                        save_session = Session()
                        user_obj = save_session.query(User).filter_by(telegram_id=user_id).first()
                        if user_obj:
                            # Сохраняем сообщение пользователя
                            user_interaction = Interaction(
                                user_id=user_obj.id,
                                message_type='user',
                                content=original_message  # Используем оригинальное сообщение с @mentions
                            )
                            save_session.add(user_interaction)
                            
                            # Сохраняем ответ AI
                            ai_interaction = Interaction(
                                user_id=user_obj.id,
                                message_type='agent',
                                content=content
                            )
                            save_session.add(ai_interaction)
                            save_session.commit()
                            logger.info(f"Saved interaction to DB for user {user_id}")
                        save_session.close()
                    except Exception as e:
                        logger.error(f"Failed to save interaction: {e}")
                
                # Очистка от технических деталей перед возвратом
                content = clean_technical_details(content)
                return content
    
    except Exception as e:
        import traceback
        logger.error(f"Error in chat_with_ai: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(traceback.format_exc())
        return f"Ошибка: {str(e)}"

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
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        base_prompt = get_system_prompt()
        system_prompt = f"{base_prompt}\nТы генерируешь краткое напоминание о задаче '{task_title}'. Будь мотивирующим и полезным. Если есть релевантная информация из памяти пользователя, используй её для более персонализированного напоминания. Задавай конкретные вопросы, которые помогут пользователю лучше подготовиться ИЛИ собрать дополнительную информацию, необходимую для принятия лучших решений по выполнению задачи. Анализируй задачу и предлагай аспекты, которые пользователь мог упустить. НЕ предлагай создавать новые задачи в напоминаниях - это только для напоминания о существующей задаче.{user_memory}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о задаче: {task_title}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime('%H:%M'))
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
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        base_prompt = get_system_prompt()
        system_prompt = f"{base_prompt}\nТы задаешь вопрос о результате выполнения задачи '{task_title}'. Спроси о времени, сложностях, улучшениях. Будь строгим при просрочке, краток.{user_memory}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Спроси о результате задачи: {task_title}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime('%H:%M'))
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
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
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
                                tips.append(f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} — может быть интересно с твоими интересами в {interest.strip()}.")
                                break
                if tips:
                    plans_info = "\nПланы людей: " + " ".join(tips[:2])
            # Получить текущие задачи
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            pending_tasks = [t.title for t in tasks if t.status in ['pending', 'in_progress']]
            if pending_tasks:
                tasks_info = f"\nТекущие невыполненные задачи: {', '.join(pending_tasks[:3])}"
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        base_prompt = get_system_prompt()
        system_prompt = f"{base_prompt}\nТы генерируешь разнообразное проактивное сообщение для пользователя без задач на ближайший час. Будь позитивным, вовлекающим, краток (1-2 предложения). Включи персонализацию на основе задач, памяти, планов людей.{user_memory}{plans_info}{tasks_info}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Создай проактивное сообщение"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime('%H:%M'))
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
        
        completed = [t for t in tasks if t.status == 'completed']
        pending = [t for t in tasks if t.status in ['pending', 'in_progress']]
        
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
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        base_prompt = get_system_prompt()
        system_prompt = f"{base_prompt}\nТы генерируешь краткий ежедневный отчет: выполнено {len(completed)} задач, ожидают {len(pending)}. Будь позитивным, мотивирующим.{user_memory}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime('%H:%M'))
                    return content
                else:
                    return "Ошибка генерации отчета."
    except Exception as e:
        print(f"Error in generate_daily_report: {e}")
        return "Отчет о задачах."

async def generate_overdue_reminder(user_id, overdue_tasks):
    """Генерирует напоминание о просроченных задачах"""
    try:
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
                except:
                    user_memory = ""
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        base_prompt = get_system_prompt()
        system_prompt = f"{base_prompt}\nТы генерируешь строгое, мотивирующее напоминание о просроченных задачах: {', '.join(task_titles)}. Будь краток, напомни о последствиях.{user_memory}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о просроченных задачах: {', '.join(task_titles)}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime('%H:%M'))
                    return content
                else:
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."

async def generate_delegation_update(user_id, task_title, recipient_username, task_status, reminder_time, update_type):
    """Генерирует обновление о прогрессе делегированной задачи через AI с использованием полного промпта"""
    try:
        # Получить полный контекст пользователя
        user_memory = ""
        user_timezone = "UTC"
        if user_id:
            from models import Session, User
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                if user.memory:
                    try:
                        decrypted = decrypt_data(user.memory)
                        user_memory = f"\nИнформация о пользователе: {decrypted}"
                    except:
                        user_memory = ""
                user_timezone = user.timezone or "UTC"
            session.close()
        
        # Конвертируем время в часовой пояс пользователя
        user_tz = pytz.timezone(user_timezone)
        local_time = datetime.now(user_tz)
        current_date = local_time.strftime("%d %B %Y")
        current_time = local_time.strftime("%H:%M")
        tomorrow = (local_time + timedelta(days=1)).strftime("%d %B")
        day_after = (local_time + timedelta(days=2)).strftime("%d %B")
        
        # Форматируем reminder_time
        if reminder_time:
            if reminder_time.tzinfo is None:
                reminder_time = pytz.UTC.localize(reminder_time)
            local_reminder = reminder_time.astimezone(user_tz)
            deadline_str = local_reminder.strftime("%d.%m.%Y %H:%M")
        else:
            deadline_str = "не указан"
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        base_prompt = get_system_prompt()
        
        # Формируем контекст в зависимости от типа обновления
        if update_type == "approaching_deadline":
            context = f"Делегированная задача '@{recipient_username}: {task_title}' приближается к дедлайну ({deadline_str}). Дедлайн через 2 часа или меньше. Текущий статус: {task_status}"
            instruction = "Проинформируй инициатора о приближающемся дедлайне делегированной задачи. Будь конкретным, напомни о времени и получателе. Используй естественный диалог без шаблонов."
        elif update_type == "midpoint":
            context = f"Делегированная задача '@{recipient_username}: {task_title}' на полпути к дедлайну ({deadline_str}). Текущий статус: {task_status}"
            instruction = "Проинформируй инициатора о прогрессе делегированной задачи. Напомни о задаче и получателе, уточни что задача находится в процессе. Используй естественный диалог."
        elif update_type == "completed":
            context = f"Делегированная задача '@{recipient_username}: {task_title}' выполнена. Дедлайн был: {deadline_str}"
            instruction = "Проинформируй инициатора о завершении делегированной задачи. Похвали получателя за выполнение. Используй естественный диалог."
        else:  # status update
            context = f"Делегированная задача '@{recipient_username}: {task_title}'. Дедлайн: {deadline_str}. Текущий статус: {task_status}"
            instruction = "Проинформируй инициатора о текущем статусе делегированной задачи. Используй естественный диалог без шаблонов."
        
        system_prompt = f"""{base_prompt}

ТЕКУЩИЙ КОНТЕКСТ:
Дата: {current_date}
Время: {current_time}
{user_memory}

ЗАДАЧА: {instruction}

КОНТЕКСТ ДЕЛЕГИРОВАНИЯ: {context}

ВАЖНО: Генерируй УНИКАЛЬНОЕ сообщение на основе текущего контекста. НЕ используй шаблонные фразы. Будь естественным и конкретным."""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Сообщи об обновлении делегированной задачи"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    return clean_content(content)
                else:
                    return f"Обновление по задаче '{task_title}' для @{recipient_username}"
    except Exception as e:
        print(f"Error in generate_delegation_update: {e}")
        return f"Обновление по задаче '{task_title}' для @{recipient_username}"
