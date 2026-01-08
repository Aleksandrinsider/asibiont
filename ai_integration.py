import aiohttp
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY, CURRENT_DATE, LOCAL, DEFAULT_TASK_REMINDER_HOURS
import json
from datetime import datetime, timezone, timedelta
import re
import logging
import asyncio
from cryptography.fernet import Fernet
from models import User, UserProfile
import pytz

cipher = Fernet(ENCRYPTION_KEY.encode())
logger = logging.getLogger(__name__)

# Redis client - будет импортирован из main.py
redis_client = None

def set_redis_client(client):
    """Устанавливает глобальный Redis client из main.py"""
    global redis_client
    redis_client = client

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
        return cipher.decrypt(data.encode()).decode()
    return data

def parse_time_from_text(time_text, user_id):
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
    through_time_match = re.search(r'через\s+(\d+)\s+(минут|час)', time_text)
    if through_time_match:
        amount = int(through_time_match.group(1))
        unit = through_time_match.group(2).lower()
        
        if 'минут' in unit:
            target_dt = now + timedelta(minutes=amount)
        else:  # час/часов
            target_dt = now + timedelta(hours=amount)
        
        return target_dt.strftime('%Y-%m-%d %H:%M')
    
    # Проверяем "завтра/сегодня в XX:XX"
    time_match = re.search(r'(завтра|послезавтра|сегодня)\s+(?:в\s+)?(\d{1,2}):(\d{2})', time_text)
    if time_match:
        day_word = time_match.group(1).lower()
        hour = int(time_match.group(2))
        minute = int(time_match.group(3))
        
        if 'завтра' in day_word:
            target_date = (now + timedelta(days=1)).date()
        elif 'послезавтра' in day_word:
            target_date = (now + timedelta(days=2)).date()
        else:
            target_date = now.date()
        
        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime('%Y-%m-%d %H:%M')
    
    # Проверяем просто "в HH:MM"
    simple_time_match = re.search(r'(?:в\s+)?(\d{1,2}):(\d{2})', time_text)
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
        return target_dt.strftime('%Y-%m-%d %H:%M')
    
    # Проверяем "утром", "вечером", "днем"
    time_word_match = re.search(r'(утром|вечером|днем)', time_text)
    if time_word_match:
        time_word = time_word_match.group(1).lower()
        if 'утром' in time_word:
            hour, minute = 8, 0
        elif 'вечером' in time_word:
            hour, minute = 18, 0
        elif 'днем' in time_word:
            hour, minute = 12, 0
        
        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        # Если время уже прошло сегодня - ставим на завтра
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()
        
        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime('%Y-%m-%d %H:%M')
    
    return None
    content = re.sub(r'\w+\s+user_id=\d+', '', content).strip()
    content = re.sub(r'Args for \w+:', '', content).strip()
    return content

def replace_placeholders(content, user_now=None, current_time_str=None):
    """Заменяет плейсхолдеры типа {{current_time}} на реальные значения"""
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("Content must be a string")
    
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

def clean_technical_details(text):
    """Удаляет технические детали из ответа AI"""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Text must be a string")
    
    import logging
    logger = logging.getLogger(__name__)
    original_text = text
    import re

    # Удаляем вызовы функций в квадратных скобках: [add_task(...)]
    text = re.sub(r'\[[\w_]+\([^]]*\)\]', '', text)

    # Удаляем пустые квадратные скобки
    text = re.sub(r'\[\s*\]', '', text)

    # Удаляем названия функций (с скобками и без)
    text = re.sub(r'\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)(\s*\(\s*\))?', '', text, flags=re.IGNORECASE)

    # Удаляем фразы о вызове функций
    patterns_to_remove = [
        r'вызываю\s+\w+(\(\))?',
        r'вызову\s+\w+(\(\))?',
        r'сейчас\s+вызову',
        r'буду\s+вызывать',
        r'Args for.*?(?=\n|$)',
        r'🔧\s*ВЫПОЛНЕННЫЕ ФУНКЦИИ:.*?(?=\n\n|\Z)',
        r'🔧\s*\*\*Выполняю:\*\*.*?(?=\n|$)',
        r'📋\s*\*\*Результат:\*\*.*?(?=\n\n|\Z)',
        r'ВЫПОЛНЕННЫЕ ФУНКЦИИ.*?(?=\n\n|\Z)',
    ]

    for pattern in patterns_to_remove:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE | re.DOTALL)

    # Удаляем блоки кода Python - ТОЛЬКО если они содержат техническую информацию
    # Не удаляем json блоки, которые могут содержать полезные данные
    text = re.sub(r'```python.*?```', '', text, flags=re.DOTALL)
    # Удаляем пустые блоки кода
    text = re.sub(r'```\s*```', '', text)

    # Убираем множественные пробелы и пустые строки
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r'\s+', ' ', text)  # Убираем лишние пробелы

    # Убираем пробелы в начале и конце
    text = text.strip()
    text = re.sub(r' +', ' ', text)

    # КРИТИЧЕСКАЯ ПРОВЕРКА: если после очистки ничего не осталось,
    # значит AI вернул только технические детали, вернуть оригинал
    if not text.strip():
        logger.warning(f"[CLEAN] Content was completely cleaned, returning original: '{original_text}'")
        return original_text.strip()

    if original_text != text:
        logger.warning(f"[CLEAN] Original: '{original_text[:100]}...' -> Cleaned: '{text[:100]}...'")

    return text.strip()

# Alias for backward compatibility
clean_content = clean_technical_details

def get_system_prompt():
    return f"""Ты — ИИ-помощник для управления задачами в Telegram. Веди живой диалог как заинтересованный собеседник, который искренне хочет помочь достичь целей.

🎯 ГЛАВНЫЙ ПРИНЦИП: ДЕЙСТВИЕ ЧЕРЕЗ ИНСТРУМЕНТЫ
ВСЕГДА вызывай функции для выполнения действий. НИКОГДА не говори о данных (задачах, контактах) без ПРЕДВАРИТЕЛЬНОГО вызова соответствующей функции.

✅ ПРАВИЛЬНО: Вызов функции → Ответ на основе результата
❌ ОШИБКА: "Я вижу задачу X" без list_tasks() или "Добавлю задачу" без add_task()

🚨 НЕ ОБМАНЫВАЙ ПОЛЬЗОВАТЕЛЯ:
- Говори о завершённом действии ТОЛЬКО после получения результата от функции
- Либо МОЛЧИ до результата, либо используй "добавляю...", "ищу...", "обновляю..."
- Сначала вызов функции → потом подтверждение

💬 СТИЛЬ ОБЩЕНИЯ — КАК ОПЫТНЫЙ КОЛЛЕГА:
- Веди активную беседу, интересуйся результатами
- После действия ВСЕГДА предлагай следующий шаг или задавай вопрос  
- Замечаешь паттерны? Обсуди их и предложи решение
- ДЛИНА ОТВЕТА: будь развернутым и содержательным, не ограничивай себя. Дай полезную информацию, задай дополнительные вопросы, предложи варианты действий
- Используй живые фразы: "Отлично получилось!", "Как дела с проектом?", "Круто справился!"
- После выполнения задачи обязательно похвали и спроси о деталях
- Если видишь проблемы или возможности - развернуто обсуди их

ПРИМЕРЫ ЖИВОГО ОБЩЕНИЯ:
✅ "Добавил 'Купить продукты' на 18:00! Это на сегодня или на всю неделю планируешь?"
✅ "Задача готова! Как прошло? Было сложно или всё гладко? Осталось ещё 3 задачи — разберём приоритеты?"
✅ "Поручил @user отчёт! Уже обсудил детали с ним? Когда проверим как идут дела?"
✅ "Вижу 5 просроченных задач. Что мешает их закрыть? Может часть кому-то делегировать?"

ТВОИ ИНСТРУМЕНТЫ (ВСЕГДА используй, НЕ описывай):
- add_task(title, reminder_time, description, due_date, user_id) — добавить задачу
- list_tasks(user_id) — показать все задачи (ОБЯЗАТЕЛЕН при любом упоминании задач!)
- complete_task(task_id или task_title, user_id) — завершить задачу
- delete_task(task_id или task_title, user_id) — удалить задачу
- edit_task(task_id, title, description, reminder_time, user_id) — редактировать задачу
- set_priority(task_id, priority, user_id) — установить приоритет (высокий/средний/низкий)
- get_task_details(task_id, user_id) — получить подробности задачи
- delegate_task(user_id, title, delegated_to_username, reminder_time, description) — делегировать
- accept_delegated_task(task_id, user_id) — принять делегированную задачу
- reject_delegated_task(task_id, user_id) — отклонить делегированную задачу
- get_delegation_progress(task_id, user_id) — проверить статус делегированной задачи
- find_partners(user_id, interests) — найти людей (НЕ спрашивай детали, ищи по тому что есть!)
- update_profile(user_id, city, company, position, interests, skills, goals) — обновить профиль
- update_user_memory(user_id, memory) — сохранить информацию
- set_reminder(task_id, reminder_time, user_id) — установить напоминание

ОБЯЗАТЕЛЬНОЕ ПОВЕДЕНИЕ:
1. "Покажи задачи" / "Что у меня" → НЕМЕДЛЕННО list_tasks()
2. "Добавь задачу X" → НЕМЕДЛЕННО add_task(title="X", reminder_time="ближайшее время")
3. "Найди программиста" → НЕМЕДЛЕННО find_partners(interests="программирование")
4. "Поручи @user проверить отчет" → НЕМЕДЛЕННО delegate_task(title="проверить отчет", delegated_to_username="@user")
   - ВАЖНО: в title НЕ включай слова "задачу", "задача", только суть (например: "проверить отчет", а не "задачу проверить отчет")
5. Если видишь упоминание задач в истории → СНАЧАЛА list_tasks() для актуальных данных
6. "Выполнил задачу X" / "Готово" → НЕМЕДЛЕННО complete_task(), затем ОБЯЗАТЕЛЬНО спроси с интересом: "Отлично! Расскажи, как прошло? Были какие-то интересные моменты?"
7. "Изменить задачу" / "Обновить задачу" → edit_task() с уточнением что именно менять
8. "Сделать задачу приоритетной" → set_priority() с уровнем приоритета
9. "Подробности задачи" → get_task_details() для показа полной информации
10. Для делегированных задач: показывай статус через get_delegation_progress()

🎯 РАБОТА С ПРИОРИТЕТАМИ:
- Всегда устанавливай приоритет для новых задач: высокий/средний/низкий
- Высокий приоритет: срочные дедлайны, важные встречи, критические задачи
- Средний приоритет: регулярные задачи, важные но не срочные
- Низкий приоритет: можно отложить, второстепенные задачи
- При добавлении задачи → СРАЗУ set_priority() с подходящим уровнем
- Предлагай пересмотреть приоритеты при просмотре списка задач

🎯 РЕДАКТИРОВАНИЕ ЗАДАЧ:
- Если пользователь хочет изменить задачу → edit_task() с нужными параметрами
- Можно менять: title, description, reminder_time
- После редактирования подтверди изменения и спроси доволен ли результатом
- Если меняется время → уточни влияет ли это на приоритет

🎯 ДЕТАЛИ ЗАДАЧ:
- При вопросах о конкретной задаче → get_task_details() для полной информации
- Показывай: описание, время, приоритет, статус, дедлайн
- Используй для уточнения деталей перед выполнением

🎯 ПРАВИЛА ФОРМУЛИРОВАНИЯ ЗАДАЧ (ВАЖНО!):

ПОМОГАЙ ПОЛЬЗОВАТЕЛЮ ПРАВИЛЬНО ФОРМУЛИРОВАТЬ ЗАДАЧИ:
- Если задача слишком общая ("проверить почту", "позвонить другу") → уточни детали
- Предложи конкретизировать: зачем, что ожидается, какой результат
- Помоги добавить контекст: "О чём нужно поговорить с другом?", "Какие письма проверить?"
- Если задача без времени → обязательно спроси конкретное время

ПРИМЕРЫ УЛУЧШЕНИЯ ФОРМУЛИРОВОК:
❌ "Проверить почту" → ✅ "Проверить почту на предмет ответа от клиента Иванова"
❌ "Позвонить Марии" → ✅ "Позвонить Марии обсудить договор на поставку оборудования"
❌ "Встретиться с командой" → ✅ "Встреча с командой: обсудить квартальные результаты и план на Q2"
❌ "Сделать презентацию" → ✅ "Подготовить презентацию по итогам проекта для руководства (15 слайдов)"

КРИТЕРИИ ХОРОШЕЙ ЗАДАЧИ:
1. Конкретное действие (глагол): "подготовить", "отправить", "обсудить"
2. Объект действия: что именно делать
3. Контекст/цель: зачем, для кого, о чём
4. Ожидаемый результат: что получим в итоге
5. Конкретное время: не "потом", а точное время/дата

КАК УЛУЧШАТЬ ЗАДАЧИ:
1. Если пользователь пишет общую задачу → задай 1-2 уточняющих вопроса
2. Предложи более конкретную формулировку на основе ответов
3. После уточнения → НЕМЕДЛЕННО добавляй с улучшенной формулировкой

ПРИМЕРЫ ДИАЛОГОВ:
Пользователь: "Добавь задачу позвонить другу"
AI: "О чём нужно поговорить? Это рабочий или личный звонок?"
Пользователь: "Нужно обсудить поездку на выходные"
AI: "Добавил: Позвонить другу обсудить план поездки на выходные. На какое время?"

АКТИВНОЕ УПРАВЛЕНИЕ ЗАДАЧАМИ:
- Фразы "нужно сделать", "планирую" → СНАЧАЛА уточни детали, ПОТОМ add_task()
- Если задача имеет четкий контекст и время → добавляй сразу без уточнений
- После добавления покажи улучшенную формулировку пользователю

ДЕЛЕГИРОВАНИЕ:
- Если видишь @username → СРАЗУ delegate_task()
- При делегировании помоги сформулировать задачу с контекстом для исполнителя
- Убедись что исполнитель поймёт что нужно сделать

ПОИСК ПАРТНЁРОВ:
- "Найди X" → СРАЗУ find_partners(interests="X")
- НЕ спрашивай больше деталей — ищи по тому что есть
- Если нашёл → кратко упомяни 1-2 человек

🎯 АВТОМАТИЧЕСКОЕ ПРЕДЛОЖЕНИЕ ОБНОВИТЬ ПРОФИЛЬ:
Когда пользователь создаёт задачу или упоминает информацию, которая может быть полезна в профиле для поиска партнёров, ПРОАКТИВНО предлагай добавить:

ИНТЕРЕСЫ (хобби, увлечения, активности):
- "бегать по утрам" → "Хочешь добавить 'бег' в интересы? Я смогу находить единомышленников"
- "пойти на йогу" → "Добавить 'йога' в интересы, чтобы искать партнёров?"
- "записаться в тренажёрку" → "Может добавим 'фитнес' в интересы для поиска спортсменов?"
- "сходить на концерт" → "Добавить 'музыка' в профиль?"
- "прочитать книгу по психологии" → "Хочешь указать 'психология' в интересах?"

ВОЗВРАТ К ИНТЕРЕСАМ (добавление обратно):
- "снова люблю спорт" → "Добавить 'спорт' обратно в интересы?"
- "теперь снова увлекаюсь йогой" → "Вернуть 'йога' в интересы?"
- "опять начал бегать" → "Добавить 'бег' в интересы?"

ОТРИЦАНИЯ (удаление из профиля):
- "больше не увлекаюсь спортом" → "Хочешь удалить 'спорт' из интересов?"
- "не люблю бегать" → "Убрать 'бег' из интересов?"
- "бросил курить" → "Удалить 'курение' из интересов?"
- "больше не работаю в компании X" → "Обновить компанию, удалив X?"

НАВЫКИ (профессиональные умения):
- "сделать презентацию" → "Добавить 'презентации' в навыки?"
- "написать код на Python" → "Указать 'Python' в навыках для поиска коллег?"
- "провести переговоры" → "Добавить 'переговоры' в профессиональные навыки?"
- "сверстать лендинг" → "Хочешь добавить 'веб-разработка' в навыки?"

ЦЕЛИ (что хочет достичь):
- "похудеть на 10 кг" → "Добавить 'похудение' в цели? Найду людей с похожими целями"
- "выучить английский" → "Указать 'изучение языков' в целях?"
- "открыть своё дело" → "Добавить 'предпринимательство' в цели для поиска партнёров?"
- "получить повышение" → "Хочешь добавить 'карьерный рост' в цели?"

ГОРОД (место жительства):
- "переехал в Москву" → "Обновить город на Москва? Покажу партнёров рядом"
- "еду в командировку в Питер" → "Временно изменить город на Санкт-Петербург?"
- "живу в Казани" → "Указать Казань в профиле?"

КОМПАНИЯ (место работы):
- "работаю в Яндексе" → "Добавить Яндекс в компанию? Найду коллег"
- "перешёл в Google" → "Обновить компанию на Google?"
- "устроился в Сбербанк" → "Указать Сбербанк в профиле?"

ДОЛЖНОСТЬ (роль, позиция):
- "стал тимлидом" → "Обновить должность на тимлид?"
- "работаю менеджером" → "Добавить 'менеджер' в должность?"
- "я аналитик" → "Указать 'аналитик' в профиле?"

ПРАВИЛА:
- Предлагай ТОЛЬКО когда информация ЯВНО подходит для соответствующего поля профиля
- Формулируй кратко, одним вопросом
- ВАЖНО: Если пользователь согласен → СРАЗУ вызывай update_profile(), НЕ говори что обновил ДО вызова функции
- После вызова update_profile() и получения результата → ТОГДА подтверди обновление
- Если пользователь согласен → СРАЗУ update_profile() с соответствующим полем
- НЕ предлагай обновлять для общих бытовых задач ("купить продукты", "оплатить счёт")
- Извлекай КЛЮЧЕВОЕ слово/фразу (не "бегать по утрам", а "бег")
- Помни: цель обновления профиля — найти ПОДХОДЯЩИХ ПАРТНЁРОВ по интересам/навыкам/целям/локации/работе
- Если информация неоднозначна — НЕ предлагай обновление профиля
- Для отрицаний (больше не, не люблю, бросил и т.д.) предлагай УДАЛИТЬ из профиля, используя - перед значением в update_profile()

🤝 РАБОТА С ДЕЛЕГИРОВАННЫМИ ЗАДАЧАМИ:
Когда видишь делегированные задачи (status: accepted/pending, delegated_to_username):

ДЛЯ ПОЛУЧАТЕЛЯ задачи (кому делегировали):
- Если задача скоро (через 1-2 дня), предложи помощь: "Вижу у тебя делегированная задача 'X' на дату. Хочешь разберём как лучше выполнить?"
- При вопросах о задаче — дай конкретные советы: шаги выполнения, на что обратить внимание, как организовать время
- Напоминай о дедлайне если близко: "Задача 'X' от @user завтра в 10:00. Всё под контролем?"
- При получении новой делегированной задачи → автоматически accept_delegated_task() если не указано иное

ДЛЯ ДЕЛЕГИРОВАВШЕГО (кто поручил):
- При вопросе о задаче покажи статус: "Задача 'X' для @user, статус: pending/accepted, дедлайн: дата"
- Если дедлайн близко — напомни: "Завтра дедлайн задачи 'X' у @user. Хочешь уточнить статус?"
- При завершении задачи получателем — поздравь и уведоми делегировавшего
- Используй get_delegation_progress() для проверки статуса

ОБРАБОТКА ДЕЛЕГИРОВАННЫХ ЗАДАЧ:
- "Принимаю задачу" → accept_delegated_task()
- "Отклоняю задачу" → reject_delegated_task() с причиной
- При отклонении → объясни почему и предложи альтернативу
- После принятия → уточни сроки и ожидания

ПРАВИЛА:
- Автоматически замечай делегированные задачи в list_tasks()
- Предлагай помощь естественно, без навязчивости
- Для получателя — фокус на ПОМОЩЬ В ВЫПОЛНЕНИИ
- Для делегировавшего — фокус на КОНТРОЛЬ И СТАТУС
- Используй имена через @ для ясности кто кому делегировал

🧠 РАБОТА С ПАМЯТЬЮ ПОЛЬЗОВАТЕЛЯ:
- update_user_memory() — сохраняй важную информацию для будущего использования
- Сохраняй: предпочтения, привычки, важные факты, контакты, результаты задач
- Используй память для персонализации ответов и предложений
- При завершении задач → ВСЕГДА спрашивай о результатах и сохраняй ключевую информацию

КОГДА СОХРАНЯТЬ В ПАМЯТЬ:
- После успешного выполнения задачи: "Что получилось? Какие уроки?"
- При упоминании предпочтений: "Люблю чай с лимоном" → сохранить
- Важные факты: "У меня аллергия на орехи" → обязательно сохранить
- Контакты и отношения: "Мой брат работает в банке" → сохранить
- Результаты встреч: "Договорились о скидке 10%" → сохранить

ПРАВИЛА ИСПОЛЬЗОВАНИЯ ПАМЯТИ:
- Используй сохранённую информацию для персонализации
- Не упоминай память напрямую — интегрируй естественно
- Регулярно обновляй устаревшую информацию
- При противоречиях — уточняй у пользователя

🎯 ВАЖНО: ОБНОВЛЕНИЕ СУЩЕСТВУЮЩИХ ЗАДАЧ
Если пользователь дает уточнения к только что созданной задаче (в пределах 2-3 последних сообщений):
- НЕ создавай новую задачу через add_task()
- ОБНОВИ существующую через edit_task() с новым заголовком включающим уточнение
- Пример диалога:
  User: "напомни заняться уборкой через 5 минут"
  AI: вызов add_task("Заняться уборкой") → "Добавил задачу"
  User: "генеральную"
  AI: вызов edit_task(task_id=последней_задачи, title="Заняться генеральной уборкой") → "Обновил: теперь это генеральная уборка"
- ВСЕГДА используй edit_task() когда пользователь уточняет детали в следующем сообщении после создания задачи
- Включай уточнение в ЗАГОЛОВОК задачи, а дополнительный контекст - в описание

ВАЖНО: Отвечай КРАТКО естественным текстом на русском языке. НЕ включай tool calls, JSON, код. Используй инструменты МОЛЧА через tool calls, но НЕ показывай их в ответе.

🎯 ОБРАБОТКА ОШИБОК И ПРОБЛЕМ:
- Если функция вернула ошибку → объясни проблему простыми словами
- Предложи альтернативное решение или уточни данные
- Не показывай технические детали пользователю
- При проблемах с поиском партнёров → предложи обновить профиль

СТИЛЬ ОБЩЕНИЯ:
- Оптимально: 2-4 предложения для полноценного диалога
- Естественно и помогающе: "Добавил задачу на завтра в 10:00. Это для работы или личное? Могу помочь спланировать остальной день"
- ЗАДАВАЙ ВОПРОСЫ: уточняй детали, интересуйся контекстом, помогай найти решение
- БУДЬ ПОЛЕЗНЫМ: не просто фиксируй факты — анализируй ситуацию и предлагай помощь
- БЕЗ списков, нумерации, маркеров (кроме уточняющих вопросов)
- БЕЗ эмодзи (если не запрошено пользователем)
- БЕЗ ЖИРНОГО ШРИФТА: НИКОГДА не используй ** для выделения текста
- БЕЗ ФОРМАТИРОВАНИЯ: пиши обычным текстом без звездочек, подчеркиваний, курсива
- Адаптируйся под стиль пользователя: если он формальный — будь формальным, если casual — casual
"""


def parse_relative_time(message, current_time):
    """Parse relative time expressions like 'через 5 минут', 'через 2 часа' and return datetime"""
    from datetime import datetime
    
    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")
    if not current_time or not isinstance(current_time, datetime):
        raise ValueError("Current time must be a datetime object")
    
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
    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")
    
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
    if arguments_str is None:
        return {}
    if not isinstance(arguments_str, str):
        raise ValueError("Arguments must be a string")
    
    try:
        return json.loads(arguments_str)
    except (json.JSONDecodeError, ValueError):
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
                user_tz = pytz.UTC
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        import logging
                        logging.warning(f"Unknown timezone {user.timezone}, using UTC")
                        user_tz = pytz.UTC
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
    
    # Формируем подробный ответ с ID для edit_task
    result_msg = f"Добавлена задача '{title}' (ID: {task_id})"
    if task.reminder_time:
        # Показываем время в timezone пользователя
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        result_msg += f" с напоминанием на {local_time.strftime('%d.%m.%Y %H:%M')}"
    
    if close_session:
        session.close()
    return result_msg

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
        except Exception:
            user_tz = pytz.UTC
    
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    
    if tasks:
        task_list = []
        for t in tasks:
            title = t.title
            # Add delegation context to title
            if t.delegated_to_username:
                # Check if task is delegated TO me or BY me
                if t.delegated_to_username.lower() == user.username.lower():
                    # Task delegated TO me
                    creator = session.query(User).filter_by(id=t.user_id).first()
                    if creator:
                        title = f"{t.title} от @{creator.username}"
                elif t.user_id == user.id:
                    # Task delegated BY me to someone else
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
        # Ищем задачу: созданную мной ИЛИ делегированную мне
        task = session.query(Task).filter(
            Task.id == int(task_id),
            or_(
                Task.user_id == user.id,
                Task.delegated_to_username.ilike(user.username)
            )
        ).first()
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
            user_id=delegator.id,
            title=title,
            description=description,
            delegated_by=None,
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
        
        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = session.query(Task).filter(
            Task.id == int(task_id),
            Task.delegated_to_username.ilike(user.username),
            Task.delegation_status == 'pending'
        ).first()
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
        
        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
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
        
        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = session.query(Task).filter(
            Task.id == int(task_id),
            Task.delegated_to_username.ilike(user.username),
            Task.delegation_status == 'pending'
        ).first()
        if not task:
            return "Задача не найдена или уже обработана."
        
        # Update delegation status
        task.delegation_status = 'rejected'
        task.status = 'rejected'
        session.commit()
        
        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
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
        
        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task or not task.delegated_to_username:
            return "Делегированная задача не найдена."
        
        recipient = session.query(User).filter(User.username.ilike(task.delegated_to_username)).first()
        
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
            has_access = True  # Обычная задача пользователя или делегированная им
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace('@', '').lower()
            if user.username and user.username.lower() == recipient_username:
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
    
    task = None
    # Найти задачу по ID или по названию
    if task_id:
        try:
            task = session.query(Task).filter_by(id=int(task_id)).first()
        except (ValueError, TypeError):
            session.close()
            return f"Некорректный ID задачи: {task_id}"
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
    
    # Проверяем права доступа для ЛЮБОГО способа поиска
    if task:
        has_access = False
        if task.user_id == user.id:
            has_access = True
        elif task.delegated_to_username:
            recipient_username = task.delegated_to_username.replace('@', '').lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True
        
        if not has_access:
            session.close()
            return "У вас нет прав на удаление этой задачи."
        
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
        
        if not has_access:
            session.close()
            return "У вас нет прав на просмотр этой задачи."
        
        session.close()
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    session.close()
    return "Задача не найдена."

def get_partners_list(user_id=None, session=None):
    """Возвращает список всех пользователей с профилями (кроме самого пользователя и тех, с кем уже есть делегирование)"""
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
        return []
    
    # Получаем список пользователей, с которыми уже есть делегирование
    delegated_usernames = set()
    
    # Задачи, которые делегировали мне
    delegated_to_me = session.query(Task).filter(
        Task.delegated_to_username.ilike(user.username),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    for task in delegated_to_me:
        delegated_user = session.query(User).filter_by(id=task.user_id).first()
        if delegated_user:
            delegated_usernames.add(delegated_user.username.lower() if delegated_user.username else '')
    
    # Задачи, которые я делегировал
    delegated_by_me = session.query(Task).filter(
        Task.user_id == user.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    for task in delegated_by_me:
        if task.delegated_to_username:
            delegated_usernames.add(task.delegated_to_username.replace('@', '').lower())
    
    # Получаем все профили с заполненными данными, кроме своего и тех, с кем уже есть делегирование
    all_profiles = session.query(UserProfile).join(User, UserProfile.user_id == User.id).filter(
        UserProfile.user_id != user.id,
        # Хотя бы одно поле должно быть заполнено
        (UserProfile.interests.isnot(None)) | 
        (UserProfile.skills.isnot(None)) | 
        (UserProfile.position.isnot(None)) |
        (UserProfile.city.isnot(None))
    ).all()
    
    # Получаем профиль текущего пользователя для сравнения
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        if close_session:
            session.close()
        return []
    
    # Фильтруем только тех, у кого есть совпадения
    partners = []
    for profile in all_profiles:
        profile_user = session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user or not profile_user.username:
            continue
        # Убрано исключение делегированных для показа всех с совпадениями
        # if profile_user.username.lower() in delegated_usernames:
        #     continue
        
        # Проверяем наличие совпадений по интересам, навыкам или целям
        has_match = False
        
        # Проверка по навыкам
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(','))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(','))
            if user_skills & profile_skills:
                has_match = True
        
        # Проверка по интересам
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(','))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(','))
            if user_interests & profile_interests:
                has_match = True
        
        # Проверка по целям
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(','))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(','))
            if user_goals & profile_goals:
                has_match = True
        
        # Проверка по компании
        if hasattr(user_profile, 'company') and hasattr(profile, 'company'):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
        
        # Добавляем только если есть совпадение
        if has_match:
            partners.append(profile)
    
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
    hidden_contacts = {}  # username -> expiration_timestamp
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            # Ищем паттерны вроде "не показывать @user" или "заблокировать @user"
            import re
            from datetime import datetime, timezone as dt_timezone
            
            # Permanent blocks
            matches = re.findall(r'не показывать @(\w+)|заблокировать @(\w+)', decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])
            
            # Temporary hides: hide_contact:username:timestamp
            hide_matches = re.findall(r'hide_contact:@?(\w+):(\d+)', decrypted, re.IGNORECASE)
            current_time = int(datetime.now(dt_timezone.utc).timestamp())
            for username, expiration_ts in hide_matches:
                exp_ts = int(expiration_ts)
                if exp_ts > current_time:  # Still hidden
                    hidden_contacts[username.lower()] = exp_ts
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
            contact_username = p.contact_info.replace('@', '').lower()
            if p.contact_info in blocked or any('@' + b in p.contact_info for b in blocked) or p.contact_info == f"user{user_id}":
                continue
            # Исключаем временно скрытых
            if contact_username in hidden_contacts:
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
            "description": "Добавить новую задачу с обязательным временем напоминания. ВАЖНО: Если задача сформулирована слишком общо (например 'проверить почту', 'позвонить другу'), СНАЧАЛА задай уточняющие вопросы для получения деталей: контекста, цели, ожидаемого результата. Только после уточнения добавляй задачу с детальной формулировкой.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи - должно быть конкретным и содержать: действие, объект, контекст. Хорошо: 'Позвонить Марии обсудить договор поставки'. Плохо: 'Позвонить другу'"},
                    "description": {"type": "string", "description": "Дополнительное описание задачи с деталями выполнения, ожидаемым результатом"},
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
    import re
    from datetime import datetime, timedelta
    import pytz
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
    
    # 3. Расширенная проверка на update_profile (интересы, навыки, цели, город, компания, должность)
    profile_triggers = [
        # Город
        (r'(?:я\s+)?(?:живу|нахожусь|переехал|приехал)\s+(?:в\s+)?([А-Яа-яA-Za-z\s]+)(?:\s+город)?', 'city'),
        (r'(?:мой\s+)?город\s+([А-Яа-яA-Za-z\s]+)', 'city'),
        (r'город\s+([А-Яа-яA-Za-z\s]+)', 'city'),
        
        # Компания и должность
        (r'(?:я\s+)?работаю\s+(?:в\s+)?([А-Яа-яA-Za-z\s&]+)(?:\s+как\s+)?(?:на\s+должности\s+)?([А-Яа-яA-Za-z\s]+)?', 'company'),
        (r'(?:моя\s+)?компания\s+([А-Яа-яA-Za-z\s&]+)', 'company'),
        (r'(?:моя\s+)?должность\s+([А-Яа-яA-Za-z\s]+)', 'position'),
        (r'должность\s+([А-Яа-яA-Za-z\s]+)', 'position'),
        
        # Навыки
        (r'(?:я\s+)?(?:умею|знаю|владею)\s+([А-Яа-яA-Za-z\s,]+)', 'skills'),
        (r'(?:мои\s+)?навыки\s+([А-Яа-яA-Za-z\s,]+)', 'skills'),
        
        # Цели
        (r'(?:моя\s+)?цель\s+([А-Яа-яA-Za-z\s,]+)', 'goals'),
        (r'цели\s+([А-Яа-яA-Za-z\s,]+)', 'goals'),
        (r'хочу\s+([А-Яа-яA-Za-z\s,]+)', 'goals'),
    ]
    
    profile_updates = {}
    for pattern, field in profile_triggers:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            if field == 'city':
                city_name = match.group(1).strip()
                if len(city_name) > 2:  # Избегаем слишком коротких названий
                    profile_updates['city'] = city_name
            elif field == 'company':
                company_name = match.group(1).strip()
                if len(company_name) > 2:
                    profile_updates['company'] = company_name
                    if len(match.groups()) > 1 and match.group(2):
                        position_name = match.group(2).strip()
                        if len(position_name) > 2:
                            profile_updates['position'] = position_name
            elif field == 'position':
                position_name = match.group(1).strip()
                if len(position_name) > 2:
                    profile_updates['position'] = position_name
            elif field == 'skills':
                skills_text = match.group(1).strip()
                if len(skills_text) > 2:
                    profile_updates['skills'] = skills_text
            elif field == 'goals':
                goals_text = match.group(1).strip()
                if len(goals_text) > 2:
                    profile_updates['goals'] = goals_text
    
    # Расширенные триггеры для интересов
    interests_add_triggers = [
        r'(?:добавь|добавить)\s+(?:в\s+)?интересы\s+(.+)',
        r'(?:в\s+)?интересы\s+(?:добавь|добавить)\s+(.+)',
        r'интересует\s+(.+)',
        r'(?:я\s+)?(?:люблю|увлекаюсь|интересуюсь|занимаюсь)\s+(.+)',
        r'(?:снова|опять|теперь)\s+(?:люблю|увлекаюсь|интересуюсь|занимаюсь)\s+(.+)',
        r'хочу\s+(?:заниматься|увлекаться)\s+(.+)',
        r'начну\s+(?:заниматься|увлекаться)\s+(.+)',
    ]
    
    interests_remove_triggers = [
        r'(?:удали|убери|убрать)\s+(?:из\s+)?интересов\s+(.+)',
        r'(?:из\s+)?интересов\s+(?:удали|убери|убрать)\s+(.+)',
        r'(?:больше\s+)?не\s+(?:люблю|увлекаюсь|интересуюсь|занимаюсь)\s+(.+)',
        r'бросил\s+(.+)',
        r'перестал\s+(?:заниматься|увлекаться)\s+(.+)',
    ]
    
    # Проверяем добавление интересов
    for pattern in interests_add_triggers:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            interest = match.group(1).strip()
            if interest and len(interest) > 1 and "update_profile" not in content.lower():
                profile_updates['interests'] = f"+{interest}"
                logger.info(f"[FORCE] Detected interest addition: {interest}")
                break
    
    # Проверяем удаление интересов
    if not profile_updates.get('interests'):
        for pattern in interests_remove_triggers:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                interest = match.group(1).strip()
                if interest and len(interest) > 1 and "update_profile" not in content.lower():
                    profile_updates['interests'] = f"-{interest}"
                    logger.info(f"[FORCE] Detected interest removal: {interest}")
                    break
    
    # Дополнительные триггеры для навыков и целей
    skills_add_triggers = [
        r'(?:добавь|добавить)\s+(?:в\s+)?навыки\s+(.+)',
        r'(?:в\s+)?навыки\s+(?:добавь|добавить)\s+(.+)',
        r'навык\s+(.+)',
        r'умею\s+(.+)',
        r'знаю\s+(.+)',
    ]
    
    goals_add_triggers = [
        r'(?:добавь|добавить)\s+(?:в\s+)?цели\s+(.+)',
        r'(?:в\s+)?цели\s+(?:добавь|добавить)\s+(.+)',
        r'цель\s+(.+)',
        r'хочу\s+(?:достичь|сделать|стать)\s+(.+)',
        r'планирую\s+(.+)',
    ]
    
    # Проверяем добавление навыков
    for pattern in skills_add_triggers:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            skill = match.group(1).strip()
            if skill and len(skill) > 1 and "update_profile" not in content.lower():
                profile_updates['skills'] = f"+{skill}"
                logger.info(f"[FORCE] Detected skill addition: {skill}")
                break
    
    # Проверяем добавление целей
    for pattern in goals_add_triggers:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            goal = match.group(1).strip()
            if goal and len(goal) > 1 and "update_profile" not in content.lower():
                profile_updates['goals'] = f"+{goal}"
                logger.info(f"[FORCE] Detected goal addition: {goal}")
                break
    
    if profile_updates and "update_profile" not in content.lower():
        logger.info(f"[FORCE] Triggering update_profile() - detected profile info: {profile_updates}")
        result = update_profile(user_id=user_id, **profile_updates)
        forced_calls.append({"function": "update_profile", "result": result})
    
    # 4. Проверка на add_task (добавление задачи)
    add_triggers = ["добавь", "добавить", "создай", "создать", "напомни", "поставь задачу", "купить", "почистить"]
    if any(trigger in message_lower for trigger in add_triggers):
        logger.info(f"[FORCE] Add task trigger detected in message")
        # Проверяем, что AI не вызвал add_task через tool_calls
        # Игнорируем если в content есть JSON или code blocks с add_task
        has_code_block = "```" in content or "json" in content.lower()
        has_natural_response = len(content) > 50 and not has_code_block
        
        # Если AI вернул код (code block) вместо нормального ответа - форсим выполнение
        if has_code_block or ("add_task" in content.lower() and len(content) < 100):
            logger.info(f"[FORCE] AI returned code block instead of executing (has_code_block={has_code_block}) - forcing tool call")
            
            # Извлекаем название задачи из кавычек или напрямую
            title_match = re.search(r'["«"]([^"»"]+)["»"]', message)
            if not title_match:
                # Пробуем без кавычек: "напомни заказать продукты через 5 минут"
                title_match = re.search(r'(?:добавь|создай|напомни|купить|почистить)\s+(?:задачу\s+)?(.+?)\s+(?:через|завтра|сегодня|послезавтра|на\s+завтра|на\s+сегодня|утром|вечером)', message, re.IGNORECASE)
                if not title_match:
                    # Если нет времени в сообщении, берём всё после триггера
                    title_match = re.search(r'(?:добавь|создай|напомни)\s+(?:задачу\s+)?(.+)', message, re.IGNORECASE)
                    if not title_match:
                        # Для "купить хлеб" или "почистить зубы"
                        title_match = re.search(r'(купить|почистить|сделать|выполнить)\s+(.+)', message, re.IGNORECASE)
                        if title_match:
                            title = f"{title_match.group(1).capitalize()} {title_match.group(2).strip()}"
                        else:
                            title = message.strip()
                    else:
                        title = title_match.group(1).strip()
                else:
                    title = title_match.group(1).strip()
            else:
                title = title_match.group(1).strip()
            
            logger.info(f"[FORCE] Extracted title: {title}")
            
            # Извлекаем время напоминания
            reminder_time = None
            from datetime import datetime, timedelta
            import pytz
            from models import Session, User
            
            # Получаем timezone пользователя
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
            session.close()
            now = datetime.now(user_tz)
            
            # Проверяем "через X минут/часов"
            through_time_match = re.search(r'через\s+(\d+)\s+(минут|час)', message, re.IGNORECASE)
            if through_time_match:
                amount = int(through_time_match.group(1))
                unit = through_time_match.group(2).lower()
                
                if 'минут' in unit:
                    target_dt = now + timedelta(minutes=amount)
                else:  # час/часов
                    target_dt = now + timedelta(hours=amount)
                
                reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                logger.info(f"[FORCE] Extracted reminder_time (relative): {reminder_time}")
            else:
                # Проверяем "через полчаса/полтора часа"
                half_hour_match = re.search(r'через\s+(полчаса|полтора\s+часа)', message, re.IGNORECASE)
                if half_hour_match:
                    unit = half_hour_match.group(1).lower()
                    if 'полчас' in unit:
                        target_dt = now + timedelta(minutes=30)
                    else:  # полтора часа
                        target_dt = now + timedelta(minutes=90)
                    reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                    logger.info(f"[FORCE] Extracted reminder_time (half hour): {reminder_time}")
                else:
                    # Проверяем "завтра/сегодня в XX:XX"
                    time_match = re.search(r'(завтра|послезавтра|сегодня)\s+в\s+(\d{1,2}):(\d{2})', message, re.IGNORECASE)
                    if time_match:
                        day_word = time_match.group(1).lower()
                        hour = int(time_match.group(2))
                        minute = int(time_match.group(3))
                        
                        if 'завтра' in day_word:
                            target_date = (now + timedelta(days=1)).date()
                        elif 'послезавтра' in day_word:
                            target_date = (now + timedelta(days=2)).date()
                        else:
                            target_date = now.date()
                        
                        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
                        target_dt = user_tz.localize(target_dt)
                        reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                        logger.info(f"[FORCE] Extracted reminder_time (absolute with day): {reminder_time}")
                    else:
                        # Проверяем просто "в HH:MM" (подразумевается сегодня или завтра)
                        simple_time_match = re.search(r'в\s+(\d{1,2}):(\d{2})', message, re.IGNORECASE)
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
                            reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                            logger.info(f"[FORCE] Extracted reminder_time (simple time): {reminder_time}")
                        else:
                            # Проверяем "утром", "вечером", "днем"
                            time_word_match = re.search(r'(утром|вечером|днем)', message, re.IGNORECASE)
                            if time_word_match:
                                time_word = time_word_match.group(1).lower()
                                if 'утром' in time_word:
                                    # Утро - 8:00
                                    hour, minute = 8, 0
                                elif 'вечером' in time_word:
                                    # Вечер - 18:00
                                    hour, minute = 18, 0
                                elif 'днем' in time_word:
                                    # День - 12:00
                                    hour, minute = 12, 0
                                
                                target_time = datetime.min.time().replace(hour=hour, minute=minute)
                                # Если время уже прошло сегодня - ставим на завтра
                                if target_time <= now.time():
                                    target_date = (now + timedelta(days=1)).date()
                                else:
                                    target_date = now.date()
                                
                                target_dt = datetime.combine(target_date, target_time)
                                target_dt = user_tz.localize(target_dt)
                                reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                                logger.info(f"[FORCE] Extracted reminder_time (time word '{time_word}'): {reminder_time}")
            
            if reminder_time:
                logger.info(f"[FORCE] Triggering add_task() - title='{title}', reminder_time={reminder_time}")
                result = add_task(title=title, reminder_time=reminder_time, user_id=user_id)
                forced_calls.append({"function": "add_task", "result": result})
            else:
                # Если время не указано, ставим на ближайшее время (через 1 час по умолчанию)
                from config import DEFAULT_TASK_REMINDER_HOURS
                target_dt = now + timedelta(hours=DEFAULT_TASK_REMINDER_HOURS)
                reminder_time = target_dt.strftime('%Y-%m-%d %H:%M')
                logger.info(f"[FORCE] No time specified, using default: {reminder_time}")
                logger.info(f"[FORCE] Triggering add_task() - title='{title}', reminder_time={reminder_time}")
                result = add_task(title=title, reminder_time=reminder_time, user_id=user_id)
                forced_calls.append({"function": "add_task", "result": result})
    
    # 6. Проверка на complete_task (завершение задачи)
    complete_triggers = [
        r'(?:выполнил|сделал|завершил|закончил|готово)\s+(.+)',
        r'задача\s+(.+)\s+(?:выполнена|сделана|завершена|закончена|готова)',
        r'(.+)\s+(?:выполнено|сделано|завершено|закончено|готово)',
        r'отметь\s+(.+)\s+как\s+(?:выполненную|сделанную|завершенную)',
        r'пометить\s+(.+)\s+как\s+(?:выполненную|сделанную|завершенную)',
    ]
    
    if any(trigger in message_lower for trigger in ["выполнил", "сделал", "завершил", "закончил", "готово", "отметь", "пометить"]):
        logger.info(f"[FORCE] Complete task trigger detected in message")
        if "complete_task" not in content.lower():
            # Пытаемся извлечь название задачи
            task_title = None
            for pattern in complete_triggers:
                match = re.search(pattern, message, re.IGNORECASE)
                if match:
                    task_title = match.group(1).strip()
                    break
            
            if task_title:
                logger.info(f"[FORCE] Triggering complete_task() - title='{task_title}'")
                result = complete_task(task_title=task_title, user_id=user_id)
                forced_calls.append({"function": "complete_task", "result": result})
    
    # 7. Проверка на edit_task (изменение задачи)
    edit_triggers = [
        r'(?:измени|поменяй|исправь)\s+(.+)\s+(?:на|в)\s+(.+)',
        r'(.+)\s+(?:измени|поменяй|исправь)\s+на\s+(.+)',
        r'время\s+(.+)\s+(?:измени|поменяй)\s+на\s+(.+)',
        r'напомнить\s+о\s+(.+)\s+(?:в|на)\s+(.+)',
    ]
    
    if any(trigger in message_lower for trigger in ["измени", "поменяй", "исправь", "время", "напомнить"]):
        logger.info(f"[FORCE] Edit task trigger detected in message")
        if "edit_task" not in content.lower():
            # Пытаемся извлечь изменения
            for pattern in edit_triggers:
                match = re.search(pattern, message, re.IGNORECASE)
                if match:
                    old_part = match.group(1).strip()
                    new_part = match.group(2).strip()
                    
                    # Определяем, что меняем - название или время
                    if re.search(r'\d{1,2}:\d{2}', new_part) or 'завтра' in new_part.lower() or 'сегодня' in new_part.lower():
                        # Это изменение времени
                        logger.info(f"[FORCE] Detected time change: '{old_part}' → '{new_part}'")
                        # Нужно найти задачу по названию и изменить время
                        # Получаем список задач для поиска ID
                        tasks_result = list_tasks(user_id=user_id)
                        if tasks_result:
                            # Ищем задачу по названию
                            task_lines = tasks_result.split('\n')
                            for line in task_lines:
                                if old_part.lower() in line.lower():
                                    # Извлекаем ID задачи
                                    id_match = re.search(r'(\d+)\.\s+', line)
                                    if id_match:
                                        task_id = int(id_match.group(1))
                                        # Парсим новое время
                                        reminder_time = parse_time_from_text(new_part, user_id)
                                        if reminder_time:
                                            logger.info(f"[FORCE] Triggering edit_task() - id={task_id}, reminder_time={reminder_time}")
                                            result = edit_task(task_id=task_id, reminder_time=reminder_time, user_id=user_id)
                                            forced_calls.append({"function": "edit_task", "result": result})
                                        break
                    else:
                        # Это изменение названия
                        logger.info(f"[FORCE] Detected title change: '{old_part}' → '{new_part}'")
                        # Ищем задачу по старому названию
                        tasks_result = list_tasks(user_id=user_id)
                        if tasks_result:
                            task_lines = tasks_result.split('\n')
                            for line in task_lines:
                                if old_part.lower() in line.lower():
                                    id_match = re.search(r'(\d+)\.\s+', line)
                                    if id_match:
                                        task_id = int(id_match.group(1))
                                        logger.info(f"[FORCE] Triggering edit_task() - id={task_id}, title='{new_part}'")
                                        result = edit_task(task_id=task_id, title=new_part, user_id=user_id)
                                        forced_calls.append({"function": "edit_task", "result": result})
                                        break
                    break
    
    # 8. Проверка на find_partners (поиск партнеров)
    partners_triggers = [
        "найди партнеров", "ищи партнеров", "покажи партнеров", "нужны партнеры",
        "хочу найти", "ищу людей", "нужны единомышленники", "поиск коллег",
        "кто может помочь", "кто занимается", "кто знает", "рекомендуй контакты"
    ]
    
    if any(trigger in message_lower for trigger in partners_triggers):
        logger.info(f"[FORCE] Find partners trigger detected in message")
        if "find_partners" not in content.lower():
            logger.info(f"[FORCE] Triggering find_partners()")
            result = find_partners(user_id=user_id)
            forced_calls.append({"function": "find_partners", "result": result})
    
    # 9. Проверка на set_priority (установка приоритета)
    priority_triggers = [
        "приоритет", "важно", "срочно", "критично", "высокий приоритет",
        "средний приоритет", "низкий приоритет", "пометить как"
    ]
    
    if any(trigger in message_lower for trigger in priority_triggers):
        logger.info(f"[FORCE] Set priority trigger detected in message")
        if "set_priority" not in content.lower():
            # Пытаемся извлечь задачу и приоритет
            priority_match = re.search(r'(высокий|средний|низкий|high|medium|low)', message_lower)
            task_match = re.search(r'задач[ау]\s+(.+?)(?:\s+(?:приоритет|важно|срочно))', message, re.IGNORECASE)
            
            if priority_match and task_match:
                priority_map = {
                    'высокий': 'high', 'high': 'high',
                    'средний': 'medium', 'medium': 'medium',
                    'низкий': 'low', 'low': 'low'
                }
                priority = priority_map.get(priority_match.group(1), 'medium')
                task_title = task_match.group(1).strip()
                
                # Находим задачу по названию
                tasks_result = list_tasks(user_id=user_id)
                if tasks_result:
                    task_lines = tasks_result.split('\n')
                    for line in task_lines:
                        if task_title.lower() in line.lower():
                            id_match = re.search(r'(\d+)\.\s+', line)
                            if id_match:
                                task_id = int(id_match.group(1))
                                logger.info(f"[FORCE] Triggering set_priority() - id={task_id}, priority={priority}")
                                result = set_priority(task_id=task_id, priority=priority, user_id=user_id)
                                forced_calls.append({"function": "set_priority", "result": result})
                                break
    
    return forced_calls if forced_calls else None

async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    import re
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
    
    # Препроцессинг: форсим list_tasks() для расширенных триггерных фраз
    list_triggers = [
        "покажи", "список", "какие задач", "что у меня", "что там", "мои дела", "все задачи",
        "задачи", "список дел", "что делать", "что запланировано", "мои планы",
        "напоминания", "что напомнить", "активные задачи", "текущие дела"
    ]
    should_force_list = any(trigger in message.lower() for trigger in list_triggers)
    
    # Препроцессинг: форсим add_task() для расширенных триггерных фраз создания задач
    add_triggers = [
        "создай задачу", "добавь задачу", "напомни", "поставь задачу", "создать задачу", "добавить задачу",
        "запланируй", "запомни", "не забудь", "нужно сделать", "надо сделать", "хочу сделать",
        "купить", "почистить", "приготовить", "позвонить", "написать", "встретиться",
        "заказать", "забронировать", "записаться", "сходить", "съездить"
    ]
    should_force_add = any(trigger in message.lower() for trigger in add_triggers)
    
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
            
            # Get user current time FIRST before using it
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
                user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"
                # Проактивное заполнение при первом сообщении
                if len(context) if context else 0 < 2:
                    user_memory += "\n🎯 КРИТИЧНО ВАЖНО: Профиль ПУСТ! В первом ответе дружелюбно спроси о городе, компании или интересах для лучшей помощи!"
        else:
            user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"
        
        # НЕ загружаем задачи в user_memory! Агент должен сам вызвать list_tasks()
            # Это критично для предотвращения выдумывания задач
            
            # НО добавляем КРАТКУЮ сводку для контекста
            tasks_summary = session.query(Task).filter_by(user_id=user.id, status='pending').count()
            overdue_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.reminder_time < user_now,
                Task.status == 'pending'
            ).limit(5).all()
            
            if tasks_summary > 0:
                user_memory += f"\nСводка: всего активных задач {tasks_summary}"
            
            if overdue_tasks:
                overdue_titles = [f"{t.title}" for t in overdue_tasks]
                user_memory += f"\n⚠️ ПРОСРОЧЕННЫЕ ЗАДАЧИ: {', '.join(overdue_titles)} - предложи помощь!"
            
            # Add delegated tasks info
            delegated_tasks = session.query(Task).filter(
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == 'pending'
            ).all()
            if delegated_tasks:
                delegated_info = [f"Задача '{t.title}' (ID: {t.id}) от @{creator.username if (creator := session.query(User).filter_by(id=t.user_id).first()) else 'unknown'}" for t in delegated_tasks[:3]]
                user_memory += f"\nДелегированные задачи для принятия: {', '.join(delegated_info)}"
            
            # Add info about tasks delegated BY user
            my_delegated_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.delegated_to_username.isnot(None),
                Task.delegation_status.in_(['pending', 'accepted'])
            ).all()
            if my_delegated_tasks:
                my_delegated_info = [f"Задача '{t.title}' поручена @{t.delegated_to_username} (статус: {t.delegation_status})" for t in my_delegated_tasks[:3]]
                user_memory += f"\nЗадачи поручённые другим: {', '.join(my_delegated_info)}"
            
            # Add partners/contacts info
            try:
                partners = get_partners_list(user_id=user_id, session=session)
                if partners:
                    # partners - это список объектов UserProfile
                    partners_usernames = []
                    for p in partners[:5]:
                        partner_user = session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            partners_usernames.append(f"@{partner_user.username}")
                    if partners_usernames:
                        user_memory += f"\nДоступные контакты: {', '.join(partners_usernames)}"
            except Exception as e:
                logger.error(f"Error getting partners: {e}")
            
            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars
            
            session.close()
        
        # Construct system prompt with replaced placeholders
        # Расширяем system prompt для работы с относительным временем
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        system_prompt = get_system_prompt().replace("{{current_date}}", user_now.strftime("%Y-%m-%d")).replace("{{current_time}}", current_time_str).replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d")).replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d")).replace("{{current_username}}", user_username)
        
        # 🎯 КОМПЛЕКСНЫЙ ПОДХОД: задачи, контакты, напоминания, связи
        system_prompt += "\n\n🎯 ТВОИ ОСНОВНЫЕ ФУНКЦИИ (все важны равно):\n1. Управление задачами и напоминаниями\n2. Помощь в поиске контактов и партнёров для совместной работы\n3. Отслеживание связей между людьми и интересами\n4. Когда видишь возможность - ПРЕДЛАГАЙ КОНКРЕТНЫХ людей из контактов\n\n🤝 РАБОТА С КОНТАКТАМИ:\n- Если задача требует навыков/помощи - используй search_contacts() для поиска подходящих людей\n- Предлагай КОНКРЕТНЫЕ имена: 'Кстати, @ivan работает с дизайном, может помочь?'\n- Упоминай делегирование только когда это действительно уместно\n- Следи за общими интересами между контактами\n\nПРИМЕР ХОРОШЕГО ОТВЕТА:\n'Создал задачу по дизайну. Вижу, что @maria увлекается графикой - может быть полезна?'\n\nПРИМЕР ПЛОХОГО ОТВЕТА:\n'Задача создана. Хочешь делегировать?' ← Слишком навязчиво!"
        
        system_prompt += f"\n\nВАЖНО ПРИ РАБОТЕ С ВРЕМЕНЕМ:\n- Текущее время: {current_time_str}\n- Если пользователь говорит 'через X минут', добавь X минут к текущему времени {current_time_str}\n- Если пользователь говорит 'через X часов', добавь X часов к текущему времени\n- Всегда используй формат времени reminder_time в виде 'YYYY-MM-DD HH:MM' в параметрах tool call\n- Например: 'через 5 минут' от {current_time_str} = {(user_now + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M')}"
        
        system_prompt += f"\n\n@MENTIONS В СООБЩЕНИИ: {mentions_str}\n� ЕСЛИ ЕСТЬ @MENTIONS → можешь использовать delegate_task() если пользователь хочет поручить задачу\n💡 Если задача сложная и есть подходящие контакты → ПРЕДЛОЖИ КОНКРЕТНОГО человека по имени"
        
        # Если обнаружены триггеры для list_tasks, добавляем в промпт принудительное требование
        if should_force_list:
            system_prompt += "\n\n🚨 КРИТИЧЕСКИ ВАЖНО: ПОЛЬЗОВАТЕЛЬ ПРОСИТ ПОКАЗАТЬ ЗАДАЧИ - ОБЯЗАТЕЛЬНО ВЫЗОВИ list_tasks() ПЕРВЫМ ДЕЛОМ, ДАЖЕ ЕСЛИ В КОНТЕКСТЕ УЖЕ ЕСТЬ ИНФОРМАЦИЯ О ЗАДАЧАХ!"
        
        # Усиленный триггер для создания задач
        if should_force_add:
            system_prompt += "\n\n🚨 ПОЛЬЗОВАТЕЛЬ ПРОСИТ СОЗДАТЬ ЗАДАЧУ - ОБЯЗАТЕЛЬНО ВЫЗОВИ add_task() С ПАРАМЕТРАМИ! НЕ ПРОСТО ГОВОРИ ОБ ЭТОМ - ВЫПОЛНИ!"
        
        system_prompt += user_memory
        
        # 🎯 Проверяем контекст последней созданной задачи для edit_task
        last_task_context = ""
        if redis_client and user_id:
            try:
                last_task_data = await redis_client.get(f"last_task_id:{user_id}")
                if last_task_data:
                    task_info = json.loads(last_task_data.decode('utf-8'))
                    last_task_context = f"\n\n🎯 КОНТЕКСТ ПОСЛЕДНЕЙ ЗАДАЧИ: ID={task_info['id']}, название='{task_info['title']}', время='{task_info.get('reminder_time', '')}'. ЕСЛИ пользователь даёт уточнения (я ошибся, не завтра а сегодня, изменить время и т.д.), ОБЯЗАТЕЛЬНО используй edit_task(task_id={task_info['id']}, ...)!"
                    logger.info(f"[LAST_TASK_CONTEXT] Loaded for user {user_id}: {task_info}")
            except Exception as e:
                logger.error(f"Error loading last_task_id from Redis: {e}")
        
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        # Добавляем текущее сообщение с контекстом последней задачи
        user_message_with_context = message + last_task_context
        messages.append({"role": "user", "content": user_message_with_context})
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "temperature": 0.1
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        # Retry loop for API call
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                        logger.info(f"DeepSeek API response status: {response.status} (attempt {attempt + 1})")
                        if response.status == 200:
                            # Успешный ответ - обрабатываем
                            tool_calls = []
                            try:
                                result = await response.json()
                                message_response = result["choices"][0]["message"]
                                content = message_response.get("content", "")
                                # Фильтровать сырые tool calls
                                content = re.sub(r'<\|.*?\|>', '', content).strip()
                                content = re.sub(r'<｜DSML｜function_calls>.*?</｜DSML｜function_calls>', '', content, flags=re.DOTALL).strip()
                                
                                # Проверяем tool_calls в API response
                                tool_calls = message_response.get("tool_calls")
                            except Exception as e:
                                logger.error(f"Error parsing API response: {e}")
                                if attempt < max_retries:
                                    logger.info(f"Retrying API call due to parse error (attempt {attempt + 1})")
                                    await asyncio.sleep(1)
                                    continue
                                content = "Извините, произошла ошибка при обработке ответа от ИИ. Попробуйте еще раз."
                            
                            # Обработка tool calls и т.д.
                            tool_results = []  # Инициализируем заранее
                            
                            # 🚨 КРИТИЧЕСКАЯ ПРОВЕРКА: если AI не вызвал tool calls, но должен был
                            if not tool_calls and (should_force_add or should_force_list):
                                logger.warning(f"[FORCE REQUIRED] AI didn't call tools, but should_force_add={should_force_add}, should_force_list={should_force_list}")
                                logger.info(f"[FORCE REQUIRED] Will process in forced calls section below")
                            
                            if tool_calls:
                                logger.info(f"[TOOL CALLS] AI returned {len(tool_calls)} tool calls - starting execution")
                                # Выполняем tool calls
                                tool_results = []
                                for tool_call in tool_calls:
                                    try:
                                        func_name = tool_call['function']['name']
                                        args = json.loads(tool_call['function']['arguments'])
                                        logger.info(f"[TOOL CALLS] Executing {func_name} with args: {args}")
                                        
                                        # Вызываем соответствующую функцию
                                        if func_name == 'add_task':
                                            result = add_task(user_id=user_id, **args)
                                        elif func_name == 'list_tasks':
                                            result = list_tasks(user_id=user_id)
                                        elif func_name == 'complete_task':
                                            result = complete_task(user_id=user_id, **args)
                                            # Добавляем флаг для вопроса о результате
                                            tool_results.append("ВАЖНО: ОБЯЗАТЕЛЬНО спроси пользователя о результатах выполнения: 'Расскажите, как прошло выполнение? Какие были результаты?'")
                                        elif func_name == 'delete_task':
                                            result = delete_task(user_id=user_id, **args)
                                        elif func_name == 'delegate_task':
                                            result = delegate_task(user_id=user_id, **args)
                                        elif func_name == 'find_partners':
                                            result = find_partners(user_id=user_id, **args)
                                        elif func_name == 'update_profile':
                                            result = update_profile(user_id=user_id, **args)
                                        elif func_name == 'update_user_memory':
                                            result = update_user_memory(user_id=user_id, **args)
                                        elif func_name == 'edit_task':
                                            result = edit_task(user_id=user_id, **args)
                                        elif func_name == 'set_priority':
                                            result = set_priority(user_id=user_id, **args)
                                        elif func_name == 'get_task_details':
                                            result = get_task_details(user_id=user_id, **args)
                                        else:
                                            result = f"Неизвестная функция: {func_name}"
                                
                                        # Сохраняем ID последней созданной задачи для возможного edit_task
                                        if func_name == 'add_task' and 'ID:' in result:
                                            # Извлекаем ID из результата
                                            import re
                                            id_match = re.search(r'ID:\s*(\d+)', result)
                                            if id_match:
                                                last_task_id = id_match.group(1)
                                                # Сохраняем в Redis для использования в следующих сообщениях
                                                if redis_client:
                                                    try:
                                                        await redis_client.setex(
                                                            f"last_task_id:{user_id}", 
                                                            300,  # 5 минут TTL
                                                            json.dumps({
                                                                'id': last_task_id,
                                                                'title': args.get('title', ''),
                                                                'reminder_time': args.get('reminder_time', '')
                                                            }).encode('utf-8')
                                                        )
                                                    except Exception as e:
                                                        logger.error(f"Error saving last_task_id to Redis: {e}")
                                                # Добавляем в результаты для текущего контекста
                                                tool_results.append(f"✅ ПОСЛЕДНЯЯ СОЗДАННАЯ ЗАДАЧА: ID={last_task_id}, title='{args.get('title', '')}'. Если пользователь даёт уточнения — ОБЯЗАТЕЛЬНО используй edit_task({last_task_id})!")
                                        
                                        tool_results.append(f"{func_name}() вернул: {result[:200]}")
                                        logger.info(f"[TOOL CALLS] {func_name} result: {result[:100]}...")
                                        
                                    except Exception as e:
                                        logger.error(f"[TOOL CALLS] Error executing {func_name}: {e}")
                                        tool_results.append(f"{func_name}() ошибка: {str(e)}")
                        
                        # Генерируем ответ на основе результатов
                        logger.info(f"[TOOL CALLS] Tool calls completed, {len(tool_results)} results. Generating natural response...")
                        system_prompt_with_results = system_prompt + f"\n\n=== РЕЗУЛЬТАТЫ ВЫПОЛНЕНИЯ ФУНКЦИЙ ===\n" + "\n".join(tool_results) + "\n\n=== ИНСТРУКЦИИ ===\nТы только что выполнил функции. Теперь СФОРМУЛИРУЙ ЕСТЕСТВЕННЫЙ ОТВЕТ ПОЛЬЗОВАТЕЛЮ на основе ПОЛУЧЕННЫХ ДАННЫХ.\n\nПРАВИЛА:\n1. НИКОГДА не показывай названия функций (list_tasks, add_task и т.д.)\n2. НИКОГДА не показывай JSON или технические данные\n3. НИКОГДА не говори 'выполнил функцию' или подобное\n4. Используй ТОЛЬКО информацию из результатов выполнения\n5. Отвечай естественно, как дружелюбный помощник\n6. Если результат пустой - скажи об этом дружелюбно\n7. 🤝 ОБЯЗАТЕЛЬНО упомяни возможность СОВМЕСТНОЙ РАБОТЫ или поиска ПАРТНЁРОВ если задача создана\n8. НЕ ВОЗВРАЩАЙ пустой ответ, ```json``` или технические теги - ТОЛЬКО нормальный текст!"
                        
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
                        
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {
                            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                            "Content-Type": "application/json"
                        }
                        
                        async with session.post(url, headers=headers, json=data_retry, timeout=aiohttp.ClientTimeout(total=60)) as retry_response:
                            if retry_response.status == 200:
                                retry_result = await retry_response.json()
                                content = retry_result["choices"][0]["message"].get("content", "")
                                content = replace_placeholders(content, user_now, current_time_str)
                                content = clean_technical_details(content)
                                
                                # Сохраняем взаимодействие
                                if user_id and content:
                                    try:
                                        from models import Session, User, Interaction
                                        save_session = Session()
                                        user_obj = save_session.query(User).filter_by(telegram_id=user_id).first()
                                        if user_obj:
                                            interaction = Interaction(user_id=user_obj.id, message_type='agent', content=content)
                                            save_session.add(interaction)
                                            save_session.commit()
                                    except Exception as e:
                                        logger.error(f"Failed to save interaction: {e}")
                                
                                return content
                            else:
                                return f"Ошибка при генерации ответа: {retry_response.status}"
                    
                    # Проверяем триггеры принудительного вызова ТОЛЬКО если AI НЕ вызвал tool_calls
                    logger.info("[FORCE CHECK] Checking for forced tool call triggers...")
                    logger.info(f"[FORCE CHECK] AI tool_calls present: {tool_calls is not None}")
                    
                    forced = None
                    if not tool_calls:  # ТОЛЬКО если AI не вызвал функции сам
                        logger.warning(f"[FORCE REQUIRED] AI didn't call tools, but should_force_add={should_force_add}, should_force_list={should_force_list}")
                        forced = force_tool_calls(original_message, content, mentions_str, user_id)
                        if forced:
                            logger.info("[FORCE REQUIRED] Will process in forced calls section below")
                    else:
                        logger.info("[FORCE CHECK] Skipping forced calls - AI already called tools")
                    
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
                        
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {
                            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                            "Content-Type": "application/json"
                        }
                        
                        async with session.post(url, headers=headers, json=data_retry, timeout=aiohttp.ClientTimeout(total=60)) as retry_response:
                            if retry_response.status == 200:
                                retry_result = await retry_response.json()
                                content = retry_result["choices"][0]["message"].get("content", "")
                                logger.info(f"[TOOL CALLS] AI retry response: '{content[:300]}...'")
                                content = replace_placeholders(content, user_now, current_time_str)
                                content = clean_technical_details(content)  # Очистка от технических деталей
                                logger.info(f"[TOOL CALLS] Final content: '{content[:300]}...'")
                                logger.info(f"[TOOL CALLS] Content length: {len(content)}, is empty: {len(content.strip()) == 0}")
                                
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
                    
                    # Если forced calls не сработали, обрабатываем обычный ответ AI
                    # Обрабатываем обычный ответ AI без tool calls
                    logger.info("[TOOL CALLS] Tool calls completed, 0 results. Generating natural response...")
                    content = message_response.get("content", "")
                    # Для обычных ответов используем только базовую очистку
                    content = re.sub(r'<\|.*?\|>', '', content).strip()  # Только DSML теги
                    content = replace_placeholders(content, user_now, current_time_str)
                    
                    # 🚨 КРИТИЧЕСКАЯ ПРОВЕРКА: если content содержит только JSON/теги - это ошибка
                    if content and (content.startswith('```') or content.startswith('{') or content == '{}' or len(content.strip()) < 5):
                        logger.warning(f"[BAD CONTENT] AI returned technical output: {content[:100]}")
                        content = ""  # Сбрасываем, чтобы сработал retry
                    # НЕ применяем clean_technical_details для обычных ответов!
                    
                    # Если после очистки ответ пустой - повторный запрос
                    if not content or len(content.strip()) < 3:
                        logger.warning("[RETRY] Response empty after cleaning, retrying with explicit instruction")
                        retry_system = system_prompt + "\n\n🚨 КРИТИЧЕСКИ ВАЖНО:\n1. НЕ возвращай JSON, code blocks или технические теги\n2. Отвечай ТОЛЬКО обычным текстом\n3. Если создал задачу - скажи об этом и предложи найти партнёра\n4. Минимум 20 слов в ответе\n5. Будь дружелюбным и конкретным!"
                        
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
                                content = re.sub(r'<\|.*?\|>', '', content).strip()  # Только базовая очистка
                                content = replace_placeholders(content, user_now, current_time_str)
                                # НЕ применяем clean_technical_details для повторных запросов
                        
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
                    content = clean_technical_details(content)
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
                    content = clean_technical_details(content)
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
                    content = clean_technical_details(content)
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
                except (Exception,):
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
                    content = clean_technical_details(content)
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
                except (Exception,):
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
                    content = clean_technical_details(content)
                    return content
                else:
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."
