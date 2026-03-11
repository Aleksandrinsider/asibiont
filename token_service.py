"""
Token Service — система токенов (1 токен = 1 рубль).

Все функции открыты для всех пользователей.
Вместо тарифных замков — расход токенов за каждое действие.
Продвинутые фичи стоят дороже.

Целевое потребление: ~300-600₽/день → ~9 000-18 000₽/мес.
Бесплатно: 1 500 токенов при регистрации (~3 дня).

Пакеты покупки:
  1 500 ₽ — 1 500 токенов
  5 000 ₽ — 5 500 токенов (10% бонус)
 50 000 ₽ — 60 000 токенов (20% бонус)
"""

import logging
import json
import datetime
from models import Session, User

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# СТОИМОСТЬ ДЕЙСТВИЙ (1 токен = 1 рубль)
# ═══════════════════════════════════════════════════════

ACTION_COSTS = {
    # ── Базовые ──
    'message':           10,   # Чат с AI (один раунд: запрос + ответ)
    'voice_message':     12,   # Голосовое сообщение (транскрипция + AI)

    # ── Задачи ──
    'add_task':           7,   # Создание задачи
    'edit_task':          5,   # Редактирование задачи
    'complete_task':      2,   # Завершение задачи
    'delete_task':        2,   # Удаление задачи
    'reschedule_task':    5,   # Перенос задачи
    'restore_task':       2,   # Восстановление задачи
    'list_tasks':         2,   # Просмотр списка задач
    'get_task_details':   2,   # Детали задачи

    # ── Цели ──
    'create_goal':       10,   # Создание цели
    'update_goal':        7,   # Обновление цели
    'complete_goal':      2,   # Завершение цели
    'list_goals':         2,   # Список целей

    # ── Делегирование ──
    'delegate_task':     20,   # Делегирование задачи
    'get_delegation_progress': 5,  # Проверка статуса
    'cancel_delegation':  5,   # Отмена делегирования

    # ── Аналитика ──
    'analyze_situation_and_suggest_tasks': 15,   # Полный анализ
    'research_and_plan':  15,   # Исследование и планирование
    'analyze_group_opportunities': 12,  # Анализ групповых возможностей

    # ── Маркетинг ──
    'generate_marketing_content': 30,  # Генерация маркетинг-контента
    'set_content_strategy':       15,  # Настройка стратегии контента
    'publish_to_telegram':        30,  # Публикация в TG-канал
    'publish_to_discord':          30,  # Публикация в Discord канал
    'generate_image':              20,  # Генерация изображения (Replicate Flux)

    # ── Автономные функции ──
    'toggle_autonomous_feature':   5,  # Вкл/выкл автономной функции

    # ── Контакты / профиль ──
    'find_partners':      7,   # Поиск партнёров
    'update_profile':     5,   # Обновление профиля
    'smart_update_profile': 5,

    # ── Напоминания ──
    'set_reminder':       5,   # Установка напоминания

    # ── Утилиты ──
    'get_weather_info':   2,   # Погода
    'get_news_trends':    5,   # Новости
    'quick_topic_search': 5,   # Быстрый поиск
    'research_topic':    10,   # Глубокое исследование
    'get_system_status':  0,   # Диагностика сервисов — бесплатно

    # ── Email-аутрич ──
    'start_email_campaign':     15,  # Создание email-кампании
    'update_email_campaign':     5,  # Обновление параметров email-кампании
    'send_outreach_email':      10,  # Отправка outreach-письма
    'email_send':               10,  # Alias для send_outreach_email
    'send_email':               10,  # Прямая отправка письма
    'reply_to_outreach_email':   8,  # Ответ на входящий reply
    'email_reply':               8,  # Alias для reply
    'send_follow_up_email':     10,  # Follow-up письмо
    'email_follow_up':          10,  # Alias для follow-up
    'add_email_leads':           5,  # Добавление лидов в кампанию
    'get_email_campaign_status': 2,  # Просмотр статуса кампании
    'pause_email_campaign':      2,  # Пауза/возобновление кампании
    'save_email_contact':        2,  # Сохранение email-контакта
    'list_email_contacts':       2,  # Просмотр контактов
    'negotiate_by_email':       12,  # Запуск переговоров по email (первое письмо + отслеживание)

    # ── Контент-кампании ──
    'start_content_campaign':   15,  # Создание контент-кампании
    'manage_content_campaign':   5,  # Управление контент-кампанией

    # ── Кампании делегирования ──
    'start_delegation_campaign': 15,  # Создание кампании делегирования
    'manage_delegation_campaign': 5,  # Управление кампанией делегирования

    # ── Задачи (дополнительно) ──
    'accept_delegated_task':     3,  # Принятие делегированной задачи
    'reject_delegated_task':     3,  # Отклонение делегированной задачи
    'check_time_conflicts':      2,  # Проверка конфликтов времени
    'find_relevant_contacts_for_task': 5,  # Поиск контактов для задачи

    # ── Цели (дополнительно) ──
    'delete_goal':               2,  # Удаление цели
    'update_goal_progress':      5,  # Обновление прогресса цели

    # ── Посты ──
    'create_post':               5,  # Создание поста
    'edit_post':                 3,  # Редактирование поста

    # ── Арена агентов ──
    'arena_agent_post':          10,  # Пост/комментарий агента в Арене (=message)
    'agent_task':                15,  # Запуск агента директором (каждый agent в delegate/multi_delegate)
    'agent_chime':                5,  # Фоновое включение агента в диалог
    'get_posts':                 2,  # Просмотр постов
    'delete_post':               2,  # Удаление поста

    # ── Маркетплейс / скрипты ──
    'list_marketplace':          2,   # Просмотр маркетплейса (только листинг)
    'switch_agent':              2,   # Переключение на агента / возврат к ASI Biont
    'run_agent_action':         10,   # Запуск действия через внешнюю интеграцию агента
    'run_user_script':          10,   # Запуск пользовательского скрипта из маркетплейса
    'install_script':            5,   # Установка скрипта из маркетплейса
    'save_user_rule':            2,   # Сохранение правила поведения AI
    'schedule_background_task':  5,   # Планирование фоновой задачи

    # ── Уведомления / контакты ──
    'set_contact_alert':         5,  # Настройка уведомления о контакте
    'find_and_message_relevant_users': 10,  # Поиск и отправка пользователям

    # ── Сообщения (автономный агент) ──
    'send_message_to_user':      5,  # Отправка сообщения пользователю
    'reply_to_user_message':     5,  # Ответ на сообщение пользователя
    'get_incoming_messages':     2,  # Просмотр входящих
    'get_message_status':        2,  # Статус сообщения

    # ── Поиск / веб ──
    'web_search':                5,  # Быстрый веб-поиск

    # ── Проактивные (от агента) ──
    'proactive_message':  7,   # Проактивное сообщение
    'proactive_post':    10,   # Проактивный пост в ленту
    'proactive_channel': 15,   # Проактивный пост в канал
}

# Стоимость по умолчанию для неизвестных инструментов
DEFAULT_TOOL_COST = 5

# Токены при регистрации — хватит на ~3 дня активного использования
FREE_TOKENS_ON_SIGNUP = 1500

# ═══════════════════════════════════════════════════════
# ПАКЕТЫ ПОКУПКИ
# ═══════════════════════════════════════════════════════

TOKEN_PACKAGES = {
    'small':  {'price': 1500,  'tokens': 1500,  'label': '1 500 ₽ — 1 500 токенов'},
    'medium': {'price': 5000,  'tokens': 5500,  'label': '5 000 ₽ — 5 500 токенов (+10%)'},
    'large':  {'price': 50000, 'tokens': 60000, 'label': '50 000 ₽ — 60 000 токенов (+20%)'},
}

# ═══════════════════════════════════════════════════════
# ОСНОВНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════


def get_balance(user_id: int, session=None) -> int:
    """Возвращает текущий баланс токенов пользователя."""
    close = False
    if session is None:
        session = Session()
        close = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return 0
        return user.token_balance or 0
    finally:
        if close:
            session.close()


def has_enough_tokens(user_id: int, action: str, session=None) -> bool:
    """Проверяет, хватает ли токенов для действия."""
    cost = ACTION_COSTS.get(action, DEFAULT_TOOL_COST)
    balance = get_balance(user_id, session)
    return balance >= cost


def spend_tokens(user_id: int, action: str, description: str = '', session=None, auto_commit: bool = True) -> dict:
    """
    Списывает токены за действие.
    
    Args:
        auto_commit: Если False, не коммитит (вызывающий код коммитит сам) — для транзакционной целостности.
    
    Returns: {'success': True/False, 'balance': int, 'spent': int, 'error': str}
    """
    cost = ACTION_COSTS.get(action, DEFAULT_TOOL_COST)
    
    close = False
    if session is None:
        session = Session()
        close = True
    
    try:
        # Атомарное списание — защита от race condition
        from sqlalchemy import text as sa_text
        from config import LOCAL
        
        if LOCAL:
            # SQLite не поддерживает RETURNING — используем ORM
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user or (user.token_balance or 0) < cost:
                balance = (user.token_balance or 0) if user else 0
                return {
                    'success': False,
                    'balance': balance,
                    'spent': 0,
                    'error': f'Недостаточно токенов. Нужно: {cost}, баланс: {balance}. Пополни: /buy'
                }
            user.token_balance = user.token_balance - cost
            user.tokens_spent = (user.tokens_spent or 0) + cost
            db_user_id = user.id
            new_balance = user.token_balance
        else:
            result = session.execute(
                sa_text(
                    "UPDATE users SET token_balance = token_balance - :cost, "
                    "tokens_spent = COALESCE(tokens_spent, 0) + :cost "
                    "WHERE telegram_id = :tid AND COALESCE(token_balance, 0) >= :cost "
                    "RETURNING id, token_balance"
                ),
                {'cost': cost, 'tid': user_id}
            )
            row = result.fetchone()
            if not row:
                balance = get_balance(user_id, session)
                return {
                    'success': False,
                    'balance': balance,
                    'spent': 0,
                    'error': f'Недостаточно токенов. Нужно: {cost}, баланс: {balance}. Пополни: /buy'
                }
            db_user_id, new_balance = row
        
        # Записываем транзакцию
        from models import TokenTransaction
        tx = TokenTransaction(
            user_id=db_user_id,
            amount=-cost,
            action=action,
            description=description or action,
            balance_after=new_balance
        )
        session.add(tx)
        if auto_commit:
            session.commit()
        
        logger.debug(f"[TOKEN] User {user_id}: -{cost} за {action} (баланс: {new_balance})")
        
        return {
            'success': True,
            'balance': new_balance,
            'spent': cost,
        }
        
    except Exception as e:
        logger.error(f"[TOKEN] spend_tokens error: {e}")
        session.rollback()
        return {'success': False, 'balance': 0, 'spent': 0, 'error': str(e)}
    finally:
        if close:
            session.close()


def add_tokens(user_id: int, amount: int, reason: str = 'purchase', session=None) -> dict:
    """
    Начисляет токены.
    
    Args:
        user_id: telegram_id
        amount: количество токенов
        reason: причина ('signup', 'purchase', 'bonus', 'refund')
    
    Returns: {'success': True/False, 'balance': int}
    """
    close = False
    if session is None:
        session = Session()
        close = True
    
    try:
        # Атомарное начисление — защита от race condition при параллельных webhook'ах
        from sqlalchemy import text as sa_text
        from config import LOCAL
        
        if LOCAL:
            # SQLite не поддерживает RETURNING — используем ORM
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {'success': False, 'balance': 0, 'error': 'Пользователь не найден'}
            user.token_balance = (user.token_balance or 0) + amount
            new_balance = user.token_balance
            db_user_id = user.id
        else:
            result = session.execute(
                sa_text(
                    "UPDATE users SET token_balance = COALESCE(token_balance, 0) + :amount "
                    "WHERE telegram_id = :tid "
                    "RETURNING id, token_balance"
                ),
                {'amount': amount, 'tid': user_id}
            )
            row = result.fetchone()
            if not row:
                return {'success': False, 'balance': 0, 'error': 'Пользователь не найден'}
            db_user_id, new_balance = row
        
        from models import TokenTransaction
        tx = TokenTransaction(
            user_id=db_user_id,
            amount=amount,
            action=reason,
            description=f'+{amount} токенов ({reason})',
            balance_after=new_balance
        )
        session.add(tx)
        session.commit()
        
        logger.info(f"[TOKEN] User {user_id}: +{amount} ({reason}) → баланс: {new_balance}")
        
        return {'success': True, 'balance': new_balance}
        
    except Exception as e:
        logger.error(f"[TOKEN] add_tokens error: {e}")
        session.rollback()
        return {'success': False, 'balance': 0, 'error': str(e)}
    finally:
        if close:
            session.close()


def grant_signup_tokens(user_id: int, session=None) -> dict:
    """Начисляет бесплатные токены при регистрации."""
    return add_tokens(user_id, FREE_TOKENS_ON_SIGNUP, reason='signup', session=session)


def get_balance_info(user_id: int, session=None) -> str:
    """Formatted balance information."""
    from i18n import get_user_lang
    lang = get_user_lang(user_id)
    close = False
    if session is None:
        session = Session()
        close = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "User not found." if lang == 'en' else "Хм, не нахожу твой профиль — отправь /start"
        
        balance = user.token_balance or 0
        spent = user.tokens_spent or 0
        
        days_left = round(balance / 450, 1) if balance > 0 else 0
        
        if lang == 'en':
            text = f"💰 Balance: {balance} tokens (~{days_left} days)\n"
            text += f"📊 Total spent: {spent} tokens\n\n"
            if balance < 100:
                text += "⚠️ Running low on tokens! Top up: /buy\n\n"
            text += "📋 Action costs:\n"
            text += "• Message: 10\n"
            text += "• Create task: 7\n"
            text += "• Delegation: 20\n"
            text += "• Marketing content: 30\n"
            text += "• Proactive message: 7\n"
            text += f"\nTop up: /buy"
        else:
            text = f"💰 Баланс: {balance} токенов (~{days_left} дней)\n"
            text += f"📊 Потрачено всего: {spent} токенов\n\n"
            if balance < 100:
                text += "⚠️ Токены заканчиваются! Пополни: /buy\n\n"
            text += "📋 Стоимость действий:\n"
            text += "• Сообщение: 10\n"
            text += "• Создание задачи: 7\n"
            text += "• Делегирование: 20\n"
            text += "• Маркетинг-контент: 30\n"
            text += "• Проактивное сообщение: 7\n"
            text += f"\nПополнить: /buy"
        
        return text
    finally:
        if close:
            session.close()


async def check_and_deduct(user_id: int, action: str, session=None) -> bool:
    """Асинхронная проверка и списание токенов (используется в anchor_engine).
    
    Returns:
        True — токены есть и списаны успешно
        False — токенов не хватает (ничего не списывается)
    """
    if not has_enough_tokens(user_id, action, session):
        return False
    result = spend_tokens(user_id, action, description=f'anchor_{action}', session=session, auto_commit=False)
    return result.get('success', False)


def insufficient_balance_message(user_id: int, action: str, session=None) -> str:
    """Сообщение о недостатке токенов."""
    from i18n import get_user_lang
    lang = get_user_lang(user_id)
    cost = ACTION_COSTS.get(action, DEFAULT_TOOL_COST)
    balance = get_balance(user_id, session)
    
    if lang == 'en':
        return (
            f"⚠️ Not enough tokens for this action.\n\n"
            f"Required: {cost} tokens\n"
            f"Balance: {balance} tokens\n\n"
            f"Top up: /buy"
        )
    return (
        f"⚠️ Токенов не хватает\n\n"
        f"Нужно: {cost}\n"
        f"Баланс: {balance}\n\n"
        f"Пополни: /buy"
    )
