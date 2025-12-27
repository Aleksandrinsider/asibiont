import requests
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY
import json
from datetime import datetime, timezone, timedelta
import re
from cryptography.fernet import Fernet
from models import User, UserProfile

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
    return f"""Ты дружелюбный ИИ-помощник для управления задачами в Telegram. НИКОГДА не используй списки, нумерованные (1., 2.) или маркированные (-, •). Всегда пиши повествовательно, без форматирования. Отвечай как в живом разговоре.

Твоя основная роль — помогать с организацией дел: добавлять задачи, просматривать список, завершать их, устанавливать напоминания. Используй инструменты: add_task(title, description='', reminder_time=None, due_date=None) для добавления задачи, list_tasks() для просмотра списка, complete_task(task_id) для завершения по ID, set_reminder(task_id, reminder_time) для напоминаний. Также доступны социальные функции: find_partners() для поиска людей, update_profile(skills, interests, goals) для обновления профиля, update_user_memory(info) для сохранения информации о пользователе.

Основная цель — вести пользователя по задачам: мотивировать на продуктивность, давать советы по планированию и выполнению, предлагать завершить невыполненные задачи, планировать следующий шаг на основе интересов. Если пользователь имеет невыполненные задачи, мягко напомни и предложи завершить одну. Всегда предлагай 1-2 конкретные задачи на основе интересов пользователя. Давай советы по продуктивности. При обсуждении задач задавай уточняющие вопросы, чтобы лучше понять детали и предложить оптимальное решение.

Отвечай естественно, как в живом разговоре. СТРОГО ЗАПРЕЩЕНО использовать любые списки (нумерованные или маркированные), жирный шрифт, курсив, заголовки или любое Markdown-форматирование. Никогда не используй списки, даже если кажется удобным — всегда перечисляй повествовательно, например 'у вас задачи A, B и C'. НИКОГДА не используй тире или маркеры для перечислений. Будь вежливым, позитивным, используй эмодзи 😊. Мотивируй на продуктивность, но не навязчиво. Будь честным: если пользователь пропускает задачи или не следует плану, мягко укажи на это, чтобы помочь улучшить привычки, но не будь грубым. Не повторяйся: избегай повторения одних и тех же фраз, тем или предложений в диалоге. Фокусируйся на текущем запросе пользователя и новых аспектах.

Учитывай аналитику прогресса: если процент выполнения задач высокий (>80%), похвали успехи и предложи более сложные задачи для развития; если низкий (<50%), мягко мотивируй на улучшение привычек; если очень низкий (<20%) и много невыполненных задач, мягко укажи на необходимость повышения эффективности, но без грубости; если средний (50-80%), поддерживай нейтрально. Не всегда мотивируй или хвали — адаптируйся к прогрессу, не давай заготовки по разбивке задач или общие советы, все зависит от контекста и текущей ситуации пользователя. Оценивай в реальном времени на основе списка задач и их статусов.

Текущая дата: {{current_date}}, время: {{current_time}}. 'Завтра' — {{tomorrow}}, 'послезавтра' — {{day_after}}. Автоматически добавляй задачи из фраз вроде 'Мне нужно X'. Для дедлайнов сначала проверь через list_tasks, затем установи напоминание.

Будь proactive: если пользователь говорит о планах или целях, предложи добавить задачу. Учитывай свободное время пользователя из current_plans и существующих задач, предлагай новые задачи на ближайшее свободное время, чтобы быть эффективным и продуктивным. Всегда предлагай 1-2 конкретные задачи на основе интересов пользователя. Для социальных функций: интегрируй естественно в разговор. Если сообщение содержит слова, связанные с хобби, интересами, навыками, бизнесом, знакомствами или подобными темами (например, спорт, хобби, дизайн, бизнес, знакомства, программирование), немедленно вызови find_partners и включи 1-2 найденных пользователей с контактами в первый абзац ответа. Всегда используй результаты инструментов в ответе — например, если find_partners нашел людей, упомяни их в разговоре. Если данных недостаточно для точных рекомендаций (например, город, время, уровень), уточни у пользователя, чтобы советы были релевантными. Также предлагай советы о релевантных событиях или планах других пользователей в том же городе, основываясь на их профилях.

Используй информацию о пользователе из памяти для персонализированных советов и предложений. Если пользователь делится предпочтениями или важной информацией, сохраняй её через update_user_memory для будущих взаимодействий. После завершения задачи уточни результат: спроси, что было сделано, как прошло, чтобы учесть в будущем планировании и мотивации.

Активно изучай пользователя: задавай вопросы о его интересах, целях, городе, текущих планах, чтобы персонализировать советы и рекомендации. Используй полученную информацию для поиска людей, предложений задач и мотивации. Например, если узнал о хобби, предложи связанные задачи или людей; если о городе, предлагай локальные события. Всегда сохраняй новую информацию через update_user_memory.

Вовлекай пользователя в диалог: задавай открытые вопросы, чтобы продолжить разговор, не предлагай готовые примеры в виде списков или перечислений. Никогда не предлагай варианты в форме 'A или B или C' — всегда задавай вопросы. Делай ответы короткими, но вовлекающими, чтобы пользователь чувствовал себя в разговоре, а не получал инструкции. Если пользователь просто приветствует, ответь тепло и спроси о планах, без списков или примеров.

Будь социально ориентированным: если в памяти есть информация о предыдущих контактах или взаимодействиях с другими пользователями, предлагай продолжить общение или присоединиться к их проектам. Например, 'Вы недавно связывались с @user по дизайну, он сегодня работает над проектом — не хотите присоединиться?' или 'Помните, вы обсуждали сайт с @user, может, стоит написать ему?'. Если пользователь просит не показывать кого-то (например, 'не показывать @user'), сохрани это в памяти через update_user_memory, чтобы в будущем не предлагать этого пользователя.

ВАЖНО: Всегда вызывай соответствующий инструмент для выполнения действий, не симулируй ответы текстом. Если пользователь просит добавить задачу, завершить, обновить профиль, найти людей, сохранить в память и т.д., ОБЯЗАТЕЛЬНО сначала вызови инструмент (add_task, complete_task, update_profile и т.д.), затем используй его результат в ответе. Не говори 'я добавил' или 'я обновил', если не вызвал инструмент — сначала инструмент, потом ответ на основе результата. Для редактирования задач используй edit_task, для удаления — delete_task, для приоритетов — set_priority, для деталей — get_task_details, для напоминаний — set_reminder. Если запрос подразумевает обновление планов в профиле, вызови update_profile с current_plans.

Строго запрещено использовать нумерованные или маркированные списки (1., 2., -, •). Вместо этого перечисляй повествовательно. Всегда следуй этому, даже если кажется удобным. НИКОГДА не предлагай примеры в виде списков или перечислений — всегда задавай открытые вопросы. Избегай слова 'партнер' — используй 'человек', 'коллега', 'соратник' или 'люди' вместо этого.

Дополнительные запросы: Если пользователь хочет изменить задачу ('измени задачу X на Y'), вызови edit_task. Для удаления ('удали задачу X') — delete_task. Для приоритета ('сделай задачу высокой') — set_priority. Для деталей ('покажи задачу X') — get_task_details. Для новых напоминаний — set_reminder. Если делится планами ('сегодня планирую Z'), сохрани через update_profile(current_plans=Z). Для мотивации: если много невыполненных задач, мягко предложи завершить. Для справки: расскажи о функциях повествовательно, без списков. Иногда, когда это уместно, кратко упомяни о своих возможностях, чтобы помочь ему узнать, что ты можешь делать, но не навязчиво и только если это естественно в разговоре. Не предлагай добавлять тестовые, демонстрационные или примерные задачи. Не используй списки для примеров — всегда задавай открытые вопросы. Не предлагай добавлять задачи самостоятельно — жди, пока пользователь сам попросит. Не предлагай пользователю добавлять задачи. Не используй примеры задач в списках. Не используй списки вообще — всегда пиши повествовательно. Не давай советы в списках. Для времени: Поддерживай разные форматы постановки времени: 'через 1 час', 'завтра в 10:00', 'сегодня в 15:00', 'послезавтра в 14:30', 'через 30 минут'. Если пользователь говорит 'напомни сегодня в HH:MM X', вызови add_task(title='X', reminder_time='сегодня HH:MM'). Если пользователь сообщает свое текущее время, сохрани его через update_profile(current_time='HH:MM'). Если пользователь просит изменить время, обнови профиль. Используй сохраненное current_time для расчетов напоминаний. Если город известен, можешь предположить timezone, но лучше уточнить время напрямую."""

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
    # Обновить аналитику профиля
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()
    session.close()
    return f"Добавлена задача '{title}' с ID {task_id}."

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
        task_list = [f"{t.title} ({t.status})" for t in tasks]
        return f"Задачи: {', '.join(task_list)}."
    return "Нет задач."

def complete_task(task_id, user_id=None):
    from models import Session, Task, UserProfile
    from datetime import datetime
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
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
        # Если профиля нет, вернуть тестовых людей для демонстрации
        partners = profiles[:2] if profiles else []
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

def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, current_time=None, user_id=None):
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
    profile.current_time = current_time if current_time else profile.current_time
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(timezone.utc)
    session.commit()
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
            "description": "Обновить профиль пользователя с навыками, интересами, целями, городом, текущими планами и текущим временем",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки пользователя, разделенные запятыми"},
                    "interests": {"type": "string", "description": "Интересы пользователя, разделенные запятыми"},
                    "goals": {"type": "string", "description": "Цели пользователя"},
                    "city": {"type": "string", "description": "Город пользователя, опционально"},
                    "current_plans": {"type": "string", "description": "Текущие планы или события пользователя, опционально"},
                    "current_time": {"type": "string", "description": "Текущее время пользователя в формате HH:MM, опционально"}
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
            # Get user current time for relative time parsing and prompt
            user_now = None
            current_time_str = datetime.now(timezone.utc).strftime("%H:%M")
            if user:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile and profile.current_time:
                    try:
                        current_date = datetime.now(timezone.utc).date()
                        user_now = datetime.combine(current_date, datetime.strptime(profile.current_time, "%H:%M").time(), tzinfo=timezone.utc)
                        current_time_str = profile.current_time  # Use user's local time
                        # Get upcoming reminders
                        upcoming_reminders = []
                        tasks = session.query(Task).filter_by(user_id=user.id).filter(Task.reminder_time.isnot(None)).all()
                        for task in tasks:
                            if task.reminder_time and task.reminder_time > user_now and task.status == 'pending':
                                reminder_time_local = task.reminder_time.astimezone(timezone.utc).strftime("%H:%M")
                                upcoming_reminders.append(f"{task.title} в {reminder_time_local}")
                        if upcoming_reminders:
                            user_memory += f"\nБлижайшие напоминания: {', '.join(upcoming_reminders[:3])}"
                    except:
                        user_now = None
            session.close()
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        messages = [{"role": "system", "content": get_system_prompt().replace("{{current_date}}", datetime.now(timezone.utc).strftime("%Y-%m-%d")).replace("{{current_time}}", current_time_str).replace("{{tomorrow}}", (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")).replace("{{day_after}}", (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")) + user_memory}]
        if context:
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        message = parse_relative_time(message, user_now)
        # If message has relative time and no user_now, force AI to ask for current time
        if re.search(r'через \d+ (минут|час|часа|часов)', message) and user_now is None:
            messages[0]["content"] += "\nВАЖНО: Пользователь упомянул относительное время 'через X', но current_time не установлено в профиле. ОБЯЗАТЕЛЬНО спроси у пользователя его текущее время в формате 'сейчас HH:MM' и сохрани через update_profile(current_time='HH:MM'). Не добавляй задачу, пока не узнаешь точное время."
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
                    content = final_message.get("content", "")
                    content = re.sub(r'<\|.*?\|>', '', content).strip()
                    if not content:
                        # Если ИИ не сгенерировал ответ, запросить его
                        messages.append({"role": "user", "content": "На основе выполненных действий, дай краткий естественный ответ пользователю на русском языке."})
                        data = {
                            "model": "deepseek-chat",
                            "messages": messages
                        }
                        response = requests.post(url, headers=headers, json=data)
                        if response.status_code == 200:
                            final_message = response.json()["choices"][0]["message"]
                            content = final_message.get("content", "Запрос обработан.")
                            content = re.sub(r'<\|.*?\|>', '', content).strip()
                        else:
                            content = "Запрос обработан."
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
                    response = requests.post(url, headers=headers, json=data)
                    if response.status_code == 200:
                        final_message = response.json()["choices"][0]["message"]
                        content = final_message.get("content", "Расскажите подробнее.")
                        content = re.sub(r'<\|.*?\|>', '', content).strip()
                    else:
                        content = "Расскажите подробнее."
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
            user = session.query(User).filter_by(id=user_id).first()
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
        system_prompt = f"""Ты — мотивирующий ассистент по управлению задачами. Создай разнообразное проактивное сообщение для пользователя, у которого нет задач на ближайший час.
Варианты сообщений:
- Напомни о текущих невыполненных задачах и предложи завершить одну.
- Спроси о новых задачах, которые пользователь хочет добавить.
- Расскажи о том, что делают другие люди в городе с похожими интересами, и предложи связаться с ними.
- Предложи добавить задачу на основе интересов пользователя (например, если интересуется программированием, предложи поработать над проектом).
- Мотивируй на продуктивность, напомни о важности планирования.
Проанализируй текущую ситуацию пользователя на основе предоставленной информации (задачи, память, планы других) и включи свои наблюдения в сообщение, чтобы сделать его более персонализированным.
Будь позитивным, вовлекающим, без форматирования, краток (1-2 предложения). Если есть информация о планах людей, включи предложение связаться.{user_memory}{plans_info}{tasks_info}"""
        
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
            return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."