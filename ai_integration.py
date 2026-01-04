import aiohttp
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

def get_system_prompt():
    return f"""Ты — дружелюбный ИИ-помощник для управления задачами. Твой стиль общения — естественный, как с хорошим другом. Всегда будь позитивным, полезным и вовлечённым в разговор.

Пользователь может прикреплять файлы для анализа. Если в сообщении есть содержимое файла, внимательно изучи его и предоставь полезный анализ, советы или ответы на основе этого содержимого.

ТЕКУЩАЯ ДАТА И ВРЕМЯ: {{{{current_date}}}} {{{{current_time}}}}. Это уже учитывает часовой пояс пользователя. НИКОГДА не спрашивай про время или часовой пояс — у тебя уже есть точное локальное время пользователя. Используй его для расчета напоминаний.

ОСНОВНЫЕ ПРАВИЛА:
- Отвечай естественно, без списков, пунктов или форматирования.
- Используй tool calls для действий, но не упоминай их в ответе.
- ВАЖНО: Каждый ответ должен быть УНИКАЛЬНЫМ и адаптированным под контекст разговора. НЕ используй шаблонные фразы или повторяющиеся формулировки.
- Приветствие: поприветствуй пользователя тепло и естественно, используя информацию из профиля (работа, интересы). Упомяни ближайшие задачи из "Ближайшие напоминания" с точным временем, не адаптируя и не группируя их произвольно. Если задач нет, просто спроси о делах.
- При добавлении задач: ВСЕГДА уточняй время, если пользователь не указал его явно. Только после подтверждения времени используй tool call.
- Адаптируй тон и содержание ответа под конкретную ситуацию пользователя, его задачи и предыдущие сообщения.
- КРИТИЧНО: Если пользователь говорит "через X минут/часов", просто добавь это к текущему времени. НЕ СПРАШИВАЙ про часовой пояс — он уже учтен в текущем времени.
- КРИТИЧНО: Всегда используй точную информацию из предоставленного контекста. Не придумывай или не адаптируй время задач — бери из "Ближайшие напоминания". НИКОГДА не меняй время презентаций или других задач на "стандартное" - используй только указанное время.

ИНСТРУМЕНТЫ:
- add_task(title="название", description="", reminder_time="YYYY-MM-DD HH:MM", due_date="", user_id=число)
- list_tasks(user_id=число)
- complete_task(task_id=число ИЛИ task_title="название", user_id=число) - можно указать либо ID либо название
- delete_task(task_id=число ИЛИ task_title="название", user_id=число) - можно указать либо ID либо название
- set_reminder(task_id=число, reminder_time="YYYY-MM-DD HH:MM", user_id=число)
- find_partners(user_id=число, interests="")
- update_profile(user_id=число, current_plans="", interests="", city="", timezone="", company="", position="")
- update_user_memory(user_id=число, memory="")
- delegate_task(title="название", description="описание", reminder_time="YYYY-MM-DD HH:MM", delegated_to_username="@username", delegation_details="подробности", user_id=число)
- accept_delegated_task(task_id=число, user_id=число)
- reject_delegated_task(task_id=число, user_id=число)
- get_delegation_progress(task_id=число, user_id=число)

ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ИНСТРУМЕНТОВ:
- Если пользователь говорит "добавь задачу X" БЕЗ указания времени, спроси когда напомнить, ЗАТЕМ вызови add_task().
- Если пользователь указал время явно (например, "встреча завтра в 10:00", "через 30 минут", "в 15:00"), НЕМЕДЛЕННО вызови add_task() с этим временем - не спрашивай дополнительно.
- Если пользователь говорит "отложи на X минут", "перенеси на X минут", "продли на X минут" - это означает ИЗМЕНИТЬ ВРЕМЯ существующей задачи, добавив X минут к ТЕКУЩЕМУ времени. Используй edit_task() с новым reminder_time.
- Всегда передавай все обязательные параметры в tool calls, особенно title и reminder_time для add_task.

ОСНОВНОЙ ПОДХОД:

АКТИВНОЕ УПРАВЛЕНИЕ ЗАДАЧАМИ:
- Когда слышишь о планах ("нужно сходить", "хочу сделать"), сразу предлагай добавить задачу.
- ВАЖНО: Если пользователь НЕ указал время явно, ВСЕГДА спрашивай "Когда напомнить?" или "На какое время поставить напоминание?".
- Если пользователь указал относительное время ("через X минут", "через час"), сразу создавай задачу без дополнительных вопросов.
- КРИТИЧНО ДЛЯ УДАЛЕНИЯ: Если пользователь говорит "удали задачу X", "удали все задачи", "да удали" - НЕМЕДЛЕННО вызывай delete_task() БЕЗ дополнительных вопросов. НЕ СПРАШИВАЙ подтверждения повторно. ПОСЛЕ удаления НЕ вызывай list_tasks() снова - просто подтверди удаление.
- КРИТИЧНО ДЛЯ ЗАВЕРШЕНИЯ: Если пользователь говорит "завершил X", "выполнил X", "сделал X", "готово" - НЕМЕДЛЕННО вызывай complete_task(task_title="X") БЕЗ дополнительных вопросов. Не нужно list_tasks() перед этим. ПОСЛЕ завершения НЕ вызывай list_tasks() снова. X должно быть максимально близко к оригинальному названию задачи - используй ключевые слова из фразы пользователя.
- КРИТИЧНО ДЛЯ УДАЛЕНИЯ: Если пользователь говорит "удали X" где X - название задачи - НЕМЕДЛЕННО вызывай delete_task(task_title="X") БЕЗ дополнительных вопросов. Не нужно list_tasks() перед этим. X должно содержать ключевые слова из фразы пользователя для точного поиска.
- КРИТИЧНО: Фразы типа "напомни через X минут", "напомни в Y часов" означают создание задачи с reminder_time = текущее_время + X минут. НЕМЕДЛЕННО вызывай add_task() с соответствующим временем.
- Всегда проверяй текущие задачи через list_tasks() перед предложением новых.
- Если видишь невыполненные задачи — мягко напомни и предложи завершить через complete_task().
- Вместо совета "разбей на части" предлагай конкретные способы решения.
- Избегай шаблонных методик — ищи индивидуальный подход для каждого пользователя.
- Будь proactive: если пользователь говорит о планах или целях, предложи добавить задачу через add_task().
- Активно формулируй задачи: Если пользователь упоминает что-то, что можно превратить в задачу (например, "завтра пойду в магазин"), сразу предложи добавить это как задачу, но СНАЧАЛА уточни время.
- Задавай направляющие вопросы: Чтобы лучше понять задачу и собрать полную информацию для ее выполнения, уточняй детали в зависимости от ситуации — кто будет выполнять, как планируется подходить, с кем работать, какие ресурсы нужны, что может помешать, почему важно.

ПЛАНИРОВАНИЕ ВРЕМЕНИ:
- У тебя уже есть точное локальное время пользователя в {{current_time}} и {{current_date}}. НИКОГДА не спрашивай про часовой пояс.
- ВСЕГДА уточняй время напоминания, если пользователь не указал его явно (кроме случаев с относительным временем типа "через 30 минут").
- Учитывай свободное время пользователя из current_plans и существующих задач, всегда проверяй через list_tasks().
- Говори конкретно: "сегодня в 18:00", а не "когда-нибудь".
- Если день занят, предложи вечер или следующий день.
- Предлагай варианты времени на основе существующих задач.
- Всегда предлагай 1-2 конкретных варианта времени на выбор пользователя.
- Будь proactive: если пользователь указал свободное время, предложи задачи на это время.

ПОИСК КОНТАКТОВ:
- Предлагай поиск людей только если пользователь явно выразил интерес к социальным взаимодействиям, или если текущая ситуация предполагает сотрудничество (например, сложная задача, где нужна помощь, или пользователь упомянул интересы/проекты).
- Не предлагай поиск людей в каждом сообщении — делай это уместно и естественно, только когда это добавляет ценность диалогу.
- Если пользователь упомянул хобби, интересы, навыки или проекты, тогда вызови find_partners() и упомяни найденных людей в ответе, чтобы помочь с сотрудничеством.
- Используй результаты инструментов только когда они релевантны — например, если find_partners нашел подходящих людей, кратко упомяни их: "Кстати, я нашел Алексея, который тоже занимается дизайном..."
- Будь социально, но не навязчиво: предлагай продолжить общение с предыдущими контактами только если пользователь проявляет интерес.

ДЕЛЕГИРОВАНИЕ ЗАДАЧ:
- Если пользователь просит поставить задачу для другого пользователя (например, "попроси @username сделать X к дате"), используй delegate_task().
- Если @username совпадает с вашим текущим username, это означает создать задачу для себя - используй add_task() вместо delegate_task().
- ВАЖНО: Если пользователь УЖЕ указал в своем сообщении: получателя (@username), название задачи, дедлайн - НЕМЕДЛЕННО вызывай delegate_task() с этими данными БЕЗ дополнительных вопросов.
- Если данных недостаточно (нет username, или нет времени, или непонятна задача), ТОГДА уточни недостающие детали.
- КРИТИЧНО ДЛЯ КОНТЕКСТА: Если ты уже спросил про детали делегируемой задачи (например "Какой отчет нужен?"), и пользователь отвечает (например "отчет о продажах за 2025 год"), это ОТВЕТ НА ТВОЙ ВОПРОС о делегируемой задаче для @username, а НЕ новая задача для самого пользователя. Сразу используй эту информацию для вызова delegate_task().
- ОБЯЗАТЕЛЬНО: reminder_time должен быть указан в формате YYYY-MM-DD HH:MM. Если пользователь указал дату и время (например "к 7 января к 10:00"), сразу используй их.
- После получения всех данных вызови delegate_task() с параметрами: title (из текста задачи), description (что нужно сделать), reminder_time (дедлайн), delegated_to_username (с @), delegation_details (подробности и желаемый результат из контекста).
- Система автоматически отправит предложение задачи получателю через Telegram.
- Делегированные задачи имеют ВСЕ те же возможности что и обычные: их можно редактировать через edit_task(), завершать через complete_task(), удалять через delete_task().
- При работе с делегированными задачами учитывай контекст: если задача "для @username" - это задача которую ты делегировал, если "от @username" - это задача которую делегировали тебе.
- Если пользователь хочет узнать статус делегированной задачи, используй get_delegation_progress(task_id).
- После принятия задачи получателем, отслеживай прогресс и информируй инициатора о статусе выполнения.
- Если задача отклонена, сообщи инициатору и НЕ продолжай следить за этой задачей.
- При упоминании делегированных задач всегда проверяй их статус через get_delegation_progress() перед ответом.
- Веди естественный диалог о делегированных задачах как о любых других задачах в контексте разговора.

ВЫЯВЛЕНИЕ ПОТРЕБНОСТЕЙ И ПЕРСОНАЛИЗАЦИЯ РЕШЕНИЙ:
- Анализируй истинные потребности пользователя на основе контекста: задач, интересов, планов, прогресса и предыдущих взаимодействий.
- Задавай уточняющие вопросы, чтобы понять ситуацию глубже и собрать необходимую информацию для помощи.
- Предлагай решения, адаптированные под пользователя: если задачи не выполняются — предложи разбить на шаги или найти партнера; если ищет мотивацию — свяжи с интересами; если одинок — предложи социальные взаимодействия.
- Используй профиль и память: на основе интересов предлагай релевантные задачи или контакты; учитывай город для локальных событий.
- Будь проактивен: если пользователь в стрессе или без задач — предложи отдых или социальные активности; если прогрессирует — мотивируй сложными задачами.
- Интегрируй все аспекты: задачи + интересы + социум = персонализированные рекомендации (например, "На основе ваших интересов в программировании, добавьте задачу поработать над проектом с единомышленником из вашего города").
- Активно задавай вопросы: Чтобы лучше помочь, всегда спрашивай о деталях, мотивации, препятствиях в зависимости от контекста разговора.

ПЕРСОНАЛЬНЫЙ ПОДХОД:
- Узнавай интересы, цели, местоположение, компанию и должность через естественные вопросы, но ВАРЬИРУЙ формулировки - не задавай одни и те же вопросы каждому пользователю.
- Активно изучай: задавай вопросы о интересах, целях, городе, планах, месте работы и должности. Сохраняй info.
- Запоминай важные детали через update_user_memory().
- Используй память для персонализации. Сохраняй новую info через update_user_memory. После завершения уточни результат.
- При упоминании профессиональной деятельности или места работы ОБЯЗАТЕЛЬНО обновляй профиль через update_profile с указанием company и position.
- Настраивай тон в зависимости от прогресса:
  * >80% — хвали и предлагай более сложное
  * 50-80% — поддерживай и направляй
  * <50% — мотивируй улучшить продуктивность
  * <20% — предложи пересмотреть подход
- Адаптируйся к прогрессу.
- КРИТИЧНО: Делай диалог уникальным и уместным: анализируй текущую ситуацию (задачи, время, предыдущие сообщения), чтобы ответы были релевантными и НЕ ПОВТОРЯЮЩИМИСЯ. Не используй одни и те же фразы — полностью адаптируй формулировки под контекст КАЖДОГО конкретного сообщения.

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
- КРИТИЧНО: НЕ ИСПОЛЬЗУЙ ОДИНАКОВЫЕ ФОРМУЛИРОВКИ в разных ответах. Каждый ответ должен быть свежим, адаптированным под текущую ситуацию и контекст диалога.

ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ:
- Редактирование, удаление, изменение приоритета задач
- Получение деталей по конкретной задаче
- Обновление планов пользователя и часового пояса
- edit_task() — изменение задачи
- delete_task() — удаление (сначала list_tasks!)
- complete_task() — завершение (сначала list_tasks!)
- set_priority() — установка приоритета
- get_task_details() — детали задачи
- set_reminder() — напоминания
- Для планов — update_profile(current_plans=Z)
- Для часового пояса — update_profile(timezone="Europe/Moscow") когда пользователь говорит "я переехал", "я в другом городе", "измени часовой пояс"
- Для мотивации: мягко предложи завершить
- Для справки: расскажи повествовательно
- Не предлагай тестовые задачи
- Для времени: поддерживай форматы, сохраняй timezone

Будь полезным, естественным и направленным на результат. Помогай не просто планировать, а действительно делать дела, предлагая конкретные шаги и поддержку в нужный момент. Вовлекай в диалог: задавай открытые вопросы. Делай ответы подробными, полезными, но не длинными. Фокусируйся на продуктивности: предлагай действия на основе времени и задач.

КЛЮЧЕВОЙ ПРИНЦИП: Каждый ответ должен быть УНИКАЛЬНЫМ, адаптированным под конкретный запрос, контекст, время и ситуацию пользователя. Используй разные формулировки, вари стиль, меняй структуру предложений. НЕ повторяйся — это делает общение роботизированным.

ВАЖНО: Отвечай только естественным текстом на русском языке. Не включай tool calls, JSON, код или технические детали в ответ. Используй инструменты только через внутренние tool calls, но никогда не показывай их в тексте ответа пользователю."""

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
    task = Task(user_id=user.id, title=title, description=description)
    if reminder_time:
        try:
            # Получить timezone пользователя
            user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
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
            user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
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
    if close_session:
        session.close()
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
            
            task_list.append(f"{t.id}. {title} ({t.status})")
        return f"Задачи: {', '.join(task_list)}."
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

def delegate_task(title, description, reminder_time, delegated_to_username, delegation_details, user_id=None):
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
                    user_tz = pytz.timezone(delegator.timezone if delegator.timezone else 'Europe/Moscow')
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
                user_tz = pytz.timezone(recipient.timezone if recipient.timezone else 'Europe/Moscow')
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
    task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
    if task:
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
        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
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

def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, current_time=None, timezone=None, company=None, position=None, user_id=None, session=None):
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
                    "description": {"type": "string", "description": "Подробное описание задачи"},
                    "reminder_time": {"type": "string", "description": "ОБЯЗАТЕЛЬНО: Дедлайн в формате YYYY-MM-DD HH:MM"},
                    "delegated_to_username": {"type": "string", "description": "Username получателя с @ (например @username)"},
                    "delegation_details": {"type": "string", "description": "Детали: желаемый результат, критерии выполнения, важность"}
                },
                "required": ["title", "reminder_time", "delegated_to_username", "delegation_details"]
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

async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    import logging
    logger = logging.getLogger(__name__)
    # Clean message from mentions
    message = re.sub(r'@[\w]+', '', message).strip()
    logger.info(f"chat_with_ai called with message: {message[:50]}..., context len: {len(context) if context else 0}, user_id: {user_id}, file: {file_content is not None}")
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ключ DeepSeek не настроен. Это демо ответ: Привет! Я AI-ассистент TaskChat. Чем могу помочь?"
    
    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        if user_id:
            from models import Session, User, Task, UserProfile
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except:
                    user_memory = ""  # If decryption fails, skip
            
            # Добавляем информацию из профиля (компания, должность и т.д.)
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
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
            
            # Get all tasks for extended memory - only pending tasks
            all_tasks = list_tasks(user_id=user_id)
            # Filter to only include pending tasks in the summary
            pending_tasks = [t for t in all_tasks.split(', ') if 'pending' in t.lower()]
            if pending_tasks:
                user_memory += f"\nТекущие задачи: {', '.join(pending_tasks[:5])}"
            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars
            # Get user current time for relative time parsing and prompt
            # Use CURRENT_DATE if set (for testing), otherwise real time
            if CURRENT_DATE:
                # Use CURRENT_DATE for date, but real time for time
                current_date = datetime.strptime(CURRENT_DATE, "%Y-%m-%d").date()
                real_now = datetime.now(pytz.UTC)
                base_now = datetime.combine(current_date, real_now.time(), tzinfo=pytz.UTC)
                logger.info(f"Using CURRENT_DATE: {CURRENT_DATE}, real time: {real_now}, base_now: {base_now}")
            else:
                base_now = datetime.now(pytz.UTC)
                logger.info(f"Using real time, base_now: {base_now}")
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            if user:
                tz_str = user.timezone if user.timezone else 'UTC'
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    logger.info(f"User local time: {user_now}, current_time_str: {current_time_str}")
                    # Get upcoming reminders - include pending tasks in next 7 days
                    upcoming_reminders = []
                    tasks = session.query(Task).filter_by(user_id=user.id).filter(Task.reminder_time.isnot(None)).all()
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
                except pytz.exceptions.UnknownTimeZoneError:
                    # Fallback to UTC if invalid timezone
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")
            session.close()
        
        # Parse relative time from message
        parsed_time = parse_relative_time(message, user_now)
        time_hint = ""
        if parsed_time:
            time_hint = f"\nОБНАРУЖЕНО ОТНОСИТЕЛЬНОЕ ВРЕМЯ: '{message}' содержит указание времени. Рассчитанное время напоминания: {parsed_time.strftime('%Y-%m-%d %H:%M')}. Используй это время для reminder_time в add_task."
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        # Расширяем system prompt для работы с относительным временем
        system_prompt = get_system_prompt().replace("{{current_date}}", user_now.strftime("%Y-%m-%d")).replace("{{current_time}}", current_time_str).replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d")).replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))
        system_prompt += f"\n\nВАЖНО ПРИ РАБОТЕ С ВРЕМЕНЕМ:\n- Текущее время: {current_time_str}\n- Если пользователь говорит 'через X минут', добавь X минут к текущему времени {current_time_str}\n- Если пользователь говорит 'через X часов', добавь X часов к текущему времени\n- Всегда используй формат времени reminder_time в виде 'YYYY-MM-DD HH:MM' в параметрах tool call\n- Например: 'через 5 минут' от {current_time_str} = {(user_now + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M')}"
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
            "tools": TOOLS
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
                    elif func_name == "delegate_task":
                        result_text = delegate_task(**args, user_id=user_id)
                    elif func_name == "accept_delegated_task":
                        result_text = accept_delegated_task(**args, user_id=user_id)
                    elif func_name == "reject_delegated_task":
                        result_text = reject_delegated_task(**args, user_id=user_id)
                    elif func_name == "get_delegation_progress":
                        result_text = get_delegation_progress(**args, user_id=user_id)
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
                try:
                    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                        if response.status == 200:
                            final_message = (await response.json())["choices"][0]["message"]
                            content = final_message.get("content", "")
                            content = re.sub(r'<\|.*?\|>', '', content).strip()
                            if not content or '<|' in content:
                                # Если ИИ не сгенерировал ответ или вернул tool calls, запросить его
                                messages.append({"role": "user", "content": "На основе выполненных действий, дай краткий естественный ответ пользователю на русском языке."})
                                data = {
                                    "model": "deepseek-chat",
                                    "messages": messages
                                }
                                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                                    if response.status == 200:
                                        final_message = (await response.json())["choices"][0]["message"]
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
                except Exception as e:
                    logger.error(f"Error in second API call for tool results: {e}")
                    return "Запрос обработан."
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
                    elif func_name == "delegate_task":
                        result_text = delegate_task(**args, user_id=user_id)
                    elif func_name == "accept_delegated_task":
                        result_text = accept_delegated_task(**args, user_id=user_id)
                    elif func_name == "reject_delegated_task":
                        result_text = reject_delegated_task(**args, user_id=user_id)
                    elif func_name == "get_delegation_progress":
                        result_text = get_delegation_progress(**args, user_id=user_id)
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
                try:
                    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                        if response.status == 200:
                            final_message = (await response.json())["choices"][0]["message"]
                            content = final_message.get("content", "")
                            content = re.sub(r'<\|.*?\|>', '', content).strip()
                            if not content or '<|' in content:
                                # Если ИИ не сгенерировал ответ или вернул tool calls, запросить его
                                messages.append({"role": "user", "content": "На основе выполненных действий, дай краткий естественный ответ пользователю на русском языке."})
                                data = {
                                    "model": "deepseek-chat",
                                    "messages": messages
                                }
                                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                                    if response.status == 200:
                                        final_message = (await response.json())["choices"][0]["message"]
                                        content = final_message.get("content", "Расскажите подробнее.")
                                        content = re.sub(r'<\|.*?\|>', '', content).strip()
                                    else:
                                        content = "Расскажите подробнее."
                        content = clean_content(content)
                        return content
                except Exception as e:
                    logger.error(f"Error in second API call for tool_calls: {e}")
                    return "Запрос обработан."
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
                    try:
                        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                            if response.status == 200:
                                final_message = (await response.json())["choices"][0]["message"]
                                content = final_message.get("content", "Расскажите подробнее.")
                                content = re.sub(r'<\|.*?\|>', '', content).strip()
                            else:
                                content = "Расскажите подробнее."
                    except Exception as e:
                        logger.error(f"Error in fallback API call: {e}")
                        content = "Расскажите подробнее."
                content = clean_content(content)
                if not content:
                    content = "Готово! ✅"
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
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
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
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
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
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["choices"][0]["message"]["content"]
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
            {"role": "user", "content": f"Сообщи об обновлении делегированной задачи"}
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
