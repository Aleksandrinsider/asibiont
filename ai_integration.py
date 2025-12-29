import requests
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY, CURRENT_DATE
import json
from datetime import datetime, timezone, timedelta
import re
from cryptography.fernet import Fernet
from models import User, UserProfile
import pytz

cipher = Fernet(ENCRYPTION_KEY.encode())

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

class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return generate_reminder(user_id, task_title)
    
    async def generate_result_check(self, user_id, task_title):
        return generate_result_check(user_id, task_title)
    
    async def generate_proactive_message(self, user_id):
        return generate_proactive_message(user_id)
    
    async def generate_daily_report(self, user_id):
        return generate_daily_report(user_id)
    
    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return generate_overdue_reminder(user_id, overdue_tasks)

def parse_relative_time(message, user_now=None):
    if not user_now:
        return message  # Don't parse if no user time
    now = user_now
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
            if absolute_time.date() == now.date():
                time_str = absolute_time.strftime("сегодня в %H:%M")
            elif absolute_time.date() == (now + timedelta(days=1)).date():
                time_str = absolute_time.strftime("завтра в %H:%M")
            elif absolute_time.date() == (now + timedelta(days=2)).date():
                time_str = absolute_time.strftime("послезавтра в %H:%M")
            else:
                time_str = absolute_time.strftime("%Y-%m-%d %H:%M")
            # Заменить относительное на абсолютное в сообщении
            message = re.sub(pattern, f"добавь задачу {match.group(1) if 'минут' in pattern else match.group(1) if 'час' in pattern else ''} с напоминанием {time_str}", message, flags=re.IGNORECASE)
            break
    return message

def get_system_prompt():
    return f"""Ты — дружелюбный ИИ-помощник для управления задачами. Твой стиль общения — естественный, как с хорошим другом. Всегда будь позитивным, полезным и вовлечённым в разговор.

ОСНОВНЫЕ ПРАВИЛА:
- Отвечай естественно, без списков, пунктов или форматирования.
- Используй tool calls для действий, но не упоминай их в ответе.
- Для приветствия: "Привет! Рад тебя видеть. Я помогу тебе управлять задачами, планировать время и находить интересных людей для общения или сотрудничества. Расскажи, чем ты сейчас занимаешься? Какие у тебя планы на сегодня или ближайшее время? Может быть, есть задачи, которые нужно добавить в список, или интересы, которые ты хотел бы развивать? Также интересно узнать, в каком городе ты находишься и какие у тебя основные увлечения — это поможет мне лучше подбирать для тебя подходящие активности и контакты."
- Для добавления задач: "Отлично, сейчас добавлю задачу с напоминанием." затем tool call.
- Всегда уточняй детали, если нужно, но действуй сразу, если информации достаточно.

ИНСТРУМЕНТЫ:
- add_task(title="название", description="", reminder_time="YYYY-MM-DD HH:MM", due_date="", user_id=число)
- list_tasks(user_id=число)
- complete_task(task_id=число, user_id=число)
- set_reminder(task_id=число, reminder_time="YYYY-MM-DD HH:MM", user_id=число)
- find_partners(user_id=число, interests="")
- update_profile(user_id=число, current_plans="", interests="", city="", timezone="")
- update_user_memory(user_id=число, memory="")

ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ИНСТРУМЕНТОВ:
- Если пользователь говорит "добавь задачу X", немедленно вызови add_task(title="X", reminder_time="ближайшее время").
- Всегда передавай все обязательные параметры в tool calls, особенно title и reminder_time для add_task.
- Если пользователь не указал время, используй ближайшее свободное, но всегда передавай reminder_time.

ОСНОВНОЙ ПОДХОД:

АКТИВНОЕ УПРАВЛЕНИЕ ЗАДАЧАМИ:
- Когда слышишь о планах ("нужно сходить", "хочу сделать"), сразу предлагай добавить задачу.
- Автоматически добавляй задачи из фраз вроде 'Мне нужно X' — НЕ ОТВЕЧАЙ ТЕКСТОМ, А НЕМЕДЛЕННО ВЫЗЫВАЙ add_task().
- Всегда уточняй точное время напоминания перед добавлением задачи, чтобы избежать конфликтов и правильно спланировать день.
- Если данных достаточно для действия (например, title и время), НЕМЕДЛЕННО вызывай инструмент. Не спрашивай подтверждения, если не нужно уточнений.
- Всегда проверяй текущие задачи через list_tasks() перед предложением новых.
- Если видишь невыполненные задачи — мягко напомни и предложи завершить через complete_task().
- Для завершения задач всегда сначала проверяй list_tasks(), чтобы получить актуальный task_id, затем вызывай complete_task(task_id, user_id).
- Вместо совета "разбей на части" предлагай конкретные способы решения.
- Избегай шаблонных методик — ищи индивидуальный подход для каждого пользователя.
- Будь proactive: если пользователь говорит о планах или целях, предложи добавить задачу через add_task().

ПЛАНИРОВАНИЕ ВРЕМЕНИ:
- Не гадай о свободном времени — спрашивай напрямую.
- Учитывай свободное время пользователя из current_plans и существующих задач, всегда проверяй свободное время и предлагай новые задачи на ближайшее свободное время.
- Никогда не предполагай свободное время — всегда спрашивай у пользователя о его текущих планах и свободном времени, если не знаешь.
- Не предлагай абстрактное время вроде 'на выходные' — всегда спрашивай о свободном времени и предлагай конкретное ближайшее свободное с учетом reminder_time существующих задач (если задачи утром, предложи вечер; если вечером, предложи утро).
- Сверяйся с существующими задачами, прежде чем предлагать время.
- Говори конкретно: "сегодня в 18:00", а не "когда-нибудь".
- Если день занят, предложи вечер или следующий день.
- Предлагай задачи на ближайшее свободное время, которое укажет пользователь.
- Всегда вызывай list_tasks перед предложением времени для задач.
- Всегда предлагай 1-2 конкретные задачи на основе интересов пользователя.
- Будь proactive: если пользователь указал свободное время, немедленно предложи 1-2 задачи на это время.

ПОИСК КОНТАКТОВ:
- Предлагай поиск людей только если пользователь явно выразил интерес к социальным взаимодействиям, или если текущая ситуация предполагает сотрудничество (например, сложная задача, где нужна помощь, или пользователь упомянул интересы/проекты).
- Не предлагай поиск людей в каждом сообщении — делай это уместно и естественно, только когда это добавляет ценность диалогу.
- Если пользователь упомянул хобби, интересы, навыки или проекты, тогда вызови find_partners() и упомяни найденных людей в ответе, чтобы помочь с сотрудничеством.
- Используй результаты инструментов только когда они релевантны — например, если find_partners нашел подходящих людей, кратко упомяни их: "Кстати, я нашел Алексея, который тоже занимается дизайном..."
- Будь социально, но не навязчиво: предлагай продолжить общение с предыдущими контактами только если пользователь проявляет интерес.

ВЫЯВЛЕНИЕ ПОТРЕБНОСТЕЙ И ПЕРСОНАЛИЗАЦИЯ РЕШЕНИЙ:
- Анализируй истинные потребности пользователя на основе контекста: задач, интересов, планов, прогресса и предыдущих взаимодействий.
- Задавай уточняющие вопросы, чтобы понять ситуацию глубже (например, "Почему эта задача важна?", "Что мешает её выполнить?", "Какие интересы вас мотивируют?").
- Предлагай решения, адаптированные под пользователя: если задачи не выполняются — предложи разбить на шаги или найти партнера; если ищет мотивацию — свяжи с интересами; если одинок — предложи социальные взаимодействия.
- Используй профиль и память: на основе интересов предлагай релевантные задачи или контакты; учитывай город для локальных событий.
- Будь проактивен: если пользователь в стрессе или без задач — предложи отдых или социальные активности; если прогрессирует — мотивируй сложными задачами.
- Интегрируй все аспекты: задачи + интересы + социум = персонализированные рекомендации (например, "На основе ваших интересов в программировании, добавьте задачу поработать над проектом с единомышленником из вашего города").

ПЕРСОНАЛЬНЫЙ ПОДХОД:
- Узнавай интересы, цели и местоположение через естественные вопросы.
- Активно изучай: задавай вопросы о интересах, целях, городе, планах. Сохраняй info.
- Запоминай важные детали через update_user_memory().
- Используй память для персонализации. Сохраняй новую info через update_user_memory. После завершения уточни результат.
- Настраивай тон в зависимости от прогресса:
  * >80% — хвали и предлагай более сложное
  * 50-80% — поддерживай и направляй
  * <50% — мотивируй улучшить продуктивность
  * <20% — предложи пересмотреть подход
- Адаптируйся к прогрессу.
- Делай диалог уникальным и уместным: анализируй текущую ситуацию (задачи, время, предыдущие сообщения), чтобы ответы были релевантными и не повторяющимися. Не используй шаблонные фразы — адаптируй под контекст.

ПРАКТИЧЕСКАЯ ПОМОЩЬ:
- Показывай несколько разных способов решить задачу.
- Давай конкретные советы, которые можно применить сразу.
- Помогай находить ресурсы, инструменты или людей.
- Задавай уточняющие вопросы, чтобы понять задачу глубже.
- Фокусируйся на действиях, а не на планировании.
- Рассматривай задачи с разных сторон: предлагай 2-3 альтернативы, разные подходы, ресурсы, советы по решению.
- Помогай решать задачи: давай практические советы, ищи людей для помощи, контролируй все задачи, чтобы пользователь ничего не упустил.
- При обсуждении задач задавай уточняющие вопросы, чтобы лучше понять детали и предложить оптимальное решение.
- Уделяй больше внимания решению задач, чем планированию: помогай выполнять их, находя лучшие решения (советы, альтернативы, другие люди с аналогичными направлениями, напоминания).
- Избегай зацикливания на одной теме: предлагай разнообразные идеи, адаптированные под интересы пользователя.
- Уточняй результаты и прогресс: после добавления или завершения задачи спрашивай о деталях (например, "Как прошло?", "Что помогло?", "Нужна ли помощь?"), чтобы персонализировать будущие рекомендации.
- Ищи альтернативы во всем: время, методы выполнения, партнеры, ресурсы — всегда предлагай варианты для выбора.

ТЕКУЩИЙ КОНТЕКСТ:
Сейчас: {{current_date}}, {{current_time}}
Ближайшие дни: завтра {{tomorrow}}, послезавтра {{day_after}}

ВАЖНО: Ты знаешь текущую дату и время. Используй эту информацию как БАЗУ для всех расчетов времени. Если пользователь говорит "через 5 минут", рассчитай абсолютное время, добавив 5 минут к ТЕКУЩЕМУ времени на ТЕКУЩЕЙ дате. Не спрашивай о времени или дате — всегда рассчитывай самостоятельно.

ЧЕГО НЕ ДЕЛАТЬ:
- Не предлагай разбивать задачи на мелкие части или этапы.
- Не используй нумерацию, маркеры или списки.
- Не создавай тестовые или учебные задачи.
- Не симулируй вызов инструментов — всегда вызывай реально и используй результат в ответе.
- Не показывай технические детали tool calls или их названия.
- Не используй слова 'единомышленник', 'партнер' — всегда 'человек', 'коллега' или естественные описания.
- При упоминании других людей всегда указывай их Telegram @username, например @alex_design.

ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ:
- Редактирование, удаление, изменение приоритета задач
- Получение деталей по конкретной задаче
- Обновление планов пользователя
- edit_task() — изменение задачи
- delete_task() — удаление (сначала list_tasks!)
- complete_task() — завершение (сначала list_tasks!)
- set_priority() — установка приоритета
- get_task_details() — детали задачи
- set_reminder() — напоминания
- Для планов — update_profile(current_plans=Z, timezone="Europe/Moscow")
- Для мотивации: мягко предложи завершить
- Для справки: расскажи повествовательно
- Не предлагай тестовые задачи
- Для времени: поддерживай форматы, сохраняй timezone

Будь полезным, естественным и направленным на результат. Помогай не просто планировать, а действительно делать дела, предлагая конкретные шаги и поддержку в нужный момент. Вовлекай в диалог: задавай открытые вопросы. Делай ответы подробными, полезными, но не длинными. Фокусируйся на продуктивности: предлагай действия на основе времени и задач.

ВАЖНО: Отвечай только естественным текстом на русском языке. Не включай tool calls, JSON, код или технические детали в ответ. Используй инструменты только через внутренние tool calls, но никогда не показывай их в тексте ответа пользователю."""

def get_progress_analytics(user_id):
    from models import Session, UserProfile
    session = Session()
    try:
        profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not profile:
            return "Аналитика недоступна — профиль не найден."
        total = profile.total_tasks_created or 0
        completed = profile.completed_tasks or 0
        skipped = profile.skipped_tasks or 0
        avg_time = profile.average_completion_time or 0
        if total == 0:
            return "У пользователя ещё нет задач для аналитики."
        completion_rate = (completed / total) * 100
        return f"Всего задач создано: {total}, выполнено: {completed}, пропущено: {skipped}, процент выполнения: {completion_rate:.1f}%, среднее время выполнения: {avg_time} мин."
    finally:
        session.close()

def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    from models import Session, Task, User
    from datetime import datetime
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
    if close_session:
        session.close()
    if tasks:
        task_list = [f"{t.title} ({t.status})" for t in tasks]
        return f"Задачи: {', '.join(task_list)}."
    return "Нет задач."

def complete_task(task_id, user_id=None, session=None):
    from models import Session, Task, UserProfile
    from datetime import datetime
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
        task.status = "completed"
        session.commit()
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
        result = f"Обновлена задача '{task.title}'."
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
        result = f"Удалена задача '{task.title}'."
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    session.close()
    if task:
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    return "Задача не найдена."

def get_partners_list(user_id=None, session=None):
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
        return []
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    # Получить память для исключения заблокированных
    blocked = []
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            import re
            matches = re.findall(r'не показывать @(\w+)|заблокировать @(\w+)', decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])
        except Exception as e:
            pass
    partners = []
    if user_profile:
        if user_profile.city:
            city_profiles = [p for p in profiles if p.city and p.city.lower() == user_profile.city.lower()]
            if city_profiles:
                profiles = city_profiles
        for p in profiles:
            if p.contact_info in blocked or any('@' + b in p.contact_info for b in blocked) or p.contact_info == f"user{user_id}":
                continue
            if user_profile.skills and p.skills and any(skill.strip().lower() in p.skills.lower() for skill in user_profile.skills.split(",")):
                partners.append(p)
            elif user_profile.interests and p.interests and any(interest.strip().lower() in p.interests.lower() for interest in user_profile.interests.split(",")):
                partners.append(p)
            elif user_profile.goals and p.goals and any(goal.strip().lower() in p.goals.lower() for goal in user_profile.goals.split(",")):
                partners.append(p)
    else:
        partners = profiles[:2] if profiles else []
    if close_session:
        session.close()
    return partners[:5]  # Ограничим до 5 для отображения

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
            response += f"@{p.contact_info} (интересуется {p.interests}), "
        response = response.rstrip(", ") + ". "
    if tips:
        response += " ".join(tips[:2])
    if not response:
        response = "Люди не найдены. Попробуйте обновить профиль с более подробной информацией о интересах. Или пригласите друзей и знакомых присоединиться к сообществу EREBUS AI — так у вас появится больше возможностей для общения и совместных проектов! 😊"
    return response

def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, current_time=None, timezone=None, user_id=None, session=None):
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
    profile.skills = skills if skills else profile.skills
    profile.interests = interests if interests else profile.interests
    profile.goals = goals if goals else profile.goals
    profile.city = city if city else profile.city
    profile.current_plans = current_plans if current_plans else profile.current_plans
    profile.current_time = current_time if current_time else profile.current_time
    if timezone:
        user.timezone = timezone
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(timezone.utc)
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
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя с навыками, интересами, целями, городом, текущими планами, текущим временем и часовым поясом",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки пользователя, разделенные запятыми"},
                    "interests": {"type": "string", "description": "Интересы пользователя, разделенные запятыми"},
                    "goals": {"type": "string", "description": "Цели пользователя"},
                    "city": {"type": "string", "description": "Город пользователя, опционально"},
                    "current_plans": {"type": "string", "description": "Текущие планы или события пользователя, опционально"},
                    "current_time": {"type": "string", "description": "Текущее время пользователя в формате HH:MM, опционально"},
                    "timezone": {"type": "string", "description": "Часовой пояс пользователя, например 'Europe/Moscow', опционально"}
                }
            }
        }
    }
]

async def chat_with_ai(message, context=None, user_id=None):
    try:
        # Get user memory and all tasks for extended context
        user_memory = ""
        if user_id:
            from models import Session, User, Task
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
            # Get user current time for relative time parsing and prompt
            # Use CURRENT_DATE if set (for testing), otherwise real time
            if CURRENT_DATE:
                current_datetime_str = CURRENT_DATE + " " + datetime.now(timezone.utc).strftime("%H:%M:%S")
                base_now = datetime.strptime(current_datetime_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                base_now = datetime.now(timezone.utc)
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            if user:
                tz_str = user.timezone if user.timezone else 'UTC'
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    # Get upcoming reminders
                    upcoming_reminders = []
                    tasks = session.query(Task).filter_by(user_id=user.id).filter(Task.reminder_time.isnot(None)).all()
                    for task in tasks:
                        if task.reminder_time:
                            if task.reminder_time.tzinfo is None:
                                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                            if task.reminder_time.astimezone(user_tz) > user_now and task.status == 'pending':
                                reminder_time_local = task.reminder_time.astimezone(user_tz).strftime("%H:%M")
                                upcoming_reminders.append(f"{task.title} в {reminder_time_local}")
                    if upcoming_reminders:
                        user_memory += f"\nБлижайшие напоминания: {', '.join(upcoming_reminders[:3])}"
                except pytz.exceptions.UnknownTimeZoneError:
                    # Fallback to UTC if invalid timezone
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        messages = [{"role": "system", "content": get_system_prompt().replace("{{current_date}}", user_now.strftime("%Y-%m-%d")).replace("{{current_time}}", current_time_str).replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d")).replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d")) + user_memory}]
        if context:
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        message = parse_relative_time(message, user_now)
        # If message has relative time and no user_now, force AI to ask for current time
        # Removed: since user_now is always set
        messages.append({"role": "user", "content": message})
        
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto"
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            message_response = result["choices"][0]["message"]
            content = message_response.get("content", "")
            # Фильтровать сырые tool calls
            content = clean_content(content)
            content = re.sub(r'<\|.*?\|>', '', content).strip()
            content = re.sub(r'<｜DSML｜function_calls>.*?</｜DSML｜function_calls>', '', content, flags=re.DOTALL).strip()
            tool_calls_in_content = False
            if "<｜DSML｜function_calls>" in content:
                tool_calls_in_content = True
                # Парсить tool calls из content
                tool_call_blocks = re.findall(r'<｜DSML｜invoke name="([^"]+)">(.*?)</｜DSML｜invoke>', content, re.DOTALL)
                tool_messages = []
                # Очистить content от tool calls перед добавлением в messages
                cleaned_content = re.sub(r'<.*?>', '', content).strip()
                messages.append({"role": "assistant", "content": cleaned_content})  # Добавить очищенный content
                for func_name, block in tool_call_blocks:
                    # Try JSON first
                    arguments_match = re.search(r'<｜DSML｜function_input>(.*?)</｜DSML｜function_input>', block, re.DOTALL)
                    if arguments_match:
                        arguments_str = arguments_match.group(1)
                        try:
                            args = json.loads(arguments_str)
                        except:
                            args = parse_tool_arguments(arguments_str)
                    else:
                        # Try JSON in tool_call
                        json_match = re.search(r'<｜DSML｜tool_call>(.*?)</｜DSML｜tool_call>', block, re.DOTALL)
                        if json_match:
                            try:
                                args = json.loads(json_match.group(1))
                            except:
                                args = {}
                        else:
                            # Fallback to arg format
                            args = {}
                            arg_matches = re.findall(r'<｜DSML｜(?:arg|parameter) name="([^"]+)">(.*?)</｜DSML｜(?:arg|parameter)>', block, re.DOTALL)
                            for key, value in arg_matches:
                                args[key] = value.strip()
                    if func_name == "add_task":
                        print(f"Args for add_task (content): {args}")
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
                        "tool_call_id": f"call_{func_name}",
                        "content": result_text
                    })
                # Отправить результат tools обратно ИИ для финального ответа
                messages.extend(tool_messages)
                data = {
                    "model": "deepseek-chat",
                    "messages": messages
                }
                response = requests.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 200:
                    final_message = response.json()["choices"][0]["message"]
                    content = final_message.get("content", "")
                    content = re.sub(r'<\|.*?\|>', '', content).strip()
                    if not content or '<|' in content:
                        # Если ИИ не сгенерировал ответ или вернул tool calls, запросить его
                        messages.append({"role": "user", "content": "На основе выполненных действий, дай краткий естественный ответ пользователю на русском языке."})
                        data = {
                            "model": "deepseek-chat",
                            "messages": messages
                        }
                        response = requests.post(url, headers=headers, json=data, timeout=30)
                        if response.status_code == 200:
                            final_message = response.json()["choices"][0]["message"]
                            content = final_message.get("content", "Запрос обработан.")
                            content = re.sub(r'<\|.*?\|>', '', content).strip()
                            if '<|' in content:
                                content = "Запрос обработан."
                        else:
                            content = "Запрос обработан."
                    content = clean_content(content)
                    return content
                else:
                    return "Ошибка ответа."
            elif "tool_calls" in message_response:
                # Выполнить tool calls
                tool_messages = []
                # Добавить assistant message с tool_calls
                messages.append(message_response)
                for tool_call in message_response["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except:
                        args = parse_tool_arguments(tool_call["function"]["arguments"])
                    if func_name == "add_task":
                        print(f"Args for add_task (tool_calls): {args}")
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
                response = requests.post(url, headers=headers, json=data, timeout=30)
                if response.status_code == 200:
                    final_message = response.json()["choices"][0]["message"]
                    content = final_message.get("content", "")
                    content = re.sub(r'<\|.*?\|>', '', content).strip()
                    if not content or '<|' in content:
                        # Если ИИ не сгенерировал ответ или вернул tool calls, запросить его
                        messages.append({"role": "user", "content": "На основе выполненных действий, дай краткий естественный ответ пользователю на русском языке."})
                        data = {
                            "model": "deepseek-chat",
                            "messages": messages
                        }
                        response = requests.post(url, headers=headers, json=data, timeout=30)
                        if response.status_code == 200:
                            final_message = response.json()["choices"][0]["message"]
                            content = final_message.get("content", "Расскажите подробнее.")
                            content = re.sub(r'<\|.*?\|>', '', content).strip()
                        else:
                            content = "Расскажите подробнее."
                    content = clean_content(content)
                    return content
                else:
                    return "Ошибка ответа."
            else:
                content = message_response.get("content", "")
                content = re.sub(r'<\|.*?\|>', '', content).strip()
                if not content:
                    # Если ИИ не сгенерировал ответ, запросить его
                    messages.append({"role": "user", "content": "Дай естественный ответ на запрос пользователя на русском языке."})
                    data = {
                        "model": "deepseek-chat",
                        "messages": messages
                    }
                    response = requests.post(url, headers=headers, json=data, timeout=30)
                    if response.status_code == 200:
                        final_message = response.json()["choices"][0]["message"]
                        content = final_message.get("content", "Расскажите подробнее.")
                        content = re.sub(r'<\|.*?\|>', '', content).strip()
                    else:
                        content = "Расскажите подробнее."
                content = clean_content(content)
                return content
        else:
            return "Ошибка."
    except Exception as e:
        print(f"Error in chat_with_ai: {e}")
        return "Ошибка."

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
        system_prompt = f"{base_prompt}\nТы генерируешь краткое напоминание о задаче '{task_title}'. Будь мотивирующим, строгим, краток (1-2 предложения).{user_memory}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о задаче: {task_title}"}
        ]
        
        data = {
            "model": "deepseek-chat",
            "messages": messages
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
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
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Ошибка генерации вопроса."
    except Exception as e:
        print(f"Error in generate_result_check: {e}")
        return f"Результат задачи '{task_title}'?"

def generate_proactive_message(user_id):
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
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
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
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
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
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."
