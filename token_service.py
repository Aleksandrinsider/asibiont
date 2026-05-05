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
import os
import time
import random
from models import Session, User

logger = logging.getLogger(__name__)


def _is_transient_lock_error(exc: Exception) -> bool:
    """Detect PostgreSQL row lock timeout / lock-not-available transient errors."""
    txt = str(exc or '').lower()
    return (
        'locknotavailable' in txt
        or 'lock timeout' in txt
        or 'could not obtain lock' in txt
        or 'canceling statement due to lock timeout' in txt
    )

# ═══════════════════════════════════════════════════════
# СТОИМОСТЬ ДЕЙСТВИЙ (1 токен = 1 рубль)
# ═══════════════════════════════════════════════════════

ACTION_COSTS = {
    # Ценообразование: 50x от реальной стоимости DeepSeek V3
    # DeepSeek V3 ≈ $0.00043/1k токенов (с учётом кэша 63%).
    # При $1=90₽: ~0.039₽/1k DS-токенов → 50× = ~2 токена/1k DS-токенов.
    # 1 платформенный токен = 1 рубль.

    # ── Базовые ──
    'message':           15,   # ~8k DS-токенов → 50x → 15
    'voice_message':     18,   # ~9k DS-токенов → 50x → 18

    # ── Задачи ──
    'add_task':           3,   # Минимальное AI — внутри message-цикла
    'edit_task':          2,   # DB-операция, без отдельного AI-вызова
    'complete_task':      1,   # Завершение задачи
    'delete_task':        1,   # Удаление задачи
    'reschedule_task':    2,   # Перенос задачи
    'restore_task':       1,   # Восстановление задачи
    'list_tasks':         1,   # Просмотр списка задач
    'get_task_details':   1,   # Детали задачи

    # ── Цели ──
    'create_goal':        3,   # DB-операция внутри message-потока
    'update_goal':        2,   # Обновление цели
    'complete_goal':      1,   # Завершение цели
    'list_goals':         1,   # Список целей

    # ── Делегирование ──
    'delegate_task':     22,   # ~12k DS-токенов (director-цикл) → 22
    'get_delegation_progress': 3,  # Проверка статуса
    'cancel_delegation':  3,   # Отмена делегирования

    # ── Аналитика ──
    'analyze_situation_and_suggest_tasks': 15,   # ~8k DS-токенов → 15
    'research_and_plan':  15,   # ~8k DS-токенов → 15
    'analyze_group_opportunities': 12,  # ~6k DS-токенов → 12

    # ── Маркетинг ──
    'generate_marketing_content': 18,  # ~9k DS-токенов → 18
    'set_content_strategy':        8,  # ~4k DS-токенов → 8
    'publish_to_telegram':        10,  # ~5k DS-токенов → 10
    'publish_to_discord':          10,  # ~5k DS-токенов → 10
    'generate_image':              12,  # Внешний API (Replicate Flux) + промпт AI

    # ── Автономные функции ──
    'toggle_autonomous_feature':   3,  # Вкл/выкл автономной функции

    # ── Контакты / профиль ──
    'find_partners':      4,   # ~2k DS-токенов → 4
    'update_profile':     2,   # DB-операция
    'smart_update_profile': 2,

    # ── Напоминания ──
    'set_reminder':       2,   # DB-операция

    # ── Утилиты ──
    'get_weather_info':   1,   # Внешний API
    'get_news_trends':    3,   # Внешний API
    'quick_topic_search': 4,   # ~2k DS-токенов → 4
    'research_topic':     8,   # ~4k DS-токенов → 8
    'get_system_status':  0,   # Диагностика сервисов — бесплатно

    # ── Email-аутрич ──
    'start_email_campaign':     12,  # ~6k DS-токенов → 12
    'update_email_campaign':     3,  # Обновление параметров
    'send_outreach_email':       8,  # ~4k DS-токенов → 8
    'email_send':                8,  # Alias для send_outreach_email
    'send_email':                8,  # ~4k DS-токенов → 8
    'reply_to_outreach_email':   6,  # ~3k DS-токенов → 6
    'email_reply':               6,  # Alias для reply
    'send_follow_up_email':      8,  # ~4k DS-токенов → 8
    'email_follow_up':           8,  # Alias для follow-up
    'add_email_leads':           3,  # Добавление лидов в кампанию
    'get_email_campaign_status': 1,  # Просмотр статуса кампании
    'pause_email_campaign':      1,  # Пауза/возобновление кампании
    'save_email_contact':        1,  # Сохранение email-контакта
    'list_email_contacts':       1,  # Просмотр контактов
    'negotiate_by_email':       12,  # ~6k DS-токенов (первое письмо + цепочка) → 12

    # ── Контент-кампании ──
    'start_content_campaign':   10,  # ~5k DS-токенов → 10
    'manage_content_campaign':   3,  # Управление контент-кампанией

    # ── Кампании делегирования ──
    'start_delegation_campaign': 10,  # ~5k DS-токенов → 10
    'manage_delegation_campaign': 3,  # Управление кампанией делегирования

    # ── Задачи (дополнительно) ──
    'accept_delegated_task':     2,  # Принятие делегированной задачи
    'reject_delegated_task':     2,  # Отклонение делегированной задачи
    'check_time_conflicts':      1,  # Проверка конфликтов времени
    'find_relevant_contacts_for_task': 6,  # ~3k DS-токенов → 6

    # ── Цели (дополнительно) ──
    'delete_goal':               1,  # Удаление цели
    'update_goal_progress':      2,  # DB-операция

    # ── Посты ──
    'create_post':               2,  # Создание поста
    'edit_post':                 2,  # Редактирование поста
    'edit_note':                 2,  # Редактирование заметки/блог-статьи

    # ── Арена агентов ──
    'arena_agent_post':          15,  # Полный AI-вызов (= message)
    'agent_task':                28,  # ~15k DS-токенов (director-цикл) → 28
    'agent_chime':                6,  # ~3k DS-токенов → 6
    'get_posts':                  1,  # Просмотр постов
    'delete_post':                1,  # Удаление поста

    # ── Маркетплейс / скрипты ──
    'list_marketplace':          1,   # Просмотр маркетплейса (только листинг)
    'switch_agent':              1,   # Переключение на агента / возврат к ASI Biont
    'run_agent_action':          5,   # Запуск действия через внешнюю интеграцию агента
    'run_user_script':           5,   # Запуск пользовательского скрипта из маркетплейса
    'install_script':            3,   # Установка скрипта из маркетплейса
    'save_user_rule':            1,   # Сохранение правила поведения AI
    'schedule_background_task':  3,   # Планирование фоновой задачи

    # ── Уведомления / контакты ──
    'set_contact_alert':         2,  # DB-операция
    'find_and_message_relevant_users': 10,  # ~5k DS-токенов → 10

    # ── Сообщения (автономный агент) ──
    'send_message_to_user':      3,  # Отправка сообщения пользователю
    'reply_to_user_message':     3,  # Ответ на сообщение пользователя
    'get_incoming_messages':     1,  # Просмотр входящих
    'get_message_status':        1,  # Статус сообщения

    # ── Поиск / веб ──
    'web_search':                4,  # ~2k DS-токенов → 4

    # ── Автопосты (фоновый сервис) ──
    'auto_post':         15,   # ~8k DS-токенов → 15

    # ── Проактивные (от агента) ──
    'proactive_message':  8,   # Fallback-стоимость; реальный расход покрывается динамическим биллингом
    'proactive_post':    15,   # ~8k DS-токенов → 15
    'proactive_channel': 15,   # ~8k DS-токенов → 15
}

# Стоимость по умолчанию для неизвестных инструментов
DEFAULT_TOOL_COST = 4

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


def spend_tokens(user_id: int, action: str, description: str = '', session=None, auto_commit: bool = True, cost: int = None) -> dict:
    """
    Списывает токены за действие.
    
    Args:
        auto_commit: Если False, не коммитит (вызывающий код коммитит сам) — для транзакционной целостности.
        cost: Переопределить стоимость вместо ACTION_COSTS[action] (для динамического биллинга).
    
    Returns: {'success': True/False, 'balance': int, 'spent': int, 'error': str}
    """
    cost = cost if cost is not None else ACTION_COSTS.get(action, DEFAULT_TOOL_COST)
    
    close = False
    if session is None:
        session = Session()
        close = True
    
    _max_retries = max(1, int(os.getenv('TOKEN_LOCK_RETRIES', '3')))
    _lock_timeout_ms = max(200, int(os.getenv('TOKEN_LOCK_TIMEOUT_MS', '800')))

    try:
        for _attempt in range(_max_retries):
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
                    # Короткий lock_timeout: если строка заблокирована другой транзакцией,
                    # не ждём долго — лучше короткий retry с backoff.
                    try:
                        session.execute(sa_text(f"SET LOCAL lock_timeout = '{_lock_timeout_ms}ms'"))
                    except Exception:
                        pass  # SQLite / старый PG — игнорируем
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
                session.rollback()
                if _is_transient_lock_error(e) and _attempt < (_max_retries - 1):
                    _sleep_s = 0.05 * (_attempt + 1) + random.uniform(0.01, 0.06)
                    logger.info(
                        "[TOKEN] spend_tokens lock contention (attempt %d/%d), retry in %.2fs: %s",
                        _attempt + 1,
                        _max_retries,
                        _sleep_s,
                        e,
                    )
                    time.sleep(_sleep_s)
                    continue
                logger.warning(f"[TOKEN] spend_tokens skipped (lock contention or transient error): {e}")
                return {'success': False, 'balance': 0, 'spent': 0, 'error': str(e)}

        return {'success': False, 'balance': 0, 'spent': 0, 'error': 'lock contention retries exceeded'}
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
    
    _max_retries = max(1, int(os.getenv('TOKEN_LOCK_RETRIES', '3')))
    _lock_timeout_ms = max(200, int(os.getenv('TOKEN_LOCK_TIMEOUT_MS', '800')))

    try:
        for _attempt in range(_max_retries):
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
                    try:
                        session.execute(sa_text(f"SET LOCAL lock_timeout = '{_lock_timeout_ms}ms'"))
                    except Exception:
                        pass
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
                session.rollback()
                if _is_transient_lock_error(e) and _attempt < (_max_retries - 1):
                    _sleep_s = 0.05 * (_attempt + 1) + random.uniform(0.01, 0.06)
                    logger.info(
                        "[TOKEN] add_tokens lock contention (attempt %d/%d), retry in %.2fs: %s",
                        _attempt + 1,
                        _max_retries,
                        _sleep_s,
                        e,
                    )
                    time.sleep(_sleep_s)
                    continue
                logger.error(f"[TOKEN] add_tokens error: {e}")
                return {'success': False, 'balance': 0, 'error': str(e)}

        return {'success': False, 'balance': 0, 'error': 'lock contention retries exceeded'}
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
        
        days_left = round(balance / 300, 1) if balance > 0 else 0
        
        if lang == 'en':
            text = f"💰 Balance: {balance} tokens (~{days_left} days)\n"
            text += f"📊 Total spent: {spent} tokens\n\n"
            if balance < 100:
                text += "⚠️ Running low on tokens! Top up: /buy\n\n"
            text += "📋 Action costs:\n"
            text += f"• Message: {ACTION_COSTS['message']}\n"
            text += f"• Create task: {ACTION_COSTS['add_task']}\n"
            text += f"• Delegation: {ACTION_COSTS['delegate_task']}\n"
            text += f"• Marketing content: {ACTION_COSTS['generate_marketing_content']}\n"
            text += f"• Proactive message: {ACTION_COSTS['proactive_message']}\n"
            text += f"\nTop up: /buy"
        else:
            text = f"💰 Баланс: {balance} токенов (~{days_left} дней)\n"
            text += f"📊 Потрачено всего: {spent} токенов\n\n"
            if balance < 100:
                text += "⚠️ Токены заканчиваются! Пополни: /buy\n\n"
            text += "📋 Стоимость действий:\n"
            text += f"• Сообщение: {ACTION_COSTS['message']}\n"
            text += f"• Создание задачи: {ACTION_COSTS['add_task']}\n"
            text += f"• Делегирование: {ACTION_COSTS['delegate_task']}\n"
            text += f"• Маркетинг-контент: {ACTION_COSTS['generate_marketing_content']}\n"
            text += f"• Проактивное сообщение: {ACTION_COSTS['proactive_message']}\n"
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
    result = spend_tokens(user_id, action, description=f'anchor_{action}', session=session)
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
