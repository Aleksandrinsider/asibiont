"""
Интернационализация (i18n) — централизованный модуль для двуязычной поддержки.

Использование:
    from i18n import t, get_user_lang
    
    # В хэндлерах:
    lang = get_user_lang(user_id)
    msg = t(lang, 'task_created', title='Купить кефир', time='14:00')
    
    # Или короче через user_id:
    msg = tu(user_id, 'task_created', title='...', time='...')
"""

import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# ЯЗЫК ПОЛЬЗОВАТЕЛЯ
# ═══════════════════════════════════════════════════════

_user_lang_cache: dict[int, str] = {}  # telegram_id → 'ru'|'en'


def get_user_lang(user_id: int) -> str:
    """Получить язык пользователя (из кэша или БД)."""
    if user_id in _user_lang_cache:
        return _user_lang_cache[user_id]
    
    try:
        from models import Session, User
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            lang = getattr(user, 'language', 'ru') or 'ru'
            _user_lang_cache[user_id] = lang
            return lang
        finally:
            session.close()
    except Exception:
        return 'ru'


def set_user_lang(user_id: int, lang: str):
    """Установить язык пользователя."""
    lang = lang.lower().strip()
    if lang not in ('ru', 'en'):
        lang = 'ru'
    
    _user_lang_cache[user_id] = lang
    
    try:
        from models import Session, User
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                user.language = lang
                session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"[I18N] Failed to save lang for {user_id}: {e}")


def detect_lang_from_telegram(language_code: str) -> str:
    """Определить язык из Telegram language_code."""
    if not language_code:
        return 'ru'
    code = language_code.lower()
    if code.startswith('ru') or code.startswith('uk') or code.startswith('be'):
        return 'ru'
    return 'en'


def get_user_lang_by_db_id(db_user_id: int, session=None) -> str:
    """Get language of a user by their database ID (not telegram_id)."""
    try:
        from models import Session, User
        _close = False
        if session is None:
            session = Session()
            _close = True
        try:
            user = session.query(User).filter_by(id=db_user_id).first()
            if user:
                lang = getattr(user, 'language', 'ru') or 'ru'
                if user.telegram_id:
                    _user_lang_cache[user.telegram_id] = lang
                return lang
            return 'ru'
        finally:
            if _close:
                session.close()
    except Exception:
        return 'ru'


def get_lang_badge(lang: str) -> str:
    """Return a language badge for display in contacts."""
    if lang == 'en':
        return 'EN'
    return 'RU'


STRINGS: dict = {'ru': {}, 'en': {}}


def _register(key: str, ru: str, en: str):
    """Регистрирует строку перевода."""
    STRINGS['ru'][key] = ru
    STRINGS['en'][key] = en


def t(lang: str, key: str, **kwargs) -> str:
    """Получить перевод строки.
    
    Args:
        lang: 'ru' или 'en'
        key: ключ строки
        **kwargs: переменные для подстановки
    
    Returns:
        Переведённая строка с подставленными переменными
    """
    strings = STRINGS.get(lang, STRINGS['ru'])
    template = strings.get(key)
    if template is None:
        # Fallback на русский
        template = STRINGS['ru'].get(key)
    if template is None:
        logger.warning(f"[I18N] Missing key: {key}")
        return key
    
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError) as e:
        logger.warning(f"[I18N] Format error for {key}: {e}")
        return template


def tu(user_id: int, key: str, **kwargs) -> str:
    """Shortcut: перевод по user_id."""
    return t(get_user_lang(user_id), key, **kwargs)


# ═══════════════════════════════════════════════════════
# СТРОКИ: ОБЩИЕ
# ═══════════════════════════════════════════════════════

_register('user_not_found', 'Хм, не нахожу твой профиль — отправь /start', 'User not found.')
_register('task_not_found', "Хм, не нахожу задачу '{title}'", "Task '{title}' not found.")
_register('task_not_found_generic', 'Хм, не нахожу такую задачу', 'Task not found.')
_register('error_generic', 'Произошла ошибка. Попробуй ещё раз.', 'An error occurred. Please try again.')
_register('error_no_user_id', 'Ошибка: user_id не указан.', 'Error: user_id not specified.')

# ═══════════════════════════════════════════════════════
# СТРОКИ: ЗАДАЧИ
# ═══════════════════════════════════════════════════════

_register('task_created',
    "Задача '{title}' создана на {time}.",
    "Task '{title}' scheduled for {time}.")
_register('task_created_no_time',
    "Задача '{title}' создана.",
    "Task '{title}' created.")
_register('task_completed',
    "Задача '{title}' завершена!",
    "Task '{title}' completed!")
_register('task_deleted',
    "Задача '{title}' удалена.",
    "Task '{title}' deleted.")
_register('task_skipped',
    "Ладно, '{title}' пропускаем",
    "Task '{title}' skipped.")
_register('task_restored',
    "'{title}' вернул в работу!",
    "Task '{title}' restored.")
_register('task_updated',
    "TASK_UPDATED: Задача '{title}' обновлена.",
    "TASK_UPDATED: Task '{title}' updated.")
_register('task_updated_with_time',
    "TASK_UPDATED: Задача '{title}' обновлена. Новое время напоминания: {time}.",
    "TASK_UPDATED: Task '{title}' updated. New reminder time: {time}.")
_register('task_duplicate',
    "Задача '{title}' уже есть в списке.",
    "Task '{title}' already exists.")
_register('task_no_time',
    "ВРЕМЯ НЕ УКАЗАНО. Спроси у пользователя: «На какое время поставить напоминание?»",
    "NO TIME SPECIFIED. Ask the user: 'What time should I set the reminder for?'")
_register('task_title_empty',
    "ERROR: Название задачи не может быть пустым — укажи название.",
    "ERROR: Task title cannot be empty.")
_register('task_linked_to_goal',
    "Привязана к цели: {goal}",
    "Linked to goal: {goal}")
_register('no_active_tasks',
    "Пока нет активных задач",
    "No active tasks.")
_register('time_conflict',
    "Конфликт: в {time} уже запланирована задача '{title}'.",
    "Conflict: task '{title}' already scheduled at {time}.")
_register('time_free',
    "Время свободно, можно создавать задачу.",
    "Time slot is free, you can create a task.")
_register('self_delegation_error',
    "SELF_DELEGATION_ERROR: Нельзя поручить задачу самому себе.",
    "SELF_DELEGATION_ERROR: Cannot delegate a task to yourself.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ЦЕЛИ
# ═══════════════════════════════════════════════════════

_register('goal_created',
    "Цель '{title}' создана!",
    "Goal '{title}' created!")
_register('goal_deleted',
    "Цель '{title}' удалена.",
    "Goal '{title}' deleted.")
_register('goal_updated',
    "Цель '{title}' обновлена: {progress}%",
    "Goal '{title}' updated: {progress}%")
_register('goal_title_required',
    "Укажи название цели.",
    "Please specify a goal title.")
_register('no_active_goals',
    "Пока нет активных целей",
    "No active goals.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ПРОФИЛЬ
# ═══════════════════════════════════════════════════════

_register('profile_updated',
    "Профиль обновлён.",
    "Profile updated.")
_register('profile_no_data',
    "Не передано данных для обновления профиля.",
    "No data provided for profile update.")
_register('profile_skills_added',
    "Добавлен навык: {skill}",
    "Skill added: {skill}")
_register('profile_garbage_rejected',
    "Хм, '{text}' не похоже на навык",
    "Rejected: '{text}' doesn't look like a skill.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ПОСТЫ
# ═══════════════════════════════════════════════════════

_register('post_created',
    "Пост создан!",
    "Post created!")
_register('post_updated',
    "Пост #{id} обновлён!",
    "Post #{id} updated!")
_register('post_deleted',
    "Пост удалён.",
    "Post deleted.")
_register('post_empty',
    "Текст поста не может быть пустым.",
    "Post text cannot be empty.")
_register('no_posts',
    "У тебя нет постов.",
    "You have no posts.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ДЕЛЕГИРОВАНИЕ
# ═══════════════════════════════════════════════════════

_register('delegation_sent',
    "Задача '{title}' поручена @{user}",
    "Task '{title}' delegated to @{user}.")
_register('delegation_accepted',
    "Задача '{title}' принята.",
    "Task '{title}' accepted.")
_register('delegation_rejected',
    "Задача '{title}' отклонена.",
    "Task '{title}' rejected.")
_register('delegation_no_progress',
    "Нет активных поручений",
    "No active delegated tasks.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ОПЛАТА И ТОКЕНЫ
# ═══════════════════════════════════════════════════════

_register('tokens_insufficient',
    "Токены закончились. Нужно: {cost}, баланс: {balance}. Пополни: /buy",
    "Insufficient tokens. Need: {cost}, balance: {balance}. Top up: /buy")
_register('tokens_balance',
    "Баланс: {balance} токенов",
    "Balance: {balance} tokens")
_register('subscription_cancelled',
    "Подписка отменена.",
    "Subscription cancelled.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: КОНТАКТЫ / АЛЕРТЫ
# ═══════════════════════════════════════════════════════

_register('contact_alert_set',
    "Настроил уведомление: буду следить за {description}.",
    "Alert set: watching for {description}.")
_register('no_contacts_found',
    "Подходящих контактов не нашлось",
    "No matching contacts found.")
_register('contacts_found',
    "Найдено {count} подходящих контактов.",
    "Found {count} matching contacts.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ИССЛЕДОВАНИЯ
# ═══════════════════════════════════════════════════════

_register('search_no_results',
    "По запросу ничего не найдено.",
    "No results found for your query.")
_register('search_error',
    "Ошибка при поиске. Попробуй другой запрос.",
    "Search error. Try a different query.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ПОГОДА
# ═══════════════════════════════════════════════════════

_register('weather_error',
    "Не удалось получить данные о погоде.",
    "Failed to get weather data.")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ПРОГРЕСС АГЕНТА
# ═══════════════════════════════════════════════════════

PROGRESS_PHRASES = {
    'ru': {
        'list_tasks': ['Открываю твой день...', 'Так, что у нас по плану...', 'Раскладываю дела по полочкам...'],
        'add_task': ['Вписываю в расписание...', 'Бронирую время для дела...', 'Ставлю в очередь...'],
        'complete_task': ['Финиш! Вычёркиваю...', 'Одним делом меньше!', 'Закрываю гештальт...'],
        'edit_task': ['Перекраиваю план...', 'Двигаю шестерёнки...', 'Тюнингую задачу...'],
        'delete_task': ['Безжалостно вычёркиваю...', 'Прощай, задача...'],
        'skip_task': ['Перебрасываю на потом...', 'Не сегодня — переношу...'],
        'restore_task': ['Воскрешаю из архива...', 'Камбэк задачи!'],
        'quick_topic_search': ['Быстрый сёрч...', 'Секунду, пробиваю...'],
        'research_topic': ['Ныряю в тему...', 'Провожу разведку...', 'Штудирую источники...', 'Прочёсываю интернет...'],
        'get_news_trends': ['Ловлю сигналы из инфополя...', 'Чекаю новости...', 'Мониторю тренды...'],
        'find_relevant_contacts_for_task': ['Ищу кто может помочь...', 'Прочёсываю базу контактов...', 'Подбираю нужных людей...'],
        'update_profile': ['Запоминаю, кто ты...', 'Обновляю досье...'],
        'get_user_goals': ['Открываю карту целей...', 'Сверяю курс...'],
        'create_goal': ['Задаю новый вектор...', 'Ставлю маяк на горизонте...', 'Фиксирую вершину...'],
        'delete_goal': ['Снимаю с повестки...', 'Расчищаю горизонт...'],
        'generate_post': ['Ловлю вдохновение...', 'Формулирую мысль...', 'Колдую над текстом...'],
        'create_post': ['Запускаю в эфир...', 'Публикую!'],
        'edit_post': ['Полирую текст...', 'Докручиваю...'],
        'get_posts': ['Листаю ленту...', 'Смотрю что публиковал...'],
        'delete_post': ['Убираю из ленты...'],
        'delegate_task': ['Передаю на исполнение...', 'Делегирую!'],
        'get_delegation_progress': ['Чекаю статус у исполнителя...', 'Как там дела...'],
        'accept_delegated_task': ['Принимаю вызов!', 'Беру на себя!'],
        'reject_delegated_task': ['Отклоняю запрос...'],
        'check_time_conflicts': ['Проверяю, не пересечётся ли...', 'Сканирую расписание...'],
        'update_goal_progress': ['Двигаю прогресс-бар...', 'Фиксирую продвижение...'],
        'list_goals': ['Разворачиваю карту целей...', 'Сверяю маршрут...'],
        'set_contact_alert': ['Ставлю на радар...', 'Настраиваю маячок...'],
        'set_content_strategy': ['Фиксирую стратегию...', 'Записываю контент-план...'],
        'toggle_autonomous_feature': ['Переключаю режим...'],
        'send_message_to_user': ['Пишу от твоего имени...', 'Отправляю послание...'],
        'find_and_message_relevant_users': ['Ищу и связываюсь...', 'Обхожу платформу...'],
        'reply_to_user_message': ['Формулирую ответ...', 'Готовлю реплику...'],
        'get_incoming_messages': ['Проверяю входящие...', 'Что в почте...'],
        'get_message_status': ['Проверяю, доставлено ли...'],
        'web_search': ['Ищу в интернете...', 'Гуглю...', 'Шерстю сеть...'],
        'send_email': ['Пишу письмо...', 'Отправляю email...'],
        'send_outreach_email': ['Пишу письмо...', 'Готовлю аутрич...'],
        'send_follow_up_email': ['Пишу follow-up...', 'Напоминаю о себе...'],
        'negotiate_by_email': ['Веду переговоры...', 'Пишу предложение...'],
        'reply_to_outreach_email': ['Отвечаю на письмо...', 'Готовлю ответ...'],
        'save_email_contact': ['Сохраняю контакт...'],
        'list_email_contacts': ['Открываю контакты...', 'Смотрю базу...'],
        'publish_to_telegram': ['Публикую в Telegram...', 'Запускаю пост в канал...'],
        'publish_to_discord': ['Публикую в Discord...', 'Отправляю в сервер...'],
        'start_content_campaign': ['Запускаю кампанию...', 'Разгоняю автопостинг...', 'Ставлю на автопилот...'],
        'manage_content_campaign': ['Управляю кампанией...', 'Настраиваю...'],
        'start_delegation_campaign': ['Ищу исполнителей...', 'Запускаю поиск...', 'Сканирую кандидатов...'],
        'manage_delegation_campaign': ['Управляю делегированием...'],
        'generate_image': ['Рисую...', 'Генерирую изображение...', 'Создаю визуал...'],
        'get_system_status': ['Проверяю статус системы...', 'Смотрю как дела...'],
        'reschedule_task': ['Переношу...', 'Двигаю дату...'],
        'get_task_details': ['Открываю задачу...', 'Смотрю детали...'],
        'cancel_delegation': ['Отзываю задачу...', 'Отменяю делегирование...'],
        'get_weather_info': ['Проверяю погоду...', 'Смотрю прогноз...'],
        'research_and_plan': ['Исследую и планирую...', 'Делаю разбор ситуации...'],
        'analyze_situation_and_suggest_tasks': ['Анализирую ситуацию...', 'Думаю над планом...'],
        'analyze_group_opportunities': ['Ищу возможности...', 'Сканирую группу...'],
        'find_partners': ['Ищу партнёров...', 'Сканирую контакты...'],
        'generate_marketing_content': ['Создаю контент...', 'Формулирую идею...'],
        'set_reminder': ['Ставлю напоминалку...', 'Запомню!'],
        'update_goal': ['Обновляю цель...', 'Корректирую курс...'],
        'complete_goal': ['Закрываю цель!', 'Победа, цель достигнута!'],
        'list_marketplace': ['Открываю маркетплейс...', 'Ищу агентов...'],
        'schedule_background_task': ['Ставлю на автопилот...', 'Запускаю фоном...'],
        'run_agent_action': ['Агент приступает...', 'Запускаю агента...'],
        'generate_post': ['Придумываю пост...', 'Генерирую контент...'],
    },
    'en': {
        'list_tasks': ['Checking your schedule...', 'Let me see your tasks...', 'Opening your planner...'],
        'add_task': ['Adding to your schedule...', 'Booking time...', 'Queuing up...'],
        'complete_task': ['Done! Crossing it off...', 'One less thing!', 'Completing...'],
        'edit_task': ['Reshuffling the plan...', 'Adjusting...', 'Tuning the task...'],
        'delete_task': ['Removing task...', 'Goodbye, task...'],
        'skip_task': ['Postponing for later...', 'Not today — moving it...'],
        'restore_task': ['Bringing it back...', 'Task comeback!'],
        'quick_topic_search': ['Quick search...', 'One moment, checking...'],
        'research_topic': ['Diving into the topic...', 'Researching...', 'Scanning sources...', 'Browsing the web...'],
        'get_news_trends': ['Catching signals...', 'Checking the news...', 'Monitoring trends...'],
        'find_relevant_contacts_for_task': ['Looking for helpers...', 'Scanning contacts...', 'Finding the right people...'],
        'update_profile': ['Remembering who you are...',  'Updating your profile...'],
        'get_user_goals': ['Opening your goals map...', 'Checking course...'],
        'create_goal': ['Setting a new direction...', 'Placing a beacon...', 'Locking in the target...'],
        'delete_goal': ['Removing from agenda...', 'Clearing the horizon...'],
        'generate_post': ['Catching inspiration...', 'Crafting the message...', 'Working on the text...'],
        'create_post': ['Publishing...', 'Going live!'],
        'edit_post': ['Polishing the text...', 'Fine-tuning...'],
        'get_posts': ['Browsing your feed...', 'Checking your posts...'],
        'delete_post': ['Removing from feed...'],
        'delegate_task': ['Handing it off...', 'Delegating!'],
        'get_delegation_progress': ['Checking with the assignee...', 'How is it going...'],
        'accept_delegated_task': ['Challenge accepted!', 'Taking it on!'],
        'reject_delegated_task': ['Declining request...'],
        'check_time_conflicts': ['Checking for conflicts...', 'Scanning schedule...'],
        'update_goal_progress': ['Moving the progress bar...', 'Recording progress...'],
        'list_goals': ['Unfolding your goals map...', 'Checking the route...'],
        'set_contact_alert': ['Setting up radar...', 'Configuring alert...'],
        'set_content_strategy': ['Locking in strategy...', 'Recording content plan...'],
        'toggle_autonomous_feature': ['Switching mode...'],
        'send_message_to_user': ['Writing on your behalf...', 'Sending message...'],
        'find_and_message_relevant_users': ['Finding and reaching out...', 'Scanning platform...'],
        'reply_to_user_message': ['Crafting a reply...', 'Preparing response...'],
        'get_incoming_messages': ['Checking inbox...', 'What\'s in the mail...'],
        'get_message_status': ['Checking delivery status...'],
        'web_search': ['Searching the web...', 'Googling...', 'Scanning the net...'],
        'send_email': ['Writing an email...', 'Sending email...'],
        'send_outreach_email': ['Writing a letter...', 'Preparing outreach...'],
        'send_follow_up_email': ['Writing follow-up...', 'Following up...'],
        'negotiate_by_email': ['Negotiating...', 'Writing proposal...'],
        'reply_to_outreach_email': ['Replying to email...', 'Crafting reply...'],
        'save_email_contact': ['Saving contact...'],
        'list_email_contacts': ['Opening contacts...', 'Browsing database...'],
        'publish_to_telegram': ['Publishing to Telegram...', 'Launching post...'],
        'publish_to_discord': ['Publishing to Discord...', 'Sending to server...'],
        'start_content_campaign': ['Starting campaign...', 'Launching autopilot...', 'Spinning up...'],
        'manage_content_campaign': ['Managing campaign...', 'Configuring...'],
        'start_delegation_campaign': ['Looking for candidates...', 'Starting search...', 'Scanning prospects...'],
        'manage_delegation_campaign': ['Managing delegation...'],
        'generate_image': ['Drawing...', 'Generating image...', 'Creating visual...'],
        'get_system_status': ['Checking system status...', 'How are things...'],
        'reschedule_task': ['Rescheduling...', 'Moving the date...'],
        'get_task_details': ['Opening task...', 'Looking at details...'],
        'cancel_delegation': ['Revoking task...', 'Cancelling delegation...'],
        'get_weather_info': ['Checking weather...', 'Looking at forecast...'],
        'research_and_plan': ['Researching and planning...', 'Analyzing situation...'],
        'analyze_situation_and_suggest_tasks': ['Analyzing situation...', 'Thinking through a plan...'],
        'analyze_group_opportunities': ['Finding opportunities...', 'Scanning group...'],
        'find_partners': ['Looking for partners...', 'Scanning contacts...'],
        'generate_marketing_content': ['Creating content...', 'Drafting the idea...'],
        'set_reminder': ['Setting a reminder...', "I'll remember!"],
        'update_goal': ['Updating goal...', 'Adjusting course...'],
        'complete_goal': ['Closing goal!', 'Goal achieved!'],
        'list_marketplace': ['Opening marketplace...', 'Searching agents...'],
        'schedule_background_task': ['Setting on autopilot...', 'Running in background...'],
        'run_agent_action': ['Agent getting to work...', 'Launching agent...'],
        'generate_post': ['Crafting a post...', 'Generating content...'],
    },
}

THINKING_PHRASES = {
    'ru': [
        'Секунду, варю кашу в голове...',
        'Включаю нейросети...',
        'Хмм, дай подумать...',
        'Разгоняю процессор...',
        'Заглядываю в контекст...',
        'Фокусируюсь на задаче...',
        'Ищу лучший подход...',
        'Собираю картину...',
        'Момент, анализирую...',
        'Прокручиваю варианты...',
    ],
    'en': [
        'One moment, thinking...',
        'Firing up the neural nets...',
        'Hmm, let me think...',
        'Spinning up the processor...',
        'Checking the context...',
        'Focusing on the task...',
        'Finding the best approach...',
        'Putting the picture together...',
        'Analyzing...',
        'Running through options...',
    ],
}

DEEP_THINKING_PHRASES = {
    'ru': [
        'Копаю глубже...',
        'Ещё чуть-чуть, почти нашёл...',
        'Связываю факты воедино...',
        'Финишная прямая...',
        'Складываю пазл...',
        'Докручиваю детали...',
        'Верифицирую данные...',
        'Полирую результат...',
    ],
    'en': [
        'Digging deeper...',
        'Almost there...',
        'Connecting the dots...',
        'Final stretch...',
        'Piecing together the puzzle...',
        'Fine-tuning details...',
        'Verifying data...',
        'Polishing the result...',
    ],
}

ERROR_RESPONSES = {
    'ru': [
        "Что-то пошло не так. Перефразируй запрос.",
        "Техническая ошибка. Попробуй ещё раз.",
        "Упс, сбой. Скажи то же самое другими словами.",
        "Технические неполадки. Давай попробуем по-другому.",
        "Что-то сломалось. Перефразируй, пожалуйста.",
    ],
    'en': [
        "Something went wrong. Try rephrasing your request.",
        "Technical error. Please try again.",
        "Oops, a glitch. Say it differently.",
        "Technical difficulties. Let's try another way.",
        "Something broke. Please rephrase.",
    ],
}

# ═══════════════════════════════════════════════════════
# СТРОКИ: БОТ-КОМАНДЫ
# ═══════════════════════════════════════════════════════

_register('cmd_start',
    "Привет{name}! Я ASI Biont — твой мыслящий ИИ-партнёр.\n\n"
    "Я помогу тебе управлять задачами, достигать целей и находить нужных людей.\n\n"
    "Просто напиши мне что тебе нужно, и я помогу!",
    
    "Hi{name}! I'm ASI Biont — your thinking AI partner.\n\n"
    "I'll help you manage tasks, achieve goals, and find the right people.\n\n"
    "Just tell me what you need, and I'll help!")

_register('cmd_help',
    "**Возможности ASI Biont:**\n\n"
    "Просто пиши — я пойму\n"
    "Задачи: создание, перенос, завершение\n"
    "Цели: постановка и отслеживание\n"
    "Исследования: поиск информации\n"
    "Контакты: поиск нужных людей\n"
    "Посты: генерация контента\n\n"
    "/tasks — мои задачи\n"
    "/goals — мои цели\n"
    "/profile — мой профиль\n"
    "/buy — пополнить токены\n"
    "/lang — сменить язык",
    
    "**ASI Biont features:**\n\n"
    "Just write — I'll understand\n"
    "Tasks: create, reschedule, complete\n"
    "Goals: set and track\n"
    "Research: find information\n"
    "Contacts: find the right people\n"
    "Posts: generate content\n\n"
    "/tasks — my tasks\n"
    "/goals — my goals\n"
    "/profile — my profile\n"
    "/buy — buy tokens\n"
    "/lang — change language")

_register('cmd_tasks_empty',
    "У тебя нет активных задач. Напиши мне что нужно сделать!",
    "You have no active tasks. Tell me what needs to be done!")

_register('cmd_lang_current',
    "Текущий язык: {lang}\n\nСменить: /lang en или /lang ru",
    "Current language: {lang}\n\nSwitch: /lang en or /lang ru")

_register('cmd_lang_changed',
    "Язык изменён на: {lang}",
    "Language changed to: {lang}")

# ═══════════════════════════════════════════════════════
# СТРОКИ: ВРЕМЯ / ДАТЫ
# ═══════════════════════════════════════════════════════

MONTHS_GENITIVE = {
    'ru': ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
           'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'],
    'en': ['January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December'],
}

TIME_OF_DAY = {
    'ru': {'morning': 'утро', 'day': 'день', 'evening': 'вечер', 'night': 'ночь'},
    'en': {'morning': 'morning', 'day': 'afternoon', 'evening': 'evening', 'night': 'night'},
}

_register('today', 'Сегодня', 'Today')
_register('tomorrow', 'Завтра', 'Tomorrow')
_register('yesterday', 'Вчера', 'Yesterday')


def pluralize(lang: str, n: int, forms_ru: tuple, forms_en: tuple = None) -> str:
    """Склонение числительных.
    
    Args:
        lang: 'ru' или 'en'
        n: число
        forms_ru: ('задача', 'задачи', 'задач')
        forms_en: ('task', 'tasks') — если None, используем forms_ru[0]/forms_ru[1]
    """
    if lang == 'en':
        if forms_en:
            return forms_en[0] if n == 1 else forms_en[1]
        return str(n)
    
    # Русское склонение
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 19:
        return forms_ru[2]
    elif n1 == 1:
        return forms_ru[0]
    elif 2 <= n1 <= 4:
        return forms_ru[1]
    else:
        return forms_ru[2]
