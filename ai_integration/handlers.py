# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timedelta, timezone
import pytz
import requests
import aiohttp
from models import Session, Task, User, UserProfile, Subscription, Goal, Post, PostLike, PostView, Comment, UserMessage, EmailCampaign, EmailOutreach, EmailContact, Anchor, AnchorPriority
from sqlalchemy import or_, and_, func

from .memory import encrypt_data, decrypt_data, LongTermMemory
from .utils import (
    parse_time_to_datetime,
    generate_unified_recommendations,
    normalize_task_title,
    sanitize_live_team_chat_text,
)
from .task_search import find_task_flexible
from .dialog_context import get_user_context, resolve_task_reference
from . import marketing_agent
from config import OPENWEATHERMAP_API_KEY, NEWSAPI_API_KEY, encrypt_token, decrypt_token, redact_email

logger = logging.getLogger(__name__)

# Множество user_id для которых сейчас активен ASI-обзор отчёта агента.
# Предотвращает бесконечную рекурсию: отчёт → ASI → делегирование → отчёт → ...
_ASI_REPORT_REVIEW_ACTIVE: set = set()

# ── Email validation cache ──
_mx_cache = {}  # domain → (has_mx: bool, timestamp)


def _validate_email_domain(email: str) -> tuple:
    """Check if email domain has valid MX records. Returns (is_valid, error_message).

    Uses DNS MX lookup to catch typos and non-existent domains BEFORE sending.
    Caches results for 1 hour to avoid repeated DNS queries.
    """
    try:
        import dns.resolver
    except ImportError:
        return True, None  # dnspython not installed — skip check, don't block
    import time

    try:
        domain = email.strip().lower().split('@')[-1]
        if not domain or '.' not in domain:
            return False, f"Некорректный домен: {domain}"

        # RFC 2606 reserved + disposable domains — have MX but don't deliver
        _TRAP_DOMAINS = {
            'example.com', 'example.org', 'example.net',
            'test.com', 'test.org', 'test.ru',
            'mailinator.com', 'guerrillamail.com', 'yopmail.com',
            'throwaway.email', 'tempmail.com', 'sharklasers.com',
            'grr.la', 'dispostable.com', 'trashmail.com',
        }
        if domain in _TRAP_DOMAINS:
            return False, f"Домен {domain} — одноразовый/тестовый, письма не будут доставлены"

        # Check cache (1 hour TTL)
        cached = _mx_cache.get(domain)
        if cached and (time.time() - cached[1]) < 3600:
            if cached[0]:
                return True, None
            return False, f"Домен {domain} не принимает почту (нет MX-записей)"

        # DNS MX lookup
        try:
            answers = dns.resolver.resolve(domain, 'MX')
            has_mx = len(answers) > 0
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            has_mx = False
        except Exception:
            # DNS timeout or other transient error — let it through
            return True, None

        _mx_cache[domain] = (has_mx, time.time())
        # Limit cache size
        if len(_mx_cache) > 1000:
            import time as _t
            now = _t.time()
            stale = [k for k, v in _mx_cache.items() if now - v[1] > 3600]
            for k in stale:
                del _mx_cache[k]

        if not has_mx:
            return False, f"Домен {domain} не принимает почту (нет MX-записей). Проверь email."
        return True, None
    except Exception:
        return True, None  # On any unexpected error, don't block sending


def _text_to_email_html(text: str) -> str:
    """Конвертирует plain-text тело письма в HTML с сохранением абзацев.
    \n\n → <p>, \n → <br>. Параграфы не слипаются в один блок.
    """
    import html as _html_mod
    safe = _html_mod.escape(text)
    # Разбиваем на параграфы по двойному переносу
    paragraphs = safe.split('\n\n')
    html_parts = []
    for p in paragraphs:
        p_html = p.replace('\n', '<br>')
        html_parts.append(f'<p style="margin: 0 0 12px 0;">{p_html}</p>')
    return ''.join(html_parts)


def _build_email_html(body_html: str, unsub_email: str = 'outreach@asibiont.com', sender_name: str = '') -> str:
    """Общий HTML-шаблон для email с unsubscribe footer.

    Чистый текстовый стиль — без баннеров, кнопок, логотипов.
    Как личное письмо.
    """
    unsub_line_ru = f'Если вы не хотите получать подобные письма, просто ответьте "отписаться" на это сообщение или напишите на {unsub_email}'
    unsub_line_en = f'If you don\'t want to receive such emails, simply reply "unsubscribe" to this message or write to {unsub_email}'
    sender_sig = f'— {sender_name}' if sender_name else ''

    return f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 14px; color: #374151; line-height: 1.6; margin: 0; padding: 0;">
<div style="max-width: 600px; margin: 0 auto; padding: 24px;">
{body_html}
</div>
<div style="max-width: 600px; margin: 0 auto; padding: 12px 24px; border-top: 1px solid #E5E7EB; font-size: 11px; color: #9CA3AF; line-height: 1.5;">
{unsub_line_ru}<br>
{unsub_line_en}
</div>
</body></html>"""


def _get_lang(user_id):
    """Get user language, default ru."""
    try:
        from i18n import get_user_lang
        return get_user_lang(user_id)
    except Exception:
        return 'ru'


def _t(user_id, key, **kwargs):
    """Translate string by user_id."""
    try:
        from i18n import tu
        return tu(user_id, key, **kwargs)
    except Exception:
        return key


def _utc_to_local(dt_naive, user_tz):
    """Конвертирует naive UTC datetime в локальный timezone пользователя.
    
    Исправляет баг: Python astimezone() на naive datetime использует 
    системный timezone машины, а не UTC. Эта функция всегда трактует
    входное время как UTC.
    """
    if dt_naive is None:
        return None
    if dt_naive.tzinfo is not None:
        return dt_naive.astimezone(user_tz)
    return dt_naive.replace(tzinfo=pytz.UTC).astimezone(user_tz)

def get_tier_priority(profile, session=None):
    """Deprecated — все пользователи равны. Возвращает 0."""
    return 0

# Расширенная карта часовых поясов для городов
CITY_TIMEZONE_MAP = {
    # Россия - Европейская часть (MSK, UTC+3)
    'москва': 'Europe/Moscow',
    'москве': 'Europe/Moscow',
    'санкт-петербург': 'Europe/Moscow',
    'петербург': 'Europe/Moscow',
    'спб': 'Europe/Moscow',
    'нижний новгород': 'Europe/Moscow',
    'нижний': 'Europe/Moscow',
    'казань': 'Europe/Moscow',
    'самара': 'Europe/Moscow',
    'саратов': 'Europe/Moscow',
    'волгоград': 'Europe/Moscow',
    'ростов-на-дону': 'Europe/Moscow',
    'ростов': 'Europe/Moscow',
    'краснодар': 'Europe/Moscow',
    'сочи': 'Europe/Moscow',
    'воронеж': 'Europe/Moscow',
    'ярославль': 'Europe/Moscow',
    'иваново': 'Europe/Moscow',
    'кострома': 'Europe/Moscow',
    'тверь': 'Europe/Moscow',
    'смоленск': 'Europe/Moscow',
    'брянск': 'Europe/Moscow',
    'курск': 'Europe/Moscow',
    'белгород': 'Europe/Moscow',
    'липецк': 'Europe/Moscow',
    'тамбов': 'Europe/Moscow',
    'орёл': 'Europe/Moscow',
    'тула': 'Europe/Moscow',
    'калуга': 'Europe/Moscow',
    
    # Россия - Уральский регион (YEKT, UTC+5)
    'пермь': 'Asia/Yekaterinburg',
    'екатеринбург': 'Asia/Yekaterinburg',
    'челябинск': 'Asia/Yekaterinburg',
    'тюмень': 'Asia/Yekaterinburg',
    'магнитогорск': 'Asia/Yekaterinburg',
    'нижний тагил': 'Asia/Yekaterinburg',
    'каменск-уральский': 'Asia/Yekaterinburg',
    'златоуст': 'Asia/Yekaterinburg',
    'миасс': 'Asia/Yekaterinburg',
    'кунгур': 'Asia/Yekaterinburg',
    
    # Россия - Сибирь (OMST, UTC+6)
    'омск': 'Asia/Omsk',
    'новосибирск': 'Asia/Novosibirsk',
    'томск': 'Asia/Novosibirsk',
    'барнаул': 'Asia/Novosibirsk',
    'кемерово': 'Asia/Novosibirsk',
    'новокузнецк': 'Asia/Novosibirsk',
    'прокопьевск': 'Asia/Novosibirsk',
    'ленск': 'Asia/Novosibirsk',
    
    # Россия - Красноярский край (KRAT, UTC+7)
    'красноярск': 'Asia/Krasnoyarsk',
    'абакан': 'Asia/Krasnoyarsk',
    'ачинск': 'Asia/Krasnoyarsk',
    'канск': 'Asia/Krasnoyarsk',
    'минусинск': 'Asia/Krasnoyarsk',
    'норильск': 'Asia/Krasnoyarsk',
    
    # Россия - Иркутская область (IRKT, UTC+8)
    'иркутск': 'Asia/Irkutsk',
    'братск': 'Asia/Irkutsk',
    'ангарск': 'Asia/Irkutsk',
    'улан-удэ': 'Asia/Irkutsk',
    'чита': 'Asia/Irkutsk',
    'усть-илимск': 'Asia/Irkutsk',
    
    # Россия - Дальний Восток (VLAT, UTC+10)
    'владивосток': 'Asia/Vladivostok',
    'хабаровск': 'Asia/Vladivostok',
    'южно-сахалинск': 'Asia/Vladivostok',
    'находка': 'Asia/Vladivostok',
    'арсеньев': 'Asia/Vladivostok',
    'спасск-дальний': 'Asia/Vladivostok',
    'биробиджан': 'Asia/Vladivostok',
    
    # Россия - Магаданская область (MAGT, UTC+11)
    'магадан': 'Asia/Magadan',
    'палатка': 'Asia/Magadan',
    
    # Россия - Камчатка (PETT, UTC+12)
    'петропавловск-камчатский': 'Asia/Kamchatka',
    'камчатка': 'Asia/Kamchatka',
    'анадырь': 'Asia/Anadyr',
    
    # Другие страны
    'карачи': 'Asia/Karachi',
    'дубай': 'Asia/Dubai',
    'лондон': 'Europe/London',
    'нью-йорк': 'America/New_York',
    'токио': 'Asia/Tokyo',
    'пекин': 'Asia/Shanghai',
    'бангкок': 'Asia/Bangkok',
    'сидней': 'Australia/Sydney',
}

def check_time_conflicts_sync(user_db_id, parsed_time, session):
    """
    Проверяет конфликты по времени для новой задачи
    
    Args:
        user_db_id: ID пользователя в БД (не telegram_id)
        parsed_time: Уже распарсенное время (datetime)
        session: Сессия БД
    
    Returns:
        tuple: (conflict_message, suggested_time) или None если конфликтов нет
    """
    try:
        if not parsed_time:
            return None
            
        # Получаем пользователя для часового пояса
        user = session.query(User).filter_by(id=user_db_id).first()
        if not user:
            return None
            
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        
        # Ищем задачи в интервале ±30 минут от новой задачи
        time_window_start = parsed_time - timedelta(minutes=30)
        time_window_end = parsed_time + timedelta(minutes=30)
        
        # Конвертируем в UTC для поиска в БД
        utc_start = time_window_start.astimezone(pytz.UTC)
        utc_end = time_window_end.astimezone(pytz.UTC)
        
        conflicting_tasks = session.query(Task).filter(
            Task.user_id == user_db_id,
            Task.status == 'pending',
            Task.reminder_time.between(utc_start, utc_end)
        ).all()
        
        if conflicting_tasks:
            # Находим ближайшее свободное время
            suggested_time = find_nearest_free_slot(user_db_id, parsed_time, session)
            
            task_list = "\n".join([f"• {task.title} ({_utc_to_local(task.reminder_time, user_tz).strftime('%H:%M')})" for task in conflicting_tasks])
            
            conflict_message = f"В это время у тебя уже запланированы задачи:\n{task_list}"
            
            if suggested_time:
                suggested_str = _utc_to_local(suggested_time, user_tz).strftime('%H:%M')
                return conflict_message, suggested_str
            else:
                return conflict_message, "укажи другое время"
                
    except Exception as e:
        logger.warning(f"Error checking time conflicts: {e}")
        return None
    
    return None

def find_nearest_free_slot(user_db_id, target_time, session, search_range_hours=4):
    """
    Находит ближайшее свободное время в пределах search_range_hours часов
    
    Args:
        user_db_id: ID пользователя в БД
        target_time: Желаемое время (datetime)
        session: Сессия БД
        search_range_hours: Диапазон поиска в часах
    
    Returns:
        datetime: Ближайшее свободное время или None
    """
    try:
        # Получаем все задачи пользователя на ближайшие часы
        user = session.query(User).filter_by(id=user_db_id).first()
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        
        search_start = target_time - timedelta(hours=search_range_hours//2)
        search_end = target_time + timedelta(hours=search_range_hours//2)
        
        utc_start = search_start.astimezone(pytz.UTC)
        utc_end = search_end.astimezone(pytz.UTC)
        
        existing_tasks = session.query(Task).filter(
            Task.user_id == user_db_id,
            Task.status == 'pending',
            Task.reminder_time.between(utc_start, utc_end)
        ).order_by(Task.reminder_time).all()
        
        # Конвертируем все времена в локальный timezone
        existing_times = [_utc_to_local(task.reminder_time, user_tz) for task in existing_tasks]
        target_local = _utc_to_local(target_time, user_tz)
        
        # Ищем свободные слоты по 30 минут
        current_time = datetime.now(user_tz)
        
        # Проверяем слоты после target_time
        for minutes_offset in range(0, search_range_hours * 60, 30):
            check_time = target_local + timedelta(minutes=minutes_offset)
            if check_time < current_time:
                continue  # Пропускаем прошедшее время
                
            # Проверяем, не конфликтует ли с существующими задачами
            conflict = False
            for existing_time in existing_times:
                if abs((check_time - existing_time).total_seconds()) < 1800:  # 30 минут
                    conflict = True
                    break
            
            if not conflict:
                return check_time
        
        # Проверяем слоты до target_time
        for minutes_offset in range(30, search_range_hours * 60, 30):
            check_time = target_local - timedelta(minutes=minutes_offset)
            if check_time < current_time:
                continue  # Пропускаем прошедшее время
                
            # Проверяем, не конфликтует ли с существующими задачами
            conflict = False
            for existing_time in existing_times:
                if abs((check_time - existing_time).total_seconds()) < 1800:  # 30 минут
                    conflict = True
                    break
            
            if not conflict:
                return check_time
                
    except Exception as e:
        logger.warning(f"Error finding free slot: {e}")
    
    return None

async def check_time_conflicts(reminder_time, user_id=None, session=None):
    """
    Асинхронная функция для проверки конфликтов времени (для tool calling)
    
    Args:
        reminder_time: Строка с временем в формате 'завтра в 10:00', 'через 2 часа' и т.д.
        user_id: Telegram ID пользователя
        session: Сессия БД (опционально)
    
    Returns:
        Строка с результатом проверки
    """
    try:
        if session is None:
            session = Session()
            close_session = True
        else:
            close_session = False
            
        # Получаем пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден"
            
        # Парсим время с помощью правильной функции
        from .utils import parse_time_to_datetime
        parsed_time_str = parse_time_to_datetime(reminder_time, user_id)
        
        if not parsed_time_str:
            if close_session:
                session.close()
            return f"Не удалось распознать время: {reminder_time}"
            
        # Конвертируем строку в datetime
        from datetime import datetime
        import pytz
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        parsed_time = datetime.strptime(parsed_time_str, "%Y-%m-%d %H:%M")
        parsed_time = user_tz.localize(parsed_time)
            
        # Проверяем конфликты
        conflicts = check_time_conflicts_sync(user.id, parsed_time, session)
        
        if close_session:
            session.close()
            
        if conflicts:
            conflict_msg, suggested_time = conflicts
            return f" КОНФЛИКТ ВРЕМЕНИ:\n{conflict_msg}\n\n ПРЕДЛАГАЮ: {suggested_time}"
        else:
            return " Время свободно, можно создавать задачу"
            
    except Exception as e:
        logger.error(f"Error in async check_time_conflicts: {e}")
        if session and 'close_session' in locals() and close_session:
            session.close()
        return f"Ошибка при проверке времени: {str(e)}"

async def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None, ignore_conflicts=False, is_recurring=False, recurrence_pattern=None, recurrence_interval=1, goal_title=None, created_by_agent_id=None):
    """Add a new task"""
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}, is_recurring={is_recurring} (type: {type(is_recurring)}), recurrence_pattern={recurrence_pattern}, recurrence_interval={recurrence_interval}")
    
    if user_id is None:
        logger.error("[ADD_TASK] ERROR: user_id is None! Cannot create task without user_id")
        return "ERROR: user_id is required but was None"
    
    # Валидация: название не может быть пустым
    if not title or not title.strip():
        logger.error("[ADD_TASK] ERROR: title is empty or whitespace only")
        return _t(user_id, 'task_title_empty')
    
    title = title.strip()

    # Единая нормализация title через централизованный нормализатор
    from .utils import normalize_task_title
    original_title = title
    title, _overflow = normalize_task_title(title, max_len=200)
    if not title:
        return 'Название задачи пустое после очистки.'
    if title != original_title:
        logger.info(f"[ADD_TASK] Title normalized: '{original_title[:80]}' -> '{title}'")
    # Overflow (остаток длинного названия) добавляем в description если пустое
    if _overflow and not description:
        description = _overflow[:500]

    # Агентские задачи: описание СОХРАНЯЕМ (AI объясняет зачем создал задачу)
    if created_by_agent_id and description:
        logger.info(f"[ADD_TASK] Agent task: keeping description ({len(description)} chars)")

    # Описание: максимум 500 символов, очищаем дубликаты title
    if description and len(description) > 500:
        description = description[:497] + "..."
    if description and title:
        _desc_norm = description.strip().lower()
        _title_norm = title.strip().lower()
        if _desc_norm == _title_norm or _desc_norm.startswith(_title_norm[:min(40, len(_title_norm))]):
            description = ''
            logger.info("[ADD_TASK] description cleared: duplicated title")

    if session is None:
        session = Session()
        close_session = True
        logger.info("[ADD_TASK] Created new session")
    else:
        close_session = False
        logger.info("[ADD_TASK] Using provided session")

    # Check if user exists
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if user_id is None:
            logger.error("[ADD_TASK] Cannot create user with None telegram_id")
            if close_session:
                session.close()
            return "ERROR: user_id cannot be None"
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()

    # ПРОВЕРКА ДУБЛИКАТОВ: если pending задача с таким же (или похожим) названием уже есть — не создаём
    existing_tasks = session.query(Task).filter(
        Task.user_id == user.id,
        Task.status == 'pending'
    ).all()
    _title_lc = title.lower().strip()
    _stop_t = {'для', 'или', 'что', 'как', 'это', 'при', 'через', 'the', 'and', 'for',
               'своем', 'своей', 'свои', 'свой', 'свою', 'данные', 'нужно', 'нашем'}
    _new_t_sig = {w for w in _title_lc.split() if len(w) > 3} - _stop_t
    # Ищем domain-идентификаторы: r/Community, URL-фрагменты, CamelCase-токены — они весят как 2 слова
    import re as _re_dup_id
    _ID_PAT = _re_dup_id.compile(r'\br/[A-Za-z][A-Za-z0-9_]+|[A-Z][a-z]+[A-Z][a-zA-Z]*|https?://\S+')
    _new_t_ids = set(m.lower() for m in _ID_PAT.findall(title))
    def _task_is_dup(t):
        _et = t.title.lower().strip()
        _et_orig = t.title
        if _et == _title_lc:
            return True
        # contains-check (одно вложено в другое)
        if _title_lc in _et or _et in _title_lc:
            return True
        # Проверяем совпадение domain-идентификаторов (r/SyntheticBiology, CamelCase-имена)
        _et_ids = set(m.lower() for m in _ID_PAT.findall(_et_orig))
        _common_ids = _new_t_ids & _et_ids
        _et_sig = {w for w in _et.split() if len(w) > 3} - _stop_t
        _common_words = _new_t_sig & _et_sig
        # Если есть совпадение идентификатора — достаточно 1 дополнительного общего слова
        if _common_ids and len(_common_words) >= 1:
            return True
        # Стандартная проверка: 3+ общих значимых слова
        return len(_common_words) >= 3
    existing = next((t for t in existing_tasks if _task_is_dup(t)), None)
    if existing:
        logger.warning(f"[ADD_TASK] Duplicate pending task found: '{existing.title}' (id={existing.id})")
        if close_session:
            session.close()
        return _t(user_id, 'task_duplicate', title=existing.title)

    # Защита от повторного создания отменённых задач (агентский цикл)
    if created_by_agent_id:
        import datetime as _dt_dup
        _cutoff_cancelled = _dt_dup.datetime.utcnow() - _dt_dup.timedelta(hours=48)
        _cancelled_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'cancelled',
            Task.source == 'agent',
            Task.created_at >= _cutoff_cancelled,
        ).all()
        _cancelled_dup = next((t for t in _cancelled_tasks if _task_is_dup(t)), None)
        if _cancelled_dup:
            logger.warning(f"[ADD_TASK] Blocked: similar task was cancelled recently: '{_cancelled_dup.title}' (id={_cancelled_dup.id})")
            if close_session:
                session.close()
            return f"⛔ Задача '{_cancelled_dup.title}' уже создавалась и была отменена. Выбери ДРУГОЙ подход."

        # Лимит: максимум 10 pending задач от агентов — не засоряем пользователю список
        _agent_pending_count = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'pending',
            Task.source == 'agent',
        ).count()
        if _agent_pending_count >= 10:
            logger.warning(f"[ADD_TASK] Agent pending limit reached: {_agent_pending_count} tasks for user {user.id}")
            if close_session:
                session.close()
            return "[INTERNAL] Лимит задач от агентов (10 pending). НЕ сообщай пользователю — переключись на другое действие."
    
    # Create new task — время обязательно для пользовательских задач,
    # но агентские задачи (created_by_agent_id) могут быть без времени (отслеживание прогресса)
    if not reminder_time and not created_by_agent_id:
        logger.warning(f"[ADD_TASK] No reminder_time provided for task '{title}'")
        if close_session:
            session.close()
        return _t(user_id, 'task_no_time')
    
    task = Task(user_id=user.id, title=title, description=encrypt_data(description))
    # Помечаем источник: задача создана агентом или пользователем
    if created_by_agent_id:
        task.source = 'agent'
        task.created_by_agent_id = created_by_agent_id
    if goal_title:
        try:
            from models import Goal
            from sqlalchemy import and_
            # Разбиваем на ключевые слова (>2 символов) и ищем все в названии
            keywords = [w for w in goal_title.split() if len(w) > 2]
            query = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status != 'deleted'
            )
            for kw in keywords:
                query = query.filter(Goal.title.ilike(f'%{kw}%'))
            goal = query.first()
            if goal:
                task.goal_id = goal.id
                logger.info(f"[ADD_TASK] Linked task to goal '{goal.title}' (id={goal.id})")
            else:
                logger.info(f"[ADD_TASK] Goal '{goal_title}' not found for user {user_id}")
        except Exception as e:
            logger.warning(f"[ADD_TASK] Error linking to goal: {e}")

    if reminder_time:
        try:
            # Check if reminder_time is already a datetime object
            if isinstance(reminder_time, datetime):
                logger.info(f"[ADD_TASK] reminder_time is already datetime: {reminder_time}")
                # Assume it's in user's timezone, convert to UTC
                user_tz = pytz.timezone('Europe/Moscow')
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        logging.warning(f"Unknown timezone {user.timezone}, using Europe/Moscow")
                        user_tz = pytz.timezone('Europe/Moscow')
                
                # If datetime has no timezone, assume it's in user's timezone
                if reminder_time.tzinfo is None:
                    reminder_time = user_tz.localize(reminder_time)
                
                task.reminder_time = reminder_time.astimezone(pytz.UTC)
                logger.info(f"[ADD_TASK] Used existing datetime: {reminder_time} -> UTC: {task.reminder_time}")
            else:
                # Parse string time
                # Get user timezone
                user_tz = pytz.timezone('Europe/Moscow')
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        logging.warning(f"Unknown timezone {user.timezone}, using Europe/Moscow")
                        user_tz = pytz.timezone('Europe/Moscow')

                # Use AI-powered flexible time parser
                from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
                
                current_time = datetime.now(user_tz)
                logger.info(f"[ADD_TASK] Parsing time '{reminder_time}' with AI, current: {current_time}")
                
                parsed_time = await parse_time_with_ai(reminder_time, current_time)
                
                # Fallback to simple parser if AI fails
                if not parsed_time:
                    logger.info("[ADD_TASK] AI parsing failed, trying simple fallback")
                    parsed_time = parse_time_simple_fallback(reminder_time, current_time)
                
                if parsed_time:
                    # Convert to UTC for storage
                    task.reminder_time = parsed_time.astimezone(pytz.UTC)
                    logger.info(f"[ADD_TASK] Time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
                else:
                    logger.warning(f"[ADD_TASK] Could not parse time '{reminder_time}'")
                    if close_session:
                        session.close()
                    return f" Не удалось распознать время '{reminder_time}'. Попробуй: 'завтра в 10:00', 'через 2 часа', '15:30'"
        except Exception as e:
            logging.warning(f"Error processing reminder_time '{reminder_time}' for task {title}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()
            if close_session:
                session.close()
            return f" Ошибка обработки времени '{reminder_time}': {e}. Попробуй: 'завтра в 10:00', 'через 2 часа', '15:30'"
        if due_date:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
                local_dt = datetime.strptime(due_date, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.due_date = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
    
    # Set recurring task fields
    logger.info(f"[ADD_TASK] About to set recurring fields: is_recurring={is_recurring} (type: {type(is_recurring)}), pattern={recurrence_pattern}, interval={recurrence_interval}")
    if is_recurring:
        # Handle string boolean values from AI
        if isinstance(is_recurring, str):
            task.is_recurring = is_recurring.lower() in ('true', '1', 'yes')
            logger.info(f"[ADD_TASK] Converted string '{is_recurring}' to boolean: {task.is_recurring}")
        else:
            task.is_recurring = bool(is_recurring)
            logger.info(f"[ADD_TASK] Used boolean value: {task.is_recurring}")
        
        if task.is_recurring:
            task.recurrence_pattern = recurrence_pattern
            task.recurrence_interval = int(recurrence_interval) if recurrence_interval else 1
            logger.info(f"[ADD_TASK] Set recurring task: pattern={recurrence_pattern}, interval={task.recurrence_interval}")
        else:
            logger.info(f"[ADD_TASK] is_recurring was '{is_recurring}' (falsy), task not marked as recurring")
    else:
        logger.info(f"[ADD_TASK] is_recurring is falsy: {is_recurring} (type: {type(is_recurring)})")
    
    # АВТОМАТИЧЕСКАЯ ПРОВЕРКА КОНФЛИКТОВ ВРЕМЕНИ
    # При конфликте — НЕ создаём задачу, возвращаем информацию для AI,
    # чтобы агент уточнил у пользователя через диалог
    if task.reminder_time and not ignore_conflicts:
        try:
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            local_parsed = _utc_to_local(task.reminder_time, user_tz)
            conflicts = check_time_conflicts_sync(user.id, local_parsed, session)
            if conflicts:
                conflict_msg, suggested_time_str = conflicts
                original_str = local_parsed.strftime('%H:%M')
                logger.info(f"[ADD_TASK] Time conflict at {original_str}. Suggested: {suggested_time_str}")
                # Пытаемся предложить другое время, но НЕ блокируем полностью.
                # При 2+ конфликтов подряд (ignore_conflicts) — просто создаём.
                if not ignore_conflicts:
                    if close_session:
                        session.close()
                    return (f"TIME_CONFLICT: На {original_str} уже запланировано:\n{conflict_msg}\n"
                            f"Ближайшее свободное время: {suggested_time_str}. "
                            f"Уточни у пользователя: создать на {suggested_time_str} или выбрать другое время?")
                else:
                    logger.info(f"[ADD_TASK] ignore_conflicts=True, creating despite conflict")
        except Exception as e:
            logger.warning(f"[ADD_TASK] Error checking time conflicts: {e}")

    session.add(task)

    # Generate recommendations
    try:
        logger.info(f"[ADD_TASK] Generating recommendations for task '{title}'")
        recommendations = generate_unified_recommendations('task_creation', title=title, description=description)
        logger.info(f"[ADD_TASK] Generated {len(recommendations) if recommendations else 0} recommendations")
        if recommendations:
            task.recommendations = json.dumps(recommendations, ensure_ascii=False)
            logger.info(f"[ADD_TASK] Saved recommendations to task: {task.recommendations}")
    except Exception as e:
        logging.warning(f"Could not generate recommendations for task {title}: {e}")
        import traceback
        traceback.print_exc()
        # НЕ делаем rollback — задача уже добавлена в сессию и должна быть сохранена

    session.commit()
    task_id = task.id
    logger.info(f"[ADD_TASK] Task '{title}' created successfully with ID {task_id}, reminder_time: {task.reminder_time}")

    # === Лог активности ===
    try:
        from models import AgentActivityLog as _AAL_at
        _at_log = _AAL_at(
            user_id=user.id,
            activity_type='task_added',
            title=f'Задача создана: {title}',
            content=description[:200] if description else None,
            status='completed',
            ref_id=task_id,
        )
        session.add(_at_log)
        session.commit()
    except Exception as _e:
        logger.warning(f"[ADD_TASK] Activity log failed: {_e}")

    # (trigger_premium_automation_realtime removed — was dead code: always returned skipped_no_analyzer)

    # Save to long-term memory for project context
    try:
        ltm = LongTermMemory(user.telegram_id)
        # Determine project based on task content
        project_name = "General Tasks"
        if any(keyword in title.lower() for keyword in ['ml', 'machine learning', 'python', 'нейрон', 'алгоритм', 'курс']):
            project_name = "ML Learning Journey"
        elif any(keyword in title.lower() for keyword in ['бег', 'спорт', 'фитнес']):
            project_name = "Fitness Goals"
        elif any(keyword in title.lower() for keyword in ['работа', 'проект', 'встреча']):
            project_name = "Work Projects"
        
        tasks = [title]
        insights = [f"Created task: {title}"]
        if description:
            insights.append(f"Description: {description}")
        
        ltm.save_project_context(project_name, tasks, insights)
        logger.info(f"[ADD_TASK] Saved task to long-term memory project: {project_name}")
    except Exception as e:
        logger.warning(f"Could not save to long-term memory: {e}")

    # Schedule reminder if specified
    if task.reminder_time:
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE:
                REMINDER_SERVICE.schedule_reminder(
                    task_id=task.id, reminder_time=task.reminder_time, user_id=user.telegram_id, task_title=task.title
                )
                logger.info(f"[ADD_TASK] Scheduled reminder for task {task.id} at {task.reminder_time}")
            else:
                logger.warning(f"[ADD_TASK] REMINDER_SERVICE not initialized, cannot schedule reminder for task {task.id}")
        except Exception as e:
            logging.warning(f"Could not schedule reminder for task {task_id}: {e}")

    # Update profile analytics
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()

    # Format result message
    lang = _get_lang(user_id)
    if task.reminder_time:
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        local_time = _utc_to_local(task.reminder_time, user_tz)
        time_str = local_time.strftime('%H:%M')
        date_str = local_time.strftime('%d.%m.%Y')
        result_msg = _t(user_id, 'task_created', title=title, time=f"{date_str} {time_str}")
    else:
        result_msg = _t(user_id, 'task_created_no_time', title=title)

    # Обновляем контекст диалога для последующих местоимений
    if user_id:
        context = get_user_context(user_id)
        context.update(action="add_task", task=task, result=result_msg)
        logger.info(f"[ADD_TASK] Updated dialog context with task '{task.title}'")

    # === Векторная память (fire-and-forget, не блокирует event loop) ===
    try:
        import asyncio as _aio_at
        from ai_integration.vector_memory import store_memory as _vmem_at
        _desc_at = f" {description.strip()[:100]}" if description and description.strip() else ""
        _meta_at = {'type': 'task', 'task_id': str(task_id)}
        if task.goal_id:
            _meta_at['goal_id'] = str(task.goal_id)
        _aio_at.get_running_loop().create_task(
            _vmem_at(user_id, f"Задача создана: «{title}».{_desc_at}".strip(), _meta_at)
        )
    except Exception as _e:
        logger.debug(f"[ADD_TASK] Vector memory skipped: {_e}")

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg

# set_recurring_task removed - feature not critical, required subscription

async def save_note(content: str, title: str = None, user_id: int = None, session=None) -> str:
    """Сохранить заметку (без напоминания/дедлайна).

    Args:
        content: Текст заметки
        title: Заголовок заметки (опционально)
        user_id: Telegram ID пользователя
        session: SQLAlchemy session
    """
    if not content or not content.strip():
        return "Текст заметки не может быть пустым."
    if user_id is None:
        return "ERROR: user_id is required"

    close_session = False
    if session is None:
        from config import Session as _Session
        session = _Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id)
            session.add(user)
            session.commit()

        from models import Note
        import datetime as _dt_sn

        _note_title = (title or content[:60]).strip()

        # --- Дедуп: проверяем похожий заголовок за последние 24ч ---
        _since_24h = _dt_sn.datetime.utcnow() - _dt_sn.timedelta(hours=24)
        _recent_notes = session.query(Note).filter(
            Note.user_id == user.id,
            Note.created_at >= _since_24h,
        ).all()

        # Простое сравнение: совпадение >60% слов в заголовке
        _title_words = set(_note_title.lower().split())
        for _rn in _recent_notes:
            _rn_words = set((_rn.title or '').lower().split())
            if _title_words and _rn_words:
                _overlap = len(_title_words & _rn_words) / max(len(_title_words), len(_rn_words))
                if _overlap > 0.6:
                    logger.info(f"[SAVE_NOTE] Dedup: similar note exists (id={_rn.id}, overlap={_overlap:.0%}): «{_rn.title}»")
                    return f"Похожая заметка уже есть: «{_rn.title}» — новая не создана."

        # --- Дневной лимит: макс 20 заметок/день ---
        _today_start = _dt_sn.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        _today_count = session.query(Note).filter(
            Note.user_id == user.id,
            Note.created_at >= _today_start,
        ).count()
        if _today_count >= 10:
            logger.info(f"[SAVE_NOTE] Daily limit reached: {_today_count} notes today")
            return "[INTERNAL] Лимит заметок (10/день) исчерпан. НЕ сообщай пользователю — используй add_task или create_post вместо заметки."

        note = Note(
            user_id=user.id,
            title=_note_title,
            content=content.strip(),
            source='chat',
        )
        session.add(note)
        session.commit()
        # === Векторная память (fire-and-forget, не блокирует event loop) ===
        try:
            import asyncio as _aio_sn
            from ai_integration.vector_memory import store_memory as _vmem_sn
            _aio_sn.get_running_loop().create_task(
                _vmem_sn(user_id, f"Заметка: «{note.title}». {content[:200]}", {'type': 'note', 'note_id': str(note.id)})
            )
        except Exception as _e:
            logger.debug(f"[SAVE_NOTE] Vector memory skipped: {_e}")
        _preview = content.strip()[:300]
        if len(content.strip()) > 300:
            _preview += '...'
        return f"Заметка сохранена: «{note.title}»\n\n{_preview}"
    except Exception as e:
        logger.warning(f"[SAVE_NOTE] Error: {e}")
        try:
            session.rollback()
        except Exception:
            pass
        return "Не удалось сохранить заметку."
    finally:
        if close_session:
            session.close()


async def complete_task(task_id=None, task_title=None, completion_note=None, user_id=None, session=None):
    """Mark task as completed

    Args:
        task_id: ID задачи
        task_title: Название задачи (если нет ID)
        completion_note: Заметка о результате выполнения
        user_id: ID пользователя
        session: Сессия БД
    """
    from models import User  # Явный импорт для избежания конфликтов области видимости
    logger.info(f"[COMPLETE_TASK] Called with task_id={task_id}, completion_note='{completion_note}', user_id={user_id}")
    
    # Преобразуем task_id в int если нужно
    task_id_int = None
    if task_id is not None:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            logger.warning(f"[COMPLETE_TASK] Invalid task_id format: {task_id}, ignoring")
    
    if user_id is None:
        logger.error("[COMPLETE_TASK] user_id is None")
        return "ERROR: user_id не может быть None"
    
    # МЯГКАЯ ПРОВЕРКА: Если нет task_id/task_title, попробуем найти последнюю активную задачу
    # Это позволит завершать задачи даже если AI не передал параметры
    if task_id_int is None and (task_title is None or task_title.strip() == ""):
        logger.warning("[COMPLETE_TASK] No task_id or task_title provided, will use fallback")
        # Не возвращаем ошибку - дадим шанс найти задачу автоматически ниже
    
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

    # СПЕЦИАЛЬНАЯ ОБРАБОТКА МЕСТОИМЕНИЙ - используем текущую задачу
    if task_title:
        from .task_context import extract_task_reference_from_message, get_user_current_task
        task_reference = extract_task_reference_from_message(task_title)
        if task_reference == "__CURRENT_TASK__":
            current_task = get_user_current_task(user)
            if current_task:
                logger.info(f"[COMPLETE_TASK] Using current task: '{current_task.title}' for pronoun '{task_title}'")
                task = current_task
                # Пропускаем обычный поиск
            else:
                logger.warning(f"[COMPLETE_TASK] No current task set for pronoun '{task_title}'")
                task = None
        else:
            task = None  # Будет найден через find_task_flexible
    else:
        task = None

    # Если задача не найдена через контекст, используем обычный поиск
    if task is None:
        # ПРИОРИТЕТ 0: Если передан task_id — ищем напрямую по ID
        if task_id_int is not None:
            task = session.query(Task).filter(
                Task.id == task_id_int,
                (Task.user_id == user.id) | (Task.delegated_to_username.ilike((user.username or "").replace('@', '')))
            ).first()
            if task:
                logger.info(f"[COMPLETE_TASK] Found task by ID: '{task.title}' (ID: {task.id})")
        
        # ПРИОРИТЕТ 1: Если task_title не указан, но у пользователя есть current_task_id - используем его!  
        if task is None and (not task_title or not task_title.strip()) and user.current_task_id:
            logger.info(f"[COMPLETE_TASK] Using user's current_task_id: {user.current_task_id}")
            task = session.query(Task).filter_by(id=user.current_task_id).first()
            if task:
                logger.info(f"[COMPLETE_TASK] Found current task: '{task.title}' (ID: {task.id})")
        
        # Если task_title не указан, завершаем последнюю активную задачу
        elif task is None and (not task_title or not task_title.strip()):
            logger.info("[COMPLETE_TASK] No task_title provided, completing the nearest active task")
            
            # Найти ближайшую по времени активную задачу пользователя
            from datetime import datetime as dt_import
            nearest_task = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status != "completed",
                Task.reminder_time != None
            ).order_by(Task.reminder_time.asc()).first()
            
            # Fallback на последнюю созданную если нет задач с временем
            recent_task = nearest_task or session.query(Task).filter(
                Task.user_id == user.id,
                Task.status != "completed"
            ).order_by(Task.created_at.desc()).first()
            
            if recent_task:
                task = recent_task
                logger.info(f"[COMPLETE_TASK] Completing most recent task: '{task.title}' (ID: {task.id})")
            else:
                if close_session:
                    session.close()
                return "Нет активных задач для завершения"
        else:
            # Если task_title указан, но нет task_id - проверяем current_task первым!
            if user.current_task_id:
                current_task = session.query(Task).filter_by(id=user.current_task_id).first()
                if current_task:
                    # Проверяем, подходит ли current_task под описание
                    title_lower = task_title.lower()
                    current_title_lower = current_task.title.lower()
                    # Простая проверка на релевантность
                    if any(word in current_title_lower for word in title_lower.split() if len(word) > 3):
                        task = current_task
                        logger.info(f"[COMPLETE_TASK] Matched current_task '{current_task.title}' with search '{task_title}'")
            
            # Если не подошла current_task, ищем через find_task_flexible
            if task is None:
                task = find_task_flexible(
                    session=session,
                    user=user,
                    task_id=task_id_int,
                    task_title=task_title,
                    include_completed=True,  # Include to check status
                    include_delegated=True
                )
    
    if not task:
        if close_session:
            session.close()
        return f"Хм, не нахожу задачу: {task_title or task_id}"

    if task:
        if task.status == "completed":
            if close_session:
                session.close()
            return f" Задача '{task.title}' уже закрыта ✔️"
        
        task.status = "completed"
        task.actual_completion_time = datetime.now(pytz.UTC)
        
        # Обновляем delegation_status если задача была делегирована
        if task.delegation_status and task.delegation_status not in ('completed', 'rejected'):
            old_ds = task.delegation_status
            task.delegation_status = 'completed'
            logger.info(f"[COMPLETE_TASK] Updated delegation_status {old_ds} → completed for task {task.id}")
        
        # Сохраняем заметку о результате выполнения
        if completion_note:
            task.completion_notes = encrypt_data(completion_note)
            logger.info(f"[COMPLETE_TASK] Saved completion note for task {task.id}")
        
        try:
            session.commit()
            logger.info(f"[COMPLETE_TASK] Task {task.id} status set to 'completed', committed to database")

            # === Лог активности ===
            try:
                from models import AgentActivityLog as _AAL_ct
                _ct_log = _AAL_ct(
                    user_id=user.id,
                    activity_type='task_completed',
                    title=f'Задача выполнена: {task.title}',
                    content=completion_note[:400] if completion_note else None,
                    status='completed',
                    ref_id=task.id,
                )
                session.add(_ct_log)
                session.commit()
            except Exception as _e:
                logger.warning(f"[COMPLETE_TASK] Activity log failed: {_e}")
            
            # Уведомляем пользователей о завершении задачи партнёром
            try:
                from ai_integration.premium_simple import save_partner_progress_notification
                
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile and profile.pending_premium_recommendations:
                    try:
                        recommendations = json.loads(profile.pending_premium_recommendations)
                        if isinstance(recommendations, list):
                            recommender_ids = set()
                            for rec in recommendations:
                                if rec.get('type') == 'task_created' and rec.get('premium_user_id'):
                                    recommender_ids.add(rec.get('premium_user_id'))
                            
                            for recommender_id in recommender_ids:
                                save_partner_progress_notification(
                                    session=session,
                                    premium_user_id=recommender_id,
                                    partner_username=user.username or f"User_{user.telegram_id}",
                                    partner_telegram_id=user.telegram_id,
                                    action_type='completed',
                                    task_title=task.title,
                                    original_goal=None
                                )
                                logger.info(f"[COMPLETE_TASK] Notified {recommender_id} about partner completing task")
                    except Exception as e:
                        logger.warning(f"[COMPLETE_TASK] Failed to notify about completion: {e}")
            except Exception as e:
                logger.warning(f"[COMPLETE_TASK] Failed notification: {e}")
                
        except Exception as e:
            logger.error(f"[COMPLETE_TASK] Commit failed: {e}")
            session.rollback()
            if close_session:
                session.close()
            return f"Ошибка при сохранении: {e}"

        # Отменяем все запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Отменяем напоминание
                reminder_job_id = f"reminder_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[COMPLETE_TASK] Cancelled reminder job for task {task.id}")
                
                # Отменяем повторное напоминание
                followup_job_id = f"followup_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(followup_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(followup_job_id)
                    logger.info(f"[COMPLETE_TASK] Cancelled followup reminder job for task {task.id}")
                
                # Отменяем проверку результата
                result_check_job_id = f"result_check_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[COMPLETE_TASK] Cancelled result check job for task {task.id}")
                
                # Отменяем чекпоинты задач
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task.id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[COMPLETE_TASK] Cancelled checkpoint job {checkpoint_type} for task {task.id}")
                
                # Отменяем чекпоинт 1/3
                checkpoint_1_3_job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[COMPLETE_TASK] Cancelled 1/3 checkpoint job for task {task.id}")
        except Exception as e:
            logger.warning(f"[COMPLETE_TASK] Could not cancel scheduled jobs for task {task.id}: {e}")

        # === Векторная память: фиксируем завершение задачи (fire-and-forget) ===
        try:
            _task_mem_text = f"Завершена задача: '{task.title}'"
            if completion_note:
                _task_mem_text += f". Результат: {completion_note[:150]}"
            if task.goal_id:
                from models import Goal as _GoalVM
                _g_vm = session.query(_GoalVM).filter_by(id=task.goal_id, user_id=user.id).first()
                if _g_vm:
                    _task_mem_text += f". Цель: {_g_vm.title}"
            import asyncio as _aio_vm
            _aio_vm.get_running_loop().create_task(
                __import__('ai_integration.vector_memory', fromlist=['store_memory']).store_memory(
                    user.telegram_id, _task_mem_text,
                    {'type': 'achievement', 'task_id': str(task.id)}
                )
            )
        except Exception as _vm_task_err:
            logger.debug(f"[COMPLETE_TASK] Vector memory store skipped: {_vm_task_err}")

        # КРИТИЧНО: всегда возвращаем маркер для запроса результата
        # AI должен ОБЯЗАТЕЛЬНО спросить о результате выполнения
        result = f"TASK_COMPLETED_ASK_RESULT:{task.title}"
        logger.info(f"[COMPLETE_TASK] Returning marker to request result: {result}")
        
        # Schedule result check - уточнение результата выполнения через 1 час
        result_check_time = datetime.now(pytz.UTC) + timedelta(hours=1)
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE:
                REMINDER_SERVICE.schedule_result_check(
                    task_id=task.id, result_check_time=result_check_time, user_id=user.telegram_id, task_title=task.title
                )
        except Exception as e:
            logging.warning(f"Could not schedule result check for task {task.id}: {e}")

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            completion_time = (
                datetime.now(pytz.UTC) - task.created_at.replace(tzinfo=pytz.UTC)
            ).total_seconds() / 60
            profile.completed_tasks = (profile.completed_tasks or 0) + 1
            profile.interaction_count = (profile.interaction_count or 0) + 1  # Увеличиваем счетчик взаимодействий
            prev_avg = profile.average_completion_time or 0
            if profile.completed_tasks > 0:
                profile.average_completion_time = (
                    (prev_avg * (profile.completed_tasks - 1)) + completion_time
                ) / profile.completed_tasks
            session.commit()

        # Автоматический пересчёт прогресса цели при завершении привязанной задачи
        if task.goal_id:
            try:
                goal = session.query(Goal).filter_by(id=task.goal_id, user_id=user.id).first()
                if goal and goal.status == 'active' and not goal.metric_target:
                    total = session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.goal_id == goal.id,
                        Task.status.notin_(['cancelled', 'deleted']),
                    ).count()
                    done = session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.goal_id == goal.id,
                        Task.status == 'completed',
                    ).count()
                    if total > 0:
                        pct = min(100, int(done / total * 100))
                        goal.progress_percentage = pct
                        if pct >= 100:
                            goal.status = 'completed'
                            goal.completed_at = datetime.now(pytz.UTC)
                        session.commit()
                        logger.info(f"[COMPLETE_TASK] Auto-updated goal '{goal.title}' progress: {pct}% ({done}/{total})")
            except Exception as _eg:
                logger.warning(f"[COMPLETE_TASK] Auto-goal-progress error: {_eg}")
                try:
                    session.rollback()
                except Exception:
                    pass
        
        # Возвращаем сообщение с флагом для AI чтобы спросил о результате
        result = f"TASK_COMPLETED_ASK_RESULT: Задача '{task.title}' завершена."

        # ЛОГИКА ДЕЛЕГИРОВАНИЯ: определяем кто выполнил задачу и кому отправлять отчет
        is_delegated_task = False
        delegator = None
        
        # Случай 1: Задача была делегирована МНЕ (я получил задачу от другого пользователя)
        # В этом случае task.delegated_by содержит ID делегатора
        if task.delegated_by and task.delegated_by != user.id and task.delegation_status == "accepted":
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            is_delegated_task = True
            logger.info(f"[COMPLETE_TASK] Task {task.id} was delegated TO user {user.username} BY {delegator.username if delegator else 'unknown'}")
        
        # Случай 2: Задача была делегирована МНОЙ (я поручил задачу другому пользователю)
        # В этом случае task.user_id == мой ID, task.delegated_to_username содержит исполнителя
        elif task.user_id == user.id and task.delegated_to_username and task.delegation_status == "accepted":
            # Это я делегатор, а выполняет кто-то другой
            # Этот случай обрабатывается отдельно - это не должно происходить здесь
            # т.к. complete_task вызывается от имени исполнителя, а не делегатора
            logger.warning(f"[COMPLETE_TASK] Task {task.id} delegated BY user {user.username}, but completed by same user - unusual case")
        
        # Отправляем отчет делегатору если задача была делегирована
        if is_delegated_task and delegator:
            try:
                from main import bot
                if bot:
                    # Запрашиваем у исполнителя результаты работы
                    result_request = (
                        f" Расскажи, как прошло с задачей:\n"
                        f"'{task.title}'\n\n"
                        f"Что сделал, какой результат, были ли сложности? "
                        f"@{delegator.username} ждёт отчёт."
                    )
                    await bot.send_message(chat_id=user.telegram_id, text=result_request)
                    logger.info(f"[COMPLETE_TASK] Requested completion results from user {user.username} for task {task.id}")
                    
                    # Сохраняем флаг что нужно отправить отчет делегатору после получения результатов
                    # Используем поле completion_notes для временного хранения ID делегатора
                    task.pending_delegator_report = delegator.telegram_id

                    # Обновляем счётчик кампании делегирования
                    if getattr(task, 'delegation_campaign_id', None):
                        try:
                            from models import DelegationCampaign
                            dc = session.query(DelegationCampaign).filter_by(id=task.delegation_campaign_id).first()
                            if dc:
                                dc.delegations_completed = (dc.delegations_completed or 0) + 1
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)

                    session.commit()
                    
                    # Обновляем сообщение для пользователя
                    result = f" Задача '{task.title}' закрыта! Расскажи как прошло — @{delegator.username} ждёт отчёт"
                    
            except Exception as e:
                logger.error(f"[COMPLETE_TASK] Failed to request completion results from executor: {e}")

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

async def skip_task(task_id=None, task_title=None, reason=None, user_id=None, session=None):
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return " Пользователь не найден"

    # Find task by ID or title
    if task_id:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike((user.username or "").replace('@', '')))
            )
            .first()
        )
    elif task_title:
        # Search by words in title (including delegated tasks)
        words = task_title.lower().split()
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(
            or_(
                and_(Task.user_id == user.id, Task.status != "completed", or_(*conditions)),
                and_(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.status != "completed",
                    or_(*conditions)
                )
            )
        ).first()
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        task.status = "skipped"
        if reason:
            from .memory import encrypt_data
            task.skipped_reason = encrypt_data(reason)
        session.commit()

        # Отменяем все запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Отменяем напоминание
                reminder_job_id = f"reminder_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[SKIP_TASK] Cancelled reminder job for task {task.id}")
                
                # Отменяем повторное напоминание
                followup_job_id = f"followup_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(followup_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(followup_job_id)
                    logger.info(f"[SKIP_TASK] Cancelled followup reminder job for task {task.id}")
                
                # Отменяем проверку результата
                result_check_job_id = f"result_check_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[SKIP_TASK] Cancelled result check job for task {task.id}")
                
                # Отменяем чекпоинты задач
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task.id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[SKIP_TASK] Cancelled checkpoint job {checkpoint_type} for task {task.id}")
                
                # Отменяем чекпоинт 1/3
                checkpoint_1_3_job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[SKIP_TASK] Cancelled 1/3 checkpoint job for task {task.id}")
        except Exception as e:
            logger.warning(f"[SKIP_TASK] Could not cancel scheduled jobs for task {task.id}: {e}")

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.skipped_tasks = (profile.skipped_tasks or 0) + 1
            session.commit()
        result = f"Ладно, '{task.title}' пропускаем"

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

async def restore_task(task_id=None, task_title=None, user_id=None, session=None):
    """
    Восстановить завершенную задачу обратно в активные

    Args:
        task_id: ID задачи для восстановления (опционально)
        task_title: Название задачи для поиска (опционально)
        user_id: ID пользователя в Telegram
        session: Сессия базы данных (опционально)

    Returns:
        Сообщение о результате восстановления задачи
    """
    logger.info(f"[RESTORE_TASK] Called with task_id={task_id}, task_title={task_title}, user_id={user_id}")
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

    # Find task by ID or title
    if task_id:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.status.in_(["completed", "skipped"]),  # Restore completed or skipped tasks
                or_(Task.user_id == user.id, Task.delegated_to_username.ilike((user.username or "").replace('@', '')))
            )
            .first()
        )
    elif task_title:
        # Search by words in title (including delegated tasks)
        words = task_title.lower().split()
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(
            or_(
                and_(Task.user_id == user.id, Task.status.in_(["completed", "skipped"]), or_(*conditions)),
                and_(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.status.in_(["completed", "skipped"]),
                    or_(*conditions)
                )
            )
        ).first()
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        task.status = "pending"
        task.actual_completion_time = None  # Reset completion time
        session.commit()

        # Update profile analytics (decrement completed tasks)
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.completed_tasks is not None and profile.completed_tasks > 0:
            profile.completed_tasks -= 1
            # Recalculate average if needed, but for simplicity, just decrement
            session.commit()

        result = f"'{task.title}' вернул в работу — снова в деле!"

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

async def reschedule_task(task_title=None, new_time=None, user_id=None, session=None):
    from models import User  # Явный импорт для избежания конфликтов области видимости
    logger.info(f"[RESCHEDULE_TASK] Called with task_title='{task_title}', new_time='{new_time}', user_id={user_id}")

    if user_id is None:
        logger.error("[RESCHEDULE_TASK] ERROR: user_id is None!")
        return "ERROR: user_id is required"
    
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

    # Find task by title using case-insensitive search
    if task_title:
        logger.info(f"[RESCHEDULE_TASK] Searching for task containing '{task_title}' for user {user.id}")
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА МЕСТОИМЕНИЙ - используем текущую задачу
        from .task_context import extract_task_reference_from_message, get_user_current_task
        task_reference = extract_task_reference_from_message(task_title)
        if task_reference == "__CURRENT_TASK__":
            current_task = get_user_current_task(user)
            if current_task:
                logger.info(f"[RESCHEDULE_TASK] Using current task: '{current_task.title}' for pronoun '{task_title}'")
                task = current_task
            else:
                logger.warning(f"[RESCHEDULE_TASK] No current task set for pronoun '{task_title}'")
                task = None
        else:
            # Используем общую функцию поиска
            from .task_search import find_task_flexible
            task = find_task_flexible(
                session=session,
                user=user,
                task_title=task_title,
                include_completed=False,
                include_delegated=True
            )
    else:
        # Если название не указано, пробуем взять текущую задачу или последнюю активную
        logger.info("[RESCHEDULE_TASK] No task_title provided, looking for current/last active task")
        from .task_context import get_user_current_task
        from models import Task
        
        # Сначала пробуем текущую задачу
        task = get_user_current_task(user)
        
        # Если текущей нет, берем последнюю активную (по reminder_time)
        if not task:
            logger.info("[RESCHEDULE_TASK] No current task, searching for last active task")
            task = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.notin_(['completed', 'cancelled', 'deleted'])
            ).order_by(Task.reminder_time.asc()).first()
            
            if task:
                logger.info(f"[RESCHEDULE_TASK] Found last active task: '{task.title}'")
            else:
                logger.info("[RESCHEDULE_TASK] No active tasks found")
        
        if not task:
            if close_session:
                session.close()
            return "Не найдено активных задач для переноса."

    if task:
        try:
            # Parse new time with AI (flexible!)
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            current_time = datetime.now(user_tz)
            logger.info(f"[RESCHEDULE_TASK] Parsing time '{new_time}', current time: {current_time}")
            
            # Use AI for flexible time parsing
            from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
            
            local_dt = None
            try:
                local_dt = await parse_time_with_ai(new_time, current_time)
            except Exception as e:
                logger.error(f"[RESCHEDULE_TASK] AI parsing error: {e}")
            
            # Fallback to simple HH:MM parsing if AI fails
            if not local_dt:
                logger.info("[RESCHEDULE_TASK] AI parsing failed, trying simple fallback...")
                try:
                    local_dt = parse_time_simple_fallback(new_time, current_time)
                except Exception as e:
                    logger.error(f"[RESCHEDULE_TASK] Simple fallback error: {e}")
            
            if not local_dt:
                logger.error(f"[RESCHEDULE_TASK] ❌ Cannot parse time format: '{new_time}'")
                if close_session:
                    session.close()
                return "Не могу понять формат времени. Попробуй указать точнее, например: 'завтра в 10:00', 'через 2 часа', '15:30'."

            # Convert to UTC for storage (local_dt already has timezone from parser)
            task.reminder_time = local_dt.astimezone(pytz.UTC)
            
            # КРИТИЧНО: Сбрасываем флаги отправки при переносе задачи
            task.reminder_sent = False
            task.followup_reminder_sent = False
            task.result_check_sent = False
            logger.info(f"[RESCHEDULE_TASK] Reset all reminder flags for task {task.id}")
            
            session.commit()
            logger.info(f"[RESCHEDULE_TASK] ✅ Task {task.id} updated, new time (UTC): {task.reminder_time}, local: {local_dt}")

            # Отменяем старое напоминание и создаем новое
            try:
                from reminder_service import REMINDER_SERVICE
                if REMINDER_SERVICE and REMINDER_SERVICE.scheduler and REMINDER_SERVICE.scheduler.running:
                    # Сначала отменяем все связанные джобы
                    reminder_job_id = f"reminder_{task.id}"
                    if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                        logger.info(f"[RESCHEDULE_TASK] Cancelled old reminder job for task {task.id}")
                    
                    # Отменяем повторное напоминание
                    followup_job_id = f"followup_{task.id}"
                    if REMINDER_SERVICE.scheduler.get_job(followup_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(followup_job_id)
                        logger.info(f"[RESCHEDULE_TASK] Cancelled old followup reminder job for task {task.id}")
                    
                    # Отменяем проверку результата
                    result_check_job_id = f"result_check_{task.id}"
                    if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                        logger.info(f"[RESCHEDULE_TASK] Cancelled old result check job for task {task.id}")
                    
                    # Создаем новое напоминание (оно само создаст и followup)
                    REMINDER_SERVICE.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title
                    )
                    logger.info(f"[RESCHEDULE_TASK] ✅ New reminder scheduled for task {task.id} at {task.reminder_time}")
                else:
                    logger.warning(f"[RESCHEDULE_TASK] REMINDER_SERVICE not running, skipping reminder rescheduling (task time updated in DB)")
            except Exception as e:
                logger.error(f"[RESCHEDULE_TASK] Error rescheduling reminder: {e}")
                import traceback
                traceback.print_exc()

            result = f"Задача '{task.title}' перенесена на {local_dt.strftime('%d.%m.%Y %H:%M')}"

        except ValueError as e:
            logger.error(f"[RESCHEDULE_TASK] ValueError: {e}")
            result = f"Не разобрал время: {e}. Попробуй так: HH:MM или YYYY-MM-DD HH:MM"
        except Exception as e:
            logger.error(f"[RESCHEDULE_TASK] Unexpected error: {e}", exc_info=True)
            result = f"Не получилось перенести задачу — попробуй ещё раз"
    else:
        result = f"Хм, не нахожу '{task_title}'"

    if close_session:
        session.close()
    return result

async def delegate_task(
    title, reminder_time=None, delegated_to_username=None, user_id=None, description="", delegation_details=""
):
    from config import FREE_ACCESS_MODE
    
    # Validate input parameters
    if user_id is None:
        logger.error("[DELEGATE] user_id is None")
        return "ERROR: Пользователь не указан"
    
    if not title or title.strip() == "":
        logger.error("[DELEGATE] title is empty or None")
        return "ERROR: Название задачи не может быть пустым"
    
    session = Session()
    try:
        # Делегирование доступно всем (оплата токенами)
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."
        
        # Делегирование доступно всем пользователям (оплата токенами)
        logger.info(f"[DELEGATE] User {user_id} delegating task")

        # ── Получатель — суб-агент пользователя (UserAgent) ─────────────────────
        # Выполняем СИНХРОННО inline: результат возвращается в тот же tool-calling
        # цикл → ASI видит ответ агента и принимает решение (доработка / другой агент / ответ).
        try:
            from models import UserAgent as _UA_chk, AgentSubscription as _AS_chk
            import json as _jj
            import re as _ren
            import datetime as _dt_d
            from difflib import SequenceMatcher as _SM_del
            from .autonomous_agent import _exec_agent_for_director as _exec_dir
            from .autonomous_agent import _save_interaction_for_director as _save_ifd
            import json as _json_ag

            def _strip_structured_text(_raw: str, _max_len: int = 220) -> str:
                _t = (_raw or '').replace('\r\n', '\n').replace('\r', '\n').strip()
                if not _t:
                    return ''
                _stop_prefixes = (
                    'данные для работы', 'ключевые данные', 'детали:', 'описание:',
                    'задача:', 'шаги:', 'план:', 'цель:', 'итог:',
                )
                _lines = []
                for _ln in _t.split('\n'):
                    _s = _ln.strip()
                    if not _s:
                        continue
                    _s_l = _s.lower()
                    if any(_s_l.startswith(_p) for _p in _stop_prefixes):
                        break
                    # Заголовок формата "Раздел:" без окончания предложения
                    if _s.endswith(':') and len(_s) <= 70 and not _ren.search(r'[.!?]', _s[:-1]):
                        break
                    _lines.append(_s)
                    if len(' '.join(_lines)) >= _max_len:
                        break
                _out = ' '.join(_lines).strip()
                _out = _ren.sub(r'\s{2,}', ' ', _out)
                _out = _ren.sub(r'^\[автопилот\]\s*', '', _out, flags=_ren.IGNORECASE)
                return _out[:_max_len].strip(' ,;:.-')

            def _truncate_by_word(_txt: str, _limit: int) -> str:
                _txt = (_txt or '').strip()
                if len(_txt) <= _limit:
                    return _txt
                _cut = _txt[:_limit].rsplit(' ', 1)[0].strip()
                return (_cut or _txt[:_limit]).strip(' ,;:.-')

            def _live_assignment_text(_agent_name: str, _task_text: str) -> str:
                _STRUCT_HEADERS = (
                    'данные для работы', 'ключевые данные', 'детали', 'описание',
                    'шаги', 'план', 'задача', 'ожидание в отчёте', 'ожидание в отчете',
                    'каналы',
                )
                _task_lines = [ln.strip() for ln in (_task_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n') if ln.strip()]
                # Ищем первую строку, которая не является структурным заголовком
                _title_line = ''
                for _ln in _task_lines:
                    _ln_lc = _ln.lower().rstrip(' :')
                    if any(_ln_lc.startswith(h) for h in _STRUCT_HEADERS):
                        continue
                    if _ln.endswith(':') and len(_ln) < 80:
                        continue
                    _title_line = _ln
                    break
                _base = _title_line or _strip_structured_text(_task_text, _max_len=280)
                # Обрезаем если первая строка склеена со структурными секциями
                _base = _ren.split(
                    r'(?i)\b(?:на\s+основе\s+анализа|на\s+основе\s+rss|используй|детали|описание|данные\s+для\s+работы|ключевые\s+данные|нужно\s+найти|нужно\s+сделать)\b',
                    _base,
                    maxsplit=1,
                )[0].strip(' ,;:.-')
                if len(_base) < 18 and len(_task_lines) > 1:
                    _base = _strip_structured_text('\n'.join(_task_lines[:2]), _max_len=220)
                _base = _ren.sub(rf'^\s*{_ren.escape(_agent_name)}\s*,?\s*', '', _base, flags=_ren.IGNORECASE).strip(' ,;:.-')
                _is_fem = (_agent_name or '')[-1:] in 'аяАЯ'
                _generic = f'{_agent_name}, продолжи работу по текущей задаче.' if not _is_fem else f'{_agent_name}, продолжи работу по текущей задаче.'
                if not _base:
                    return _generic
                _base = _truncate_by_word(_base, 160)
                if _base and _base[:1].isupper() and not _base[:3].isupper():
                    _base = _base[:1].lower() + _base[1:]
                # Эвристика: глагол (инфинитив/императив) или существительное?
                _first_w = (_base.split()[0] if _base else '').lower().rstrip('.,;:')
                _is_verb = bool(_ren.match(
                    r'.+(ть|ться|чь|чься)$|.+(и|й|ись|йся|йте|ьте|ьтесь)$',
                    _first_w,
                )) and not _ren.match(r'.+(ость|ение|ание|ция|ство|ок|ка|ие|тель)$', _first_w)
                # Noun→imperative conversion
                _NOUN_IMP_DEL = {
                    'поиск': 'поищи', 'анализ': 'проанализируй', 'отправка': 'отправь',
                    'проверка': 'проверь', 'создание': 'создай', 'подготовка': 'подготовь',
                    'исследование': 'исследуй', 'публикация': 'опубликуй',
                    'обновление': 'обнови', 'написание': 'напиши', 'составление': 'составь',
                    'настройка': 'настрой', 'разработка': 'разработай', 'сбор': 'собери',
                    'обзор': 'сделай обзор', 'подбор': 'подбери', 'оценка': 'оцени',
                }
                if not _is_verb and _first_w in _NOUN_IMP_DEL:
                    _noun_imp = _NOUN_IMP_DEL[_first_w]
                    _rest = _base[len(_first_w):].lstrip()
                    _base = f'{_noun_imp} {_rest}' if _rest else _noun_imp
                    _is_verb = True
                if _is_verb:
                    # INF→IMP: конвертируем инфинитив в императив
                    _INF_IMP_DEL = {
                        'найти': 'найди', 'проверить': 'проверь', 'отправить': 'отправь',
                        'создать': 'создай', 'написать': 'напиши', 'собрать': 'собери',
                        'подготовить': 'подготовь', 'исследовать': 'исследуй',
                        'поискать': 'поищи', 'сделать': 'сделай', 'запустить': 'запусти',
                        'использовать': 'используй', 'опубликовать': 'опубликуй',
                        'обновить': 'обнови', 'связаться': 'свяжись',
                        'составить': 'составь', 'настроить': 'настрой', 'добавить': 'добавь',
                        'изучить': 'изучи', 'узнать': 'узнай', 'проанализировать': 'проанализируй',
                    }
                    if _first_w in _INF_IMP_DEL:
                        _base = _INF_IMP_DEL[_first_w] + _base[len(_first_w):]
                    _msg = f'{_agent_name}, {_base}.'
                else:
                    _msg = f'{_agent_name}, {_base}.' if len(_base) > 30 else f'{_agent_name}, есть задача — {_base}.'
                _sanitized = sanitize_live_team_chat_text(
                    _msg,
                    anchor_type='agent_delegation',
                    speaker_name='ASI',
                    target_name=_agent_name,
                )
                return _sanitized if _sanitized and len(_sanitized.strip()) >= 8 else _generic

            def _live_result_text(_agent_name: str, _result_text: str) -> str:
                _txt = (_result_text or '').strip()
                if not _txt:
                    _is_fem = (_agent_name or '')[-1:] in 'аяАЯ'
                    _fallback = 'Вот что я нашла: пока данных мало, продолжаю проверку.' if _is_fem else 'Вот что я нашел: пока данных мало, продолжаю проверку.'
                    return sanitize_live_team_chat_text(_fallback, anchor_type='agent_delegation', speaker_name=_agent_name)
                _txt = _ren.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', _txt)
                _txt = _ren.sub(r'\n\s*[•\-\*]\s*', '\n', _txt)
                _txt = _ren.sub(r'\n\s*\d+[.)\]]\s*', '\n', _txt)
                _txt = _ren.sub(r'\n{2,}', '\n', _txt)
                _txt = _strip_structured_text(_txt, _max_len=700)
                _sent = [s.strip() for s in _ren.split(r'(?<=[.!?])\s+', _txt) if s.strip()]
                _txt = ' '.join(_sent[:4]).strip() if _sent else _txt
                _txt = _truncate_by_word(_txt, 520)
                _txt_l = _txt.lower()
                _is_fem = (_agent_name or '')[-1:] in 'аяАЯ'
                _prefix = 'Вот что я нашла: ' if _is_fem else 'Вот что я нашел: '
                if not _txt_l.startswith(('вот что', 'нашла', 'нашел', 'проверила', 'проверил', 'сделала', 'сделал', 'нашли')):
                    _txt = _prefix + (_txt[:1].lower() + _txt[1:] if _txt and _txt[:1].isupper() and not _txt[:3].isupper() else _txt)
                return sanitize_live_team_chat_text(_txt.strip(), anchor_type='agent_delegation', speaker_name=_agent_name)

            _subscribed_ids = [r[0] for r in session.query(_AS_chk.agent_id).filter(_AS_chk.user_id == delegator.id).all()]
            # Загружаем агентов: подписки ИЛИ собственные агенты пользователя
            from sqlalchemy import or_ as _or_d
            _agent_filter = [_UA_chk.status.in_(['active', 'paused', 'published'])]
            if _subscribed_ids:
                _agent_filter.append(_or_d(_UA_chk.id.in_(_subscribed_ids), _UA_chk.author_id == delegator.id))
            else:
                _agent_filter.append(_UA_chk.author_id == delegator.id)
            _all_agents = (
                session.query(_UA_chk)
                .filter(*_agent_filter)
                .all()
            )

            # ── Адаптивный выбор получателя, если имя не передано явно ───────────
            _delegated_raw = (delegated_to_username or '').strip()
            if not _delegated_raw:
                _request_text = ' '.join(filter(None, [title, description, delegation_details])).strip()
                _request_l = _request_text.lower()

                # 1) Прямая подсказка в тексте: DELEGATE[Имя], @имя, "для Имя"
                _direct_names = []
                _m_del = _ren.findall(r'DELEGATE\[([^\]]+)\]|@([A-Za-zА-Яа-я0-9_\-]+)|(?:для|to)\s+([A-Za-zА-Яа-я0-9_\-]{2,})', _request_text, flags=_ren.IGNORECASE)
                for _m in _m_del:
                    _nm = (_m[0] or _m[1] or _m[2] or '').strip()
                    if _nm:
                        _direct_names.append(_nm)

                _direct_names_norm = [n.replace('@', '').lower().strip() for n in _direct_names if n.strip()]
                _picked_agent = None

                if _direct_names_norm:
                    for _hint in _direct_names_norm:
                        for _ag in _all_agents:
                            _slug_ok = bool(_ag.slug and _hint in _ag.slug.lower())
                            _name_ok = bool(_ag.name and _hint in _ag.name.lower())
                            if _slug_ok or _name_ok:
                                _picked_agent = _ag
                                break
                        if _picked_agent:
                            break

                # 2) Если прямого имени нет — выбираем по смыслу запроса и интеграциям агента
                if not _picked_agent and _all_agents:
                    _domain_map = {
                        'email': ('email', 'gmail', 'imap', 'inbox', 'outreach', 'reply', 'letter', 'почт', 'письм', 'отправ'),
                        'rss': ('rss', 'news', 'trend', 'хабр', 'новост', 'стать', 'feed'),
                        'market': ('market', 'alpha vantage', 'finance', 'stock', 'crypto', 'рын', 'акц', 'котиров'),
                        'social': ('telegram', 'discord', 'post', 'канал', 'пост', 'публик'),
                        'code': ('github', 'repo', 'pull request', 'commit', 'код', 'разработ', 'issue'),
                    }

                    def _domain_signals(_txt: str) -> set:
                        _res = set()
                        _t = (_txt or '').lower()
                        for _dn, _kws in _domain_map.items():
                            if any(_kw in _t for _kw in _kws):
                                _res.add(_dn)
                        return _res

                    _req_signals = _domain_signals(_request_l)
                    _req_tokens = {t for t in _ren.findall(r'[A-Za-zА-Яа-я0-9_]{3,}', _request_l)}
                    _recent_agent_load = {}
                    try:
                        from models import Interaction as _Int_del
                        _cutoff = _dt_d.datetime.now(_dt_d.timezone.utc) - _dt_d.timedelta(hours=8)
                        _recent_msgs = session.query(_Int_del.content).filter(
                            _Int_del.user_id == delegator.id,
                            _Int_del.message_type.in_(['proactive', 'agent_msg']),
                            _Int_del.created_at >= _cutoff,
                        ).order_by(_Int_del.created_at.desc()).limit(250).all()
                        _name_to_id = {(a.name or '').strip().lower(): a.id for a in _all_agents if a.name}
                        for (_cnt_raw,) in _recent_msgs:
                            try:
                                _jd = _jj.loads(_cnt_raw or '{}')
                                _ag_n = ((_jd.get('__agent') or {}).get('name') or '').strip().lower()
                                if _ag_n in _name_to_id:
                                    _aid = _name_to_id[_ag_n]
                                    _recent_agent_load[_aid] = _recent_agent_load.get(_aid, 0) + 1
                            except Exception:
                                continue
                    except Exception as _load_err:
                        logger.debug('[DELEGATE] recent load calc skipped: %s', _load_err)

                    _best_score = -1.0
                    for _ag in _all_agents:
                        _ag_text = ' '.join([
                            _ag.name or '',
                            _ag.slug or '',
                            _ag.job_title or '',
                            _ag.specialization or '',
                            _ag.description or '',
                            _ag.user_api_keys or '',
                            _ag.tools_allowed or '',
                            _ag.python_code or '',
                        ]).lower()
                        _ag_signals = _domain_signals(_ag_text)
                        _signal_overlap = len(_req_signals & _ag_signals)
                        _ag_tokens = {t for t in _ren.findall(r'[A-Za-zА-Яа-я0-9_]{3,}', _ag_text)}
                        _tok_overlap = len(_req_tokens & _ag_tokens)
                        _name_sim = _SM_del(None, _request_l[:120], (_ag.name or '').lower()).ratio()
                        _recent_load = _recent_agent_load.get(_ag.id, 0)
                        _load_penalty = min(1.4, _recent_load * 0.25)
                        _diversity_bonus = 0.4 if _recent_load == 0 else 0.0
                        _score = (_signal_overlap * 1.4) + min(2.0, _tok_overlap * 0.15) + (_name_sim * 0.6) + _diversity_bonus - _load_penalty
                        if _score > _best_score:
                            _best_score = _score
                            _picked_agent = _ag

                # 3) Финальный fallback: если агент один — выбираем его; иначе первого по score
                if not _picked_agent and len(_all_agents) == 1:
                    _picked_agent = _all_agents[0]

                if _picked_agent:
                    delegated_to_username = _picked_agent.name or _picked_agent.slug or ''
                    logger.info(
                        "[DELEGATE] adaptive recipient selected: %s (user=%s)",
                        delegated_to_username,
                        user_id,
                    )

            _recip_check = (delegated_to_username or '').replace("@", "").lower().strip()
            if not _recip_check:
                logger.error("[DELEGATE] delegated_to_username unresolved (user=%s)", user_id)
                return "ERROR: Получатель не указан"

            # Поддержка нескольких имён: "Кристина и Марк", "Кристина, Марк" → ['кристина', 'марк']
            _name_parts = [p.strip() for p in _ren.split(r'\s+и\s+|\s+and\s+|,\s*|;\s*', _recip_check) if p.strip() and len(p.strip()) > 1]
            if not _name_parts:
                _name_parts = [_recip_check]
            logger.info(f"[DELEGATE] Looking for agents: {_name_parts} (user_db_id={delegator.id})")

            _found_agents = []
            _used_ag_ids: set = set()
            for _np in _name_parts:
                for _ag in _all_agents:
                    if _ag.id in _used_ag_ids:
                        continue
                    _slug_match = _ag.slug and _np in _ag.slug.lower()
                    _name_match = _ag.name and _np in _ag.name.lower()
                    if _slug_match or _name_match:
                        _found_agents.append(_ag)
                        _used_ag_ids.add(_ag.id)
                        break
            logger.info(f"[DELEGATE] Found agents: {[a.name for a in _found_agents]}")

            if _found_agents:
                _agent_task_text = (f"{title}\n{description}".strip() if description else title)
                if delegation_details:
                    _agent_task_text += f"\n\nДетали: {delegation_details}"
                if reminder_time:
                    _agent_task_text += f"\n\nДедлайн: {reminder_time}"

                # ── Inline-выполнение каждого агента СИНХРОННО ────────────────────
                _results_parts = []
                for _agent_recipient in _found_agents:
                    _agent_name = _agent_recipient.name or 'Агент'
                    logger.info(f"[DELEGATE] Sub-agent inline: {_agent_name} (id={_agent_recipient.id})")
                    _tools_parsed = []
                    try:
                        _tools_parsed = _jj.loads(_agent_recipient.tools_allowed or '[]')
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    _agent_dict = {
                        'id': _agent_recipient.id,
                        'name': _agent_name,
                        'job_title': _agent_recipient.job_title or '',
                        'specialization': _agent_recipient.specialization or '',
                        'description': _agent_recipient.description or '',
                        'personality': _agent_recipient.personality or '',
                        'python_code': _agent_recipient.python_code or '',
                        'user_api_keys': _agent_recipient.user_api_keys or '',
                        'tools_allowed': _agent_recipient.tools_allowed or '',
                        'search_scope': _agent_recipient.search_scope or '',
                        'avatar_url': _agent_recipient.avatar_url or '',
                        'tools': _tools_parsed,
                    }

                    # Логируем передачу задачи агенту
                    _aal_delegation_id = None
                    try:
                        from models import AgentActivityLog as _AAL_d
                        _log = _AAL_d(
                            user_id=delegator.id,
                            activity_type='agent_task',
                            title=f'Поручено {_agent_name}: {title}',
                            content=description[:500] if description else None,
                            target=f'agent:{_agent_name}',
                            status='in_progress',
                            result=(f'Поручил {_agent_name}. Дедлайн: {reminder_time}'
                                    if reminder_time else f'Поручил {_agent_name}'),
                        )
                        session.add(_log)
                        session.commit()
                        _aal_delegation_id = _log.id
                    except Exception as _log_err:
                        logger.warning(f"[DELEGATE] activity log error: {_log_err}")
                        try:
                            session.rollback()
                        except Exception:
                            pass

                    # Агентские поручения — создаём Task с source='agent' для дашборда
                    # Dedup: не создаём если похожая задача уже есть за последние 4 часа
                    _agent_task_id = None
                    _skip_task_creation = False
                    try:
                        from datetime import timedelta as _td_dedup
                        _dedup_since = datetime.now(pytz.UTC) - _td_dedup(hours=4)
                        _existing_similar = session.query(Task).filter(
                            Task.user_id == delegator.id,
                            Task.delegated_to_username == _agent_name,
                            Task.source == 'agent',
                            Task.created_at >= _dedup_since,
                            Task.status.in_(['pending', 'in_progress']),
                        ).order_by(Task.id.desc()).first()
                        if _existing_similar:
                            # Сравниваем заголовки — если первые 50 символов совпадают → дубль
                            _existing_title = (_existing_similar.title or '').lower().strip()[:50]
                            _new_title = title.lower().strip()[:50]
                            if _existing_title == _new_title or (
                                len(_existing_title) > 10 and len(_new_title) > 10 and
                                _existing_title[:30] == _new_title[:30]
                            ):
                                _skip_task_creation = True
                                _agent_task_id = _existing_similar.id
                                logger.info(f"[DELEGATE] Dedup: skipping task creation for {_agent_name}, existing #{_existing_similar.id}")
                    except Exception as _dedup_err:
                        logger.debug(f"[DELEGATE] Dedup check error: {_dedup_err}")

                    if not _skip_task_creation:
                        try:
                            _norm_title, _norm_overflow = normalize_task_title(title, agent_name=_agent_name)
                            _norm_desc = description[:500] if description else _norm_overflow[:500]
                            _agent_task = Task(
                                user_id=delegator.id,
                                title=_norm_title,
                                description=encrypt_data(_norm_desc),
                                source='agent',
                                created_by_agent_id=_agent_recipient.id,
                                delegated_to_username=_agent_name,
                                status='in_progress',
                            )
                            if reminder_time:
                                try:
                                    _atz = pytz.timezone(delegator.timezone) if getattr(delegator, 'timezone', None) else pytz.timezone('Europe/Moscow')
                                    _adt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                                    _adt = _atz.localize(_adt)
                                    _agent_task.reminder_time = _adt.astimezone(pytz.UTC)
                                except (ValueError, Exception):
                                    pass
                            session.add(_agent_task)
                            session.commit()
                            _agent_task_id = _agent_task.id
                        except Exception as _atask_err:
                            logger.warning(f"[DELEGATE] agent task creation error: {_atask_err}")
                            try:
                                session.rollback()
                            except Exception:
                                pass

                    # Записываем обращение директора к агенту в чат (с метаданными ASI)
                    # Пропускаем если задача содержит внутренние инструкции (ОТВЕТЬ НА ВОПРОС и т.п.)
                    _skip_dir_msg = any(kw in _agent_task_text.upper() for kw in ['ОТВЕТЬ НА ВОПРОС', 'ПРОСТО ОТВЕТЬ'])
                    if not _skip_dir_msg:
                        try:
                            _live_assign = _live_assignment_text(_agent_name, _agent_task_text)
                            if not _live_assign or len(_live_assign.strip()) < 5:
                                logger.warning(
                                    "[DELEGATE] DIR message skipped — empty text after sanitize for %s", _agent_name
                                )
                            else:
                                _dir_json = _json_ag.dumps({
                                    '__agent': {
                                        'name': 'ASI',
                                        'id': 0,
                                        'avatar_url': '/static/asibiont.svg',
                                    },
                                    'text': _live_assign,
                                    '__to_agent': _agent_name,
                                    '__anchor_type': 'agent_delegation',
                                }, ensure_ascii=False)
                                _save_ifd(user_id, _dir_json)
                        except Exception as _dir_err:
                            logger.error(f"[DELEGATE] DIR message save failed: {_dir_err}")

                    # ── СИНХРОННОЕ выполнение агента (inline) ──────────────────────
                    import asyncio as _asyncio_dt
                    # Маркер [АВТОПИЛОТ] — чтобы агент получил полный toolset (email-инструменты и т.д.)
                    # Если задача связана с email/кампанией — добавляем маркер, даже без явного autopilot context
                    _AUTOPILOT_TASK_HINTS = (
                        'email', 'кампани', 'рассылк', 'аутрич', 'outreach',
                        'привлечен', 'автопилот', '[автопилот]',
                    )
                    _task_lc = _agent_task_text.lower()
                    if not _agent_task_text.startswith('[АВТОПИЛОТ]') and any(w in _task_lc for w in _AUTOPILOT_TASK_HINTS):
                        _agent_task_text = '[АВТОПИЛОТ] ' + _agent_task_text
                    try:
                        logger.info(f"[DELEGATE] Starting inline exec for {_agent_name}...")
                        _raw_result = await _asyncio_dt.wait_for(
                            _exec_dir(_agent_dict, _agent_task_text, user_id),
                            timeout=60
                        )
                        _result = _raw_result[0] if isinstance(_raw_result, (tuple, list)) else _raw_result
                        logger.info(f"[DELEGATE] Inline exec result for {_agent_name}: {len(_result or '')} chars")
                    except _asyncio_dt.TimeoutError:
                        logger.warning(f"[DELEGATE] agent exec timeout ({_agent_name}), 60s limit")
                        _result = f"Задача передана {_agent_name}, результат будет чуть позже."
                    except Exception as _exec_err:
                        logger.warning(f"[DELEGATE] agent exec error ({_agent_name}): {_exec_err}", exc_info=True)
                        _result = None

                    if not _result or not _result.strip():
                        _results_parts.append(f"[{_agent_name}]: не удалось выполнить задачу — нужна доработка")
                        # Помечаем задачу как отменённую (не оставляем в pending)
                        if _agent_task_id:
                            try:
                                _at_fail = session.query(Task).get(_agent_task_id)
                                if _at_fail and _at_fail.status == 'pending':
                                    _at_fail.status = 'cancelled'
                                    _at_fail.skipped_reason = 'agent_exec_failed'
                                    _at_fail.completion_notes = 'Агент не вернул результат'
                                    session.commit()
                            except Exception as _tf:
                                logger.debug("[DELEGATE] task cancel failed: %s", _tf)
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                        # Помечаем AAL как failed
                        if _aal_delegation_id:
                            try:
                                from sqlalchemy import text as _aal_fail_text
                                session.execute(_aal_fail_text(
                                    "UPDATE agent_activity_log SET status='failed', result='Агент не вернул результат' WHERE id=:id"
                                ), {'id': _aal_delegation_id})
                                session.commit()
                            except Exception as _af:
                                logger.debug("[DELEGATE] aal fail update: %s", _af)
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                        continue

                    # ── Критическая оценка результата (эвристика без лишнего LLM-вызова) ──
                    _needs_rework = False
                    _result_stripped = _result.strip()
                    if len(_result_stripped) < 40:
                        _needs_rework = True
                    elif _result_stripped.lower() in (
                        'задачу выполнил.', 'задачу выполнила.', 'данных нет.', 'результат будет чуть позже.',
                        'задачу принял.', 'принял в работу.', 'задачу приняла.',
                    ):
                        _needs_rework = True
                    elif _result_stripped.startswith('BLOCKED:'):
                        _needs_rework = True
                    elif not any(c.isalpha() for c in _result_stripped):
                        _needs_rework = True

                    # Проверка релевантности: ключевые слова задачи должны пересекаться с ответом
                    if not _needs_rework:
                        _task_words = set(w.lower() for w in _ren.findall(r'[а-яёa-z]{4,}', _agent_task_text.lower()))
                        _result_words = set(w.lower() for w in _ren.findall(r'[а-яёa-z]{4,}', _result_stripped.lower()))
                        _common = _task_words & _result_words
                        # Если менее 2 общих слов (4+ букв) — скорее всего галлюцинация
                        if len(_task_words) >= 3 and len(_common) < 2:
                            _needs_rework = True

                    if _needs_rework:
                        # Доработка — 1 попытка (без шума в чат)
                        _rework_task = (
                            f"ДОРАБОТКА: твой предыдущий ответ был недостаточно конкретным или не по теме.\n\n"
                            f"Задача: {_agent_task_text[:400]}\n\n"
                            f"Твой предыдущий ответ:\n{_result[:600]}\n\n"
                            f"Исправь: дай конкретный, развёрнутый ответ по существу задачи."
                        )
                        try:
                            _raw_result2 = await _exec_dir(_agent_dict, _rework_task, user_id)
                            _result2 = _raw_result2[0] if isinstance(_raw_result2, (tuple, list)) else _raw_result2
                            if _result2 and _result2.strip() and len(_result2.strip()) > len(_result.strip()):
                                _result = _result2
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)

                    # Очищаем DSML-теги из ответа
                    try:
                        from .utils import clean_technical_details as _ctd_r
                        _result = _ctd_r(_result).strip() or _result
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                    # Очищаем чрезмерное форматирование (bullet-списки, лишние пробелы)
                    _result = _ren.sub(r'\n{3,}', '\n\n', _result)  # не более 2 переносов подряд
                    _result = _ren.sub(r'^\s*[•\-\*]\s*', '', _result, flags=_ren.MULTILINE)  # убираем маркеры списков
                    _result = _live_result_text(_agent_name, _result)

                    # Записываем ответ агента в чат (видно на дашборде с аватаркой)
                    try:
                        if not _result or len(_result.strip()) < 5:
                            logger.warning(
                                "[DELEGATE] agent result skipped — empty text after sanitize for %s", _agent_name
                            )
                        else:
                            _av = _agent_dict.get('avatar_url', '')
                            _resp_json = _json_ag.dumps({
                                '__agent': {
                                    'name': _agent_name,
                                    'id': _agent_recipient.id,
                                    'avatar_url': _av,
                                },
                                'text': _result,
                                '__anchor_type': 'agent_delegation',
                            }, ensure_ascii=False)
                            _save_ifd(user_id, _resp_json)
                    except Exception as _resp_err:
                        logger.error(f"[DELEGATE] agent response save failed: {_resp_err}")

                    # Логируем завершение в AgentActivityLog
                    try:
                        session.add(_AAL_d(
                            user_id=delegator.id,
                            activity_type='agent_task',
                            title=f'{_agent_name}: выполнено',
                            content=_result[:500],
                            target=f'agent:{_agent_name}',
                            status='completed',
                            result=_result[:500] if _result else f'Задача выполнена агентом {_agent_name}',
                        ))
                        # Помечаем Task как выполненную
                        if _agent_task_id:
                            _at = session.query(Task).get(_agent_task_id)
                            if _at:
                                _at.status = 'completed'
                                _at.completion_notes = _result[:1000]
                                _at.actual_completion_time = datetime.now(timezone.utc)
                        session.commit()
                    except Exception:
                        try:
                            session.rollback()
                        except Exception:
                            pass

                    # Возвращаем КРАТКОЕ содержание — полный ответ уже показан через _save_ifd
                    _summary = _result[:200] + ('...' if len(_result) > 200 else '')
                    _results_parts.append(
                        f"[{_agent_name}] уже ответил пользователю в чате (ответ уже показан, НЕ ДУБЛИРУЙ его). "
                        f"Суть: {_summary}"
                    )
                    logger.info(f"[DELEGATE] {_agent_name} completed inline ({len(_result)} chars)")

                try:
                    session.close()
                except Exception:
                    pass

                # Возвращаем результат INLINE — ASI видит его и решает что делать дальше
                if not _results_parts:
                    return f"Агенты не смогли выполнить задачу «{title}»."
                return "\n\n".join(_results_parts)
        except Exception as _ua_err:
            logger.warning(f"[DELEGATE] sub-agent lookup error: {_ua_err}")

        # Validate reminder_time
        if not reminder_time:
            return "Для делегирования задачи требуется точная дата и время дедлайна. Пожалуйста, уточните: на какое точное время и дату поставить дедлайн? (Например: '2026-01-10 15:00' или 'завтра в 14:30')"

        # Validate reminder_time format
        if reminder_time:
            try:
                datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
            except ValueError:
                logger.info(f"[DELEGATE] Parsing relative time: {reminder_time}")
                parsed_time = parse_time_to_datetime(reminder_time, user_id)
                if parsed_time:
                    reminder_time = parsed_time
                    logger.info(f"[DELEGATE] Parsed to: {reminder_time}")
                else:
                    return f"Некорректный формат времени '{reminder_time}'. Укажите точное время в формате YYYY-MM-DD HH:MM (например: 2026-01-10 15:00)"

        # Find recipient by username
        recipient_username = delegated_to_username.replace("@", "").lower()
        recipient = session.query(User).filter(User.username.ilike(recipient_username)).first()

        if not recipient:
            return (
                f"Пользователь @{recipient_username} не найден в системе. "
                f"Убедитесь, что он зарегистрирован в боте, или укажите имя одного из ваших активных агентов."
            )

        # Check if recipient has blocked the delegator
        from models import UserProfile
        recipient_profile = session.query(UserProfile).filter_by(user_id=recipient.id).first()
        if recipient_profile and recipient_profile.blocked_contacts:
            try:
                import json
                blocked_list = json.loads(recipient_profile.blocked_contacts)
                if delegator.username and delegator.username.lower().replace('@', '') in [b.lower().replace('@', '') for b in blocked_list]:
                    # Notify delegator that recipient is not accepting tasks from them
                    try:
                        from main import bot
                        if bot:
                            import asyncio
                            message = f"@{recipient_username} не готов принимать задачи от вас. Задача '{title}' не была отправлена."
                            asyncio.create_task(bot.send_message(delegator.telegram_id, message))
                    except Exception as e:
                        logging.error(f"Failed to notify about blocked delegation: {e}")
                        import traceback
                        traceback.print_exc()
                        session.rollback()
                    
                    return f"@{recipient_username} не готов принимать задачи от вас. Попробуйте делегировать задачу другому пользователю."
            except (json.JSONDecodeError, Exception) as e:
                logging.error(f"Error checking blocked contacts: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()

        # If delegating to self, return error marker
        if recipient.id == delegator.id:
            return "Нельзя поручить задачу самому себе."

        # Create task with pending delegation status
        task = Task(
            user_id=recipient.id,  # Получатель задачи
            title=title,
            description=encrypt_data(description),
            delegated_by=delegator.id,  # Кто делегировал
            delegated_to_username=recipient_username,
            delegation_status="pending",
            delegation_details=delegation_details,
            status="pending",
        )

        if reminder_time:
            try:
                user_tz = pytz.timezone(recipient.timezone) if recipient.timezone else pytz.timezone('Europe/Moscow')
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass

        session.add(task)
        session.commit()
        task_id = task.id

        # Log agent activity
        try:
            from models import AgentActivityLog
            deadline_str = str(reminder_time) if reminder_time else ''
            log_entry = AgentActivityLog(
                user_id=delegator.id,
                activity_type='delegation',
                title=title,
                content=description[:500] if description else None,
                target=f'@{recipient_username}',
                status='pending',
                ref_id=task_id,
                result=f'Дедлайн: {deadline_str}' if deadline_str else None,
            )
            session.add(log_entry)
            session.commit()
        except Exception as log_err:
            logger.warning(f"[DELEGATE] Failed to log activity: {log_err}")
            try:
                session.rollback()
            except Exception:
                pass

        # Send notification to recipient
        try:
            from main import bot
            if bot:
                # Generate AI-powered personalized notification
                import asyncio
                asyncio.create_task(generate_delegation_notification_async(
                    delegator.username,
                    recipient_username,
                    title,
                    description,
                    reminder_time,
                    delegation_details,
                    recipient.telegram_id
                ))

        except Exception as e:
            logging.error(f"Failed to send delegation notification: {e}")

        # Schedule automatic monitoring for task execution (outside try block to ensure it runs)
        try:
            schedule_delegation_monitoring(
                task_id=task_id,
                delegator_id=delegator.telegram_id,
                recipient_id=recipient.telegram_id,
                deadline=task.reminder_time
            )
        except Exception as e:
            logging.error(f"Failed to schedule delegation monitoring: {e}")

        return f"Задача '{title}' успешно делегирована пользователю @{recipient_username}. Ожидается подтверждение от получателя."
    except Exception as e:
        logger.error(f"[DELEGATE] Unexpected error in delegate_task: {e}")
        if 'session' in locals():
            session.rollback()
        return f"ERROR: Произошла ошибка при делегировании задачи: {str(e)}"
    finally:
        if 'session' in locals():
            session.close()

def check_subscription_status(user_id=None):
    """Check subscription status"""
    from subscription_service import get_subscription_status
    from config import FREE_ACCESS_MODE

    try:
        if FREE_ACCESS_MODE:
            return "Режим бесплатного доступа активен. Подписка не требуется."

        status = get_subscription_status(user_id)
        if status:
            status_text = f"Статус подписки: {status['status']}\n"
            status_text += f"План: {status['plan']}\n"
            if status["start_date"]:
                status_text += f"Дата начала: {status['start_date'][:10]}\n"
            if status["end_date"]:
                status_text += f"Дата окончания: {status['end_date'][:10]}\n"
            status_text += f"Количество входов: {status['login_count']}"
            return status_text
        else:
            return "Подписка не найдена. Для использования сервиса требуется активная подписка."
    except Exception as e:
        return f"Ошибка проверки подписки: {str(e)}"

def accept_delegated_task(task_id=None, task_title=None, user_id=None):
    """Accept a delegated task - supports both task_id and task_title"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        # Find task by ID or title
        if task_id:
            try:
                task_id_int = int(task_id)
            except (ValueError, TypeError):
                return f"Некорректный ID задачи: {task_id}"

            # Find task delegated to ME
            task = (
                session.query(Task)
                .filter(
                    Task.id == task_id_int,
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.delegation_status == "pending",
                )
                .first()
            )
        elif task_title:
            # Search by words in title (including delegated tasks)
            words = task_title.lower().split()
            conditions = [Task.title.ilike(f"%{word}%") for word in words]
            task = session.query(Task).filter(
                Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                Task.delegation_status == "pending",
                or_(*conditions)
            ).first()
        else:
            return "Не указан ни task_id, ни task_title."

        if not task:
            return "Задача не найдена или уже обработана."

        # Сохраняем данные до коммита/rollback, чтобы избежать DetachedInstanceError
        task_title = task.title
        task_id = task.id
        task_reminder_time = task.reminder_time
        task_delegated_by = task.delegated_by

        # Update delegation status and task status
        task.delegation_status = "accepted"
        task.status = "in_progress"  # Задача теперь в работе

        # Обновляем счётчик кампании делегирования
        if getattr(task, 'delegation_campaign_id', None):
            try:
                from models import DelegationCampaign
                dc = session.query(DelegationCampaign).filter_by(id=task.delegation_campaign_id).first()
                if dc:
                    dc.delegations_accepted = (dc.delegations_accepted or 0) + 1
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        session.commit()

        # Schedule reminder
        if task_reminder_time:
            try:
                from reminder_service import REMINDER_SERVICE
                if REMINDER_SERVICE:
                    REMINDER_SERVICE.schedule_reminder(
                        task_id=task_id,
                        reminder_time=task_reminder_time,
                        user_id=user.telegram_id,
                        task_title=task_title,
                    )
            except Exception as e:
                logging.error(f"Failed to schedule reminder: {e}")
                import traceback
                traceback.print_exc()

        # Save username for notification before potential session issues
        user_username = user.username
        
        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task_delegated_by).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user_username} принял задачу: {task_title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")
            import traceback
            traceback.print_exc()

        # Update AgentActivityLog status to 'accepted'
        try:
            from models import AgentActivityLog
            log_entry = session.query(AgentActivityLog).filter_by(
                activity_type='delegation', ref_id=task_id
            ).first()
            if log_entry:
                log_entry.status = 'accepted'
                log_entry.result = (log_entry.result or '') + f' | Принято: @{user_username}'
                import datetime as _dt
                log_entry.updated_at = _dt.datetime.now(_dt.timezone.utc)
                session.commit()
            # Новая запись в хронологию делегатора
            _deleg_owner = session.query(User).filter_by(id=task_delegated_by).first()
            if _deleg_owner:
                _accept_log = AgentActivityLog(
                    user_id=_deleg_owner.id,
                    activity_type='delegation_accepted',
                    title=f'@{user_username} принял задачу: {task_title}',
                    status='completed',
                    ref_id=task_id,
                )
                session.add(_accept_log)
                session.commit()
        except Exception as log_err:
            logger.warning(f"[ACCEPT_DELEGATE] Failed to update activity log: {log_err}")

        return f"Вы приняли задачу '{task_title}'. Она добавлена в ваш список задач."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        session.close()

def reject_delegated_task(task_id=None, task_title=None, reason=None, user_id=None):
    """Reject a delegated task"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        # Find task by ID or title
        if task_id:
            try:
                task_id_int = int(task_id)
            except (ValueError, TypeError):
                return f"Некорректный ID задачи: {task_id}"

            # Find task delegated to ME
            task = (
                session.query(Task)
                .filter(
                    Task.id == task_id_int,
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.delegation_status == "pending",
                )
                .first()
            )
        elif task_title:
            # Search by words in title (including delegated tasks)
            words = task_title.lower().split()
            conditions = [Task.title.ilike(f"%{word}%") for word in words]
            task = session.query(Task).filter(
                Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                Task.delegation_status == "pending",
                or_(*conditions)
            ).first()
        else:
            return "Не указан ни task_id, ни task_title."

        if not task:
            return "Задача не найдена или уже обработана."

        # Сохраняем данные до коммита/rollback, чтобы избежать DetachedInstanceError
        task_title = task.title
        task_id = task.id
        task_delegated_by = task.delegated_by

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"

        # Обновляем счётчик кампании делегирования
        if getattr(task, 'delegation_campaign_id', None):
            try:
                from models import DelegationCampaign
                dc = session.query(DelegationCampaign).filter_by(id=task.delegation_campaign_id).first()
                if dc:
                    dc.delegations_rejected = (dc.delegations_rejected or 0) + 1
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        session.commit()

        # Отменяем все запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Отменяем напоминание
                reminder_job_id = f"reminder_{task_id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled reminder job for task {task_id}")
                
                # Отменяем проверку результата
                result_check_job_id = f"result_check_{task_id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled result check job for task {task_id}")
                
                # Отменяем чекпоинты задач
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task_id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[REJECT_DELEGATED_TASK] Cancelled checkpoint job {checkpoint_type} for task {task_id}")
                
                # Отменяем чекпоинт 1/3
                checkpoint_1_3_job_id = f"task_checkpoint_{task_id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled 1/3 checkpoint job for task {task_id}")
        except Exception as e:
            logger.warning(f"[REJECT_DELEGATED_TASK] Could not cancel scheduled jobs for task {task_id}: {e}")
            import traceback
            traceback.print_exc()

        # Save data for notification before closing session
        user_username = user.username
        
        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task_delegated_by).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user_username} отклонил задачу: {task_title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")
            import traceback
            traceback.print_exc()

        # Update AgentActivityLog status to 'rejected'
        try:
            from models import AgentActivityLog
            log_entry = session.query(AgentActivityLog).filter_by(
                activity_type='delegation', ref_id=task_id
            ).first()
            if log_entry:
                log_entry.status = 'rejected'
                log_entry.result = (log_entry.result or '') + f' | Отклонено: @{user_username}'
                import datetime as _dt
                log_entry.updated_at = _dt.datetime.now(_dt.timezone.utc)
                session.commit()
            # Новая запись в хронологию делегатора
            _deleg_owner = session.query(User).filter_by(id=task_delegated_by).first()
            if _deleg_owner:
                _reject_log = AgentActivityLog(
                    user_id=_deleg_owner.id,
                    activity_type='delegation_rejected',
                    title=f'@{user_username} отклонил задачу: {task_title}',
                    content=reason[:300] if reason else None,
                    status='completed',
                    ref_id=task_id,
                )
                session.add(_reject_log)
                session.commit()
        except Exception as log_err:
            logger.warning(f"[REJECT_DELEGATE] Failed to update activity log: {log_err}")

        return f"Вы отклонили задачу '{task_title}'."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        session.close()

def get_delegation_progress(user_id, session=None):
    """Получить отчет о статусе делегированных задач"""
    should_close = False
    if session is None:
        session = Session()
        should_close = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if should_close:
                session.close()
            return "Пользователь не найден"

        # Задачи, делегированные ОТ пользователя (кому он делегировал)
        delegated_by_user = session.query(Task).filter(
            Task.delegated_by == user.id
        ).order_by(Task.created_at.desc()).all()

        # Задачи, делегированные ПОЛЬЗОВАТЕЛЮ (кто делегировал ему)
        delegated_to_user = session.query(Task).filter(
            Task.delegated_to_username.ilike(user.username.replace('@', '') if user.username else ''),
            Task.delegation_status.isnot(None)
        ).order_by(Task.created_at.desc()).all()

        report = []

        if delegated_by_user:
            report.append(" ВАШИ ДЕЛЕГИРОВАННЫЕ ЗАДАЧИ:")
            for task in delegated_by_user[:10]:  # Ограничим 10 задачами
                status_emoji = {
                    None: "",
                    "pending": "",
                    "accepted": "",
                    "rejected": "",
                    "completed": ""
                }.get(task.delegation_status, "")

                status_text = {
                    None: "ожидает принятия",
                    "pending": "ожидает принятия",
                    "accepted": "принята в работу",
                    "rejected": "отклонена",
                    "completed": "завершена"
                }.get(task.delegation_status, "неизвестный статус")

                report.append(f"{status_emoji} '{task.title}' → @{task.delegated_to_username}")
                report.append(f"   Статус: {status_text}")

                if task.completion_notes:
                    report.append(f"   Результат: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   Дедлайн: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")  # Пустая строка между задачами

        if delegated_to_user:
            report.append(" ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ВАМ:")
            # Pre-fetch delegators (batch)
            _dt_delegator_ids = list({t.delegated_by for t in delegated_to_user[:10] if t.delegated_by})
            _dt_delegators = session.query(User).filter(User.id.in_(_dt_delegator_ids)).all()
            _dt_delegator_by_id = {u.id: u for u in _dt_delegators}
            for task in delegated_to_user[:10]:
                delegator = _dt_delegator_by_id.get(task.delegated_by)
                delegator_name = f"@{delegator.username}" if delegator and delegator.username else "неизвестный"

                status_emoji = {
                    "pending": "",
                    "accepted": "",
                    "rejected": "",
                    "completed": ""
                }.get(task.delegation_status, "")

                status_text = {
                    "pending": "ожидает вашего решения",
                    "accepted": "вы работаете над ней",
                    "rejected": "вы отклонили",
                    "completed": "завершена"
                }.get(task.delegation_status, "неизвестный статус")

                report.append(f"{status_emoji} '{task.title}' от {delegator_name}")
                report.append(f"   Статус: {status_text}")

                if task.completion_notes:
                    report.append(f"   Результат: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   Дедлайн: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")

        if not delegated_by_user and not delegated_to_user:
            report.append("У вас нет делегированных задач.")

        if should_close:
            session.close()

        return "DELEGATION_REPORT:\n" + "\n".join(report)

    except Exception as e:
        logger.error(f"Error getting delegation progress for user {user_id}: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        if should_close:
            session.close()
        return f"Ошибка при получении отчета о делегировании: {str(e)}"

async def cancel_delegation(task_id, user_id):
    """
    Отменить делегирование задачи и вернуть её инициатору

    Args:
        task_id: ID задачи, делегирование которой нужно отменить
        user_id: ID пользователя в Telegram (делегатор)

    Returns:
        Сообщение о результате отмены делегирования
    """
    """Cancel delegation of a task, returning it to the initiator"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        # Ищем задачу где текущий пользователь является делегатором
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"
        task = session.query(Task).filter_by(id=task_id_int, delegated_by=user.id).first()
        if not task:
            return "Задача не найдена или вы не являетесь делегатором этой задачи."

        if not task.delegated_to_username:
            return "Эта задача не делегирована."

        # Check if task is already completed
        if task.status == "completed":
            return "Нельзя отменить делегирование выполненной задачи."

        # Cancel delegation - возвращаем задачу делегатору
        task_title = task.title
        delegated_to = task.delegated_to_username
        
        task.user_id = user.id  # Возвращаем владение делегатору
        task.delegated_to_username = None
        task.delegation_status = None
        task.delegated_by = None
        task.delegation_details = None

        session.commit()

        return f"Делегирование задачи '{task_title}' для @{delegated_to} отменено. Задача возвращена в ваш список."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        return f"Ошибка при отмене делегирования: {str(e)}"
    finally:
        session.close()

async def edit_task(
        task_id=None,
        task_title=None,
        title=None,
        description=None,
        reminder_time=None,
        user_id=None,
        session=None):
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

    # Find task using flexible search with stemming
    from ai_integration.task_search import find_task_flexible
    
    task_id_int = None
    if task_id:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"
    
    task = find_task_flexible(
        session=session,
        user=user,
        task_id=task_id_int,
        task_title=task_title,
        include_completed=False,
        include_delegated=True
    )

    if task:
        # Check access rights
        has_access = False
        if task.user_id == user.id:
            has_access = True
        elif task.delegated_to_username:
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            if close_session:
                session.close()
            return "У вас нет прав на редактирование этой задачи."

        if title:
            task.title = title
        if description is not None:
            task.description = encrypt_data(description)
        if reminder_time:
            try:
                # Use AI-powered flexible time parser
                from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
                current_time = datetime.now(user_tz)
                logger.info(f"[EDIT_TASK] Parsing time '{reminder_time}' with AI, current: {current_time}")
                
                parsed_time = await parse_time_with_ai(reminder_time, current_time)
                
                # Fallback to simple parser if AI fails
                if not parsed_time:
                    logger.info("[EDIT_TASK] AI parsing failed, trying simple fallback")
                    parsed_time = parse_time_simple_fallback(reminder_time, current_time)
                
                if parsed_time:
                    task.reminder_time = parsed_time.astimezone(pytz.UTC)
                    # КРИТИЧНО: сбрасываем флаги при переносе, чтобы AnchorEngine создал новое напоминание
                    task.reminder_sent = False
                    task.followup_reminder_sent = False
                    task.result_check_sent = False
                    logger.info(f"[EDIT_TASK] Time updated: '{reminder_time}' -> {task.reminder_time} UTC, reminder flags reset")
                    
                    # КРИТИЧНО: удаляем pending overdue-якоря для этой задачи
                    try:
                        from models import Session as AnchorSession
                        anchor_session = Session() if close_session else session
                        from anchor_engine import Anchor
                        deleted_count = anchor_session.query(Anchor).filter(
                            Anchor.source == f'task:{task.id}',
                            Anchor.anchor_type.in_(['task_overdue', 'task_reminder', 'task_deadline_soon']),
                            Anchor.delivered_at.is_(None)
                        ).delete(synchronize_session='fetch')
                        if deleted_count:
                            anchor_session.commit()
                            logger.info(f"[EDIT_TASK] Deleted {deleted_count} pending overdue/reminder anchors for task {task.id}")
                    except Exception as e:
                        logger.warning(f"[EDIT_TASK] Could not clean up anchors: {e}")
                else:
                    if close_session:
                        session.close()
                    return f"Не могу понять формат времени '{reminder_time}'. Попробуй: 'завтра в 10:00', 'через 2 часа', '15:30'"
                
                # КРИТИЧНО: Перепланировать напоминание после изменения времени
                try:
                    from reminder_service import REMINDER_SERVICE
                    if REMINDER_SERVICE and task.reminder_time:
                        REMINDER_SERVICE.schedule_reminder(
                            task_id=task.id,
                            reminder_time=task.reminder_time,
                            user_id=user.telegram_id,
                            task_title=task.title
                        )
                        logger.info(f"[EDIT_TASK] Rescheduled reminder for task {task.id} to {task.reminder_time}")
                    else:
                        logger.warning(f"[EDIT_TASK] Cannot reschedule reminder: REMINDER_SERVICE={REMINDER_SERVICE}, reminder_time={task.reminder_time}")
                except Exception as e:
                    logger.error(f"[EDIT_TASK] Error rescheduling reminder for task {task.id}: {e}")
                    import traceback
                    traceback.print_exc()
                    session.rollback()
                    
            except Exception as e:
                logger.error(f"[EDIT_TASK] Error parsing time: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()
                if close_session:
                    session.close()
                return f"Ошибка при обработке времени: {e}"
        session.commit()
        # Включаем точное время в ответ, чтобы агент не угадывал
        if reminder_time and task.reminder_time:
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            local_new_time = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
            result = f"TASK_UPDATED: Задача '{task.title}' обновлена. Новое время напоминания: {local_new_time.strftime('%d.%m.%Y %H:%M')}."
        else:
            result = f"TASK_UPDATED: Задача '{task.title}' обновлена."
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

def list_tasks(user_id=None, session=None, include_completed=False, filter_type=None):
    """Return list of user's tasks in plain text format
    
    Args:
        user_id: Telegram ID пользователя
        session: Database session (опционально)
        include_completed: Если True, показывает только выполненные задачи. По умолчанию False (активные)
        filter_type: Тип фильтра: 'Автоматические' для worker задач (только премиум)
    """
    if user_id is None:
        logger.error("[LIST_TASKS] user_id is None")
        return "ERROR: user_id не может быть None"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "У вас пока нет задач"

        # Get user tasks or delegated tasks - ОПТИМИЗИРОВАННЫЙ ЗАПРОС
        # Используем отдельные запросы для лучшей производительности
        base_query = session.query(Task).filter(Task.user_id == user.id)
        
        # Для больших объемов данных ограничиваем количество загружаемых задач
        MAX_TASKS_TO_LOAD = 500  # Максимум задач для загрузки в память
        
        # Получаем задачи: если запрошены завершённые - загружаем все, иначе только активные
        if include_completed:
            active_tasks_query = base_query.order_by(Task.created_at.desc()).limit(MAX_TASKS_TO_LOAD)
        else:
            # Пользовательские задачи: исключаем завершённые
            active_tasks_query = base_query.filter(
                Task.status.notin_(['completed', 'cancelled', 'deleted']),
            ).limit(MAX_TASKS_TO_LOAD)
        
        # Получаем делегированные задачи отдельно
        if user.username and user.username.strip():
            delegated_query = session.query(Task).filter(
                Task.delegated_to_username.ilike((user.username or "").replace('@', ''))
            ).limit(MAX_TASKS_TO_LOAD // 2)  # Меньше лимит для делегированных
            delegated_tasks = delegated_query.all()
        else:
            delegated_tasks = []
        
        # Объединяем результаты
        my_active_tasks = active_tasks_query.all()
        all_active_tasks = my_active_tasks + delegated_tasks
        
        # Базовый список задач для дальнейшей обработки
        tasks = all_active_tasks

        # ФИЛЬТРАЦИЯ ЗАДАЧ
        if filter_type == "Автоматические":
            # Фильтруем только worker задачи (начинаются с "Worker:")
            tasks = [t for t in tasks if t.title and t.title.startswith("Worker:")]
            
            if not tasks:
                return "У вас нет автоматических задач. Создайте первую командой типа 'Мониторь золото каждый день'"

        if not tasks:
            return "У вас нет задач" if include_completed else "У вас нет активных задач. Добавьте первую задачу - просто напишите что нужно сделать!"

        # Format detailed list
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
        
        # Если запрошены выполненные задачи, показываем только их
        if include_completed:
            if not completed_tasks:
                return "У вас пока нет выполненных задач"
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            result = f"Выполненные задачи ({len(completed_tasks)}):\n\n"
            
            # Показываем последние 20 выполненных задач
            for task in completed_tasks[-20:]:
                completed_info = ""
                if task.actual_completion_time:
                    try:
                        completed_dt = task.actual_completion_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        completed_info = f" - выполнено {completed_dt.strftime('%d.%m.%Y %H:%M')}"
                    except Exception as e:
                        logger.warning(f"Failed to process completion time for task {task.id}: {e}")
                result += f" {task.title}{completed_info}\n"
            
            if len(completed_tasks) > 20:
                result += f"\n...всего {len(completed_tasks)} выполненных задач"
            
            return result.strip()
        user_username_lower = user.username.lower() if user.username else ""
        delegated_to_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() == user_username_lower
        ]
        my_tasks = [t for t in active_tasks if not t.delegated_to_username]

        # Determine user timezone
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        now = datetime.now(user_tz)

        # Count overdue tasks
        overdue_count = 0
        for task in active_tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_count += 1
                except Exception as e:
                    logger.warning(f"Failed to process reminder time for task {task.id}: {e}")
                    pass

        # Format brief response
        if not active_tasks:
            return "Нет активных задач. Что планируете?"

        # УМНАЯ ПАГИНАЦИЯ: при большом количестве задач показываем топ-20
        MAX_TASKS_IN_RESPONSE = 20
        
        # Приоритизируем: 1) просроченные, 2) сегодня, 3) завтра, 4) будущие
        priority_tasks = []
        today_tasks = []
        upcoming_tasks = []
        later_tasks = []
        
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        tomorrow_end = tomorrow_start + timedelta(days=1)
        
        no_time_tasks = []  # Задачи без времени — отдельная проблемная группа
        
        for task in my_tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        priority_tasks.append(task)  # Просроченные
                    elif today_start <= reminder_dt < tomorrow_start:
                        today_tasks.append(task)  # Сегодня
                    elif tomorrow_start <= reminder_dt < tomorrow_end:
                        upcoming_tasks.append(task)  # Завтра
                    else:
                        later_tasks.append(task)  # Позже
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error parsing reminder time: {e}")
                    later_tasks.append(task)
            else:
                no_time_tasks.append(task)  # Без времени — проблема!
        
        # Сортируем по времени внутри каждой группы
        priority_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        today_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        upcoming_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        
        # Объединяем: сначала важные, задачи без времени в конец
        sorted_tasks = priority_tasks + today_tasks + upcoming_tasks + later_tasks + no_time_tasks
        
        # КРИТИЧНО: Просроченные задачи показываем ВСЕГДА, независимо от лимита
        # Остальные задачи ограничиваем с учетом уже показанных просроченных
        max_other_tasks = MAX_TASKS_IN_RESPONSE - len(priority_tasks)
        other_tasks_to_show = (today_tasks + upcoming_tasks + later_tasks)[:max_other_tasks] if max_other_tasks > 0 else []
        
        # Итоговый список: ВСЕ просроченные + другие до лимита
        tasks_to_show = priority_tasks + other_tasks_to_show
        hidden_count = len(sorted_tasks) - len(tasks_to_show)

        # Правильный подсчёт: только личные незавершённые задачи
        result = f"У тебя {len(my_tasks)} {'задача' if len(my_tasks) == 1 else ('задачи' if 2 <= len(my_tasks) <= 4 else 'задач')}"
        if delegated_to_me:
            result += f" плюс {len(delegated_to_me)} делегированных"
        result += ". "

        def _task_label(t):
            """Title + short description if exists."""
            lbl = f"'{t.title}'"
            desc = getattr(t, 'description', None)
            if desc and desc.strip():
                lbl += f" ({desc.strip()[:80]})"
            return lbl

        # ФОРМАТИРОВАНИЕ В ПОВЕСТВОВАТЕЛЬНОМ СТИЛЕ
        if priority_tasks:
            result += f"Просроченные задачи: "
            for i, task in enumerate(priority_tasks):
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    delta = now - reminder_dt
                    days = delta.days
                    hours = delta.seconds // 3600
                    if days > 0:
                        delay_str = f"{days} дней {hours} часов" if hours else f"{days} дней"
                    else:
                        delay_str = f"{hours} часов"
                    result += f"{_task_label(task)} просрочена на {delay_str}"
                    if i < len(priority_tasks) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting priority task time: {e}")
                    result += _task_label(task)
                    if i < len(priority_tasks) - 1:
                        result += ", "
                    else:
                        result += ". "
        
        if today_tasks:
            result += f"Сегодня запланированы: "
            for i, task in enumerate(today_tasks[:5]):  # Ограничиваем до 5
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    time_str = reminder_dt.strftime("%H:%M")
                    result += f"{_task_label(task)} в {time_str}"
                    if i < len(today_tasks[:5]) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting today task time: {e}")
                    result += _task_label(task)
                    if i < len(today_tasks[:5]) - 1:
                        result += ", "
                    else:
                        result += ". "
        
        if upcoming_tasks and len(tasks_to_show) > len(priority_tasks) + len(today_tasks):
            result += f"Завтра: "
            for i, task in enumerate(upcoming_tasks[:3]):  # Ограничиваем до 3
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    time_str = reminder_dt.strftime("%H:%M")
                    result += f"{_task_label(task)} в {time_str}"
                    if i < len(upcoming_tasks[:3]) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting upcoming task time: {e}")
                    result += _task_label(task)
                    if i < len(upcoming_tasks[:3]) - 1:
                        result += ", "
                    else:
                        result += ". "
        
        # Остальные задачи
        remaining_later = [t for t in tasks_to_show if t in later_tasks][:3]  # Максимум 3
        if remaining_later:
            result += f"Позже запланированы: "
            for i, task in enumerate(remaining_later):
                try:
                    if task.reminder_time:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        time_str = reminder_dt.strftime("%d.%m в %H:%M")
                        result += f"{_task_label(task)} {time_str}"
                    else:
                        result += _task_label(task)
                    if i < len(remaining_later) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting later task time: {e}")
                    result += _task_label(task)
                    if i < len(remaining_later) - 1:
                        result += ", "
                    else:
                        result += ". "
        
        # Показываем задачи без времени — это проблема!
        if no_time_tasks:
            result += f" ЗАДАЧИ БЕЗ ВРЕМЕНИ (нужно установить напоминание!): "
            for i, task in enumerate(no_time_tasks):
                result += _task_label(task)
                if i < len(no_time_tasks) - 1:
                    result += ", "
                else:
                    result += ". "
        
        # Показываем сколько задач скрыто
        if hidden_count > 0:
            result += f"Всего у тебя {len(sorted_tasks)} задач, но я показал только самые важные. "
        
        # Show delegated tasks
        if delegated_to_me:
            result += "Делегированные тебе задачи: "
            for i, task in enumerate(delegated_to_me[:3]):  # Максимум 3
                delegator_info = "неизвестно"
                if task.delegated_by:
                    delegator = session.query(User).filter_by(id=task.delegated_by).first()
                    if delegator and delegator.username:
                        delegator_info = f"@{delegator.username}"
                
                delegation_status_text = ""
                if task.delegation_status == "pending":
                    delegation_status_text = " ожидает принятия"
                elif task.delegation_status == "accepted":
                    delegation_status_text = " принято"
                elif task.delegation_status == "rejected":
                    delegation_status_text = " отклонено"
                elif task.delegation_status == "agent_assigned":
                    delegation_status_text = " выполняет агент"
                elif task.delegation_status == "agent_completed":
                    delegation_status_text = " выполнено агентом"
                elif task.delegation_status == "needs_rework":
                    delegation_status_text = " требует доработки"
                
                result += f"'{task.title}' от {delegator_info}{delegation_status_text}"
                if i < len(delegated_to_me[:3]) - 1:
                    result += ", "
                else:
                    result += ". "

        # Brief recommendation
        if overdue_count > 0:
            result += f"У тебя {overdue_count} просроченных задач - стоит разобраться с ними."
        elif len(active_tasks) == 1:
            result += "Одна задача - отличный фокус на цели."
        elif len(active_tasks) > 5:
            result += "Много задач - лучше приоритизировать самые важные."

        # Краткая статистика завершённых за сегодня — AI знает прогресс дня
        if completed_tasks:
            today_completed = [t for t in completed_tasks if t.actual_completion_time and 
                         t.actual_completion_time.replace(tzinfo=pytz.UTC) >= today_start.astimezone(pytz.UTC)]
            if today_completed:
                last_titles = [t.title for t in today_completed[:3]]
                result += f" Завершено сегодня: {len(today_completed)} "
                result += f"({', '.join(last_titles)})."



        logger.info(f"[LIST_TASKS] Returning {len(active_tasks)} active tasks for user {user_id}")
        return result.strip()
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return "Ошибка получения списка задач"
    finally:
        if close_session:
            session.close()

# Function removed

# Cross-language RU↔EN city name synonyms for matching users across language variants
_CITY_ALIASES: dict = {
    'пермь': 'perm', 'perm': 'пермь',
    'москва': 'moscow', 'moscow': 'москва',
    'санкт-петербург': 'saint petersburg', 'saint petersburg': 'санкт-петербург',
    'санкт петербург': 'saint petersburg', 'питер': 'saint petersburg', 'спб': 'saint petersburg',
    'екатеринбург': 'yekaterinburg', 'yekaterinburg': 'екатеринбург', 'ekaterinburg': 'екатеринбург',
    'новосибирск': 'novosibirsk', 'novosibirsk': 'новосибирск',
    'казань': 'kazan', 'kazan': 'казань',
    'нижний новгород': 'nizhny novgorod', 'nizhny novgorod': 'нижний новгород',
    'уфа': 'ufa', 'ufa': 'уфа',
    'самара': 'samara', 'samara': 'самара',
    'омск': 'omsk', 'omsk': 'омск',
    'челябинск': 'chelyabinsk', 'chelyabinsk': 'челябинск',
    'ростов-на-дону': 'rostov-on-don', 'rostov-on-don': 'ростов-на-дону', 'rostov on don': 'ростов-на-дону',
    'красноярск': 'krasnoyarsk', 'krasnoyarsk': 'красноярск',
    'воронеж': 'voronezh', 'voronezh': 'воронеж',
    'волгоград': 'volgograd', 'volgograd': 'волгоград',
    'краснодар': 'krasnodar', 'krasnodar': 'краснодар',
    'саратов': 'saratov', 'saratov': 'саратов',
    'тюмень': 'tyumen', 'tyumen': 'тюмень',
    'тольятти': 'tolyatti', 'tolyatti': 'тольятти',
    'ижевск': 'izhevsk', 'izhevsk': 'ижевск',
    'барнаул': 'barnaul', 'barnaul': 'барнаул',
    'ульяновск': 'ulyanovsk', 'ulyanovsk': 'ульяновск',
    'хабаровск': 'khabarovsk', 'khabarovsk': 'хабаровск',
    'новокузнецк': 'novokuznetsk', 'novokuznetsk': 'новокузнецк',
    'оренбург': 'orenburg', 'orenburg': 'оренбург',
    'липецк': 'lipetsk', 'lipetsk': 'липецк',
    'пенза': 'penza', 'penza': 'пенза',
    'киров': 'kirov', 'kirov': 'киров',
    'чебоксары': 'cheboksary', 'cheboksary': 'чебоксары',
    'тула': 'tula', 'tula': 'тула',
    'калининград': 'kaliningrad', 'kaliningrad': 'калининград',
    'курск': 'kursk', 'kursk': 'курск',
    'брянск': 'bryansk', 'bryansk': 'брянск',
    'иркутск': 'irkutsk', 'irkutsk': 'иркутск',
    'магнитогорск': 'magnitogorsk', 'magnitogorsk': 'магнитогорск',
    'владивосток': 'vladivostok', 'vladivostok': 'владивосток',
    'нижний тагил': 'nizhny tagil', 'nizhny tagil': 'нижний тагил',
    'ярославль': 'yaroslavl', 'yaroslavl': 'ярославль',
    'астрахань': 'astrakhan', 'astrakhan': 'астрахань',
    'набережные челны': 'naberezhnye chelny', 'naberezhnye chelny': 'набережные челны',
    'томск': 'tomsk', 'tomsk': 'томск',
    'рязань': 'ryazan', 'ryazan': 'рязань',
    'балашиха': 'balashikha', 'balashikha': 'балашиха',
    'пермский край': 'perm krai', 'perm krai': 'пермский край',
}

import re as _re_city

def _clean_city_name(raw: str) -> str:
    """Strip common prefixes/suffixes from city name: 'г. Пермь' → 'пермь', 'Perm, Russia' → 'perm'"""
    s = raw.strip().lower()
    # Remove prefixes
    s = _re_city.sub(r'^(город\s+|г\.?\s*|city\s+of\s+)', '', s)
    # Remove suffixes
    s = _re_city.sub(r'[,;].*$', '', s).strip()
    return s

def get_partners_list(user_id=None, session=None):
    """Return list of all users with profiles (except self and those with existing delegation)"""
    logger.info(f"[PARTNERS] get_partners_list called for user_id: {user_id}")

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        # Fallback: может быть передан telegram_id вместо db pk
        user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        logger.warning(f"[PARTNERS] User not found for user_id: {user_id}")
        if close_session:
            session.close()
        return []

    logger.info(f"[PARTNERS] Found user: {user.id}, username: {user.username}")

    # Get list of users with existing delegation
    delegated_usernames = set()

    # Tasks delegated to me
    if user.username:
        delegated_to_me = (
            session.query(Task)
            .filter(
                Task.delegated_to_username.ilike((user.username or "").replace('@', '')), Task.delegation_status.in_(["pending", "accepted"])
            )
            .all()
        )
        # Pre-fetch delegated task owners (batch)
        _dtm_task_user_ids = list({t.user_id for t in delegated_to_me if t.user_id})
        _dtm_task_users = session.query(User).filter(User.id.in_(_dtm_task_user_ids)).all() if _dtm_task_user_ids else []
        _dtm_task_user_by_id = {u.id: u for u in _dtm_task_users}
        for task in delegated_to_me:
            delegated_user = _dtm_task_user_by_id.get(task.user_id)
            if delegated_user:
                delegated_usernames.add(delegated_user.username.lower() if delegated_user.username else "")
    else:
        delegated_to_me = []

    # Tasks I delegated
    delegated_by_me = (
        session.query(Task)
        .filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(["pending", "accepted"]),
        )
        .all()
    )
    for task in delegated_by_me:
        if task.delegated_to_username:
            delegated_usernames.add(task.delegated_to_username.replace("@", "").lower())

    # Get all profiles with filled data
    # Apply subscription-based filtering
    profile_query = (
            session.query(UserProfile)
            .join(User, UserProfile.user_id == User.id)
            .filter(
                UserProfile.user_id != user.id,
                (UserProfile.interests.isnot(None))
                | (UserProfile.skills.isnot(None))
                | (UserProfile.position.isnot(None))
                | (UserProfile.city.isnot(None))
                | (UserProfile.bio.isnot(None))
                | (UserProfile.languages.isnot(None)),
        )
    )
    
    # Примечание: PREMIUM пользователи видят всех
    # LIGHT/STANDARD могут видеть PREMIUM только при наличии совпадений (проверяется ниже)
    
    all_profiles = profile_query.limit(500).all()

    logger.info(f"[PARTNERS] Found {len(all_profiles)} profiles with data")

    # Get current user profile for comparison
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        if close_session:
            session.close()
        return []

    # Предзагружаем цели текущего пользователя и всех партнёров ДО цикла — избегаем N+1
    try:
        _user_goals_for_filter = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status.in_(['active', 'in_progress'])
        ).all()
    except Exception:
        _user_goals_for_filter = []

    try:
        _profile_ids = [p.user_id for p in all_profiles]
        _bulk_partner_goals = session.query(Goal).filter(
            Goal.user_id.in_(_profile_ids),
            Goal.status.in_(['active', 'in_progress'])
        ).all()
        # Индекс: user_id → list[Goal]
        _partner_goals_index: dict = {}
        for _bg in _bulk_partner_goals:
            _partner_goals_index.setdefault(_bg.user_id, []).append(_bg)
    except Exception:
        _partner_goals_index = {}

    # Filter only those with matches
    # Helper: get normalized field value or fallback to original (defined once, not inside the loop)
    def _norm(obj, field):
        return getattr(obj, f'{field}_normalized', None) or getattr(obj, field, None)

    # Stop-words (defined once outside the loop)
    _stop_words = {'в', 'и', 'с', 'на', 'по', 'для', 'от', 'к', 'о', 'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}

    # Предвычисляем данные текущего пользователя ОДИН РАЗ до цикла
    def _city_variants(obj):
        variants = set()
        for attr in ('city_normalized', 'city_normalized_ru', 'city'):
            v = (getattr(obj, attr, None) or '').strip().lower()
            if v:
                # Очищаем от типовых префиксов/суффиксов
                cleaned = _clean_city_name(v)
                if cleaned:
                    variants.add(cleaned)
                    alias = _CITY_ALIASES.get(cleaned)
                    if alias:
                        variants.add(alias)
                # Также пробуем сырое значение в алиасах
                alias_raw = _CITY_ALIASES.get(v)
                if alias_raw:
                    variants.add(v)
                    variants.add(alias_raw)
        return variants

    _u_skills = _norm(user_profile, 'skills')
    _u_interests = _norm(user_profile, 'interests')
    _u_goals = _norm(user_profile, 'goals')
    _u_company = _norm(user_profile, 'company')
    _u_cities = _city_variants(user_profile)

    # Константные множества для семантического расширения совпадений интересов
    _sport_keywords = {'спорт', 'бег', 'пробежка', 'йога', 'фитнес', 'тренировка', 'велоспорт', 'плавание',
                       'футбол', 'баскетбол', 'теннис', 'волейбол', 'хоккей', 'кроссфит', 'гимнастика',
                       'марафон', 'триатлон', 'бадминтон', 'сквош', 'гольф', 'бильярд', 'пилатес'}
    _business_keywords = {'бизнес', 'стартап', 'предпринимательство', 'инвестиции', 'маркетинг',
                          'продажи', 'финансы', 'управление', 'менеджмент', 'e-commerce'}

    # Предзагружаем все User-объекты для профилей ОДНИМ запросом (избегаем N+1 в цикле фильтрации)
    try:
        _profile_user_ids = [p.user_id for p in all_profiles]
        _bulk_users = session.query(User).filter(User.id.in_(_profile_user_ids)).all()
        _user_by_id: dict = {u.id: u for u in _bulk_users}
    except Exception:
        _user_by_id = {}

    partners = []
    for profile in all_profiles:
        profile_user = _user_by_id.get(profile.user_id) or session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user:
            continue

        # Skip users already in delegation (delegated_usernames set built above)
        if profile_user.username and profile_user.username.lower() in delegated_usernames:
            continue

        has_match = False
        match_reasons = []  # Для логирования причин совпадения

        # Check skills - улучшенная логика с частичным совпадением (cross-language via normalized)
        _p_skills = _norm(profile, 'skills')
        if _u_skills and _p_skills:
            user_skills = set(s.strip().lower() for s in _u_skills.replace(';', ',').split(","))
            profile_skills = set(s.strip().lower() for s in _p_skills.replace(';', ',').split(","))
            
            # Стоп-слова
            stop_words = _stop_words
            
            # Точное совпадение навыков
            if user_skills & profile_skills:
                has_match = True
                match_reasons.append(f"skills exact: {user_skills & profile_skills}")
            else:
                # Частичное совпадение - требуем минимум 2 значимых слова или одно специфичное
                for user_skill in user_skills:
                    user_words = set(w for w in user_skill.split() if w not in stop_words)
                    for profile_skill in profile_skills:
                        profile_words = set(w for w in profile_skill.split() if w not in stop_words)
                        # Совпадение минимум 2 слов
                        common_words = user_words & profile_words
                        if len(common_words) >= 2:
                            has_match = True
                            match_reasons.append(f"skills partial (2+ words): {user_skill} <-> {profile_skill}")
                            break
                        # Или одно специфичное слово длиной >= 5 символов (для навыков чуть меньше)
                        elif len(common_words) == 1:
                            word = list(common_words)[0]
                            if len(word) >= 5:
                                has_match = True
                                match_reasons.append(f"skills specific word: {word}")
                                break
                    if has_match:
                        break

        # Check interests - улучшенная логика с частичным совпадением (cross-language via normalized)
        _p_interests = _norm(profile, 'interests')
        if _u_interests and _p_interests:
            user_interests = set(i.strip().lower() for i in _u_interests.replace(';', ',').split(","))
            profile_interests = set(i.strip().lower() for i in _p_interests.replace(';', ',').split(","))
            
            # Стоп-слова которые игнорируем при частичном совпадении
            stop_words = _stop_words
            
            # Семантические группы для расширения совпадений (вынесены за пределы цикла как _sport_keywords / _business_keywords)
            sport_keywords = _sport_keywords
            business_keywords = _business_keywords
            
            # Точное совпадение интересов
            if user_interests & profile_interests:
                has_match = True
                match_reasons.append(f"interests exact: {user_interests & profile_interests}")
            else:
                # Проверка семантических групп
                user_has_sport = any(k in interest for interest in user_interests for k in sport_keywords)
                profile_has_sport = any(k in interest for interest in profile_interests for k in sport_keywords)
                user_has_business = any(k in interest for interest in user_interests for k in business_keywords)
                profile_has_business = any(k in interest for interest in profile_interests for k in business_keywords)
                
                if (user_has_sport and profile_has_sport):
                    has_match = True
                    match_reasons.append("interests semantic: sport")
                elif (user_has_business and profile_has_business):
                    has_match = True
                    match_reasons.append("interests semantic: business")
                
                # Проверка вхождения одного интереса в другой (например "спорт" в "пляжный спорт")
                if not has_match:
                    for user_interest in user_interests:
                        user_clean = user_interest.strip().lower()
                        # Пропускаем слишком короткие слова (менее 3 символов)
                        if len(user_clean) < 3:
                            continue
                        for profile_interest in profile_interests:
                            profile_clean = profile_interest.strip().lower()
                            # Проверяем вхождение как подстроки (спорт <-> пляжный спорт)
                            if user_clean in profile_clean or profile_clean in user_clean:
                                has_match = True
                                match_reasons.append(f"interests substring: '{user_clean}' <-> '{profile_clean}'")
                                break
                    if has_match:
                        break
                
                # Если еще не нашли, проверяем частичное совпадение по словам
                if not has_match:
                    for user_interest in user_interests:
                        user_words = set(w for w in user_interest.split() if w not in stop_words)
                        for profile_interest in profile_interests:
                            profile_words = set(w for w in profile_interest.split() if w not in stop_words)
                            # Совпадение минимум 2 слов
                            common_words = user_words & profile_words
                            if len(common_words) >= 2:
                                has_match = True
                                match_reasons.append(f"interests partial (2+ words): {user_interest} <-> {profile_interest}")
                                break
                            # Или одно специфичное слово длиной >= 5 символов
                            elif len(common_words) == 1:
                                word = list(common_words)[0]
                                if len(word) >= 5:
                                    has_match = True
                                    match_reasons.append(f"interests specific word: {word}")
                                    break
                        if has_match:
                            break

        # Check current_plans for interest matches (cross-language via normalized)
        _u_interests2 = _norm(user_profile, 'interests')
        _p_plans = _norm(profile, 'current_plans')
        if _u_interests2 and _p_plans:
            user_interests = set(i.strip().lower() for i in _u_interests2.replace(';', ',').split(","))
            for interest in user_interests:
                interest_words = interest.strip().lower().split()
                if any(word in _p_plans.lower() for word in interest_words):
                    has_match = True
                    match_reasons.append(f"current_plans: {interest}")
                    break

        # Check goals (text from UserProfile, cross-language via normalized)
        _p_goals = _norm(profile, 'goals')
        if _u_goals and _p_goals:
            user_goals = set(g.strip().lower() for g in _u_goals.replace(';', ',').split(","))
            profile_goals = set(g.strip().lower() for g in _p_goals.replace(';', ',').split(","))
            if user_goals & profile_goals:
                has_match = True
                match_reasons.append(f"goals: {user_goals & profile_goals}")

        # Check structured Goals from Goal table
        if not has_match:
            try:
                user_goals_db = _user_goals_for_filter
                partner_goals_db = _partner_goals_index.get(profile.user_id, [])
                if user_goals_db and partner_goals_db:
                    # Match by category
                    user_goal_categories = set(g.category.lower().strip() for g in user_goals_db if g.category)
                    partner_goal_categories = set(g.category.lower().strip() for g in partner_goals_db if g.category)
                    common_categories = user_goal_categories & partner_goal_categories
                    if common_categories:
                        has_match = True
                        match_reasons.append(f"goal categories: {common_categories}")
                    # Match by title keywords (>= 4 chars)
                    if not has_match:
                        user_goal_words = set()
                        for g in user_goals_db:
                            if g.title:
                                user_goal_words.update(w.lower() for w in g.title.split() if len(w) >= 4)
                        partner_goal_words = set()
                        for g in partner_goals_db:
                            if g.title:
                                partner_goal_words.update(w.lower() for w in g.title.split() if len(w) >= 4)
                        common_goal_words = user_goal_words & partner_goal_words
                        if common_goal_words:
                            has_match = True
                            match_reasons.append(f"goal keywords: {common_goal_words}")
            except Exception as e:
                logger.debug(f"[PARTNERS] Goal table check error: {e}")

        # Check company (cross-language via normalized)
        _p_company = _norm(profile, 'company')
        if _u_company and _p_company:
            if _u_company.lower() == _p_company.lower():
                    has_match = True
                    match_reasons.append(f"company: {profile.company}")

        # Check city — одного города достаточно для показа в рекомендациях
        # _u_cities и _city_variants вынесены выше, до цикла
        _p_cities = _city_variants(profile)
        if _u_cities and _p_cities and (_u_cities & _p_cities):
            has_match = True
            match_reasons.append(f"city: {profile.city}")

        # ВАЖНО: Всегда показывать избранные и заблокированные контакты
        
        # Все пользователи видят всех (токенная модель, без тарифных ограничений)
        
        if user_profile.favorite_contacts:
            try:
                _fav_raw = json.loads(user_profile.favorite_contacts)
                favorite_usernames = [str(u).strip().lower().replace('@', '') for u in _fav_raw]
            except (json.JSONDecodeError, TypeError):
                favorite_usernames = [u.strip().lower().replace('@', '') for u in user_profile.favorite_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in favorite_usernames:
                has_match = True  # Принудительно показываем избранных
                match_reasons.append("favorite contact")
                
        if user_profile.blocked_contacts:
            try:
                _blk_raw = json.loads(user_profile.blocked_contacts)
                blocked_usernames = [str(u).strip().lower().replace('@', '') for u in _blk_raw]
            except (json.JSONDecodeError, TypeError):
                blocked_usernames = [u.strip().lower().replace('@', '') for u in user_profile.blocked_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in blocked_usernames:
                has_match = True  # Принудительно показываем заблокированных
                match_reasons.append("blocked contact")

        if has_match:
            logger.info(f"[PARTNERS] Match found: @{profile_user.username or profile_user.first_name or profile_user.id} - {', '.join(match_reasons)}")
            partners.append(profile)
        else:
            logger.debug(f"[PARTNERS] No match: @{profile_user.username or profile_user.first_name or profile_user.id}")

    logger.info(f"[PARTNERS] Total partners found: {len(partners)}")

# НОВАЯ ЛОГИКА СОРТИРОВКИ: способствовать росту пользователя через всю базу данных
    # Приоритет: (1) релевантность, (2) город (бонус, но не ограничение), (3) Premium, (4) рейтинг
    user_city = (user_profile.city_normalized or user_profile.city or '').lower() or None

    # Фетчим цели пользователя ОДИН РАЗ — не внутри sort_key
    try:
        _sort_user_goals = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.status.in_(['active', 'in_progress'])
        ).all()
        _sort_user_goal_cats = set(g.category.lower().strip() for g in _sort_user_goals if g.category)
    except Exception:
        _sort_user_goals = []
        _sort_user_goal_cats = set()

    # Предвычисляем данные текущего пользователя один раз для всех сортировок
    def _split_norm(obj, field):
        v = _norm(obj, field)
        if not v:
            return set()
        return set(x.strip().lower() for x in v.replace(';', ',').split(',') if x.strip())

    _u_sort_skills = _split_norm(user_profile, 'skills')
    _u_sort_interests = _split_norm(user_profile, 'interests')
    _u_sort_goals = _split_norm(user_profile, 'goals')

    def _city_variants_set(obj):
        vs = set()
        for attr in ('city_normalized', 'city_normalized_ru', 'city'):
            v = (getattr(obj, attr, None) or '').strip().lower()
            if v:
                cleaned = _clean_city_name(v)
                if cleaned:
                    vs.add(cleaned)
                    alias = _CITY_ALIASES.get(cleaned)
                if alias:
                    vs.add(alias)
        return vs

    _u_sort_cities = _city_variants_set(user_profile)

    # Предзагружаем цели всех партнёров ОДНИМ запросом вместо N запросов внутри sort_key
    _partners_goal_cats: dict = {}
    if _sort_user_goal_cats:
        try:
            _partner_ids = [p.user_id for p in partners]
            _all_partner_goals = session.query(Goal).filter(
                Goal.user_id.in_(_partner_ids),
                Goal.status.in_(['active', 'in_progress'])
            ).all()
            for _pg in _all_partner_goals:
                if _pg.category:
                    _partners_goal_cats.setdefault(_pg.user_id, set()).add(_pg.category.lower().strip())
        except Exception as _e:
            logger.debug(f"Failed to pre-fetch partner goal categories: {_e}")

    def sort_key(p):
        relevance_score = 0

        # Совпадения навыков (cross-language via normalized, user data pre-computed)
        p_skills = _split_norm(p, 'skills')
        if p_skills:
            relevance_score += len(_u_sort_skills & p_skills) * 3

        # Совпадения интересов (cross-language)
        p_interests = _split_norm(p, 'interests')
        if p_interests:
            relevance_score += len(_u_sort_interests & p_interests) * 2

        # Совпадения целей (cross-language)
        p_goals = _split_norm(p, 'goals')
        if p_goals:
            relevance_score += len(_u_sort_goals & p_goals) * 4

        # Бонус за совпадение структурированных целей (Goal table) — предзагружено выше
        if _sort_user_goal_cats and _partners_goal_cats:
            p_cats = _partners_goal_cats.get(p.user_id, set())
            relevance_score += len(_sort_user_goal_cats & p_cats) * 5

        # Бонус за тот же город (cross-language, user cities pre-computed)
        city_bonus = 1 if _u_sort_cities & _city_variants_set(p) else 0

        return (-relevance_score, -city_bonus, -(p.average_rating or 0))

    # Сортируем по новой логике
    partners.sort(key=sort_key)

    # Логируем результаты для анализа
    top_partners = partners[:5]  # Показываем топ-5 для логирования
    for i, p in enumerate(top_partners):
        partner_user = _user_by_id.get(p.user_id)
        if partner_user and partner_user.username and partner_user.username != 'None':
            logger.info(f"[PARTNERS] Top {i+1}: @{partner_user.username} (city: {p.city}, relevance: calculated in sort_key)")

    logger.info(f"[PARTNERS] Total partners after sorting: {len(partners)} (using full database for user growth)")
    
    # Получить текущие задачи пользователя для динамических рекомендаций
    user_tasks = session.query(Task).filter(
        Task.user_id == user.id,
        Task.status.in_(['active', 'pending', 'in_progress'])
    ).all()
    
    # Извлечь ключевые слова из задач пользователя
    user_task_keywords = set()
    
    # Словарь синонимов для лучшего сопоставления
    synonyms = {
        'пробежка': ['бег', 'бегать', 'пробежки', 'бега', 'running', 'jogging'],
        'йога': ['yoga', 'йоги', 'йогой'],
        'плавание': ['плавать', 'бассейн', 'плаванье', 'swimming'],
        'футбол': ['football', 'футболом', 'футбола'],
        'баскетбол': ['basketball', 'баскетболом'],
        'теннис': ['tennis', 'теннисом'],
        'велоспорт': ['велосипед', 'cycling', 'bike', 'велик'],
        'фитнес': ['fitness', 'тренажерный зал', 'тренажерка', 'gym'],
        'стартап': ['startup', 'бизнес', 'предпринимательство'],
        'инвестиции': ['invest', 'инвестировать', 'вложения'],
    }
    
    for task in user_tasks:
        if task.title:
            # Простая токенизация: разбиваем на слова, убираем короткие
            words = [w.lower().strip() for w in task.title.split() if len(w) > 3]
            user_task_keywords.update(words)
            
            # Добавляем синонимы
            for word in words:
                for key, syns in synonyms.items():
                    if key in word or any(syn in word for syn in syns):
                        user_task_keywords.update([key] + syns)
                        
        if task.description:
            words = [w.lower().strip() for w in task.description.split() if len(w) > 3]
            user_task_keywords.update(words)
    
    logger.info(f"[PARTNERS] User task keywords: {user_task_keywords}")
    
    # ENRICHMENT: Добавляем ключевые слова из LTM (weighted interests + search history)
    try:
        ltm_data = json.loads(user.long_term_memory) if user.long_term_memory else {}
        # LTM interests — берём топ-10 по весу
        ltm_interests = ltm_data.get('interests', {})
        if ltm_interests:
            top_interests = sorted(ltm_interests.items(), key=lambda x: x[1], reverse=True)[:10]
            for topic, weight in top_interests:
                if len(topic) >= 3 and weight >= 2:  # минимум 2 упоминания
                    user_task_keywords.add(topic.lower().strip())
            logger.info(f"[PARTNERS] Added LTM interests: {[t for t, w in top_interests if w >= 2]}")
        # Search history — последние 20 запросов, берём topics
        search_history = ltm_data.get('search_history', [])
        for entry in search_history[-20:]:
            topics = entry.get('topics', [])
            for topic in topics:
                if len(topic) >= 3:
                    user_task_keywords.add(topic.lower().strip())
            # Также слова из самого запроса
            query = entry.get('query', '')
            if query:
                q_words = [w.lower().strip() for w in query.split() if len(w) >= 4]
                user_task_keywords.update(q_words)
        if search_history:
            logger.info(f"[PARTNERS] Added {min(len(search_history), 20)} search history entries to keywords")
    except Exception as e:
        logger.debug(f"[PARTNERS] LTM enrichment error: {e}")
    
    # ENRICHMENT: Добавляем ключевые слова из структурированных целей (Goal table)
    # Используем _sort_user_goals, уже загруженный выше — без повторного запроса к БД
    try:
        for g in _sort_user_goals:
            if g.title:
                user_task_keywords.update(w.lower() for w in g.title.split() if len(w) >= 4)
            if g.category:
                user_task_keywords.add(g.category.lower().strip())
        if _sort_user_goals:
            logger.info(f"[PARTNERS] Added {len(_sort_user_goals)} goal keywords")
    except Exception as e:
        logger.debug(f"Failed to extract goal keywords: {e}")
    
    # Добавляем информацию об общих интересах, навыках, целях и задачах
    # Используем нормализованные поля чтобы EN/RU правильно совпадали
    def _norm_set(obj, field):
        val = getattr(obj, f'{field}_normalized', None) or getattr(obj, field, None)
        if not val:
            return set()
        return set(v.strip().lower() for v in val.replace(';', ',').split(',') if v.strip())

    user_interests = _norm_set(user_profile, 'interests')
    user_skills = _norm_set(user_profile, 'skills')
    user_goals = _norm_set(user_profile, 'goals')

    # Batch-load active tasks for all partners (avoid N+1 in task-keyword matching loop)
    _enrich_partner_uids = list({p.user_id for p in partners if p.user_id})
    _enrich_partner_tasks_all = session.query(Task).filter(
        Task.user_id.in_(_enrich_partner_uids),
        Task.status.in_(['active', 'pending', 'in_progress'])
    ).all() if _enrich_partner_uids else []
    _enrich_ptasks_by_uid: dict = {}
    for _ept in _enrich_partner_tasks_all:
        _enrich_ptasks_by_uid.setdefault(_ept.user_id, []).append(_ept)

    for partner in partners:
        # Common interests — cross-language via normalized
        partner_interests = _norm_set(partner, 'interests')
        if partner_interests:
            common = user_interests & partner_interests
            if not common:  # fallback: substring match
                for ui in user_interests:
                    for pi in partner_interests:
                        if ui and pi and len(ui) >= 3 and (ui in pi or pi in ui):
                            common.add(pi)
            partner.common_interests = ', '.join(sorted(common)) if common else None
        else:
            partner.common_interests = None

        # Common skills — cross-language via normalized
        partner_skills = _norm_set(partner, 'skills')
        if partner_skills:
            common_skills = user_skills & partner_skills
            if not common_skills:
                for us in user_skills:
                    for ps in partner_skills:
                        if us and ps and len(us) >= 3 and (us in ps or ps in us):
                            common_skills.add(ps)
            partner.common_skills = ', '.join(sorted(common_skills)) if common_skills else None
        else:
            partner.common_skills = None

        # Common goals — cross-language via normalized
        partner_goals = _norm_set(partner, 'goals')
        if partner_goals:
            common_goals = user_goals & partner_goals
            if not common_goals:
                for ug in user_goals:
                    for pg in partner_goals:
                        if ug and pg and len(ug) >= 3 and (ug in pg or pg in ug):
                            common_goals.add(pg)
            partner.common_goals = ', '.join(sorted(common_goals)) if common_goals else None
        else:
            partner.common_goals = None
        
        # НОВОЕ: Релевантность для текущих задач пользователя
        partner.task_relevance = None
        partner.task_relevance_score = 0
        
        if user_task_keywords:
            # Проверяем совпадение навыков партнера с задачами пользователя
            if partner.skills:
                partner_skill_words = set()
                for skill in partner.skills.split(','):
                    skill_words = [w.lower().strip() for w in skill.split() if len(w) > 3]
                    partner_skill_words.update(skill_words)
                
                # Находим пересечение ключевых слов задач с навыками партнера
                task_skill_match = user_task_keywords & partner_skill_words
                if task_skill_match:
                    partner.task_relevance = f"навыки для задач: {', '.join(list(task_skill_match)[:3])}"
                    partner.task_relevance_score += len(task_skill_match) * 3  # Высокий приоритет
                    logger.debug(f"[PARTNERS] user_id={partner.user_id} relevant for tasks: {task_skill_match}")
            
            # Проверяем совпадение интересов партнера с задачами
            if partner.interests:
                partner_interest_words = set()
                for interest in partner.interests.split(','):
                    interest_words = [w.lower().strip() for w in interest.split() if len(w) > 3]
                    partner_interest_words.update(interest_words)
                
                # Точное совпадение
                task_interest_match = user_task_keywords & partner_interest_words
                
                # Частичное совпадение (stemming-like)
                if not task_interest_match:
                    partial_matches = set()
                    for task_word in user_task_keywords:
                        for interest_word in partner_interest_words:
                            # Проверяем подстроку (минимум 4 символа)
                            if len(task_word) >= 4 and len(interest_word) >= 4:
                                if task_word[:4] in interest_word or interest_word[:4] in task_word:
                                    partial_matches.add(f"{task_word}~{interest_word}")
                    task_interest_match = partial_matches
                
                if task_interest_match and not partner.task_relevance:
                    matched_words = [m.split('~')[0] if '~' in m else m for m in list(task_interest_match)[:3]]
                    partner.task_relevance = f"интересы для задач: {', '.join(matched_words)}"
                    partner.task_relevance_score += len(task_interest_match) * 2
                    logger.debug(f"[PARTNERS] user_id={partner.user_id} task relevance: {task_interest_match}")
            
            # Проверяем совпадение задач партнера с задачами пользователя (схожие активности)
            partner_user = _user_by_id.get(partner.user_id)
            if partner_user:
                partner_tasks = _enrich_ptasks_by_uid.get(partner_user.id, [])
                
                partner_task_keywords = set()
                for task in partner_tasks:
                    if task.title:
                        words = [w.lower().strip() for w in task.title.split() if len(w) > 3]
                        partner_task_keywords.update(words)
                    if task.description:
                        desc_words = [w.lower().strip() for w in task.description.split() if len(w) > 4]
                        partner_task_keywords.update(desc_words)
                
                # Enrichment: LTM interests партнера расширяют его ключевые слова
                try:
                    p_ltm = json.loads(partner_user.long_term_memory) if partner_user.long_term_memory else {}
                    p_ltm_interests = p_ltm.get('interests', {})
                    for topic, weight in p_ltm_interests.items():
                        if weight >= 2 and len(topic) >= 3:
                            partner_task_keywords.add(topic.lower().strip())
                except Exception as e:
                    logger.debug(f"Failed to parse partner LTM interests: {e}")
                
                common_task_words = user_task_keywords & partner_task_keywords
                if common_task_words and not partner.task_relevance:
                    partner.task_relevance = f"похожие задачи: {', '.join(list(common_task_words)[:3])}"
                    partner.task_relevance_score += len(common_task_words) * 4  # Очень высокий приоритет
                    logger.info(f"[PARTNERS] @{partner_user.username} has similar tasks: {common_task_words}")
                
                # НОВОЕ: Проверяем точное совпадение названий активных задач
                if not partner.task_relevance:  # Если еще не нашли релевантность
                    user_active_task_titles = set()
                    for ut in user_tasks:
                        if ut.title and ut.status in ['active', 'pending', 'in_progress']:
                            # Нормализуем название: убираем лишние пробелы, приводим к нижнему регистру
                            normalized_title = ' '.join(ut.title.lower().split())
                            user_active_task_titles.add(normalized_title)
                    
                    partner_active_task_titles = set()
                    for pt in partner_tasks:
                        if pt.title and pt.status in ['active', 'pending', 'in_progress']:
                            normalized_title = ' '.join(pt.title.lower().split())
                            partner_active_task_titles.add(normalized_title)
                    
                    # Ищем точные совпадения названий задач
                    exact_task_matches = user_active_task_titles & partner_active_task_titles
                    if exact_task_matches:
                        partner.task_relevance = f"та же активная задача: {', '.join(list(exact_task_matches)[:2])}"
                        partner.task_relevance_score += 10  # Максимальный приоритет для точных совпадений
                        logger.info(f"[PARTNERS] @{partner_user.username} has exact same active tasks: {exact_task_matches}")
    
    # Пересортируем ВСЕХ партнеров: (1) релевантность, (2) город, (3) рейтинг
    def _same_city_sort(p):
        """Cross-language city match: сравниваем все варианты названий (EN/RU/raw)."""
        if not _u_cities:
            return False
        p_vars = {v for v in (
            (getattr(p, 'city', '') or '').strip().lower(),
            (getattr(p, 'city_normalized', '') or '').strip().lower(),
            (getattr(p, 'city_normalized_ru', '') or '').strip().lower(),
        ) if v}
        return bool(_u_cities & p_vars)

    partners.sort(key=lambda p: (
        -p.task_relevance_score,  # релевантность
        0 if _same_city_sort(p) else 1,  # город (EN/RU/raw all compared)
        -(p.average_rating or 0)  # рейтинг
    ))
    
    # Подсчитываем партнеров с релевантностью для задач
    relevant_count = sum(1 for p in partners if p.task_relevance_score > 0)
    not_relevant_count = len(partners) - relevant_count
    logger.info(f"[PARTNERS] Task-relevant partners: {relevant_count}, other: {not_relevant_count}")
    
    # Batch-load top-5 partner users for logging and common-goals/tasks enrichment
    _top5_uids = [p.user_id for p in partners[:5]]
    _top5_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_top5_uids)).all()}

    # Batch-load tasks: own user tasks + all top-5 partner tasks (avoid N+1)
    _own_tasks = session.query(Task).filter_by(user_id=user.id).all() if user.id else []
    _own_task_titles = set(t.title.lower().strip() for t in _own_tasks if t.title)
    _partner_tasks_all = session.query(Task).filter(Task.user_id.in_(_top5_uids)).all() if _top5_uids else []
    _partner_tasks_by_uid: dict = {}
    for _pt in _partner_tasks_all:
        _partner_tasks_by_uid.setdefault(_pt.user_id, []).append(_pt)

    for partner in partners[:5]:  # Log top 5
        partner_user = _top5_user_by_id.get(partner.user_id)
        if partner_user:
            if partner_user.username and partner_user.username != 'None':
                logger.info(f"[PARTNERS] Top partner: @{partner_user.username}, task_score={partner.task_relevance_score}, relevance={partner.task_relevance}")
        else:
            partner.common_skills = None
            
        # Common goals
        if partner.goals:
            partner_goals = set(g.strip().lower() for g in partner.goals.split(','))
            common_goals = user_goals & partner_goals
            partner.common_goals = ', '.join(common_goals) if common_goals else None
        else:
            partner.common_goals = None
            
        # Common tasks
        if partner.user_id:
            _p_tasks = _partner_tasks_by_uid.get(partner.user_id, [])
            partner_task_titles = set(t.title.lower().strip() for t in _p_tasks if t.title)
            common_task_titles = _own_task_titles & partner_task_titles
            partner.common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None
        else:
            partner.common_tasks = None

    try:
        if close_session:
            session.close()
    except Exception as e:
        logger.error(f"[PARTNERS] Error closing session in get_partners_list: {e}")

    return partners[:50]  # Увеличено с 20 до 50

def analyze_group_opportunities(user_id, session):
    """
    Анализирует задачи ВСЕХ пользователей и находит возможности для объединения:
    - Похожие задачи в близкое время
    - Общие интересы/активности
    - Конкретные предложения с @username и временем
    
    Returns:
        Строка с конкретным предложением присоединиться или None
    """
    from datetime import datetime, timedelta
    import pytz
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        return None
    
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        return None
    
    # Получаем текущее время пользователя
    base_now = datetime.now(pytz.UTC)
    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
    user_now = base_now.astimezone(user_tz)
    
    # Получаем ближайшие задачи других пользователей (следующие 48 часов)
    next_48h = user_now + timedelta(hours=48)
    
    # Ищем релевантных партнеров
    partners = get_partners_list(user.id, session)
    if not partners:
        return None
    
    # Анализируем их задачи
    partner_activities = []
    # Batch-load top-10 partner users to avoid N+1
    _ago_uids = [p.user_id for p in partners[:10]]
    _ago_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_ago_uids)).all()}
    # Batch-load partner tasks for next 48h (avoid N+1 per partner)
    _ago_tasks_all = session.query(Task).filter(
        Task.user_id.in_(_ago_uids),
        Task.status.in_(['pending', 'active', 'in_progress']),
        Task.reminder_time.isnot(None),
        Task.reminder_time >= base_now,
        Task.reminder_time <= base_now + timedelta(hours=48)
    ).order_by(Task.reminder_time.asc()).all()
    _ago_tasks_by_uid: dict = {}
    for _t in _ago_tasks_all:
        _ago_tasks_by_uid.setdefault(_t.user_id, []).append(_t)

    for partner in partners[:10]:  # Топ-10 партнеров
        partner_user = _ago_user_by_id.get(partner.user_id)
        if not partner_user or not partner_user.username:
            continue
        
        # Получаем активные задачи партнера (из batch-карты)
        partner_tasks = _ago_tasks_by_uid.get(partner_user.id, [])[:5]
        
        for task in partner_tasks:
            # Проверяем релевантность по интересам
            if profile.interests:
                user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                task_text = f"{task.title} {task.description or ''}".lower()
                
                # Ищем совпадения интересов в тексте задачи
                relevant = False
                matched_interest = None
                for interest in user_interests:
                    interest_words = interest.split()
                    if any(word in task_text for word in interest_words if len(word) >= 4):
                        relevant = True
                        matched_interest = interest
                        break
                
                if relevant:
                    # Форматируем время
                    task_time = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    time_str = task_time.strftime('%H:%M')
                    
                    # Определяем день
                    if task_time.date() == user_now.date():
                        day_str = "сегодня"
                    elif task_time.date() == (user_now + timedelta(days=1)).date():
                        day_str = "завтра"
                    else:
                        day_str = task_time.strftime('%d.%m')
                    
                    partner_activities.append({
                        'username': partner_user.username,
                        'activity': task.title,
                        'time': f"{day_str} в {time_str}",
                        'interest': matched_interest
                    })
    
    # Возвращаем первое найденное предложение
    if partner_activities:
        activity = partner_activities[0]
        return f" @{activity['username']} {activity['activity']} {activity['time']}. Присоединяйся?"
    
    # Если нет конкретных задач, анализируем goals
    if profile.goals:
        user_goals = set(g.strip().lower() for g in profile.goals.split(','))
        _g_pids = [p.user_id for p in partners[:5]]
        _g_profiles = {pp.user_id: pp for pp in session.query(UserProfile).filter(UserProfile.user_id.in_(_g_pids)).all()}
        _g_users = {u.id: u for u in session.query(User).filter(User.id.in_(_g_pids)).all()}
        for partner in partners[:5]:
            partner_profile = _g_profiles.get(partner.user_id)
            if partner_profile and partner_profile.goals:
                partner_user = _g_users.get(partner.user_id)
                if partner_user and partner_user.username:
                    partner_goals = set(g.strip().lower() for g in partner_profile.goals.split(','))
                    common_goals = user_goals & partner_goals
                    if common_goals:
                        goal = list(common_goals)[0]
                        return f" @{partner_user.username} тоже хочет '{goal}'. Можете объединиться!"
    
    # ГРУППОВОЙ АНАЛИЗ: Находим группы с похожими задачами/целями
    # Собираем все задачи всех пользователей за последние 7 дней
    week_ago = base_now - timedelta(days=7)
    all_recent_tasks = session.query(Task).filter(
        Task.status.in_(['pending', 'active', 'in_progress']),
        Task.created_at >= week_ago,
        Task.user_id != user.id  # Исключаем текущего пользователя
    ).all()
    
    # Динамически группируем задачи по общим значимым словам
    from collections import defaultdict
    
    # Стоп-слова для фильтрации
    stop_words = {'в', 'на', 'с', 'для', 'по', 'из', 'к', 'о', 'от', 'и', 'а', 'но', 'что', 'как', 'это', 
                  'все', 'еще', 'уже', 'только', 'так', 'здесь', 'там', 'тут', 'где', 'когда', 'мой', 'твой',
                  'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'my', 'your'}
    
    # Извлекаем значимые слова из задач
    word_to_tasks = defaultdict(list)
    # Batch-load all unique users from recent tasks (avoid N+1 per task)
    _art_uids = list({t.user_id for t in all_recent_tasks})
    _art_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_art_uids)).all()} if _art_uids else {}
    for task in all_recent_tasks:
        task_text = f"{task.title} {task.description or ''}".lower()
        words = [w.strip('.,!?;:()[]{}') for w in task_text.split()]
        
        # Берем только значимые слова (>= 4 символа, не стоп-слова)
        significant_words = [w for w in words if len(w) >= 4 and w not in stop_words]
        
        task_user = _art_user_by_id.get(task.user_id)
        if not task_user or not task_user.username:
            continue
        
        for word in significant_words:
            word_to_tasks[word].append({
                'username': task_user.username,
                'task': task.title,
                'user_id': task.user_id
            })
    
    # Находим слова, которые встречаются у 3+ разных пользователей
    group_opportunities = []
    for word, tasks_list in word_to_tasks.items():
        # Убираем дубликаты по user_id
        unique_users = {}
        for task_info in tasks_list:
            if task_info['user_id'] not in unique_users:
                unique_users[task_info['user_id']] = task_info
        
        if len(unique_users) >= 3:
            # Проверяем релевантность этого слова для текущего пользователя
            user_text = ''
            if profile.interests:
                user_text += ' ' + profile.interests.lower()
            if profile.goals:
                user_text += ' ' + profile.goals.lower()
            if profile.skills:
                user_text += ' ' + profile.skills.lower()
            
            # Если слово релевантно пользователю (есть в его профиле или похожие корни)
            is_relevant = False
            
            # Прямое совпадение
            if word in user_text:
                is_relevant = True
            # Проверка по корням (первые 5 символов)
            elif len(word) >= 5:
                for ut in user_text.split():
                    if len(ut) >= 5 and (word[:5] in ut or ut[:5] in word):
                        is_relevant = True
                        break
            
            if is_relevant:
                group_opportunities.append({
                    'topic': word,
                    'users': unique_users,
                    'count': len(unique_users)
                })
    
    # Возвращаем первую найденную групповую возможность
    if group_opportunities:
        # Сортируем по количеству участников
        group_opportunities.sort(key=lambda x: x['count'], reverse=True)
        best_group = group_opportunities[0]
        
        usernames = [f"@{info['username']}" for info in list(best_group['users'].values())[:3]]
        count = best_group['count']
        topic = best_group['topic']
        
        return f" {count} человек работают над задачами связанными с '{topic}' — организовать обсуждение? Участники: {', '.join(usernames)}"
    
    return None


def create_goal(title=None, description=None, category=None, priority=None, target_date=None, success_criteria=None, metric_target=None, metric_unit=None, user_id=None, session=None):
    """Создать новую цель пользователя
    
    Args:
        title: Название цели
        description: Описание цели
        category: Категория (work, personal, health, learning, finance, social)
        priority: Приоритет (low, medium, high, critical)
        target_date: Целевая дата достижения
        success_criteria: Критерии успеха
        metric_target: Целевое числовое значение (50, 10, 1000000)
        metric_unit: Единица измерения (учеников, кг, руб)
        user_id: Telegram ID пользователя
        session: SQLAlchemy session
    """
    if not title:
        return "Укажи название цели."
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        # Проверяем количество активных целей (лимит 20)
        active_goals = session.query(Goal).filter_by(user_id=user.id, status='active').count()
        if active_goals >= 20:
            return " У тебя уже 20 активных целей. Заверши или отмени старые перед созданием новых."

        # ПРОВЕРКА ДУБЛЕЙ: цель с похожим названием уже существует — не создаём
        _stop_g = {'для', 'или', 'что', 'как', 'это', 'при', 'через', 'чтобы', 'the', 'and', 'for', 'with', 'that'}
        _title_lc = title.strip().lower()
        _new_sig = {w for w in _title_lc.split() if len(w) > 3} - _stop_g
        _existing_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status.in_(['active', 'paused'])
        ).all()
        for _eg in _existing_goals:
            _eg_lc = _eg.title.strip().lower()
            _eg_sig = {w for w in _eg_lc.split() if len(w) > 3} - _stop_g
            _overlap_g = _new_sig & _eg_sig
            if _eg_lc == _title_lc or len(_overlap_g) >= 2:
                return (
                    f"⚠️ Похожая цель уже существует: «{_eg.title}» (id={_eg.id}, статус={_eg.status}). "
                    f"Используй update_goal_progress для обновления прогресса, или уточни чем новая цель отличается."
                )

        # Парсим target_date
        parsed_date = None
        if target_date:
            # Пробуем разные форматы
            for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y'):
                try:
                    parsed_date = datetime.strptime(target_date, fmt)
                    break
                except (ValueError, TypeError):
                    continue
            
            # Парсим относительные даты
            if not parsed_date:
                try:
                    td_lower = target_date.lower()
                    import re as _re
                    m = _re.search(r'(\d+)\s*(?:месяц|мес)', td_lower)
                    if m:
                        parsed_date = datetime.now() + timedelta(days=int(m.group(1)) * 30)
                    else:
                        m = _re.search(r'(\d+)\s*(?:недел|нед)', td_lower)
                        if m:
                            parsed_date = datetime.now() + timedelta(weeks=int(m.group(1)))
                        else:
                            m = _re.search(r'(\d+)\s*(?:дн|день|дня)', td_lower)
                            if m:
                                parsed_date = datetime.now() + timedelta(days=int(m.group(1)))
                            else:
                                m = _re.search(r'(\d+)\s*(?:год|лет)', td_lower)
                                if m:
                                    parsed_date = datetime.now() + timedelta(days=int(m.group(1)) * 365)
                except Exception as e:
                    logger.debug(f"Failed to parse goal target_date: {e}")
        
        goal = Goal(
            user_id=user.id,
            title=title[:255],
            description=description[:1000] if description else None,
            category=category or 'personal',
            priority=priority or 'medium',
            target_date=parsed_date,
            success_criteria=success_criteria[:500] if success_criteria else None,
            metric_target=float(metric_target) if metric_target else None,
            metric_unit=str(metric_unit)[:100] if metric_unit else None,
            metric_current=0,
            status='active',
            progress_percentage=0
        )
        session.add(goal)
        session.commit()
        
        # Синхронизируем profile.goals
        try:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                existing = profile.goals or ""
                existing_lower = existing.lower()
                title_lower = title.lower()
                # Проверяем дубликат: точное вхождение ИЛИ один является частью другого
                is_duplicate = (
                    title_lower in existing_lower or
                    any(part.strip() and title_lower.startswith(part.strip()) 
                        for part in existing_lower.split(';'))
                )
                if existing and not is_duplicate:
                    profile.goals = f"{existing}; {title}"
                elif existing and is_duplicate:
                    # Заменяем короткую версию на полную (более детальную)
                    parts = [p.strip() for p in existing.split(';') if p.strip()]
                    updated_parts = []
                    replaced = False
                    for part in parts:
                        if not replaced and title_lower.startswith(part.lower()):
                            updated_parts.append(title)
                            replaced = True
                        else:
                            updated_parts.append(part)
                    if not replaced:
                        updated_parts = parts  # ничего не меняем если точное вхождение
                    profile.goals = '; '.join(updated_parts)
                elif not existing:
                    profile.goals = title
                session.commit()
                logger.info(f"[CREATE_GOAL] Synced profile.goals: {profile.goals}")
        except Exception as e:
            logger.warning(f"[CREATE_GOAL] Failed to sync profile.goals: {e}")

        # === Лог активности ===
        try:
            from models import AgentActivityLog as _AAL_cg
            _cg_log = _AAL_cg(
                user_id=user.id,
                activity_type='goal_created',
                title=f'Проект создан: {goal.title}',
                content=(description[:200] if description else None),
                status='completed',
                ref_id=goal.id,
            )
            session.add(_cg_log)
            session.commit()
        except Exception as _e:
            logger.warning(f"[CREATE_GOAL] Activity log failed: {_e}")

        # === Векторная память: сохраняем цель в Pinecone ===
        try:
            from ai_integration.vector_memory import store_memory_sync as _vmem_goal
            _goal_mem = f"Цель пользователя: {goal.title}"
            if goal.description:
                _goal_mem += f". {goal.description[:200]}"
            if goal.success_criteria:
                _goal_mem += f". Критерий: {goal.success_criteria[:100]}"
            if goal.metric_target and goal.metric_unit:
                _goal_mem += f". Метрика: 0/{int(goal.metric_target)} {goal.metric_unit}"
            _vmem_goal(user.telegram_id, _goal_mem, {
                'type': 'goal',
                'goal_id': str(goal.id),
                'category': goal.category or 'personal',
                'priority': goal.priority or 'medium',
            })
        except Exception as _vm_err:
            logger.debug(f"[CREATE_GOAL] Vector memory store skipped: {_vm_err}")

        result = f"Цель создана: {goal.title}"
        if goal.metric_target and goal.metric_unit:
            result += f"\nМетрика: 0/{int(goal.metric_target)} {goal.metric_unit}"
        if goal.category:
            result += f"\nКатегория: {goal.category}"
        if goal.priority and goal.priority != 'medium':
            result += f"\nПриоритет: {goal.priority}"
        if parsed_date:
            result += f"\nДедлайн: {parsed_date.strftime('%d.%m.%Y')}"
        if goal.success_criteria:
            result += f"\nКритерии: {goal.success_criteria}"
        result += f"\n\nТеперь можешь привязывать задачи к этой цели — так ты увидишь прогресс!"

        return result
    
    except Exception as e:
        logger.error(f"Error creating goal for user {user_id}: {e}")
        return f" Ошибка при создании цели: {str(e)}"
    finally:
        if close_session:
            session.close()


def update_goal_progress(goal_title=None, progress=None, status=None, notes=None, metric_current=None, user_id=None, session=None, progress_increment=None):
    """Обновить прогресс или статус цели
    
    Args:
        goal_title: Название или часть названия цели для поиска
        progress: Новый процент прогресса (0-100) — для целей без метрики
        status: Новый статус (active, completed, paused, cancelled)
        notes: Заметки о прогрессе
        metric_current: Текущее значение метрики (авто-расчёт процента)
        user_id: Telegram ID
        session: SQLAlchemy session
        progress_increment: Инкрементный прогресс (add N% to current). Used by auto-tracking.
    """
    if not goal_title:
        return "Укажи название цели для обновления."
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        # Гибкий поиск цели
        goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status.in_(['active', 'paused'])
        ).all()
        
        if not goals:
            return "У тебя нет активных целей. Создай цель командой или просто скажи — например, 'хочу выучить Python за 3 месяца'."
        
        # Ищем по ключевым словам
        search = goal_title.lower()
        matched = None
        for g in goals:
            if search in g.title.lower() or (g.description and search in g.description.lower()):
                matched = g
                break
        
        # Fuzzy fallback
        if not matched:
            for g in goals:
                title_words = g.title.lower().split()
                if any(w in search for w in title_words if len(w) > 2):
                    matched = g
                    break
        
        if not matched:
            titles = ', '.join(f'"{g.title}"' for g in goals[:5])
            return f"Цель \"{goal_title}\" не найдена. Активные цели: {titles}"
        
        changes = []

        # ── progress_increment: auto-tracking adds N% to current progress ──
        if progress_increment is not None and progress is None and metric_current is None:
            try:
                _incr = int(progress_increment)
                _old_pct = matched.progress_percentage or 0
                _new_pct = min(99, _old_pct + _incr)  # cap at 99% — completion only via metric or explicit
                if _new_pct > _old_pct:
                    matched.progress_percentage = _new_pct
                    changes.append(f"прогресс: {_old_pct}% → {_new_pct}% (+{_incr}%)")
            except (ValueError, TypeError):
                pass
            # Still save notes as AAL even if no progress change
            if notes:
                try:
                    from models import AgentActivityLog as _AAL_incr
                    session.add(_AAL_incr(
                        user_id=matched.user_id,
                        activity_type='goal_updated',
                        ref_id=matched.id,
                        result=notes[:500] if notes else '',
                    ))
                except Exception:
                    pass
            if changes:
                try:
                    session.commit()
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass
            return f"Прогресс цели «{matched.title}»: {', '.join(changes)}" if changes else "OK"

        # Авто-определение metric_target из названия цели, если оно None
        if not matched.metric_target:
            import re as _re_ugp
            _numbers = _re_ugp.findall(r'\b(\d{1,4})\b', matched.title + ' ' + (matched.description or ''))
            _plausible = [int(n) for n in _numbers if 2 <= int(n) <= 10000]
            if _plausible:
                matched.metric_target = float(_plausible[0])
                session.commit()

        # Обработка metric_current — автоматический расчёт процента
        if metric_current is not None and not matched.metric_target:
            # metric_target не задан — сохраняем metric_current, но не можем рассчитать процент
            try:
                mc = float(metric_current)
                matched.metric_current = mc
                changes.append(f"метрика: {int(mc)} (цель не задана — обновляй progress вручную)")
            except (ValueError, TypeError):
                pass
        elif metric_current is not None and matched.metric_target:
            try:
                mc = float(metric_current)
                # GUARD: metric_current должен увеличиться хотя бы на 1 целую единицу
                _old_mc = float(matched.metric_current or 0)
                if mc <= _old_mc:
                    return f"metric_current ({mc}) не больше текущего ({_old_mc}). Обновляй ТОЛЬКО когда нашёл РЕАЛЬНОГО нового пользователя/контакт."
                if mc - _old_mc < 1.0:
                    return f"Прирост метрики слишком мал ({mc - _old_mc:.1f}). Увеличивай на целые единицы — 1 единица = 1 реальный найденный пользователь."
                # GUARD: для people-целей — запрет крупного прироста без подтверждённых ответов
                # Агент НЕ должен ставить metric_current = N_contacts_in_db (это не тестировщики!)
                _ppl_units_chk = ('пользователь', 'пользователей', 'тестировщик', 'тестировщиков',
                                  'человек', 'участник', 'участников', 'подписчик', 'подписчиков',
                                  'лиц', 'клиент', 'клиентов', 'партнёр', 'партнёров')
                _ppl_kw_chk = ('тестировщик', 'пользовател', 'участник', 'tester', 'user ',
                               'заинтересован', 'привлеч', 'клиент', 'партнёр')
                _gfull_chk = (matched.title + ' ' + (matched.description or '') + ' ' + (matched.metric_unit or '')).lower()
                _is_ppl_chk = (
                    any(u in (matched.metric_unit or '').lower() for u in _ppl_units_chk)
                    or any(w in _gfull_chk for w in _ppl_kw_chk)
                )
                if _is_ppl_chk and (mc - _old_mc) >= 1:
                    try:
                        from models import EmailOutreach as _EO_chk, AgentActivityLog as _AAL_chk
                        _delta_chk = mc - _old_mc
                        # Размер прыжка определяет требуемые доказательства:
                        # +1..3 — всегда OK (small increment per real contact found)
                        # +4..10 — нужны свежие inbox_reply за последний час
                        # +11 и более — нужны реальные EmailOutreach.replied > 0
                        if _delta_chk > 10:
                            _rpl_chk = session.query(_EO_chk).filter(
                                _EO_chk.user_id == user.id, _EO_chk.status == 'replied'
                            ).count()
                            if _rpl_chk == 0:
                                return (
                                    f"⛔ Нельзя увеличить метрику «{matched.title}» сразу на +{int(_delta_chk)} — "
                                    f"нет подтверждённых ответов на outreach-письма.\n\n"
                                    f"Правило: 1 единица цели = 1 реальный человек, подтвердивший участие.\n"
                                    f"Обновляй метрику постепенно: +1 за каждый реальный ответ.\n\n"
                                    f"Текущая метрика: {int(_old_mc)} / {int(matched.metric_target)} {matched.metric_unit or ''}"
                                )
                        elif _delta_chk > 3:
                            # Нужны inbox_reply за последний час (реальная свежая активность)
                            _ibx_chk = session.query(_AAL_chk).filter(
                                _AAL_chk.user_id == user.id, _AAL_chk.activity_type == 'inbox_reply',
                                _AAL_chk.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
                            ).count()
                            if _ibx_chk == 0:
                                return (
                                    f"⛔ Нельзя увеличить метрику «{matched.title}» на +{int(_delta_chk)} сразу — "
                                    f"не было свежих ответов на письма в последний час.\n\n"
                                    f"Правило: обновляй метрику только после check_emails.\n"
                                    f"Шаг: +1-3 за каждую реальную новую группу ответов.\n\n"
                                    f"Текущая метрика: {int(_old_mc)} / {int(matched.metric_target)} {matched.metric_unit or ''}"
                                )
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                # Исключение: если это финальный update (цель достигается) — rate-limit пропускаем
                # Исключение 2: если новая метрика > старой (реальный рост) — тоже пропускаем rate-limit
                _would_complete = (mc >= matched.metric_target)
                _real_growth = mc > (matched.metric_current or 0)
                if not _would_complete and not _real_growth:
                    try:
                        from models import AgentActivityLog as _AAL_rl
                        _recent_updates = session.query(_AAL_rl).filter(
                            _AAL_rl.user_id == user.id,
                            _AAL_rl.activity_type == 'goal_updated',
                            _AAL_rl.ref_id == matched.id,
                            _AAL_rl.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
                        ).count()
                        if _recent_updates >= 1:
                            return f"Метрика цели '{matched.title}' уже обновлялась менее часа назад. Подожди перед следующим обновлением. Метрика обновляется только при РЕАЛЬНОМ новом результате."
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                matched.metric_current = mc
                pct = int(mc / matched.metric_target * 100)
                if mc > 0 and pct == 0:
                    pct = 1  # показываем хотя бы 1% при наличии прогресса
                pct = max(0, min(100, pct))
                matched.progress_percentage = pct
                changes.append(f"метрика: {int(mc)}/{int(matched.metric_target)} {matched.metric_unit or ''} ({pct}%)")
                if pct >= 100 and matched.status == 'active':
                    # GUARD: people-goals требуют подтверждённого участия перед закрытием
                    _mc_people_units = ('пользователь', 'пользователей', 'тестировщик', 'тестировщиков',
                                        'человек', 'участник', 'участников', 'подписчик', 'подписчиков',
                                        'лиц', 'клиент', 'клиентов', 'партнёр', 'партнёров')
                    _mc_people_kw = ('тестировщик', 'пользовател', 'участник', 'tester', 'user ',
                                     'заинтересован', 'привлеч', 'клиент', 'партнёр')
                    _mc_gfull = (matched.title + ' ' + (matched.description or '') + ' ' + (matched.metric_unit or '')).lower()
                    _mc_is_ppl = (
                        any(u in (matched.metric_unit or '').lower() for u in _mc_people_units)
                        or any(w in _mc_gfull for w in _mc_people_kw)
                    )
                    if _mc_is_ppl:
                        try:
                            from models import EmailOutreach as _EO_mc
                            # Требуем минимум 1 реальный ответ на outreach-письмо
                            _rpl_mc = session.query(_EO_mc).filter(
                                _EO_mc.user_id == user.id, _EO_mc.status == 'replied'
                            ).count()
                            if _rpl_mc == 0:
                                # Записываем обновление метрики, но НЕ закрываем цель
                                changes.append(f"⚠️ цель НЕ закрыта — нет подтверждённых ответов на outreach-письма")
                                session.commit()
                                return (
                                    f"⚠️ Метрика обновлена: {int(mc)}/{int(matched.metric_target)} {matched.metric_unit or ''}, "
                                    f"но цель «{matched.title}» НЕ закрыта.\n\n"
                                    f"Email-контакты в базе ≠ пользователи, начавшие тестирование.\n"
                                    f"Следующий шаг:\n"
                                    f"  1. Вызови check_emails — есть ли ответы на outreach?\n"
                                    f"  2. Если есть ответ → negotiate_by_email: спроси начали ли они тестировать\n"
                                    f"  3. При подтверждении → update_goal_progress(status='completed')"
                                )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    matched.status = 'completed'
                    matched.completed_at = datetime.now()
                    changes.append("статус: завершено! ")
                    # === Векторная память: фиксируем достижение ===
                    try:
                        from ai_integration.vector_memory import store_memory_sync as _vmem_ach
                        _ach_text = (
                            f"Достижение: цель '{matched.title}' выполнена! "
                            f"Метрика: {int(mc)}/{int(matched.metric_target)} {matched.metric_unit or ''}."
                        )
                        _vmem_ach(user.telegram_id, _ach_text, {
                            'type': 'achievement',
                            'goal_id': str(matched.id),
                            'category': matched.category or 'personal',
                        })
                    except Exception as _vm_err:
                        logger.debug(f"[UPDATE_GOAL] Vector memory achievement skipped: {_vm_err}")
            except (ValueError, TypeError):
                pass
        elif progress is not None:
            try:
                pct = int(progress)
                pct = max(0, min(100, pct))
                # GUARD: если у цели есть metric_target — прогресс считается ТОЛЬКО через metric_current
                # Запрещаем AI-агенту произвольно ставить progress на цели с метриками
                if matched.metric_target and matched.metric_target > 0:
                    actual_pct = int((matched.metric_current or 0) / matched.metric_target * 100)
                    if abs(pct - actual_pct) >= 5:
                        return f"У цели '{matched.title}' есть числовая метрика ({int(matched.metric_current or 0)}/{int(matched.metric_target)}). Обновляй через metric_current, а не progress."
                # GUARD: если нет metric_target — прогресс нельзя ставить без notes (подтверждения)
                # И прирост не более +5% за один вызов
                if not matched.metric_target or matched.metric_target <= 0:
                    if not notes:
                        return (
                            f"⛔ Нельзя изменить progress цели '{matched.title}' без обоснования.\n"
                            f"Укажи notes= с описанием КОНКРЕТНОГО результата, который даёт этот прогресс.\n"
                            f"Например: notes='Получен ответ от Иван Иванов — подтвердил участие'"
                        )
                    _old_pct = matched.progress_percentage or 0
                    if pct - _old_pct > 5:
                        return (
                            f"⛔ Нельзя увеличить прогресс цели '{matched.title}' сразу на +{pct - _old_pct}% (с {_old_pct}% до {pct}%).\n"
                            f"Максимальный прирост: +5% за один вызов.\n"
                            f"Обновляй прогресс только на основе РЕАЛЬНЫХ подтверждённых результатов."
                        )
                # GUARD: прогресс не может уменьшаться (агент может ошибочно занизить)
                if matched.progress_percentage and pct < matched.progress_percentage:
                    pct = matched.progress_percentage
                matched.progress_percentage = pct
                changes.append(f"прогресс: {pct}%")
                if pct == 100 and matched.status == 'active':
                    # === Векторная память: фиксируем достижение ===
                    _vmem_pct_text = f"Достижение: цель '{matched.title}' выполнена на 100%!"
                    try:
                        from ai_integration.vector_memory import store_memory_sync as _vmem_p
                        _vmem_p(user.telegram_id, _vmem_pct_text, {
                            'type': 'achievement',
                            'goal_id': str(matched.id),
                            'category': matched.category or 'personal',
                        })
                    except Exception as _vm_err:
                        logger.debug(f"[UPDATE_GOAL] Vector memory pct achievement skipped: {_vm_err}")
                    # Та же проверка участия для people-целей
                    _p_units = ('пользователь', 'пользователей', 'тестировщик', 'тестировщиков',
                                'человек', 'участник', 'участников', 'подписчик', 'подписчиков',
                                'лиц', 'клиент', 'клиентов', 'партнёр', 'партнёров')
                    _p_kw = ('тестировщик', 'пользовател', 'участник', 'tester', 'user ',
                             'заинтересован', 'привлеч', 'клиент', 'партнёр')
                    _g_full = (matched.title + ' ' + (matched.description or '') + ' ' + (matched.metric_unit or '')).lower()
                    _is_ppl = (
                        any(u in (matched.metric_unit or '').lower() for u in _p_units)
                        or any(w in _g_full for w in _p_kw)
                    )
                    if _is_ppl and matched.metric_target:
                        try:
                            from models import EmailContact as _EC_p, AgentActivityLog as _AAL_p
                            _rpl = session.query(_EC_p).filter(_EC_p.user_id == user.id, _EC_p.status == 'replied').count()
                            _ibx = session.query(_AAL_p).filter(
                                _AAL_p.user_id == user.id, _AAL_p.activity_type == 'inbox_reply',
                                _AAL_p.created_at >= datetime.now(timezone.utc) - timedelta(days=14),
                            ).count()
                            if _rpl == 0 and _ibx == 0:
                                return (
                                    f"⛔ Нельзя выставить 100% для цели «{matched.title}» — "
                                    f"email-контакты ≠ реальные тестировщики. "
                                    f"Сначала проверь ответы через check_emails и подтверди реальное участие."
                                )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    matched.status = 'completed'
                    matched.completed_at = datetime.now()
                    changes.append("статус: завершено! ")
            except (ValueError, TypeError):
                pass
        
        if status:
            valid = {'active', 'completed', 'paused', 'cancelled'}
            if status in valid:
                # ── GUARD: цели по людям (тестировщики/пользователи) нельзя закрывать
                # без подтверждения реального участия — не просто отправленных писем ──
                if status == 'completed':
                    _people_units = ('пользователь', 'пользователей', 'тестировщик', 'тестировщиков',
                                     'человек', 'участник', 'участников', 'подписчик', 'подписчиков')
                    _people_kw = ('тестировщик', 'пользовател', 'участник', 'tester', 'user ')
                    _goal_full = (matched.title + ' ' + (matched.description or '') + ' ' + (matched.metric_unit or '')).lower()
                    _is_people_goal = (
                        any(u in (matched.metric_unit or '').lower() for u in _people_units)
                        or any(w in _goal_full for w in _people_kw)
                    )
                    if _is_people_goal:
                        try:
                            from models import EmailOutreach as _EO_v
                            # Требуем только реальные ответы на outreach (EmailOutreach.replied > 0)
                            _replied_cnt = session.query(_EO_v).filter(
                                _EO_v.user_id == user.id,
                                _EO_v.status == 'replied',
                            ).count()
                            if _replied_cnt == 0:
                                return (
                                    f"⛔ Цель «{matched.title}» нельзя закрыть — нет подтверждённых ответов на outreach-письма.\n\n"
                                    f"Email-контакты в базе ≠ зарегистрированные тестировщики/пользователи.\n"
                                    f"Сначала убедись в реальном участии:\n"
                                    f"  1. Вызови check_emails — проверь есть ли ответы на outreach\n"
                                    f"  2. Если есть ответы с подтверждением — используй negotiate_by_email чтобы уточнить начали ли тестирование\n"
                                    f"  3. Только после получения подтверждений обнови metric_current реальным числом участников\n\n"
                                    f"Текущее состояние: {int(matched.metric_current or 0)}/{int(matched.metric_target or 0)} "
                                    f"{matched.metric_unit or ''} — это только отправленные письма, не подтверждённые пользователи."
                                )
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                matched.status = status
                if status == 'completed':
                    matched.completed_at = datetime.now()
                    matched.progress_percentage = 100
                changes.append(f"статус: {status}")
        
        if notes:
            existing = matched.progress_notes or ''
            timestamp = datetime.now().strftime('%d.%m')
            new_note = f"[{timestamp}] {notes[:200]}"
            matched.progress_notes = (existing + '\n' + new_note).strip()[-2000:]
            changes.append("добавлена заметка")
        
        if not changes:
            return f"Укажи что обновить: progress (0-100), status (active/completed/paused/cancelled), или notes."

        # Rate-limit: notes-only обновления не чаще раза в 30 минут
        if changes == ["добавлена заметка"]:
            try:
                from models import AgentActivityLog as _AAL_rl
                from datetime import timedelta as _td_rl
                _cutoff_rl = datetime.now() - _td_rl(minutes=30)
                _recent_rl = session.query(_AAL_rl).filter(
                    _AAL_rl.user_id == user.id,
                    _AAL_rl.ref_id == matched.id,
                    _AAL_rl.activity_type == 'goal_updated',
                    _AAL_rl.created_at >= _cutoff_rl,
                ).first()
                if _recent_rl:
                    return (
                        f"ℹ️ Цель '{matched.title}': заметка уже обновлялась менее 30 минут назад. "
                        f"Не нужно добавлять одни и те же заметки повторно — это шум в логах."
                    )
            except Exception as _rl_e:
                logger.debug("rate-limit check failed: %s", _rl_e)

        session.commit()

        # === Лог активности ===
        try:
            from models import AgentActivityLog as _AAL_ugp
            _ugp_type = 'goal_completed' if matched.status == 'completed' else 'goal_updated'
            _ugp_title = f'Цель достигнута: {matched.title}' if _ugp_type == 'goal_completed' else f'Проект обновлён: {matched.title}'
            _ugp_log = _AAL_ugp(
                user_id=user.id,
                activity_type=_ugp_type,
                title=_ugp_title,
                content=', '.join(changes[:3]),
                status='completed',
                ref_id=matched.id,
            )
            session.add(_ugp_log)
            session.commit()
        except Exception as _e:
            logger.warning(f"[UPDATE_GOAL] Activity log failed: {_e}")

        result = f" **{matched.title}** обновлена:\n"
        result += ", ".join(changes)
        if matched.metric_target and matched.metric_unit:
            mc = int(matched.metric_current or 0)
            mt = int(matched.metric_target)
            result += f"\n {mc}/{mt} {matched.metric_unit} ({matched.progress_percentage}%)"
        else:
            result += f"\n Прогресс: {matched.progress_percentage}%"
        
        # Связанные задачи
        linked_tasks = session.query(Task).filter_by(user_id=user.id, goal_id=matched.id, status='pending').count()
        if linked_tasks:
            result += f"\n Связанных задач: {linked_tasks}"
        
        return result
    
    except Exception as e:
        logger.error(f"Error updating goal for user {user_id}: {e}")
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def _progress_bar(pct, width=10):
    """Возвращает текстовый прогресс-бар, например: ██████░░░░ 60%"""
    pct = max(0, min(100, int(pct or 0)))
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def list_goals(status_filter=None, user_id=None, session=None):
    """Показать цели пользователя
    
    Args:
        status_filter: Фильтр по статусу (active, completed, paused, all)
        user_id: Telegram ID
        session: SQLAlchemy session
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        query = session.query(Goal).filter_by(user_id=user.id)
        
        if status_filter and status_filter != 'all':
            query = query.filter_by(status=status_filter)
        else:
            # По умолчанию показываем активные и приостановленные
            query = query.filter(Goal.status.in_(['active', 'paused']))
        
        goals = query.order_by(Goal.created_at.desc()).limit(15).all()
        
        if not goals:
            if status_filter == 'completed':
                return "У тебя нет завершённых целей."
            return "У тебя пока нет целей. Расскажи о своих планах — помогу сформулировать и отслеживать!"
        
        priority_label = {'critical': '[!]', 'high': '[высокий]', 'medium': '', 'low': '[низкий]'}
        status_label = {'active': '', 'completed': '[выполнена]', 'paused': '[пауза]', 'cancelled': '[отменена]'}
        
        result = "Твои цели:\n\n"

        # Batch-load all linked tasks for all goals (avoid N+1)
        _gl_goal_ids = [g.id for g in goals]
        _gl_tasks_all = session.query(Task).filter(
            Task.user_id == user.id, Task.goal_id.in_(_gl_goal_ids)
        ).all() if _gl_goal_ids else []
        _gl_tasks_by_goal: dict = {}
        for _glt in _gl_tasks_all:
            if _glt.goal_id is not None:
                _gl_tasks_by_goal.setdefault(_glt.goal_id, []).append(_glt)

        for g in goals:
            status_lbl = status_label.get(g.status, '')
            pri = priority_label.get(g.priority, '')
            progress_bar = _progress_bar(g.progress_percentage)
            
            result += f"{g.title} {status_lbl} {pri}\n".replace('  ', ' ').strip() + '\n'
            result += f"   {progress_bar} {g.progress_percentage}%"
            
            if getattr(g, 'metric_current', None) is not None and getattr(g, 'metric_target', None):
                result += f" ({int(g.metric_current)}/{int(g.metric_target)})"
            
            if g.category:
                result += f" | {g.category}"
            if g.target_date:
                days = g.days_until_target()
                if days is not None:
                    if days < 0:
                        result += f" | просрочено на {abs(days)} дн."
                    elif days == 0:
                        result += f" | дедлайн сегодня"
                    elif days <= 7:
                        result += f" | {days} дн. осталось"
                    else:
                        result += f" | до {g.target_date.strftime('%d.%m.%Y')}"
            
            # Связанные задачи
            linked = _gl_tasks_by_goal.get(g.id, [])
            if linked:
                done = sum(1 for t in linked if t.status == 'completed')
                total = len(linked)
                result += f" | задачи: {done}/{total}"
            
            result += "\n"
        
        return result
    
    except Exception as e:
        logger.error(f"Error listing goals for user {user_id}: {e}")
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def delete_goal(goal_title=None, user_id=None, session=None):
    """Удалить цель пользователя
    
    Args:
        goal_title: Название или ключевые слова цели для поиска. 'all' — удалить все цели.
        user_id: Telegram ID
        session: SQLAlchemy session
    """
    if not goal_title:
        return "Укажи название цели для удаления или 'все' чтобы удалить все."
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        # Удалить все цели
        if goal_title.lower().strip() in ('all', 'все', 'всё'):
            goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status.in_(['active', 'paused'])
            ).all()
            if not goals:
                return "У тебя нет активных целей."
            count = len(goals)
            for g in goals:
                session.delete(g)
            # Очистить goals в профиле
            try:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    profile.goals = ''
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            # Очистить conversation_history чтобы бот не цитировал старые цели
            try:
                from .conversation_history import clear_conversation_history
                clear_conversation_history(user_id)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            session.commit()
            # === Удаляем из векторной памяти (Pinecone) ===
            try:
                from ai_integration.vector_memory import store_memory_sync as _vmem_del_all
                from ai_integration.vector_memory import _search_memory_sync as _vsearch_del
                from ai_integration.vector_memory import _get_pinecone as _vpc_del
                _vpc_idx = _vpc_del()
                if _vpc_idx:
                    # Ищем все goal/achievement векторы пользователя и удаляем
                    _all_vecs = _vsearch_del(user.telegram_id, 'цель проект достижение', top_k=50)
                    _del_ids = []
                    import hashlib as _hsh_del
                    for _mv in _all_vecs:
                        if _mv.get('type') in ('goal', 'achievement'):
                            # Пересоздаём ID чтобы не хранить его отдельно — нельзя, ID включает timestamp
                            # Поэтому используем delete_by_filter (Pinecone serverless поддерживает)
                            pass
                    # Pinecone serverless: удаляем вектора по filter через delete
                    try:
                        _vpc_idx.delete(
                            filter={'user_id': {'$eq': str(user.telegram_id)}, 'type': {'$in': ['goal', 'achievement']}},
                            namespace=f'user_{user.telegram_id}'
                        )
                        logger.info(f"[DELETE_GOAL] Removed all goal/achievement vectors for user {user.telegram_id}")
                    except Exception as _pf_err:
                        logger.debug(f"[DELETE_GOAL] Pinecone filter delete failed: {_pf_err}")
            except Exception as _vm_del_err:
                logger.debug(f"[DELETE_GOAL] Vector memory cleanup skipped: {_vm_del_err}")
            # === Лог активности ===
            try:
                from models import AgentActivityLog as _AAL_dga
                session.add(_AAL_dga(
                    user_id=user.id, activity_type='goal_deleted',
                    title=f'Удалены все проекты ({count} шт.)',
                    status='completed',
                ))
                session.commit()
            except Exception as _e:
                logger.warning(f"[DELETE_GOAL] Activity log failed: {_e}")
            return f"Удалено целей: {count}. Чистый лист — можно ставить новые! ВНИМАНИЕ: все упоминания целей в текущем контексте и профиле УСТАРЕЛИ. НЕ ссылайся на них, НЕ цитируй, НЕ предлагай вернуть. Целей НОЛЬ."
        
        # Поиск конкретной цели
        goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status.in_(['active', 'paused'])
        ).all()
        
        if not goals:
            return "У тебя нет активных целей."
        
        search = goal_title.lower()
        matched = None
        for g in goals:
            if search in g.title.lower() or (g.description and search in g.description.lower()):
                matched = g
                break
        
        if not matched:
            for g in goals:
                title_words = g.title.lower().split()
                if any(w in search for w in title_words if len(w) > 2):
                    matched = g
                    break
        
        if not matched:
            titles = ', '.join(f'"{g.title}"' for g in goals[:5])
            return f"Цель \"{goal_title}\" не найдена. Активные цели: {titles}"
        
        title = matched.title
        session.delete(matched)
        
        # Убрать из profile.goals
        try:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile and profile.goals:
                parts = [p.strip() for p in profile.goals.split(';') if title.lower() not in p.strip().lower()]
                profile.goals = '; '.join(parts) if parts else ''
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        
        # Очистить conversation_history чтобы бот не цитировал удалённую цель
        try:
            from .conversation_history import clear_conversation_history
            clear_conversation_history(user_id)
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        
        session.commit()
        # === Удаляем из векторной памяти (Pinecone) ===
        try:
            from ai_integration.vector_memory import _get_pinecone as _vpc_dg
            _vpc_idx_dg = _vpc_dg()
            if _vpc_idx_dg:
                _vpc_idx_dg.delete(
                    filter={
                        'user_id': {'$eq': str(user.telegram_id)},
                        'type': {'$in': ['goal', 'achievement']},
                        'goal_id': {'$eq': str(matched.id if hasattr(matched, 'id') else '')},
                    },
                    namespace=f'user_{user.telegram_id}'
                )
                logger.info(f"[DELETE_GOAL] Removed vectors for goal '{title}' from Pinecone")
        except Exception as _vm_dg_err:
            logger.debug(f"[DELETE_GOAL] Vector memory cleanup skipped: {_vm_dg_err}")
        # === Лог активности ===
        try:
            from models import AgentActivityLog as _AAL_dg
            session.add(_AAL_dg(
                user_id=user.id, activity_type='goal_deleted',
                title=f'Проект удалён: {title}',
                status='completed',
            ))
            session.commit()
        except Exception as _e:
            logger.warning(f"[DELETE_GOAL] Activity log failed: {_e}")
        return f"Цель \"{title}\" удалена. Если эта цель упоминается в контексте или профиле — ИГНОРИРУЙ, она удалена."
    
    except Exception as e:
        logger.error(f"Error deleting goal for user {user_id}: {e}")
        return f"Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def complete_goal(goal_id=None, title=None, user_id=None, session=None):
    """Отметить цель как выполненную. Алиас update_goal_progress(status='completed')."""
    search_title = title or (str(goal_id) if goal_id else None)
    if not search_title:
        return "Укажи название или ID цели."
    return update_goal_progress(
        goal_title=search_title,
        status='completed',
        progress=100,
        user_id=user_id,
        session=session,
    )


def update_goal(goal_id=None, title=None, description=None, target_date=None, user_id=None, session=None):
    """Обновить параметры цели: название, описание, дедлайн."""
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        # Найти цель по ID или названию
        goal = None
        if goal_id:
            goal = session.query(Goal).filter_by(id=goal_id, user_id=user.id).first()
        if not goal and title:
            goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status.in_(['active', 'paused'])
            ).all()
            search = title.lower()
            for g in goals:
                if search in g.title.lower():
                    goal = g
                    break
            if not goal:
                for g in goals:
                    if any(w in search for w in g.title.lower().split() if len(w) > 2):
                        goal = g
                        break
        if not goal:
            return f"Цель не найдена. Проверь название или ID."

        changes = []
        if title and title != goal.title:
            goal.title = title.strip()
            changes.append(f"название: {title.strip()}")
        if description is not None:
            goal.description = description.strip()
            changes.append("описание обновлено")
        if target_date:
            from .time_parser import parse_time_to_datetime
            dt = parse_time_to_datetime(target_date, user_id=user_id)
            if dt:
                goal.target_date = dt
                changes.append(f"дедлайн: {dt.strftime('%d.%m.%Y')}")

        if not changes:
            return "Укажи что нужно изменить: title, description или target_date."

        session.commit()
        try:
            from models import AgentActivityLog as _AAL_ug
            session.add(_AAL_ug(
                user_id=user.id, activity_type='goal_updated',
                title=f'Проект изменён: {goal.title}',
                content=', '.join(changes),
                status='completed', ref_id=goal.id,
            ))
            session.commit()
        except Exception as _e:
            logger.warning(f"[UPDATE_GOAL] Activity log failed: {_e}")
        # === Векторная память ===
        try:
            from ai_integration.vector_memory import store_memory_sync as _vmem_ug
            _desc_ug = f" {goal.description[:100]}" if goal.description else ""
            _vmem_ug(user_id, f"Цель обновлена: «{goal.title}».{_desc_ug} Изменения: {', '.join(changes)}",
                     {'type': 'goal', 'goal_id': str(goal.id)})
        except Exception as _e:
            logger.debug(f"[UPDATE_GOAL] Vector memory skipped: {_e}")
        return f" Цель «{goal.title}» обновлена: {', '.join(changes)}"
    except Exception as e:
        logger.error(f"Error in update_goal for user {user_id}: {e}")
        return f"Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def set_reminder(reminder_text=None, reminder_time=None, user_id=None, session=None):
    """Установить напоминание — создаёт задачу с заданным reminder_time."""
    if not reminder_text:
        return "Укажи текст напоминания."
    if not reminder_time:
        return "Укажи время напоминания."
    return await add_task(
        title=reminder_text,
        description="",
        reminder_time=reminder_time,
        user_id=user_id,
        session=session,
    )


def set_do_not_disturb(hours=None, user_id=None, session=None):
    """Включить режим «Не беспокоить» — бот не будет отправлять проактивные сообщения указанное количество часов.

    Args:
        hours: На сколько часов включить (1-720). Если 0 — выключить.
        user_id: Telegram ID
        session: SQLAlchemy session
    """
    if session is None:
        from models import Session as _S
        session = _S()
        _close = True
    else:
        _close = False
    try:
        from models import User
        from datetime import datetime, timedelta, timezone as _tz
        _user = session.query(User).filter_by(telegram_id=user_id).first()
        if not _user:
            return "Пользователь не найден."
        _h = 0
        try:
            _h = int(float(hours or 0))
        except (TypeError, ValueError):
            return "Укажи количество часов (число от 0 до 720)."
        if _h < 0 or _h > 720:
            return "Допустимый диапазон: 0-720 часов (0 — выключить)."
        if _h == 0:
            _user.do_not_disturb_until = None
            session.commit()
            return "Режим «Не беспокоить» выключен. Бот снова будет отправлять сообщения."
        _until = datetime.now(_tz.utc) + timedelta(hours=_h)
        _user.do_not_disturb_until = _until
        session.commit()
        return f"Режим «Не беспокоить» включён на {_h}ч (до {_until.strftime('%d.%m %H:%M')} UTC). Бот не будет отправлять проактивные сообщения."
    except Exception as _e:
        try:
            session.rollback()
        except Exception:
            pass
        return f"Ошибка: {_e}"
    finally:
        if _close:
            session.close()



    """Визуальная полоска прогресса"""
    filled = int(pct / 10)
    empty = 10 - filled
    return '█' * filled + '░' * empty


def show_profile(user_id=None, session=None):
    """Показать профиль пользователя с основной информацией"""
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        result = " **Твой профиль:**\n\n"

        # Основная информация
        if user.username:
            result += f" Имя: @{user.username}\n"
        if user.first_name:
            result += f" Имя: {user.first_name}\n"

        if profile:
            if profile.city:
                result += f" Город: {profile.city}\n"
            if profile.company:
                result += f" Компания: {profile.company}\n"
            if profile.position:
                result += f" Должность: {profile.position}\n"
            if profile.interests:
                result += f" Интересы: {profile.interests}\n"
            if profile.skills:
                result += f" Навыки: {profile.skills}\n"
            if profile.goals:
                result += f" Цели: {profile.goals}\n"
            if profile.birthdate:
                result += f" Дата рождения: {profile.birthdate}\n"
        else:
            result += "\n Профиль ещё не заполнен. Расскажи о себе — город, интересы, навыки, цели — и я всё запомню!"

        # Подписка / токены
        token_balance = getattr(user, 'token_balance', 0) or 0
        result += f"\n Баланс: {token_balance} токенов"

        # Timezone
        if user.timezone:
            result += f"\n Часовой пояс: {user.timezone}"

        return result

    except Exception as e:
        logger.error(f"Ошибка при показе профиля пользователя {user_id}: {e}")
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def update_user_memory(memory_type=None, content=None, user_id=None, session=None):
    """Сохраняет информацию в память/профиль пользователя.
    
    Для interest/skill/goal — добавляет в соответствующее поле профиля.
    Для остальных типов — сохраняет в общую память.
    
    Args:
        memory_type: Тип информации (interest, skill, goal, preference, project, contact, etc.)
        content: Что запомнить
        user_id: Telegram ID пользователя
        session: SQLAlchemy session
    """
    if not content:
        return "Не указано что запомнить."

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)

        content = content.strip()

        # Для профильных типов — добавляем в соответствующие поля
        if memory_type in ('interest', 'interests'):
            existing = set(i.strip().lower() for i in (profile.interests or '').split(',') if i.strip())
            if content.lower() not in existing:
                profile.interests = (profile.interests + ', ' + content) if profile.interests else content
                session.commit()
                # Schedule normalization
                try:
                    import asyncio
                    from .utils import normalize_profile_background
                    asyncio.get_running_loop().create_task(normalize_profile_background(profile.user_id))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                return f" Добавлен интерес: {content}"
            return f"Интерес '{content}' уже есть в профиле."

        elif memory_type in ('skill', 'skills'):
            existing = set(s.strip().lower() for s in (profile.skills or '').split(',') if s.strip())
            if content.lower() not in existing:
                profile.skills = (profile.skills + ', ' + content) if profile.skills else content
                session.commit()
                # Schedule normalization
                try:
                    import asyncio
                    from .utils import normalize_profile_background
                    asyncio.get_running_loop().create_task(normalize_profile_background(profile.user_id))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                return f" Добавлен навык: {content}"
            return f"Навык '{content}' уже есть в профиле."

        elif memory_type in ('goal', 'goals'):
            existing = set(g.strip().lower() for g in (profile.goals or '').split(',') if g.strip())
            if content.lower() not in existing:
                profile.goals = (profile.goals + ', ' + content) if profile.goals else content
                session.commit()
                # Schedule normalization
                try:
                    import asyncio
                    from .utils import normalize_profile_background
                    asyncio.get_running_loop().create_task(normalize_profile_background(profile.user_id))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                return f" Добавлена цель: {content}"
            return f"Цель '{content}' уже есть в профиле."

        else:
            # Для остальных типов — сохраняем в общую память
            from .memory import update_user_memory as _update_memory
            return _update_memory(f"[{memory_type or 'info'}] {content}", user_id=user_id)

    except Exception as e:
        logger.error(f"Ошибка при обновлении памяти пользователя {user_id}: {e}")
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def find_partners(user_id=None, session=None):
    """Find potential partners based on user profile - FULL implementation here"""
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    from i18n import get_user_lang, get_user_lang_by_db_id, get_lang_badge
    lang = get_user_lang(user_id)

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "User not found." if lang == 'en' else "Пользователь не найден."

    # Get user profile
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()

    # Get partners list
    partners = get_partners_list(user.id, session)

    if not partners:
        if close_session:
            session.close()
        if lang == 'en':
            return "No matching people found for your profile yet. Fill in your profile (interests, skills, city) and I'll find like-minded people!"
        return "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, город), и я найду единомышленников!"

    if lang == 'en':
        response = "Found interesting people for your growth and development:\n\n"
    else:
        response = "Нашел интересных людей для твоего роста и развития:\n\n"

    _rec_uids = [p.user_id for p in partners[:5]]
    _rec_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_rec_uids)).all()}

    for idx, p in enumerate(partners[:5], 1):
        partner_user = _rec_user_by_id.get(p.user_id)
        if partner_user and partner_user.username:
            # Get partner language badge
            partner_lang = get_user_lang_by_db_id(p.user_id, session=session)
            badge = get_lang_badge(partner_lang)

            info_parts = []
            relevance_indicators = []

            if user_profile and user_profile.skills and p.skills:
                user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
                profile_skills = set(s.strip().lower() for s in p.skills.split(","))
                if user_skills & profile_skills:
                    relevance_indicators.append(" " + ("shared skills" if lang == 'en' else "общие навыки"))

            if user_profile and user_profile.interests and p.interests:
                user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
                profile_interests = set(i.strip().lower() for i in p.interests.split(","))
                if user_interests & profile_interests:
                    relevance_indicators.append(" " + ("shared interests" if lang == 'en' else "общие интересы"))

            if user_profile and user_profile.goals and p.goals:
                user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
                profile_goals = set(g.strip().lower() for g in p.goals.split(","))
                if user_goals & profile_goals:
                    relevance_indicators.append(" " + ("shared goals" if lang == 'en' else "общие цели"))

            if hasattr(p, "current_plans") and p.current_plans:
                lbl = "now" if lang == 'en' else "сейчас"
                info_parts.append(f"{lbl}: {p.current_plans}")
            if p.interests:
                lbl = "interests" if lang == 'en' else "интересы"
                info_parts.append(f"{lbl}: {p.interests}")
            if hasattr(p, "position") and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, "company") and p.company:
                lbl = "company" if lang == 'en' else "компания"
                info_parts.append(f"{lbl}: {p.company}")
            if p.city:
                lbl = "city" if lang == 'en' else "город"
                info_parts.append(f"{lbl}: {p.city}")

            info_str = ", ".join(info_parts) if info_parts else ("profile in progress" if lang == 'en' else "профиль в разработке")

            contact_line = f"{idx}. {badge} @{partner_user.username}"
            if relevance_indicators:
                contact_line += f" {' • '.join(relevance_indicators)}"
            contact_line += f"\n   {info_str}\n"

            response += contact_line

    if len(partners) > 5:
        if lang == 'en':
            response += "\n These are the top-5 most relevant contacts. Use the full database for maximum growth!"
        else:
            response += "\n Это топ-5 самых релевантных контактов. Используй всю базу данных для максимального роста!"

    if not partners:
        if lang == 'en':
            response = "No matching people found yet. Fill in your profile (interests, skills, goals) and I'll find like-minded people for your development!"
        else:
            response = "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, цели), и я найду единомышленников для твоего развития!"

    if close_session:
        session.close()

    return response

def save_user_rule(rule: str, user_id: int = None, session=None) -> str:
    """Сохраняет поведенческое правило/предпочтение пользователя в долгосрочную память."""
    if not rule or not rule.strip():
        return "Правило не может быть пустым."
    rule = rule.strip()[:400]
    import json as _json_sr
    close_session = False
    if session is None:
        from models import Session as _Sess
        session = _Sess()
        close_session = True
    try:
        from models import User as _User
        from ai_integration.memory import decrypt_data as _dec, encrypt_data as _enc
        _u = session.query(_User).filter_by(telegram_id=user_id).first()
        if not _u:
            return "Пользователь не найден."
        _mem = _json_sr.loads(_dec(_u.memory)) if _u.memory else {}
        _rules = _mem.get('rules', [])
        # Дедупликация по первым 80 символам
        _short = rule[:80].lower()
        if any(r[:80].lower() == _short for r in _rules):
            return "Это правило уже сохранено."
        _rules.append(rule)
        _mem['rules'] = _rules
        _u.memory = _enc(_json_sr.dumps(_mem, ensure_ascii=False))
        session.commit()
        logger.info(f"[SAVE_RULE] uid={user_id}: {rule[:80]}")
        # === Векторная память ===
        try:
            from ai_integration.vector_memory import store_memory_sync as _vmem_sr
            _vmem_sr(user_id, f"Правило: {rule[:300]}", {'type': 'rule'})
        except Exception as _e:
            logger.debug(f"[SAVE_RULE] Vector memory skipped: {_e}")
        return f"Запомнил: «{rule[:120]}»"
    except Exception as e:
        logger.warning(f"[SAVE_RULE] Failed: {e}")
        return "Не удалось сохранить правило."
    finally:
        if close_session:
            session.close()


def find_relevant_contacts_for_task(task_description: str, user_id: int = None, limit: int = 15, session=None) -> str:
    """
    Find contacts relevant for a specific task (bilingual).
    """
    logger.info(f"[FIND_RELEVANT] Searching contacts for task: '{task_description}', user_id={user_id}")
    
    from i18n import get_user_lang, get_user_lang_by_db_id, get_lang_badge
    lang = get_user_lang(user_id) if user_id else 'ru'
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return " User not found" if lang == 'en' else " Пользователь не найден"
    
    # Извлечь ключевые слова из описания задачи
    task_keywords = set()
    stop_words = {'я', 'мне', 'нужно', 'надо', 'хочу', 'буду', 'пойду', 'сделать', 'в', 'на', 'с', 'для', 'от', 'к', 'по', 'из'}
    
    # Синонимы для расширения поиска
    synonyms = {
        'пробежка': ['бег', 'бегать', 'running', 'jogging'],
        'бег': ['пробежка', 'бегать', 'running', 'jogging'],
        'тренировка': ['фитнес', 'спорт', 'gym', 'workout'],
        'спорт': ['фитнес', 'тренировка', 'gym', 'workout'],
        'йога': ['yoga', 'медитация', 'растяжка'],
        'плавание': ['бассейн', 'swimming', 'плавать'],
        'футбол': ['football', 'soccer'],
        'стартап': ['startup', 'бизнес', 'предпринимательство'],
        'startup': ['стартап', 'бизнес', 'предпринимательство'],
        'инвестиции': ['invest', 'финансы', 'вложения'],
        'программирование': ['coding', 'разработка', 'development', 'python', 'javascript'],
        'python': ['программирование', 'coding', 'разработка'],
        'ai': ['искусственный интеллект', 'машинное обучение', 'ml'],
    }
    
    # Гибкие связи желаний с навыками (расширенные синонимы и пересечения)
    flexible_skill_mappings = {
        # Заработок и бизнес
        'заработать': ['маркетинг', 'продажи', 'бизнес', 'финансы', 'предпринимательство', 'партнерская сеть', 'инвестиции', 'консалтинг', 'стартап', 'фриланс', 'монетизация'],
        'деньги': ['финансы', 'инвестиции', 'бизнес', 'продажи', 'маркетинг'],
        'доход': ['бизнес', 'продажи', 'инвестиции', 'фриланс'],
        'богатство': ['инвестиции', 'бизнес', 'финансы', 'предпринимательство'],
        
        # Спорт и здоровье
        'спорт': ['тренер', 'фитнес', 'спорт', 'йога', 'бег', 'плавание', 'футбол', 'баскетбол', 'волейбол', 'теннис', 'гимнастика', 'здоровье'],
        'тренировка': ['тренер', 'фитнес', 'спорт', 'здоровье'],
        'фитнес': ['тренер', 'фитнес', 'спорт', 'здоровье', 'питание'],
        'здоровье': ['врач', 'диетолог', 'психолог', 'массажист', 'натуропат', 'тренер', 'фитнес'],
        'бег': ['тренер', 'бег', 'спорт', 'здоровье'],
        'йога': ['тренер', 'йога', 'медитация', 'растяжка', 'здоровье'],
        
        # Обучение и развитие
        'обучение': ['преподаватель', 'учитель', 'ментор', 'курсы', 'обучение', 'коучинг', 'тренинг', 'развитие'],
        'курс': ['преподаватель', 'учитель', 'курсы', 'обучение'],
        'учить': ['преподаватель', 'учитель', 'ментор', 'коучинг'],
        'развитие': ['ментор', 'коучинг', 'психолог', 'обучение'],
        
        # Творчество
        'творчество': ['дизайнер', 'фотограф', 'художник', 'музыкант', 'писатель', 'видео', 'арт', 'креатив'],
        'дизайн': ['дизайнер', 'арт', 'креатив'],
        'фото': ['фотограф', 'арт'],
        'музыка': ['музыкант', 'арт'],
        'искусство': ['художник', 'арт', 'дизайнер'],
        
        # Технологии
        'программирование': ['программист', 'разработчик', 'it', 'ai', 'машинное обучение', 'data science', 'python', 'javascript'],
        'ai': ['ai', 'машинное обучение', 'data science', 'программист', 'разработчик'],
        'технологии': ['it', 'программист', 'разработчик', 'ai', 'стартап'],
        'стартап': ['предприниматель', 'стартапер', 'бизнес', 'технологии', 'инвестиции'],
        
        # Путешествия
        'путешествия': ['гид', 'туроператор', 'путешественник', 'фотограф'],
        'туризм': ['гид', 'туроператор', 'путешественник'],
        
        # Бизнес общее
        'бизнес': ['предприниматель', 'стартапер', 'инвестор', 'консультант', 'менеджер', 'маркетинг', 'продажи'],
        'предпринимательство': ['предприниматель', 'стартапер', 'бизнес', 'инвестиции'],
        'инвестиции': ['инвестор', 'финансы', 'бизнес'],
    }
    
    # Снижаем минимальную длину до 2 символов чтобы захватить "AI", "ML", "бег"
    words = [w.lower().strip() for w in task_description.split() if len(w) >= 2 and w.lower() not in stop_words]
    task_keywords.update(words)
    
    # Добавить синонимы
    for word in words:
        if word in synonyms:
            task_keywords.update(synonyms[word])
        # Частичное совпадение для длинных слов
        for key, syns in synonyms.items():
            if len(word) > 4 and (key in word or any(syn in word for syn in syns if len(syn) > 3)):
                task_keywords.update([key] + syns)
    
    # Добавить навыки из гибких связей на основе ключевых слов задачи
    for word in task_keywords.copy():  # copy чтобы не изменять во время итерации
        if word in flexible_skill_mappings:
            task_keywords.update(flexible_skill_mappings[word])
    
    logger.info(f"[FIND_RELEVANT] Task keywords: {task_keywords}")
    
    # ENRICHMENT: Добавляем LTM interests + search history для расширения поиска
    try:
        ltm_data = json.loads(user.long_term_memory) if user.long_term_memory else {}
        ltm_interests = ltm_data.get('interests', {})
        if ltm_interests:
            top_interests = sorted(ltm_interests.items(), key=lambda x: x[1], reverse=True)[:5]
            for topic, weight in top_interests:
                if len(topic) >= 3 and weight >= 3:
                    task_keywords.add(topic.lower().strip())
        search_history = ltm_data.get('search_history', [])
        for entry in search_history[-10:]:
            for topic in entry.get('topics', []):
                if len(topic) >= 3:
                    task_keywords.add(topic.lower().strip())
    except Exception as e:
        logger.debug(f"Failed to parse LTM for task keywords: {e}")
    
    # Получить город пользователя для приоритизации
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    def _get_city_variants(obj):
        """Return set of all city name variants (EN normalized, RU normalized, raw) in lowercase."""
        vs = set()
        for attr in ('city_normalized', 'city_normalized_ru', 'city'):
            v = (getattr(obj, attr, None) or '').strip().lower()
            if v:
                vs.add(v)
        return vs
    user_city_variants = _get_city_variants(user_profile) if user_profile else set()
    user_city = next(iter(user_city_variants), None)  # primary city value for display
    
    # Определить тип активности (оффлайн = город критичен)
    offline_keywords = {'пробежка', 'бег', 'бегать', 'тренировка', 'зал', 'спорт', 'йога', 'плавание', 
                        'встреча', 'кофе', 'прогулка', 'футбол', 'баскетбол', 'волейбол', 'теннис'}
    is_offline_activity = bool(task_keywords & offline_keywords)
    
    # Получить всех потенциальных партнеров
    all_partners = get_partners_list(user_id=user.id, session=session)
    
    if not all_partners:
        if close_session:
            session.close()
        if lang == 'en':
            return """ No contacts found in the network for this task.

 Recommendations:
• Fill in your profile (interests, skills, goals)
• Add your city information
• Describe how you can help others

Once profiles are filled, I'll be able to suggest suitable people for collaboration."""
        return """ В сети пока нет контактов для этой задачи.

 Рекомендации:
• Заполни профиль (интересы, навыки, цели)
• Добавь информацию о своем городе
• Опиши, чем можешь помочь другим

Когда профили будут заполнены, я смогу предложить подходящих людей для сотрудничества."""
    
    # Найти релевантные контакты
    relevant_contacts = []

    # Pre-fetch all partner User objects (batch, avoid N+1 in reverse_matches/task loops)
    _frt_ap_uids = [p.user_id for p in all_partners]
    _frt_ap_users = session.query(User).filter(User.id.in_(_frt_ap_uids)).all()
    _frt_ap_user_by_id = {u.id: u for u in _frt_ap_users}

    # Batch-load Goal objects for all partners (avoid N+1 in ПРИОРИТЕТ 4.5 loop)
    _frt_goals_all = session.query(Goal).filter(
        Goal.user_id.in_(_frt_ap_uids),
        Goal.status.in_(['active', 'in_progress'])
    ).all() if _frt_ap_uids else []
    _frt_goals_by_uid: dict = {}
    for _fg in _frt_goals_all:
        _frt_goals_by_uid.setdefault(_fg.user_id, []).append(_fg)

    for partner in all_partners:
        relevance_score = 0
        match_reasons = []
        
        # ПРИОРИТЕТ 1: Город (особенно для оффлайн активностей)
        partner_city_variants = _get_city_variants(partner)
        partner_city = next(iter(partner_city_variants), None)
        same_city = bool(user_city_variants & partner_city_variants)
        
        if same_city:
            if is_offline_activity:
                relevance_score += 15  # Критично для спорта/встреч
                match_reasons.append(f"{'same city' if lang == 'en' else 'тот же город'} ({partner.city})")
            else:
                relevance_score += 5  # Полезно для онлайн активностей
        elif is_offline_activity and user_city and partner_city:
            # Для оффлайн активностей разные города - сильный минус
            relevance_score -= 10
        
        # ПРИОРИТЕТ 2: Навыки (для профессиональных задач)
        if hasattr(partner, 'skills') and partner.skills:
            partner_skills = set(s.lower().strip() for s in partner.skills.split(','))
            skill_match = task_keywords & partner_skills
            if skill_match:
                relevance_score += len(skill_match) * 8  # Навыки очень важны
                match_reasons.append(f"{'skills' if lang == 'en' else 'навыки'}: {', '.join(list(skill_match)[:2])}")
        
        # ПРИОРИТЕТ 3: Интересы
        if hasattr(partner, 'interests') and partner.interests:
            partner_interests = set(i.lower().strip() for i in partner.interests.split(','))
            interest_match = task_keywords & partner_interests
            if interest_match:
                relevance_score += len(interest_match) * 4
                match_reasons.append(f"{'interests' if lang == 'en' else 'интересы'}: {', '.join(list(interest_match)[:2])}")
        
        # ПРИОРИТЕТ 4: Цели контакта совпадают с задачей пользователя
        if hasattr(partner, 'goals') and partner.goals:
            partner_goals = set(g.lower().strip() for g in partner.goals.split(','))
            goal_match = task_keywords & partner_goals
            if goal_match:
                relevance_score += len(goal_match) * 6  # Цели важны
                match_reasons.append(f"{'goals' if lang == 'en' else 'цели'}: {', '.join(list(goal_match)[:2])}")
        
        # ПРИОРИТЕТ 4.5: Структурированные цели (Goal table, из batch-карты)
        try:
            partner_goals_db = _frt_goals_by_uid.get(partner.user_id, [])
            if partner_goals_db:
                for pg in partner_goals_db:
                    goal_text = ((pg.title or '') + ' ' + (pg.description or '') + ' ' + (pg.category or '')).lower()
                    goal_words = set(w for w in goal_text.split() if len(w) >= 4)
                    goal_kw_match = task_keywords & goal_words
                    if goal_kw_match:
                        relevance_score += len(goal_kw_match) * 5
                        match_reasons.append(f"{'goal' if lang == 'en' else 'цель'} «{pg.title[:30]}»")
                        break  # Одного совпадения достаточно
        except Exception as e:
            logger.debug(f"Failed to compare partner goals: {e}")
        
        # Используем уже вычисленную релевантность из get_partners_list
        if hasattr(partner, 'task_relevance_score') and partner.task_relevance_score > 0:
            relevance_score += partner.task_relevance_score
        
        if relevance_score > 0:
            partner_user = _frt_ap_user_by_id.get(partner.user_id)
            if partner_user and partner_user.username:
                partner_lang = get_user_lang_by_db_id(partner.user_id, session=session)
                relevant_contacts.append({
                    'username': partner_user.username,
                    'name': partner_user.username,
                    'interests': partner.interests or '',
                    'skills': partner.skills or '',
                    'city': partner.city or '',
                    'city_normalized': getattr(partner, 'city_normalized', None) or '',
                    'city_normalized_ru': getattr(partner, 'city_normalized_ru', None) or '',
                    'score': relevance_score,
                    'reasons': match_reasons,
                    'lang': partner_lang,
                })
    
    # НОВАЯ ЛОГИКА СОРТИРОВКИ: способствовать росту через всю базу данных
    # Город - бонус, но не ограничение для максимального развития

    # Сортируем по релевантности: (1) score, (2) город (бонус)
    def contact_sort_key(contact):
        # Основной скор релевантности
        base_score = contact['score']

        # Бонус за тот же город (cross-language: EN/RU/raw варианты)
        city_bonus = 0
        contact_city_variants = {v for v in (
            (contact.get('city') or '').lower().strip(),
            (contact.get('city_normalized') or '').lower().strip(),
            (contact.get('city_normalized_ru') or '').lower().strip(),
        ) if v}
        if user_city_variants & contact_city_variants:
            if is_offline_activity:
                city_bonus = 3  # Бонус для оффлайн активностей
            else:
                city_bonus = 1  # Маленький бонус для онлайн

        return (base_score + city_bonus, base_score, city_bonus)

    sorted_contacts = sorted(relevant_contacts, key=contact_sort_key, reverse=True)

    logger.info(f"[FIND_RELEVANT] Total relevant contacts found: {len(sorted_contacts)} (using full database for growth)")
    
    # ДВУСТОРОННИЙ АНАЛИЗ: кому пользователь может помочь
    reverse_matches = []
    if user_profile and user_profile.skills:
        user_skills_set = set(s.strip().lower() for s in user_profile.skills.split(','))
        for partner in all_partners:
            partner_user = _frt_ap_user_by_id.get(partner.user_id)
            if not partner_user or not partner_user.username:
                continue
            
            score = 0
            reasons = []
            # Навыки пользователя совпадают с целями контакта
            if hasattr(partner, 'goals') and partner.goals:
                partner_goals_set = set(g.strip().lower() for g in partner.goals.split(','))
                overlap = user_skills_set & partner_goals_set
                if overlap:
                    score += len(overlap) * 3
                    reasons.append(f"{'needs your skills' if lang == 'en' else 'нуждается в твоих навыках'}: {', '.join(list(overlap)[:2])}")
            # Навыки пользователя совпадают с интересами контакта
            if hasattr(partner, 'interests') and partner.interests:
                partner_interests_set = set(i.strip().lower() for i in partner.interests.split(','))
                overlap = user_skills_set & partner_interests_set
                if overlap:
                    score += len(overlap) * 2
                    reasons.append(f"{'interested in your expertise' if lang == 'en' else 'интересуется тем, в чем ты эксперт'}")
            
            if score > 0:
                partner_lang = get_user_lang(partner_user.telegram_id) if partner_user.telegram_id else 'ru'
                reverse_matches.append({
                    'username': partner_user.username,
                    'city': partner.city or '',
                    'score': score,
                    'reasons': reasons,
                    'lang': partner_lang
                })
    
    reverse_matches.sort(key=lambda x: x['score'], reverse=True)
    
    # УЧЕТ СУЩЕСТВУЮЩИХ ЗАДАЧ ПОЛЬЗОВАТЕЛЯ: предложить партнеров для активных задач
    user_tasks_suggestions = []
    if user_profile and user_profile.interests:
        # Получить активные задачи пользователя
        active_tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
        
        for task in active_tasks:
            task_title_lower = task.title.lower()
            # Проверить, подходит ли задача для поиска партнеров (спорт, обучение, бизнес)
            if any(keyword in task_title_lower for keyword in ['пробежка', 'бег', 'тренировка', 'спорт', 'йога', 'плавание', 'футбол', 'обучение', 'курс', 'программирование', 'стартап', 'бизнес']):
                # Найти партнеров для этой задачи
                task_contacts = []
                for partner in all_partners:
                    partner_user = _frt_ap_user_by_id.get(partner.user_id)
                    if not partner_user or not partner_user.username:
                        continue
                    
                    # Простая проверка совпадения интересов/навыков с задачей
                    partner_interests = set(i.lower().strip() for i in (partner.interests or '').split(','))
                    partner_skills = set(s.lower().strip() for s in (partner.skills or '').split(','))
                    
                    task_words = set(w.lower() for w in task.title.split() if len(w) > 2)
                    if task_words & (partner_interests | partner_skills):
                        task_contacts.append(partner_user.username)
                
                if task_contacts:
                    user_tasks_suggestions.append({
                        'task': task.title,
                        'contacts': task_contacts[:3]  # Максимум 3 контакта на задачу
                    })
    
    if close_session:
        session.close()
    
    # Формирование ответа
    result_lines = []
    
    if sorted_contacts:
        header = " Who can help you:" if lang == 'en' else " Кто может помочь тебе:"
        result_lines.append(header)
        top_contacts = sorted_contacts[:min(3, limit)]
        for i, contact in enumerate(top_contacts, 1):
            badge = get_lang_badge(contact.get('lang', 'ru'))
            line = f"• {badge} @{contact['username']}"
            if contact['reasons']:
                line += f" — {', '.join(contact['reasons'][:2])}"
            if contact['city']:
                line += f" | {contact['city']}"
            result_lines.append(line)
    
    if reverse_matches:
        if result_lines:
            result_lines.append("")
        header = " Who you can help:" if lang == 'en' else " Кому ты можешь помочь:"
        result_lines.append(header)
        for i, contact in enumerate(reverse_matches[:min(3, limit)], 1):
            badge = get_lang_badge(contact.get('lang', 'ru'))
            line = f"• {badge} @{contact['username']}"
            if contact['reasons']:
                line += f" — {', '.join(contact['reasons'][:2])}"
            if contact['city']:
                line += f" | {contact['city']}"
            result_lines.append(line)
    
    if user_tasks_suggestions:
        if result_lines:
            result_lines.append("")
        header = " Also for your tasks:" if lang == 'en' else " Также для твоих задач:"
        result_lines.append(header)
        for suggestion in user_tasks_suggestions:
            contacts_str = ', '.join(f"@{c}" for c in suggestion['contacts'])
            result_lines.append(f"• {suggestion['task']}: {contacts_str}")
    
    # Если контактов мало (< 2) — добавляем хинт об email-кампании для поиска внешних лидов
    all_found_count = len(sorted_contacts) + len(reverse_matches)
    _email_hint = (
        "\n\n💡 Внутренних контактов мало — попробуй поискать нужных людей через интернет "
        "или запустить email-кампанию для автоматического поиска и связи с потенциальными контактами."
    )

    if result_lines:
        result = '\n'.join(result_lines)
        if all_found_count < 2:
            result += _email_hint
        return result
    else:
        if lang == 'en':
            return (
                "No matching contacts found in the internal network for this task.\n\n"
                "💡 Try searching the web for relevant people or launching an email campaign "
                "to automatically find and reach out to potential contacts."
            )
        return (
            "Не нашел подходящих контактов в сети для этой задачи.\n\n"
            "💡 Попробуй поискать нужных людей через интернет или запустить email-кампанию "
            "для автоматического поиска и связи с потенциальными контактами."
        )

async def generate_delegation_notification_async(delegator_username, recipient_username, task_title, task_description, deadline, delegation_details, recipient_telegram_id):
    try:
        from main import bot
        from i18n import get_user_lang
        if not bot:
            return

        lang = get_user_lang(recipient_telegram_id)

        # Generate AI-powered personalized notification
        notification_text = await generate_delegation_notification(
            delegator_username,
            recipient_username,
            task_title,
            task_description,
            deadline,
            delegation_details,
            recipient_telegram_id
        )

        if notification_text:
            message = notification_text
        else:
            # Fallback to template if AI generation fails
            if lang == 'en':
                message = f"New task proposal from @{delegator_username}:\n\n"
                message += f"Task: {task_title}\n"
                if task_description:
                    message += f"Description: {task_description}\n"
                if deadline:
                    message += f"Deadline: {deadline}\n"
                if delegation_details:
                    message += f"Details: {delegation_details}\n"
                message += "\nWrite 'accept task' to confirm or 'reject task' to decline."
            else:
                message = f"Новое предложение задачи от @{delegator_username}:\n\n"
                message += f"Задача: {task_title}\n"
                if task_description:
                    message += f"Описание: {task_description}\n"
                if deadline:
                    message += f"Дедлайн: {deadline}\n"
                if delegation_details:
                    message += f"Детали: {delegation_details}\n"
                message += "\nНапишите боту 'принять задачу' для подтверждения или 'отклонить задачу' для отказа."

        await bot.send_message(recipient_telegram_id, message)

    except Exception as e:
        logging.error(f"Failed to send delegation notification: {e}")

async def generate_delegation_notification(delegator_username, recipient_username, task_title, task_description, deadline, delegation_details, user_id):
    import aiohttp
    from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
    from .prompts import get_extended_system_prompt
    from .utils import clean_technical_details
    from i18n import get_user_lang

    try:
        lang = get_user_lang(user_id)
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None, None, None, None, None, None, user_id, lang=lang)

        if lang == 'en':
            prompt = f"""Create a personalized and motivating notification about a delegated task.

CONTEXT:
- Sender: @{delegator_username}
- Recipient: @{recipient_username}
- Task: {task_title}
- Description: {task_description or 'Not specified'}
- Deadline: {deadline or 'Not specified'}
- Delegation details: {delegation_details or 'Not specified'}

REQUIREMENTS:
1. Be friendly and motivating
2. Emphasize the importance of the task for the team/project
3. Mention the deadline if provided
4. Add a call to action (accept/reject)
5. Make the message personalized
6. No more than 300 characters

RESPONSE FORMAT:
Return only the notification text, without additional comments."""
        else:
            prompt = f"""Создай персонализированное и мотивирующее уведомление о делегированной задаче.

КОНТЕКСТ:
- Отправитель: @{delegator_username}
- Получатель: @{recipient_username}
- Задача: {task_title}
- Описание: {task_description or 'Не указано'}
- Дедлайн: {deadline or 'Не указан'}
- Детали делегирования: {delegation_details or 'Не указаны'}

ТРЕБОВАНИЯ К УВЕДОМЛЕНИЮ:
1. Будь дружелюбным и мотивирующим
2. Подчеркни важность задачи для команды/проекта
3. Упомяни дедлайн если он есть
4. Добавь призыв к действию (принять/отклонить)
5. Сделай сообщение персонализированным
6. Не более 300 символов

ФОРМАТ ОТВЕТА:
Верни только текст уведомления, без дополнительных комментариев."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.8, "max_tokens": 200}

        async with aiohttp.ClientSession() as aio_session:
            async with aio_session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    return content.strip()
                else:
                    logger.error(f"AI notification generation failed: {response.status}")
                    return None

    except Exception as e:
        logger.error(f"Error generating delegation notification: {e}")
        return None

async def generate_progress_request(task_title, delegator_username, time_remaining, user_id):
    import aiohttp
    from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
    from .prompts import get_extended_system_prompt
    from .utils import clean_technical_details

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None, None, None, None, None, None, user_id, lang='ru')

        prompt = """Создай запрос о прогрессе выполнения делегированной задачи.

КОНТЕКСТ:
- Задача: {task_title}
- Отправитель: @{delegator_username}
- Осталось времени: {time_remaining}

ТРЕБОВАНИЯ К ЗАПРОСУ:
1. Будь вежливым и не навязчивым
2. Спроси о текущем прогрессе (в процентах или описательно)
3. Уточни, есть ли сложности или нужна помощь
4. Напомни об оставшемся времени
5. Не более 200 символов

ФОРМАТ ОТВЕТА:
Верни только текст запроса."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 150}

        async with aiohttp.ClientSession() as aio_session:
            async with aio_session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    return content.strip()
                else:
                    logger.error(f"AI progress request generation failed: {response.status}")
                    return None

    except Exception as e:
        logger.error(f"Error generating progress request: {e}")
        return None

async def generate_delegation_response_notification_async(task_title, response, delegator_telegram_id, delegatee_username):
    try:
        from main import bot
        if not bot:
            return

        if response == "accepted":
            message = f" Отлично! Пользователь @{delegatee_username} принял вашу задачу '{task_title}' и добавил её в свой список задач."
        elif response.startswith("rejected"):
            reason = response.replace("rejected", "").strip()
            if reason:
                message = f" Пользователь @{delegatee_username} отклонил задачу '{task_title}'. Причина: {reason}"
            else:
                message = f" Пользователь @{delegatee_username} отклонил задачу '{task_title}'."
        else:
            message = f" Статус задачи '{task_title}' изменён пользователем @{delegatee_username}: {response}"

        await bot.send_message(delegator_telegram_id, message)

    except Exception as e:
        logging.error(f"Failed to send delegation response notification: {e}")

def schedule_delegation_monitoring(task_id, delegator_id, recipient_id, deadline):
    """Schedule delegation monitoring with three progress checkpoints for all tasks"""
    try:
        from reminder_service import REMINDER_SERVICE
        if not REMINDER_SERVICE:
            logger.warning("Reminder service not available for delegation monitoring")
            return

        if not deadline:
            logger.info(f"No deadline for task {task_id}, skipping monitoring")
            return

        current_time = datetime.now(pytz.UTC)
        
        # Ensure deadline is timezone-aware
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=pytz.UTC)
        
        time_until_deadline = deadline - current_time

        # Convert to hours for easier calculation
        hours_until_deadline = time_until_deadline.total_seconds() / 3600

        logger.info(f"Task {task_id} has {hours_until_deadline:.1f} hours until deadline")

        # For ALL tasks: schedule three checkpoints
        # 1. First checkpoint at 1/3 of the deadline
        # 2. Second checkpoint at 2/3 of the deadline
        # 3. Final overdue check 1 day after deadline

        check_times = [
            current_time + (time_until_deadline * 1 / 3),  # 1/3 point
            current_time + (time_until_deadline * 2 / 3),  # 2/3 point
        ]

        for i, check_time in enumerate(check_times, 1):
            if check_time > current_time:
                logger.info(f"Scheduling progress check {i}/2 for task {task_id} at {check_time}")

                REMINDER_SERVICE.schedule_delegation_check(
                    task_id=task_id,
                    check_time=check_time,
                    delegator_id=delegator_id,
                    recipient_id=recipient_id,
                    task_title="Делегированная задача",
                    check_type="progress_request"
                )

        # Always schedule final overdue check 1 day after deadline
        overdue_check = deadline + timedelta(days=1)
        if overdue_check > current_time:
            REMINDER_SERVICE.schedule_delegation_check(
                task_id=task_id,
                check_time=overdue_check,
                delegator_id=delegator_id,
                recipient_id=recipient_id,
                task_title="Делегированная задача",
                check_type="overdue_reminder"
            )
            logger.info(f"Scheduled overdue check for task {task_id} at {overdue_check}")

        logger.info(f"Scheduled three-checkpoint delegation monitoring for task {task_id}")
    except Exception as e:
        logger.error(f"Failed to schedule delegation monitoring for task {task_id}: {e}")

def check_delegation_deadlines():
    """Check for overdue delegated tasks and send reminders"""
    session = Session()
    try:
        current_time = datetime.now(pytz.UTC)

        # Find accepted delegated tasks that are overdue
        overdue_tasks = session.query(Task).filter(
            Task.delegation_status == "accepted",
            Task.status != "completed",
            Task.reminder_time < current_time
        ).all()

        for task in overdue_tasks:
            try:
                # Reminder functionality for delegated tasks is handled by the reminder service
                # End of task processing
                pass

            except Exception as e:
                logger.error(f"Error processing overdue task {task.id}: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()

    except Exception as e:
        logger.error(f"Error in check_delegation_deadlines: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()

def create_subscription_payment(tier=None, user_id=None, session=None):
    """Создать платёж для пополнения токенов (legacy, перенаправляет на токены)"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id, 'light')
        return f"Ссылка на пополнение токенов: {payment_url}"
    except Exception as e:
        return f"Ошибка создания платежа: {str(e)}"

def cancel_subscription(user_id=None):
    """Cancel subscription"""
    from subscription_service import cancel_subscription as cancel_sub

    try:
        success = cancel_sub(user_id)
        if success:
            return "Подписка успешно отменена."
        else:
            return "Подписка не найдена или уже отменена."
    except Exception as e:
        return f"Ошибка отмены подписки: {str(e)}"

async def delete_task(task_id=None, task_title=None, reason=None, user_id=None, session=None, close_session=True) -> str:
    """Delete a task by ID or title search
    
    Args:
        task_id: ID задачи (опционально)
        task_title: Название или часть названия задачи (опционально)
        reason: Причина удаления (опционально)
        user_id: telegram_id пользователя
        session: Сессия БД
        close_session: Закрывать ли сессию (если создана внутри)
    """
    logger.info(f"[DELETE_TASK] Called with task_id={task_id}, task_title='{task_title}', reason='{reason}', user_id={user_id}")
    
    if user_id is None:
        return "ERROR: user_id не может быть None"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        task = None
        
        # Поиск по ID
        if task_id is not None:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter(
                    Task.id == task_id_int,
                    Task.user_id == user.id
                ).first()
            except (ValueError, TypeError):
                logger.warning(f"[DELETE_TASK] Invalid task_id: {task_id}")
        
        # Поиск по названию
        if task is None and task_title:
            task = find_task_flexible(session, user, task_id=None, task_title=task_title)
        
        # Если ничего не найдено - последняя задача
        if task is None and not task_id and not task_title:
            task = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status != "completed"
            ).order_by(Task.created_at.desc()).first()
        
        if not task:
            search_term = task_title or task_id or "неизвестно"
            return f"Задача '{search_term}' не найдена."
        
        task_name = task.title
        task_db_id = task.id
        
        # Отменяем ВСЕ запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and hasattr(REMINDER_SERVICE, 'scheduler'):
                for prefix in [f"reminder_{task_db_id}", f"followup_{task_db_id}", f"result_check_{task_db_id}"]:
                    try:
                        if REMINDER_SERVICE.scheduler.get_job(prefix):
                            REMINDER_SERVICE.scheduler.remove_job(prefix)
                            logger.info(f"[DELETE_TASK] Removed job {prefix}")
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                # Чекпоинты
                for ctype in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    cjob = f"task_overdue_{task_db_id}_{ctype}_{user_id}"
                    try:
                        if REMINDER_SERVICE.scheduler.get_job(cjob):
                            REMINDER_SERVICE.scheduler.remove_job(cjob)
                            logger.info(f"[DELETE_TASK] Removed checkpoint job {cjob}")
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                cp13 = f"task_checkpoint_{task_db_id}_1_3_{user_id}"
                try:
                    if REMINDER_SERVICE.scheduler.get_job(cp13):
                        REMINDER_SERVICE.scheduler.remove_job(cp13)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
        except ImportError:
            pass
        
        # Сбрасываем current_task_id у ВСЕХ пользователей, которые ссылаются на эту задачу
        # (иначе FK constraint не даст удалить)
        users_with_this_task = session.query(User).filter(User.current_task_id == task_db_id).all()
        for u in users_with_this_task:
            u.current_task_id = None
            logger.info(f"[DELETE_TASK] Reset current_task_id for user {u.telegram_id}")
        
        # Удаляем дочерние задачи (рекурентные инстансы с parent_task_id)
        # Иначе FK constraint на parent_task_id не даст удалить родителя
        child_tasks = session.query(Task).filter(Task.parent_task_id == task_db_id).all()
        if child_tasks:
            # Batch-reset current_task_id for all child tasks (avoid N+1)
            _child_ids = [c.id for c in child_tasks]
            _child_users = session.query(User).filter(User.current_task_id.in_(_child_ids)).all()
            for _cu in _child_users:
                _cu.current_task_id = None
        for child in child_tasks:
            session.delete(child)
            logger.info(f"[DELETE_TASK] Deleted child task ID: {child.id}")
        
        # Мягкое удаление (soft-delete): ставим статус 'cancelled' + время удаления
        # чтобы статистика "удалённых задач" корректно считалась
        from datetime import datetime as _dt_del
        import pytz as _pytz_del
        task.status = 'cancelled'
        task.actual_completion_time = _dt_del.now(_pytz_del.UTC)
        session.commit()

        # === Векторная память: удаляем вектор задачи ===
        try:
            from ai_integration.vector_memory import get_pinecone_index as _get_pc_idx
            _pc_idx = _get_pc_idx()
            if _pc_idx:
                user_obj = session.query(User).filter_by(telegram_id=user_id).first()
                _ns = f"user_{user_id}"
                _pc_idx.delete(filter={'type': 'task', 'task_id': str(task_db_id)}, namespace=_ns)
                logger.debug(f"[DELETE_TASK] Pinecone vectors cleaned for task_id={task_db_id}")
        except Exception as _e:
            logger.debug(f"[DELETE_TASK] Vector memory cleanup skipped: {_e}")

        logger.info(f"[DELETE_TASK] Task '{task_name}' (ID: {task_db_id}) soft-deleted (status=cancelled)")

        reason_text = f" Причина: {reason}" if reason else ""
        return f"Задача '{task_name}' удалена.{reason_text}"
    
    except Exception as e:
        logger.error(f"[DELETE_TASK] Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            session.rollback()
        except Exception:
            pass
        return f"Ошибка при удалении задачи: {str(e)}"
    finally:
        if close_session:
            session.close()

def get_task_details(task_id=None, task_title=None, user_id=None, session=None):
    """Get detailed information about a task"""
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден."

        # Поиск по названию если task_title указан
        if task_title and not task_id:
            task = find_task_flexible(session, user, task_id=None, task_title=task_title)
            if task:
                task_id = task.id
            else:
                if close_session:
                    session.close()
                return f"Задача с названием '{task_title}' не найдена"

        # Find task by ID
        if task_id:
            try:
                task_id_int = int(task_id)
            except (ValueError, TypeError):
                if close_session:
                    session.close()
                return f"Некорректный ID задачи: {task_id}"

            task = (
                session.query(Task)
                .filter(
                    or_(
                        and_(Task.id == task_id_int, Task.user_id == user.id),
                        and_(Task.id == task_id_int, Task.delegated_to_username.ilike((user.username or '').replace('@', '')), Task.delegation_status == "accepted")
                    )
                )
                .first()
            )
        else:
            if close_session:
                session.close()
            return "Не указан ID задачи."

        if task:
            # Format detailed task information
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            
            details = " Подробная информация о задаче:\n\n"
            details += f"🆔 ID: {task.id}\n"
            details += f" Название: {task.title}\n"
            
            if task.description:
                details += f" Описание: {decrypt_data(task.description)}\n"
            
            details += f" Статус: {task.status}\n"
            
            if task.reminder_time:
                local_time = _utc_to_local(task.reminder_time, user_tz)
                details += f" Время напоминания: {local_time.strftime('%d.%m.%Y %H:%M')} ({user_tz.zone})\n"
            
            if task.due_date:
                local_due = _utc_to_local(task.due_date, user_tz)
                details += f" Дедлайн: {local_due.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.delegated_to_username:
                details += f" Поручено: @{task.delegated_to_username}\n"
                details += f" Статус делегирования: {task.delegation_status or 'Не определён'}\n"
                if task.delegation_details:
                    details += f" Детали делегирования: {task.delegation_details}\n"
            
            if task.completion_notes:
                details += f" Заметки о выполнении: {decrypt_data(task.completion_notes)}\n"
            
            if task.actual_completion_time:
                local_completion = _utc_to_local(task.actual_completion_time, user_tz)
                details += f" Фактическое время выполнения: {local_completion.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.recommendations:
                try:
                    import json
                    recs = json.loads(task.recommendations)
                    if recs:
                        details += " Рекомендации AI:\n"
                        for i, rec in enumerate(recs[:3], 1):
                            details += f"  {i}. {rec}\n"
                except Exception as e:
                    logger.warning(f"[TASKDETAILS] Error parsing recommendations: {e}")
            
            details += f" Создана: {_utc_to_local(task.created_at, user_tz).strftime('%d.%m.%Y %H:%M')}\n"
            
            if close_session:
                session.close()
            return details
        else:
            if close_session:
                session.close()
            return f"Задача с ID {task_id} не найдена."

    except Exception as e:
        logger.error(f"Error in get_task_details: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        if close_session and 'session' in locals():
            session.close()
        return f"Ошибка при получении деталей задачи: {str(e)}"

# Function removed

def delegate_task_with_session(title, description, reminder_time, delegated_to_username, delegation_details="", user_id=None, session=None):
    """Delegate a task to another user"""
    logger.info(f"[DELEGATE_TASK] Called with title='{title}', delegated_to='{delegated_to_username}', user_id={user_id}")
    
    if user_id is None:
        logger.error("[DELEGATE_TASK] ERROR: user_id is None!")
        return "ERROR: user_id is required"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    # Check user subscription for delegation
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден"
    
    # Validate input parameters
    if not title or title.strip() == "":
        logger.error("[DELEGATE_TASK] title is empty or None")
        return "ERROR: Название задачи не может быть пустым"
    
    if not delegated_to_username or delegated_to_username.strip() == "":
        logger.error("[DELEGATE_TASK] delegated_to_username is empty or None")
        return "ERROR: Получатель не указан"
    
    # Validate reminder_time
    if not reminder_time:
        return "Для делегирования задачи требуется точная дата и время дедлайна. Пожалуйста, уточните: на какое точное время и дату поставить дедлайн? (Например: '2026-01-10 15:00' или 'завтра в 14:30')"
    
    # Validate reminder_time format
    if reminder_time:
        try:
            datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
        except ValueError:
            logger.info(f"[DELEGATE_TASK] Parsing relative time: {reminder_time}")
            parsed_time = parse_time_to_datetime(reminder_time, user_id)
            if parsed_time:
                reminder_time = parsed_time
                logger.info(f"[DELEGATE_TASK] Parsed to: {reminder_time}")
            else:
                return f"Некорректный формат времени '{reminder_time}'. Укажите точное время в формате YYYY-MM-DD HH:MM (например: 2026-01-10 15:00)"
    
    # Find delegated user
    delegated_username = delegated_to_username.lstrip('@')
    delegated_user = session.query(User).filter_by(username=delegated_username).first()
    if not delegated_user:
        if close_session:
            session.close()
        return f"Пользователь @{delegated_username} не найден в системе"
    
    # Create delegated task
    task = Task(
        user_id=delegated_user.id,  # Получатель задачи
        title=title,
        description=encrypt_data(description),
        delegated_by=user.id,  # ВАЖНО: кто делегировал задачу
        delegated_to_username=delegated_username,  # Сохраняем БЕЗ @
        delegation_details=encrypt_data(delegation_details) if delegation_details else None,
        status="pending",
        delegation_status="pending"
    )
    
    # Parse reminder_time
    if reminder_time:
        try:
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            
            # Если reminder_time уже datetime (после parse_time_to_datetime), используем напрямую
            if isinstance(reminder_time, datetime):
                if reminder_time.tzinfo is None:
                    reminder_time = user_tz.localize(reminder_time)
                task.reminder_time = reminder_time.astimezone(pytz.UTC)
            else:
                # Try different string formats
                for fmt in ["%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%H:%M"]:
                    try:
                        if "завтра" in reminder_time.lower():
                            local_dt = datetime.now(user_tz) + timedelta(days=1)
                            time_part = reminder_time.lower().replace("завтра", "").strip()
                            if time_part:
                                time_dt = datetime.strptime(time_part, "%H:%M")
                                local_dt = local_dt.replace(hour=time_dt.hour, minute=time_dt.minute)
                        elif "сегодня" in reminder_time.lower():
                            local_dt = datetime.now(user_tz)
                            time_part = reminder_time.lower().replace("сегодня", "").strip()
                            if time_part:
                                time_dt = datetime.strptime(time_part, "%H:%M")
                                local_dt = local_dt.replace(hour=time_dt.hour, minute=time_dt.minute)
                        else:
                            local_dt = datetime.strptime(reminder_time, fmt)
                            if user.timezone:
                                local_dt = user_tz.localize(local_dt)
                        
                        task.reminder_time = local_dt.astimezone(pytz.UTC)
                        break
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"[DELEGATE_TASK] Could not parse reminder_time '{reminder_time}': {e}")
            import traceback
            traceback.print_exc()
            session.rollback()
    
    session.add(task)
    session.commit()
    
    if close_session:
        session.close()
    
    return f"Задача '{title}' делегирована пользователю @{delegated_username}"

def suggest_trends_and_opportunities(user_id=None, focus_area=None, num_suggestions=3, session=None):
    """Предложить новые тренды и возможности развития на основе профиля пользователя"""
    logger.info(f"[SUGGEST_TRENDS] Called with user_id={user_id}, focus_area='{focus_area}', num_suggestions={num_suggestions}")

    if user_id is None:
        return "Необходимо указать user_id"

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Получаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Базовые тренды по областям
        trends_data = {
            'career': [
                "Удаленная работа и гибридный формат",
                "ИИ-инструменты для повышения продуктивности",
                "Фриланс и цифровой номадизм",
                "Непрерывное обучение и сертификации",
                "Экологичное предпринимательство",
                "Креативные индустрии и NFT",
                "Блокчейн и криптовалюты",
                "Кибербезопасность и защита данных"
            ],
            'personal': [
                "Цифровая детоксикация и mindful living",
                "Экологичный образ жизни",
                "Саморазвитие через подкасты и книги",
                "Спорт и здоровье в метaverse",
                "Путешествия с минимальным воздействием",
                "Цифровое искусство и творчество",
                "Медитация и практики осознанности",
                "Обучение новым навыкам онлайн"
            ],
            'business': [
                "SaaS и облачные сервисы",
                "Электронная коммерция и маркетплейсы",
                "Зеленые технологии и устойчивое развитие",
                "ИИ в бизнес-процессах",
                "Криптоэкономика и DeFi",
                "NFT и цифровые активы",
                "Платформенная экономика",
                "Социальное предпринимательство"
            ],
            'technology': [
                "Искусственный интеллект и машинное обучение",
                "Квантовые вычисления",
                "Блокчейн и Web3",
                "Расширенная реальность (AR/VR)",
                "Интернет вещей (IoT)",
                "Биотехнологии и генная инженерия",
                "Нейронные интерфейсы",
                "Космические технологии"
            ],
            'health': [
                "Персонализированная медицина",
                "Телемедицина и цифровое здоровье",
                "Функциональное питание",
                "Ментальное здоровье и приложения",
                "Биохакинг и longevity",
                "Спортивные гаджеты и wearables",
                "Йога и альтернативные практики",
                "Экологичное питание"
            ],
            'finance': [
                "Криптовалюты и цифровые активы",
                "DeFi и decentralized finance",
                "Персональные финансы и приложения",
                "Зеленые инвестиции",
                "Краудфандинг и краудинвестинг",
                "NFT как инвестиционный актив",
                "Финтех инновации",
                "Пассивный доход онлайн"
            ],
            'education': [
                "Онлайн-образование и платформы",
                "Микро-обучение и геймификация",
                "Виртуальная реальность в обучении",
                "ИИ-тьюторы и персонализация",
                "Блокчейн-сертификаты",
                "Образование для пожилых",
                "Экологическое образование",
                "Креативное мышление и дизайн"
            ],
            'auto': [
                "Электромобили и зарядная инфраструктура",
                "Автопилот и автономный транспорт",
                "Каршеринг и sharing economy",
                "Экологичный транспорт",
                "Умные города и инфраструктура",
                "Дроны и воздушный транспорт",
                "Водородные технологии",
                "Электросамокаты и микромобильность"
            ]
        }

        # Получаем тренды для выбранной области
        if focus_area not in trends_data:
            focus_area = 'personal'  # дефолт

        available_trends = trends_data[focus_area]

        # Персонализируем на основе профиля
        user_interests = []
        user_skills = []

        if profile:
            if profile.interests:
                user_interests = [i.strip().lower() for i in profile.interests.split(',')]
            if profile.skills:
                user_skills = [s.strip().lower() for s in profile.skills.split(',')]

        # Фильтруем и ранжируем тренды на основе интересов пользователя
        scored_trends = []
        for trend in available_trends:
            score = 0
            trend_lower = trend.lower()

            # Проверяем релевантность к интересам
            for interest in user_interests:
                if any(word in trend_lower for word in interest.split()):
                    score += 2

            # Проверяем релевантность к навыкам
            for skill in user_skills:
                if any(word in trend_lower for word in skill.split()):
                    score += 1

            scored_trends.append((trend, score))

        # Сортируем по релевантности
        scored_trends.sort(key=lambda x: x[1], reverse=True)

        # Выбираем топ предложений
        selected_trends = [trend for trend, score in scored_trends[:num_suggestions]]

        # Если мало релевантных, добавляем случайные
        if len(selected_trends) < num_suggestions:
            remaining = [trend for trend, score in scored_trends[num_suggestions:]]
            selected_trends.extend(remaining[:num_suggestions - len(selected_trends)])

        # Формируем ответ
        area_names = {
            'career': 'карьере',
            'personal': 'личном развитии',
            'business': 'бизнесе',
            'technology': 'технологиях',
            'health': 'здоровье',
            'finance': 'финансах',
            'education': 'образовании',
            'auto': 'автомобильной сфере'
        }

        area_name = area_names.get(focus_area, focus_area)

        response = f"Интересные направления в {area_name}:\n\n"
        for i, trend in enumerate(selected_trends, 1):
            response += f"{i}. {trend}\n"

        # Добавляем персонализацию если есть профиль
        if profile and (user_interests or user_skills):
            response += f"\nРекомендации адаптированы под твои интересы: {', '.join(user_interests[:3])}"

        return response

    finally:
        if close_session:
            session.close()

def _merge_similar_goals(current_goals: str, new_goals: str) -> tuple[str, bool, str]:
    """
    Умно объединяет похожие цели, избегая дубликатов.
    
    Args:
        current_goals: Текущие цели через запятую
        new_goals: Новые цели для добавления
        
    Returns:
        (обновленные_цели, было_ли_изменение, описание_изменения)
    """
    if not new_goals or not new_goals.strip():
        return current_goals, False, "Ничего не добавлено"
    
    # Разбираем текущие цели
    current_list = []
    if current_goals:
        current_list = [goal.strip() for goal in current_goals.split(',') if goal.strip()]
    
    # Разбираем новые цели
    new_list = [goal.strip() for goal in new_goals.split(',') if goal.strip()]
    
    # Нормализуем для сравнения (нижний регистр, убираем лишние слова)
    def normalize_goal(goal: str) -> str:
        goal_lower = goal.lower()
        # Убираем общие слова
        remove_words = ['хочу', 'хотелось бы', 'планирую', 'намерен', 'мечтаю', 'стремлюсь', 'желаю']
        for word in remove_words:
            goal_lower = goal_lower.replace(word, '').strip()
        return goal_lower
    
    current_normalized = {normalize_goal(g): g for g in current_list}
    added_goals = []
    
    for new_goal in new_list:
        normalized = normalize_goal(new_goal)
        if normalized not in current_normalized:
            added_goals.append(new_goal)
            current_normalized[normalized] = new_goal
    
    if not added_goals:
        return current_goals, False, "Цели уже есть в профиле"
    
    # Объединяем
    all_goals = current_list + added_goals
    result = ', '.join(all_goals)
    
    return result, True, f"Добавлены новые цели: {', '.join(added_goals)}"

def _add_to_list_field(current_value: str, new_value: str) -> tuple[str, bool]:
    """
    Добавляет новое значение в поле-список (через запятую).
    Возвращает (обновленное_значение, было_ли_добавлено).
    Разбивает new_value по запятым и проверяет каждый элемент на дубликаты.
    """
    if not new_value or not new_value.strip():
        return current_value, False
    
    # Разбираем текущие значения
    if current_value:
        current_items = [item.strip() for item in current_value.split(',')]
        current_items_lower = [item.lower() for item in current_items]
    else:
        current_items = []
        current_items_lower = []
    
    # Разбираем новые значения по запятым
    new_items = [item.strip() for item in new_value.split(',') if item.strip()]
    
    # Фильтруем дубликаты (точные и подстроковые)
    added_items = []
    replaced_in_place = False
    for new_item in new_items:
        new_item_lower = new_item.lower()
        # Точный дубликат
        if new_item_lower in current_items_lower:
            continue
        # Подстроковый дубликат: если новый элемент является частью существующего или наоборот
        is_substring_dup = False
        for idx, existing_lower in enumerate(current_items_lower):
            if new_item_lower in existing_lower:
                # Новый короче существующего — пропускаем
                is_substring_dup = True
                break
            if existing_lower in new_item_lower:
                # Новый длиннее существующего — заменяем на более детальный
                current_items[idx] = new_item
                current_items_lower[idx] = new_item_lower
                is_substring_dup = True
                replaced_in_place = True
                break
        if not is_substring_dup:
            added_items.append(new_item)
            current_items_lower.append(new_item_lower)
    
    if not added_items and not replaced_in_place:
        return current_value, False
    
    # Объединяем со старыми (current_items могут содержать in-place замены)
    if current_items:
        result = ', '.join(current_items + added_items)
    else:
        result = ', '.join(added_items)
    
    return result, True

def update_profile(user_id: int, city: str = None, birth_date: str = None, interests: str = None, skills: str = None, goals: str = None, company: str = None, position: str = None, replace_mode: bool = False, session=None, close_session: bool = True) -> str:
    """
    Обновляет профиль пользователя с новыми данными.
    
    ПО УМОЛЧАНИЮ ДОБАВЛЯЕТ данные в списочные поля (interests, skills, goals).
    Для замены используйте replace_mode=True.

    Args:
        user_id: ID пользователя (telegram_id)
        city: Город пользователя (опционально)
        birth_date: Дата рождения в формате DD.MM.YYYY (опционально)
        interests: Интересы пользователя (опционально) - ДОБАВЛЯЮТСЯ к существующим
        skills: Навыки пользователя (опционально) - ДОБАВЛЯЮТСЯ к существующим
        goals: Цели пользователя (опционально) - ДОБАВЛЯЮТСЯ к существующим
        company: Компания пользователя (опционально)
        position: Должность пользователя (опционально)
        replace_mode: Если True - заменяет данные, если False - добавляет (по умолчанию False)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Сообщение об успешном обновлении
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Проверяем что хотя бы один параметр передан
        has_any_data = any(v is not None for v in [city, birth_date, interests, skills, goals, company, position])
        if not has_any_data:
            return "Ошибка: не передано ни одного параметра. Укажи что обновить: city, skills, interests, goals, company, position."

        # Получаем пользователя по telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return f"Пользователь с ID {user_id} не найден"

        # Получаем или создаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)

        # Обновляем поля если они переданы
        updates = []
        added = []
        
        # Простые поля (заменяются всегда)
        if city is not None:
            profile.city = city
            cleaned = _clean_city_name(city)
            profile.city_normalized = cleaned
            # Обновляем city_normalized_ru — русский вариант через алиасы
            ru_variant = _CITY_ALIASES.get(cleaned, '')
            if ru_variant and any(c in ru_variant for c in 'абвгдежзиклмнопрстуфхцчшщэюя'):
                profile.city_normalized_ru = ru_variant
            elif any(c in cleaned for c in 'абвгдежзиклмнопрстуфхцчшщэюя'):
                profile.city_normalized_ru = cleaned
            else:
                profile.city_normalized_ru = None
            updates.append(f"город: {city}")
            # Обновляем timezone на основе города
            tz = CITY_TIMEZONE_MAP.get(city.lower())
            if tz:
                user.timezone = tz
                updates.append(f"timezone: {tz}")
        if birth_date is not None:
            profile.birthdate = birth_date
            updates.append(f"день рождения: {birth_date}")
        if company is not None:
            from .utils import _normalize_company_name
            company = _normalize_company_name(company)
            profile.company = company
            updates.append(f"компания: {company}")
        if position is not None:
            # Нормализуем падеж: творительный → именительный
            from .utils import _normalize_position_case
            position = _normalize_position_case(position)
            profile.position = position
            updates.append(f"должность: {position}")
        
        # Списочные поля (добавляются или заменяются в зависимости от replace_mode)
        if interests is not None:
            # Нормализуем падеж
            from .utils import _normalize_skills_text
            interests = _normalize_skills_text(interests)
            # Валидация
            # Фильтр: мусорные фразы скопированные из контекста (не интересы)
            garbage_interest_patterns = [
                'и настрой', 'настрой алерт', 'добавь', 'помоги', 'подскажи',
                'сделай', 'поставь', 'напомни', 'создай', 'проверь', 'покажи',
                'расскажи', 'навыки, цели', 'навыки)', 'цели)', 'заполни профиль',
                'нужно', 'будет', 'можно', 'стоит', 'важно', 'отлично',
                'знаю что', 'вижу что', 'понимаю', 'считаю', 'думаю что',
            ]
            if len(interests.strip()) < 2 or len(interests.strip()) > 100:
                logger.warning(f"Invalid interests length: {len(interests)}")
            elif any(pattern in interests.lower() for pattern in ['<script', 'onclick', 'onerror', 'javascript:', 'http://', 'https://']):
                logger.warning(f"Invalid interests content: {interests}")
            elif any(g in interests.lower() for g in garbage_interest_patterns):
                logger.warning(f"[UPDATE_PROFILE] Garbage interests rejected: '{interests}' — looks like copied phrase, not an interest")
            else:
                if replace_mode:
                    profile.interests = interests
                    updates.append(f"интересы заменены: {interests}")
                else:
                    new_value, was_added = _add_to_list_field(profile.interests, interests)
                    if was_added:
                        profile.interests = new_value
                        added.append(f"интерес: {interests}")
                    else:
                        updates.append(f"интерес '{interests}' уже есть")
        
        if skills is not None:
            # Нормализуем падеж
            from .utils import _normalize_skills_text
            skills = _normalize_skills_text(skills)
            # Валидация (исключаем вредоносный контент и мусорные значения)
            # Фильтр: мусорные фразы скопированные из контекста (не навыки)
            garbage_patterns = [
                'реально востребован', 'нужно', 'хочу', 'планирую', 'думаю',
                'будет', 'можно', 'стоит', 'важно', 'интересно', 'отлично',
                'работаю', 'знаю что', 'вижу что', 'понимаю', 'считаю',
                'и интересы', 'и цели', 'навыки)', 'цели)', 'профиль',
            ]
            if len(skills.strip()) < 2 or len(skills.strip()) > 200:
                logger.warning(f"Invalid skills length: {len(skills)}")
            elif any(pattern in skills.lower() for pattern in ['<script', 'http://', 'https://', 'onclick', 'onerror']):
                logger.warning(f"Invalid skills content (suspicious): {skills}")
            elif any(g in skills.lower() for g in garbage_patterns):
                logger.warning(f"[UPDATE_PROFILE] Garbage skills rejected: '{skills}' — looks like copied phrase, not a skill")
            else:
                if replace_mode:
                    profile.skills = skills
                    updates.append(f"навыки заменены: {skills}")
                else:
                    new_value, was_added = _add_to_list_field(profile.skills, skills)
                    if was_added:
                        profile.skills = new_value
                        added.append(f"навык: {skills}")
                    else:
                        updates.append(f"навык '{skills}' уже есть")
        
        if goals is not None:
            # Серверная обрезка: если goals длиннее 50 символов — обрезаем разумно
            if goals and len(goals.strip()) > 50:
                truncated = goals.strip()[:50]
                # Обрезаем по последнему разделителю (точка с запятой, запятая, " и ", пробел)
                for sep in ['; ', ', ', ' и ', ' ']:
                    idx = truncated.rfind(sep)
                    if idx > 10:
                        truncated = truncated[:idx]
                        break
                logger.info(f"[UPDATE_PROFILE] Goals truncated: '{goals}' -> '{truncated}'")
                goals = truncated
            # Чистим начальные глаголы: «использовать X» → «X», «создать Y» → «Y»
            import re as _re_goals
            goals = _re_goals.sub(
                r'^(?:использовать|создать|разработать|внедрить|освоить|изучить|научиться|применять|запустить|начать|попробовать|сделать|дать|автоматизировать|организовать|настроить|подготовить|провести|выполнить)\s+',
                '', goals.strip(), flags=_re_goals.IGNORECASE
            ).strip()
            # Валидация - для replace_mode позволяем пустые строки (удаление)
            if replace_mode and goals.strip() == "":
                # Разрешаем пустую строку для удаления
                profile.goals = goals
                updates.append(f"цели заменены: {goals}")
                # Также удаляем Goal записи из БД (иначе останутся призраки)
                try:
                    from models import Goal
                    deleted_goals = session.query(Goal).filter(
                        Goal.user_id == user.id,
                        Goal.status.in_(['active', 'paused'])
                    ).all()
                    for g in deleted_goals:
                        session.delete(g)
                    if deleted_goals:
                        updates.append(f"удалено Goal записей: {len(deleted_goals)}")
                except Exception as e:
                    logger.warning(f"[UPDATE_PROFILE] Failed to delete Goal records: {e}")
            elif len(goals.strip()) < 2 or len(goals.strip()) > 200:
                logger.warning(f"Invalid goals length: {len(goals)}")
            elif any(pattern in goals.lower() for pattern in ['<script', 'http://', 'https://', 'onclick', 'onerror']):
                logger.warning(f"Invalid goals content (suspicious): {goals}")
            elif any(g in goals.lower() for g in [
                'обсудить', 'поговорить', 'узнать', 'спросить', 'понять',
                'посмотреть', 'попробовать', 'подумать', 'разобраться',
                'как его лучше', 'как лучше', 'чтобы ты', 'чтоб ты',
            ]):
                logger.warning(f"[UPDATE_PROFILE] Garbage goals rejected: '{goals}' — looks like conversational phrase, not a goal")
            else:
                if replace_mode:
                    profile.goals = goals
                    updates.append(f"цели заменены: {goals}")
                else:
                    new_value, was_added = _add_to_list_field(profile.goals, goals)
                    if was_added:
                        profile.goals = new_value
                        added.append(f"цель: {goals}")
                    else:
                        updates.append(f"цель '{goals}' уже есть")

        # Обновляем время последнего обновления
        profile.updated_at = datetime.utcnow()

        session.commit()

        # === Лог активности ===
        try:
            from models import AgentActivityLog as _AAL_up
            _up_changes = (added + updates)
            if _up_changes:
                _up_log = _AAL_up(
                    user_id=user.id,
                    activity_type='profile_updated',
                    title='Профиль обновлён',
                    content=', '.join(_up_changes[:5])[:200],
                    status='completed',
                )
                session.add(_up_log)
                session.commit()
        except Exception as _e:
            logger.warning(f"[UPDATE_PROFILE] Activity log failed: {_e}")

        # Schedule background normalization for cross-language matching
        if added or updates:
            try:
                import asyncio
                from .utils import normalize_profile_background
                loop = asyncio.get_running_loop()
                loop.create_task(normalize_profile_background(profile.user_id))
            except Exception:
                pass  # Non-critical: normalization will happen on next web save

        result_parts = []
        if added:
            result_parts.append(f" Добавлено: {', '.join(added)}")
        if updates:
            result_parts.append(f"Обновлено: {', '.join(updates)}")
        
        if result_parts:
            # === Векторная память ===
            try:
                from ai_integration.vector_memory import store_memory_sync as _vmem_up
                _all_changes_up = added + [u for u in updates if 'уже есть' not in u and 'изменений' not in u]
                if _all_changes_up:
                    _vmem_up(user_id, f"Профиль обновлён: {', '.join(_all_changes_up[:5])}", {'type': 'profile'})
            except Exception as _e:
                logger.debug(f"[UPDATE_PROFILE] Vector memory skipped: {_e}")
            return ' | '.join(result_parts)
        else:
            return "Профиль проверен, изменений не требуется"

    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при обновлении профиля пользователя {user_id}: {e}")
        raise

    finally:
        if close_session:
            session.close()

def smart_update_profile(user_id: int, field: str, value: str, action: str = 'add', session=None, close_session: bool = True) -> str:
    """
    Умное обновление профиля с выбором действия.
    
    Args:
        user_id: ID пользователя (telegram_id)
        field: Поле для обновления ('goals', 'interests', 'skills', 'city', 'company', 'position')
        value: Новое значение
        action: Действие ('add', 'replace', 'merge') - merge только для goals
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения
    
    Returns:
        Сообщение об успешном обновлении
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Получаем пользователя по telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return f"Пользователь с ID {user_id} не найден"

        # Получаем или создаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)

        field_names = {
            'goals': 'цели',
            'interests': 'интересы', 
            'skills': 'навыки',
            'city': 'город',
            'company': 'компания',
            'position': 'должность'
        }
        
        if field not in field_names:
            return f"Неподдерживаемое поле: {field}"
        
        # Обрабатываем разные поля
        if field in ['goals', 'interests', 'skills']:
            # Списочные поля
            if action == 'replace':
                setattr(profile, field, value)
                result = f" {field_names[field]} заменены: {value}"
            elif action == 'merge' and field == 'goals':
                # Умное объединение только для целей
                new_value, was_changed, change_desc = _merge_similar_goals(getattr(profile, field), value)
                if was_changed:
                    setattr(profile, field, new_value)
                    result = f" {change_desc}"
                else:
                    result = f"ℹ {field_names[field]} уже актуальны"
            else:  # add
                new_value, was_added = _add_to_list_field(getattr(profile, field), value)
                if was_added:
                    setattr(profile, field, new_value)
                    result = f" Добавлено в {field_names[field]}: {value}"
                else:
                    result = f"ℹ '{value}' уже есть в {field_names[field]}"
        else:
            # Простые поля
            setattr(profile, field, value)
            result = f" {field_names[field]} обновлен: {value}"
            
            # Специальная обработка для города - обновляем timezone
            if field == 'city':
                tz = CITY_TIMEZONE_MAP.get(value.lower())
                if tz:
                    user.timezone = tz
                    result += f" | timezone: {tz}"

        # Обновляем время последнего обновления
        profile.updated_at = datetime.utcnow()
        session.commit()

        # Schedule background normalization for cross-language matching
        try:
            import asyncio
            from .utils import normalize_profile_background
            loop = asyncio.get_running_loop()
            loop.create_task(normalize_profile_background(profile.user_id))
        except Exception:
            pass  # Non-critical

        # === Векторная память ===
        try:
            from ai_integration.vector_memory import store_memory_sync as _vmem_sp
            _vmem_sp(user_id, f"Профиль обновлён: {field} → {value}", {'type': 'profile', 'field': field})
        except Exception as _e:
            logger.debug(f"[SMART_UPDATE_PROFILE] Vector memory skipped: {_e}")

        return result

    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при умном обновлении профиля пользователя {user_id}: {e}")
        return f"Ошибка: {str(e)}"

    finally:
        if close_session:
            session.close()

def set_activity_alert(activity_type=None, keywords=None, location=None, frequency='any', enabled=True, user_id=None, session=None):
    """Настроить автоматические уведомления об активностях других пользователей
    
    Monitors tasks created by other users and automatically adds information to your next conversation.
    When someone creates a matching task (e.g., running, meetup), AI will naturally mention it in dialogue.
    
    Args:
        activity_type: Type of activity to monitor (e.g., 'пробежка', 'митап по AI')
        keywords: List of keywords to search for in tasks
        location: Optional city filter
        frequency: 'any', 'regular', or 'one_time'
        enabled: Enable (True) or disable (False) the alert
        user_id: Telegram ID of the user
        session: Database session
    
    Returns:
        Success message
    """
    from models import Session, User, ActivityAlert
    import json
    
    logger.info(f"[SET_ACTIVITY_ALERT] user_id={user_id}, type={activity_type}, keywords={keywords}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        if not activity_type or not keywords:
            return "Укажи тип активности и ключевые слова для поиска. Например: 'скажи когда кто-то пойдет на пробежку'"
        
        # Convert keywords to JSON
        if isinstance(keywords, str):
            keywords_list = [k.strip() for k in keywords.split(',')]
        else:
            keywords_list = keywords
        keywords_json = json.dumps(keywords_list, ensure_ascii=False)
        
        # Check if alert already exists
        existing_alert = session.query(ActivityAlert).filter_by(
            user_id=user.id,
            activity_type=activity_type
        ).first()
        
        if existing_alert:
            # Update existing
            existing_alert.keywords = keywords_json
            existing_alert.location = location
            existing_alert.frequency = frequency
            existing_alert.enabled = enabled
            session.commit()
            
            if enabled:
                return f" Обновил уведомление об активности '{activity_type}'. Теперь буду автоматически сообщать когда кто-то планирует такую активность!"
            else:
                return f"Уведомление об активности '{activity_type}' отключено."
        else:
            # Create new
            alert = ActivityAlert(
                user_id=user.id,
                activity_type=activity_type,
                keywords=keywords_json,
                location=location,
                frequency=frequency,
                enabled=enabled
            )
            session.add(alert)
            session.commit()
            
            keywords_str = ', '.join(keywords_list)
            location_str = f" в {location}" if location else ""
            return f" Настроил автоматическое уведомление! Буду следить за активностями '{activity_type}'{location_str}. Когда кто-то создаст задачу по ключевым словам ({keywords_str}), я естественно упомяну это в нашем следующем диалоге. Никаких навязчивых уведомлений!"
        
    except Exception as e:
        logger.error(f"[SET_ACTIVITY_ALERT] Error: {e}", exc_info=True)
        return f"Ошибка настройки уведомления: {str(e)}"
    finally:
        if close_session:
            session.close()

def set_contact_alert(skill=None, interest=None, city=None, position=None, enabled=True, user_id=None, session=None):
    """Set up automatic alerts for new users with specific skills/interests (all tiers)
    
    Monitors new user registrations and profile updates, automatically adds information to your next conversation.
    When someone with matching skills/interests joins, AI will naturally mention them in dialogue.
    
    Args:
        skill: Skill to search for (e.g., 'продажи', 'Python')
        interest: Interest to search for (e.g., 'стартапы', 'ИИ')
        city: Optional city filter
        position: Optional position/role filter
        enabled: Enable (True) or disable (False) the alert
        user_id: Telegram ID of the user
        session: Database session
    
    Returns:
        Success message
    """
    from models import Session, User, ContactAlert
    
    logger.info(f"[SET_CONTACT_ALERT] user_id={user_id}, skill={skill}, interest={interest}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        # Алерты доступны всем тарифам
        
        if not skill and not interest:
            return "Укажи навык или интерес для поиска. Например: 'скажи когда появится специалист по продажам' или 'предупреди о программистах на Python'"
        
        # Check if alert already exists
        existing_alert = session.query(ContactAlert).filter_by(
            user_id=user.id,
            skill=skill,
            interest=interest
        ).first()
        
        if existing_alert:
            # Update existing
            existing_alert.city = city
            existing_alert.position = position
            existing_alert.enabled = enabled
            session.commit()
            
            if enabled:
                filter_str = skill or interest
                return f" Обновил уведомление о '{filter_str}'. Буду автоматически сообщать когда зарегистрируются подходящие специалисты!"
            else:
                filter_str = skill or interest
                return f"Уведомление о '{filter_str}' отключено."
        else:
            # Create new
            alert = ContactAlert(
                user_id=user.id,
                skill=skill,
                interest=interest,
                city=city,
                position=position,
                enabled=enabled
            )
            session.add(alert)
            session.commit()
            
            filter_parts = []
            if skill:
                filter_parts.append(f"навык '{skill}'")
            if interest:
                filter_parts.append(f"интерес '{interest}'")
            if city:
                filter_parts.append(f"город {city}")
            if position:
                filter_parts.append(f"должность '{position}'")
            
            filter_str = ', '.join(filter_parts)
            return f" Настроил автоматическое уведомление! Буду следить за новыми пользователями ({filter_str}). Когда кто-то подходящий зарегистрируется или обновит профиль, я естественно упомяну это в нашем следующем диалоге. Никаких навязчивых уведомлений!"
        
    except Exception as e:
        logger.error(f"[SET_CONTACT_ALERT] Error: {e}", exc_info=True)
        return f"Ошибка настройки уведомления: {str(e)}"
    finally:
        if close_session:
            session.close()

async def set_auto_post_time(post_time, user_id=None, session=None):
    """
    Установить время автоматической публикации контента

    Args:
        post_time: Время в формате HH:MM (например, '14:30')
        user_id: ID пользователя в Telegram
        session: Сессия базы данных (опционально)

    Returns:
        Сообщение о настройке времени автопостинга
    """
    from models import Session, User, UserProfile
    
    logger.info(f"[SET_AUTO_POST_TIME] user_id={user_id}, post_time={post_time}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        # Validate time format
        import re
        if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', post_time):
            return "Неверный формат времени. Используй HH:MM, например: '14:30' или '09:15'"
        
        # Get or create user profile
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)
        
        # Update post time
        profile.auto_post_time = post_time
        session.commit()
        
        return f" Время автопостинга установлено на {post_time}! Каждый день в это время я буду автоматически публиковать контент в ваш канал. Следующий пост: завтра в {post_time}."
        
    except Exception as e:
        logger.error(f"[SET_AUTO_POST_TIME] Error: {e}", exc_info=True)
        return f"Ошибка настройки времени: {str(e)}"
    finally:
        if close_session:
            session.close()

# ============================================================================
# MARKETING & GROWTH AUTOMATION
# ============================================================================

async def generate_marketing_content(product_name, target_audience, platform, goal="привлечение", user_id=None, session=None):
    """
    AI генерация маркетингового контента для привлечения клиентов
    Требует: STANDARD или PREMIUM подписку
    """
    from .marketing_agent import generate_marketing_content as gen_content
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        # Все функции открыты — оплата токенами
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден. Напишите /start."
        
        result = await gen_content(
            product_name=product_name,
            target_audience=target_audience,
            platform=platform,
            goal=goal,
            user_id=user_id,
            session=session
        )
        
        return result.get('message', 'Контент создан')
        
    except Exception as e:
        logger.error(f"[MARKETING] Error in handler: {e}", exc_info=True)
        return f"Ошибка генерации контента: {str(e)}"
    finally:
        if close_session:
            session.close()

async def research_topic(query: str, depth: str = 'full', user_id: int = None, session=None):
    """
     ПОИСК И АНАЛИЗ актуальной информации по теме
    Доступно для ВСЕХ тарифов с одинаковым качеством

    Этапы:
    1. Поиск свежей информации из надежных источников
    2. AI-анализ найденных данных
    3. Создание задач для топ-3 рекомендаций

    Args:
        query: Тема для исследования
        depth: quick/balanced/deep (5/10/15 источников)
        user_id: ID пользователя
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        # Функция доступна для всех тарифов
        logger.info(f"[RESEARCH] Starting for user {user_id}: query='{query}', depth={depth}")
        
        result = await marketing_agent.research_topic(
            query=query,
            depth=depth,
            user_id=user_id,
            session=session
        )
        
        # НЕ публикуем автоматически — пусть AI предложит пользователю создать пост
        # и пользователь решит сам
        
        if isinstance(result, dict):
            return result.get('message', 'Исследование завершено')
        else:
            return str(result) if result else 'Исследование завершено'
        
    except Exception as e:
        logger.error(f"[RESEARCH] Error in handler: {e}", exc_info=True)
        return f"Ошибка исследования: {str(e)}"
    finally:
        if close_session:
            session.close()


async def schedule_background_task(
    query: str,
    reason: str = '',
    delay_minutes: int = 60,
    user_id: int = None,
    session=None,
):
    """
    Запланировать фоновое исследование.
    Агент ставит себе задачу: через delay_minutes выполнить research_topic(query)
    и автоматически отправить результат пользователю.
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        delay_minutes = max(5, min(int(delay_minutes or 60), 1440))  # 5мин..24ч
        from datetime import timezone as _tz
        now_utc = datetime.utcnow().replace(tzinfo=_tz.utc)
        trigger_at = now_utc + timedelta(minutes=delay_minutes)
        expires_at = trigger_at + timedelta(hours=48)

        anchor = Anchor(
            user_id=user.id,
            anchor_type='background_research',
            source=f'agent_scheduled:{user_id}',
            topic=f"Фоновое исследование: «{query[:80]}»",
            priority=AnchorPriority.HIGH,
            data=json.dumps({'query': query, 'reason': reason}, ensure_ascii=False),
            triggered_at=trigger_at,
            expires_at=expires_at,
            cooldown_hours=0,
            batch_group='insights',
        )
        session.add(anchor)
        session.commit()

        t = trigger_at.strftime('%H:%M')
        reason_str = f" ({reason})" if reason else ""
        logger.info(f"[BG_TASK] Scheduled research '{query[:60]}' at {t} for user {user_id}")
        return f"Поставил фоновую задачу себе{reason_str}: в {t} исследую «{query[:60]}» и пришлю результат."
    except Exception as e:
        logger.error(f"[BG_TASK] Schedule error: {e}")
        return f"Ошибка планирования: {e}"
    finally:
        if close_session:
            session.close()


async def set_content_strategy(strategy: str, user_id: int, session):
    """
     СОХРАНИТЬ СТРАТЕГИЮ КОНТЕНТА для автоматического маркетинга
    Требует: STANDARD или PREMIUM подписку
    
    Args:
        strategy: Описание стратегии контента от пользователя
        user_id: ID пользователя
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        # Все функции открыты — оплата токенами
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден. Напишите /start."
        
        logger.info(f"[CONTENT_STRATEGY] Saving for user {user_id}")
        
        # Получаем или создаем профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)
        
        # Сохраняем стратегию
        profile.content_strategy = strategy
        
        # Автоматически включаем автомаркетинг при сохранении стратегии
        if not profile.auto_marketing_enabled:
            profile.auto_marketing_enabled = True
            logger.info(f"[CONTENT_STRATEGY] Auto-enabled marketing for user {user_id}")
        
        session.commit()
        
        logger.info(f"[CONTENT_STRATEGY] ✅ Saved: {strategy[:100]}...")
        
        channel_info = ''
        if user.telegram_channel:
            channel_info = f"\n\n Канал: {user.telegram_channel}\n Автопостинг включён"
        else:
            channel_info = "\n\n Telegram-канал не указан. Укажи его в профиле, чтобы посты публиковались автоматически."
        
        return f" Стратегия контента сохранена!\n\n{strategy}{channel_info}"
        
    except Exception as e:
        logger.error(f"[CONTENT_STRATEGY] Error: {e}", exc_info=True)
        session.rollback()
        return f"Ошибка сохранения стратегии: {str(e)}"
    finally:
        if close_session:
            session.close()

async def toggle_autonomous_feature(feature: str, enabled: bool, user_id: int, session):
    """
     УПРАВЛЕНИЕ АВТОНОМНЫМИ ФУНКЦИЯМИ
    Требует: PREMIUM подписку
    
    Args:
        feature: 'marketing', 'delegation', или 'all'
        enabled: True = включить, False = выключить
        user_id: ID пользователя
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        # Все функции открыты — оплата токенами
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден. Напишите /start."
        
        logger.info(f"[AUTONOMOUS_TOGGLE] User {user_id}: {feature} = {enabled}")
        
        # Получаем или создаем профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)
        
        # Обновляем флаги
        status_parts = []
        
        if feature == 'marketing' or feature == 'all':
            profile.auto_marketing_enabled = enabled
            status_emoji = "" if enabled else ""
            action = "включён" if enabled else "выключен"
            status_parts.append(f"{status_emoji} Автопостинг: {action}")
        
        if feature == 'delegation' or feature == 'all':
            profile.auto_delegation_enabled = enabled
            status_emoji = "" if enabled else ""
            action = "включено" if enabled else "выключено"
            status_parts.append(f"{status_emoji} Автоделегирование: {action}")
        
        session.commit()
        
        response = " Настройки автономных функций обновлены!\n\n" + "\n".join(status_parts)
        
        if not enabled:
            response += "\n\n Ты всегда можешь включить обратно используя эту же команду."
        
        logger.info(f"[AUTONOMOUS_TOGGLE] ✅ Updated for user {user_id}")
        
        return response
        
    except Exception as e:
        logger.error(f"[AUTONOMOUS_TOGGLE] Error: {e}", exc_info=True)
        session.rollback()
        return f"Ошибка обновления настроек: {str(e)}"
    finally:
        if close_session:
            session.close()


async def create_post(content: str, user_id: int, session=None, force: bool = False, image_url: str = None):
    """
     ПУБЛИКАЦИЯ ПОСТА В БЛОГ
    
    Создаёт пост от имени пользователя в блог платформы,
    который видят все пользователи.
    
    Args:
        content: Текст поста
        user_id: Telegram ID пользователя
        session: DB сессия
        image_url: URL картинки (Unsplash или иной)
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        if not content or not content.strip():
            return "Текст поста не может быть пустым."

        # Sanitize token hallucinations (AI иногда пишет "1000+500" вместо "1500")
        from ai_integration.conversation_history import sanitize_token_hallucinations
        content = sanitize_token_hallucinations(content)

        # Лимит: 1 пост в ленту в день (можно обойти force=True если пользователь явно просит)
        import datetime as dt
        import pytz as _pytz_cp
        _utz_cp = _pytz_cp.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
        _now_cp = dt.datetime.now(_utz_cp)
        _today_start_cp = _now_cp.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_pytz_cp.UTC).replace(tzinfo=None)
        posts_today = session.query(Post).filter(
            Post.user_id == user.id,
            Post.created_at >= _today_start_cp,
        ).count()
        if posts_today >= 1 and not force:
            return "[INTERNAL] Пост в ленту уже опубликован (1/день). НЕ сообщай пользователю — переключись на другую задачу (email, research, задачи)."

        # ── Авто-генерация картинки если image_url не указан И пользователь просил картинки в правилах ──
        if not image_url or not image_url.strip():
            _should_auto_img = False
            try:
                _raw_mem_img = getattr(user, 'memory', None) or ''
                if _raw_mem_img:
                    try:
                        from ai_integration.memory import decrypt_data as _decrypt_img
                        _dec_img = _decrypt_img(_raw_mem_img)
                    except Exception:
                        _dec_img = _raw_mem_img
                    if _dec_img:
                        _lo = _dec_img.lower()
                        _should_auto_img = any(kw in _lo for kw in (
                            'картинк', 'изображен', 'image', 'визуал', 'иллюстрац', 'фото',
                        ))
            except Exception:
                pass
            if _should_auto_img:
                try:
                    import re as _re_img
                    _img_keywords = content[:200].replace('\n', ' ').strip()
                    _img_result = await generate_image(
                        prompt=f"Blog illustration, modern digital art: {_img_keywords}",
                        style="modern",
                        user_id=user_id,
                        session=session,
                        close_session=False,
                        send_to_telegram=False,
                    )
                    _img_match = _re_img.search(r'!\[.*?\]\((https?://[^\)]+)\)', _img_result or '')
                    if _img_match:
                        image_url = _img_match.group(1)
                        logger.info(f"[CREATE_POST] Auto-generated image: {image_url[:100]}")
                    else:
                        logger.info(f"[CREATE_POST] Auto image: no URL in result: {(_img_result or '')[:120]}")
                except Exception as _img_err:
                    logger.warning(f"[CREATE_POST] Auto image generation failed: {_img_err}")

        post = Post(
            user_id=user.id,
            username=user.username or user.first_name or f"user_{user.telegram_id}",
            content=content.strip(),
            image_url=(image_url.strip() if image_url and image_url.strip() else None),
            created_at=dt.datetime.now(dt.timezone.utc)
        )
        
        session.add(post)
        session.commit()
        session.refresh(post)
        
        post_preview = content[:80] + '...' if len(content) > 80 else content
        has_img = bool(post.image_url)
        logger.info(f"[CREATE_POST] User {user_id} published post #{post.id}: '{post_preview}' image={has_img}")

        # ── Кросс-постинг в TG и Discord с той же картинкой ──
        cross_notes = []
        try:
            if getattr(user, 'telegram_channel', None):
                _tg_result = await publish_to_telegram(
                    content=content.strip(),
                    image_url=post.image_url,
                    user_id=user_id,
                    session=session,
                    force=True,
                )
                if '✅' in str(_tg_result):
                    cross_notes.append(" TG-канал")
                else:
                    cross_notes.append(f" TG: {str(_tg_result)[:80]}")
        except Exception as _tge:
            logger.warning(f"[CREATE_POST] TG cross-post error: {_tge}")
        try:
            if getattr(user, 'discord_webhook', None):
                _dc_result = await publish_to_discord(
                    content=content.strip(),
                    image_url=post.image_url,
                    user_id=user_id,
                    session=session,
                    force=True,
                )
                if '✅' in str(_dc_result):
                    cross_notes.append(" Discord")
                else:
                    cross_notes.append(f" Discord: {str(_dc_result)[:80]}")
        except Exception as _dce:
            logger.warning(f"[CREATE_POST] Discord cross-post error: {_dce}")

        cross_line = (" + " + " + ".join(cross_notes)) if cross_notes else ""
        return (
            f" Пост #{post.id} опубликован в блог{cross_line}!{' ' if has_img else ''}\n\n"
            f"«{post_preview}»\n\nСсылка на блог: https://asibiont.com/dashboard"
        )
        
    except Exception as e:
        logger.error(f"[CREATE_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка публикации поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def edit_post(new_content: str, user_id: int, post_id: int = None, session=None):
    """
     РЕДАКТИРОВАНИЕ ПОСТА В ЛЕНТЕ
    
    Изменяет текст существующего поста. Если post_id не указан — редактирует последний.
    
    Args:
        new_content: Новый текст поста
        user_id: Telegram ID пользователя
        post_id: ID поста (опционально)
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        if not new_content or not new_content.strip():
            return "Новый текст поста не может быть пустым."
        
        if post_id:
            post = session.query(Post).filter_by(id=post_id, user_id=user.id).first()
            if not post:
                return f"Пост #{post_id} не найден или не принадлежит тебе."
        else:
            post = session.query(Post).filter_by(user_id=user.id).order_by(Post.created_at.desc()).first()
            if not post:
                return "У тебя нет постов для редактирования."
        
        old_preview = post.content[:40] + '...' if len(post.content) > 40 else post.content
        post.content = new_content.strip()
        session.commit()
        
        new_preview = new_content[:80] + '...' if len(new_content) > 80 else new_content
        logger.info(f"[EDIT_POST] User {user_id} edited post #{post.id}")
        return f" Пост #{post.id} обновлён!\n\nБыло: «{old_preview}»\nСтало: «{new_preview}»\n\nСсылка на ленту: https://asibiont.com/dashboard"
        
    except Exception as e:
        logger.error(f"[EDIT_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка редактирования поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def get_posts(user_id: int, limit: int = 5, session=None):
    """
     СПИСОК ПОСТОВ ПОЛЬЗОВАТЕЛЯ
    
    Возвращает посты пользователя с датами, лайками и просмотрами.
    
    Args:
        user_id: Telegram ID пользователя
        limit: Количество постов (макс 20)
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        limit = min(max(1, limit or 5), 20)
        
        posts = session.query(Post).filter_by(user_id=user.id).order_by(Post.created_at.desc()).limit(limit).all()
        
        if not posts:
            return "У тебя пока нет постов в ленте. Хочешь, напишу пост от твоего имени?"
        
        result_lines = [f" Твои посты ({len(posts)} из последних):\n"]

        # Aggregate likes/views/comments per post (avoid N+1 ×3 per post)
        from sqlalchemy import func as _func_gp
        _post_ids_gp = [p.id for p in posts]
        _likes_map = dict(session.query(PostLike.post_id, _func_gp.count(PostLike.id)).filter(
            PostLike.post_id.in_(_post_ids_gp)
        ).group_by(PostLike.post_id).all())
        _views_map = dict(session.query(PostView.post_id, _func_gp.count(PostView.id)).filter(
            PostView.post_id.in_(_post_ids_gp)
        ).group_by(PostView.post_id).all())
        _coms_map = dict(session.query(Comment.post_id, _func_gp.count(Comment.id)).filter(
            Comment.post_id.in_(_post_ids_gp)
        ).group_by(Comment.post_id).all())

        for post in posts:
            likes_count = _likes_map.get(post.id, 0)
            views_count = _views_map.get(post.id, 0)
            comments_count = _coms_map.get(post.id, 0)
            
            preview = post.content[:60] + '...' if len(post.content) > 60 else post.content
            # Формат даты
            date_str = post.created_at.strftime('%d.%m.%Y %H:%M') if post.created_at else '?'
            
            result_lines.append(
                f"#{post.id} ({date_str}) — {views_count} | {likes_count} | {comments_count}\n«{preview}»\n"
            )
        
        logger.info(f"[GET_POSTS] User {user_id} listed {len(posts)} posts")
        return '\n'.join(result_lines)
        
    except Exception as e:
        logger.error(f"[GET_POSTS] Error: {e}", exc_info=True)
        return f" Ошибка получения постов: {str(e)}"
    finally:
        if close_session:
            session.close()


async def delete_post(user_id: int, post_id: int = None, session=None):
    """
     УДАЛЕНИЕ ПОСТА из ленты
    
    Удаляет пост пользователя. Если post_id не указан — удаляет последний пост.
    
    Args:
        user_id: Telegram ID пользователя
        post_id: ID поста (опционально, если не указан — последний)
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        if post_id:
            # Удаляем конкретный пост
            post = session.query(Post).filter_by(id=post_id, user_id=user.id).first()
            if not post:
                return f"Пост #{post_id} не найден или не принадлежит тебе."
        else:
            # Удаляем последний пост пользователя
            post = session.query(Post).filter_by(user_id=user.id).order_by(Post.created_at.desc()).first()
            if not post:
                return "У тебя нет постов для удаления."
        
        post_preview = post.content[:50] + '...' if len(post.content) > 50 else post.content
        post_id_deleted = post.id
        
        # Удаляем лайки и просмотры (каскадно через FK, но подстраховка)
        try:
            session.query(PostLike).filter_by(post_id=post.id).delete()
            session.query(PostView).filter_by(post_id=post.id).delete()
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        
        session.delete(post)
        session.commit()
        
        logger.info(f"[DELETE_POST] User {user_id} deleted post #{post_id_deleted}: '{post_preview}'")
        return f" Пост #{post_id_deleted} удалён: «{post_preview}»"
        
    except Exception as e:
        logger.error(f"[DELETE_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка удаления поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def publish_to_telegram(content: str, image_url: str = None, user_id: int = None, session=None, force: bool = False):
    """
     ПУБЛИКАЦИЯ В TELEGRAM канал пользователя
    
    Требования:
    - Пользователь должен указать telegram_channel в профиле
    - Бот должен быть админом канала
    - Лимит: 1 пост в канал в день
    
    Args:
        content: Текст для публикации (Markdown)
        user_id: ID пользователя
        session: DB сессия
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден. Напишите /start."
        
        logger.info(f"[PUBLISH] Starting for user {user_id}")
        
        # ── Проверка дневного лимита (1 пост в канал в день) ──
        import pytz
        from models import AnchorDeliveryLog
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)
        
        # Проверяем по AnchorDeliveryLog (автоматические публикации)
        auto_channel_today = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= today_start_utc,
            AnchorDeliveryLog.anchor_types.contains('channel_post')
        ).count()
        
        # Также проверяем по задачам (ручные публикации через publish_to_telegram)
        from models import Task
        manual_channel_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.like('%Пост опубликован в%'),
            Task.status == 'completed',
            Task.actual_completion_time >= today_start_utc
        ).count()
        
        total_channel_posts_today = auto_channel_today + manual_channel_today
        # 1 пост в канал в день
        if total_channel_posts_today >= 1 and not force:
            channel = user.telegram_channel or 'канал'
            if not channel.startswith('@') and not channel.startswith('-'):
                channel = f"@{channel}"
            return (
                f"[INTERNAL] В {channel} уже был пост (1/день). "
                f"НЕ сообщай пользователю — переключись на другую задачу."
            )
        
        # Если content это JSON строка от generate_marketing_content, парсим
        try:
            import json
            content_data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            content_data = content

        # Sanitize token hallucinations (AI иногда пишет "1000+500" вместо "1500")
        from ai_integration.conversation_history import sanitize_token_hallucinations
        if isinstance(content_data, str):
            content_data = sanitize_token_hallucinations(content_data)
            content = sanitize_token_hallucinations(content)
        elif isinstance(content_data, dict):
            for _k in ('text', 'title', 'body'):
                if _k in content_data and isinstance(content_data[_k], str):
                    content_data[_k] = sanitize_token_hallucinations(content_data[_k])

        # ── GUARD: не публиковать внутренние отчёты в публичный канал ──
        _tg_lower = (content if isinstance(content, str) else str(content)).lower()
        _TG_INTERNAL = (
            'проверил', 'проверила', 'обновила прогресс', 'обновил прогресс',
            'update_goal_progress', 'goal_progress', 'save_email_contact',
            'отправил письм', 'отправила письм', 'нашёл контакт', 'нашла контакт',
            'сохранила контакт', 'сохранил контакт', 'добавила в crm', 'добавил в crm',
            'делегиру', 'delegate[',
        )
        _TG_PUBLIC = (
            'тренд', 'обзор', 'кейс', 'инсайт', 'аналитик', 'исследован',
            'стратеги', 'индустри', 'рынок', 'технолог',
        )
        _tg_int = sum(1 for m in _TG_INTERNAL if m in _tg_lower)
        _tg_pub = sum(1 for m in _TG_PUBLIC if m in _tg_lower)
        if _tg_int >= 2 and _tg_pub == 0:
            logger.warning('[TG_GUARD] Blocked internal report from public channel: %.100s', content)
            return (
                "⛔ Этот текст похож на внутренний отчёт, а не на публичный пост. "
                "Telegram-канал — для аудитории: инсайты, кейсы, аналитика. "
                "Переформулируй контент как экспертный пост для подписчиков."
            )

        result = await marketing_agent.publish_to_telegram(
            content=content_data,
            image_url=image_url,
            user_id=user_id,
            session=session
        )
        
        # Проверяем результат публикации
        if isinstance(result, dict):
            if result.get('success'):
                return result.get('message', ' Пост успешно опубликован в Telegram-канал')
            else:
                # Публикация не удалась - возвращаем детальное сообщение об ошибке
                return result.get('message', ' Не удалось опубликовать пост в Telegram-канал')
        else:
            return str(result)
        
    except Exception as e:
        logger.error(f"[PUBLISH] Error in handler: {e}", exc_info=True)
        return f"Ошибка публикации: {str(e)}"
    finally:
        if close_session:
            session.close()


async def web_search(query: str, user_id: int = None, session=None, close_session: bool = False):
    """
    Прямой поиск в интернете — возвращает результаты с ссылками.
    Универсальный: ищет любую информацию — людей, контакты, ресурсы, статьи.
    """
    from .api_client import get_api_client

    logger.info(f"[WEB_SEARCH] user={user_id}, query='{query}'")
    api = get_api_client()

    results = await api.web_search(query, num=8)
    if not results:
        return f"По запросу «{query}» ничего не найдено. Попробуй переформулировать запрос."

    lines = [f"🔎 Результаты поиска: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get('title', '')
        snippet = r.get('snippet', '')
        link = r.get('link', '')
        lines.append(f"{i}. **{title}**")
        if snippet:
            lines.append(f"   {snippet[:200]}")
        if link:
            lines.append(f"   🔗 {link}")
        lines.append("")

    return '\n'.join(lines)


async def quick_topic_search(topic: str, user_id: int = None, session=None):
    """
     БЫСТРЫЙ ПОИСК ПО ТЕМЕ (LIGHT+)
    Простой поиск без AI анализа - топ-3 результата с ссылками
    """
    from .api_client import get_api_client
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"
        
        logger.info(f"[QUICK_SEARCH] Starting for user {user_id}: topic='{topic}'")
        api = get_api_client()
        
        results = await api.web_search(topic, num=3)
        if not results:
            return f" По запросу '{topic}' не найдено результатов"
        
        result_text = f" **Быстрый поиск**: {topic}\n\n"
        for i, r in enumerate(results, 1):
            result_text += f"{i}. **{r['title']}**\n"
            snippet = r['snippet']
            if snippet:
                result_text += f"   {snippet[:150]}{'...' if len(snippet) > 150 else ''}\n"
            result_text += f" [Читать далее]({r['link']})\n\n"
        
        # AI анализ для всех тарифов
        try:
            context = "\n\n".join([f"**{r['title']}**\n{r['snippet']}" for r in results[:3]])
            prompt = f"""На основе этих результатов поиска по теме "{topic}":

{context}

Сделай краткий практичный вывод в 2-3 предложениях: суть темы, ключевой факт, и что с этим делать. Не пересказывай, а синтезируй."""
            ai_analysis = await api.deepseek_analyze(prompt, system_prompt="Ты эксперт-аналитик. Давай конкретику и практическую пользу.", max_tokens=200)
            if ai_analysis:
                result_text += f" **AI анализ**: {ai_analysis}\n\n"
        except Exception as e:
            logger.warning(f"[QUICK_SEARCH] AI analysis failed: {e}")
        
        result_text += " **Подсказка**: Для более детального анализа используйте функцию research_topic."
        return result_text
        
    except Exception as e:
        logger.error(f"Error in quick_topic_search: {e}")
        return f" Ошибка поиска по теме: {topic}"
    finally:
        if close_session:
            session.close()

async def check_topic_relevance(topic: str, user_id: int = None, session=None):
    """
     ПРОВЕРКА АКТУАЛЬНОСТИ ТЕМЫ (LIGHT+)
    AI-анализ: насколько тема актуальна сейчас и стоит ли ей заниматься
    """
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"
        
        logger.info(f"[RELEVANCE_CHECK] Starting for user {user_id}: topic='{topic}'")
        
        from .api_client import get_api_client
        api = get_api_client()
        
        current_year = datetime.now().year
        
        results = await api.web_search(f"{topic} {current_year} тренды актуальность", num=7)
        
        if not results:
            return f" **Проверка актуальности**: {topic}\n\n Информация по теме не найдена"
        
        # AI-анализ актуальности вместо подсчёта слов
        context = "\n\n".join([
            f"**{r['title']}**\n{r['snippet']}"
            for r in results[:7]
        ])
        
        prompt = f"""Проанализируй актуальность темы "{topic}" на основе этих свежих данных из поиска:

{context}

Ответь кратко (3-5 предложений):
1. Насколько тема актуальна прямо сейчас? (высокая/средняя/низкая)
2. Почему? Приведи 1-2 конкретных факта из данных
3. На что обратить внимание / что сейчас происходит в этой области
4. Стоит ли сейчас погружаться в эту тему?"""

        analysis = await api.deepseek_analyze(
            prompt=prompt,
            system_prompt="Ты аналитик. Отвечай кратко и конкретно, опираясь на данные.",
            max_tokens=300
        )
        
        result = f" **Проверка актуальности**: {topic}\n\n"
        if analysis:
            result += f"{analysis}\n\n"
        result += f"Найдено {len(results)} свежих источников по теме."
        
        return result
    except Exception as e:
        logger.error(f"Error in check_topic_relevance: {e}")
        return f" Ошибка проверки темы: {topic}"
    finally:
        if close_session:
            session.close()

async def get_news_trends(topic: str = "tech startups AI", period: str = "week", focus: str = "trends", user_id: int = None, session=None):
    """
     ПОЛУЧЕНИЕ НОВОСТЕЙ И АНАЛИЗ ТРЕНДОВ
    Использует NewsAPI для поиска новостей + AI для анализа трендов
    """
    from .api_client import get_api_client
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."
        
        logger.info(f"[NEWS_TRENDS] Starting for user {user_id}: topic='{topic}', period={period}, focus={focus}")
        
        api = get_api_client()
        result = await api.news_and_analyze(
            topic=topic,
            period=period,
            focus=focus,
            max_articles=15
        )
        
        return result['message']
    
    except Exception as e:
        logger.error(f"[NEWS_TRENDS] Error: {e}", exc_info=True)
        return f" Ошибка получения новостей: {str(e)}"
    finally:
        if close_session:
            session.close()

async def research_and_plan(query: str, user_id: int = None, session=None):
    """
     КОМПЛЕКСНЫЙ АНАЛИЗ РЫНКА И ПЛАН ДЕЙСТВИЙ (STANDARD+)

    Проводит глубокое исследование и создает персонализированный план действий

    Args:
        query: Запрос для исследования (тема, ниша, продукт)
        user_id: ID пользователя
        session: DB сессия

    Returns:
        Детальный анализ рынка + план действий + предлагаемые задачи
    """
    from .api_client import get_api_client
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        logger.info(f"[RESEARCH_PLAN] Starting comprehensive research for user {user_id}: '{query}'")

        api = get_api_client()
        
        # Динамический год
        current_year = datetime.now().year
        next_year = current_year + 1

        # ШАГ 1: Многоаспектный ПАРАЛЛЕЛЬНЫЙ поиск
        search_queries = [
            f"{query} {current_year} {next_year}",
            f"{query} анализ обзор",
            f"{query} практические советы опыт",
            f"{query} плюсы минусы отзывы",
            f"{query} рекомендации лучшие"
        ]

        all_results = await api.web_multi_search(search_queries, num_per_query=5)

        if not all_results:
            return f" Не удалось найти информацию по запросу '{query}'"

        # ШАГ 2: AI анализ всех результатов
        context = "\n\n".join([
            f"**{r['title']}**\n{r['snippet']}\nИсточник: {r['link']}"
            for r in all_results[:15]
        ])

        # Персонализация на основе профиля
        profile_context = ""
        if profile:
            profile_parts = []
            if profile.skills: profile_parts.append(f"Навыки: {profile.skills}")
            if profile.interests: profile_parts.append(f"Интересы: {profile.interests}")
            if profile.goals: profile_parts.append(f"Цели: {profile.goals}")
            if profile.city: profile_parts.append(f"Город: {profile.city}")
            if profile.company: profile_parts.append(f"Компания: {profile.company}")
            if profile.position: profile_parts.append(f"Должность: {profile.position}")
            if profile_parts:
                profile_context = f"""
ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (персонализируй рекомендации под ЭТОГО человека):
{chr(10).join('- ' + p for p in profile_parts)}
"""

        analysis_prompt = f"""Ты — бизнес-аналитик. Проведи исследование по теме "{query}" для конкретного человека.

{profile_context}

ДАННЫЕ ИЗ ИНТЕРНЕТА (свежие результаты поиска):
{context}

ЗАДАЧА: На основе РЕАЛЬНЫХ данных выше (не выдумывай!) создай анализ.

Правила:
- Цитируй конкретные цифры, компании, факты ИЗ ДАННЫХ ПОИСКА
- Связывай каждую рекомендацию с профилем пользователя
- "Возможность" = что конкретно этот человек может сделать с его навыками
- "Шаг" = действие, которое можно выполнить за 1-3 дня
- НЕ пиши общие слова. "Рынок растёт" — плохо. "Рынок вырос с $X до $Y по данным [источник]" — хорошо

Формат JSON:
{{
    "market_summary": "обзор на основе данных поиска: размер рынка, динамика, ключевые цифры",
    "key_trends": ["конкретный тренд с данными", "второй тренд с примером"],
    "competitor_analysis": {{
        "main_players": ["название компании — что делает — чем интересна"],
        "gaps": ["конкретный пробел на рынке, который следует из данных"]
    }},
    "opportunities_for_user": ["возможность привязанная к навыкам/целям пользователя"],
    "action_plan": {{
        "this_week": ["конкретное действие на эту неделю"],
        "this_month": ["цель на месяц с метрикой успеха"]
    }},
    "risks": ["главный риск или подводный камень"],
    "recommended_tasks": [
        {{
            "title": "задача для бота, максимум 50 символов",
            "description": "что именно сделать и зачем",
            "priority": "высокий/средний/низкий"
        }}
    ]
}}"""

        analysis = await api.deepseek_analyze(
            prompt=analysis_prompt,
            max_tokens=4000,
            temperature=0.5,
            parse_json=True
        )

        if not analysis:
            return f" Ошибка AI анализа"

        # Форматируем ответ
        if isinstance(analysis, dict):
            result = f" **АНАЛИЗ: {query.upper()}**\n\n"
            
            summary = analysis.get('summary') or analysis.get('market_summary', '')
            if summary:
                result += f" **ОБЗОР**\n{summary}\n\n"

            findings = analysis.get('key_findings') or analysis.get('key_trends', [])
            if findings:
                result += " **КЛЮЧЕВЫЕ ФАКТЫ**\n"
                for item in findings[:3]:
                    result += f"• {item}\n"
                result += "\n"

            existing = analysis.get('what_exists') or []
            if existing:
                result += " **ЧТО УЖЕ ЕСТЬ**\n"
                for item in existing[:3]:
                    result += f"• {item}\n"
                result += "\n"
            elif analysis.get('competitor_analysis'):
                comp = analysis['competitor_analysis']
                players = comp.get('main_players') or comp.get('main_competitors', [])
                if players:
                    result += " **ОСНОВНЫЕ ИГРОКИ**\n"
                    for player in players[:3]:
                        result += f"• {player}\n"
                    result += "\n"

            opps = analysis.get('gaps_or_opportunities') or analysis.get('opportunities_for_user') or analysis.get('opportunities', [])
            if opps:
                result += " **ВОЗМОЖНОСТИ ДЛЯ ТЕБЯ**\n"
                for opp in opps[:3]:
                    result += f"• {opp}\n"
                result += "\n"

            advice = analysis.get('personalized_advice', '')
            if advice:
                result += f" **ПЕРСОНАЛЬНЫЙ СОВЕТ**\n{advice}\n\n"

            plan = analysis.get('action_plan') or analysis.get('actionable_plan', {})
            if isinstance(plan, dict):
                steps = plan.get('this_week') or plan.get('immediate_steps', [])
                if steps:
                    result += " **НА ЭТОЙ НЕДЕЛЕ**\n"
                    for step in steps[:3]:
                        result += f"• {step}\n"
                    result += "\n"
                month = plan.get('this_month') or plan.get('short_term_goals', [])
                if month:
                    result += " **НА МЕСЯЦ**\n"
                    for goal in month[:2]:
                        result += f"• {goal}\n"
                    result += "\n"

            risks = analysis.get('risks_or_caveats') or analysis.get('risks', [])
            if risks:
                if isinstance(risks, str):
                    risks = [risks]
                result += " **НЮАНСЫ**\n"
                for risk in risks[:2]:
                    result += f"• {risk}\n"
                result += "\n"

            if analysis.get('recommended_tasks'):
                result += " **РЕКОМЕНДУЕМЫЕ ЗАДАЧИ**\n"
                for task in analysis['recommended_tasks'][:2]:
                    if isinstance(task, dict):
                        result += f"• **{task.get('title', '')}** — {task.get('description', '')}\n"
                    else:
                        result += f"• {task}\n"
                result += "\n"

            result += f" Анализ основан на {len(all_results)} актуальных источниках"

            return result
        else:
            # Если JSON не распарсился — вернём текстовый ответ
            return f" **Анализ: {query}**\n\n{analysis}"

    except Exception as e:
        logger.error(f"[RESEARCH_PLAN] Error: {e}", exc_info=True)
        return f" Ошибка комплексного исследования: {str(e)}"
    finally:
        if close_session:
            session.close()

# ===== EXTERNAL API FUNCTIONS (через единый api_client) =====

async def get_weather_info(city: str, user_id: int = None, session=None) -> str:
    """Получить информацию о погоде с практическими рекомендациями"""
    from .api_client import get_api_client
    
    try:
        api = get_api_client()
        data = await api.get_weather(city)
        
        if not data:
            return f" Не удалось получить погоду для города '{city}'"
        
        temp = data['temp']
        feels = data['feels_like']
        desc = data['description']
        humidity = data['humidity']
        wind = data['wind_speed']
        
        result = f" **Погода в {data['city_name']}:**\n"
        result += f"• Температура: {temp:.1f}°C (ощущается как {feels:.1f}°C)\n"
        result += f"• {desc.capitalize()}, влажность {humidity}%, ветер {wind} м/с\n"
        
        # Практические рекомендации
        tips = []
        if temp < 0:
            tips.append("Тепло одевайтесь: мороз")
        elif temp < 10:
            tips.append("Понадобится куртка")
        elif temp > 30:
            tips.append("Жарко — пейте больше воды")
        
        if wind > 10:
            tips.append("сильный ветер")
        if humidity > 80:
            tips.append("высокая влажность")
        if 'дожд' in desc.lower() or 'rain' in desc.lower():
            tips.append("возьмите зонт")
        if 'снег' in desc.lower() or 'snow' in desc.lower():
            tips.append("осторожно на дорогах")
        
        if tips:
            result += f"\n {', '.join(tips).capitalize()}\n"
        
        return result

    except Exception as e:
        logger.error(f"[WEATHER] Error: {e}")
        return f" Ошибка получения погоды: {str(e)}"


async def get_stock_price(symbol: str, data_type: str = "quote", user_id: int = None, session=None) -> str:
    """Получить котировку акции, курс валюты или цену металла через Alpha Vantage.
    
    Работает только если у агента пользователя настроен ALPHAVANTAGE_API_KEY.
    Тикеры акций: AAPL, MSFT, TSLA, GOOGL, AMZN и т.д.
    Форекс: EUR/USD, USD/RUB, GBP/USD и т.д.
    Криптовалюты: BTC (через symbol='BTC', data_type='crypto').
    """
    import urllib.request as _urllib_req
    import json as _json

    if not user_id:
        return "❌ Не указан user_id"

    # Ищем ALPHAVANTAGE_API_KEY в ключах агентов пользователя
    _api_key = None
    try:
        from models import UserAgent as _UA_av, User as _User_av
        _db_sess = session
        _close_sess = False
        if _db_sess is None:
            from models import Session as _SessionLocal
            _db_sess = _SessionLocal()
            _close_sess = True
        try:
            # user_id — это telegram_id, нужно найти DB user.id
            _db_user = _db_sess.query(_User_av).filter_by(telegram_id=user_id).first()
            _db_user_id = _db_user.id if _db_user else None
            if _db_user_id:
                _agents = _db_sess.query(_UA_av).filter(
                    _UA_av.author_id == _db_user_id,
                    _UA_av.user_api_keys.isnot(None),
                    _UA_av.user_api_keys.contains('ALPHAVANTAGE_API_KEY='),
                ).all()
                for _ag in _agents:
                    for _line in (_ag.user_api_keys or '').splitlines():
                        _line = _line.strip()
                        if _line.startswith('ALPHAVANTAGE_API_KEY='):
                            _val = _line.split('=', 1)[1].strip()
                            if _val and len(_val) > 4:
                                _api_key = _val
                                break
                    if _api_key:
                        break
        finally:
            if _close_sess:
                _db_sess.close()
    except Exception as _e:
        logger.warning(f"[STOCK] Error fetching API key: {_e}")

    if not _api_key:
        return (
            "💡 Котировки недоступны: ALPHAVANTAGE_API_KEY не настроен.\n"
            "Получи бесплатный ключ на alphavantage.co → добавь в настройки агента → API-ключи:\n"
            "ALPHAVANTAGE_API_KEY=твой_ключ"
        )

    symbol = symbol.strip().upper()
    try:
        if data_type == "forex" or "/" in symbol:
            from_c, _, to_c = symbol.partition("/")
            if not to_c:
                to_c = "USD"
            url = (
                f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE"
                f"&from_currency={from_c}&to_currency={to_c}&apikey={_api_key}"
            )
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=15) as r:
                d = _json.loads(r.read().decode())
            info = d.get("Realtime Currency Exchange Rate", {})
            if not info:
                return f"❌ Данные по паре {from_c}/{to_c} не получены (проверьте ключ или тикер)"
            rate = info.get("5. Exchange Rate", "?")
            refreshed = info.get("6. Last Refreshed", "")[:16]
            bid = info.get("8. Bid Price", "")
            ask = info.get("9. Ask Price", "")
            result = f"💱 **{from_c}/{to_c}**: {rate}"
            if bid and ask:
                result += f"  (bid: {bid}, ask: {ask})"
            if refreshed:
                result += f"\n  Обновлено: {refreshed} UTC"
            return result

        elif data_type == "crypto":
            url = (
                f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE"
                f"&from_currency={symbol}&to_currency=USD&apikey={_api_key}"
            )
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=15) as r:
                d = _json.loads(r.read().decode())
            info = d.get("Realtime Currency Exchange Rate", {})
            if not info:
                return f"❌ Данные по {symbol} не получены"
            rate = info.get("5. Exchange Rate", "?")
            refreshed = info.get("6. Last Refreshed", "")[:16]
            return f"🪙 **{symbol}/USD**: ${rate}  (обновлено: {refreshed} UTC)"

        else:
            url = (
                f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE"
                f"&symbol={symbol}&apikey={_api_key}"
            )
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=15) as r:
                d = _json.loads(r.read().decode())
            q = d.get("Global Quote", {})
            if not q or not q.get("05. price"):
                return f"❌ Котировка {symbol} не найдена (проверьте тикер или ключ)"
            price = q.get("05. price", "?")
            chg = q.get("09. change", "0") or "0"
            chg_pct = q.get("10. change percent", "0%")
            prev = q.get("08. previous close", "?")
            vol = q.get("06. volume", "")
            direction = "▲" if float(chg) >= 0 else "▼"
            result = f"📈 **{symbol}**: ${price}  {direction} {chg} ({chg_pct})\n"
            result += f"  Закрытие вчера: ${prev}"
            if vol:
                vol_m = round(int(vol) / 1_000_000, 1)
                result += f"  |  Объём: {vol_m}M"
            return result

    except Exception as e:
        logger.error(f"[STOCK] Error for {symbol}: {e}")
        return f"❌ Ошибка получения котировки {symbol}: {str(e)}"


async def analyze_situation_and_suggest_tasks(user_id: int = None, session=None) -> str:
    """
    Умный анализ ситуации пользователя и предложение релевантных задач.
    Анализирует профиль, контакты, тренды и предлагает персонализированные задачи.
    """
    if not user_id:
        return " Не указан ID пользователя"

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return " Пользователь не найден"

        # Получаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        suggestions = []  # legacy, может использоваться позже
        analysis_data = {
            'profile_interests': [],
            'profile_skills': [],
            'profile_goals': [],
            'relevant_contacts': [],
            'active_tasks': [],
            'trends': [],
            'time_context': None
        }

        # 1. АНАЛИЗ ПРОФИЛЯ
        if profile:
            if profile.interests:
                analysis_data['profile_interests'] = [i.strip() for i in profile.interests.split(',')]
            if profile.skills:
                analysis_data['profile_skills'] = [s.strip() for s in profile.skills.split(',')]
            if profile.goals:
                analysis_data['profile_goals'] = [g.strip() for g in profile.goals.split(',')]

        # 1.5. ПОЛУЧАЕМ АКТИВНЫЕ ЗАДАЧИ ПОЛЬЗОВАТЕЛЯ
        active_tasks = session.query(Task).filter_by(
            user_id=user.id
        ).filter(
            Task.status.in_(['pending', 'in_progress'])  # Активные задачи
        ).filter(
            or_(Task.due_date.is_(None), Task.due_date >= datetime.now(pytz.UTC))
        ).limit(5).all()

        analysis_data['active_tasks'] = active_tasks

        # 2. АНАЛИЗ КОНТАКТОВ - находим релевантных людей и их активности
        if analysis_data['profile_interests'] or analysis_data['profile_skills']:
            partners = get_partners_list(user.id, session)
            analysis_data['relevant_contacts'] = partners[:5]  # Топ-5 релевантных контактов

        # 2.5. ПОИСК КОНТАКТОВ ПО ПОХОЖИМ ЗАДАЧАМ
        task_based_contacts = []
        if analysis_data['active_tasks']:
            logger.info(f"[TASK_CONTACTS] Ищем контакты по задачам. Активных задач: {len(analysis_data['active_tasks'])}")
            # Для каждой активной задачи ищем пользователей с похожими задачами
            for user_task in analysis_data['active_tasks'][:3]:  # Берем топ-3 задачи пользователя
                task_title_lower = user_task.title.lower().strip()
                logger.info(f"[TASK_CONTACTS] Обрабатываем задачу: '{task_title_lower}'")

                # Ищем похожие задачи у других пользователей
                # Разбиваем заголовок на ключевые слова и ищем по ним
                task_words = [word.strip() for word in task_title_lower.split() if len(word.strip()) > 2]

                # Простая карта синонимов для распространенных активностей
                synonyms = {
                    'бег': ['бег', 'пробежка', 'бегать', 'пробежки', 'джоггинг', 'run', 'running'],
                    'тренировка': ['тренировка', 'workout', 'фитнес', 'спорт', 'упражнения'],
                    'программирование': ['программирование', 'код', 'разработка', 'programming', 'code'],
                    'чтение': ['чтение', 'книга', 'читать', 'read', 'reading'],
                    'работа': ['работа', 'проект', 'задача', 'work', 'task'],
                    'учеба': ['учеба', 'изучение', 'обучение', 'study', 'learning']
                }

                # Расширяем ключевые слова синонимами
                expanded_words = set(task_words)
                for word in task_words:
                    for key, syn_list in synonyms.items():
                        if word in syn_list:
                            expanded_words.update(syn_list)
                        elif any(word in syn for syn in syn_list):
                            expanded_words.add(key)
                            expanded_words.update(syn_list)

                # Получаем все активные задачи других пользователей
                all_other_tasks = session.query(Task).filter(
                    Task.user_id != user.id,
                    Task.status.in_(['pending', 'in_progress'])
                ).all()

                # Фильтруем по ключевым словам в Python (более надежно)
                similar_tasks = []
                for task in all_other_tasks:
                    task_lower = task.title.lower()
                    if any(word in task_lower for word in expanded_words):
                        similar_tasks.append(task)

                logger.info(f"[TASK_CONTACTS] Найдено похожих задач: {len(similar_tasks)}")
                # Batch-load users for similar tasks logging and match lookup
                _st_uids = list({st.user_id for st in similar_tasks})
                _st_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_st_uids)).all()} if _st_uids else {}
                for st in similar_tasks[:5]:  # Ограничим для логов
                    st_user = _st_user_by_id.get(st.user_id)
                    st_username = st_user.first_name if st_user else "Unknown"
                    logger.info(f"[TASK_CONTACTS]   - '{st.title}' (пользователь: {st_username})")

                # Группируем по пользователям и считаем схожесть
                user_task_matches = {}
                for similar_task in similar_tasks:
                    if similar_task.user_id not in user_task_matches:
                        user_task_matches[similar_task.user_id] = {
                            'user_id': similar_task.user_id,
                            'matching_tasks': [],
                            'similarity_score': 0
                        }
                    user_task_matches[similar_task.user_id]['matching_tasks'].append(similar_task.title)
                    user_task_matches[similar_task.user_id]['similarity_score'] += 1

                # Добавляем топ пользователей с похожими задачами
                for match in sorted(user_task_matches.values(), key=lambda x: x['similarity_score'], reverse=True)[:2]:
                    # Проверяем, что этого пользователя еще нет в контактах
                    existing_contact_ids = [c.user_id for c in analysis_data['relevant_contacts']]
                    if match['user_id'] not in existing_contact_ids:
                        match_user = _st_user_by_id.get(match['user_id'])
                        if match_user:
                            # Используем first_name или telegram_id как username
                            display_name = match_user.first_name or f"user_{match_user.telegram_id}"
                            task_based_contacts.append({
                                'user_id': match['user_id'],
                                'username': display_name,
                                'common_tasks': match['matching_tasks'][:2],  # Топ-2 похожих задач
                                'similarity_score': match['similarity_score']
                            })

            # Добавляем контакты по задачам в общий список
            analysis_data['task_based_contacts'] = task_based_contacts[:3]  # Топ-3 контакта по задачам

        # 3. АНАЛИЗ ВРЕМЕНИ И КОНТЕКСТА
        now = datetime.now(pytz.UTC)
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        user_now = now.astimezone(user_tz)

        hour = user_now.hour
        if 6 <= hour < 12:
            analysis_data['time_context'] = 'утро'
        elif 12 <= hour < 18:
            analysis_data['time_context'] = 'день'
        elif 18 <= hour < 22:
            analysis_data['time_context'] = 'вечер'
        else:
            analysis_data['time_context'] = 'ночь'

        # 4. ПОЛУЧАЕМ КОНКРЕТНЫЕ ТРЕНДЫ ПО ИНТЕРЕСАМ
        if analysis_data['profile_interests']:
            # Берем первый интерес для анализа трендов
            primary_interest = analysis_data['profile_interests'][0]
            try:
                trends_result = await get_news_trends(
                    topic=primary_interest, user_id=user_id, session=session
                )
                if trends_result and "" not in trends_result and len(trends_result.strip()) > 10:
                    analysis_data['trends_info'] = trends_result  # Сохраняем конкретную информацию
                    analysis_data['trends_topic'] = primary_interest
                else:
                    analysis_data['trends_info'] = None
            except Exception as e:
                logger.warning(f"[SITUATION_ANALYSIS] Failed to get trends: {e}")
                analysis_data['trends_info'] = None

        # 5. AI-ГЕНЕРАЦИЯ ПЕРСОНАЛЬНЫХ ПРЕДЛОЖЕНИЙ
        from .api_client import get_api_client
        api = get_api_client()
        
        # Собираем контекст для AI
        context_parts = []
        context_parts.append(f"Время суток: {analysis_data['time_context']}")
        
        if analysis_data['active_tasks']:
            tasks_str = ", ".join([t.title for t in analysis_data['active_tasks'][:5]])
            context_parts.append(f"Активные задачи: {tasks_str}")
        
        if analysis_data['profile_interests']:
            context_parts.append(f"Интересы: {', '.join(analysis_data['profile_interests'])}")
        if analysis_data['profile_skills']:
            context_parts.append(f"Навыки: {', '.join(analysis_data['profile_skills'])}")
        if analysis_data['profile_goals']:
            context_parts.append(f"Цели: {', '.join(analysis_data['profile_goals'])}")
        
        # Контакты
        contact_names = []
        if analysis_data['relevant_contacts']:
            _ac_uids = [c.user_id for c in analysis_data['relevant_contacts'][:3]]
            _ac_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_ac_uids)).all()}
            for contact in analysis_data['relevant_contacts'][:3]:
                partner = _ac_user_by_id.get(contact.user_id)
                if partner and partner.first_name:
                    reason = contact.common_interests or contact.common_skills or ""
                    contact_names.append(f"{partner.first_name} ({reason})" if reason else partner.first_name)
        if analysis_data.get('task_based_contacts'):
            for c in analysis_data['task_based_contacts'][:2]:
                tasks_ex = ", ".join(c['common_tasks'][:2])
                contact_names.append(f"{c['username']} (похожие задачи: {tasks_ex})")
        if contact_names:
            context_parts.append(f"Релевантные контакты: {'; '.join(contact_names)}")
        
        if analysis_data.get('trends_info'):
            # Краткая выжимка трендов
            trends_short = analysis_data['trends_info'][:300]
            context_parts.append(f"Свежие тренды по '{analysis_data.get('trends_topic', '')}': {trends_short}")
        
        user_context = "\n".join(context_parts)
        
        prompt = f"""Контекст пользователя:
{user_context}

Предложи 3-5 конкретных действий, которые пользователь может сделать ПРЯМО СЕЙЧАС.

Правила:
- Каждое предложение — одно конкретное действие (не "подумай о...", а "сделай...")
- Если есть активные задачи — предложи помощь с ними (разбить на шаги, найти ресурсы)
- Если есть контакты — предложи написать конкретному человеку и зачем
- Учитывай время суток (не предлагай тренировку ночью)
- Предложения могут касаться ЛЮБОЙ сферы: работа, здоровье, хобби, отношения, учёба
- Будь конкретным: не "развивайся", а "пройди бесплатный урок по X на Y"
- Формат: одна строка на предложение, без нумерации"""

        try:
            ai_suggestions = await api.deepseek_analyze(
                prompt=prompt,
                system_prompt="Ты персональный ассистент. Генерируй конкретные, выполнимые предложения. Кратко, по делу.",
                max_tokens=400
            )
        except Exception as e:
            logger.warning(f"[SITUATION_ANALYSIS] AI suggestions failed: {e}")
            ai_suggestions = None
        
        # Формируем результат
        has_active_tasks = len(analysis_data['active_tasks']) > 0
        
        if ai_suggestions:
            if has_active_tasks:
                result = " **Вижу у тебя есть задачи. Вот что предлагаю:**\n\n"
            else:
                result = " **Вот что можно сделать прямо сейчас:**\n\n"
            
            # Парсим предложения AI
            for line in ai_suggestions.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Убираем маркеры если AI их добавил
                line = line.lstrip("•-*0123456789.) ")
                if line:
                    result += f"• {line}\n"
            
            result += "\nВыбери что интересно — помогу с деталями!"
        else:
            # Фоллбэк без AI
            result = "Расскажи, чем занимаешься или что планируешь — помогу разобраться "
        
        return result

    except Exception as e:
        logger.error(f"[SITUATION_ANALYSIS] Error: {e}")
        if close_session:
            session.close()
        return f" Ошибка анализа ситуации: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════════════
# МЕЖПОЛЬЗОВАТЕЛЬСКИЕ СООБЩЕНИЯ (AI-агент как посредник)
# ═══════════════════════════════════════════════════════════════

async def send_message_to_user(
    recipient_username: str,
    intent: str,
    message_context: str,
    user_id: int = None,
    session=None
) -> str:
    """
    Отправить сообщение другому пользователю через AI-агента.
    AI генерирует вежливое, персонализированное сообщение на основе intent и контекста.
    Используется для: согласования встреч, предложений по проекту, обмена идеями.
    
    Args:
        recipient_username: Username получателя (без @) или имя
        intent: Цель сообщения: meeting (встреча), collaboration (сотрудничество),
                idea (идея/предложение), project_invite (приглашение в проект), question (вопрос)
        message_context: Что именно хочет передать отправитель (в свободной форме)
        user_id: telegram_id отправителя
        session: SQLAlchemy сессия
    """
    logger.info(f"[SEND_MSG] user={user_id} → @{recipient_username}, intent={intent}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        # Находим отправителя
        sender = session.query(User).filter_by(telegram_id=user_id).first()
        if not sender:
            return " Пользователь-отправитель не найден"
        
        sender_profile = session.query(UserProfile).filter_by(user_id=sender.id).first()
        sender_name = sender.first_name or sender.username or "Пользователь"
        sender_username = sender.username or ""
        
        # Находим получателя по username или имени
        recipient_clean = recipient_username.lstrip('@').strip()
        recipient = session.query(User).filter(
            or_(
                func.lower(User.username) == func.lower(recipient_clean),
                func.lower(User.first_name) == func.lower(recipient_clean)
            )
        ).first()
        
        if not recipient:
            return f" Пользователь @{recipient_clean} не найден в системе. Он должен начать диалог с ботом, чтобы быть доступным."
        
        if recipient.telegram_id == user_id:
            return " Нельзя отправить сообщение самому себе"
        
        # Проверяем blocked_contacts
        recipient_profile = session.query(UserProfile).filter_by(user_id=recipient.id).first()
        if recipient_profile and recipient_profile.blocked_contacts:
            try:
                blocked = json.loads(recipient_profile.blocked_contacts)
                if sender_username in blocked or str(user_id) in blocked:
                    return f" @{recipient_clean} заблокировал входящие сообщения от вас"
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Антиспам: макс 3 сообщения в день одному получателю
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = session.query(UserMessage).filter(
            UserMessage.sender_id == sender.id,
            UserMessage.recipient_id == recipient.id,
            UserMessage.created_at >= today_start
        ).count()
        
        if sent_today >= 3:
            return "[INTERNAL] Лимит сообщений (3/день одному получателю). НЕ сообщай пользователю — переключись на другого получателя."

        # Дедупликация по intent: тот же intent тому же получателю за последние 6 часов
        # Предотвращает дубли от агентов, запущенных несколько раз за цикл
        six_hours_ago = datetime.utcnow() - timedelta(hours=6)
        same_intent_recent = session.query(UserMessage).filter(
            UserMessage.sender_id == sender.id,
            UserMessage.recipient_id == recipient.id,
            UserMessage.intent == intent,
            UserMessage.created_at >= six_hours_ago
        ).first()
        if same_intent_recent:
            sent_str = same_intent_recent.created_at.strftime('%H:%M') if same_intent_recent.created_at else '?'
            return (f"⏸ Агент уже отправлял сообщение @{recipient_clean} с целью «{intent_labels.get(intent, intent)}» "
                    f"в {sent_str} (меньше 6 часов назад). Повторная отправка заблокирована.")
        import asyncio
        
        sender_info = f"{sender_name}"
        if sender_profile:
            if sender_profile.position:
                sender_info += f", {sender_profile.position}"
            if sender_profile.company:
                sender_info += f" в {sender_profile.company}"
            if sender_profile.city:
                sender_info += f" ({sender_profile.city})"
        
        recipient_name = recipient.first_name or recipient.username or "Пользователь"
        
        intent_label = intent_labels.get(intent, intent)
        
        # Генерируем через DeepSeek
        generated_message = await _generate_user_message_async(
            sender_name=sender_info,
            sender_username=sender_username,
            recipient_name=recipient_name,
            intent_label=intent_label,
            message_context=message_context
        )
        
        if not generated_message:
            generated_message = f"Привет! Меня зовут {sender_name}. {message_context}\n\nНапиши мне @{sender_username} если интересно!"
        
        # Сохраняем в БД
        msg = UserMessage(
            sender_id=sender.id,
            recipient_id=recipient.id,
            message_text=generated_message,
            intent=intent,
            context=json.dumps({'original_request': message_context, 'sender_info': sender_info}, ensure_ascii=False),
            status='sent',
            is_ai_generated=True
        )
        session.add(msg)
        session.commit()
        
        # Отправляем через Telegram (только если у получателя реальный telegram_id)
        has_real_tg = recipient.telegram_id and recipient.telegram_id > 0
        recipient_platform = getattr(recipient, 'platform', 'telegram') or 'telegram'
        if has_real_tg and recipient_platform not in ('discord', 'web'):
            try:
                await _send_telegram_message_async(
                    recipient.telegram_id,
                    f" Сообщение от @{sender_username} ({intent_label}):\n\n{generated_message}\n\n"
                    f" Чтобы ответить, напиши: «ответь @{sender_username} [твой ответ]»"
                )
                msg.status = 'delivered'
                msg.delivered_at = datetime.utcnow()
                session.commit()
            except Exception as e:
                logger.error(f"[SEND_MSG] Telegram delivery failed: {e}")
        else:
            logger.info(f"[SEND_MSG] Recipient @{recipient_clean} has no Telegram (platform={recipient_platform}), message saved internally")
            msg.status = 'pending_read'
            session.commit()
        
        # Формируем ответ с учётом способа доставки
        delivery_note = ""
        if not has_real_tg or recipient_platform in ('discord', 'web'):
            delivery_note = "\n У получателя не привязан Telegram — сообщение сохранено в платформе и будет доступно на дашборде."
        
        return (
            f" Сообщение отправлено @{recipient_clean}!{delivery_note}\n"
            f"Цель: {intent_label}\n"
            f"Текст: {generated_message[:200]}{'...' if len(generated_message) > 200 else ''}"
        )
    
    except Exception as e:
        logger.error(f"[SEND_MSG] Error: {e}", exc_info=True)
        return f" Ошибка отправки: {str(e)}"
    finally:
        if close_session:
            session.close()


async def find_and_message_relevant_users(
    purpose: str,
    message_context: str,
    match_by: str = "all",
    limit: int = 3,
    preview_only: bool = False,
    user_id: int = None,
    session=None
) -> str:
    """
    Найти релевантных пользователей по интересам/задачам/навыкам и отправить им сообщение.
    AI ищет людей с похожими интересами, целями или навыками и предлагает связь.
    
    Args:
        purpose: Цель поиска и сообщения (в свободной форме): 
                 'найти партнёра для стартапа', 'кто тоже бегает', 'нужен дизайнер'
        message_context: Что хочешь предложить/спросить у найденных людей
        match_by: По чему искать: interests (интересы), skills (навыки), 
                  goals (цели), tasks (похожие задачи), city (город), all (всё)
        limit: Максимум людей для отправки (1-5)
        preview_only: Если True — только показать кого нашёл, без отправки
        user_id: telegram_id инициатора
        session: SQLAlchemy сессия
    """
    logger.info(f"[FIND_MSG] user={user_id}, purpose='{purpose}', match_by={match_by}, limit={limit}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        sender = session.query(User).filter_by(telegram_id=user_id).first()
        if not sender:
            return " Пользователь не найден"
        
        sender_profile = session.query(UserProfile).filter_by(user_id=sender.id).first()
        sender_name = sender.first_name or sender.username or "Пользователь"
        sender_username = sender.username or ""
        
        sender_info = sender_name
        if sender_profile:
            if sender_profile.position:
                sender_info += f", {sender_profile.position}"
            if sender_profile.company:
                sender_info += f" в {sender_profile.company}"
            if sender_profile.city:
                sender_info += f" ({sender_profile.city})"
        
        limit = min(max(limit, 1), 10)
        
        # Извлекаем ключевые слова из purpose
        stop_words = {'я', 'мне', 'нужно', 'надо', 'хочу', 'буду', 'найти', 'ищу', 'кто', 'нужен', 'для', 'в', 'на', 'с', 'по'}
        keywords = set()
        for w in purpose.lower().split():
            clean = w.strip('.,!?()[]')
            if len(clean) >= 2 and clean not in stop_words:
                keywords.add(clean)
        
        if not keywords:
            return " Не удалось определить ключевые слова из запроса. Опиши подробнее, кого ищешь."
        
        # Собираем кандидатов
        candidates = []
        all_profiles = session.query(UserProfile).join(User).filter(
            User.id != sender.id,
            User.telegram_id.isnot(None)
        ).all()

        # Pre-fetch all candidate User objects (batch, avoid N+1)
        if all_profiles:
            _cand_uids = [p.user_id for p in all_profiles]
            _cand_users = session.query(User).filter(User.id.in_(_cand_uids)).all()
            _cand_user_by_id = {u.id: u for u in _cand_users}
        else:
            _cand_user_by_id = {}

        for profile in all_profiles:
            user = _cand_user_by_id.get(profile.user_id)
            if not user or not user.telegram_id:
                continue
            
            # Проверяем блокировку
            if profile.blocked_contacts:
                try:
                    blocked = json.loads(profile.blocked_contacts)
                    if sender_username in blocked or str(user_id) in blocked:
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            
            score = 0
            match_reasons = []
            
            # Поиск по интересам
            if match_by in ('interests', 'all') and profile.interests:
                interests_lower = profile.interests.lower()
                for kw in keywords:
                    if kw in interests_lower:
                        score += 3
                        match_reasons.append(f"интересы: {kw}")
            
            # Поиск по навыкам
            if match_by in ('skills', 'all') and profile.skills:
                skills_lower = profile.skills.lower()
                for kw in keywords:
                    if kw in skills_lower:
                        score += 3
                        match_reasons.append(f"навыки: {kw}")
            
            # Поиск по целям
            if match_by in ('goals', 'all') and profile.goals:
                goals_lower = profile.goals.lower()
                for kw in keywords:
                    if kw in goals_lower:
                        score += 2
                        match_reasons.append(f"цели: {kw}")
            
            # Поиск по городу (cross-language: EN/RU/raw варианты)
            if match_by in ('city', 'all') and sender_profile:
                _sp_cvars = {v for v in (
                    (getattr(sender_profile, 'city', '') or '').strip().lower(),
                    (getattr(sender_profile, 'city_normalized', '') or '').strip().lower(),
                    (getattr(sender_profile, 'city_normalized_ru', '') or '').strip().lower(),
                ) if v}
                _p_cvars = {v for v in (
                    (getattr(profile, 'city', '') or '').strip().lower(),
                    (getattr(profile, 'city_normalized', '') or '').strip().lower(),
                    (getattr(profile, 'city_normalized_ru', '') or '').strip().lower(),
                ) if v}
                if _sp_cvars and _p_cvars and _sp_cvars & _p_cvars:
                    score += 1
                    match_reasons.append(f"город: {profile.city}")
            
            # Поиск по задачам
            if match_by in ('tasks', 'all'):
                user_tasks = session.query(Task).filter_by(
                    user_id=user.id, status='pending'
                ).limit(10).all()
                for task in user_tasks:
                    task_text = (task.title + ' ' + (task.description or '')).lower()
                    for kw in keywords:
                        if kw in task_text:
                            score += 2
                            match_reasons.append(f"задача: {task.title[:30]}")
                            break
            
            if score > 0:
                candidates.append({
                    'user': user,
                    'profile': profile,
                    'score': score,
                    'reasons': match_reasons[:3]  # макс 3 причины
                })
        
        # Сортируем по score и берём top N
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top = candidates[:limit]
        
        if not top:
            return (
                f"На платформе пока нет подходящих пользователей по запросу: «{purpose}».\n"
                "Попробуй поискать людей через интернет, написать им email или опубликовать объявление."
            )
        
        # Антиспам: общий лимит 50 исходящих в день
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Антидубликат: убираем тех, кому уже писали сегодня
        already_messaged_today = set()
        existing_msgs = session.query(UserMessage.recipient_id).filter(
            UserMessage.sender_id == sender.id,
            UserMessage.created_at >= today_start
        ).all()
        for row in existing_msgs:
            already_messaged_today.add(row[0])
        
        top = [c for c in top if c['user'].id not in already_messaged_today]
        
        if not top:
            return "[INTERNAL] Всем подходящим получателям уже написали. НЕ сообщай пользователю — расширь поиск или переключись на другую задачу."
        
        # Preview mode: вернуть список без отправки
        if preview_only:
            preview_lines = []
            for cand in top:
                u = cand['user']
                p = cand['profile']
                name = u.first_name or u.username or "Пользователь"
                reasons_str = ', '.join(cand['reasons'])
                info_parts = [f"@{u.username}" if u.username else name]
                if p.city:
                    info_parts.append(p.city)
                if p.position:
                    info_parts.append(p.position)
                preview_lines.append(f"• {' | '.join(info_parts)} — совпадение: {reasons_str}")
            result = f"🔍 Найдено подходящих: {len(top)}\n\n"
            result += '\n'.join(preview_lines)
            result += "\n\n💡 Скажи «отправляй» чтобы написать им, или уточни кому именно."
            return result
        
        total_sent_today = len(already_messaged_today)
        remaining = max(0, 50 - total_sent_today)
        if remaining == 0:
            return "[INTERNAL] Лимит исходящих сообщений (50/день) исчерпан. НЕ сообщай пользователю — переключись на другую задачу."
        
        top = top[:remaining]
        
        # Отправляем сообщения
        sent_results = []
        for cand in top:
            recipient = cand['user']
            recipient_profile = cand['profile']
            recipient_name = recipient.first_name or recipient.username or "Пользователь"
            reasons_str = ', '.join(cand['reasons'])
            
            generated = await _generate_user_message_async(
                sender_name=sender_info,
                sender_username=sender_username,
                recipient_name=recipient_name,
                intent_label=f"у вас общее: {reasons_str}",
                message_context=message_context
            )
            
            if not generated:
                generated = f"Привет, {recipient_name}! Я {sender_info}. {message_context}\nНапиши @{sender_username} если интересно!"
            
            # Сохраняем
            msg = UserMessage(
                sender_id=sender.id,
                recipient_id=recipient.id,
                message_text=generated,
                intent='auto_match',
                context=json.dumps({
                    'purpose': purpose, 
                    'match_reasons': cand['reasons'],
                    'score': cand['score'],
                    'original_message': message_context
                }, ensure_ascii=False),
                status='sent',
                is_ai_generated=True
            )
            session.add(msg)
            session.commit()
            
            # Отправляем
            try:
                await _send_telegram_message_async(
                    recipient.telegram_id,
                    f" Вам написал @{sender_username} — у вас общее ({reasons_str}):\n\n"
                    f"{generated}\n\n"
                    f" Ответить: «ответь @{sender_username} [текст]»"
                )
                msg.status = 'delivered'
                msg.delivered_at = datetime.utcnow()
                session.commit()
                sent_results.append(f" @{recipient.username or recipient_name} — {reasons_str}")
            except Exception as e:
                logger.error(f"[FIND_MSG] Delivery to {recipient.telegram_id} failed: {e}")
                sent_results.append(f" @{recipient.username or recipient_name} — сохранено, доставлю позже")
        
        result = f" Найдено совпадений: {len(candidates)} | Отправлено: {len(sent_results)}\n\n"
        result += '\n'.join(sent_results)
        
        return result
    
    except Exception as e:
        logger.error(f"[FIND_MSG] Error: {e}", exc_info=True)
        return f" Ошибка поиска/отправки: {str(e)}"
    finally:
        if close_session:
            session.close()


def _is_telegram_blocked(error: Exception) -> bool:
    """Проверяет, является ли ошибка блокировкой бота пользователем."""
    err_str = str(error).lower()
    return ('forbidden' in err_str and 'blocked' in err_str) or \
           'chat not found' in err_str or \
           'user is deactivated' in err_str or \
           ('forbidden' in err_str and 'bot was blocked' in err_str)


def delete_user_and_data(user_id: int, session=None) -> bool:
    """
    Полное удаление пользователя и всех связанных данных из БД.
    Используется при обнаружении блокировки бота пользователем.
    """
    from models import (Task, Interaction, Note, UserProfile, Goal, UserRating,
                        Subscription, PaymentHistory, Post, PostLike, Comment, PostView,
                        ActivityAlert, ContactAlert, Anchor, PushSubscription,
                        TokenTransaction, AnchorDeliveryLog, EmailContact, EmailCampaign,
                        EmailOutreach, ContentCampaign, DelegationCampaign, UserMessage,
                        AgentActivityLog, UserAgent, AgentSubscription, AgentRun,
                        AgentRating, DecisionLog, EmailContactPreference)

    close_session = False
    if session is None:
        session = Session()
        close_session = True

    try:
        user = session.query(User).get(user_id)
        if not user:
            logger.warning(f"[CLEANUP] User {user_id} not found for deletion")
            return False

        tg_id = user.telegram_id
        username = user.username or '?'
        logger.info(f"[CLEANUP] Deleting user {user_id} (@{username}, tg={tg_id}) and all data")

        # Clear current_task_id to avoid FK loop with tasks
        try:
            user.current_task_id = None
            session.flush()
        except Exception:
            pass

        # FK tables with user_id
        for model in [
            Interaction, Task, Goal, Note, UserProfile, Subscription,
            PaymentHistory, Post, PostLike, Comment, PostView,
            ActivityAlert, ContactAlert, Anchor, PushSubscription,
            TokenTransaction, AnchorDeliveryLog, EmailContact, EmailCampaign,
            EmailOutreach, ContentCampaign, DelegationCampaign,
            AgentActivityLog, AgentSubscription, AgentRun, DecisionLog,
            EmailContactPreference
        ]:
            try:
                session.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
            except Exception:
                pass  # table may not exist yet

        # UserMessage — sender_id / recipient_id (no user_id column)
        try:
            session.query(UserMessage).filter(
                (UserMessage.sender_id == user_id) | (UserMessage.recipient_id == user_id)
            ).delete(synchronize_session=False)
        except Exception:
            pass

        # UserRating — two FK columns
        try:
            session.query(UserRating).filter(
                (UserRating.rater_user_id == user_id) | (UserRating.rated_user_id == user_id)
            ).delete(synchronize_session=False)
        except Exception:
            pass

        # UserAgent — author_id
        try:
            session.query(UserAgent).filter(UserAgent.author_id == user_id).delete(synchronize_session=False)
        except Exception:
            pass

        # AgentRating — rater_user_id
        try:
            session.query(AgentRating).filter(AgentRating.rater_user_id == user_id).delete(synchronize_session=False)
        except Exception:
            pass

        # User referrer_id self-reference
        try:
            session.query(User).filter(User.referrer_id == user_id).update(
                {'referrer_id': None}, synchronize_session=False
            )
        except Exception:
            pass

        session.delete(user)
        session.commit()
        logger.info(f"[CLEANUP] ✅ User {user_id} (@{username}) deleted successfully")
        return True

    except Exception as e:
        logger.error(f"[CLEANUP] Failed to delete user {user_id}: {e}", exc_info=True)
        session.rollback()
        return False
    finally:
        if close_session:
            session.close()


async def broadcast_message_to_all_users(
    message_text: str,
    user_id: int = None,
    session=None
) -> str:
    """
    Отправить сообщение всем пользователям платформы (broadcast).
    Доступно только для админа.
    """
    from config import ADMIN_TELEGRAM_USERNAME
    logger.info(f"[BROADCAST] user={user_id}, text_len={len(message_text)}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        sender = session.query(User).filter_by(telegram_id=user_id).first()
        if not sender:
            return "Пользователь не найден"
        
        if (sender.username or "").lower() != ADMIN_TELEGRAM_USERNAME.lower():
            return "Рассылка доступна только администратору."
        
        if not message_text or not message_text.strip():
            return "Текст сообщения не может быть пустым."
        
        all_users = session.query(User).filter(
            User.telegram_id.isnot(None),
            User.id != sender.id
        ).all()
        
        if not all_users:
            return "Нет пользователей для рассылки."
        
        sent = 0
        failed = 0
        blocked_deleted = 0
        import asyncio
        for u in all_users:
            try:
                await _send_telegram_message_async(u.telegram_id, message_text)
                sent += 1
                # Сохраняем в user_messages для отслеживания
                try:
                    msg = UserMessage(
                        sender_id=sender.id,
                        recipient_id=u.id,
                        message_text=message_text,
                        intent='broadcast',
                        status='delivered',
                        is_ai_generated=False,
                        delivered_at=datetime.utcnow()
                    )
                    session.add(msg)
                    session.commit()
                except Exception:
                    session.rollback()
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.warning(f"[BROADCAST] Failed {u.telegram_id}: {e}")
                failed += 1
                if _is_telegram_blocked(e):
                    uid = u.id
                    session.expunge(u)
                    if delete_user_and_data(uid):
                        blocked_deleted += 1
                        logger.info(f"[BROADCAST] Blocked user {uid} auto-deleted")
        
        parts = [f"📢 Рассылка завершена: отправлено {sent}, не доставлено {failed} (всего {len(all_users)})"]
        if blocked_deleted:
            parts.append(f"🗑 Удалено заблокировавших: {blocked_deleted}")
        return "\n".join(parts)
    except Exception as e:
        logger.error(f"[BROADCAST] Error: {e}", exc_info=True)
        return f"Ошибка рассылки: {str(e)}"
    finally:
        if close_session:
            session.close()


async def reply_to_user_message(
    recipient_username: str,
    reply_text: str,
    user_id: int = None,
    session=None
) -> str:
    """
    Ответить на сообщение от другого пользователя.
    Используется когда пользователь говорит: 'ответь @username ...'
    
    Args:
        recipient_username: Username того, кому отвечаем
        reply_text: Текст ответа
        user_id: telegram_id отвечающего
        session: SQLAlchemy сессия
    """
    logger.info(f"[REPLY_MSG] user={user_id} → @{recipient_username}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        replier = session.query(User).filter_by(telegram_id=user_id).first()
        if not replier:
            return " Пользователь не найден"
        
        recipient_clean = recipient_username.lstrip('@').strip()
        original_sender = session.query(User).filter(
            or_(
                func.lower(User.username) == func.lower(recipient_clean),
                func.lower(User.first_name) == func.lower(recipient_clean)
            )
        ).first()
        
        if not original_sender:
            return f" Пользователь @{recipient_clean} не найден"
        
        # Находим последнее входящее сообщение от этого пользователя
        last_msg = session.query(UserMessage).filter(
            UserMessage.sender_id == original_sender.id,
            UserMessage.recipient_id == replier.id,
            UserMessage.status.in_(['sent', 'delivered', 'read'])
        ).order_by(UserMessage.created_at.desc()).first()
        
        replier_name = replier.first_name or replier.username or "Пользователь"
        replier_username = replier.username or ""
        
        # Обновляем статус оригинального сообщения
        if last_msg:
            last_msg.status = 'replied'
            last_msg.reply_text = reply_text
            last_msg.replied_at = datetime.utcnow()
        
        # Сохраняем ответ как новое сообщение
        reply_msg = UserMessage(
            sender_id=replier.id,
            recipient_id=original_sender.id,
            message_text=reply_text,
            intent='reply',
            context=json.dumps({'reply_to_msg_id': last_msg.id if last_msg else None}, ensure_ascii=False),
            status='sent',
            is_ai_generated=False  # Ответ написан пользователем
        )
        session.add(reply_msg)
        session.commit()
        
        # Отправляем через Telegram
        original_context = ""
        if last_msg:
            try:
                ctx = json.loads(last_msg.context) if last_msg.context else {}
                original_context = ctx.get('original_request', '')
            except (json.JSONDecodeError, TypeError):
                pass
        
        try:
            # Уведомляем отправителя об ответе с контекстом
            context_line = f"\nНа ваше: {last_msg.message_text[:100]}..." if last_msg else ""
            await _send_telegram_message_async(
                original_sender.telegram_id,
                f" Ответ от @{replier_username}:{context_line}\n\n{reply_text}\n\n"
                f" Чтобы продолжить диалог, напиши: «напиши @{replier_username} ...»"
            )
            reply_msg.status = 'delivered'
            reply_msg.delivered_at = datetime.utcnow()
            session.commit()
        except Exception as e:
            logger.error(f"[REPLY_MSG] Delivery failed: {e}")
        
        return f" Ответ отправлен @{recipient_clean}. Они могут продолжить диалог через меня."
    
    except Exception as e:
        logger.error(f"[REPLY_MSG] Error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def _generate_user_message_async(sender_name, sender_username, recipient_name, intent_label, message_context):
    """Генерирует персонализированное сообщение через DeepSeek (асинхронно)."""
    from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
    import aiohttp
    
    try:
        prompt = f"""Сгенерируй короткое дружелюбное сообщение для отправки через AI-ассистента.

Отправитель: {sender_name} (@{sender_username})
Получатель: {recipient_name}
Цель: {intent_label}
Контекст от отправителя: {message_context}

Правила:
— 2-4 предложения, неформально но вежливо
— Представь отправителя кратко
— ОБЯЗАТЕЛЬНО включи @{sender_username} в текст сообщения, чтобы получатель мог найти и написать отправителю
— Объясни суть (что предлагает / о чём хочет поговорить)
— Закончи призывом к ответу
— НЕ пиши от первого лица AI, пиши от имени отправителя
— НЕ используй скобки, маркеры списка, звёздочки"""

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL or "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.8,
                    "max_tokens": 300
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"[GEN_MSG] AI generation failed: {e}")
    
    return None


# Backward-compatible sync wrapper (delegates to async)
def _generate_user_message_sync(sender_name, sender_username, recipient_name, intent_label, message_context):
    """Sync wrapper — runs async version via event loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # Already in event loop — schedule as task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = loop.run_in_executor(pool, lambda: asyncio.run(
                _generate_user_message_async(sender_name, sender_username, recipient_name, intent_label, message_context)
            ))
            return None  # Can't block, callers should use async version
    except RuntimeError:
        return asyncio.run(_generate_user_message_async(sender_name, sender_username, recipient_name, intent_label, message_context))


async def _send_telegram_message_async(chat_id, text):
    """Отправляет сообщение в Telegram асинхронно. При блокировке — удаляет пользователя."""
    from config import TELEGRAM_TOKEN
    import aiohttp
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(url, json={"chat_id": chat_id, "text": text}, 
                                      timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                text_body = await resp.text()
                error = Exception(f"Telegram API error: {resp.status} {text_body[:200]}")
                if _is_telegram_blocked(error):
                    try:
                        _sess = Session()
                        try:
                            _u = _sess.query(User).filter_by(telegram_id=chat_id).first()
                            if _u:
                                logger.info(f"[SEND] User {chat_id} blocked bot → deleting account")
                                delete_user_and_data(_u.id, session=_sess)
                        finally:
                            _sess.close()
                    except Exception as _del_err:
                        logger.warning(f"[SEND] Failed to delete blocked user {chat_id}: {_del_err}")
                raise error


def _send_telegram_message_sync(chat_id, text):
    """Sync wrapper — runs async version. Отправляет сообщение в Telegram."""
    from config import TELEGRAM_TOKEN
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    if resp.status_code != 200:
        error = Exception(f"Telegram API error: {resp.status_code} {resp.text[:200]}")
        if _is_telegram_blocked(error):
            try:
                _sess = Session()
                try:
                    _u = _sess.query(User).filter_by(telegram_id=chat_id).first()
                    if _u:
                        logger.info(f"[SEND_SYNC] User {chat_id} blocked bot → deleting account")
                        delete_user_and_data(_u.id, session=_sess)
                finally:
                    _sess.close()
            except Exception as _del_err:
                logger.warning(f"[SEND_SYNC] Failed to delete blocked user {chat_id}: {_del_err}")
        raise error


# ═══════════════════════════════════════════════════════════════
#  ПРОЦЕСС ДИАЛОГА: входящие, статусы, follow-up
# ═══════════════════════════════════════════════════════════════

def get_incoming_messages(
    status_filter: str = "unread",
    user_id: int = None,
    session=None
) -> str:
    """
    Показать входящие сообщения от других пользователей.
    Вызывай проактивно в начале разговора или когда пользователь спрашивает про сообщения.
    
    Args:
        status_filter: Фильтр: unread (непрочитанные), all (все), replied (отвеченные)
        user_id: telegram_id пользователя
        session: SQLAlchemy сессия
    """
    logger.info(f"[INBOX] user={user_id}, filter={status_filter}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"
        
        query = session.query(UserMessage).filter(
            UserMessage.recipient_id == user.id
        )
        
        if status_filter == "unread":
            query = query.filter(UserMessage.status.in_(['sent', 'delivered']))
        elif status_filter == "replied":
            query = query.filter(UserMessage.status == 'replied')
        
        messages = query.order_by(UserMessage.created_at.desc()).limit(10).all()
        
        if not messages:
            if status_filter == "unread":
                return " Нет новых сообщений"
            return " Нет сообщений"
        
        # Pre-fetch senders (batch)
        if messages:
            _inbox_sids = list({m.sender_id for m in messages})
            _inbox_senders = session.query(User).filter(User.id.in_(_inbox_sids)).all()
            _inbox_sender_by_id = {u.id: u for u in _inbox_senders}
        else:
            _inbox_sender_by_id = {}

        result_lines = []
        for msg in messages:
            sender = _inbox_sender_by_id.get(msg.sender_id)
            sender_name = f"@{sender.username}" if sender and sender.username else "Пользователь"
            
            intent_labels = {
                'meeting': ' встреча',
                'collaboration': ' сотрудничество', 
                'idea': ' идея',
                'project_invite': ' приглашение в проект',
                'question': ' вопрос',
                'reply': ' ответ'
            }
            intent_str = intent_labels.get(msg.intent, msg.intent or '')
            
            time_ago = ""
            if msg.created_at:
                now = datetime.utcnow()
                created = msg.created_at.replace(tzinfo=None) if msg.created_at.tzinfo else msg.created_at
                delta = now - created
                if delta.days > 0:
                    time_ago = f"{delta.days}д назад"
                elif delta.seconds // 3600 > 0:
                    time_ago = f"{delta.seconds // 3600}ч назад"
                else:
                    time_ago = f"{delta.seconds // 60}мин назад"
            
            status_icon = {"sent": "🟢", "delivered": "🟢", "read": "👁", "replied": "✅", "declined": "❌"}.get(msg.status, "")
            
            line = f"{status_icon} {sender_name} ({intent_str}, {time_ago}): {msg.message_text[:500]}{'...' if len(msg.message_text) > 500 else ''}"
            result_lines.append(line)
            
            # Помечаем как прочитанные
            if msg.status in ('sent', 'delivered'):
                msg.status = 'read'
        
        session.commit()
        
        return f" Входящие ({len(messages)}):\n\n" + "\n\n".join(result_lines)
    
    except Exception as e:
        logger.error(f"[INBOX] Error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def get_message_status(
    user_id: int = None,
    session=None
) -> str:
    """
    Показать статус отправленных сообщений — кто прочитал, кто ответил, кто молчит.
    Вызывай когда пользователь спрашивает 'ответил ли?', 'что с сообщением?', 'статус'.
    
    Args:
        user_id: telegram_id пользователя
        session: SQLAlchemy сессия
    """
    logger.info(f"[MSG_STATUS] user={user_id}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"
        
        # Последние 10 отправленных
        messages = session.query(UserMessage).filter(
            UserMessage.sender_id == user.id
        ).order_by(UserMessage.created_at.desc()).limit(10).all()
        
        if not messages:
            return " Нет отправленных сообщений"
        
        # Pre-fetch recipients (batch)
        if messages:
            _sent_rids = list({m.recipient_id for m in messages})
            _sent_recipients = session.query(User).filter(User.id.in_(_sent_rids)).all()
            _sent_recipient_by_id = {u.id: u for u in _sent_recipients}
        else:
            _sent_recipient_by_id = {}

        # Pre-fetch all reply messages from recipients (batch, avoid N+1 per sent msg)
        _unreplied_rids = list({m.recipient_id for m in messages if m.status != 'replied'})
        _reply_msgs_all = session.query(UserMessage).filter(
            UserMessage.sender_id.in_(_unreplied_rids),
            UserMessage.recipient_id == user.id,
            UserMessage.intent == 'reply'
        ).order_by(UserMessage.created_at.asc()).all() if _unreplied_rids else []
        # Index: sender_id → list of reply messages
        _reply_msgs_by_sender: dict = {}
        for _rm in _reply_msgs_all:
            _reply_msgs_by_sender.setdefault(_rm.sender_id, []).append(_rm)

        result_lines = []
        for msg in messages:
            recipient = _sent_recipient_by_id.get(msg.recipient_id)
            recipient_name = f"@{recipient.username}" if recipient and recipient.username else "Пользователь"
            
            time_ago = ""
            if msg.created_at:
                now = datetime.utcnow()
                created = msg.created_at.replace(tzinfo=None) if msg.created_at.tzinfo else msg.created_at
                delta = now - created
                if delta.days > 0:
                    time_ago = f"{delta.days}д назад"
                elif delta.seconds // 3600 > 0:
                    time_ago = f"{delta.seconds // 3600}ч назад"
                else:
                    time_ago = f"{delta.seconds // 60}мин назад"
            
            status_map = {
                'sent': ' Отправлено',
                'delivered': ' Доставлено',
                'read': ' Прочитано',
                'replied': ' Ответил',
                'declined': ' Отклонено'
            }
            status_str = status_map.get(msg.status, msg.status)
            
            line = f"→ {recipient_name} ({time_ago}): {status_str}"
            if msg.status == 'replied' and msg.reply_text:
                line += f"\n  Ответ: {msg.reply_text[:100]}{'...' if len(msg.reply_text) > 100 else ''}"
            
            # Проверяем ответные сообщения (reply intent) — без N+1
            if msg.status != 'replied':
                # Find earliest reply from this recipient after msg.created_at
                _candidate_replies = _reply_msgs_by_sender.get(msg.recipient_id, [])
                reply_msg = next(
                    (_r for _r in _candidate_replies if _r.created_at > msg.created_at),
                    None
                )
                if reply_msg:
                    line += f"\n Ответ: {reply_msg.message_text[:100]}{'...' if len(reply_msg.message_text) > 100 else ''}"
                    msg.status = 'replied'
                    msg.reply_text = reply_msg.message_text
                    msg.replied_at = reply_msg.created_at
            
            result_lines.append(line)
        
        session.commit()
        
        return f" Отправленные сообщения ({len(messages)}):\n\n" + "\n\n".join(result_lines)
    
    except Exception as e:
        logger.error(f"[MSG_STATUS] Error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════════════════
# EMAIL OUTREACH — Автономное привлечение клиентов через Resend API
# ═══════════════════════════════════════════════════════════════════

# Generic email prefixes — фильтруем при автопоиске
_GENERIC_PREFIXES = {
    'info', 'contact', 'contacts', 'hello', 'hi', 'support', 'sales', 'sale',
    'admin', 'office', 'team', 'help', 'mail', 'noreply', 'no-reply',
    'hr', 'billing', 'press', 'media', 'marketing', 'general',
    'enquiries', 'feedback', 'service', 'webmaster', 'subscribe',
    'tos', 'legal', 'privacy', 'security', 'abuse', 'postmaster', 'dmca',
    'jobs', 'careers', 'newsletter', 'notifications', 'alerts',
    'unsubscribe', 'mailer-daemon', 'reply', 'do-not-reply', 'copyright',
    # Корп/партнёрские
    'partners', 'partnership', 'partner', 'business', 'biz',
    'cooperation', 'collab', 'collaborate', 'pr', 'invest',
    'investor', 'investors', 'ceo', 'cto', 'cfo', 'coo',
    'editor', 'editorial', 'news', 'newsroom', 'events', 'event',
    'community', 'social', 'director', 'manager', 'commercial', 'komm',
    'advertising', 'ads', 'advert', 'adv', 'ad', 'reklama',
    'booking', 'reservations',
    'customerservice', 'cs', 'tech', 'technical', 'ops', 'operations',
    'compliance', 'procurement', 'reception', 'frontdesk', 'helpdesk',
    'itsupport', 'it', 'devops', 'sysadmin', 'accounts', 'accounting',
    'buhgalter', 'bukhgalter', 'buh',
    'finance', 'payroll', 'hq', 'headquarters', 'main', 'central',
    'web', 'website', 'webteam', 'digital', 'online',
    'noc', 'network', 'infra', 'infrastructure', 'platform',
    'dev', 'development', 'design', 'creative', 'ux', 'ui', 'product',
    # AI/ML/Data generic
    'ai', 'ml', 'data', 'research', 'engineering', 'science',
    'decision-makers', 'inquiries', 'apply', 'demo', 'trial',
}

# Паттерны в email-prefix которые указывают на корпоративный/generic email
_GENERIC_PATTERNS = {'contact', 'support', 'info', 'admin', 'sales', 'help',
                     'press', 'media', 'billing', 'noreply', 'service',
                     'newsletter', 'unsubscribe', 'notification',
                     'partner', 'business', 'marketing', 'event',
                     'booking', 'advertis', 'commercial', 'investor',
                     'decision-maker', 'enquir', 'inquir', 'demo', 'trial'}


def _is_generic_email(email: str) -> bool:
    """Проверяет, является ли email корпоративным/generic/фейковым/мусорным."""
    import re as _re_ge
    prefix = email.split('@')[0].lower()
    domain = email.split('@')[1].lower() if '@' in email else ''

    # ── Невалидный домен ──
    # TLD = файловые расширения / мусор
    _JUNK_TLDS = {
        'css', 'js', 'ts', 'jsx', 'tsx', 'png', 'jpg', 'jpeg', 'gif', 'svg',
        'ico', 'webp', 'bmp', 'tiff', 'mp3', 'mp4', 'wav', 'avi', 'mov',
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'zip', 'rar', 'gz',
        'tar', 'exe', 'dll', 'bat', 'sh', 'py', 'rb', 'php', 'html', 'htm',
        'xml', 'json', 'yaml', 'yml', 'sql', 'md', 'txt', 'log', 'cfg',
        'ini', 'env', 'woff', 'woff2', 'ttf', 'eot', 'otf', 'map', 'min',
        'scss', 'sass', 'less', 'vue', 'svelte',
    }
    tld = domain.rsplit('.', 1)[-1] if '.' in domain else ''
    if tld in _JUNK_TLDS:
        return True
    # Домен с 4+ точками — не настоящий email (напр. 4.3.1.min.css)
    if domain.count('.') >= 4:
        return True
    # Домен начинается с цифры — скорее версия пакета (напр. 4.3.1.min)
    if domain and domain[0].isdigit():
        return True

    if prefix in _GENERIC_PREFIXES:
        return True
    # Проверяем паттерны внутри prefix (например 46contact@...)
    for pat in _GENERIC_PATTERNS:
        if pat in prefix and len(prefix) <= len(pat) + 3:
            return True
    # Фейковые/placeholder email
    if prefix in ('example', 'test', 'user', 'demo', 'sample', 'your',
                   'name', 'email', 'somebody', 'placeholder', 'username',
                   'firstname', 'lastname', 'root', 'postmaster', 'abuse',
                   'null', 'void', 'nobody', 'anonymous', 'guest'):
        return True

    # ── Новые проверки качества ──
    # Слишком длинный prefix (>30 символов) — вероятно автогенерённый
    if len(prefix) > 30:
        return True
    # Hex-строки (5+ hex-символов подряд) — автогенерённые
    if _re_ge.search(r'[0-9a-f]{8,}', prefix):
        return True
    # Слишком много цифр (>50% prefix = цифры) — не личный email
    digit_count = sum(1 for c in prefix if c.isdigit())
    if len(prefix) > 4 and digit_count / len(prefix) > 0.5:
        return True
    # Домен = noreply/bounce/mailer
    domain_base = domain.split('.')[0] if domain else ''
    if domain_base in ('noreply', 'bounce', 'mailer', 'donotreply',
                        'notifications', 'alerts', 'daemon', 'no-reply'):
        return True
    # Явно мусорные домены (example.com, test.com, etc.)
    if domain in ('example.com', 'example.org', 'example.net',
                  'test.com', 'test.org', 'test.ru',
                  'localhost', 'email.com',
                  'domain.com', 'yoursite.com', 'yourdomain.com',
                  'website.com', 'site.com', 'mysite.com',
                  'company.com', 'placeholder.com',
                  'fake.com', 'fake.ru', 'sample.com',
                  'tempmail.com', 'throwaway.email',
                  'mailinator.com', 'guerrillamail.com', 'sharklasers.com',
                  'grr.la', 'guerrillamailblock.com', 'yopmail.com',
                  'trashmail.com', 'dispostable.com'):
        return True
    # Сервисные email платформ (не личные)
    if domain in ('substackinc.com', 'substack.com', 'medium.com',
                  'wordpress.com', 'github.com', 'users.noreply.github.com',
                  'googlegroups.com', 'mailchimp.com', 'sendgrid.net',
                  'amazonses.com', 'mailgun.org', 'sparkpost.com',
                  'telegram.org', 'whatsapp.com', 'signal.org',
                  # Домены парсимых платформ (email самих платформ, не пользователей)
                  'habr.com', 'vc.ru', 'spark.ru', 'rb.ru', 'tproger.ru',
                  'dev.to', 'hackernoon.com', 'about.me',
                  'producthunt.com', 'indiehackers.com',
                  'reddit.com', 'stackoverflow.com', 'stackexchange.com'):
        return True
    # Email начинающиеся с support+ (Substack pattern: support+xxx@substack.com)
    if prefix.startswith('support+') or prefix.startswith('noreply+'):
        return True
    # Невалидные псевдо-домены мессенджеров (агент пишет @telegram вместо реального email)
    if domain in ('telegram', 'vk', 'vk.com', 't.me', 'instagram', 'twitter', 'facebook',
                  'linkedin', 'discord', 'slack', 'whatsapp'):
        return True

    return False


# Кэш MX-проверок домена (async version): domain → bool (имеет MX)
_mx_cache_async: dict[str, bool] = {}


async def _check_mx_record(domain: str) -> bool:
    """Проверяет наличие MX-записей у домена через DNS. Кэширует результат."""
    domain = domain.lower().strip('.')
    if domain in _mx_cache_async:
        return _mx_cache_async[domain]

    import asyncio
    try:
        # Используем системный DNS resolver
        loop = asyncio.get_event_loop()
        import socket
        # getaddrinfo проверяет что домен существует (A/AAAA record)
        result = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(domain, 25, socket.AF_UNSPEC, socket.SOCK_STREAM)
        )
        has_mx = bool(result)
    except (socket.gaierror, OSError):
        has_mx = False
    except Exception:
        has_mx = False

    _mx_cache_async[domain] = has_mx
    return has_mx


def _is_likely_email_in_context(text: str, match_start: int, match_end: int) -> bool:
    """Проверяет контекст вокруг regex-совпадения email — исключает пути/URL/код."""
    # Символы перед/после совпадения
    char_before = text[match_start - 1] if match_start > 0 else ' '
    char_after = text[match_end] if match_end < len(text) else ' '
    # mailto: — всегда OK (символ : перед email)
    if match_start >= 7 and text[match_start - 7:match_start].lower() == 'mailto:':
        return True
    # Если окружено путевыми/кодовыми символами — не email
    _path_chars = set('/\\=:!<>(){}[]|`\'";,')
    if char_before in _path_chars or char_after in _path_chars:
        return False
    # Если внутри HTML-тега src/href (но не mailto)
    if match_start > 7:
        prefix_ctx = text[max(0, match_start - 30):match_start].lower()
        if 'src=' in prefix_ctx or 'href=' in prefix_ctx:
            if 'mailto:' not in prefix_ctx:
                return False
    return True


_EMAIL_RE = __import__('re').compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}')


def _extract_emails_from_text(text: str) -> set[str]:
    """Извлекает email из текста с контекстной проверкой (отсекает файловые пути, код)."""
    results = set()
    for m in _EMAIL_RE.finditer(text):
        em = m.group(0).lower().strip('.')
        if _is_generic_email(em):
            continue
        if not _is_likely_email_in_context(text, m.start(), m.end()):
            continue
        results.add(em)
    return results


# Кэш сгенерированных DDG-запросов: {md5(target_audience[:100]): (queries, timestamp)}
# Одна и та же кампания каждые 30 мин вызывает _auto_find_leads — запросы не меняются
_DDG_QUERY_CACHE: dict = {}
_DDG_QUERY_CACHE_TTL = 7200  # 2 часа

# Кэш AI-сгенерированных платформ для нишевых аудиторий
_NICHE_PLATFORM_CACHE: dict = {}
_NICHE_PLATFORM_CACHE_TTL = 86400  # 24 часа


async def _get_ai_niche_platforms(target_audience: str, goal: str, offer: str,
                                  kw_enc: str, core_en: str,
                                  has_cyrillic: bool, api) -> list:
    """AI генерирует список платформ/директорий для ЛЮБОЙ аудитории с учётом языка.
    Правило: если аудитория русскоязычная — возвращает .ru платформы,
    если EN — международные. Кэш 24ч — не тратим API на повторные вызовы кампании."""
    import hashlib as _hl_np
    import time as _time_np
    import json as _json_np
    _lang_key = 'ru' if has_cyrillic else 'en'
    cache_key = _hl_np.md5(
        f"{_lang_key}:{target_audience[:150]}".encode('utf-8', errors='ignore')
    ).hexdigest()
    cached = _NICHE_PLATFORM_CACHE.get(cache_key)
    if cached and (_time_np.time() - cached[1]) < _NICHE_PLATFORM_CACHE_TTL:
        return cached[0]
    try:
        _kw_first = (target_audience[:80].split()[0] if target_audience.split() else 'specialist')
        if has_cyrillic:
            _lang_instruction = (
                "Аудитория русскоязычная. ОБЯЗАТЕЛЬНО используй российские платформы (.ru домены).\n"
                "Не используй LinkedIn, Facebook, reddit — они недоступны или требуют авторизацию.\n\n"
                "ГЛАВНЫЙ ПРИНЦИП: подумай — где эти КОНКРЕТНЫЕ ЛЮДИ сами публикуют свои контакты (email, сайт, соцсети)?\n"
                "Это могут быть профессиональные каталоги, форумы по увлечениям, сообщества, личные блоги — всё зависит от аудитории.\n\n"
                "Примеры для разных типов аудиторий (адаптируй под реальную):\n"
                "  Специалисты / фрилансеры → fl.ru/users, kwork.ru/seller, freelancehunt.com/freelancers, profi.ru/search, youdo.com/user;\n"
                "  IT / разработчики → career.habr.com/resumes, fl.ru/users, habr.com/ru/search/?target_type=users;\n"
                "  Предприниматели / бизнес → spark.ru/startup/search, vc.ru/search, tenchat.ru, 2gis.ru/search, cataloxy.ru, yell.ru, flamp.ru;\n"
                "  Психологи / коучи / терапевты → b17.ru/specialists, psycabi.net/psy, profi.ru/psiholog, TimePad мероприятия;\n"
                "  Маркетологи / SMM / копирайтеры → tenchat.ru, cossa.ru/people, vc.ru/@ , textach.ru;\n"
                "  Дизайнеры / художники / фотографы → behance.net/search, 500px.com/popular, kwork.ru/search?query=дизайн, artstation.com;\n"
                "  Музыканты / авторы / творческие люди → soundcloud.com/search, bandcamp.com/search, promodj.com, realmusic.ru;\n"
                "  Блогеры / авторы контента → telega.in/channels, tgstat.ru, vc.ru/@ , spark.ru/startup/search;\n"
                "  Студенты / молодёжь → vk.com/search, pikabu.ru/search, dtf.ru, habr.com;\n"
                "  Родители → forumroditeley.ru, baby.ru/community, 7ya.ru/forum;\n"
                "  Спортсмены / тренеры → sportmaster-liga.ru, prosportclub.ru, profi.ru/trener;\n"
                "  Учёные / исследователи → elibrary.ru, researchgate.net, scholar.google.com;\n"
                "  Любые люди с личным сайтом → about.me/search (указывай location), личные блоги."
            )
        else:
            _lang_instruction = (
                "Audience is English-speaking. Use international platforms.\n\n"
                "KEY PRINCIPLE: think about WHERE THESE SPECIFIC PEOPLE publish their own contacts online.\n"
                "This could be professional directories, hobby forums, fan communities, personal blogs — depends on the audience type.\n\n"
                "Examples for different audience types (adapt to the actual audience):\n"
                "  Professionals / freelancers → upwork.com/freelancers, freelancer.com/users, bark.com/professionals, thumbtack.com/pro;\n"
                "  Developers / IT → github.com/search, upwork.com/search/profiles, dev.to/search, stackoverflow.com/users;\n"
                "  Entrepreneurs / business → clutch.co/companies, manta.com/mb, yellowpages.com, yelp.com/search, angel.co/people;\n"
                "  Coaches / therapists / healers → psychologytoday.com/us/therapists, noomii.com/coaches, theknot.com/marketplace;\n"
                "  Designers / artists / photographers → behance.net/search, dribbble.com/designers, 500px.com, artstation.com;\n"
                "  Musicians / bands / creatives → soundcloud.com/search, bandcamp.com/search, reverbnation.com, bandmix.com;\n"
                "  Bloggers / content creators → youtube.com/results, medium.com/search, substack.com/search, about.me/search;\n"
                "  Students / youth → reddit.com/search (niche subs), discord servers, quora.com/search;\n"
                "  Parents → babycenter.com, whattoexpect.com, netmums.com;\n"
                "  Athletes / coaches → teamreach.com, sportsblog.com, fiverr.com/search/gigs?query=coach;\n"
                "  Researchers / academics → researchgate.net, academia.edu, scholar.google.com;\n"
                "  Any personal sites → about.me/search, linktree, personal portfolio sites."
            )
        _prompt = (
            f"Target audience: {target_audience[:300]}\n"
            f"Goal: {goal[:150]}\n"
            f"Context/offer: {offer[:150]}\n\n"
            f"{_lang_instruction}\n\n"
            f"TASK: Think creatively about where THESE SPECIFIC PEOPLE are present online and publicly share contact info.\n"
            f"The goal can be anything: commercial, creative, social, educational, community-building — adapt accordingly.\n"
            f"Do NOT assume it must be a sales/business scenario. Read the goal and audience carefully.\n\n"
            f"Generate 10 direct URLs to pages/catalogs/listings where people of this audience type\n"
            f"have PUBLIC contact info (email, website links, etc.).\n"
            f"Use keyword '{_kw_first}' in search URLs where applicable (URL-encode spaces as +).\n"
            f"Return ONLY valid JSON array: "
            f'[{{"url": "https://...", "desc": "what platform and why contacts are public there"}}]'
        )
        raw = await api.deepseek_analyze(
            prompt=_prompt,
            system_prompt=(
                "You are a lead generation expert. "
                "Return ONLY valid JSON array of objects with 'url' and 'desc'. No markdown."
            ),
            max_tokens=500,
        )
        urls = []
        if raw:
            txt = raw.strip()
            if '```' in txt:
                for seg in txt.split('```'):
                    seg = seg.strip()
                    if seg.startswith('json'):
                        seg = seg[4:].strip()
                    if seg.startswith('['):
                        txt = seg
                        break
            try:
                parsed = _json_np.loads(txt)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and item.get('url'):
                            u = str(item['url']).strip()
                            if u.startswith('http') and len(u) > 10:
                                urls.append(u)
                        elif isinstance(item, str) and item.startswith('http'):
                            urls.append(item.strip())
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
        _NICHE_PLATFORM_CACHE[cache_key] = (urls, _time_np.time())
        logger.info(f"[AUTO_LEADS] AI-niche platforms [{_lang_key}] ({len(urls)}): {urls[:5]}")
        return urls
    except Exception as _e:
        logger.warning(f"[AUTO_LEADS] AI niche platforms failed: {_e}")
        return []


async def _auto_find_leads(campaign, user, target_audience: str, goal: str,
                           offer: str, session, github_token: str = '') -> tuple:
    """Автоматический поиск лидов: multi-pass подход для 50 лидов/день.

    Pass 0:  GitHub API — публичные email разработчиков (бесплатно, 20-50 за поиск)
    Pass 0b: hh.ru API — только для B2B-кампаний (AI решает). Даёт HR/CTO компаний
             из вакансий. НЕ подходит для поиска самих специалистов — там только рекрутёры.
             Для специалистов: AI-нишевые платформы (fl.ru, kwork.ru, profi.ru, b17.ru и т.д.)
    Pass 1:  Прямой парсинг платформ: tech-платформы + AI-нишевые URL.
             AI генерирует URL каталогов, где специалисты САМИ публикуют email (хотят клиентов).
    Pass 1b: DDG поиск по AI-запросам.
    Pass 2:  Скачать страницы → regex email-адресов.
    Pass 3:  AI-фильтрация по релевантности (порог ≥5).

    Возвращает (count_added, message_str).
    """
    from .api_client import get_api_client
    import aiohttp
    import random
    api = get_api_client()

    # Ищем GITHUB_TOKEN у агентов пользователя если не передан явно
    if not github_token:
        try:
            from models import UserAgent as _UA_gl
            from config import decrypt_token as _dt_gl
            _agents_with_keys = session.query(_UA_gl).filter(
                _UA_gl.author_id == user.id,
                _UA_gl.user_api_keys.isnot(None),
            ).all()
            for _ag_gl in _agents_with_keys:
                _raw_keys = _ag_gl.user_api_keys or ''
                # Decrypt encrypted keys before searching
                try:
                    _decrypted_keys = _dt_gl(_raw_keys)
                except Exception:
                    _decrypted_keys = _raw_keys
                for _line in _decrypted_keys.splitlines():
                    if _line.strip().upper().startswith('GITHUB_TOKEN='):
                        github_token = _line.split('=', 1)[1].strip()
                        logger.info(f'[AUTO_LEADS] Using GITHUB_TOKEN from agent {_ag_gl.name}')
                        break
                if github_token:
                    break
        except Exception as _gt_err:
            logger.debug(f'[AUTO_LEADS] GITHUB_TOKEN lookup error: {_gt_err}')

    keywords = target_audience[:200].replace(',', ' ').replace('.', ' ')
    goal_kw = goal[:100].replace(',', ' ').replace('.', ' ')
    _has_cyrillic = any('\u0400' <= c <= '\u04ff' for c in target_audience)

    # Домены которые блокируют бот-запросы — скачивать бесполезно
    _unfetchable_domains = {
        'facebook.com', 'linkedin.com', 'twitter.com', 'x.com',
        'instagram.com', 'youtube.com', 'reddit.com', 'tiktok.com',
        'vk.com', 'ok.ru', 't.me', 'pinterest.com',
    }

    # Извлекаем 2-3 ключевых слова
    _kw_words = [w for w in keywords.split() if len(w) > 2][:3]
    core_kw = ' '.join(_kw_words)
    # Для EN-запросов (работают лучше)
    _goal_words = [w for w in goal_kw.split() if len(w) > 2][:2]
    goal_short = ' '.join(_goal_words)

    import re as _re_al
    all_emails_raw = set()  # email найденные напрямую — инициализируем ДО всех пассов

    # Определяем, техническая ли аудитория (GitHub полезен ТОЛЬКО если сам ЧЕЛОВЕК — тех. спец.)
    # ВАЖНО: проверяем ТОЛЬКО target_audience, а НЕ goal/offer.
    # Продукт может быть AI-платформой, но ПОКУПАТЕЛИ могут быть предпринимателями из любых отраслей.
    # Меченые слова должны описывать ПРОФЕССИЮ/РОЛЬ целевого человека, а не характеристики продукта.
    _audience_text = target_audience.lower()
    _tech_markers = [
        # Языки программирования и фреймворки — явные индикаторы разработчика
        'python', 'javascript', 'typescript', 'react', 'node', 'django',
        'fastapi', 'flask', 'blockchain', 'web3', 'devops',
        'rust', 'golang', 'java', 'php', 'ruby', 'swift',
        'flutter', 'vue', 'angular', 'nextjs',
        # Роли разработчиков
        'developer', 'разработ', 'программист', 'engineer', 'инженер',
        'backend', 'frontend', 'fullstack', 'open source', 'github',
        'code', 'coding', 'software',
        # QA / тестировщики
        'тестировщ', 'tester', 'testing', 'qa ', 'quality assurance',
        'selenium', 'cypress', 'appium', 'pytest',
        # Аналитики / продуктовые роли (IT-контекст)
        'продуктолог', 'product manager',
        # IT-роли
        'it-', 'it специал', 'ит специал',
        # Инди/соло разработчики
        'инди разраб', 'indie dev', 'соло.разраб', 'соло разраб',
        'npm', 'pypi', 'open-source', 'contributor', 'maintainer',
        # AI/ML инженеры (роль, не интерес к продукту)
        'ml engineer', 'ai engineer', 'data scientist', 'data science',
        'machine learning', 'langchain', 'llm developer', 'llm engineer',
        # SaaS-строители / tech-founders (явный код/продуктовый контекст)
        'saas founder', 'tech founder', 'технический директор', 'tech lead',
    ]
    _is_tech_audience = any(t in _audience_text for t in _tech_markers)

    # Если пользователь явно настроил GITHUB_TOKEN — он хочет GitHub-поиск,
    # расширяем маркеры чтобы покрыть AI/ML/tech аудитории (гибкость per-user).
    if github_token and not _is_tech_audience:
        _broad_tech_markers = [
            'ai-', 'ai ', 'ml-', 'ml ', 'artificial intelligence', 'искусственн',
            'нейросет', 'deep learning', 'llm', 'gpt', 'технологич',
            'автоматизац', 'automation', 'no-code', 'low-code',
        ]
        if any(t in _audience_text for t in _broad_tech_markers):
            _is_tech_audience = True
            logger.info('[AUTO_LEADS] Broad tech audience match (user has GITHUB_TOKEN)')

    # Предпринимательский контекст без явно технической роли → НЕ ищем на GitHub
    _business_markers = [
        'предпринимател', 'бизнесмен', 'владелец бизнеса', 'собственник бизнеса',
        'entrepreneur', 'business owner', 'малый бизнес', 'средний бизнес',
        'розница', 'ритейл', 'ресторан', 'кафе', 'услуги', 'торговл',
        'директор', 'генеральный директор', 'руководитель компании',
    ]
    _is_business_audience = any(b in _audience_text for b in _business_markers)
    if _is_business_audience and not _is_tech_audience:
        _is_tech_audience = False

    # ══════════════════════════════════════════════════════════════════════
    # PASS -1: CRM CONTACTS — используем уже известные контакты из email_contacts
    # Быстрый путь: email_contacts(status='new') → email_outreach(status='draft')
    # Создаёт черновики ДО внешнего поиска, экономит API-вызовы
    # ══════════════════════════════════════════════════════════════════════
    _crm_added = 0
    try:
        _crm_contacts = session.query(EmailContact).filter(
            EmailContact.user_id == user.id,
            EmailContact.status == 'new',
        ).all()
        _crm_candidates = []
        for _ec in _crm_contacts:
            _ec_email = (_ec.email or '').strip().lower()
            if not _ec_email or '@' not in _ec_email:
                continue
            # Пропускаем если уже есть в outreach этой кампании
            _already = session.query(EmailOutreach).filter_by(
                campaign_id=campaign.id,
                recipient_email=_ec_email,
            ).first()
            if _already:
                continue
            _crm_candidates.append({
                'email': _ec_email,
                'name': _ec.name or '',
                'company': _ec.company or '',
                'context': _ec.notes or f'CRM contact ({_ec.source or "manual"})',
                'relevance': 8,
            })
        if _crm_candidates:
            _crm_leads_json = json.dumps(_crm_candidates[:50], ensure_ascii=False)
            _crm_result = await add_email_leads(
                campaign_id=campaign.id,
                leads=_crm_leads_json,
                user_id=user.telegram_id,
                session=session,
                close_session=False,
            )
            _crm_m = _re_al.search(r'(\d+)\s*email', _crm_result or '')
            _crm_added = int(_crm_m.group(1)) if _crm_m else 0
            # Обновляем статус контактов на 'contacted'
            if _crm_added > 0:
                _added_emails = {c['email'] for c in _crm_candidates[:50]}
                for _ec in _crm_contacts:
                    if (_ec.email or '').strip().lower() in _added_emails:
                        _ec.status = 'contacted'
                        _ec.last_contacted_at = datetime.now(timezone.utc)
                session.commit()
            logger.info(f"[AUTO_LEADS] PASS -1 CRM contacts: {len(_crm_contacts)} total, "
                        f"{len(_crm_candidates)} candidates → {_crm_added} added to campaign #{campaign.id}")
        else:
            logger.info(f"[AUTO_LEADS] PASS -1 CRM contacts: {len(_crm_contacts)} total, 0 new candidates for campaign #{campaign.id}")
    except Exception as _crm_err:
        logger.warning(f"[AUTO_LEADS] PASS -1 CRM contacts error: {_crm_err}")
        try:
            session.rollback()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # PASS 0: GitHub API — ТОЛЬКО для технической аудитории
    # (маркетологи, дизайнеры, бизнес и т.д. — GitHub бесполезен)
    # ══════════════════════════════════════════════════════════════════════
    github_leads = []
    if _is_tech_audience:
        try:
            gh_queries = []
            _found_techs = [t for t in _tech_markers if t in _audience_text and len(t) > 2]
            
            # Перевод русских терминов в английские для GitHub
            _ru_to_en = {
                'тестировщ': 'QA engineer',
                'разработ': 'developer',
                'программист': 'programmer',
                'инженер': 'engineer',
                'аналитик': 'analyst',
                'продуктолог': 'product manager',
                'автоматизац': 'automation',
                'it специал': 'IT specialist',
                'ит специал': 'IT specialist',
                'технолог': 'technology',
            }
            _en_techs = []
            for t in _found_techs:
                for ru, en in _ru_to_en.items():
                    if ru in t:
                        _en_techs.append(en)
                        break
                else:
                    _en_techs.append(t)

            # Используем англ. термины для GitHub-запросов
            _gh_techs = _en_techs if _en_techs else _found_techs
            if _gh_techs:
                # Для location-запросов предпочитаем специфичный термин (не 'ai', 'bot', 'api' — слишком общие)
                _generic_gh = {'ai', 'api', 'bot', 'app ', 'saas', 'developer', 'programmer',
                               'engineer', 'bi ', 'crm', 'erp', 'llm', 'gpt', 'software'}
                _specific_techs = [t for t in _gh_techs if t.lower().strip() not in _generic_gh]
                _gh_main_base = (_specific_techs[0] if _specific_techs else _gh_techs[0])

                # Основные запросы: каждый уникальный тех в отдельном запросе
                _seen_qterms: set = set()
                for tech in _gh_techs[:4]:
                    if tech not in _seen_qterms:
                        _seen_qterms.add(tech)
                        gh_queries.append(tech)
                # Комбинированные запросы: специфичный тех + 'developer'
                for tech in (_specific_techs or _gh_techs)[:2]:
                    combo = f"{tech} developer"
                    if combo not in _seen_qterms:
                        _seen_qterms.add(combo)
                        gh_queries.append(combo)
            elif core_kw:
                _gh_main_base = core_kw
                gh_queries.append(core_kw)
            else:
                _gh_main_base = core_kw

            if _has_cyrillic:
                # Для русской аудитории — используем СПЕЦИФИЧНЫЙ тех для location-запросов
                _gh_main = _gh_main_base
                gh_queries.insert(0, f"{_gh_main} location:Russia")
                gh_queries.append(f"{_gh_main} location:Moscow")

            gh_queries = list(dict.fromkeys(gh_queries))[:6]  # дедупликация, лимит 6
            if gh_queries:
                # Циклируем страницы 1-12 с элементом случайности для разнообразия
                # Дедуп в add_email_leads не даст повторно добавить уже отправленных.
                import random as _rnd_gh
                _gh_page_base = max(1, ((campaign.emails_sent or 0) // 10) % 12 + 1)
                _gh_page_alt = _rnd_gh.randint(1, 12)
                _gh_pages = list(dict.fromkeys([_gh_page_base, _gh_page_alt]))  # dedup
                logger.info(f"[AUTO_LEADS] Tech audience → GitHub search pages={_gh_pages}: {gh_queries}")
                for _gh_page in _gh_pages:
                    _page_leads = await api.github_multi_search(
                        queries=gh_queries,
                        max_users_per_query=20,
                        page=_gh_page,
                        github_token=github_token or None,
                    )
                    github_leads.extend(_page_leads)
                for lead in github_leads:
                    em = lead.get('email', '').lower().strip('.')
                    if em and not _is_generic_email(em):
                        all_emails_raw.add(em)
                logger.info(f"[AUTO_LEADS] GitHub found {len(github_leads)} users total from {len(_gh_pages)} pages")
                if not github_leads and not github_token:
                    logger.warning("[AUTO_LEADS] GitHub returned 0 leads without GITHUB_TOKEN — likely rate limited (60 req/hr)")
        except Exception as _gh_err:
            logger.warning(f"[AUTO_LEADS] GitHub search failed: {_gh_err}")
    else:
        logger.info(f"[AUTO_LEADS] Non-tech audience → skipping GitHub, using web search only")

    # ══════════════════════════════════════════════════════════════════════
    # PASS 0b: hh.ru API — контакты HR/найма (только для русскоязычной аудитории)
    # ВАЖНО: contacts.email на hh.ru — это HR/нанимающий менеджер компании, а НЕ сам специалист.
    # Это работает для B2B-кампаний (выйти на компании нужной ниши через их HR/CTO).
    # Для B2C (найти индивидуальных профессионалов) — AI определит, нужен ли hh пасс.
    # ══════════════════════════════════════════════════════════════════════
    hh_leads = []
    if _has_cyrillic:
        try:
            import aiohttp as _aiohttp_hh
            import asyncio as _asyncio_hh
            import json as _json_hh

            # AI определяет: полезен ли hh.ru для этого сценария
            # hh.ru возвращает HR/нанимающие контакты КОМПАНИЙ — только для корпоративных целей
            _hh_decide_prompt = (
                f"Goal: {goal[:200]}\n"
                f"Target audience: {target_audience[:200]}\n"
                f"Context: {offer[:100]}\n\n"
                f"Task: decide if hh.ru job vacancy API is useful for reaching this audience.\n"
                f"hh.ru API returns: HR managers and hiring contacts of COMPANIES — NOT individual people.\n"
                f"Use it ONLY when the target is: company HR, hiring managers, recruiters, corporate decision-makers,\n"
                f"or any scenario where 'the company' itself is the contact target.\n\n"
                f"Set use_hh=false for: individual people (any profession), hobbyists, students, creatives,\n"
                f"consumers, personal goals, non-business scenarios, or any case where a person (not a company) is the target.\n\n"
                f"Return JSON: {{\"use_hh\": true/false, \"queries\": [\"query1\", \"query2\"], \"reason\": \"...\"}}\n"
                f"queries: 1-2 hh.ru vacancy search queries relevant to the niche (if use_hh=true).\n"
                f"ONLY valid JSON, no markdown."
            )
            _hh_ai_raw = await api.deepseek_analyze(
                prompt=_hh_decide_prompt,
                system_prompt="Return ONLY valid JSON. No explanation.",
                max_tokens=150,
            )
            _hh_use = False
            _hh_queries = []
            if _hh_ai_raw:
                try:
                    _hh_txt = _hh_ai_raw.strip()
                    if '```' in _hh_txt:
                        for _seg in _hh_txt.split('```'):
                            _seg = _seg.strip()
                            if _seg.startswith('json'): _seg = _seg[4:].strip()
                            if _seg.startswith('{'): _hh_txt = _seg; break
                    _hh_parsed = _json_hh.loads(_hh_txt)
                    _hh_use = bool(_hh_parsed.get('use_hh', False))
                    _hh_queries = [str(q) for q in (_hh_parsed.get('queries') or []) if q][:2]
                    logger.info(f"[AUTO_LEADS] hh.ru decision: use={_hh_use}, reason={_hh_parsed.get('reason','')[:100]}")
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            if not _hh_use:
                logger.info(f"[AUTO_LEADS] hh.ru Pass 0b: skipped (AI decided not relevant for this campaign type)")

            if _hh_use and _hh_queries:
                _hh_headers = {
                    'User-Agent': 'ASI-Biont/1.0 (outreach@asibiont.com)',
                    'Accept': 'application/json',
                }

            async def _hh_get_vacancy_email(session_hh, vacancy_id: str) -> dict | None:
                """Получить contacts.email из конкретной вакансии hh.ru."""
                try:
                    async with session_hh.get(
                        f'https://api.hh.ru/vacancies/{vacancy_id}',
                        headers=_hh_headers,
                        timeout=_aiohttp_hh.ClientTimeout(total=8),
                        ssl=False,
                    ) as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.json(content_type=None)
                        contacts = data.get('contacts') or {}
                        email = (contacts.get('email') or '').strip().lower()
                        if not email or _is_generic_email(email):
                            return None
                        name = contacts.get('name') or ''
                        employer = (data.get('employer') or {}).get('name') or ''
                        area = (data.get('area') or {}).get('name') or ''
                        snippet = (data.get('description') or '')[:300]
                        return {
                            'email': email,
                            'name': name,
                            'company': employer,
                            'context': (
                                f"hh.ru hiring contact: {name or 'HR'} at {employer}"
                                f"{', ' + area if area else ''}. "
                                f"Vacancy snippet: {_re_al.sub(r'<[^>]+>', ' ', snippet)[:200]}"
                            ),
                        }
                except Exception:
                    return None

            async with _aiohttp_hh.ClientSession() as _hh_sess:
                # Собираем ID вакансий по всем запросам
                _vacancy_ids = []
                for _hh_q in _hh_queries:
                    try:
                        async with _hh_sess.get(
                            'https://api.hh.ru/vacancies',
                            params={'text': _hh_q, 'area': 113, 'per_page': 20, 'page': 0},
                            headers=_hh_headers,
                            timeout=_aiohttp_hh.ClientTimeout(total=10),
                            ssl=False,
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                for item in (data.get('items') or []):
                                    vid = str(item.get('id', ''))
                                    if vid and vid not in _vacancy_ids:
                                        _vacancy_ids.append(vid)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                # Параллельно запрашиваем детали (макс 30 вакансий)
                _vacancy_ids = _vacancy_ids[:30]
                if _vacancy_ids:
                    _tasks_hh = [_hh_get_vacancy_email(_hh_sess, vid) for vid in _vacancy_ids]
                    # Пауза между батчами чтобы не перегружать API hh.ru
                    _batch_results = []
                    for i in range(0, len(_tasks_hh), 10):
                        batch = await _asyncio_hh.gather(*_tasks_hh[i:i+10], return_exceptions=True)
                        _batch_results.extend(batch)
                        if i + 10 < len(_tasks_hh):
                            await _asyncio_hh.sleep(1)

                    for res in _batch_results:
                        if isinstance(res, dict) and res.get('email'):
                            hh_leads.append(res)
                            all_emails_raw.add(res['email'])

            logger.info(f"[AUTO_LEADS] hh.ru Pass 0b: {len(_vacancy_ids)} vacancies → {len(hh_leads)} contacts with email")
        except Exception as _hh_err:
            logger.warning(f"[AUTO_LEADS] hh.ru pass failed: {_hh_err}")

    # ══════════════════════════════════════════════════════════════════════
    # PASS 1: ПРЯМОЙ ПАРСИНГ ПЛАТФОРМ (основной источник email)
    # DDG ненадёжен для email (rate-limit, блокировки) — парсим платформы напрямую
    # ══════════════════════════════════════════════════════════════════════
    import asyncio as _asyncio_al

    _kw_enc = core_kw.replace(' ', '%20')
    _core_en = core_kw.replace(' ', '+')
    _platform_urls = []

    # ──────────────────────────────────────────────────────────────────────
    # PASS 1 платформы: AI думает сам — какие площадки подходят этой аудитории
    # ──────────────────────────────────────────────────────────────────────

    # AI анализирует аудиторию и выбирает платформы сам
    _ai_platforms = await _get_ai_niche_platforms(
        target_audience, goal, offer, _kw_enc, _core_en, _has_cyrillic, api
    )
    _platform_urls.extend(_ai_platforms)

    # Страховочный минимум если AI вернул 0 URL
    if not _ai_platforms:
        if _has_cyrillic:
            _platform_urls.extend([
                f'https://career.habr.com/resumes?q={_kw_enc}',
                f'https://www.fl.ru/users/?skills={_kw_enc}',
                f'https://profi.ru/search/?q={_kw_enc}',
                f'https://vc.ru/search?q={_kw_enc}',
            ])
        else:
            _platform_urls.extend([
                f'https://www.upwork.com/search/profiles/?q={_core_en}',
                f'https://about.me/search?q={_core_en}',
                f'https://medium.com/search?q={_core_en}',
            ])
    _niche_contact_urls = []

    async def _fetch_platform(url: str) -> tuple:
        """Скачать страницу платформы, вернуть (url, html)."""
        try:
            s = await api._get_session()
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10),
                             headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
                             allow_redirects=True, ssl=False) as resp:
                if resp.status == 200 and 'text' in (resp.content_type or ''):
                    html = await resp.text(errors='replace')
                    return (url, html[:30000])
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        return (url, "")

    # Параллельная загрузка всех платформ
    _all_platform_urls = _platform_urls + _niche_contact_urls
    _pages = await _asyncio_al.gather(
        *[_fetch_platform(u) for u in _all_platform_urls[:15]],
        return_exceptions=True,
    )

    all_results = []  # для совместимости с Pass 2 (url scoring)
    _direct_emails = 0
    page_texts = []  # для AI-фильтрации

    for _page_result in _pages:
        if isinstance(_page_result, Exception) or not isinstance(_page_result, tuple):
            continue
        _p_url, _p_html = _page_result
        if not _p_html:
            continue

        # Извлекаем email напрямую из HTML
        _found = _extract_emails_from_text(_p_html)
        all_emails_raw.update(_found)
        for em in _re_al.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6})', _p_html):
            em = em.lower().strip('.')
            if not _is_generic_email(em):
                all_emails_raw.add(em)
        _direct_emails += len(_found)

        # Извлекаем ссылки на профили/страницы контактов для Pass 2
        _profile_links = _re_al.findall(r'href="(https?://[^"]{10,200})"', _p_html)
        for _pl in _profile_links[:30]:
            _pl_lower = _pl.lower()
            if any(h in _pl_lower for h in ('/user/', '/author/', '/profile/', '/@', '/people/', '/u/')):
                all_results.append({'title': '', 'snippet': '', 'url': _pl})

        # Чистый текст для AI-анализа
        _clean = _re_al.sub(r'<[^>]+>', ' ', _p_html)
        _clean = _re_al.sub(r'\s+', ' ', _clean)[:2000]
        page_texts.append(_clean)

    logger.info(f"[AUTO_LEADS] Direct platform scrape: {len(_all_platform_urls)} URLs → "
                f"{_direct_emails} emails extracted, {len(all_results)} profile links, "
                f"GitHub leads: {len(github_leads)}, total raw: {len(all_emails_raw)}")

    # ══════════════════════════════════════════════════════════════════════
    # PASS 1b: DDG поиск с AI-генерированными запросами
    # Главный путь для ЛЮБОЙ аудитории — DDG находит реальные личные страницы
    # ══════════════════════════════════════════════════════════════════════
    import json as _json_q
    import hashlib as _hl_ddg
    import time as _time_ddg
    _ddg_hits = 0
    try:
        # Кэш DDG-запросов — та же аудитория = те же запросы, API вызов не нужен
        _cache_key = _hl_ddg.md5(target_audience[:100].encode('utf-8', errors='ignore')).hexdigest()
        _cached = _DDG_QUERY_CACHE.get(_cache_key)
        _now_ts = _time_ddg.time()
        if _cached and (_now_ts - _cached[1]) < _DDG_QUERY_CACHE_TTL:
            _ddg_queries = _cached[0]
            logger.info(f"[AUTO_LEADS] PASS 1b DDG queries from cache ({len(_ddg_queries)}): {_ddg_queries}")
        else:
            _q_lang = 'Russian' if _has_cyrillic else 'English'
            _queries_prompt = (
                f"Generate 6 web search queries to find CONTACT EMAIL ADDRESSES of people matching:\n"
                f"Target audience: {target_audience[:200]}\n"
                f"Goal: {goal[:150]}\n"
                f"Language preference: {_q_lang}\n\n"
                f"IMPORTANT: The goal can be anything — commercial, creative, social, hobby, educational.\n"
                f"Do NOT assume it's a sales scenario. Read the audience and goal carefully.\n"
                f"Focus on who these PEOPLE ARE (their identity, interests, role) — not what the product is.\n\n"
                f"Rules:\n"
                f"- Each query must target pages where these specific people publicly share their email\n"
                f"  (personal sites, portfolios, community profiles, contact pages, forum profiles, etc.)\n"
                f"- Think: where do people of this type VOLUNTARILY publish contact info?\n"
                f"  Professionals → freelance platforms, specialist directories\n"
                f"  Creatives/artists → portfolio sites, communities, about.me\n"
                f"  Hobbyists → niche forums, club sites, meetup pages\n"
                f"  Business people → catalogs, review sites, business directories\n"
                f"  Enthusiasts/fans → fan communities, event pages\n"
                f"- Mix direct audience-type searches with platform-specific ones\n"
                f"- If Russian-speaking audience, use both Russian and English queries\n"
                f"Return ONLY valid JSON array of 6 query strings: [\"q1\", \"q2\", ...]"
            )
            _ai_q_raw = await api.deepseek_analyze(
                prompt=_queries_prompt,
                system_prompt="Return ONLY a valid JSON array of strings, no markdown or explanation.",
                max_tokens=300,
            )
            _ddg_queries = []
            if _ai_q_raw:
                _qt = _ai_q_raw.strip()
                if '```' in _qt:
                    for _seg in _qt.split('```'):
                        _seg = _seg.strip()
                        if _seg.startswith('json'):
                            _seg = _seg[4:].strip()
                        if _seg.startswith('['):
                            _qt = _seg
                            break
                try:
                    _pq = _json_q.loads(_qt)
                    if isinstance(_pq, list):
                        _ddg_queries = [str(q).strip() for q in _pq if q][:6]
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            # Fallback-запросы если AI не вернул список
            if not _ddg_queries:
                _ddg_queries = [f"{core_kw} email contact", f"{core_kw} личный сайт"]
                if _has_cyrillic:
                    _ddg_queries.append(f"{core_kw} написать мне")
            # Сохраняем в кэш
            if _ddg_queries:
                _DDG_QUERY_CACHE[_cache_key] = (_ddg_queries, _now_ts)
            logger.info(f"[AUTO_LEADS] PASS 1b DDG queries ({len(_ddg_queries)}): {_ddg_queries}")
        _ddg_raw = await api.web_multi_search(_ddg_queries, num_per_query=8)
        _ddg_hits = len(_ddg_raw)

        for _r in _ddg_raw:
            # Сразу извлекаем email из сниппетов DDG
            _snip = (_r.get('snippet') or '') + ' ' + (_r.get('title') or '')
            all_emails_raw.update(_extract_emails_from_text(_snip))
            # URL → PASS 2 (скачать страницу и поискать email там)
            _r_url = _r.get('link', '')
            if _r_url:
                try:
                    _r_domain = _r_url.split('/')[2]
                    _r_base = '.'.join(_r_domain.split('.')[-2:])
                    if _r_base not in _unfetchable_domains:
                        all_results.append({
                            'title': _r.get('title', ''),
                            'snippet': _r.get('snippet', ''),
                            'url': _r_url,
                        })
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

        logger.info(f"[AUTO_LEADS] PASS 1b DDG: {_ddg_hits} results → "
                    f"{len(all_results)} URLs in pool, {len(all_emails_raw)} emails total")
    except Exception as _ddg_err:
        logger.warning(f"[AUTO_LEADS] PASS 1b DDG failed: {_ddg_err}")

    # Если после ВСЕХ пассов (платформы + GitHub + DDG) ничего — выходим
    if not all_results and not github_leads and not all_emails_raw:
        logger.warning(f"[AUTO_LEADS] ZERO results after all passes for campaign #{campaign.id}")
        return 0, ""

    # ══════════════════════════════════════════════════════════════════════
    # PASS 2: Скачиваем профильные страницы + contact/about sub-pages → email
    # ══════════════════════════════════════════════════════════════════════
    _contact_hints = {'contact', 'about', 'profile', 'author', 'user',
                      'контакт', 'автор', 'профиль', 'обо мне',
                      '@', 'email', 'mailto', 'написать', 'связаться'}
    scored_urls = []
    seen_domains = set()
    contact_sub_urls = []  # URL контактных страниц для дополнительного сканирования

    for r in all_results:
        url = r['url']
        if not url:
            continue
        try:
            domain = url.split('/')[2]
        except IndexError:
            continue
        
        # Пропускаем домены которые блокируют ботов
        base_domain = '.'.join(domain.split('.')[-2:])
        if base_domain in _unfetchable_domains or domain in _unfetchable_domains:
            # Но извлекаем email из сниппета
            all_emails_raw.update(_extract_emails_from_text(r['snippet']))
            continue
        
        if domain not in seen_domains:
            seen_domains.add(domain)
            # Добавляем контактные sub-pages для каждого нового домена
            scheme = 'https' if url.startswith('https') else 'http'
            for sub in ['/contact', '/contacts', '/about', '/about-us', '/team', '/kontakty']:
                contact_sub_urls.append(f"{scheme}://{domain}{sub}")

        # Скоринг: сниппет/заголовок содержат email-подсказки?
        text_lower = f"{r['title']} {r['snippet']}".lower()
        score = sum(1 for h in _contact_hints if h in text_lower)
        if '@' in r['snippet'] and '.' in r['snippet'].split('@')[-1][:10]:
            score += 5
        scored_urls.append((score, url, r['snippet']))

    scored_urls.sort(reverse=True)
    top_urls = scored_urls[:20]  # Увеличили до 20 страниц
    logger.info(f"[AUTO_LEADS] Unique domains: {len(seen_domains)}, "
                f"top URLs: {len(top_urls)}, contact sub-pages: {len(contact_sub_urls)}")

    page_texts = []

    # Извлекаем email из сниппетов сразу
    for _, _, snippet in scored_urls:
        all_emails_raw.update(_extract_emails_from_text(snippet))

    # Скачиваем страницы параллельно
    async def _fetch_page(url: str) -> str:
        """Скачать текст страницы (первые 15KB)."""
        try:
            s = await api._get_session()
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8),
                             headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}, 
                             allow_redirects=True, ssl=False) as resp:
                if resp.status == 200 and 'text' in resp.content_type:
                    raw = await resp.text(errors='replace')
                    return raw[:15000]
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        return ""

    import asyncio as _asyncio_al

    # Fetch основных страниц
    pages = await _asyncio_al.gather(*[_fetch_page(u) for _, u, _ in top_urls],
                                      return_exceptions=True)

    for page_html in pages:
        if isinstance(page_html, str) and page_html:
            all_emails_raw.update(_extract_emails_from_text(page_html))
            # Также ищем mailto: ссылки (часто скрыты от глаз но есть в HTML)
            for em in _re_al.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6})', page_html):
                em = em.lower().strip('.')
                if not _is_generic_email(em):
                    all_emails_raw.add(em)
            clean_text = _re_al.sub(r'<[^>]+>', ' ', page_html)
            clean_text = _re_al.sub(r'\s+', ' ', clean_text)[:2000]
            page_texts.append(clean_text)

    pages_fetched = sum(1 for p in pages if isinstance(p, str) and p)

    # Fetch контактных sub-pages (если основные не дали достаточно email)
    contact_pages_fetched = 0
    if len(all_emails_raw) < 30 and contact_sub_urls:
        # Берём до 30 контактных страниц
        _sub_to_fetch = contact_sub_urls[:30]
        sub_pages = await _asyncio_al.gather(*[_fetch_page(u) for u in _sub_to_fetch],
                                              return_exceptions=True)
        for page_html in sub_pages:
            if isinstance(page_html, str) and page_html:
                contact_pages_fetched += 1
                all_emails_raw.update(_extract_emails_from_text(page_html))
                for em in _re_al.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,6})', page_html):
                    em = em.lower().strip('.')
                    if not _is_generic_email(em):
                        all_emails_raw.add(em)

    logger.info(f"[AUTO_LEADS] Pages fetched: {pages_fetched} main + {contact_pages_fetched} contact, "
                f"emails from regex: {len(all_emails_raw)}, "
                f"GitHub leads: {len(github_leads)}, hh.ru leads: {len(hh_leads)}")

    # ══════════════════════════════════════════════════════════════════════
    # PASS 3: AI-фильтрация по релевантности
    # ══════════════════════════════════════════════════════════════════════
    combined_text = "\n---\n".join(page_texts[:6])
    snippets_text = "\n".join(f"- {r['title']}: {r['snippet']}" for r in all_results[:15])

    # Добавляем GitHub и hh.ru leads к all_emails_raw + строим контекст-карту
    github_context_map = {}  # email → context info from GitHub / hh.ru
    for gl in github_leads:
        em = gl.get('email', '').lower().strip('.')
        if em and not _is_generic_email(em):
            all_emails_raw.add(em)
            github_context_map[em] = (
                f"GitHub user: {gl.get('name', '')} (@{gl.get('url', '').split('/')[-1] if gl.get('url') else '?'}), "
                f"bio: {gl.get('bio', '')[:100]}, company: {gl.get('company', '')}, "
                f"repos: {gl.get('repos', 0)}, followers: {gl.get('followers', 0)}"
            )
    for hl in hh_leads:
        em = hl.get('email', '').lower().strip('.')
        if em and not _is_generic_email(em):
            all_emails_raw.add(em)
            github_context_map[em] = hl.get('context', f"hh.ru contact: {hl.get('name','')} at {hl.get('company','')}")

    # Если уже нашли email через regex/GitHub/hh.ru — просим AI отфильтровать по РЕЛЕВАНТНОСТИ
    if all_emails_raw:
        emails_list = ", ".join(list(all_emails_raw)[:40])  # Увеличили лимит до 40

        # Формируем контекст из GitHub и hh.ru
        github_context = ""
        if github_context_map:
            gh_lines = [f"  {em}: {ctx}" for em, ctx in list(github_context_map.items())[:20]]
            github_context = "\n\nProfile/contact data (GitHub + hh.ru):\n" + "\n".join(gh_lines)
        
        # Инструкция по языку для AI-фильтра
        _lang_filter_hint = ""
        if _has_cyrillic:
            _lang_filter_hint = "\n7. LANGUAGE PRIORITY: The target audience is RUSSIAN-SPEAKING. Strongly prefer people with Russian names, from .ru/.by/.ua/.kz domains, or with Russian context. Foreign recipients are acceptable ONLY if they clearly match the target audience AND work in the Russian market."

        extract_prompt = f"""I found these email addresses from web search, GitHub profiles and hh.ru vacancies.
Your job is to FILTER them — keep ONLY emails of people who GENUINELY match the target audience.

Found emails: {emails_list}

Target audience: {target_audience[:300]}
Campaign goal: {goal[:300]}
Product: {offer[:200]}
{github_context}

Context (search results + page content):
{snippets_text}

{combined_text[:3000]}

STRICT RULES:
1. For each email, determine: Does this person ACTUALLY match the target audience?
2. Rate relevance 1-10. Only include emails rated 7+.
3. If you cannot determine the person's role/interests from context, EXCLUDE them (relevance=0).
4. SKIP: info@, contact@, support@, sales@, admin@, noreply@, ai@, ml@, data@, research@, dev@, and any corporate/generic emails.
5. SKIP: emails from unrelated people (random commenters, unrelated authors, etc.)
6. Better to return 3 RELEVANT leads than 15 irrelevant ones.
7. ⚠️ EMAIL-PERSON VERIFICATION: Do NOT guess email-to-person associations. Only include an email if there is CLEAR evidence on the page that THIS email belongs to THIS specific person (e.g. email listed next to the person's name/profile). If a page lists multiple emails and multiple names, verify each association independently. Never assign a random email from the page to a person just because both appear on the same page.
8. Prefer emails with personal prefixes (ivan@, john.doe@) over generic (rating@, user123@) — generic-looking prefixes need stronger evidence of association.{_lang_filter_hint}

Return JSON array: [{{"email":"...","name":"...","company":"...","relevance":8,"context":"DETAILED context: what this person/company does, their specific projects/products/articles, why they match the target audience. This context will be used to write a personalized email, so include SPECIFIC details (product names, technologies, achievements, article topics). NOT just 'works in AI' — write 'built an open-source RAG framework with 2k GitHub stars'"}}]
If NO emails are relevant, return empty array: []"""
    else:
        extract_prompt = f"""Find personal email addresses of people matching this SPECIFIC target audience.

Target audience: {target_audience[:300]}
Campaign goal: {goal[:300]}
Product: {offer[:200]}

Search results:
{snippets_text}

Page content:
{combined_text[:3000]}

STRICT RULES:
1. ONLY include emails of people who GENUINELY match the target audience.
2. Rate relevance 1-10. Only include emails rated 7+.
3. SKIP: info@, contact@, support@, sales@, admin@, noreply@, ai@, ml@, data@, research@, dev@ — only PERSONAL emails.
4. If you can't determine why a person matches the target audience, DON'T include them.
5. Better to return 0 leads than add irrelevant people.
6. ⚠️ EMAIL-PERSON VERIFICATION: Only include an email if there is CLEAR evidence on the page that THIS email belongs to THIS specific person. Never guess email-to-person associations.

Return JSON array: [{{"email":"...","name":"...","company":"...","relevance":8,"context":"DETAILED context: what this person/company does, their specific projects/products/articles, why they match the target audience. Include SPECIFIC details for email personalization (product names, technologies, achievements). NOT 'works in AI' — write 'built an open-source RAG framework with 2k stars'"}}]
If no relevant emails found return []"""

    try:
        ai_result = await api.deepseek_analyze(
            prompt=extract_prompt,
            system_prompt="Extract email addresses from text. Return ONLY valid JSON array, no markdown.",
            max_tokens=1000
        )
        logger.info(f"[AUTO_LEADS] AI result length: {len(ai_result or '')}, preview: {(ai_result or '')[:200]}")
    except Exception as _ai_err:
        logger.warning(f"[AUTO_LEADS] AI extraction failed: {_ai_err}")
        ai_result = None

    parsed_leads = []

    if ai_result:
        import json as _json_al
        text = ai_result.strip()
        if '```' in text:
            parts = text.split('```')
            for p in parts:
                p = p.strip()
                if p.startswith('json'):
                    p = p[4:].strip()
                if p.startswith('['):
                    text = p
                    break
        try:
            raw = _json_al.loads(text)
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and item.get('email'):
                        em = item['email'].lower().strip('.')
                        if _is_generic_email(em):
                            continue
                        # Фильтр по relevance score — порог 4 пропускает кандидатов с uncertain context
                        # (AI часто ставит 4-5 когда контекст неполный, но email встречен на релевантной площадке)
                        relevance = item.get('relevance', 0)
                        try:
                            relevance = int(relevance)
                        except (ValueError, TypeError):
                            relevance = 0
                        if relevance < 4:
                            logger.info(f"[AUTO_LEADS] Skipping low-relevance lead: "
                                        f"{em} (score={relevance})")
                            continue
                        parsed_leads.append(item)
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

    # Fallback: если AI не отфильтровал, но есть GitHub/hh.ru leads с контекстом
    if not parsed_leads and (github_leads or hh_leads):
        _fb_leads = list(github_leads) + list(hh_leads)
        logger.info(f"[AUTO_LEADS] AI filter returned 0, using {len(_fb_leads)} GitHub/hh.ru leads as fallback")
        for gl in github_leads:
            em = gl.get('email', '').lower().strip('.')
            if em and not _is_generic_email(em):
                parsed_leads.append({
                    'email': em,
                    'name': gl.get('name', gl.get('url', '').split('/')[-1] if gl.get('url') else ''),
                    'company': gl.get('company', ''),
                    'relevance': 7,
                    'context': f"GitHub: {gl.get('bio', '')[:100]}, {gl.get('repos', 0)} repos, {gl.get('followers', 0)} followers",
                })
        for hl in hh_leads:
            em = hl.get('email', '').lower().strip('.')
            if em and not _is_generic_email(em):
                parsed_leads.append({
                    'email': em,
                    'name': hl.get('name', ''),
                    'company': hl.get('company', ''),
                    'relevance': 6,
                    'context': hl.get('context', ''),
                })

    # Если всё ещё 0 — используем regex emails как последний резерв,
    # но ТОЛЬКО после DNS MX-проверки домена + базовой валидации + персональности
    if not parsed_leads and all_emails_raw:
        logger.info(f"[AUTO_LEADS] Regex fallback: validating {len(all_emails_raw)} emails via DNS MX...")
        validated_fallback = []
        for em in list(all_emails_raw)[:20]:
            domain = em.split('@')[1] if '@' in em else ''
            if not domain:
                continue
            # Повторная проверка generic (может быть пропущен для новых prefix'ов)
            if _is_generic_email(em):
                logger.info(f"[AUTO_LEADS] Fallback skip (generic): {em}")
                continue
            # Базовая структура домена: 1-3 точки, TLD 2-6 букв
            parts = domain.split('.')
            tld = parts[-1] if parts else ''
            if len(parts) < 2 or len(parts) > 4 or len(tld) < 2 or len(tld) > 6:
                logger.info(f"[AUTO_LEADS] Fallback skip (bad domain structure): {em}")
                continue
            # Prefix должен выглядеть как личное имя (минимум 4 символа, не чисто цифры)
            prefix = em.split('@')[0]
            if len(prefix) < 4 or prefix.isdigit():
                logger.info(f"[AUTO_LEADS] Fallback skip (non-personal prefix): {em}")
                continue
            # DNS MX проверка
            if not await _check_mx_record(domain):
                logger.info(f"[AUTO_LEADS] Fallback skip (no MX record): {em}")
                continue
            validated_fallback.append(em)
        if validated_fallback:
            logger.info(f"[AUTO_LEADS] Fallback: {len(validated_fallback)} emails passed MX validation")
            for em in validated_fallback:
                # Try to extract name from email prefix (john.doe@ → John Doe)
                _fb_prefix = em.split('@')[0] if '@' in em else ''
                _fb_parts = [p.capitalize() for p in _re_al.split(r'[._\-]', _fb_prefix)
                             if len(p) >= 2 and p.isalpha()]
                _fb_name = ' '.join(_fb_parts) if 1 <= len(_fb_parts) <= 3 else ''
                if not _fb_name:
                    logger.info(f"[AUTO_LEADS] Fallback skip (can't extract name): {em}")
                    continue
                parsed_leads.append({
                    'email': em,
                    'name': _fb_name,
                    'company': '',
                    'relevance': 5,
                    'context': 'Found via web search regex (MX-verified domain)',
                })
        else:
            logger.info(f"[AUTO_LEADS] Fallback: 0 emails passed MX validation")

    if not parsed_leads:
        # Сбрасываем кэш нишевых платформ если не нашли ни одного лида
        # — при следующем вызове AI сгенерирует свежие URL вместо тех же плохих
        import hashlib as _hl_clr
        _lang_clr = 'ru' if _has_cyrillic else 'en'
        _clr_key = _hl_clr.md5(
            f"{_lang_clr}:{target_audience[:150]}".encode('utf-8', errors='ignore')
        ).hexdigest()
        if _clr_key in _NICHE_PLATFORM_CACHE:
            del _NICHE_PLATFORM_CACHE[_clr_key]
            logger.info(f"[AUTO_LEADS] Cleared stale niche platform cache for campaign #{campaign.id}")
        logger.warning(f"[AUTO_LEADS] FINAL: 0 leads found for campaign #{campaign.id} "
                       f"(ddg_results={len(all_results)}, pages={pages_fetched}, "
                       f"contact_pages={contact_pages_fetched}, github={len(github_leads)}, "
                       f"regex_emails={len(all_emails_raw)}, ai_parsed=0)")
        # Подсказки по отсутствующим интеграциям
        import os as _os_leads
        _intg_hints = []
        # GITHUB_TOKEN: сначала проверяем уже найденный в user_api_keys, затем env
        if _is_tech_audience and not github_token and not _os_leads.getenv('GITHUB_TOKEN'):
            _intg_hints.append(
                "💡 Для поиска разработчиков на GitHub — добавь GITHUB_TOKEN в настройки агента "
                "(дашборд → агент → API-ключи → GITHUB_TOKEN=ghp_...). "
                "Увеличит лимит запросов с 60 до 5000 в час."
            )
        # RESEND_API_KEY: проверяем platform env + личный ключ в user_api_keys агентов
        _has_personal_resend_h = False
        try:
            from models import UserAgent as _UA_rh
            _has_personal_resend_h = session.query(_UA_rh).filter(
                _UA_rh.author_id == user.id,
                _UA_rh.user_api_keys.isnot(None),
                _UA_rh.user_api_keys.contains('RESEND_API_KEY='),
            ).first() is not None
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        if not _os_leads.getenv('RESEND_API_KEY') and not _has_personal_resend_h:
            _intg_hints.append(
                "💡 Для отправки писем нужен RESEND_API_KEY "
                "(добавь в настройки агента → API-ключи → RESEND_API_KEY=re_...). "
                "Регистрация бесплатна: resend.com"
            )
        _hint_msg = ("\n⚠️ Интеграции для улучшения поиска: \n" + "\n".join(_intg_hints)) if _intg_hints else ""
        # Если CRM contacts были добавлены в PASS -1, считаем их
        if _crm_added > 0:
            return _crm_added, ""
        return 0, _hint_msg

    logger.info(f"[AUTO_LEADS] Found {len(parsed_leads)} leads for campaign #{campaign.id}: "
                f"{[l.get('email') for l in parsed_leads[:10]]}")

    # Добавляем через add_email_leads (централизованная логика с дедупом)
    leads_json = json.dumps(parsed_leads[:100], ensure_ascii=False)  # Увеличили лимит до 100
    result_msg = await add_email_leads(
        campaign_id=campaign.id,
        leads=leads_json,
        user_id=user.telegram_id,
        session=session,
        close_session=False,
    )

    # Парсим количество добавленных
    m = _re_al.search(r'(\d+)\s*email', result_msg or '')
    count = int(m.group(1)) if m else 0

    return count + _crm_added, ""

async def start_email_campaign(
    name: str,
    goal: str,
    target_audience: str,
    offer: str,
    sender_name: str = None,
    sender_email: str = None,
    tone: str = 'professional',
    max_emails: int = 0,
    daily_limit: int = 100,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Создать email-кампанию для автономного привлечения клиентов.

    AI-агент будет автономно:
    1. Искать email-адреса через web_search
    2. Генерировать персонализированные письма
    3. Отправлять через Resend API
    4. Отвечать на replies в рамках заданной цели

    max_emails: 0 = безлимитно (рекомендуется). Кампания работает пока AI видит отдачу.
        НЕ ставь произвольные числа вроде 100 — автопилот сам решает когда остановиться.
    daily_limit: макс. писем в день (обычно 100).
    """
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Fallback sender info
        if not sender_name:
            sender_name = user.first_name or user.username or 'Team'
        if not sender_email:
            sender_email = 'outreach@asibiont.com'

        # Проверка на дубликат — если есть активная кампания с похожей целью (personal скрытые исключаем)
        from sqlalchemy import func as sa_func
        existing = session.query(EmailCampaign).filter(
            EmailCampaign.user_id == user.id,
            EmailCampaign.status == 'active',
        ).all()
        _stop_camp = {'и', 'в', 'на', 'для', 'по', 'с', 'к', 'или', 'что', 'при', 'a', 'the', 'to', 'for', 'of', 'and', 'in', 'with'}
        for ex in existing:
            # Сравниваем и goal-текст, и name кампании — достаточно 2 значимых общих слов
            ex_goal_words = {w for w in (ex.goal or '').lower().split() if len(w) > 2} - _stop_camp
            ex_name_words = {w for w in (ex.name or '').lower().split() if len(w) > 2} - _stop_camp
            new_goal_words = {w for w in goal.lower().split() if len(w) > 2} - _stop_camp
            new_name_words = {w for w in name.lower().split() if len(w) > 2} - _stop_camp
            goal_overlap = ex_goal_words & new_goal_words
            name_overlap = ex_name_words & new_name_words
            if len(goal_overlap) >= 2 or len(name_overlap) >= 2:
                # Обновляем существующую кампанию вместо создания новой
                if daily_limit > ex.daily_limit:
                    ex.daily_limit = min(daily_limit, 100)
                if max_emails and max_emails > (ex.max_emails or 0):
                    ex.max_emails = max_emails
                session.commit()
                lang = getattr(user, 'language_code', 'ru') or 'ru'
                if lang == 'en':
                    return (
                        f" Campaign #{ex.id} «{ex.name}» already exists and is active (sent {ex.emails_sent}/{ex.max_emails or '∞'}, today limit {ex.daily_limit})! "
                        f"DO NOT call start_email_campaign again. "
                        f"To send emails use send_outreach_email(recipient_email, subject, body) for each contact individually."
                    )
                return (
                    f" Кампания #{ex.id} «{ex.name}» уже существует и активна (отправлено {ex.emails_sent}/{ex.max_emails or '∞'}, дневной лимит {ex.daily_limit})! "
                    f"НЕ вызывай start_email_campaign повторно. "
                    f"Для отправки писем используй send_outreach_email(recipient_email, subject, body) — по одному письму на контакт."
                )

        campaign = EmailCampaign(
            user_id=user.id,
            name=name[:300],
            goal=goal[:2000],
            target_audience=target_audience[:1000],
            offer=offer[:2000],
            tone=tone,
            sender_name=sender_name,
            sender_email=sender_email,
            max_emails=max_emails,
            daily_limit=min(daily_limit, 100),
            status='active',
        )
        session.add(campaign)
        session.commit()

        # Логируем создание кампании в хронологию
        try:
            from models import AgentActivityLog as _AAL
            _camp_log = _AAL(
                user_id=user.id,
                activity_type='content_campaign',
                title=f'Email-кампания: {name[:150]}',
                content=f'Цель: {goal[:200]}' + (f'\nАудитория: {target_audience[:100]}' if target_audience else ''),
                status='active',
                ref_id=campaign.id,
            )
            session.add(_camp_log)
            session.commit()
        except Exception as _le:
            logger.warning(f"[EMAIL_CAMPAIGN] Failed to log activity: {_le}")

        # ═══════════════════════════════════════════════════════
        # АВТОПОИСК ЛИДОВ — только для ПРИВЛЕЧЕНИЯ (сценарий 3)
        # Переговоры (max_emails<=5) — агент сам добавит конкретный email
        # ═══════════════════════════════════════════════════════
        is_outreach_campaign = (max_emails == 0 or max_emails > 10) and daily_limit >= 5
        auto_leads_count = 0
        auto_leads_msg = ""
        if is_outreach_campaign:
            try:
                auto_leads_count, auto_leads_msg = await _auto_find_leads(
                    campaign=campaign, user=user, target_audience=target_audience,
                    goal=goal, offer=offer, session=session
                )
            except Exception as _af_err:
                logger.error(f"[EMAIL_CAMPAIGN] Auto-find leads error: {_af_err}", exc_info=True)
                auto_leads_msg = ""

        lang = _get_lang(user_id)
        if is_outreach_campaign:
            # Сценарий 3 — привлечение
            if lang == 'en':
                base = f" Campaign #{campaign.id} «{name}» created!"
                if auto_leads_count > 0:
                    base += f"\n Found {auto_leads_count} contacts — first emails will be sent automatically."
                else:
                    base += "\n No contacts found automatically. Search for people via the web, then add their emails."
            else:
                base = f" Кампания #{campaign.id} «{name}» создана!"
                if auto_leads_count > 0:
                    base += f"\n Найдено {auto_leads_count} контактов — первые письма будут отправлены автоматически."
                else:
                    base += "\n Автопоиск не нашёл контактов. Найди людей через интернет, затем добавь их email."
            if auto_leads_msg:
                base += f"\n{auto_leads_msg}"
            return base
        else:
            # Сценарий 2 — переговоры (конкретный контакт)
            if lang == 'en':
                return (
                    f" Campaign #{campaign.id} «{name}» created.\n"
                    f"Now add the contact emails and send the first outreach email."
                )
            return (
                f" Кампания #{campaign.id} «{name}» создана.\n"
                f"Теперь добавь контакты и отправь первое письмо."
            )
    except Exception as e:
        logger.error(f"[EMAIL_CAMPAIGN] Error creating campaign: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка создания кампании: {str(e)}"
    finally:
        if close_session:
            session.close()


async def update_email_campaign(
    campaign_id: int = None,
    name: str = None,
    goal: str = None,
    target_audience: str = None,
    offer: str = None,
    tone: str = None,
    max_emails: int = None,
    daily_limit: int = None,
    status: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Обновить параметры существующей email-кампании.

    Позволяет изменить daily_limit, max_emails, name, goal, target_audience,
    offer, tone, status — без создания дубликата.
    """
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        # Найти кампанию
        campaign = None
        if campaign_id:
            campaign = session.query(EmailCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            # Берём последнюю активную кампанию
            campaign = session.query(EmailCampaign).filter_by(
                user_id=user.id, status='active'
            ).order_by(EmailCampaign.created_at.desc()).first()

        if not campaign:
            return " Кампания не найдена. Укажи ID кампании или создай новую."

        changes = []
        if name is not None:
            campaign.name = name[:300]
            changes.append(f"название: {name[:80]}")
        if goal is not None:
            campaign.goal = goal[:2000]
            changes.append("цель обновлена")
        if target_audience is not None:
            campaign.target_audience = target_audience[:1000]
            changes.append("аудитория обновлена")
        if offer is not None:
            campaign.offer = offer[:2000]
            changes.append("оффер обновлён")
        if tone is not None and tone in ('professional', 'friendly', 'formal'):
            campaign.tone = tone
            changes.append(f"тон: {tone}")
        if max_emails is not None:
            campaign.max_emails = max(0, int(max_emails))
            changes.append(f"макс. писем: {max_emails if max_emails > 0 else 'безлимитно'}")
        if daily_limit is not None:
            campaign.daily_limit = min(max(1, int(daily_limit)), 100)
            changes.append(f"лимит/день: {campaign.daily_limit}")
        if status is not None and status in ('active', 'paused', 'completed', 'cancelled'):
            campaign.status = status
            changes.append(f"статус: {status}")

        if not changes:
            return f"ℹ Кампания #{campaign.id} «{campaign.name}» — нечего обновлять. Укажи параметры для изменения."

        session.commit()

        lang = _get_lang(user_id)
        changes_str = ', '.join(changes)
        if lang == 'en':
            return (
                f" Campaign #{campaign.id} «{campaign.name}» updated:\n"
                f"{changes_str}\n\n"
                f" Current: {campaign.daily_limit}/day, "
                f"{'unlimited' if not campaign.max_emails or campaign.max_emails == 0 else f'max {campaign.max_emails}'} total, "
                f"status: {campaign.status}"
            )
        return (
            f" Кампания #{campaign.id} «{campaign.name}» обновлена:\n"
            f"{changes_str}\n\n"
            f" Текущие параметры: {campaign.daily_limit} писем/день, "
            f"{'безлимитно' if not campaign.max_emails or campaign.max_emails == 0 else f'макс. {campaign.max_emails}'} всего, "
            f"статус: {campaign.status}"
        )
    except Exception as e:
        logger.error(f"[EMAIL_CAMPAIGN] Error updating campaign: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка обновления кампании: {str(e)}"
    finally:
        if close_session:
            session.close()


async def send_outreach_email(
    campaign_id: int = None,
    recipient_email: str = None,
    recipient_name: str = None,
    recipient_company: str = None,
    recipient_context: str = None,
    subject: str = None,
    body: str = None,
    sent_by_agent: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Отправить email в рамках кампании через Resend API.

    Может вызываться вручную или автономно агентом (через якорь email_outreach_send).
    """
    if not session:
        session = Session()
        close_session = True
    try:
        from config import RESEND_API_KEY as _platform_resend_key

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        # ── GUARD: проверка user_rules — запрет email-рассылки ──
        try:
            from ai_integration.memory import decrypt_data as _dec_email_rule
            import json as _json_er
            _mem_er = _json_er.loads(_dec_email_rule(user.memory)) if user.memory else {}
            _EMAIL_STOP_KW = ('не писать', 'не отправлять', 'не слать', 'стоп email',
                              'stop email', 'без email', 'без рассылк', 'запрет email',
                              'не рассыл', 'прекрати email', 'прекрати рассыл',
                              'отключить email', 'отключи email', 'не использовать email',
                              'не отправляй email', 'не отправляй письм',
                              'не пиши по email', 'не пиши email', 'не пиши по почте',
                              'не писать по email', 'не писать email', 'не писать по почте')
            for _r_er in _mem_er.get('rules', []):
                if any(kw in _r_er.lower() for kw in _EMAIL_STOP_KW):
                    return f"⛔ Email-рассылка заблокирована правилом пользователя: «{_r_er[:80]}»"
        except Exception as _e_er:
            logger.debug("suppressed email rule check: %s", _e_er)

        # ── GUARD: не отправлять email на адрес самого пользователя ИЛИ IMAP-аккаунт агента ──
        _rcpt = (recipient_email or '').strip().lower()
        _user_email = (getattr(user, 'email', '') or '').strip().lower()
        _own_emails_oe = set()
        if _user_email:
            _own_emails_oe.add(_user_email)
        try:
            from models import UserAgent as _UA_oe
            for _ag_oe in session.query(_UA_oe).filter(
                _UA_oe.author_id == user.id,
                _UA_oe.user_api_keys.isnot(None),
            ).all():
                for _ln_oe in (_ag_oe.user_api_keys or '').splitlines():
                    _ln_oe = _ln_oe.strip()
                    if _ln_oe.upper().startswith('GMAIL_USER=') or _ln_oe.upper().startswith('IMAP_USER='):
                        _imap_val_oe = _ln_oe.split('=', 1)[1].strip().lower()
                        if _imap_val_oe and '@' in _imap_val_oe:
                            _own_emails_oe.add(_imap_val_oe)
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        if _rcpt and _rcpt in _own_emails_oe:
            return (f" Нельзя отправлять outreach на {_rcpt} — это ваша почта или IMAP-аккаунт агента. "
                    f"Найди email реального внешнего получателя.")

        # ── GUARD: не отправлять на домен собственной платформы ──
        if _rcpt and '@' in _rcpt:
            _OWN_PLATFORM_DOMAINS_SEND = {'asibiont.com'}
            _rcpt_domain_chk = _rcpt.rsplit('@', 1)[1]
            if _rcpt_domain_chk in _OWN_PLATFORM_DOMAINS_SEND:
                return f"⛔ {_rcpt} — адрес платформы ASI Biont. Не отправляй outreach на собственный домен."

        # ── GUARD: невалидные домены — LinkedIn, noreply, бот-адреса ──
        if _rcpt and '@' in _rcpt:
            _rcpt_domain_val = _rcpt.rsplit('@', 1)[1]
            _BLOCKED_EMAIL_DOMAINS = {
                'linkedin.com', 'users.noreply.github.com',
                'reply.github.com', 'notifications.github.com',
            }
            if _rcpt_domain_val in _BLOCKED_EMAIL_DOMAINS:
                return (f"⛔ {_rcpt} — адрес сервиса {_rcpt_domain_val}, email не доставляется. "
                        f"Найди реальный рабочий email получателя.")
            _rcpt_local_val = _rcpt.rsplit('@', 1)[0]
            _BLOCKED_LOCAL_PREFIXES = ('noreply', 'no-reply', 'donotreply', 'do-not-reply',
                                       'mailer-daemon', 'postmaster', 'abuse', 'bounce')
            if any(_rcpt_local_val.startswith(p) for p in _BLOCKED_LOCAL_PREFIXES):
                return (f"⛔ {_rcpt} — системный/noreply адрес. Найди реальный email человека.")

        # ── GUARD: фейковый / generic email ──
        if _rcpt and _is_generic_email(_rcpt):
            return f"⛔ {_rcpt} — фейковый или generic email (example.com, test.com и т.п.). Найди реальный email получателя."

        # ── GUARD: не отправлять outreach уже зарегистрированному пользователю платформы ──
        if _rcpt:
            _platform_user_chk = session.query(User).filter(
                User.id != user.id,
                User.email == _rcpt,
            ).first()
            if _platform_user_chk:
                return (f"⚠️ {_rcpt} — уже зарегистрирован на платформе ASI Biont "
                        f"(@{_platform_user_chk.username or _platform_user_chk.first_name or '?'}). "
                        f"Приглашать существующего пользователя бессмысленно. Ищи НОВЫХ людей.")

        # ── GUARD: фейковое / корпоративное имя получателя ──
        _rname_chk = (recipient_name or '').strip()
        if _rname_chk:
            import re as _re_fn
            # Слова-признаки команды/компании вместо реального человека
            _FAKE_NAME_WORDS = {
                'team', 'founders', 'автор', 'author', 'редакция', 'редактор',
                'компания', 'company', 'group', 'corp', 'inc', 'llc', 'ltd',
                'department', 'отдел', 'команда', 'коллектив', 'staff',
            }
            _name_lower = _rname_chk.lower()
            _name_words = set(_re_fn.findall(r'[a-zA-Zа-яА-ЯёЁ]+', _name_lower))
            if _name_words & _FAKE_NAME_WORDS:
                return (f"⛔ «{_rname_chk}» — это не имя конкретного человека (команда/компания/автор). "
                        f"Найди ФИО реального получателя.")
            # Имя = один токен И всё в нижнем или начинается с заглавной — фамилия без имени
            _name_parts = _rname_chk.split()
            if len(_name_parts) == 1 and len(_rname_chk) > 2:
                # Одно слово — OK только если это явно имя (Liam, Марк), иначе предупреждение
                # Но если нет имени в body, то лучше иметь полное имя
                pass  # Допускаем одно слово — NAME_NOT_IN_BODY поймает

        # ── GUARD: C-suite крупнейших мировых корпораций ──
        # Холодный outreach на CEO/CTO Fortune 500 вредит репутации домена и бесполезен
        _FORTUNE500_DOMAINS = {
            'oracle.com', 'microsoft.com', 'google.com', 'apple.com', 'amazon.com',
            'meta.com', 'facebook.com', 'berkshirehathaway.com', 'exxonmobil.com',
            'unh.com', 'cvs.com', 'mckesson.com', 'walmart.com', 'jpmorgan.com',
            'jpmorganchase.com', 'bofa.com', 'bankofamerica.com', 'wellsfargo.com',
            'citi.com', 'citigroup.com', 'goldmansachs.com', 'gs.com',
            'sap.com', 'ibm.com', 'cisco.com', 'intel.com', 'samsung.com',
            'nvidia.com', 'tesla.com', 'spacex.com', 'samsung.com',
        }
        _CSUITE_PREFIXES = (
            'ceo', 'cto', 'cfo', 'coo', 'cmo', 'chairman', 'president',
            'larry', 'elon', 'sundar', 'satya', 'tim.cook', 'jensen',
            'mark.zuckerberg', 'jeff', 'bezos', 'warren', 'buffett',
        )
        if _rcpt and '@' in _rcpt:
            _rcpt_local, _rcpt_domain = _rcpt.rsplit('@', 1)
            if _rcpt_domain in _FORTUNE500_DOMAINS:
                import logging as _log_f500
                _log_f500.getLogger('anchors').info(f"[EMAIL] Fortune 500 domain skipped: {_rcpt}")
                return (
                    f"⚠️ {_rcpt} — корпоративный адрес крупной компании ({_rcpt_domain}). "
                    f"Холодный outreach на Fortune 500 обычно не результативен. "
                    f"Лучше писать стартапам, инди-разработчикам, малому/среднему бизнесу — но решать тебе."
                )
            if any(_rcpt_local.startswith(pref) for pref in _CSUITE_PREFIXES):
                # Дополнительно проверим: домен крупной компании или C-suite prefix явный?
                _csuite_explicit = any(_rcpt_local == pref for pref in ('ceo', 'cto', 'cfo', 'coo', 'cmo', 'chairman'))
                if _csuite_explicit:
                    return (
                        f"⚠️ {_rcpt} — похоже на email C-suite руководителя (префикс '{_rcpt_local}'). "
                        f"Такие адреса обычно не читаются лично. Лучше найти более подходящий контакт."
                    )

        # ── GUARD: плейсхолдеры в теле письма ──
        _body_to_check_oe = (body or '') + ' ' + (subject or '')
        if _body_to_check_oe:
            import re as _re_ph_oe
            _PH_RE_OE = _re_ph_oe.compile(
                r'\[(?:вставьте|вставить|ваш[аеу]?|your|insert|add)\s+[^\]]{3,50}\]|'
                r'\[(?:ссылк[аеу]|link|url|zoom|meet)\s*(?:здесь|here|сюда)?\]',
                _re_ph_oe.IGNORECASE,
            )
            _ph_m_oe = _PH_RE_OE.search(_body_to_check_oe)
            if _ph_m_oe:
                return (f"⛔ Письмо содержит плейсхолдер: «{_ph_m_oe.group()}». "
                        f"Замени на реальные данные или убери. Нельзя отправлять шаблон клиенту.")

        # ── GUARD: имя получателя обязательно ──
        _rname_send = (recipient_name or '').strip()
        if not _rname_send:
            return ("⛔ Не указано имя получателя (recipient_name). "
                    "Нельзя отправлять холодное письмо без имени — сначала найди ФИО контакта.")

        # ── GUARD: имя получателя должно быть в теле письма (персонализация) ──
        if _rname_send and body:
            _first_name_oe = _rname_send.split()[0]
            if len(_first_name_oe) >= 2 and _first_name_oe not in body:
                return (f"⛔ Имя получателя «{_first_name_oe}» не упоминается в теле письма. "
                        f"Добавь персональное обращение — холодное письмо без имени выглядит как спам.")

        # ── GUARD: не отправлять уже зарегистрированным в системе пользователям ──
        # Пользователь просил: "не нужно писать тем, кто уже есть в системе — ищем новых"
        if _rcpt:
            try:
                from sqlalchemy import func as _func_reg
                _registered = session.query(User).filter(
                    User.email.isnot(None),
                    _func_reg.lower(User.email) == _rcpt,
                    User.id != user.id,
                ).first()
                if _registered:
                    return (f"⛔ {_rcpt} уже зарегистрирован в ASI Biont. "
                            f"Пишем только новым внешним пользователям — этот контакт пропускаем.")
            except Exception as _e_reg:
                logger.debug("suppressed registered-user check: %s", _e_reg)

        # ── GUARD: не отправлять отписавшимся / bounced контактам ──
        if _rcpt:
            try:
                _ec_unsub_chk = session.query(EmailContact).filter_by(
                    user_id=user.id, email=_rcpt,
                ).first()
                if _ec_unsub_chk and _ec_unsub_chk.status == 'unsubscribed':
                    return f"⛔ {_rcpt} отписался — отправка заблокирована."
                if _ec_unsub_chk and _ec_unsub_chk.status == 'bounced':
                    return f"⛔ {_rcpt} — адрес bounced, отправка заблокирована."
            except Exception as _e_unsub_chk:
                logger.debug("suppressed unsubscribed/bounced check: %s", _e_unsub_chk)

        # ── GUARD: не слать новый холодный outreach тому, кто уже ответил ──
        # replied/interested — это активные контакты, для них используй reply_to_outreach_email или negotiate_by_email
        if _rcpt:
            try:
                _ec_replied_chk = session.query(EmailContact).filter(
                    EmailContact.user_id == user.id,
                    EmailContact.email == _rcpt,
                    EmailContact.status.in_(['replied', 'interested']),
                ).first()
                if _ec_replied_chk:
                    return (
                        f"⛔ {_rcpt} уже ответил (статус: {_ec_replied_chk.status}). "
                        "Не отправляй новый холодный outreach — используй reply_to_outreach_email "
                        "чтобы ответить на их сообщение, или negotiate_by_email для продолжения диалога."
                    )
            except Exception as _e_rpl_chk:
                logger.debug("suppressed replied check: %s", _e_rpl_chk)

        # Sanitize token hallucinations in email body/subject
        from ai_integration.conversation_history import sanitize_token_hallucinations
        if body:
            body = sanitize_token_hallucinations(body)
        if subject:
            subject = sanitize_token_hallucinations(subject)

        # Личный RESEND_API_KEY из user_api_keys агентов пользователя имеет приоритет
        RESEND_API_KEY = _platform_resend_key
        _personal_resend_from = ''
        try:
            from models import UserAgent as _UA_rs
            for _ag_rs in session.query(_UA_rs).filter(
                _UA_rs.author_id == user.id,
                _UA_rs.status != 'disabled',
                _UA_rs.user_api_keys.isnot(None),
            ).all():
                _env_rs = {}
                for _ln_rs in (_ag_rs.user_api_keys or '').splitlines():
                    _ln_rs = _ln_rs.strip()
                    if '=' in _ln_rs and not _ln_rs.startswith('#'):
                        _k_rs, _, _v_rs = _ln_rs.partition('=')
                        _env_rs[_k_rs.strip().upper()] = _v_rs.strip()
                if _env_rs.get('RESEND_API_KEY'):
                    RESEND_API_KEY = _env_rs['RESEND_API_KEY']
                    _personal_resend_from = (
                        _env_rs.get('RESEND_FROM') or
                        _env_rs.get('SENDER_EMAIL') or
                        _env_rs.get('FROM_EMAIL') or ''
                    )
                    import logging as _log_rs
                    _log_rs.getLogger(__name__).info(
                        f'[EMAIL_OUTREACH] Using personal RESEND_API_KEY from agent {_ag_rs.name}'
                    )
                    break
        except Exception as _rs_err:
            import logging as _log_rs2
            _log_rs2.getLogger(__name__).debug(f'[EMAIL_OUTREACH] Personal Resend lookup: {_rs_err}')

        if not RESEND_API_KEY:
            return " Resend API не настроен. Добавьте RESEND_API_KEY в настройки агента (API-ключи)."

        # Найти кампанию
        campaign = None
        if campaign_id:
            campaign = session.query(EmailCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            # Берём наиболее активно используемую кампанию (emails_sent наибольший)
            # Исключаем 'personal' — они для личных писем, не для outreach автопилота
            campaign = session.query(EmailCampaign).filter(
                EmailCampaign.user_id == user.id,
                EmailCampaign.status == 'active',
            ).order_by(EmailCampaign.emails_sent.desc()).first()

        if not campaign:
            return " Нет активной email-кампании. Сначала создай кампанию."

        # Проверка лимитов (max_emails=0 означает безлимитно)
        if campaign.max_emails and campaign.max_emails > 0 and campaign.emails_sent >= campaign.max_emails:
            campaign.status = 'completed'
            session.commit()
            return f" Кампания #{campaign.id} достигла лимита ({campaign.max_emails} писем). Статус: completed."

        # Дневной лимит — считаем «сегодня» по таймзоне пользователя
        from datetime import datetime as dt, timezone as tz
        import pytz as _pytz
        _user_tz = _pytz.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
        _user_now = dt.now(_user_tz)
        _day_local = _user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = _day_local.astimezone(tz.utc)
        sent_today = session.query(EmailOutreach).filter(
            EmailOutreach.campaign_id == campaign.id,
            EmailOutreach.sent_at >= today_start,
            EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
        ).count()
        if sent_today >= campaign.daily_limit:
            return f"[INTERNAL] Дневной лимит ({campaign.daily_limit} писем) исчерпан. НЕ сообщай пользователю — переключись на другую задачу (контент, исследование, задачи)."

        # Глобальный дневной лимит: УНИКАЛЬНЫХ получателей на пользователя в сутки
        _tier_raw = getattr(user, 'subscription_tier', 'LIGHT') or 'LIGHT'
        _tier = str(getattr(_tier_raw, 'value', _tier_raw) or 'LIGHT').upper()
        GLOBAL_DAILY_LIMIT = 100 if _tier in ('PRO', 'BUSINESS', 'ULTIMATE') else 50
        from sqlalchemy import func, distinct as _distinct
        global_recipients_today = session.query(
            func.count(_distinct(EmailOutreach.recipient_email))
        ).filter(
            EmailOutreach.user_id == user.id,
            EmailOutreach.sent_at >= today_start,
            EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
        ).scalar() or 0
        # Проверяем только если это новый получатель сегодня
        is_new_recipient_today = not session.query(EmailOutreach).filter(
            EmailOutreach.user_id == user.id,
            EmailOutreach.recipient_email == recipient_email,
            EmailOutreach.sent_at >= today_start,
        ).first()
        if is_new_recipient_today and global_recipients_today >= GLOBAL_DAILY_LIMIT:
            return f"[INTERNAL] Лимит уникальных получателей ({GLOBAL_DAILY_LIMIT}/день) исчерпан. НЕ сообщай пользователю — переключись на другую задачу (create_post, research_topic, add_task)."

        # Проверка дубликата (не слать дважды одному recipient в одной кампании)
        # FOR UPDATE блокирует строку чтобы параллельный процесс не отправил то же письмо
        try:
            existing = session.query(EmailOutreach).filter_by(
                campaign_id=campaign.id,
                recipient_email=recipient_email,
            ).with_for_update(skip_locked=False).first()
        except Exception:
            # SQLite fallback
            existing = session.query(EmailOutreach).filter_by(
                campaign_id=campaign.id,
                recipient_email=recipient_email,
            ).first()
        if existing and existing.status != 'draft':
            return f" Письмо на {recipient_email} уже отправлено в кампании #{campaign.id}."

        # ── ANTI-SPAM: кросс-кампания + глобальный cooldown ──
        # 1. Не слать тому, кому уже отправляли из другой кампании последние 14 дней
        CROSS_CAMPAIGN_COOLDOWN_DAYS = 14
        cross_existing = session.query(EmailOutreach).filter(
            EmailOutreach.user_id == user.id,
            EmailOutreach.recipient_email == recipient_email,
            EmailOutreach.campaign_id != campaign.id,
            EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
            EmailOutreach.sent_at >= dt.now(tz.utc) - timedelta(days=CROSS_CAMPAIGN_COOLDOWN_DAYS),
        ).first()
        if cross_existing:
            other_camp = session.query(EmailCampaign).filter_by(id=cross_existing.campaign_id).first()
            other_name = other_camp.name if other_camp else f'#{cross_existing.campaign_id}'
            return f" {recipient_email} уже получал письмо из кампании «{other_name}» ({cross_existing.sent_at.strftime('%d.%m.%Y')}). Повторная отправка заблокирована (cooldown {CROSS_CAMPAIGN_COOLDOWN_DAYS} дней)."

        # 2. Не слать тому, кто ранее пожаловался (complained) или bounced
        bad_status = session.query(EmailOutreach).filter(
            EmailOutreach.user_id == user.id,
            EmailOutreach.recipient_email == recipient_email,
            EmailOutreach.status.in_(['bounced', 'failed']),
        ).first()
        if bad_status:
            return f" {recipient_email} ранее вернул bounced/failed (статус: {bad_status.status}). Отправка заблокирована."

        if not subject or not body:
            return " Нужны subject и body письма."

        # ── GUARD: запрещённые слова в теме письма ──
        # DeepSeek регулярно игнорирует инструкции и ставит "тестирование", "AI employee" и т.п.
        # Проверяем программно и отклоняем.
        if subject:
            import re as _re_subj
            _subj_lower = subject.lower()
            _BANNED_SUBJECT_PATTERNS = [
                r'\bтест\w*',            # тест, тестирование, тестовый
                r'\btest\w*',             # test, testing
                r'\bai.?employee\b',      # AI employee, AI-employee
                r'\bai.?сотрудни\w*',     # AI-сотрудник, AI сотрудников
                r'\basi\s*biont\b',       # ASI Biont
                r'\bплатформ\w*',         # платформа, платформы
                r'\bпредложение о сотрудничестве\b',
                r'\bcooperation\s+offer\b',
                r'\bbusiness\s+opportunity\b',
                r'\bpartnership\s+opportunity\b',
                r'\bсотрудничество\b',    # слишком generic
            ]
            _banned_match = None
            for _bp in _BANNED_SUBJECT_PATTERNS:
                _m = _re_subj.search(_bp, _subj_lower)
                if _m:
                    _banned_match = _m.group()
                    break
            if _banned_match:
                return (
                    f"⛔ Тема письма содержит запрещённое слово/фразу: «{_banned_match}». "
                    f"Текущая тема: «{subject}». "
                    "ПЕРЕПИШИ тему: она должна быть конкретной, персонализированной под получателя, "
                    "без слов 'тест', 'платформа', 'AI employee', 'ASI Biont', 'сотрудничество'. "
                    "Хороший пример: «Вопрос по автоматизации {компания}» или «{имя}, идея для {проект}»."
                )

        # ── GUARD: язык subject+body должен соответствовать языку контакта ──
        if subject and body:
            import unicodedata as _ud_lang
            def _detect_script_oe(text):
                scripts = {}
                for ch in text:
                    if ch.isalpha():
                        try:
                            name = _ud_lang.name(ch, '').split()[0]
                        except ValueError:
                            continue
                        scripts[name] = scripts.get(name, 0) + 1
                return scripts

            # Определяем ожидаемый язык контакта
            _expected_lang = None
            try:
                from models import EmailContactPreference as _ECP_oe
                _pref_oe = session.query(_ECP_oe).filter_by(
                    user_id=user.id, contact_email=_rcpt
                ).first()
                if _pref_oe and _pref_oe.preferred_language:
                    _expected_lang = _pref_oe.preferred_language.lower()
            except Exception:
                pass

            if not _expected_lang:
                # Определяем по домену/имени/контексту (согласованно с _detect_recipient_lang)
                _ru_domains = ('.ru', '.by', '.ua', '.kz', '.рф')
                _ru_providers = ('yandex.com', 'yandex.ru', 'ya.ru', 'mail.ru', 'bk.ru',
                                 'rambler.ru', 'inbox.ru', 'list.ru', 'tut.by')
                _domain_oe = _rcpt.split('@')[-1].lower() if '@' in _rcpt else ''
                def _has_cyr_oe(s):
                    return any('\u0400' <= c <= '\u04ff' for c in (s or ''))
                _cyr_in_name_oe = _has_cyr_oe(f"{recipient_name or ''} {recipient_company or ''}")
                _ctx_lower_oe = (recipient_context or '').lower()
                _ru_ctx_oe = any(p in _ctx_lower_oe for p in [
                    'habr', 'vc.ru', 'хабр', 'pikabu', 'mail.ru',
                    'rambler', 'yandex.ru', 'vk.com', 't.me', 'ok.ru',
                ])
                if (any(_domain_oe.endswith(d) for d in _ru_domains)
                        or _domain_oe in _ru_providers
                        or _cyr_in_name_oe
                        or _ru_ctx_oe):
                    _expected_lang = 'ru'
                else:
                    _expected_lang = 'en'

            _body_scripts = _detect_script_oe(subject + ' ' + body)
            _body_top = max(_body_scripts, key=_body_scripts.get) if _body_scripts else 'LATIN'

            if _expected_lang == 'en' and _body_top == 'CYRILLIC' and _body_scripts.get('CYRILLIC', 0) > 20:
                return ("⚠ Email написан на русском (кириллица), но контакт ожидает English. "
                        "ПЕРЕПИШИ subject и body на английском языке!")
            if _expected_lang == 'ru' and _body_top == 'LATIN' and _body_scripts.get('LATIN', 0) > 20:
                return ("⚠ Email написан на английском (латиница), но контакт ожидает русский. "
                        "ПЕРЕПИШИ subject и body на русском языке!")

        # MX-проверка домена получателя
        mx_valid, mx_err = _validate_email_domain(recipient_email)
        if not mx_valid:
            # Помечаем контакт как bounced — чтобы он не попадал в unsent_contacts повторно
            try:
                _ec_mx = session.query(EmailContact).filter_by(
                    user_id=user.id, email=_rcpt,
                ).first()
                if _ec_mx and _ec_mx.status not in ('unsubscribed',):
                    _ec_mx.status = 'bounced'
                    session.commit()
            except Exception as _e_mx:
                logger.debug("suppressed MX bounce mark: %s", _e_mx)
            return f" {mx_err}"

        # Отправляем через Resend — plain text (без HTML чтобы не попасть в Промоакции)
        import aiohttp as _aiohttp
        from config import WEB_APP_URL
        _unsub_url = f"{WEB_APP_URL}/terms#unsubscribe"
        resend_id = None
        try:
            async with _aiohttp.ClientSession() as http:
                # Используем RESEND_FROM (верифицированный домен) если sender_email — сторонний
                # _personal_resend_from: из user_api_keys агента (RESEND_FROM/SENDER_EMAIL/FROM_EMAIL)
                from config import RESEND_FROM as _resend_from_cfg
                _effective_resend_from = _personal_resend_from or _resend_from_cfg
                _free_domains = ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
                                 'mail.ru', 'yandex.ru', 'yandex.com', 'inbox.ru', 'list.ru')
                _sender_domain = (campaign.sender_email or '').split('@')[-1].lower()
                # Free-mail domains (gmail, yandex, mail.ru etc.) cannot be used as Resend
                # sender — Resend requires a verified domain. Always use RESEND_FROM / platform
                # default for free-mail senders. reply_to is set to the real user address below.
                if _sender_domain in _free_domains:
                    _from_addr = _effective_resend_from or 'outreach@asibiont.com'
                else:
                    _from_addr = campaign.sender_email or _effective_resend_from or 'outreach@asibiont.com'
                from_header = f"{campaign.sender_name} <{_from_addr}>"
                # reply_to указывает на реальный адрес пользователя (может быть gmail)
                _reply_to_addr = campaign.sender_email if campaign.sender_email and '@' in campaign.sender_email else None
                # Добавляем строку отписки в тело (CAN-SPAM / GDPR)
                _body_with_footer = body + f"\n\n---\nЧтобы отписаться от писем: {_unsub_url}"
                resp = await http.post(
                    'https://api.resend.com/emails',
                    headers={
                        'Authorization': f'Bearer {RESEND_API_KEY}',
                        'Content-Type': 'application/json',
                    },
                    json={
                        'from': from_header,
                        'to': [recipient_email],
                        'reply_to': [_reply_to_addr] if _reply_to_addr else None,
                        'subject': subject,
                        'text': _body_with_footer,
                        'headers': {'List-Unsubscribe': f'<{_unsub_url}>', 'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'},
                    },
                    timeout=_aiohttp.ClientTimeout(total=15),
                )
                resp_data = await resp.json()
                if resp.status in (200, 201):
                    resend_id = resp_data.get('id')
                    logger.info(f"[EMAIL_OUTREACH] Sent to {redact_email(recipient_email)}: {resend_id}")
                    # Сбрасываем запись ошибки при успехе
                    try:
                        from .service_health import clear_error as _clr_svc
                        _clr_svc('resend')
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                else:
                    err = resp_data.get('message', str(resp_data))
                    logger.error(f"[EMAIL_OUTREACH] Resend error: {resp.status} {err}")
                    try:
                        from .service_health import record_error as _rec_svc
                        _rec_svc('resend', f'HTTP {resp.status}: {err}', code=resp.status, detail=str(resp_data)[:300])
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    return f" Ошибка Resend API: {err}"
        except Exception as e:
            logger.error(f"[EMAIL_OUTREACH] Send error: {e}")
            return f" Ошибка отправки: {str(e)}"

        # Anti-spam задержка между письмами (10 сек)
        import asyncio as _asyncio_delay
        await _asyncio_delay.sleep(10)

        # Сохраняем в БД (обновляем draft или создаём новый)
        if existing and existing.status == 'draft':
            outreach = existing
            outreach.subject = subject
            outreach.body = body
            outreach.status = 'sent'
            outreach.resend_id = resend_id
            outreach.sent_at = dt.now(tz.utc)
        else:
            outreach = EmailOutreach(
                campaign_id=campaign.id,
                user_id=user.id,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                recipient_company=recipient_company,
                recipient_context=recipient_context,
                subject=subject,
                body=body,
                status='sent',
                resend_id=resend_id,
                sent_at=dt.now(tz.utc),
                sent_by_agent=sent_by_agent or None,
            )
            session.add(outreach)
        campaign.emails_sent = (campaign.emails_sent or 0) + 1
        # Ставим follow-up через 3 дня
        outreach.next_follow_up_at = dt.now(tz.utc) + timedelta(days=3)

        # ── Авто-обновление прогресса цели при отправке письма ──
        # Ищем активную цель, которая отслеживает отправку писем по этой кампании
        try:
            from models import Goal as _Goal_oe
            _email_kw_oe = ('рассылк', 'email', 'письм', 'outreach', 'кампан', 'campaign', 'отправ')
            _active_goals_oe = session.query(_Goal_oe).filter(
                _Goal_oe.user_id == user.id,
                _Goal_oe.status == 'active',
                _Goal_oe.metric_target.isnot(None),
            ).all()
            for _goal_oe in _active_goals_oe:
                _gtext_oe = (
                    _goal_oe.title + ' ' +
                    (_goal_oe.description or '') + ' ' +
                    (_goal_oe.metric_unit or '')
                ).lower()
                # Цель должна быть про email/рассылку И метрика — про письма/отправку
                _is_email_goal = any(kw in _gtext_oe for kw in _email_kw_oe)
                _not_reply_goal = not any(
                    w in _gtext_oe for w in ('ответ', 'reply', 'replied', 'ответили')
                )
                if _is_email_goal and _not_reply_goal:
                    _new_mc_oe = float(campaign.emails_sent)
                    _old_mc_oe = float(_goal_oe.metric_current or 0)
                    if _new_mc_oe > _old_mc_oe:
                        _pct_oe = min(100, int(_new_mc_oe / float(_goal_oe.metric_target) * 100))
                        _goal_oe.metric_current = _new_mc_oe
                        _goal_oe.progress_percentage = _pct_oe
                        logger.info(
                            f'[EMAIL_OUTREACH] Auto-updated goal "{_goal_oe.title}": '
                            f'{_new_mc_oe}/{_goal_oe.metric_target} ({_pct_oe}%)'
                        )
                    break
        except Exception as _e_goal_oe:
            logger.debug(f'[EMAIL_OUTREACH] Auto goal update failed: {_e_goal_oe}')

        # Логируем в AgentActivityLog для ленты активности
        try:
            from models import AgentActivityLog
            _name_part = f" ({recipient_name})" if recipient_name else ""
            _sndr_name = (campaign.sender_name or '').strip()
            _sndr_email = (campaign.sender_email or '').strip()
            _from_line = ''
            if _sndr_name or _sndr_email:
                _from_line = f"От: {_sndr_name}{' <' + _sndr_email + '>' if _sndr_email else ''}\n"
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='email',
                title=f"{_sndr_name + ' → ' if _sndr_name else ''}{recipient_email}{_name_part}",
                content=f"Тема: {subject}\n{_from_line}\n{body}",
                target=recipient_email,
                status='sent',
                ref_id=outreach.id if hasattr(outreach, 'id') else None,
            )
            session.add(log_entry)
        except Exception as _log_err:
            logger.warning(f"[EMAIL_OUTREACH] Activity log error: {_log_err}")

        # Авто-сохранение EmailContact при успешной отправке
        try:
            from models import EmailContact as _EC_auto
            _ec_email = (recipient_email or '').strip().lower()
            _ec_existing = session.query(_EC_auto).filter_by(
                user_id=user.id, email=_ec_email
            ).first()
            if not _ec_existing:
                session.add(_EC_auto(
                    user_id=user.id,
                    email=_ec_email,
                    name=(recipient_name or '').strip() or None,
                    company=(recipient_company or '').strip() or None,
                    source='outreach',
                    status='contacted',
                    last_contacted_at=dt.now(tz.utc),
                ))
            else:
                _ec_existing.last_contacted_at = dt.now(tz.utc)
                if recipient_name and not _ec_existing.name:
                    _ec_existing.name = recipient_name.strip()
                # Обновляем статус: new → contacted (если ещё не replied/interested)
                if _ec_existing.status in ('new', None):
                    _ec_existing.status = 'contacted'
        except Exception as _ec_err:
            logger.warning(f"[EMAIL_OUTREACH] Auto-save contact error: {_ec_err}")

        session.commit()

        # ── Email Content Fingerprint (Improvement #2) ──
        # Вычисляем fingerprint сразу после commit чтобы id уже был
        try:
            _body_text = (body or '')
            _body_len = len(_body_text)
            _rcpt_markers = []
            if recipient_name:
                _rcpt_markers.append(recipient_name.lower().split()[0] if ' ' in recipient_name else recipient_name.lower())
            if recipient_company:
                _rcpt_markers.append(recipient_company.lower()[:10])
            _has_pers = bool(_rcpt_markers and any(m in _body_text.lower() for m in _rcpt_markers))
            _CTA_KW = ('напишите', 'свяжитесь', 'позвоните', 'ответьте', 'reply', 'contact', 'call', 'book', 'schedule',
                       'запишитесь', 'оставьте', 'перейдите', 'click', 'нажмите', 'заполните')
            _has_cta = any(kw in _body_text.lower() for kw in _CTA_KW)
            _FORMAL_KW = ('уважаемый', 'уважаемая', 'dear', 'sincerely', 'regards', 'с уважением', 'господин', 'госпожа')
            _TECH_KW = ('api', 'github', 'технолог', 'разработ', 'stack', 'backend', 'frontend', 'cloud', 'devops', 'code')
            _COMM_KW = ('продажи', 'клиент', 'заказ', 'цена', 'прайс', 'скидка', 'offer', 'price', 'deal', 'partnership')
            _bl = _body_text.lower()
            if any(kw in _bl for kw in _FORMAL_KW):
                _tone = 'formal'
            elif any(kw in _bl for kw in _TECH_KW):
                _tone = 'technical'
            elif any(kw in _bl for kw in _COMM_KW):
                _tone = 'commercial'
            else:
                _tone = 'friendly'
            from datetime import datetime as _dt_fp, timezone as _tz_fp
            outreach.body_length = _body_len
            outreach.has_personalization = _has_pers
            outreach.has_call_to_action = _has_cta
            outreach.tone_type = _tone
            outreach.sent_at_hour_utc = _dt_fp.now(_tz_fp.utc).hour
            session.commit()
            logger.info(f'[EMAIL_FP] outreach#{outreach.id}: len={_body_len}, pers={_has_pers}, cta={_has_cta}, tone={_tone}')
        except Exception as _e_fp:
            logger.debug('[EMAIL_FP] fingerprint failed: %s', _e_fp)

        lang = _get_lang(user_id)
        name_str = f" ({recipient_name})" if recipient_name else ""
        _max_label = campaign.max_emails if campaign.max_emails and campaign.max_emails > 0 else '∞'
        if lang == 'en':
            return f" Email sent to {recipient_email}{name_str}\nSubject: {subject}\nCampaign #{campaign.id} — {campaign.emails_sent}/{_max_label} sent"
        return f" Письмо отправлено: {recipient_email}{name_str}\nТема: {subject}\nКампания #{campaign.id} — {campaign.emails_sent}/{_max_label} отправлено"

    except Exception as e:
        logger.error(f"[EMAIL_OUTREACH] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def reply_to_outreach_email(
    outreach_id: int = None,
    recipient_email: str = None,
    reply_body: str = None,
    user_id: int = None,
    sent_by_agent: str = None,
    session=None,
    close_session: bool = True,
):
    """Ответить на входящий reply от получателя (AI автоматически или по запросу).

    Приоритет отправки: SMTP пользователя → Resend пользователя → платформенный Resend.
    """
    if not session:
        session = Session()
        close_session = True
    try:
        from datetime import datetime as dt, timezone as tz
        import aiohttp as _aiohttp

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        # Найти письмо
        outreach = None
        if outreach_id:
            outreach = session.query(EmailOutreach).filter_by(
                id=outreach_id, user_id=user.id
            ).first()
        elif recipient_email:
            # Сначала ищем с status='replied' (идеальный случай — check_emails уже обновил)
            outreach = session.query(EmailOutreach).filter_by(
                user_id=user.id, recipient_email=recipient_email, status='replied'
            ).order_by(EmailOutreach.reply_at.desc()).first()
            # Fallback: если check_emails не успел обновить статус — ищем sent/delivered/opened
            if not outreach:
                outreach = session.query(EmailOutreach).filter(
                    EmailOutreach.user_id == user.id,
                    EmailOutreach.recipient_email == recipient_email,
                    EmailOutreach.status.in_(['sent', 'delivered', 'opened']),
                ).order_by(EmailOutreach.sent_at.desc()).first()
                # Помечаем как replied раз агент знает об ответе
                if outreach:
                    outreach.status = 'replied'
                    if not outreach.reply_at:
                        from datetime import datetime as _dt_roe, timezone as _tz_roe
                        outreach.reply_at = _dt_roe.now(_tz_roe.utc)
                    session.commit()

        if not outreach:
            return " Не найдено письмо для ответа."

        # ── GUARD: проверяем владение email-перепиской (кто отправлял = тот и отвечает) ──
        _original_agent = (outreach.sent_by_agent or '').strip()
        _current_agent = (sent_by_agent or '').strip()
        if _original_agent and _current_agent and _original_agent.lower() != _current_agent.lower():
            # Проверяем пользовательское правило о продолжении переписки тем же агентом
            _has_ownership_rule = False
            try:
                import json as _j_own
                _u_mem = (user.memory or '').strip()
                if _u_mem.startswith('{'):
                    _mem_j = _j_own.loads(_u_mem)
                    _rules = _mem_j.get('rules', [])
                    for _r in _rules:
                        _rl = str(_r).lower()
                        if ('от имени' in _rl and 'агент' in _rl) or ('перв' in _rl and 'писал' in _rl):
                            _has_ownership_rule = True
                            break
            except Exception:
                pass
            if _has_ownership_rule:
                return (f"⛔ Это письмо отправлял(а) {_original_agent}. "
                        f"По правилам пользователя переписку ведёт тот агент, кто первый писал. "
                        f"Передай задачу {_original_agent} через DELEGATE[{_original_agent}].")
            else:
                logger.info(f"[EMAIL_REPLY] agent mismatch: original={_original_agent}, current={_current_agent}")

        # ── GUARD: не отвечать отписавшимся контактам ──
        _reply_rcpt = (recipient_email or outreach.recipient_email or '').strip().lower()
        if _reply_rcpt:
            try:
                _ec_reply_chk = session.query(EmailContact).filter_by(
                    user_id=user.id, email=_reply_rcpt, status='unsubscribed',
                ).first()
                if _ec_reply_chk:
                    return f"⛔ {_reply_rcpt} отписался — ответ заблокирован."
            except Exception as _e_reply_chk:
                logger.debug("suppressed unsubscribed check in reply: %s", _e_reply_chk)

        # ── GUARD: сканируем reply_text на opt-out сигналы (на случай если check_emails ещё не обработал) ──
        # Убираем цитируемую часть (quoted): строки с '>' и всё после 'On ... wrote:'
        _raw_reply_text = outreach.reply_text or ''
        import re as _re_strip_quote
        # Обрезаем по 'On ... wrote:' (стандарт Gmail/Outlook)
        _quote_cut = _re_strip_quote.split(r'\r?\nOn .{10,120}wrote:', _raw_reply_text, maxsplit=1)
        _reply_no_quote = _quote_cut[0] if _quote_cut else _raw_reply_text
        # Убираем строки начинающиеся с '>'
        _reply_no_quote = '\n'.join(
            ln for ln in _reply_no_quote.splitlines() if not ln.strip().startswith('>')
        )
        _contact_reply_text = _reply_no_quote.lower()
        if _contact_reply_text:
            import re as _re_unsub_guard
            _UNSUB_GUARD_RE = _re_unsub_guard.compile(
                r'\bunsubscribe\b|\bopt[\s\-]?out\b|'
                r'\bstop\s+(?:emailing|contacting|writing|sending)\b|'
                r'\bdo\s+not\s+(?:contact|email|write|send)\b|'
                r'\bdon\'?t\s+(?:contact|email|write|send)\b|'
                r'\bnot\s+interested\b|\bleave\s+me\s+alone\b|'
                r'не\s*пиши(?:те)?|(?:прошу|просьба)\s+(?:не\s+писать|больше\s+не|прекратить)|'
                r'отпис(?:ать|ка|аться)|'
                r'(?:больше\s+)?не\s+(?:нужно|надо|хочу)\s*(?:писать|получать)|'
                r'(?:прекратите|перестаньте)\s+(?:писать|рассылку|отправлять)|'
                # Greek
                r'μη\s*(?:μου)?\s*(?:στ[εέ]λν|γρ[αά]φ)|σταματ[ήη]στε|'
                r'(?:δεν|δε)\s+(?:με\s+)?ενδιαφ[εέ]ρ|(?:δεν|δε)\s+θ[εέ]λω|'
                r'αφ[ήη]στ[εέ]\s+(?:με|μου)|'
                # Spanish / German / French / Italian / Portuguese / Turkish
                r'(?:no\s+me\s+(?:escriba|contacte))|(?:darse\s+de\s+baja)|'
                r'(?:ab(?:bestellen|melden))|(?:kein\s+interesse)|'
                r'(?:d[eé]sabonner|d[eé]sinscri)|(?:pas\s+int[eé]ress[eé])|'
                r'(?:non\s+(?:sono\s+)?interessat[oa])|'
                r'(?:(?:não|nao)\s+(?:estou\s+)?interessad[oa])|'
                r'(?:yazma(?:yın|yin))|(?:ilgilenmiyorum)',
                _re_unsub_guard.IGNORECASE,
            )
            if _UNSUB_GUARD_RE.search(_contact_reply_text):
                # Auto-unsubscribe the contact
                try:
                    _ec_auto = session.query(EmailContact).filter_by(
                        user_id=user.id, email=_reply_rcpt
                    ).first()
                    if _ec_auto:
                        _ec_auto.status = 'unsubscribed'
                        _old_n = _ec_auto.notes or ''
                        if 'отписка' not in _old_n.lower():
                            _ec_auto.notes = ((_old_n + '\n') if _old_n else '') + '[отписка: контакт попросил не писать]'
                    outreach.status = 'unsubscribed'
                    outreach.next_follow_up_at = None
                    session.commit()
                    logger.info(f'[EMAIL_REPLY] AUTO-UNSUBSCRIBE on reply guard: {_reply_rcpt}')
                except Exception as _e_auto_unsub:
                    logger.debug(f'[EMAIL_REPLY] auto-unsubscribe failed: {_e_auto_unsub}')
                    try:
                        session.rollback()
                    except Exception:
                        pass
                return f"⛔ {_reply_rcpt} просил не писать — ответ заблокирован. Контакт отмечен как отписавшийся."

        # Защита от спама: не более 2 AI-ответов одному контакту суммарно по всем записям
        # Считаем SUM(ai_reply_count), а не количество СТРОК — иначе 1 строка с 10 ответами пройдёт guard
        _MAX_AI_REPLIES = 2
        _email_to_check = (recipient_email or outreach.recipient_email or '').strip().lower()
        _ai_reply_count = 0
        _last_ai_reply_at = outreach.ai_reply_sent_at
        if _email_to_check:
            from sqlalchemy import func as _func_spam
            _total_replies = session.query(
                _func_spam.coalesce(_func_spam.sum(EmailOutreach.ai_reply_count), 0)
            ).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == _email_to_check,
                EmailOutreach.ai_reply_sent_at.isnot(None),
            ).scalar() or 0
            _ai_reply_count = int(_total_replies)
            # Get last reply time
            _last_row = session.query(EmailOutreach.ai_reply_sent_at).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == _email_to_check,
                EmailOutreach.ai_reply_sent_at.isnot(None),
            ).order_by(EmailOutreach.ai_reply_sent_at.desc()).first()
            if _last_row:
                _last_ai_reply_at = _last_row[0]
        elif outreach.ai_reply_sent_at:
            _ai_reply_count = outreach.ai_reply_count or 1
        if _ai_reply_count >= _MAX_AI_REPLIES:
            sent_str = _last_ai_reply_at.strftime('%d.%m %H:%M') if _last_ai_reply_at else '?'
            return (f"🛑 Стоп-спам: {_email_to_check or outreach.recipient_email} уже получил {_ai_reply_count} AI-ответа "
                    f"(последний: {sent_str}). Максимум {_MAX_AI_REPLIES} ответа на контакт — дальше спам.")

        campaign = session.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
        if not campaign:
            return " Кампания не найдена."

        _sender_addr_norm = (campaign.sender_email or '').strip().lower()
        _user_email_norm = (getattr(user, 'email', '') or '').strip().lower()
        if _reply_rcpt and ((_sender_addr_norm and _reply_rcpt == _sender_addr_norm) or (_user_email_norm and _reply_rcpt == _user_email_norm)):
            return f"⛔ Self-reply detected: {_reply_rcpt} — автоответ самому себе заблокирован."

        if not reply_body:
            return " Нужен текст ответа (reply_body)."

        # ── GUARD: блокируем плейсхолдеры в тексте ответа ──
        import re as _re_placeholder
        _PLACEHOLDER_RE = _re_placeholder.compile(
            r'\[(?:вставьте|вставить|ваш[аеу]?|your|insert|add)\s+[^\]]{3,50}\]|'
            r'\[(?:ссылк[аеу]|link|url|zoom|meet)\s*(?:здесь|here|сюда)?\]',
            _re_placeholder.IGNORECASE,
        )
        _ph_match = _PLACEHOLDER_RE.search(reply_body)
        if _ph_match:
            return (f"⛔ Ответ содержит плейсхолдер: «{_ph_match.group()}». "
                    f"Нельзя отправлять шаблон вместо реальных данных. "
                    f"Спроси у пользователя через send_message_to_user если нужна ссылка/данные.")
        # ── GUARD: язык reply_body должен совпадать с языком ответа контакта (reply_text) ──
        # Если контакт ответил на определённом языке — AI должен отвечать на ТОМ ЖЕ языке.
        # Fallback: если reply_text нет — сравниваем с языком оригинального outreach.
        _contact_reply = (outreach.reply_text or '')
        _lang_reference = _contact_reply if len(_contact_reply) > 20 else (outreach.body or '')
        if _lang_reference and reply_body:
            import re as _re_lang
            import unicodedata as _ud
            def _detect_script(text):
                scripts = {}
                for ch in text:
                    if ch.isalpha():
                        try:
                            name = _ud.name(ch, '').split()[0]
                        except ValueError:
                            continue
                        scripts[name] = scripts.get(name, 0) + 1
                return scripts
            _ref_scripts = _detect_script(_lang_reference)
            _reply_scripts = _detect_script(reply_body)
            _ref_top = max(_ref_scripts, key=_ref_scripts.get) if _ref_scripts else 'LATIN'
            _reply_top = max(_reply_scripts, key=_reply_scripts.get) if _reply_scripts else 'LATIN'
            # Блокируем если доминирующий скрипт отличается (Greek vs Cyrillic, Cyrillic vs Latin, etc.)
            if _ref_top != _reply_top and max(_ref_scripts.values(), default=0) > 20:
                _script_names = {'LATIN': 'латиница', 'CYRILLIC': 'кириллица', 'GREEK': 'греческий', 'ARABIC': 'арабский', 'CJK': 'CJK', 'HANGUL': 'корейский', 'HIRAGANA': 'японский', 'KATAKANA': 'японский', 'DEVANAGARI': 'деванагари'}
                _ref_name = _script_names.get(_ref_top, _ref_top)
                _reply_name = _script_names.get(_reply_top, _reply_top)
                _src = 'ответа контакта' if len(_contact_reply) > 20 else 'оригинального письма'
                return (f"⚠ Язык reply_body ({_reply_name}) не совпадает с языком {_src} ({_ref_name}). "
                        f"ПЕРЕПИШИ reply_body на {_ref_name} — контакт ожидает ответ на своём языке!")

        # MX-проверка (на всякий — получатель мог сменить домен)
        mx_valid, mx_err = _validate_email_domain(outreach.recipient_email)
        if not mx_valid:
            return f" {mx_err}"

        subject = f"Re: {outreach.subject}" if outreach.subject else "Re: Your inquiry"
        to_clean = outreach.recipient_email.strip().lower()
        sender_name = campaign.sender_name or ''
        sender_addr = campaign.sender_email or ''

        # ── Выбор канала отправки ──────────────────────────────────────────────
        # Ищем интеграцию пользователя с адресом = sender_addr кампании.
        # Если совпадение есть — используем его (Gmail OAuth / SMTP / user Resend).
        # Если не найдено — fallback на платформенный Resend с адресом кампании.
        _integrations = _get_user_email_integrations(user, session)
        _matched = None
        for _intg in _integrations:
            if _intg.get('email_user', '').lower() == sender_addr.lower():
                _matched = _intg
                break
        # Нет точного совпадения — берём первую доступную интеграцию (пользователь настроил почту)
        if not _matched and _integrations:
            _matched = _integrations[0]

        _send_error = None

        if _matched and _matched.get('type') == 'gmail_oauth':
            # ── Gmail OAuth: прямая отправка через Gmail API ─────────────────
            _ok_r, _res_r = await _send_via_gmail_api(
                _matched['token_data'], to_clean, subject, reply_body,
                sender_name, user, session,
            )
            if _ok_r:
                logger.info(f'[EMAIL_REPLY] Sent via Gmail API from {_res_r} to {to_clean}')
            else:
                _send_error = _res_r

        elif _matched and _matched.get('type') == 'gmail_server':
            # ── Gmail (пароль приложения): серверный Resend + Reply-To ────────
            from config import RESEND_API_KEY as _rk_gm_r
            _rt_gm_r = _matched.get('reply_to') or _matched.get('email_user') or sender_addr
            _gm_r_json = {'from': f"{sender_name} <outreach@asibiont.com>",
                          'to': [to_clean], 'subject': subject, 'text': reply_body}
            try:
                _gm_r_json['html'] = _build_email_html(_text_to_email_html(reply_body), sender_name=sender_name)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            if _rt_gm_r and '@' in _rt_gm_r:
                _gm_r_json['reply_to'] = [_rt_gm_r]
            try:
                async with _aiohttp.ClientSession() as _hgr:
                    _rgr = await _hgr.post('https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {_rk_gm_r}', 'Content-Type': 'application/json'},
                        json=_gm_r_json, timeout=_aiohttp.ClientTimeout(total=15))
                    _dgr = await _rgr.json()
                    if _rgr.status not in (200, 201):
                        _send_error = _dgr.get('message', str(_dgr))
                    else:
                        logger.info(f'[EMAIL_REPLY] Sent via server Resend (Gmail Reply-To: {_rt_gm_r}) to {to_clean}')
            except Exception as _egr:
                _send_error = f'Gmail server: {_egr}'

        elif _matched and _matched.get('type') == 'smtp':
            # ── SMTP пользователя (Яндекс / Mail.ru / Gmail app-password) ──────
            import smtplib as _smtplib
            from email.mime.text import MIMEText as _MimeSmtp
            from email.mime.multipart import MIMEMultipart as _MMsmtp
            import asyncio as _aio_smtp
            import ssl as _ssl_smtp

            _smtp_host = _matched['smtp_host']
            _smtp_port = _matched['smtp_port']
            _smtp_user = _matched['email_user']
            _smtp_pass = _matched['email_pass'].replace(' ', '')

            def _do_smtp():
                msg = _MMsmtp('alternative')
                msg['From'] = f"{sender_name} <{_smtp_user}>"
                msg['To'] = to_clean
                msg['Subject'] = subject
                msg.attach(_MimeSmtp(reply_body, 'plain', 'utf-8'))
                try:
                    _reply_html = _build_email_html(_text_to_email_html(reply_body), sender_name=sender_name)
                    msg.attach(_MimeSmtp(_reply_html, 'html', 'utf-8'))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                _ctx = _ssl_smtp.create_default_context()
                with _smtplib.SMTP(_smtp_host, _smtp_port, timeout=30) as s:
                    s.ehlo(); s.starttls(context=_ctx); s.ehlo()
                    s.login(_smtp_user, _smtp_pass)
                    s.sendmail(_smtp_user, to_clean, msg.as_string())

            _loop_smtp = _aio_smtp.get_running_loop()
            try:
                await _aio_smtp.wait_for(_loop_smtp.run_in_executor(None, _do_smtp), timeout=35.0)
                logger.info(f'[EMAIL_REPLY] Sent via SMTP ({_matched["label"]}) from {_smtp_user} to {to_clean}')
            except Exception as _se:
                _send_error = f'SMTP ({_matched["label"]}): {_se}'

        elif _matched and _matched.get('type') == 'resend':
            # ── Личный Resend ключ пользователя ───────────────────────────────
            _urk = _matched['resend_key']
            _uf = _matched.get('email_user') or sender_addr
            try:
                async with _aiohttp.ClientSession() as http:
                    resp = await http.post(
                        'https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {_urk}', 'Content-Type': 'application/json'},
                        json={'from': f"{sender_name} <{_uf}>", 'to': [to_clean],
                              'subject': subject, 'text': reply_body,
                              'html': _build_email_html(_text_to_email_html(reply_body), sender_name=sender_name)},
                        timeout=_aiohttp.ClientTimeout(total=15),
                    )
                    rd = await resp.json()
                    if resp.status not in (200, 201):
                        _send_error = rd.get('message', str(rd))
                    else:
                        logger.info(f'[EMAIL_REPLY] Sent via user Resend from {_uf} to {to_clean}')
            except Exception as _re:
                _send_error = f'Resend: {_re}'

        # Fallback: платформенный Resend (если нет интеграции или предыдущие упали)
        if _matched is None or _send_error:
            from config import RESEND_API_KEY
            if not RESEND_API_KEY:
                return f" Ошибка отправки{': ' + _send_error if _send_error else ''}. Подключи почту в настройках агента."
            try:
                async with _aiohttp.ClientSession() as http:
                    _fb_r_json = {'from': f"{sender_name} <outreach@asibiont.com>",
                                  'to': [to_clean], 'subject': subject, 'text': reply_body}
                    try:
                        _fb_r_json['html'] = _build_email_html(_text_to_email_html(reply_body), sender_name=sender_name)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    if sender_addr and '@' in sender_addr:
                        _fb_r_json['reply_to'] = [sender_addr]
                    resp = await http.post(
                        'https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
                        json=_fb_r_json,
                        timeout=_aiohttp.ClientTimeout(total=15),
                    )
                    resp_data = await resp.json()
                    if resp.status not in (200, 201):
                        err = resp_data.get('message', str(resp_data))
                        prev_err = f' (предыдущая попытка: {_send_error})' if _send_error else ''
                        return f" Ошибка Resend API: {err}{prev_err}"
                    logger.info(f'[EMAIL_REPLY] Sent via platform Resend (Reply-To: {sender_addr}) to {to_clean}')
            except Exception as e:
                return f" Ошибка отправки: {_send_error or str(e)}"

        outreach.ai_reply_text = reply_body
        outreach.ai_reply_sent_at = dt.now(tz.utc)
        outreach.ai_reply_count = (outreach.ai_reply_count or 0) + 1
        outreach.success = True  # конверсия: двусторонний диалог состоялся

        # Продвигаем EmailContact в статус 'interested' — двусторонний контакт состоялся.
        # НЕ оставляем 'replied' — иначе агент будет бесконечно видеть их через list_email_contacts(status='replied').
        try:
            from models import EmailContact as _EC_rply
            _ec_rply = session.query(_EC_rply).filter_by(
                user_id=user.id, email=outreach.recipient_email.strip().lower()
            ).first()
            if _ec_rply:
                _ec_rply.status = 'interested'  # двусторонний диалог подтверждён
                _ec_rply.last_contacted_at = dt.now(tz.utc)
            else:
                # Создаём контакт с interested статусом если его ещё нет
                session.add(_EC_rply(
                    user_id=user.id,
                    email=outreach.recipient_email.strip().lower(),
                    name=outreach.recipient_name or '',
                    source='outreach_reply',
                    status='interested',  # сразу отмечаем как engaged
                    notes='Ответил на outreach — подтверждён двусторонний контакт',
                    last_contacted_at=dt.now(tz.utc),
                ))
        except Exception as _ec_err:
            logger.debug(f"[EMAIL_REPLY] EmailContact replied update failed: {_ec_err}")
        try:
            from models import AgentActivityLog
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='email',
                title=f"Reply → {outreach.recipient_email}",
                content=f"Re: {outreach.subject}\n\n{reply_body}",
                target=outreach.recipient_email,
                status='sent',
                ref_id=outreach.id,
            )
            session.add(log_entry)
        except Exception as _log_err:
            logger.warning(f"[EMAIL_REPLY] Activity log error: {_log_err}")

        session.commit()

        return f" Ответ отправлен на {outreach.recipient_email}\nТема: {subject}"
    except Exception as e:
        logger.error(f"[EMAIL_REPLY] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def add_email_leads(
    campaign_id: int = None,
    leads: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Добавить email-адреса в кампанию (найденные через web_search или указанные вручную).

    leads — JSON-массив: [{"email": "a@b.com", "name": "Name", "company": "Co", "context": "why relevant"}]
    или простой список email через запятую.
    """
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        campaign = None
        if campaign_id:
            campaign = session.query(EmailCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            campaign = session.query(EmailCampaign).filter_by(
                user_id=user.id, status='active'
            ).order_by(EmailCampaign.created_at.desc()).first()
        if not campaign:
            return " Нет активной кампании."

        # Парсим leads
        parsed = []

        # Если AI передал leads как list/dict — работаем напрямую
        if isinstance(leads, (list, dict)):
            raw_list = leads if isinstance(leads, list) else [leads]
            for item in raw_list:
                if isinstance(item, dict):
                    clean = {k.strip('"\' '): v for k, v in item.items()}
                    parsed.append(clean)
                elif isinstance(item, str) and '@' in item:
                    parsed.append({'email': item.strip().lower()})
        else:
            leads_str = (leads or '').strip()
            # Убираем двойное экранирование которое иногда добавляет AI
            leads_clean = leads_str.replace('\\"', '"')
            try:
                raw = json.loads(leads_clean)
                if isinstance(raw, list):
                    # normalize keys: strip extra quotes that AI may add
                    for item in raw:
                        if isinstance(item, dict):
                            clean = {k.strip('"\' '): v for k, v in item.items()}
                            parsed.append(clean)
                elif isinstance(raw, dict):
                    clean = {k.strip('"\' '): v for k, v in raw.items()}
                    parsed.append(clean)
                elif isinstance(raw, str):
                    # double-encoded
                    raw2 = json.loads(raw)
                    if isinstance(raw2, list):
                        for item in raw2:
                            if isinstance(item, dict):
                                clean = {k.strip('"\' '): v for k, v in item.items()}
                                parsed.append(clean)
            except Exception:
                parsed = []

            if not parsed and isinstance(leads, str):
                # Простой список email через запятую/перенос строки.
                # ВАЖНО: сначала пробуем парсить каждую строку как JSON-объект,
                # чтобы не сохранять фрагменты вроде '{"email": "foo@bar.com"'
                # (это происходит когда AI передаёт JSONL-строку и json.loads fails,
                # тогда split(',') режет JSON-объекты по запятым внутри них).
                _email_re = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
                _seen_emails_fp: set = set()
                for line in re.split(r'\n', leads):
                    line = line.strip(' ,;[]')
                    if not line:
                        continue
                    # Попытка: парсим строку как JSON-объект (JSONL формат)
                    if line.startswith('{'):
                        try:
                            _obj = json.loads(line.rstrip(','))
                            if isinstance(_obj, dict):
                                _em = str(_obj.get('email', '')).strip().lower()
                                if _em and '@' in _em and _em not in _seen_emails_fp:
                                    _seen_emails_fp.add(_em)
                                    parsed.append({k: v for k, v in _obj.items()})
                                continue
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        # Fallback: вытащить email regex из фрагмента JSON
                        _match = _email_re.search(line)
                        if _match:
                            _em = _match.group(0).lower()
                            if _em not in _seen_emails_fp:
                                _seen_emails_fp.add(_em)
                                parsed.append({'email': _em})
                        continue
                    # Обычная строка: ищем email регуляркой
                    for _m in _email_re.finditer(line):
                        _em = _m.group(0).lower()
                        if _em not in _seen_emails_fp:
                            _seen_emails_fp.add(_em)
                            parsed.append({'email': _em})

        if not parsed:
            return " Не удалось распарсить email-адреса. Укажи JSON или через запятую."

        # ── ФИЛЬТР: generic-адреса компаний (info@, contact@, etc.) ──
        GENERIC_PREFIXES = {
            'info', 'contact', 'contacts', 'hello', 'hi', 'support', 'sales',
            'admin', 'office', 'team', 'help', 'mail', 'noreply', 'no-reply',
            'hr', 'billing', 'press', 'media', 'marketing', 'general',
            'enquiries', 'enquiry', 'feedback', 'service', 'webmaster',
        }

        added = 0
        skipped = 0
        skipped_generic = 0
        skipped_registered = 0
        _user_email_lower = (getattr(user, 'email', '') or '').strip().lower()
        # Собираем ВСЕ собственные email (user + IMAP-аккаунты агентов)
        _own_emails_leads = set()
        if _user_email_lower:
            _own_emails_leads.add(_user_email_lower)
        try:
            from models import UserAgent as _UA_leads
            for _ag_leads in session.query(_UA_leads).filter(
                _UA_leads.author_id == user.id,
                _UA_leads.user_api_keys.isnot(None),
            ).all():
                for _ln_leads in (_ag_leads.user_api_keys or '').splitlines():
                    _ln_leads = _ln_leads.strip()
                    if _ln_leads.upper().startswith(('GMAIL_USER=', 'IMAP_USER=')):
                        _imap_v = _ln_leads.split('=', 1)[1].strip().lower()
                        if _imap_v and '@' in _imap_v:
                            _own_emails_leads.add(_imap_v)
        except Exception:
            pass
        # Предвыбираем emails зарегистрированных пользователей для быстрой проверки
        from sqlalchemy import func as _func_leads
        _registered_emails_set: set = set()
        try:
            _reg_rows = session.query(_func_leads.lower(User.email)).filter(
                User.email.isnot(None)
            ).all()
            _registered_emails_set = {r[0] for r in _reg_rows if r[0]}
        except Exception as _e_re:
            logger.debug("suppressed registered-emails prefetch: %s", _e_re)
        # Предвыбираем домены с bounce/failed для быстрой блокировки целых доменов
        _bounced_domains_set: set = set()
        try:
            _bounced_rows = session.query(EmailOutreach.recipient_email).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.status.in_(['bounced', 'failed']),
            ).all()
            for _br in _bounced_rows:
                if _br[0] and '@' in _br[0]:
                    _bd = _br[0].rsplit('@', 1)[1].lower()
                    _bounced_domains_set.add(_bd)
            # Не блокируем крупные домены — gmail, yandex, mail.ru и т.д. (bounce скорее по человеку)
            _bounced_domains_set -= {'gmail.com', 'yandex.ru', 'mail.ru', 'outlook.com',
                                      'hotmail.com', 'yahoo.com', 'protonmail.com', 'icloud.com'}
        except Exception as _e_bd:
            logger.debug("suppressed bounced-domains prefetch: %s", _e_bd)
        for lead in parsed:
            email = lead.get('email', '').strip().lower()
            if not email or '@' not in email:
                continue
            # ── GUARD: не добавлять email самого пользователя / IMAP-аккаунт как лид ──
            if email in _own_emails_leads:
                skipped += 1
                continue
            # ── GUARD: не добавлять уже зарегистрированных пользователей платформы ──
            if email in _registered_emails_set:
                skipped_registered += 1
                continue
            # ── GUARD: не добавлять отписавшихся контактов ──
            _ec_lead_chk = session.query(EmailContact).filter_by(
                user_id=user.id, email=email, status='unsubscribed',
            ).first()
            if _ec_lead_chk:
                skipped += 1
                continue
            # Отклоняем generic-адреса через полный фильтр
            if _is_generic_email(email):
                skipped_generic += 1
                continue
            # ── GUARD: сервисные домены (LinkedIn, noreply.github, etc.) ──
            _lead_domain = email.rsplit('@', 1)[1] if '@' in email else ''
            _BLOCKED_LEAD_DOMAINS = {
                'linkedin.com', 'users.noreply.github.com',
                'reply.github.com', 'notifications.github.com',
                'asibiont.com', 'example.com', 'test.com', 'localhost',
            }
            # Добавляем собственные домены пользователя
            for _oe_l in _own_emails_leads:
                if '@' in _oe_l:
                    _BLOCKED_LEAD_DOMAINS.add(_oe_l.rsplit('@', 1)[1])
            if _lead_domain in _BLOCKED_LEAD_DOMAINS:
                skipped += 1
                continue
            _lead_local = email.rsplit('@', 1)[0] if '@' in email else ''
            if _lead_local.startswith(('noreply', 'no-reply', 'donotreply', 'mailer-daemon')):
                skipped += 1
                continue
            # ── GUARD: домен ранее давал bounce/failed — скорее всего весь домен мёртв ──
            if _lead_domain and _lead_domain in _bounced_domains_set:
                skipped += 1
                continue
            # Дубль-проверка в текущей кампании
            exists = session.query(EmailOutreach).filter_by(
                campaign_id=campaign.id, recipient_email=email
            ).first()
            if exists:
                skipped += 1
                continue

            # ── ANTI-SPAM: кросс-кампания + bounced/failed ──
            from datetime import datetime as _dt_leads, timezone as _tz_leads
            CROSS_CAMPAIGN_COOLDOWN_DAYS = 14
            cross_exists = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == email,
                EmailOutreach.campaign_id != campaign.id,
                EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
                EmailOutreach.sent_at >= _dt_leads.now(_tz_leads.utc) - timedelta(days=CROSS_CAMPAIGN_COOLDOWN_DAYS),
            ).first()
            if cross_exists:
                skipped += 1
                continue
            bad_history = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == email,
                EmailOutreach.status.in_(['bounced', 'failed']),
            ).first()
            if bad_history:
                skipped += 1
                continue

            outreach = EmailOutreach(
                campaign_id=campaign.id,
                user_id=user.id,
                recipient_email=email,
                recipient_name=lead.get('name'),
                recipient_company=lead.get('company'),
                recipient_context=lead.get('context'),
                status='draft',
            )
            session.add(outreach)

            # Контакт НЕ создаём при добавлении лида — только при реальной переписке
            # (когда контакт ответил на письмо или идёт диалог)

            added += 1
        session.commit()

        # ── Немедленный триггер anchor engine для отправки черновиков ──
        if added > 0:
            try:
                from anchor_engine import get_anchor_engine
                _engine = get_anchor_engine()
                if _engine:
                    try:
                        import asyncio as _asyncio_leads
                        loop = _asyncio_leads.get_running_loop()
                        loop.create_task(_engine._process_user(user.telegram_id))
                    except RuntimeError:
                        # Нет текущего event loop — запускаем через ensure_future
                        import asyncio as _asyncio_leads
                        _asyncio_leads.ensure_future(_engine._process_user(user.telegram_id))
                    logger.info(f"[EMAIL_LEADS] Triggered anchor engine for user {user.telegram_id} after adding {added} leads")
            except Exception as _trigger_err:
                logger.warning(f"[EMAIL_LEADS] Failed to trigger anchor engine: {_trigger_err}")

        parts = [f" Добавлено {added} email-адресов в кампанию #{campaign.id}"]
        if skipped:
            parts.append(f"пропущено {skipped} дублей/cooldown")
        if skipped_generic:
            parts.append(f"отклонено {skipped_generic} generic-адресов (info@/contact@/hello@ — нужны ЛИЧНЫЕ email людей)")
        if skipped_registered:
            parts.append(f"пропущено {skipped_registered} уже зарегистрированных в системе — ищем новых")
        return parts[0] + (f" ({', '.join(parts[1:])})" if len(parts) > 1 else "")
    except Exception as e:
        logger.error(f"[EMAIL_LEADS] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def get_email_campaign_status(
    campaign_id: int = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Получить статус email-кампании: сколько отправлено, ответов, ожидающих."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        campaigns = []
        if campaign_id:
            c = session.query(EmailCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
            if c:
                campaigns = [c]
        else:
            campaigns = session.query(EmailCampaign).filter_by(
                user_id=user.id
            ).order_by(EmailCampaign.created_at.desc()).limit(5).all()

        if not campaigns:
            return " Нет email-кампаний. Создай кампанию: «запусти email-кампанию для привлечения клиентов»."

        result = []
        import pytz as _pytz_cs
        from datetime import timezone as _tz_cs
        _user_tz_cs = _pytz_cs.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
        _user_now_cs = datetime.now(_user_tz_cs)
        _today_start_cs = _user_now_cs.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz_cs.utc)

        # Batch: load all emails for all campaigns in one query
        _cs_camp_ids = [c.id for c in campaigns]
        _cs_all_emails = session.query(EmailOutreach).filter(
            EmailOutreach.campaign_id.in_(_cs_camp_ids)
        ).all()
        _cs_emails_by_camp: dict = {}
        for _e in _cs_all_emails:
            _cs_emails_by_camp.setdefault(_e.campaign_id, []).append(_e)

        # Batch: sent_today per campaign via GROUP BY
        from sqlalchemy import func as _func_cs
        _cs_sent_today_map = dict(
            session.query(EmailOutreach.campaign_id, _func_cs.count(EmailOutreach.id)).filter(
                EmailOutreach.campaign_id.in_(_cs_camp_ids),
                EmailOutreach.sent_at >= _today_start_cs,
                EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
            ).group_by(EmailOutreach.campaign_id).all()
        )

        for c in campaigns:
            emails = _cs_emails_by_camp.get(c.id, [])
            draft = sum(1 for e in emails if e.status == 'draft')
            sent = sum(1 for e in emails if e.status == 'sent')
            delivered = sum(1 for e in emails if e.status == 'delivered')
            replied = sum(1 for e in emails if e.status == 'replied')
            bounced = sum(1 for e in emails if e.status in ('bounced', 'failed'))

            # Сколько отправлено сегодня (из batch-карты)
            sent_today = _cs_sent_today_map.get(c.id, 0)
            daily_limit = c.daily_limit or 50

            # Умный подстатус
            is_active = c.status in ('active', 'running')
            if c.status == 'paused':
                status_emoji = ''
                status_text = 'На паузе'
            elif c.status == 'completed':
                status_emoji = ''
                status_text = 'Завершена'
            elif c.status == 'cancelled':
                status_emoji = ''
                status_text = 'Отменена'
            elif is_active and sent_today >= daily_limit:
                status_emoji = ''
                status_text = f'Ждёт завтра (лимит {daily_limit}/день исчерпан)'
            elif is_active and draft == 0 and (c.emails_sent or 0) == 0 and sent_today == 0:
                status_emoji = ''
                status_text = 'Нет лидов — нужны контакты (add_email_leads)'
            elif is_active and draft == 0 and ((c.emails_sent or 0) > 0 or sent_today > 0):
                status_emoji = ''
                status_text = 'Все отправлены, ищет новые контакты'
            elif is_active:
                status_emoji = '🟢'
                status_text = f'Отправляет ({draft} черновиков готово)'
            else:
                status_emoji = ''
                status_text = c.status or 'неизвестно'

            block = (
                f"{status_emoji} Кампания #{c.id}: «{c.name}»\n"
                f" Статус: {status_text}\n"
                f" Всего: {len(emails)} | Черновики: {draft} | Отправлено: {sent + delivered}\n"
                f" Ответов: {replied} | Ошибки: {bounced}\n"
                f" Сегодня: {sent_today}/{daily_limit} | Всего: {c.emails_sent or 0}{f'/{c.max_emails}' if c.max_emails and c.max_emails > 0 else '/∞'}"
            )
            if replied > 0:
                recent_replies = [e for e in emails if e.status == 'replied' and e.reply_text]
                for r in recent_replies[:5]:
                    _rt_display = (r.reply_text or '').strip()
                    _rt_name = r.recipient_name or r.recipient_email
                    block += f"\n\n  📩 Ответ от {_rt_name} ({r.recipient_email}):"
                    if r.reply_at:
                        import pytz as _ptz_r
                        _rtz = _ptz_r.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
                        _rat = r.reply_at.replace(tzinfo=__import__('datetime').timezone.utc).astimezone(_rtz)
                        block += f" {_rat.strftime('%d.%m.%Y %H:%M')}"
                    block += f"\n     {_rt_display}"
            result.append(block)

        return "\n\n".join(result)
    except Exception as e:
        logger.error(f"[EMAIL_STATUS] Error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def pause_email_campaign(
    campaign_id: int = None,
    action: str = 'pause',
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Поставить на паузу или возобновить email-кампанию."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        campaign = None
        if campaign_id:
            campaign = session.query(EmailCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            campaign = session.query(EmailCampaign).filter_by(
                user_id=user.id, status='active' if action == 'pause' else 'paused'
            ).order_by(EmailCampaign.created_at.desc()).first()

        if not campaign:
            return " Кампания не найдена."

        if action == 'pause':
            campaign.status = 'paused'
            session.commit()
            return f" Кампания #{campaign.id} «{campaign.name}» поставлена на паузу."
        elif action == 'resume':
            campaign.status = 'active'
            session.commit()
            return f"▶ Кампания #{campaign.id} «{campaign.name}» возобновлена."
        elif action == 'cancel':
            campaign.status = 'cancelled'
            session.commit()
            return f" Кампания #{campaign.id} «{campaign.name}» отменена."
        else:
            return f" Неизвестное действие: {action}. Допустимо: pause, resume, cancel."
    except Exception as e:
        logger.error(f"[EMAIL_PAUSE] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def send_follow_up_email(
    outreach_id: int = None,
    recipient_email: str = None,
    subject: str = None,
    body: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Отправить follow-up email (агент вызывает автономно при якоре email_follow_up).

    Обновляет follow_up_count, next_follow_up_at.
    Приоритет отправки: SMTP пользователя → Resend пользователя → платформенный Resend.
    """
    if not session:
        session = Session()
        close_session = True
    try:
        from datetime import datetime as dt, timezone as tz
        import aiohttp as _aiohttp

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        # Найти письмо
        outreach = None
        if outreach_id:
            outreach = session.query(EmailOutreach).filter_by(
                id=outreach_id, user_id=user.id
            ).first()
        elif recipient_email:
            outreach = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == recipient_email,
                EmailOutreach.status.in_(['sent', 'delivered', 'opened']),
            ).order_by(EmailOutreach.sent_at.desc()).first()

        if not outreach:
            return " Не найдено письмо для follow-up."

        # ── GUARD: не отправлять follow-up отписавшимся / bounced контактам ──
        try:
            _ec_fu_chk = session.query(EmailContact).filter_by(
                user_id=user.id, email=(outreach.recipient_email or '').strip().lower(),
            ).first()
            if _ec_fu_chk and _ec_fu_chk.status == 'unsubscribed':
                return f"⛔ {outreach.recipient_email} отписался — follow-up заблокирован."
            if _ec_fu_chk and _ec_fu_chk.status == 'bounced':
                return f"⛔ {outreach.recipient_email} — адрес bounced, follow-up заблокирован."
        except Exception as _e_fu_chk:
            logger.debug("suppressed unsubscribed check in follow_up: %s", _e_fu_chk)

        campaign = session.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
        if not campaign:
            return " Кампания не найдена."

        max_follow_ups = campaign.max_follow_ups or 2
        # Если контакт ответил (replied) — follow-up без ограничений (продолжаем диалог)
        if outreach.status != 'replied' and outreach.follow_up_count >= max_follow_ups:
            return f" Достигнут лимит follow-up ({max_follow_ups}) для {outreach.recipient_email}. Контакт не отвечает."

        # Follow-up — к уже существующему получателю, глобальный лимит не применяется

        if not subject:
            subject = f"Re: {outreach.subject}" if outreach.subject else "Following up"
        if not body:
            return " Нужен текст follow-up (body)."

        # ── GUARD: плейсхолдеры в теле follow-up ──
        import re as _re_ph_fu
        _PH_RE_FU = _re_ph_fu.compile(
            r'\[(?:вставьте|вставить|ваш[аеу]?|your|insert|add)\s+[^\]]{3,50}\]|'
            r'\[(?:ссылк[аеу]|link|url|zoom|meet)\s*(?:здесь|here|сюда)?\]',
            _re_ph_fu.IGNORECASE,
        )
        _ph_m_fu = _PH_RE_FU.search((body or '') + ' ' + (subject or ''))
        if _ph_m_fu:
            return (f"⛔ Follow-up содержит плейсхолдер: «{_ph_m_fu.group()}». "
                    f"Замени на реальные данные или убери. Спроси у пользователя через send_message_to_user.")

        # MX-проверка
        mx_valid, mx_err = _validate_email_domain(outreach.recipient_email)
        if not mx_valid:
            return f" {mx_err}"

        # ── Выбор канала отправки: SMTP пользователя → user Resend → platform Resend ──
        sender_name = campaign.sender_name or ''
        sender_addr = campaign.sender_email or ''
        to_clean = outreach.recipient_email.strip().lower()
        from config import WEB_APP_URL
        _unsub_url = f"{WEB_APP_URL}/terms#unsubscribe"

        _integrations = _get_user_email_integrations(user, session)
        _matched = None
        for _intg in _integrations:
            if _intg.get('email_user', '').lower() == sender_addr.lower():
                _matched = _intg
                break
        if not _matched and _integrations:
            _matched = _integrations[0]

        _send_error = None

        if _matched and _matched.get('type') == 'gmail_oauth':
            # ── Gmail OAuth: прямая отправка через Gmail API ─────────────────
            _ok_f, _res_f = await _send_via_gmail_api(
                _matched['token_data'], to_clean, subject, body,
                sender_name, user, session,
            )
            if _ok_f:
                logger.info(f'[EMAIL_FOLLOWUP] Sent via Gmail API from {_res_f} to {to_clean}')
            else:
                _send_error = _res_f

        elif _matched and _matched.get('type') == 'gmail_server':
            # ── Gmail (пароль приложения): серверный Resend + Reply-To ────────
            from config import RESEND_API_KEY as _rk_gm_f
            _rt_gm_f = _matched.get('reply_to') or _matched.get('email_user') or sender_addr
            _gm_f_json = {'from': f"{sender_name} <outreach@asibiont.com>",
                          'to': [to_clean], 'subject': subject, 'text': body}
            try:
                _gm_f_json['html'] = _build_email_html(_text_to_email_html(body), sender_name=sender_name)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            if _rt_gm_f and '@' in _rt_gm_f:
                _gm_f_json['reply_to'] = [_rt_gm_f]
            try:
                async with _aiohttp.ClientSession() as _hgf:
                    _rgf = await _hgf.post('https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {_rk_gm_f}', 'Content-Type': 'application/json'},
                        json=_gm_f_json, timeout=_aiohttp.ClientTimeout(total=15))
                    _dgf = await _rgf.json()
                    if _rgf.status not in (200, 201):
                        _send_error = _dgf.get('message', str(_dgf))
                    else:
                        logger.info(f'[EMAIL_FOLLOWUP] Sent via server Resend (Gmail Reply-To: {_rt_gm_f}) to {to_clean}')
            except Exception as _egf:
                _send_error = f'Gmail server: {_egf}'

        elif _matched and _matched.get('type') == 'smtp':
            import smtplib as _smtplib2
            from email.mime.text import MIMEText as _MimeSmtp2
            from email.mime.multipart import MIMEMultipart as _MMsmtp2
            import asyncio as _aio_smtp2
            import ssl as _ssl2
            _sh2 = _matched['smtp_host']; _sp2 = _matched['smtp_port']
            _su2 = _matched['email_user']; _spw2 = _matched['email_pass'].replace(' ', '')

            def _do_smtp2():
                msg2 = _MMsmtp2('alternative')
                msg2['From'] = f"{sender_name} <{_su2}>"
                msg2['To'] = to_clean; msg2['Subject'] = subject
                msg2.attach(_MimeSmtp2(body, 'plain', 'utf-8'))
                try:
                    _fu_html = _build_email_html(_text_to_email_html(body), sender_name=sender_name)
                    msg2.attach(_MimeSmtp2(_fu_html, 'html', 'utf-8'))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                _ctx2 = _ssl2.create_default_context()
                with _smtplib2.SMTP(_sh2, _sp2, timeout=30) as s2:
                    s2.ehlo(); s2.starttls(context=_ctx2); s2.ehlo()
                    s2.login(_su2, _spw2); s2.sendmail(_su2, to_clean, msg2.as_string())

            _loop2 = _aio_smtp2.get_running_loop()
            try:
                await _aio_smtp2.wait_for(_loop2.run_in_executor(None, _do_smtp2), timeout=35.0)
                logger.info(f'[EMAIL_FOLLOWUP] Sent via SMTP ({_matched["label"]}) to {to_clean}')
            except Exception as _se2:
                _send_error = f'SMTP ({_matched["label"]}): {_se2}'

        elif _matched and _matched.get('type') == 'resend':
            _urk2 = _matched['resend_key']
            _uf2 = _matched.get('email_user') or sender_addr
            try:
                async with _aiohttp.ClientSession() as http2:
                    resp2 = await http2.post('https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {_urk2}', 'Content-Type': 'application/json'},
                        json={'from': f"{sender_name} <{_uf2}>", 'to': [to_clean], 'subject': subject,
                              'text': body,
                              'html': _build_email_html(_text_to_email_html(body), sender_name=sender_name),
                              'headers': {'List-Unsubscribe': f'<{_unsub_url}>'}},
                        timeout=_aiohttp.ClientTimeout(total=15))
                    rd2 = await resp2.json()
                    if resp2.status not in (200, 201):
                        _send_error = rd2.get('message', str(rd2))
            except Exception as _re2:
                _send_error = f'Resend: {_re2}'

        # Fallback: платформенный Resend
        if _matched is None or _send_error:
            from config import RESEND_API_KEY
            if not RESEND_API_KEY:
                return f" Ошибка отправки{': ' + _send_error if _send_error else ''}. Подключи почту в настройках агента."
            try:
                async with _aiohttp.ClientSession() as http:
                    _fbu_json = {'from': f"{sender_name} <outreach@asibiont.com>",
                                 'to': [to_clean], 'subject': subject, 'text': body,
                                 'headers': {'List-Unsubscribe': f'<{_unsub_url}>'}}
                    try:
                        _fbu_json['html'] = _build_email_html(_text_to_email_html(body), sender_name=sender_name)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    if sender_addr and '@' in sender_addr:
                        _fbu_json['reply_to'] = [sender_addr]
                    resp = await http.post('https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
                        json=_fbu_json,
                        timeout=_aiohttp.ClientTimeout(total=15))
                    resp_data = await resp.json()
                    if resp.status not in (200, 201):
                        err = resp_data.get('message', str(resp_data))
                        prev_err = f' (предыдущая попытка: {_send_error})' if _send_error else ''
                        return f" Ошибка Resend API: {err}{prev_err}"
            except Exception as e:
                return f" Ошибка отправки: {_send_error or str(e)}"

        # Anti-spam задержка (10 сек)
        import asyncio as _asyncio_delay
        await _asyncio_delay.sleep(10)

        # Обновляем запись
        outreach.follow_up_count = (outreach.follow_up_count or 0) + 1
        outreach.last_follow_up_at = dt.now(tz.utc)
        # Следующий follow-up через 5 дней (экспоненциальное замедление)
        next_gap_days = 3 + (outreach.follow_up_count * 2)
        outreach.next_follow_up_at = dt.now(tz.utc) + timedelta(days=next_gap_days)

        # Логируем в AgentActivityLog
        try:
            from models import AgentActivityLog
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='email',
                title=f"Follow-up #{outreach.follow_up_count} → {outreach.recipient_email}",
                content=f"{subject}\n\n{body}",
                target=outreach.recipient_email,
                status='sent',
                ref_id=outreach.id,
            )
            session.add(log_entry)
        except Exception as _log_err:
            logger.warning(f"[EMAIL_FOLLOWUP] Activity log error: {_log_err}")

        session.commit()

        return f" Follow-up #{outreach.follow_up_count} отправлен на {outreach.recipient_email}\nТема: {subject}"
    except Exception as e:
        logger.error(f"[EMAIL_FOLLOWUP] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════════════════
# NEGOTIATE BY EMAIL — Автономные переговоры для достижения цели
# ═══════════════════════════════════════════════════════════════════


async def negotiate_by_email(
    contact_email: str = None,
    contact_name: str = None,
    goal: str = None,
    opening_message: str = None,
    subject: str = None,
    sender_name: str = None,
    from_account: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Начать email-переговоры с конкретным человеком для достижения цели.

    Агент автономно ведёт переписку: отправляет первое письмо, отслеживает ответы
    (через якорь email_reply_received) и продолжает диалог до достижения цели.

    Примеры целей:
    - «Договориться о встрече на следующей неделе»
    - «Согласовать условия партнёрства»
    - «Уточнить детали заказа и получить подтверждение»
    - «Договориться об интервью»
    """
    if not session:
        session = Session()
        close_session = True
    try:
        from datetime import datetime as dt, timezone as tz

        if not contact_email or '@' not in contact_email:
            return " Укажи email контакта (contact_email)."
        if not goal:
            return " Укажи цель переговоров (goal)."
        if not opening_message:
            return " Нужен текст открывающего письма (opening_message)."

        # ── GUARD: плейсхолдеры в теле переговорного письма ──
        import re as _re_ph_neg
        _PH_RE_NEG = _re_ph_neg.compile(
            r'\[(?:вставьте|вставить|ваш[аеу]?|your|insert|add)\s+[^\]]{3,50}\]|'
            r'\[(?:ссылк[аеу]|link|url|zoom|meet)\s*(?:здесь|here|сюда)?\]',
            _re_ph_neg.IGNORECASE,
        )
        _ph_m_neg = _PH_RE_NEG.search((opening_message or '') + ' ' + (subject or ''))
        if _ph_m_neg:
            return (f"⛔ Письмо содержит плейсхолдер: «{_ph_m_neg.group()}». "
                    f"Замени на реальные данные или убери. Спроси у пользователя через send_message_to_user.")

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден."

        # ── GUARD: не начинать переговоры с отписавшимся контактом ──
        _neg_rcpt = contact_email.strip().lower()
        try:
            _ec_neg = session.query(EmailContact).filter_by(
                user_id=user.id, email=_neg_rcpt, status='unsubscribed'
            ).first()
            if _ec_neg:
                return f"⛔ {_neg_rcpt} отписался — переговоры заблокированы."
        except Exception as _e_neg:
            logger.debug("suppressed unsubscribed check in negotiate: %s", _e_neg)

        # ── GUARD: не начинать новые переговоры с тем, кто уже в активном диалоге ──
        # Если контакт уже ответил на письмо — нужно reply_to_outreach_email, не новая кампания
        try:
            _ec_active_neg = session.query(EmailContact).filter(
                EmailContact.user_id == user.id,
                EmailContact.email == _neg_rcpt,
                EmailContact.status.in_(['replied', 'interested']),
            ).first()
            if _ec_active_neg:
                _existing_replied_eon = session.query(EmailOutreach).filter(
                    EmailOutreach.user_id == user.id,
                    EmailOutreach.recipient_email == _neg_rcpt,
                    EmailOutreach.status == 'replied',
                    EmailOutreach.ai_reply_sent_at.is_(None),
                ).order_by(EmailOutreach.reply_at.desc()).first()
                if _existing_replied_eon:
                    return (
                        f"⛔ {contact_email} уже ответил на письмо #{_existing_replied_eon.id} "
                        f"(«{(_existing_replied_eon.subject or '')[:50]}»). "
                        f"Используй reply_to_outreach_email(outreach_id={_existing_replied_eon.id}, reply_body=...) "
                        f"вместо negotiate_by_email."
                    )
                else:
                    return (
                        f"⛔ {contact_email} уже в активном диалоге (статус: {_ec_active_neg.status}). "
                        f"Вызови check_emails чтобы увидеть входящие, и reply_to_outreach_email чтобы ответить. "
                        f"Новые переговоры не нужны — переписка уже идёт."
                    )
        except Exception as _e_active_neg:
            logger.debug("suppressed active contact check in negotiate: %s", _e_active_neg)

        # ── GUARD: анти-спам — не более 3 писем одному контакту за 7 дней ──
        try:
            from datetime import timedelta as _td_neg
            _neg_sent_count = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.recipient_email == _neg_rcpt,
                EmailOutreach.sent_at >= dt.now(tz.utc) - _td_neg(days=7),
                EmailOutreach.status.in_(['sent', 'delivered', 'replied', 'opened']),
            ).count()
            if _neg_sent_count >= 3:
                return (
                    f"🛑 Стоп-спам: {contact_email} уже получил {_neg_sent_count} писем за последние 7 дней. "
                    f"Подожди ответа или смени контакт."
                )
        except Exception as _e_spam_neg:
            logger.debug("suppressed spam check in negotiate: %s", _e_spam_neg)

        # MX-проверка
        mx_valid, mx_err = _validate_email_domain(contact_email.strip().lower())
        if not mx_valid:
            return f" {mx_err}"

        # ── Определяем имя и адрес отправителя ──────────────────────────────
        _integrations = _get_user_email_integrations(user, session)
        _chosen = None
        if _integrations:
            if from_account:
                _fa = from_account.strip().lower()
                for _i in _integrations:
                    if _fa in _i.get('email_user', '').lower() or _fa in _i.get('label', '').lower():
                        _chosen = _i
                        break
                if not _chosen:
                    _list = ', '.join(f"{i['label']} ({i['email_user']})" for i in _integrations)
                    return f" Аккаунт '{from_account}' не найден. Доступные: {_list}"
            else:
                _chosen = _integrations[0]

        if not _chosen:
            return (
                " Не настроена почтовая интеграция. Добавь в ключи агента:\n"
                "• Gmail: GMAIL_USER=you@gmail.com и GMAIL_PASS=xxxx xxxx xxxx xxxx\n"
                "• Яндекс: YANDEX_USER=you@yandex.ru и YANDEX_PASS=...\n"
                "• Mail.ru: MAILRU_USER=you@mail.ru и MAILRU_PASS=...\n"
                "• Resend: RESEND_API_KEY=re_... и RESEND_FROM=noreply@домен.com"
            )

        _sender_addr = _chosen['email_user']
        _sender_name = sender_name or user.first_name or user.username or 'Team'
        _subject = subject or f"Regarding: {goal[:60]}"

        # ── Создаём мини-кампанию для отслеживания переговоров ──────────────
        campaign = EmailCampaign(
            user_id=user.id,
            name=f"Переговоры: {goal[:80]}",
            goal=goal,
            target_audience=f"{contact_name or contact_email}",
            offer=goal,
            tone='professional',
            sender_name=_sender_name,
            sender_email=_sender_addr,
            max_emails=1,           # один контакт
            daily_limit=5,          # follow-ups разрешены
            status='active',
            max_follow_ups=3,
        )
        session.add(campaign)
        session.flush()  # получаем campaign.id

        # ── Сохраняем контакт в переговорную кампанию ───────────────────────
        outreach = EmailOutreach(
            campaign_id=campaign.id,
            user_id=user.id,
            recipient_email=contact_email.strip().lower(),
            recipient_name=contact_name,
            subject=_subject,
            body=opening_message,
            status='draft',
        )
        session.add(outreach)
        session.flush()

        # ── Отправляем первое письмо ─────────────────────────────────────────
        # Повторно используем логику из send_email (без дублирования кода)
        send_result = await send_email(
            to=contact_email,
            subject=_subject,
            body=opening_message,
            sender_name=_sender_name,
            from_account=_sender_addr,
            user_id=user_id,
            session=session,
            close_session=False,
        )

        if '' in (send_result or ''):
            # Помечаем outreach как отправленный
            outreach.status = 'sent'
            outreach.sent_at = dt.now(tz.utc)
            outreach.next_follow_up_at = dt.now(tz.utc) + timedelta(days=3)
            campaign.emails_sent = 1

            # Логируем в AgentActivityLog
            try:
                from models import AgentActivityLog
                log_entry = AgentActivityLog(
                    user_id=user.id,
                    activity_type='email',
                    title=f"Переговоры → {contact_email}",
                    content=f"Цель: {goal}\n\nТема: {_subject}\n\n{opening_message[:400]}",
                    target=contact_email,
                    status='sent',
                    ref_id=outreach.id,
                )
                session.add(log_entry)
            except Exception as _le:
                logger.warning(f"[NEGOTIATE_EMAIL] Activity log error: {_le}")

            # Обновляем EmailContact → 'contacted' (контакт получил первое письмо)
            # Если уже был 'replied'/'interested' — guards выше заблокировали бы этот вызов.
            try:
                from models import EmailContact as _EC_neg
                _ec_neg_upd = session.query(_EC_neg).filter_by(
                    user_id=user.id, email=contact_email.strip().lower()
                ).first()
                if _ec_neg_upd:
                    if _ec_neg_upd.status in ('new', None):
                        _ec_neg_upd.status = 'contacted'
                    _ec_neg_upd.last_contacted_at = dt.now(tz.utc)
                else:
                    session.add(_EC_neg(
                        user_id=user.id,
                        email=contact_email.strip().lower(),
                        name=contact_name or '',
                        source='negotiate',
                        status='contacted',
                        notes=f'Переговоры: {goal[:80]}',
                        last_contacted_at=dt.now(tz.utc),
                    ))
            except Exception as _le_ec:
                logger.debug("[NEGOTIATE_EMAIL] EmailContact update: %s", _le_ec)

            session.commit()
            return (
                f" Переговоры начаты!\n"
                f" Кому: {contact_email}{' (' + contact_name + ')' if contact_name else ''}\n"
                f" Цель: {goal}\n"
                f" Тема: {_subject}\n"
                f" Кампания #{campaign.id} (активна — агент отслеживает ответы)\n\n"
                f"Когда {contact_email} ответит — агент автоматически продолжит диалог "
                f"через якорь email_reply_received."
            )
        else:
            # Отправка не удалась — удаляем пустую кампанию
            session.rollback()
            return f" Не удалось отправить первое письмо: {send_result}"

    except Exception as e:
        logger.error(f"[NEGOTIATE_EMAIL] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════════════════
# GENERIC EMAIL — Отправка одиночных писем через Resend API или SMTP
# ═══════════════════════════════════════════════════════════════════


async def _send_via_gmail_oauth(
    to_email: str, subject: str, body: str, sender_name: str,
    token_data: dict, user_obj, session_obj
) -> tuple:
    """Отправить письмо через Gmail API (HTTPS, обходит блокировку SMTP на Railway).
    При 401 автоматически обновляет access_token через refresh_token.
    Возвращает (success: bool, error_str: str).
    """
    import base64 as _b64_go, json as _jsn_go
    from email.mime.text import MIMEText as _MT_go
    from email.mime.multipart import MIMEMultipart as _MM_go
    import aiohttp as _ah_go

    _go_email = token_data.get('email', '')
    _go_access = token_data.get('access_token', '')
    _go_refresh = token_data.get('refresh_token', '')

    msg_go = _MM_go()
    msg_go['From'] = f"{sender_name} <{_go_email}>"
    msg_go['To'] = to_email
    msg_go['Subject'] = subject
    msg_go.attach(_MT_go(body, 'plain', 'utf-8'))
    _raw_go = _b64_go.urlsafe_b64encode(msg_go.as_bytes()).decode()

    async def _gmail_post(token):
        async with _ah_go.ClientSession() as _hh:
            _rr = await _hh.post(
                'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                json={'raw': _raw_go},
                timeout=_ah_go.ClientTimeout(total=20),
            )
            return _rr.status, await _rr.json()

    _gs, _gd = await _gmail_post(_go_access)
    if _gs in (200, 201):
        return True, ''

    if _gs == 401 and _go_refresh:
        # Обновляем access_token
        try:
            from config import GOOGLE_CLIENT_ID as _GCI_r, GOOGLE_CLIENT_SECRET as _GCS_r
            async with _ah_go.ClientSession() as _hh2:
                _tr = await _hh2.post(
                    'https://oauth2.googleapis.com/token',
                    data={
                        'client_id': _GCI_r, 'client_secret': _GCS_r,
                        'refresh_token': _go_refresh, 'grant_type': 'refresh_token',
                    },
                    timeout=_ah_go.ClientTimeout(total=10),
                )
                _td = await _tr.json()
            if 'error' in _td:
                return False, f"Gmail токен истёк, переподключи Gmail в профиле: {_td.get('error_description', _td.get('error'))}"
            _new_access = _td['access_token']
            user_obj.google_oauth_token = encrypt_token(_jsn_go.dumps({**token_data, 'access_token': _new_access}))
            try:
                session_obj.commit()
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            _gs2, _gd2 = await _gmail_post(_new_access)
            if _gs2 in (200, 201):
                return True, ''
            return False, (_gd2.get('error') or {}).get('message', str(_gd2))
        except Exception as _ref_e:
            return False, f'Ошибка обновления Gmail токена: {_ref_e}'

    return False, (_gd.get('error') or {}).get('message', str(_gd))


def _get_user_email_integrations(user, session) -> list:
    """Возвращает список почтовых интеграций пользователя.

    Каждый элемент имеет поле 'type':
      'gmail_oauth' — {label, email_user, token_data}  ← приоритет #1, HTTPS
      'smtp'        — {label, email_user, email_pass, smtp_host, smtp_port, agent_name, agent_id}
      'resend'      — {label, email_user, resend_key, agent_name, agent_id}
    """
    try:
        results = []
        seen_emails: set = set()
        seen_resend: set = set()

        # Gmail OAuth2 — приоритет #1, отправка через HTTPS Gmail API (не SMTP)
        if getattr(user, 'google_oauth_token', None):
            import json as _jsn_go_i
            try:
                _go_data_i = _jsn_go_i.loads(decrypt_token(user.google_oauth_token))
                _go_email_i = _go_data_i.get('email', '')
                if _go_email_i and _go_email_i not in seen_emails:
                    seen_emails.add(_go_email_i)
                    results.append({
                        'type': 'gmail_oauth',
                        'label': 'Gmail OAuth',
                        'email_user': _go_email_i,
                        'token_data': _go_data_i,
                    })
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        from models import UserAgent as _UA
        agents = session.query(_UA).filter(
            _UA.author_id == user.id,
            _UA.status != 'disabled',
            _UA.user_api_keys != None,
            _UA.user_api_keys != '',
        ).all()
        # SMTP-конфиги для поддерживаемых почтовых сервисов
        # Порт 587 + STARTTLS: Railway/Render/Heroku не блокируют его.
        # Порт 465 (SMTP_SSL) заблокирован на большинстве хостингов.
        # Gmail SMTP заблокирован Railway (порт 587) — регистрируем как gmail_server
        # (отправка через платформенный Resend + Reply-To на gmail пользователя)
        # Яндекс и Mail.ru — работают через SMTP напрямую
        _SMTP_SVC = [
            ('YANDEX', 'smtp.yandex.ru',  587, 'Яндекс Почта'),
            ('MAILRU', 'smtp.mail.ru',    587, 'Mail.ru'),
        ]
        for agent in agents:
            env: dict = {}
            for line in (agent.user_api_keys or '').splitlines():
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, _, v = line.partition('=')
                    env[k.strip().upper()] = v.strip()
            # Gmail — через серверный Resend + Reply-To (SMTP заблокирован на Railway)
            _gmail_u = env.get('GMAIL_USER', '')
            _gmail_p = env.get('GMAIL_PASS', '')
            if _gmail_u and _gmail_u not in seen_emails:
                seen_emails.add(_gmail_u)
                results.append({
                    'type': 'gmail_server',
                    'label': 'Gmail',
                    'email_user': _gmail_u,
                    'email_pass': _gmail_p,  # пароль приложения для IMAP
                    'reply_to': _gmail_u,
                    'agent_name': agent.name or 'Gmail',
                    'agent_id': agent.id,
                })
            # SMTP-сервисы (Яндекс, Mail.ru)
            for svc_key, smtp_host, smtp_port, label in _SMTP_SVC:
                eu = env.get(f'{svc_key}_USER', '')
                ep = env.get(f'{svc_key}_PASS', '')
                if eu and ep and eu not in seen_emails:
                    seen_emails.add(eu)
                    results.append({
                        'type': 'smtp',
                        'label': label,
                        'email_user': eu,
                        'email_pass': ep,
                        'smtp_host': smtp_host,
                        'smtp_port': smtp_port,
                        'agent_name': agent.name or label,
                        'agent_id': agent.id,
                    })
            # Личный Resend API ключ
            rk = env.get('RESEND_API_KEY', '')
            re_from = env.get('RESEND_FROM', env.get('SENDER_EMAIL', env.get('FROM_EMAIL', '')))
            if rk and rk not in seen_resend:
                seen_resend.add(rk)
                results.append({
                    'type': 'resend',
                    'label': 'Resend',
                    'email_user': re_from,  # пустая строка если не задан — проверим позже
                    'resend_key': rk,
                    'agent_name': agent.name or 'Resend',
                    'agent_id': agent.id,
                })
        return results
    except Exception as _e:
        logger.warning(f'[EMAIL_INTEGRATIONS] {_e}')
        return []


async def _send_via_gmail_api(
    token_data: dict,
    to: str,
    subject: str,
    body: str,
    sender_name: str,
    user,
    session,
) -> tuple:
    """Отправить письмо напрямую через Gmail API v1.

    Автоматически рефрешит access_token при истечении (401).
    Возвращает: (success: bool, result: str)
      success=True  → result = gmail_email пользователя
      success=False → result = текст ошибки
    """
    import base64 as _b64
    import json as _jsn_gapi
    import datetime as _dt_gapi
    from email.mime.text import MIMEText as _MimeGapi
    import aiohttp as _ah_gapi
    from config import GOOGLE_CLIENT_ID as _GCI_gapi, GOOGLE_CLIENT_SECRET as _GCS_gapi

    gmail_email = token_data.get('email', '')
    access_token = token_data.get('access_token', '')
    refresh_token = token_data.get('refresh_token', '')

    async def _refresh():
        nonlocal access_token
        if not refresh_token or not _GCI_gapi or not _GCS_gapi:
            return False
        try:
            async with _ah_gapi.ClientSession() as _hrf:
                _r = await _hrf.post(
                    'https://oauth2.googleapis.com/token',
                    data={
                        'client_id': _GCI_gapi,
                        'client_secret': _GCS_gapi,
                        'refresh_token': refresh_token,
                        'grant_type': 'refresh_token',
                    },
                    timeout=_ah_gapi.ClientTimeout(total=10),
                )
                _rd = await _r.json()
                if 'access_token' in _rd:
                    access_token = _rd['access_token']
                    new_tok = dict(token_data)
                    new_tok['access_token'] = access_token
                    new_tok['saved_at'] = _dt_gapi.datetime.utcnow().isoformat()
                    user.google_oauth_token = encrypt_token(_jsn_gapi.dumps(new_tok))
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                    return True
        except Exception as _re_err:
            logger.warning(f'[GMAIL_API] Token refresh error: {_re_err}')
        return False

    async def _do_send():
        from email.mime.multipart import MIMEMultipart as _MimeMp
        # Multipart alternative: plain + HTML
        msg = _MimeMp('alternative')
        msg['From'] = f"{sender_name} <{gmail_email}>"
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(_MimeGapi(body, 'plain', 'utf-8'))
        try:
            _body_html = _text_to_email_html(body)
            _full_html = _build_email_html(_body_html, sender_name=sender_name)
            from email.mime.text import MIMEText as _MHTml
            msg.attach(_MHTml(_full_html, 'html', 'utf-8'))
        except Exception:
            pass  # plain-text fallback
        raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
        async with _ah_gapi.ClientSession() as _hgm:
            _resp = await _hgm.post(
                'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                },
                json={'raw': raw},
                timeout=_ah_gapi.ClientTimeout(total=20),
            )
            return _resp.status, await _resp.json()

    try:
        status, data = await _do_send()
        if status == 401:
            refreshed = await _refresh()
            if not refreshed:
                return False, "Gmail OAuth токен истёк. Переподключи Gmail в настройках агента."
            status, data = await _do_send()
        if status in (200, 201):
            logger.info(f'[GMAIL_API] Sent from {gmail_email} to {to}')
            return True, gmail_email
        err = data.get('error', {}).get('message', str(data))
        return False, f"Gmail API error {status}: {err}"
    except Exception as _ge:
        return False, f"Gmail API exception: {_ge}"


# ══════════════════════════════════════════════════════════════════════════════
# check_emails — чтение входящих писем из почты пользователя
# ══════════════════════════════════════════════════════════════════════════════

async def check_emails(
    limit: int = 5,
    from_account: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Проверить входящие письма из подключённой почты пользователя (Gmail/Яндекс/Mail.ru)."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        _integrations = _get_user_email_integrations(user, session)
        if not _integrations:
            return ("Почта не подключена. Чтобы я мог проверять входящие письма, "
                    "подключи почтовый ящик в настройках дашборда: Профиль → Настройки агента → API-ключи → "
                    "добавь GMAIL_USER (для Gmail OAuth) или YANDEX_USER/YANDEX_PASS (для Яндекс/Mail.ru).")

        # Выбираем интеграцию
        chosen = None
        if from_account:
            _fa = from_account.strip().lower()
            for _intg in _integrations:
                if _fa in _intg['email_user'].lower() or _fa in _intg['label'].lower():
                    chosen = _intg
                    break
        if not chosen:
            chosen = _integrations[0]

        limit = max(1, min(limit, 20))

        # Загружаем уже-известные контакты для фильтрации дублей
        _my_email = chosen.get('email_user', '').lower()
        # Собираем ВСЕ собственные email-адреса (Gmail + Resend sender + все интеграции)
        # чтобы не показывать копии собственных исходящих как "входящие"
        _my_emails: set = {_my_email} if _my_email else set()
        for _intg in _integrations:
            _ie = _intg.get('email_user', '').lower()
            if _ie:
                _my_emails.add(_ie)
        # Resend sender — платформенный outreach@...
        try:
            import os as _os_me
            _resend_from = _os_me.getenv('RESEND_FROM_EMAIL', 'outreach@asibiont.com').lower()
            _my_emails.add(_resend_from)
        except Exception:
            _my_emails.add('outreach@asibiont.com')
        # Личный Resend-адрес пользователя (из user_api_keys агентов)
        try:
            from models import UserAgent as _UA_me
            _ua_rows = session.query(_UA_me).filter_by(author_id=user.id).all()
            for _ua_r in _ua_rows:
                _keys_str = getattr(_ua_r, 'user_api_keys', '') or ''
                if 'RESEND_FROM=' in _keys_str:
                    import re as _re_me
                    _m_from = _re_me.search(r'RESEND_FROM=([^\s,;]+)', _keys_str)
                    if _m_from:
                        _my_emails.add(_m_from.group(1).strip().lower())
        except Exception as _e_me:
            logger.debug("suppressed own-emails: %s", _e_me)
        _my_emails.discard('')
        _known_emails: set = set()
        _registered_emails: set = set()
        try:
            from models import EmailContact as _EC_ce
            _known_emails = {r.email.lower() for r in session.query(_EC_ce.email).filter_by(user_id=user.id).all() if r.email}
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        try:
            from models import User as _U_re
            _registered_emails = {r.email.lower() for r in session.query(_U_re.email).filter(_U_re.email.isnot(None)).all() if r.email}
        except Exception as _e_re:
            logger.debug("suppressed registered_emails: %s", _e_re)

        # Словарь outreach: кто из наших агентов писал каждому контакту
        _outreach_map: dict = {}
        try:
            from models import EmailOutreach as _EO_ce3
            _eo_rows = session.query(_EO_ce3).filter(
                _EO_ce3.user_id == user.id,
                _EO_ce3.status.in_(['sent', 'delivered', 'opened', 'replied']),
            ).order_by(_EO_ce3.sent_at.desc()).limit(500).all()
            for _eo_r in _eo_rows:
                if _eo_r.recipient_email:
                    _eo_key = _eo_r.recipient_email.lower()
                    if _eo_key not in _outreach_map:  # берём последний по sent_at
                        _outreach_map[_eo_key] = {
                            'sent_by_agent': _eo_r.sent_by_agent,
                            'outreach_id': _eo_r.id,
                            'sent_at': _eo_r.sent_at,
                            'subject': _eo_r.subject or '',
                        }
        except Exception as _e_om:
            logger.debug("outreach_map build: %s", _e_om)

        if chosen['type'] == 'gmail_oauth':
            result = await _check_emails_gmail_api(chosen['token_data'], limit, user, session, _known_emails, _my_emails, _outreach_map, _registered_emails)
        elif chosen['type'] in ('smtp', 'gmail_server'):
            result = await _check_emails_imap(chosen, limit, _known_emails, _my_emails, _outreach_map, _registered_emails)
        elif chosen['type'] == 'resend':
            return "Resend — сервис только для отправки, входящие не поддерживаются."
        else:
            return f"Тип интеграции '{chosen['type']}' не поддерживает чтение входящих."

        # ── Дедупликация: фильтруем письма, о которых агент уже сообщал ──
        _no_new_kw_pre = ('нет новых писем', 'входящих писем нет', 'нет писем', 'no new', 'нет входящих')
        if result and not any(kw in result.lower() for kw in _no_new_kw_pre):
            try:
                import hashlib as _hl_dup
                import json as _json_dup
                from models import AgentActivityLog as _AAL_dup
                # Загружаем ранее виденные fingerprints (за 48ч)
                _seen_fp: set = set()
                try:
                    _seen_rows = session.query(_AAL_dup).filter(
                        _AAL_dup.user_id == user.id,
                        _AAL_dup.activity_type == 'seen_inbox_emails',
                        _AAL_dup.created_at >= datetime.utcnow() - timedelta(hours=48),
                    ).all()
                    for _sr in _seen_rows:
                        try:
                            _fps = _json_dup.loads(_sr.content or '[]')
                            _seen_fp.update(_fps)
                        except Exception:
                            pass
                except Exception as _e_seen:
                    logger.debug('[CHECK_EMAILS] seen_fp load: %s', _e_seen)

                # Разбиваем result на блоки по "---"
                import re as _re_dup
                _header_match = _re_dup.match(r'^(.*?\d+\s*писем[^:]*:?\s*(?:\n[^\n]*примечание[^\n]*)?)\n\n', result, _re_dup.DOTALL | _re_dup.IGNORECASE)
                _header_part = _header_match.group(1) if _header_match else ''
                _body_part = result[len(_header_part):].strip() if _header_part else result
                _blocks = _re_dup.split(r'\n---\n', _body_part)

                _new_blocks = []
                _new_fps = []
                _skipped_count = 0
                for _blk in _blocks:
                    if not _blk.strip():
                        continue
                    # Fingerprint = hash(from + subject + date_prefix)
                    _fp_from = _re_dup.search(r'От:\s*(?:\[email-контакт[^\]]*\]\s*)?(.+)', _blk)
                    _fp_subj = _re_dup.search(r'Тема:\s*(.+)', _blk)
                    _fp_date = _re_dup.search(r'Дата:\s*(.+)', _blk)
                    _fp_str = (
                        (_fp_from.group(1).strip() if _fp_from else '') + '|' +
                        (_fp_subj.group(1).strip() if _fp_subj else '') + '|' +
                        (_fp_date.group(1).strip()[:20] if _fp_date else '')
                    )
                    _fp_hash = _hl_dup.md5(_fp_str.encode('utf-8', 'ignore')).hexdigest()[:16]

                    if _fp_hash in _seen_fp:
                        _skipped_count += 1
                        continue
                    _new_blocks.append(_blk)
                    _new_fps.append(_fp_hash)

                # Сохраняем fingerprints новых писем — ОТДЕЛЬНАЯ сессия чтобы не зависеть от
                # состояния основной (она может быть в mid-transaction или dirty).
                if _new_fps:
                    try:
                        from models import Session as _SessFP
                        _fp_sess = _SessFP()
                        try:
                            _aal_seen = _AAL_dup(
                                user_id=user.id,
                                activity_type='seen_inbox_emails',
                                title=f'seen {len(_new_fps)} emails',
                                content=_json_dup.dumps(_new_fps),
                                target='email_inbox',
                                status='completed',
                            )
                            _fp_sess.add(_aal_seen)
                            _fp_sess.commit()
                            logger.info('[CHECK_EMAILS] Saved %d inbox fingerprints', len(_new_fps))
                        except Exception as _e_save_fp:
                            logger.warning('[CHECK_EMAILS] save seen_fp FAILED: %s', _e_save_fp)
                            try:
                                _fp_sess.rollback()
                            except Exception:
                                pass
                        finally:
                            try:
                                _fp_sess.close()
                            except Exception:
                                pass
                    except Exception as _e_fp_outer:
                        logger.warning('[CHECK_EMAILS] fingerprint session setup failed: %s', _e_fp_outer)

                if _skipped_count > 0:
                    logger.info('[CHECK_EMAILS] Dedup: skipped %d already-seen emails, %d new', _skipped_count, len(_new_blocks))

                if not _new_blocks:
                    result = f"Нет новых писем (все {_skipped_count} уже были обработаны ранее)."
                elif _skipped_count > 0:
                    _email_account = _re_dup.search(r'\(([^,]+),', _header_part)
                    _acct = _email_account.group(1) if _email_account else chosen.get('email_user', '')
                    result = (
                        f"Новые входящие ({_acct}, {len(_new_blocks)} новых, {_skipped_count} уже обработано):\n\n"
                        + "\n---\n".join(_new_blocks)
                    )
                # else: result stays as-is (all emails are new)
            except Exception as _e_dedup:
                logger.debug('[CHECK_EMAILS] dedup error (skipping): %s', _e_dedup)

        # Автоматически сохраняем/обновляем контакты из входящих в EmailContact
        _no_new_keywords = ('нет новых писем', 'входящих писем нет', 'нет писем', 'no new', 'нет входящих')
        if result and not any(kw in result.lower() for kw in _no_new_keywords):
            import re as _re_ce
            import datetime as _dt_ce
            from models import EmailContact as _EC_ce2, EmailOutreach as _EO_ce2
            # Фильтруем нотификационные / авто-адреса — они не являются реальными контактами
            _NOREPLY_PATS = (
                'no-reply', 'noreply', 'do-not-reply', 'donotreply',
                'notification', 'notifications', 'mailer-daemon', 'postmaster',
                'bounce@', 'bounces@', 'automated@', 'reply-to@',
                '@email.github', '@notifications.github', '@github.com',
                'support@', 'info@', 'admin@', 'hello@', 'team@',
                'feedback@', 'newsletter@', 'news@', 'updates@',
            )
            def _is_noreply(em: str) -> bool:
                el = em.lower()
                return any(p in el for p in _NOREPLY_PATS)

            _found_em = set(e.lower() for e in _re_ce.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', result, _re_ce.IGNORECASE))
            _found_em -= _my_emails
            _found_em = {em for em in _found_em if not _is_noreply(em)}
            _new_auto = _found_em - _known_emails
            # 1) Новые контакты → создаём с status=replied
            _truly_new_contacts = 0  # Счётчик реально новых контактов (не повторных)
            for _new_em in list(_new_auto)[:5]:
                try:
                    _existing_ec = session.query(_EC_ce2).filter_by(user_id=user.id, email=_new_em).first()
                    if not _existing_ec:
                        _ec_new = _EC_ce2(
                            user_id=user.id,
                            email=_new_em,
                            source='imap_reply',
                            status='replied',
                            notes='Автоматически найден во входящих письмах',
                            last_contacted_at=_dt_ce.datetime.utcnow(),
                        )
                        session.add(_ec_new)
                        session.commit()
                        _known_emails.add(_new_em)
                        _truly_new_contacts += 1
                        logger.info(f'[CHECK_EMAILS] Auto-saved contact: {_new_em} for user {user.id}')
                except Exception as _e_save:
                    logger.debug(f'[CHECK_EMAILS] auto-save contact failed: {_e_save}')
                    try:
                        session.rollback()
                    except Exception:
                        pass
            # Парсим result чтобы вытащить snippet/preview для каждого email
            _reply_snippets: dict = {}
            try:
                import re as _re_snip
                # Разбиваем по блокам "---", каждый блок = одно письмо
                _blocks = re.split(r'\n---\n', result)
                for _blk in _blocks:
                    _em_in_blk = _re_snip.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', _blk, _re_snip.IGNORECASE)
                    _preview_m = _re_snip.search(r'[Пп]ревью:\s*(.+)', _blk, _re_snip.DOTALL)
                    if _em_in_blk and _preview_m:
                        _snip_raw = _preview_m.group(1).strip()[:3000]
                        # Отрезаем служебные аннотации outreach (не должны попадать в reply_text в БД)
                        _snip_raw = _re_snip.split(r'\n?⚡ ИСХОДЯЩИЙ OUTREACH', _snip_raw, maxsplit=1)[0]
                        # Очищаем MIME boundary/header артефакты (защита от старых raw-ответов IMAP)
                        _snip_raw = _re_snip.sub(r'--[A-Za-z0-9_\-]{6,}[^\n]*\n?', '', _snip_raw)
                        _snip_raw = _re_snip.sub(r'Content-[A-Za-z\-]+:[^\n]*\n?', '', _snip_raw)
                        _snip_text = _snip_raw.strip()[:3000]
                        if not _snip_text:
                            continue
                        for _em_raw in _em_in_blk:
                            _reply_snippets[_em_raw.lower()] = _snip_text  # до 3000 символов
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            # 2) Известные контакты, которые ответили → обновляем статус на replied + сохраняем текст
            _replied_known = _found_em & _known_emails
            _truly_new_replies = 0  # Счётчик контактов, чей статус ИЗМЕНИЛСЯ на replied впервые
            for _rep_em in list(_replied_known)[:10]:
                try:
                    _rep_snippet = _reply_snippets.get(_rep_em, '')
                    _now_ce = _dt_ce.datetime.utcnow()
                    _ec_existing = session.query(_EC_ce2).filter_by(user_id=user.id, email=_rep_em).first()
                    if _ec_existing and _ec_existing.status in ('contacted', 'new', None):
                        _ec_existing.status = 'replied'
                        _ec_existing.last_contacted_at = _now_ce
                        _truly_new_replies += 1  # Только реальное ПЕРВОЕ изменение статуса
                        session.commit()
                        logger.info(f'[CHECK_EMAILS] Updated contact status to replied: {_rep_em}')
                    # Также обновляем EmailOutreach если есть — сохраняем reply_text и reply_at
                    _eo = session.query(_EO_ce2).filter_by(
                        user_id=user.id, recipient_email=_rep_em
                    ).filter(_EO_ce2.status.in_(['sent', 'delivered', 'opened'])).first()
                    if _eo:
                        _was_replied_ce = (_eo.status == 'replied')
                        _eo.status = 'replied'
                        # Обновляем reply_text если новый текст лучше старого (нет старого или старый короче)
                        if _rep_snippet and (not _eo.reply_text or len(_rep_snippet) > len(_eo.reply_text or '')):
                            _eo.reply_text = _rep_snippet
                        if not _eo.reply_at:
                            _eo.reply_at = _now_ce
                        # Outcome Feedback Loop (#1): обновляем счётчик ответов
                        try:
                            _eo.reply_count = (_eo.reply_count or 0) + 1
                            # Рассчитываем задержку ответа в часах
                            if _eo.sent_at and _eo.reply_at:
                                _delay_h = (_eo.reply_at - _eo.sent_at).total_seconds() / 3600.0
                                # engagement_rating: быстрый ответ (<24ч) = высокий рейтинг
                                _eo.engagement_rating = max(0.1, min(1.0, 1.0 - _delay_h / 96.0))
                        except Exception as _e_rc:
                            logger.debug('[CHECK_EMAILS] reply_count update: %s', _e_rc)
                        # Инкрементируем счётчик ответов на кампании (только если статус изменился)
                        if not _was_replied_ce and _eo.campaign_id:
                            try:
                                from models import EmailCampaign as _EC_camp_ce
                                _camp_ce = session.query(_EC_camp_ce).filter_by(id=_eo.campaign_id).first()
                                if _camp_ce:
                                    _camp_ce.emails_replied = (_camp_ce.emails_replied or 0) + 1
                            except Exception as _e_camp:
                                logger.debug(f'[CHECK_EMAILS] campaign replies counter update failed: {_e_camp}')
                        session.commit()
                        logger.info(f'[CHECK_EMAILS] Updated EmailOutreach status=replied, reply_text saved: {_rep_em}')

                        # Contact Preference Memory (#3): обновляем предпочтения на основе ответа
                        try:
                            from models import EmailContactPreference as _ECP_ce
                            _pref = session.query(_ECP_ce).filter_by(
                                user_id=user.id, contact_email=_rep_em
                            ).first()
                            if not _pref:
                                _pref = _ECP_ce(user_id=user.id, contact_email=_rep_em)
                                session.add(_pref)
                            _pref.emails_received = (_pref.emails_received or 0)
                            _pref.emails_replied = (_pref.emails_replied or 0) + 1
                            _pref.last_reply_at = _now_ce
                            _pref.typical_reply_hour = _now_ce.hour
                            # Определяем предпочтения по телу письма которое вызвало ответ
                            if _eo.body_length:
                                if _eo.body_length < 300:
                                    _pref.preferred_length = 'short'
                                elif _eo.body_length < 600:
                                    _pref.preferred_length = 'medium'
                                else:
                                    _pref.preferred_length = 'long'
                            if _eo.tone_type:
                                _pref.preferred_tone = _eo.tone_type
                            # Определяем язык ответа контакта и сохраняем
                            if _rep_snippet and len(_rep_snippet) > 20:
                                import unicodedata as _ud_cl
                                _cl_scripts = {}
                                for _ch_cl in _rep_snippet:
                                    if _ch_cl.isalpha():
                                        try:
                                            _sn = _ud_cl.name(_ch_cl, '').split()[0]
                                        except ValueError:
                                            continue
                                        _cl_scripts[_sn] = _cl_scripts.get(_sn, 0) + 1
                                _cl_top = max(_cl_scripts, key=_cl_scripts.get) if _cl_scripts else None
                                if _cl_top == 'CYRILLIC':
                                    _pref.preferred_language = 'ru'
                                elif _cl_top == 'LATIN':
                                    _pref.preferred_language = 'en'
                            _pref.updated_at = _now_ce
                            session.commit()
                            logger.info(f'[CHECK_EMAILS] Updated ContactPreference for {_rep_em}')
                        except Exception as _e_pref:
                            logger.debug('[CHECK_EMAILS] ContactPreference update: %s', _e_pref)
                            try:
                                session.rollback()
                            except Exception:
                                pass
                    else:
                        # Если контакт уже replied/unsubscribed (напр. через webhook) — обновить reply_text если snippet лучше
                        _eo_any = session.query(_EO_ce2).filter_by(
                            user_id=user.id, recipient_email=_rep_em,
                        ).filter(_EO_ce2.status.in_(['replied', 'unsubscribed'])).order_by(
                            _EO_ce2.reply_at.desc()
                        ).first()
                        if _eo_any and _rep_snippet and (not _eo_any.reply_text or len(_rep_snippet) > len(_eo_any.reply_text or '')):
                            _eo_any.reply_text = _rep_snippet
                            if not _eo_any.reply_at:
                                _eo_any.reply_at = _now_ce
                            session.commit()
                            logger.info(f'[CHECK_EMAILS] Saved/updated reply_text for replied: {_rep_em}')
                except Exception as _e_upd:
                    logger.debug(f'[CHECK_EMAILS] update replied status failed: {_e_upd}')
                    try:
                        session.rollback()
                    except Exception:
                        pass

            # ── Извлекаем предпочтения по общению из входящих писем ─────────
            # Сканируем snippets ответных писем: если контакт написал что хочет
            # общаться на определённом языке или в определённом стиле — сохраняем
            # это в EmailContact.notes и показываем агенту при ответе.
            #
            # ── AUTO-UNSUBSCRIBE: распознаём отказы в ответах ──
            # Если контакт просит не писать → status='unsubscribed', блокируем follow-up и новые письма
            _unsubscribed_emails: list = []
            if _reply_snippets:
                import re as _re_unsub_ce
                _UNSUB_RE = _re_unsub_ce.compile(
                    r'\bunsubscribe\b|'
                    r'\bopt[\s\-]?out\b|'
                    r'\bstop\s+(?:emailing|contacting|writing|sending)\b|'
                    r'\bremove\s+(?:me|my\s+email)\b|'
                    r'\bdo\s+not\s+(?:contact|email|write|send)\b|'
                    r'\bdon\'?t\s+(?:contact|email|write|send)\b|'
                    r'\bnot\s+interested\b|'
                    r'\bno\s+thanks?\b|'
                    r'\bleave\s+me\s+alone\b|'
                    r'\bnot\s+(?:for\s+us|for\s+me|relevant|applicable)\b|'
                    r'\bwe\s+already\s+(?:have|use)\b|'
                    r'\bnot\s+(?:right\s+)?now\b|'
                    r'\bmaybe\s+later\b|'
                    r'\bdoes\s*n\'?t\s+(?:fit|suit|apply|work\s+for)\b|'
                    r'\bpass\s+on\s+this\b|'
                    r'\bwe\'?re?\s+(?:not\s+looking|good|all\s+set)\b|'
                    # Russian
                    r'не\s*пиши(?:те)?|'
                    r'(?:прошу|просьба)\s+(?:не\s+писать|больше\s+не|прекратить)|'
                    r'отпис(?:ать|ка|аться)|'
                    r'(?:больше\s+)?не\s+(?:нужно|надо|хочу)\s*(?:писать|получать|ваших?\s+пис)|'
                    r'(?:уберите|удалите)\s+(?:меня|мой\s+(?:email|адрес))|'
                    r'(?:прекратите|перестаньте)\s+(?:писать|рассылку|отправлять)|'
                    r'(?:не\s+)?интересно|'
                    r'не\s+(?:подходит|актуально|релевантно)|'
                    r'(?:у\s+нас\s+)?уже\s+(?:есть|используем|имеется)|'
                    r'не\s+сейчас|'
                    r'(?:может\s+)?позже|как[\s\-]нибудь\s+потом|'
                    r'нет[,.]?\s*спасибо|'
                    r'(?:мы\s+)?(?:это\s+)?не\s+(?:используем|применяем)|'
                    r'спам|spam|'
                    # Greek
                    r'μη\s*(?:μου)?\s*(?:στ[εέ]λν|γρ[αά]φ)|'
                    r'σταματ[ήη]στε|'
                    r'(?:δεν|δε)\s+(?:με\s+)?ενδιαφ[εέ]ρ|'
                    r'(?:δεν|δε)\s+θ[εέ]λω|'
                    r'αφ[ήη]στ[εέ]\s+(?:με|μου)|'
                    r'διαγρ[αά]ψτε\s+(?:με|μου)|'
                    # Spanish
                    r'(?:no\s+me\s+(?:escriba|contacte|envíe))|'
                    r'(?:darse\s+de\s+baja|cancelar\s+suscripci[oó]n)|'
                    r'(?:no\s+(?:estoy\s+)?interesad[oa])|'
                    # German
                    r'(?:ab(?:bestellen|melden))|'
                    r'(?:(?:nicht|kein)\s+(?:mehr\s+)?(?:schreiben|kontaktieren|senden))|'
                    r'(?:kein\s+interesse)|'
                    # French
                    r'(?:(?:ne\s+)?(?:m\'|me\s+)?(?:[ée]crivez|contactez|envoyez)\s+(?:plus|pas))|'
                    r'(?:d[eé]sabonner|d[eé]sinscri)|'
                    r'(?:pas\s+int[eé]ress[eé])|'
                    # Italian
                    r'(?:(?:non\s+)?(?:mi\s+)?(?:scriva|contatti|invii)\s+più)|'
                    r'(?:non\s+(?:sono\s+)?interessat[oa])|'
                    r'(?:cancellar[emsit]+\s+(?:la\s+)?iscrizion)|'
                    # Portuguese
                    r'(?:(?:não|nao)\s+me\s+(?:escreva|contacte|envie))|'
                    r'(?:(?:não|nao)\s+(?:estou\s+)?interessad[oa])|'
                    r'(?:cancelar\s+inscri[çc][ãa]o)|'
                    # Turkish
                    r'(?:yazma(?:yın|yin))|(?:abonelikten\s+çık)|(?:ilgilenmiyorum)',
                    _re_unsub_ce.IGNORECASE,
                )
                for _unsub_em, _unsub_snip in _reply_snippets.items():
                    if _UNSUB_RE.search(_unsub_snip):
                        _unsubscribed_emails.append(_unsub_em)
                        try:
                            # Обновляем EmailContact → unsubscribed
                            _ec_unsub = session.query(_EC_ce2).filter_by(
                                user_id=user.id, email=_unsub_em
                            ).first()
                            if _ec_unsub:
                                _ec_unsub.status = 'unsubscribed'
                                _old_notes = _ec_unsub.notes or ''
                                if 'отписка' not in _old_notes.lower():
                                    _ec_unsub.notes = ((_old_notes + '\n') if _old_notes else '') + '[отписка: контакт попросил не писать]'
                            else:
                                session.add(_EC_ce2(
                                    user_id=user.id, email=_unsub_em,
                                    source='imap_reply', status='unsubscribed',
                                    notes='[отписка: контакт попросил не писать]',
                                    last_contacted_at=_dt_ce.datetime.utcnow(),
                                ))
                            # Обновляем EmailOutreach → статус 'unsubscribed', убираем follow-up
                            _eo_unsub = session.query(_EO_ce2).filter(
                                _EO_ce2.user_id == user.id,
                                _EO_ce2.recipient_email == _unsub_em,
                            ).all()
                            for _eo_u in _eo_unsub:
                                _eo_u.status = 'unsubscribed' if _eo_u.status != 'replied' else 'replied'
                                _eo_u.next_follow_up_at = None  # убираем запланированные follow-up
                            session.commit()
                            logger.info(f'[CHECK_EMAILS] AUTO-UNSUBSCRIBE: {_unsub_em} marked as unsubscribed')
                        except Exception as _e_unsub:
                            logger.debug(f'[CHECK_EMAILS] auto-unsubscribe failed: {_e_unsub}')
                            try:
                                session.rollback()
                            except Exception:
                                pass

            _contact_prefs_found: dict = {}
            if _reply_snippets:
                import re as _re_pref_ce
                _PREF_TRIGGER_CE = _re_pref_ce.compile(
                    r'prefer|would like|please (?:use|write|respond|reply)|'
                    r'write in|respond in|reply in|communicate in|'
                    r'хочу использовать|хочу общаться|хотел.{0,10}бы|'
                    r'предпочитаю|пожалуйста пишите|пишите на|прошу писать|'
                    r'давайте (?:общаться|переписываться)|want to (?:use|communicate|write)|'
                    # Greek preference triggers
                    r'(?:παρακαλ[ώω]|θ[αά]\s+[ήη]θελα)\s+(?:γρ[αά]ψτε|απαντ[ήη]στε)|'
                    r'(?:γρ[αά]ψτε|στ[εέ]λνετε)\s+(?:στα?|σε)\s+(?:ελληνικ[αά]|αγγλικ[αά])|'
                    r'προτιμ[ώω]|'
                    # Spanish
                    r'(?:por\s+favor\s+(?:escrib|respond))|(?:prefer[ií]a)|'
                    # German
                    r'(?:bitte\s+(?:schreiben|antworten)\s+(?:auf|in))|'
                    # French
                    r'(?:veuillez\s+(?:[ée]crire|r[ée]pondre))|(?:je\s+pr[eé]f[eè]re)',
                    _re_pref_ce.IGNORECASE,
                )
                _LANG_PREF_CE = [
                    (r'greek|греческ|ελληνικ[αά]|στα\s+ελληνικ', 'язык: греческий'),
                    (r'\brussian\b|по-?русски|на\s+русском|ρωσικ[αά]', 'язык: русский'),
                    (r'\benglish\b|по-?английски|на\s+английском|αγγλικ[αά]', 'язык: английский'),
                    (r'spanish|по-?испански|на\s+испанском|español|ισπανικ[αά]', 'язык: испанский'),
                    (r'german|по-?немецки|на\s+немецком|deutsch', 'язык: немецкий'),
                    (r'french|по-?французски|на\s+французском|français', 'язык: французский'),
                    (r'chinese|по-?китайски|на\s+китайском|中文',  'язык: китайский'),
                    (r'japanese|по-?японски|на\s+японском|日本語',  'язык: японский'),
                    (r'ukrainian|по-?украински|на\s+украинском',  'язык: украинский'),
                    (r'portuguese|по-?португальски|на\s+португальском', 'язык: португальский'),
                    (r'italian|по-?итальянски|на\s+итальянском',  'язык: итальянский'),
                    (r'arabic|по-?арабски|на\s+арабском',         'язык: арабский'),
                    (r'turkish|по-?турецки|на\s+турецком',        'язык: турецкий'),
                    (r'formal|официальн|деловой стиль',            'стиль: официальный'),
                    (r'informal|неформальн|casual|дружеск',        'стиль: неформальный'),
                ]
                for _pref_em, _pref_snip in _reply_snippets.items():
                    if not _PREF_TRIGGER_CE.search(_pref_snip):
                        continue
                    _pref_low = _pref_snip.lower()
                    for _lpat, _llabel in _LANG_PREF_CE:
                        if _re_pref_ce.search(_lpat, _pref_low):
                            _contact_prefs_found[_pref_em] = _llabel
                            try:
                                _ec_pref = session.query(_EC_ce2).filter_by(user_id=user.id, email=_pref_em).first()
                                if _ec_pref:
                                    _old_notes_pref = _ec_pref.notes or ''
                                    _pref_tag = f'[предпочтение: {_llabel}]'
                                    if _llabel not in _old_notes_pref:
                                        _clean_pref = _re_pref_ce.sub(r'\[предпочтение:[^\]]*\]', '', _old_notes_pref).strip()
                                        _ec_pref.notes = ((_clean_pref + '\n') if _clean_pref else '') + _pref_tag
                                        session.commit()
                                        logger.info(f'[CHECK_EMAILS] Saved preference for {_pref_em}: {_pref_tag}')
                            except Exception as _e_pref:
                                logger.debug(f'[CHECK_EMAILS] pref save failed: {_e_pref}')
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                            break  # одно предпочтение на контакт

            # Авто-обновление метрики если появились новые replied контакты
            # Считаем ТОЛЬКО реально новые ответы (статус изменился с non-replied → replied)
            # НЕ считаем уже известных replied-контактов повторно при каждой проверке!
            _newly_replied_this_call = _truly_new_contacts + _truly_new_replies
            if _newly_replied_this_call > 0:
                try:
                    from models import Goal as _Goal_ce
                    _ppl_kw_ce = ('пользовател', 'тестировщик', 'участник', 'подписчик', 'user', 'tester', 'контакт',
                                  'заинтересован', 'привлеч', 'клиент', 'партнёр', 'лиц ')
                    _ppl_goals_ce = session.query(_Goal_ce).filter(
                        _Goal_ce.user_id == user.id,
                        _Goal_ce.status == 'active',
                        _Goal_ce.metric_target.isnot(None),
                    ).all()
                    for _g_ce2 in _ppl_goals_ce:
                        _g_text_ce = (_g_ce2.title + ' ' + (_g_ce2.description or '') + ' ' + (_g_ce2.metric_unit or '')).lower()
                        if any(w in _g_text_ce for w in _ppl_kw_ce):
                            _new_metric = float(_g_ce2.metric_current or 0) + _newly_replied_this_call
                            if _new_metric > (_g_ce2.metric_current or 0):
                                update_goal_progress(
                                    goal_title=_g_ce2.title,
                                    metric_current=int(_new_metric),
                                    notes=f'check_emails: +{_newly_replied_this_call} новых ответов',
                                    user_id=user_id,
                                )
                                logger.info(f'[CHECK_EMAILS] Auto-updated goal metric: {_g_ce2.title} +{_newly_replied_this_call} → {_new_metric}')
                                # Логируем inbox_reply в AgentActivityLog — status='new' чтобы
                                # _scan_agent_inbox_replies создал CRITICAL якорь → пользователь получит уведомление
                                try:
                                    from models import AgentActivityLog as _AAL_ce_ir
                                    # Собираем preview ответов для content
                                    _reply_preview_lines = []
                                    for _rp_em, _rp_snip in list(_reply_snippets.items())[:5]:
                                        _rp_name = ''
                                        try:
                                            _rp_ec = session.query(_EC_ce2).filter_by(user_id=user.id, email=_rp_em).first()
                                            _rp_name = getattr(_rp_ec, 'name', '') or ''
                                        except Exception:
                                            pass
                                        _reply_preview_lines.append(
                                            f"От: {_rp_name} <{_rp_em}>\n"
                                            f"Тема: ответ на outreach\n"
                                            f"Превью: {(_rp_snip or '')[:300]}"
                                        )
                                    _reply_content = '\n---\n'.join(_reply_preview_lines) if _reply_preview_lines else f'Новые ответы: {_newly_replied_this_call}'
                                    _aal_ir = _AAL_ce_ir(
                                        user_id=user.id,
                                        activity_type='inbox_reply',
                                        title=f'check_emails: {_newly_replied_this_call} новых ответов на письма',
                                        content=_reply_content[:2000],
                                        target=f'agent:{chosen.get("agent_name", chosen.get("label", "email"))}',
                                        status='new',
                                    )
                                    session.add(_aal_ir)
                                    session.commit()
                                except Exception as _e_aal_ir:
                                    logger.debug(f'[CHECK_EMAILS] AAL inbox_reply log failed: {_e_aal_ir}')
                                break
                except Exception as _e_auto_gp:
                    logger.debug(f'[CHECK_EMAILS] auto update_goal_progress failed: {_e_auto_gp}')
            else:
                # Reconciliation: metric_current=0 но replied-контакты уже есть → синхронизировать
                try:
                    from models import Goal as _Goal_rec, EmailOutreach as _EO_rec
                    _ppl_kw_rec = ('пользовател', 'тестировщик', 'участник', 'подписчик', 'user', 'tester', 'контакт',
                                   'заинтересован', 'привлеч', 'клиент', 'партнёр', 'лиц ')
                    _ppl_goals_rec = session.query(_Goal_rec).filter(
                        _Goal_rec.user_id == user.id,
                        _Goal_rec.status == 'active',
                        _Goal_rec.metric_target.isnot(None),
                    ).all()
                    for _g_rec in _ppl_goals_rec:
                        if float(_g_rec.metric_current or 0) > 0:
                            continue  # уже синхронизирована
                        _g_text_rec = (_g_rec.title + ' ' + (_g_rec.description or '') + ' ' + (_g_rec.metric_unit or '')).lower()
                        if not any(w in _g_text_rec for w in _ppl_kw_rec):
                            continue
                        _total_replied_rec = session.query(_EO_rec).filter(
                            _EO_rec.user_id == user.id,
                            _EO_rec.status == 'replied',
                        ).count()
                        if _total_replied_rec > 0:
                            update_goal_progress(
                                goal_title=_g_rec.title,
                                metric_current=_total_replied_rec,
                                notes=f'check_emails reconciliation: {_total_replied_rec} replied контактов',
                                user_id=user_id,
                            )
                            logger.info(f'[CHECK_EMAILS] Reconciled metric: {_g_rec.title} → {_total_replied_rec}')
                except Exception as _e_rec:
                    logger.debug(f'[CHECK_EMAILS] reconciliation failed: {_e_rec}')

            # Аннотируем результат: показываем агенту предпочтения контактов
            # (только что найденные + ранее сохранённые в EmailContact.notes)
            try:
                _all_prefs_ann: dict = dict(_contact_prefs_found)
                import re as _re_pref_ann
                for _ann_em in _found_em:
                    if _ann_em not in _all_prefs_ann:
                        _ann_ec = session.query(_EC_ce2).filter_by(user_id=user.id, email=_ann_em).first()
                        if _ann_ec and 'предпочтение' in (_ann_ec.notes or ''):
                            _saved = _re_pref_ann.findall(r'\[предпочтение: ([^\]]+)\]', _ann_ec.notes)
                            if _saved:
                                _all_prefs_ann[_ann_em] = ', '.join(_saved)
                if _all_prefs_ann:
                    _pref_ann = '\n\n⚠ ПРЕДПОЧТЕНИЯ КОНТАКТОВ (обязательно учитывай при ответе):\n'
                    _pref_ann += '\n'.join(f'• {_em}: {_pref}' for _em, _pref in _all_prefs_ann.items())
                    _pref_ann += '\n→ Пиши reply_body на указанном языке и в указанном стиле!'
                    result += _pref_ann
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            # ── Аннотация для агента: отписавшиеся контакты ──
            if _unsubscribed_emails:
                result += '\n\n⛔ ОТПИСАЛИСЬ (НЕ отправляй им больше ни одного письма):\n'
                result += '\n'.join(f'• {_ue}' for _ue in _unsubscribed_emails)

            # ── Явный счётчик новых ответов для агента ──
            if _newly_replied_this_call > 0:
                result += (
                    f'\n\n📊 НОВЫЕ ОТВЕТЫ В ЭТОМ СЕАНСЕ: +{_newly_replied_this_call} контакт(а/ов) с интересом к проекту.\n'
                    f'   → Метрика цели уже обновлена автоматически (+{_newly_replied_this_call}).\n'
                    f'   → НЕ вызывай update_goal_progress повторно — уже сделано!'
                )

            # ── Пре-классификация намерений + аннотация для агента ──
            _has_replies = bool(_reply_snippets)
            _reply_classifications: dict = {}
            if _has_replies:
                import re as _re_cls_ce
                _QUESTION_RE = _re_cls_ce.compile(
                    r'\?\s*$|'
                    r'\b(?:how|what|when|where|which|who|why|can\s+you|could\s+you|is\s+there|do\s+you)\b|'
                    r'\b(?:как|что|когда|где|какой|какая|какие|можно|можете|есть\s+ли|подскажите|расскажите|покажите)\b|'
                    r'(?:πώς|τι|πότε|πού|ποιο|μπορ(?:εί|ούν)|υπάρχ)|'
                    r'(?:cómo|qué|cuándo|dónde|puede|hay)|'
                    r'(?:wie|was|wann|wo|können|gibt\s+es)',
                    _re_cls_ce.IGNORECASE | _re_cls_ce.MULTILINE,
                )
                _INTEREST_RE = _re_cls_ce.compile(
                    r'\b(?:interested|love|great|awesome|sounds?\s+good|let\'?s?\s+(?:do|try|talk)|sign\s+me\s+up|count\s+me\s+in)\b|'
                    r'\b(?:интересно|отлично|здорово|давайте|хочу|готов[аы]?|попробу|подключ|хотел.{0,5}бы)\b|'
                    r'(?:ενδιαφ[εέ]ρ(?:ομαι|ον)|τ[εέ]λεια|θα\s+[ήη]θελα)',
                    _re_cls_ce.IGNORECASE,
                )
                for _cls_em, _cls_snip in _reply_snippets.items():
                    if _cls_em in [e.lower() for e in _unsubscribed_emails]:
                        _reply_classifications[_cls_em] = '🔴 ОТКАЗ'
                    elif _INTEREST_RE.search(_cls_snip):
                        if _QUESTION_RE.search(_cls_snip):
                            _reply_classifications[_cls_em] = '🟢 ИНТЕРЕС + ВОПРОС'
                        else:
                            _reply_classifications[_cls_em] = '🟢 ИНТЕРЕС'
                    elif _QUESTION_RE.search(_cls_snip):
                        _reply_classifications[_cls_em] = '🟡 ВОПРОС'
                    else:
                        _reply_classifications[_cls_em] = '⚪ НЕЯСНО — прочитай внимательно'

                # Показываем агенту пре-классификацию
                if _reply_classifications:
                    result += '\n\n📋 КЛАССИФИКАЦИЯ ОТВЕТОВ (проверь по тексту выше):\n'
                    for _cls_em2, _cls_label in _reply_classifications.items():
                        result += f'• {_cls_em2} → {_cls_label}\n'

                result += (
                    '\n🛡️ КАК ДЕЙСТВОВАТЬ ПО КАЖДОМУ ТИПУ ОТВЕТА:'
                    '\n'
                    '\n🟢 ИНТЕРЕС (хочу попробовать, расскажите подробнее, давайте):'
                    '\n   → Ответь БЫСТРО, дай конкретику: ссылку, инструкцию, предложение созвона'
                    '\n   → reply_to_outreach_email → negotiate_by_email → update_goal_progress(+1)'
                    '\n'
                    '\n🟡 ВОПРОС (как это работает? сколько стоит? есть ли X?):'
                    '\n   → ОТВЕТЬ НА КОНКРЕТНЫЙ ВОПРОС — не шаблонно, а именно то что спросили'
                    '\n   → Если знаешь ответ — дай его. Если нет — скажи "уточню и вернусь"'
                    '\n   → reply_to_outreach_email с reply_body = ответ на вопрос + мягкий CTA'
                    '\n   → НЕ игнорируй вопрос, не отвечай общими фразами'
                    '\n'
                    '\n🔴 ОТКАЗ (не интересно, уже есть решение, не пишите, не сейчас):'
                    '\n   → НЕ ОТВЕЧАЙ. Контакт уже автоматически отписан.'
                    '\n   → Если автоотписка не сработала — вызови DELEGATE или отметь вручную'
                    '\n'
                    '\n⚪ НЕЯСНО (автоответ, подпись, короткое "ок"):'
                    '\n   → Прочитай текст внимательно. Если есть вопрос — ответь.'
                    '\n   → Если просто "ок/спасибо" без запроса — не отвечай, жди следующего шага от них.'
                    '\n'
                    '\n→ Определяй язык из текста ответа контакта и пиши reply_body на том же языке!'
                )

            # ── AI-уже-ответил: УДАЛЯЕМ из результата контакты, которым AI уже дважды ответил ──
            # Стратегия: если ai_reply_count >= _MAX_AI_REPLIES → блок удаляется из result целиком.
            # Это предотвращает ситуацию когда агент «объявляет» ответ на уже закрытые контакты.
            # Если остались блоки с частично-ответившими → добавляем inline-метку внутрь блока.
            try:
                if _found_em and result:
                    _MAX_R = 2  # должен совпадать с _MAX_AI_REPLIES в reply_to_outreach_email
                    import re as _re_air2
                    # Разбиваем result на заголовок + блоки
                    _hdr_m2 = _re_air2.match(r'^(.*?(?:\d+\s*писем[^\n]*|входящих[^\n]*)\n(?:[^\n]*примечание[^\n]*\n)?\n)', result, _re_air2.DOTALL | _re_air2.IGNORECASE)
                    _hdr2 = _hdr_m2.group(1) if _hdr_m2 else ''
                    _body2 = result[len(_hdr2):].strip()
                    _blks2 = _re_air2.split(r'\n---\n', _body2)
                    _kept_blks: list = []
                    _skipped_replied_count = 0
                    for _blk2 in _blks2:
                        if not _blk2.strip():
                            continue
                        # Находим email в блоке
                        _blk_ems = _re_air2.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', _blk2, _re_air2.IGNORECASE)
                        _blk_em = _blk_ems[0].lower() if _blk_ems else None
                        if not _blk_em or _blk_em not in _found_em:
                            _kept_blks.append(_blk2)
                            continue
                        # Считаем сколько раз AI уже ответил этому контакту
                        _replied_cnt2 = session.query(_EO_ce2).filter(
                            _EO_ce2.user_id == user.id,
                            _EO_ce2.recipient_email == _blk_em,
                            _EO_ce2.ai_reply_sent_at.isnot(None),
                        ).count()
                        if _replied_cnt2 >= _MAX_R:
                            # Максимум ответов — убираем блок из result
                            _skipped_replied_count += 1
                            logger.info('[CHECK_EMAILS] Filtered already-max-replied contact from output: %s (%d replies)', _blk_em, _replied_cnt2)
                        elif _replied_cnt2 > 0:
                            # Уже ответили один раз, но можно ещё — добавляем inline-пометку
                            _eo_air2 = session.query(_EO_ce2).filter(
                                _EO_ce2.user_id == user.id,
                                _EO_ce2.recipient_email == _blk_em,
                                _EO_ce2.ai_reply_sent_at.isnot(None),
                            ).order_by(_EO_ce2.ai_reply_sent_at.desc()).first()
                            _air2_date = str(_eo_air2.ai_reply_sent_at)[:10] if _eo_air2 else '?'
                            _kept_blks.append(_blk2.rstrip() + f'\n[ℹ️ AI уже отвечал {_air2_date} — ответ допустим ещё 1 раз]')
                        else:
                            _kept_blks.append(_blk2)
                    if _skipped_replied_count > 0:
                        if not _kept_blks:
                            result = 'Нет новых входящих для обработки (все письма уже получили ответ от AI).'
                        else:
                            result = _hdr2 + '\n---\n'.join(_kept_blks)
            except Exception as _e_air:
                logger.debug('[CHECK_EMAILS] ai_reply_sent_at filter failed: %s', _e_air)

        return result
    except Exception as e:
        logger.error(f"[CHECK_EMAILS] Error: {e}", exc_info=True)
        return f"Ошибка при проверке почты: {e}"
    finally:
        if close_session:
            session.close()


async def _check_emails_gmail_api(token_data: dict, limit: int, user, session, known_emails: set = None, my_emails: set = None, outreach_map: dict = None, registered_emails: set = None) -> str:
    """Читает входящие через Gmail API v1."""
    import base64 as _b64_r
    import json as _jsn_r
    import datetime as _dt_r
    from config import GOOGLE_CLIENT_ID as _GCI_r, GOOGLE_CLIENT_SECRET as _GCS_r

    access_token = token_data.get('access_token', '')
    refresh_token = token_data.get('refresh_token', '')
    gmail_email = token_data.get('email', '')

    async def _refresh():
        nonlocal access_token
        if not refresh_token or not _GCI_r or not _GCS_r:
            return False
        try:
            async with aiohttp.ClientSession() as _h:
                _r = await _h.post(
                    'https://oauth2.googleapis.com/token',
                    data={
                        'client_id': _GCI_r,
                        'client_secret': _GCS_r,
                        'refresh_token': refresh_token,
                        'grant_type': 'refresh_token',
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                _rd = await _r.json()
                if 'access_token' in _rd:
                    access_token = _rd['access_token']
                    new_tok = dict(token_data)
                    new_tok['access_token'] = access_token
                    new_tok['saved_at'] = _dt_r.datetime.utcnow().isoformat()
                    from config import encrypt_token as _et_r
                    user.google_oauth_token = _et_r(_jsn_r.dumps(new_tok))
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                    return True
        except Exception as _re:
            logger.warning(f'[CHECK_EMAILS_GMAIL] Token refresh error: {_re}')
        return False

    async def _fetch(tok):
        async with aiohttp.ClientSession() as _h:
            # Список последних писем
            _resp = await _h.get(
                'https://gmail.googleapis.com/gmail/v1/users/me/messages',
                headers={'Authorization': f'Bearer {tok}'},
                params={'maxResults': str(limit), 'labelIds': 'INBOX'},
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if _resp.status == 401:
                return None  # need refresh
            _data = await _resp.json()
            msgs = _data.get('messages', [])
            if not msgs:
                return "Входящих писем нет."

            results = []
            skipped_known_g = []
            import re as _re_gm
            import base64 as _b64_gm

            def _gmail_extract_body(payload: dict) -> str:
                """Рекурсивно извлекает text/plain (или text/html) из Gmail payload."""
                import re as _re_body
                mime = payload.get('mimeType', '')
                parts = payload.get('parts', [])
                body_data = payload.get('body', {}).get('data', '')
                if mime == 'text/plain' and body_data:
                    try:
                        return _b64_gm.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace').strip()
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                if mime == 'text/html' and body_data:
                    try:
                        raw = _b64_gm.urlsafe_b64decode(body_data + '==').decode('utf-8', errors='replace')
                        return _re_body.sub(r'<[^>]+>', '', raw).strip()
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                # Рекурсивно искать text/plain среди parts
                for part in parts:
                    if part.get('mimeType') == 'text/plain':
                        _d = part.get('body', {}).get('data', '')
                        if _d:
                            try:
                                return _b64_gm.urlsafe_b64decode(_d + '==').decode('utf-8', errors='replace').strip()
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                # Fallback: text/html
                for part in parts:
                    if part.get('mimeType') == 'text/html':
                        _d = part.get('body', {}).get('data', '')
                        if _d:
                            try:
                                raw = _b64_gm.urlsafe_b64decode(_d + '==').decode('utf-8', errors='replace')
                                return _re_body.sub(r'<[^>]+>', '', raw).strip()
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                    # multipart вложенные
                    if part.get('parts'):
                        _sub = _gmail_extract_body(part)
                        if _sub:
                            return _sub
                return ''

            for msg_ref in msgs[:limit]:
                # format=full чтобы получить полный текст письма
                msg_resp = await _h.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                    headers={'Authorization': f'Bearer {tok}'},
                    params={'format': 'full'},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                if msg_resp.status != 200:
                    continue
                msg_data = await msg_resp.json()
                headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
                # Извлекаем полный текст тела письма (приоритет: body_text > snippet)
                body_text = _gmail_extract_body(msg_data.get('payload', {}))
                snippet = body_text[:3000] if body_text else msg_data.get('snippet', '')[:500]
                from_hdr = headers.get('From', '?')
                # Фильтруем письма от собственных аккаунтов (копии исходящих)
                _gm_ems = _re_gm.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', from_hdr, _re_gm.IGNORECASE)
                _gm_em_low = _gm_ems[0].lower() if _gm_ems else ''
                if my_emails and _gm_em_low and _gm_em_low in my_emails:
                    continue
                # Помечаем известные контакты — но НЕ скрываем их письма
                _is_known = known_emails and _gm_em_low and _gm_em_low in known_emails
                _is_registered = registered_emails and _gm_em_low and _gm_em_low in registered_emails
                if _is_known:
                    skipped_known_g.append(from_hdr)
                _known_badge = "[зарегистрированный пользователь] " if _is_registered else ("[email-контакт, не зарегистрирован в сервисе] " if _is_known else "")
                _gm_outreach_ctx = ""
                if outreach_map and _gm_em_low and _gm_em_low in outreach_map:
                    _goc = outreach_map[_gm_em_low]
                    _goc_agent = _goc.get("sent_by_agent") or "наш агент"
                    _goc_date = _goc["sent_at"].strftime("%d.%m") if _goc.get("sent_at") else "?"
                    _goc_subj = (_goc.get("subject") or "")[:50]
                    _gm_outreach_ctx = (f"\n⚡ ИСХОДЯЩИЙ OUTREACH (outreach_id={_goc['outreach_id']}): "
                                        f"{_goc_agent} писал(а) этому контакту {_goc_date}, тема: «{_goc_subj}». "
                                        f"⚠️ Отвечать ДОЛЖЕН(А) {_goc_agent} — переписку ведёт тот агент, кто начал диалог. "
                                        f"Используй reply_to_outreach_email(outreach_id={_goc['outreach_id']})")
                results.append(
                    f"От: {_known_badge}{from_hdr}\n"
                    f"Тема: {headers.get('Subject', '(без темы)')}\n"
                    f"Дата: {headers.get('Date', '?')}\n"
                    f"Превью: {snippet}{_gm_outreach_ctx}\n"
                )
            if not results:
                return "Входящих писем нет."
            _known_count = len(skipped_known_g)
            _known_note = (f"\n⚠️ Примечание: {_known_count} из них помечены [email-контакт]. "
                           f"Проверяй бейдж: [зарегистрированный пользователь] = есть в сервисе, "
                           f"[email-контакт, не зарегистрирован в сервисе] = только в контактах." if _known_count else "")
            return (f"Входящие ({gmail_email}, {len(results)} писем){_known_note}:\n\n"
                    + "\n---\n".join(results))

    result = await _fetch(access_token)
    if result is None:
        # Token expired → refresh
        if await _refresh():
            result = await _fetch(access_token)
        if result is None:
            return "Не удалось авторизоваться в Gmail. Пользователю нужно переподключить Google OAuth."
    return result


async def _check_emails_imap(integration: dict, limit: int, known_emails: set = None, my_emails: set = None, outreach_map: dict = None, registered_emails: set = None) -> str:
    """Читает входящие через IMAP (Яндекс, Mail.ru, Gmail app-password)."""    
    import asyncio
    import imaplib
    import email as _email_mod
    from email.header import decode_header as _dh

    label = integration.get('label', 'Email')
    email_user = integration.get('email_user', '')

    # Определяем IMAP-сервер
    if 'gmail' in label.lower() or 'gmail' in email_user.lower():
        imap_host = 'imap.gmail.com'
    elif 'yandex' in label.lower() or 'yandex' in email_user.lower():
        imap_host = 'imap.yandex.ru'
    elif 'mail.ru' in label.lower() or 'mail.ru' in email_user.lower():
        imap_host = 'imap.mail.ru'
    else:
        imap_host = integration.get('smtp_host', '').replace('smtp.', 'imap.')

    email_pass = integration.get('email_pass', '')
    if not email_pass:
        svc = 'GMAIL_PASS' if 'gmail' in (email_user or label or '').lower() else 'YANDEX_PASS или MAILRU_PASS'
        return (f"Для чтения входящих через IMAP нужен пароль приложения. "
                f"Настрой {svc} в настройках агента на дашборде.")

    def _decode_subj(raw):
        parts = _dh(raw)
        result = []
        for data, charset in parts:
            if isinstance(data, bytes):
                result.append(data.decode(charset or 'utf-8', errors='replace'))
            else:
                result.append(str(data))
        return ' '.join(result)

    def _imap_fetch():
        try:
            mail = imaplib.IMAP4_SSL(imap_host, 993, timeout=15)
            mail.login(email_user, email_pass)
            mail.select('INBOX', readonly=True)
            _status, _nums = mail.search(None, 'ALL')
            if _status != 'OK' or not _nums[0]:
                mail.logout()
                return "Входящих писем нет."
            ids = _nums[0].split()
            ids = ids[-limit:]  # последние N
            ids.reverse()

            results = []
            skipped_known = []
            import re as _re_imap
            for mid in ids:
                _s, _d = mail.fetch(mid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                if _s != 'OK':
                    continue
                raw_header = _d[0][1] if _d[0] and len(_d[0]) > 1 else b''
                msg = _email_mod.message_from_bytes(raw_header)
                from_addr = msg.get('From', '?')
                subject = _decode_subj(msg.get('Subject', '(без темы)'))
                date = msg.get('Date', '?')

                # Фильтруем уже-известные контакты
                _from_ems = _re_imap.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', from_addr, _re_imap.IGNORECASE)
                _from_low = _from_ems[0].lower() if _from_ems else ''

                # Пропускаем письма от собственных аккаунтов (копии исходящих, тесты)
                if my_emails and _from_low and _from_low in my_emails:
                    continue

                # Помечаем известные контакты — но НЕ скрываем их письма
                _is_known_imap = known_emails and _from_low and _from_low in known_emails
                _is_registered_imap = registered_emails and _from_low and _from_low in registered_emails
                if _is_known_imap:
                    skipped_known.append(from_addr)

                # Snippet: фетчим начало полного письма и парсим через email-модуль.
                # BODY.PEEK[TEXT] для multipart возвращает сырой MIME с boundary-строками
                # вместо текста — поэтому используем BODY.PEEK[] + email.message_from_bytes.
                _st, _dt2 = mail.fetch(mid, '(BODY.PEEK[]<0.5000>)')
                snippet = ''
                if _st == 'OK' and _dt2[0] and len(_dt2[0]) > 1:
                    raw_full = _dt2[0][1]
                    try:
                        _parsed = _email_mod.message_from_bytes(raw_full)
                        if _parsed.is_multipart():
                            # Ищем text/plain часть
                            for _part in _parsed.walk():
                                if (_part.get_content_type() == 'text/plain'
                                        and 'attachment' not in str(_part.get('Content-Disposition', ''))):
                                    _pay = _part.get_payload(decode=True)
                                    if _pay:
                                        _cs = _part.get_content_charset() or 'utf-8'
                                        snippet = _pay.decode(_cs, errors='replace').strip()[:400]
                                        break
                            # Fallback: text/html → убираем теги
                            if not snippet:
                                import re as _re_htm
                                for _part in _parsed.walk():
                                    if _part.get_content_type() == 'text/html':
                                        _pay = _part.get_payload(decode=True)
                                        if _pay:
                                            _cs = _part.get_content_charset() or 'utf-8'
                                            snippet = _re_htm.sub(
                                                r'<[^>]+>', '',
                                                _pay.decode(_cs, errors='replace'),
                                            ).strip()[:400]
                                            break
                        else:
                            _pay = _parsed.get_payload(decode=True)
                            if _pay:
                                _cs = _parsed.get_content_charset() or 'utf-8'
                                snippet = _pay.decode(_cs, errors='replace').strip()[:400]
                    except Exception:
                        # Крайний fallback: вырезаем MIME boundary вручную
                        try:
                            import re as _re_mime
                            _raw_s = raw_full.decode('utf-8', errors='replace')
                            _raw_s = _re_mime.sub(r'--[A-Za-z0-9_\-]{6,}[^\n]*\n?', '', _raw_s)
                            _raw_s = _re_mime.sub(r'Content-[A-Za-z\-]+:[^\n]*\n?', '', _raw_s)
                            snippet = _raw_s.strip()[:3000]
                        except Exception:
                            snippet = ''
                # Храним полный текст (до 3000 символов) для reply_text в БД
                _known_badge_imap = "[зарегистрированный пользователь] " if _is_registered_imap else ("[email-контакт, не зарегистрирован в сервисе] " if _is_known_imap else "")
                _im_outreach_ctx = ""
                if outreach_map and _from_low and _from_low in outreach_map:
                    _ioc = outreach_map[_from_low]
                    _ioc_agent = _ioc.get("sent_by_agent") or "наш агент"
                    _ioc_date = _ioc["sent_at"].strftime("%d.%m") if _ioc.get("sent_at") else "?"
                    _ioc_subj = (_ioc.get("subject") or "")[:50]
                    _im_outreach_ctx = (f"\n⚡ ИСХОДЯЩИЙ OUTREACH (outreach_id={_ioc['outreach_id']}): "
                                        f"{_ioc_agent} писал(а) этому контакту {_ioc_date}, тема: «{_ioc_subj}». "
                                        f"⚠️ Отвечать ДОЛЖЕН(А) {_ioc_agent} — переписку ведёт тот агент, кто начал диалог. "
                                        f"Используй reply_to_outreach_email(outreach_id={_ioc['outreach_id']})")
                results.append(
                    f"От: {_known_badge_imap}{from_addr}\n"
                    f"Тема: {subject}\n"
                    f"Дата: {date}\n"
                    f"Превью: {snippet}{_im_outreach_ctx}\n"
                )
            mail.logout()
            if not results:
                return "Входящих писем нет."
            _known_cnt = len(skipped_known)
            _known_n = (f"\n⚠️ Примечание: {_known_cnt} из них помечены [email-контакт]. "
                        f"Проверяй бейдж: [зарегистрированный пользователь] = есть в сервисе, "
                        f"[email-контакт, не зарегистрирован в сервисе] = только в контактах." if _known_cnt else "")
            return (f"Входящие ({email_user}, {len(results)} писем){_known_n}:\n\n"
                    + "\n---\n".join(results))
        except imaplib.IMAP4.error as e:
            return f"Ошибка IMAP ({label}): {e}. Проверь пароль приложения."
        except Exception as e:
            return f"Ошибка при чтении почты ({label}): {e}"

    return await asyncio.get_running_loop().run_in_executor(None, _imap_fetch)


async def send_email(
    to: str = None,
    subject: str = None,
    body: str = None,
    sender_name: str = None,
    sender_email: str = None,
    from_account: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Отправить одиночное email-сообщение.

    Требует подключённой почты пользователя (Gmail/Яндекс/Mail.ru) или личного
    Resend-ключа. Платформенный email НЕ используется.
    Универсальный инструмент — предложение, вопрос, напоминание,
    благодарность, что угодно. НЕ связан с кампаниями.
    """
    if not session:
        session = Session()
        close_session = True
    try:
        import aiohttp as _aiohttp

        if not to:
            return " Укажи email получателя (to)."
        if not subject:
            return " Укажи тему письма (subject)."
        if not body:
            return " Нужен текст письма (body)."

        # Sanitize token hallucinations
        from ai_integration.conversation_history import sanitize_token_hallucinations
        body = sanitize_token_hallucinations(body)
        subject = sanitize_token_hallucinations(subject)

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден."

        # ── Проверяем почтовые интеграции пользователя ──────────────────────
        _email_integrations = _get_user_email_integrations(user, session)
        _chosen_integration = None

        if _email_integrations:
            if len(_email_integrations) == 1:
                # Одна интеграция — используем автоматически
                _chosen_integration = _email_integrations[0]
            elif from_account:
                # Пользователь уточнил откуда отправить
                _fa = from_account.strip().lower()
                for _intg in _email_integrations:
                    if _fa in _intg['email_user'].lower() or _fa in _intg['label'].lower():
                        _chosen_integration = _intg
                        break
                if not _chosen_integration:
                    _list = ', '.join(f"{i['label']} ({i['email_user']})" for i in _email_integrations)
                    return f" Аккаунт '{from_account}' не найден среди подключённых почт. Доступные: {_list}"
            else:
                # Несколько интеграций — Gmail OAuth в приоритете, затем SMTP
                _oauth_integrations = [i for i in _email_integrations if i.get('type') == 'gmail_oauth']
                if _oauth_integrations:
                    _chosen_integration = _oauth_integrations[0]
                else:
                    _smtp_integrations = [i for i in _email_integrations if i.get('type') == 'smtp']
                    if len(_smtp_integrations) == 1:
                        _chosen_integration = _smtp_integrations[0]
                    elif len(_smtp_integrations) > 1:
                        _list = '\n'.join(
                            f"• {i['label']}: {i['email_user']}" for i in _smtp_integrations
                        )
                        return (
                            f"У тебя подключено несколько почтовых аккаунтов:\n{_list}\n\n"
                            f"С какого адреса отправить письмо?"
                        )
                    else:
                        # Нет личной почты — берём первый Resend без вопроса
                        _chosen_integration = _email_integrations[0]

        if not _chosen_integration:
            return (
                " Не настроена почтовая интеграция. "
                "Добавь в настройках агента одно из:\n"
                "• Gmail: GMAIL_USER=you@gmail.com и GMAIL_PASS=xxxx xxxx xxxx xxxx\n"
                "• Яндекс: YANDEX_USER=you@yandex.ru и YANDEX_PASS=...\n"
                "• Mail.ru: MAILRU_USER=you@mail.ru и MAILRU_PASS=...\n"
                "• Resend: RESEND_API_KEY=re_... и RESEND_FROM=noreply@твой-домен.com"
            )

        # Fallback sender
        if not sender_name:
            sender_name = user.first_name or user.username or 'Team'
        # Всегда использовать email из интеграции (не из параметров ИИ)
        sender_email = _chosen_integration['email_user']
        # Нормализация адресата
        to_clean = to.strip().lower()
        if not to_clean or '@' not in to_clean:
            return f" Некорректный email получателя: {to!r}. Укажи адрес в формате name@domain.com"

        # ── GUARD: не отправлять email самому себе (user.email / IMAP-аккаунт агента) ──
        _own_emails_se = set()
        _user_email_se = (getattr(user, 'email', '') or '').strip().lower()
        if _user_email_se:
            _own_emails_se.add(_user_email_se)
        try:
            from models import UserAgent as _UA_se
            for _ag_se in session.query(_UA_se).filter(
                _UA_se.author_id == user.id,
                _UA_se.user_api_keys.isnot(None),
            ).all():
                for _ln_se in (_ag_se.user_api_keys or '').splitlines():
                    _ln_se = _ln_se.strip()
                    if _ln_se.upper().startswith(('GMAIL_USER=', 'IMAP_USER=')):
                        _v = _ln_se.split('=', 1)[1].strip().lower()
                        if _v and '@' in _v:
                            _own_emails_se.add(_v)
        except Exception:
            pass
        if to_clean in _own_emails_se:
            return (f"⛔ {to_clean} — это ваша собственная почта или IMAP-аккаунт агента. "
                    f"Нельзя отправлять email самому себе. Найди email реального получателя.")

        # ── GUARD: фейковый / generic / сервисный email ──
        if _is_generic_email(to_clean):
            return f"⛔ {to_clean} — фейковый или generic email. Найди реальный email получателя через поиск или контакты."

        # ── GUARD: дубликат — не слать тому же адресату чаще 1 раза за 4 часа ──
        try:
            from models import EmailOutreach as _EO_dup
            _dup_cut = datetime.now(timezone.utc) - timedelta(hours=4)
            _dup_sess = session
            _dup_close = False
            if _dup_sess is None:
                _dup_sess = Session()
                _dup_close = True
            try:
                _dup_cnt = _dup_sess.query(func.count(_EO_dup.id)).filter(
                    _EO_dup.user_id == user.id,
                    func.lower(_EO_dup.recipient_email) == to_clean,
                    _EO_dup.sent_at >= _dup_cut,
                ).scalar() or 0
            finally:
                if _dup_close:
                    _dup_sess.close()
            if _dup_cnt > 0:
                return f"⛔ {to_clean} уже получал письмо менее 4ч назад. Подожди или выбери другого получателя."
        except Exception as _dup_e:
            logger.debug("send_email dup check: %s", _dup_e)

        # ── Gmail OAuth: прямая отправка через Gmail API ──────────────────────
        if _chosen_integration.get('type') == 'gmail_oauth':
            _goa_ok, _goa_result = await _send_via_gmail_api(
                _chosen_integration['token_data'], to_clean, subject, body,
                sender_name, user, session,
            )
            if not _goa_ok:
                return f" Ошибка отправки (Gmail OAuth): {_goa_result}"
            _gmail_from = _goa_result  # email пользователя
            try:
                from models import EmailOutreach as _EO_log_g
                from models import EmailCampaign as _EC_log_g
                import datetime as _dt_mod_g
                _now_g = _dt_mod_g.datetime.now(_dt_mod_g.timezone.utc)
                _camp_g = session.query(_EC_log_g).filter_by(
                    user_id=user.id, status='personal', sender_email=_gmail_from,
                ).first()
                if not _camp_g:
                    _camp_g = _EC_log_g(
                        user_id=user.id, name='Личная почта (Gmail OAuth)',
                        goal='', target_audience='', offer='',
                        sender_name=sender_name, sender_email=_gmail_from,
                        status='personal', daily_limit=50, max_emails=0,
                        emails_sent=0, emails_replied=0,
                    )
                    session.add(_camp_g)
                    session.flush()
                _eo_g = session.query(_EO_log_g).filter_by(
                    campaign_id=_camp_g.id, recipient_email=to_clean,
                ).first()
                if _eo_g:
                    _eo_g.subject = subject; _eo_g.body = body
                    _eo_g.status = 'sent'; _eo_g.sent_at = _now_g
                else:
                    session.add(_EO_log_g(
                        campaign_id=_camp_g.id, user_id=user.id,
                        recipient_email=to_clean, subject=subject, body=body,
                        sender_email=_gmail_from, status='sent', sent_at=_now_g,
                    ))
                    _camp_g.emails_sent = (_camp_g.emails_sent or 0) + 1
                session.commit()
            except Exception as _log_err_goa:
                logger.warning(f'[SEND_EMAIL] Campaign log (gmail_oauth) error: {_log_err_goa}')
                try: session.rollback()
                except Exception: pass
            # Не создаем AgentActivityLog - письмо уже залогировано в EmailOutreach кампании
            return f" Письмо отправлено с {_gmail_from} на {to_clean} через Gmail"

        # ── Gmail server (пароль приложения) → серверный Resend + Reply-To ───
        # (SMTP Gmail заблокирован Railway; пользователь не привязал OAuth)
        if _chosen_integration.get('type') == 'gmail_server':
            from config import RESEND_API_KEY as _srv_rk
            if not _srv_rk:
                return " Серверный Resend не настроен (RESEND_API_KEY)."
            _gmail_reply_to = (_chosen_integration.get('reply_to')
                               or _chosen_integration.get('email_user', '')
                               or (user.first_name or ''))
            _gmail_json = {
                'from': f"{sender_name} <outreach@asibiont.com>",
                'to': [to_clean],
                'subject': subject,
                'text': body,
            }
            try:
                _gmail_json['html'] = _build_email_html(_text_to_email_html(body), sender_name=sender_name)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            if _gmail_reply_to and '@' in _gmail_reply_to:
                _gmail_json['reply_to'] = [_gmail_reply_to]
            try:
                async with _aiohttp.ClientSession() as _gm_http:
                    _gm_resp = await _gm_http.post(
                        'https://api.resend.com/emails',
                        headers={'Authorization': f'Bearer {_srv_rk}', 'Content-Type': 'application/json'},
                        json=_gmail_json,
                        timeout=_aiohttp.ClientTimeout(total=15),
                    )
                    _gm_data = await _gm_resp.json()
                    if _gm_resp.status not in (200, 201):
                        return f" Ошибка отправки (Gmail через сервер): {_gm_data.get('message', str(_gm_data))}"
            except Exception as _gm_e:
                return f" Ошибка отправки (Gmail): {_gm_e}"
            logger.info(f'[SEND_EMAIL] Sent via server Resend (Gmail Reply-To: {_gmail_reply_to}) to {to_clean}')
            try:
                from models import EmailOutreach as _EO_log_g
                from models import EmailCampaign as _EC_log_g
                import datetime as _dt_mod_g
                _now_g = _dt_mod_g.datetime.now(_dt_mod_g.timezone.utc)
                # Ищем личную кампанию для этого gmail-адреса отправителя
                _camp_g = session.query(_EC_log_g).filter_by(
                    user_id=user.id, status='personal',
                    sender_email=_gmail_reply_to
                ).first()
                if not _camp_g:
                    _camp_g = _EC_log_g(
                        user_id=user.id, name='Личная почта',
                        goal='', target_audience='', offer='',
                        sender_name=sender_name, sender_email=_gmail_reply_to,
                        status='personal', daily_limit=50, max_emails=0,
                        emails_sent=0, emails_replied=0,
                    )
                    session.add(_camp_g)
                    session.flush()
                # Обновляем существующий outreach или создаём новый
                # (уникальный индекс: campaign_id + recipient_email)
                _eo_g = session.query(_EO_log_g).filter_by(
                    campaign_id=_camp_g.id, recipient_email=to_clean
                ).first()
                if _eo_g:
                    _eo_g.subject = subject
                    _eo_g.body = body
                    _eo_g.status = 'sent'
                    _eo_g.sent_at = _now_g
                else:
                    session.add(_EO_log_g(
                        campaign_id=_camp_g.id, user_id=user.id,
                        recipient_email=to_clean, subject=subject, body=body,
                        status='sent', sent_at=_now_g,
                    ))
                    _camp_g.emails_sent = (_camp_g.emails_sent or 0) + 1
                session.commit()
            except Exception as _log_err_g:
                logger.warning(f'[SEND_EMAIL] Campaign log error: {_log_err_g}')
                try:
                    session.rollback()
                except Exception:
                    pass
            # Не создаем AgentActivityLog - письмо уже залогировано в EmailOutreach кампании
            _reply_hint = f" (ответы придут на {_gmail_reply_to})" if _gmail_reply_to and '@' in _gmail_reply_to else ''
            return f" Письмо отправлено на {to_clean} (Gmail){_reply_hint}"

        # Для Resend: проверяем что from-адрес задан и валиден
        if _chosen_integration.get('type') == 'resend' and '@' not in (sender_email or ''):
            return (
                " Для Resend не задан адрес отправителя.\n"
                "Добавь в настройках агента: RESEND_FROM=noreply@твой-домен.com\n"
                "(домен должен быть верифицирован в Resend dashboard)"
            )

        # Нормализация: удалить пробелы, lowercase
        to_clean = to.strip().lower()

        # MX-проверка домена
        mx_valid, mx_err = _validate_email_domain(to_clean)
        if not mx_valid:
            return f" {mx_err}"

        # Простой дневной лимит для прямых писем: 50 отправок/день
        from models import EmailOutreach as _EO_check
        from datetime import datetime as _dt_limit, timezone as _tz_limit
        import pytz as _pytz_limit
        _user_tz_p = _pytz_limit.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
        _today_start = _dt_limit.now(_user_tz_p).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz_limit.utc)
        from sqlalchemy import func as _func_limit
        _sent_today = session.query(_func_limit.count(_EO_check.id)).filter(
            _EO_check.user_id == user.id,
            _EO_check.sent_at >= _today_start,
        ).scalar() or 0
        if _sent_today >= 50:
            return f" Достигнут дневной лимит: {_sent_today} писем отправлено сегодня (макс. 50). Продолжим завтра."


        from config import WEB_APP_URL
        _unsub_url = f"{WEB_APP_URL}/terms#unsubscribe"

        resend_id = ''
        try:
            if _chosen_integration and _chosen_integration.get('type') == 'smtp':
                # ── Отправка через личную почту пользователя (SMTP) ────────
                import smtplib as _smtplib
                from email.mime.text import MIMEText as _MIMEText
                from email.mime.multipart import MIMEMultipart as _MIMEMultipart
                import asyncio as _aio_smtp

                _smtp_host = _chosen_integration['smtp_host']
                _smtp_port = _chosen_integration['smtp_port']
                _smtp_user = _chosen_integration['email_user']
                _smtp_pass = _chosen_integration['email_pass'].replace(' ', '')
                _from_label = _chosen_integration['label']

                def _smtp_send_personal():
                    import ssl as _ssl
                    msg = _MIMEMultipart('alternative')
                    msg['From'] = f"{sender_name} <{_smtp_user}>"
                    msg['To'] = to_clean
                    msg['Subject'] = subject
                    msg.attach(_MIMEText(body, 'plain', 'utf-8'))
                    try:
                        _html_smtp = _build_email_html(_text_to_email_html(body), sender_name=sender_name)
                        msg.attach(_MIMEText(_html_smtp, 'html', 'utf-8'))
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    _ssl_ctx = _ssl.create_default_context()
                    # STARTTLS (порт 587) — работает на Railway.
                    # Порт 465 (SMTP_SSL) блокируется хостингом на уровне сети.
                    with _smtplib.SMTP(_smtp_host, _smtp_port, timeout=30) as s:
                        s.ehlo()
                        s.starttls(context=_ssl_ctx)
                        s.ehlo()
                        s.login(_smtp_user, _smtp_pass)
                        s.sendmail(_smtp_user, to_clean, msg.as_string())

                loop = _aio_smtp.get_running_loop()
                # Перед SMTP пробуем Gmail OAuth если текущая интеграция — не oauth,
                # но oauth доступен (на случай если выбрали SMTP а oauth есть)
                _smtp_net_err = None  # сетевая ошибка → будет Resend fallback
                try:
                    await _aio_smtp.wait_for(
                        loop.run_in_executor(None, _smtp_send_personal),
                        timeout=35.0
                    )
                except _aio_smtp.TimeoutError:
                    _smtp_net_err = f"Таймаут ({_from_label}): сервер не ответил за 35 сек."
                except Exception as _smtp_err:
                    _smtp_msg = str(_smtp_err)
                    # Gmail: 535 = неверный app password — это не сетевая ошибка, сразу возвращаем
                    if '535' in _smtp_msg or 'Username and Password not accepted' in _smtp_msg:
                        return (
                            f" Gmail не принял пароль. Нужен App Password, а не обычный пароль.\n"
                            f"Зайди в Google Account → Security → App Passwords → создай пароль для 'Mail'.\n"
                            f"Вставь его в настройки агента: GMAIL_PASS=xxxx xxxx xxxx xxxx"
                        )
                    _smtp_net_err = f"{_from_label}: {_smtp_msg}"

                if _smtp_net_err:
                    # ── Автоматический fallback на Resend (Railway блокирует SMTP) ──
                    logger.warning(f"[SEND_EMAIL] SMTP failed ({_smtp_net_err}), trying Resend fallback")
                    # 1. Ищем Resend-интеграцию пользователя
                    _resend_fallback_key = None
                    _resend_fallback_from = None
                    for _ri in _email_integrations:
                        if _ri.get('type') == 'resend' and _ri.get('resend_key') and _ri.get('email_user') and '@' in _ri['email_user']:
                            _resend_fallback_key = _ri['resend_key']
                            _resend_fallback_from = _ri['email_user']
                            break
                    if _resend_fallback_key and _resend_fallback_from:
                        try:
                            async with _aiohttp.ClientSession() as _fb_http:
                                _fb_resp = await _fb_http.post(
                                    'https://api.resend.com/emails',
                                    headers={
                                        'Authorization': f'Bearer {_resend_fallback_key}',
                                        'Content-Type': 'application/json',
                                    },
                                    json={
                                        'from': f"{sender_name} <{_resend_fallback_from}>",
                                        'to': [to_clean],
                                        'subject': subject,
                                        'text': body,
                                    },
                                    timeout=_aiohttp.ClientTimeout(total=15),
                                )
                                _fb_data = await _fb_resp.json()
                                if _fb_resp.status in (200, 201):
                                    resend_id = _fb_data.get('id', '')
                                    sender_email = _resend_fallback_from
                                    logger.info(f'[SEND_EMAIL] Sent via Resend fallback ({_resend_fallback_from}) to {to_clean}')
                                    # не возвращаем ошибку — письмо дошло
                                else:
                                    _fb_err = _fb_data.get('message', str(_fb_data))
                                    return f" Ошибка отправки через {_from_label} (SMTP): {_smtp_net_err}\n Резервный Resend тоже не сработал: {_fb_err}"
                        except Exception as _fb_exc:
                            return f" Ошибка отправки через {_from_label} (SMTP): {_smtp_net_err}\n Резервный Resend тоже не сработал: {_fb_exc}"
                    else:
                        return (
                            f" Не удалось отправить через {_from_label} (SMTP): {_smtp_net_err}\n\n"
                            f"Варианты решения:\n"
                            f"• Gmail: убедись, что GMAIL_PASS — это App Password (не обычный пароль)\n"
                            f"• Добавь Resend-интеграцию: RESEND_API_KEY=re_... и RESEND_FROM=noreply@домен.com"
                        )
                else:
                    # Обновляем sender_email чтобы лог показывал реальный адрес
                    sender_email = _smtp_user
                    logger.info(f'[SEND_EMAIL] Sent via {_from_label} SMTP from {_smtp_user} to {to_clean}')
            elif _chosen_integration.get('type') == 'resend':
                # ── Отправка через личный Resend ключ пользователя ────────
                _user_resend_key = _chosen_integration['resend_key']
                async with _aiohttp.ClientSession() as http:
                    from_header = f"{sender_name} <{sender_email}>"
                    resp = await http.post(
                        'https://api.resend.com/emails',
                        headers={
                            'Authorization': f'Bearer {_user_resend_key}',
                            'Content-Type': 'application/json',
                        },
                        json={
                            'from': from_header,
                            'to': [to_clean],
                            'subject': subject,
                            'text': body,
                            'html': _build_email_html(_text_to_email_html(body), sender_name=sender_name),
                            'headers': {'List-Unsubscribe': f'<{_unsub_url}>'},
                        },
                        timeout=_aiohttp.ClientTimeout(total=15),
                    )
                    resp_data = await resp.json()
                    if resp.status not in (200, 201):
                        err = resp_data.get('message', str(resp_data))
                        return f" Ошибка Resend API: {err}"
                    resend_id = resp_data.get('id', '')
                    logger.info(f'[SEND_EMAIL] Sent via user Resend from {redact_email(sender_email)} to {redact_email(to_clean)}')
        except Exception as e:
            return f" Ошибка отправки: {str(e)}"

        # Anti-spam задержка (только для Resend, не для личного SMTP)
        if _chosen_integration and _chosen_integration.get('type') == 'resend':
            import asyncio as _asyncio_delay
            await _asyncio_delay.sleep(10)

        # --- Сохраняем EmailOutreach для трекинга ответов через webhook ---
        try:
            from models import EmailCampaign as _EmailCampaign, EmailOutreach as _EmailOutreach
            from datetime import datetime as _dt2, timezone as _tz2
            # Ищем скрытую служебную кампанию для личных писем (status='personal')
            # НЕ используем активные кампании — они принадлежат пользователю
            campaign = session.query(_EmailCampaign).filter_by(
                user_id=user.id, status='personal'
            ).first()
            if not campaign:
                campaign = _EmailCampaign(
                    user_id=user.id,
                    name='Личная почта',
                    goal='Служебная запись для личных писем',
                    target_audience='',
                    offer='',
                    sender_name=sender_name,
                    sender_email=sender_email,
                    status='personal',  # скрыто от UI и ИИ
                    daily_limit=50,
                    max_emails=0,
                )
                session.add(campaign)
                session.flush()
            now_utc = _dt2.now(_tz2.utc)
            # Обновляем или создаём запись outreach (unique: campaign_id + recipient_email)
            _eo_existing = session.query(_EmailOutreach).filter_by(
                campaign_id=campaign.id, recipient_email=to_clean
            ).first()
            if _eo_existing:
                _eo_existing.subject = subject
                _eo_existing.body = body
                _eo_existing.status = 'sent'
                _eo_existing.sent_at = now_utc
                if resend_id:
                    _eo_existing.resend_id = resend_id
                _eo_saved = _eo_existing
            else:
                _eo_new = _EmailOutreach(
                    campaign_id=campaign.id,
                    user_id=user.id,
                    recipient_email=to_clean,
                    subject=subject,
                    body=body,
                    status='sent',
                    resend_id=resend_id,
                    sent_at=now_utc,
                )
                session.add(_eo_new)
                campaign.emails_sent = (campaign.emails_sent or 0) + 1
                _eo_saved = _eo_new
            session.commit()
            logger.info(f"[SEND_EMAIL] Outreach saved for {to_clean} (campaign #{campaign.id})")
            # Контакт НЕ создаём при отправке — только при реальной переписке
            # (reply_to_outreach_email, negotiate_by_email) или вручную.
        except Exception as _e:
            logger.warning(f"[SEND_EMAIL] Failed to save outreach record: {_e}")
            session.rollback()

        # Не создаем AgentActivityLog - письмо уже залогировано в EmailOutreach кампании со status='personal'
        # Кампании отображаются отдельно в интерфейсе, не загромождая хронологию каждым письмом

        lang = _get_lang(user_id)
        _from_info = f' (от {sender_email})' if _chosen_integration else ''
        if lang == 'en':
            _from_en = f' from {sender_email}' if _chosen_integration else ''
            return f" Email sent to {to_clean}{_from_en}\nSubject: {subject}"
        return f" Email отправлен на {to_clean}{_from_info}\nТема: {subject}"
    except Exception as e:
        logger.error(f"[SEND_EMAIL] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def save_email_contact(
    email: str = None,
    name: str = None,
    company: str = None,
    position: str = None,
    notes: str = None,
    source: str = 'manual',
    status: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Сохранить email-контакт в справочник пользователя."""
    if not session:
        session = Session()
        close_session = True
    try:
        from models import User, EmailContact
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        email_clean = (email or '').strip().lower()
        if not email_clean or '@' not in email_clean:
            return " Некорректный email"

        # Блокируем фейковые/placeholder домены — агент не должен придумывать email
        _FAKE_DOMAINS = {
            'example.com', 'example.org', 'example.net',
            'test.com', 'test.ru', 'test.org',
            'fake.com', 'fake.ru', 'placeholder.com',
            'domain.com', 'email.com', 'yourdomain.com',
        }
        # Домены сервисов — email не доставляется, нет смысла сохранять
        _SERVICE_DOMAINS = {
            'linkedin.com', 'users.noreply.github.com',
            'reply.github.com', 'notifications.github.com',
            'facebook.com', 'twitter.com', 'instagram.com',
        }
        _email_domain = email_clean.split('@')[-1] if '@' in email_clean else ''
        if _email_domain in _FAKE_DOMAINS:
            return (
                f"⛔ {email_clean} — это placeholder/фейковый адрес. "
                "Сохраняй только РЕАЛЬНЫЕ email, найденные через поиск или входящие письма. "
                "НЕ придумывай адреса из имён пользователей."
            )
        if _email_domain in _SERVICE_DOMAINS:
            return (
                f"⛔ {email_clean} — адрес сервиса {_email_domain}, email не доставляется. "
                "Найди реальный рабочий email этого человека через web_search."
            )

        # Блокируем generic/корпоративные адреса
        if _is_generic_email(email_clean):
            return f" {email_clean} — это корпоративный/generic адрес. Сохраняй только личные email конкретных людей."

        # ── GUARD: не сохранять адреса собственной платформы (asibiont.com, resend bounce и т.п.) ──
        _OWN_PLATFORM_DOMAINS = {'asibiont.com'}
        if _email_domain in _OWN_PLATFORM_DOMAINS:
            return f"⛔ {email_clean} — это адрес платформы ASI Biont, не внешний контакт. Не сохраняй."

        # ── GUARD: не сохранять свой собственный email или IMAP-аккаунт агента ──
        _user_email_own = (getattr(user, 'email', '') or '').strip().lower()
        _own_emails = set()
        if _user_email_own:
            _own_emails.add(_user_email_own)
        try:
            from models import UserAgent as _UA_sec
            for _ag_sec in session.query(_UA_sec).filter(
                _UA_sec.author_id == user.id,
                _UA_sec.user_api_keys.isnot(None),
            ).all():
                for _ln_sec in (_ag_sec.user_api_keys or '').splitlines():
                    _ln_sec = _ln_sec.strip()
                    if _ln_sec.upper().startswith('GMAIL_USER=') or _ln_sec.upper().startswith('IMAP_USER='):
                        _imap_val = _ln_sec.split('=', 1)[1].strip().lower()
                        if _imap_val and '@' in _imap_val:
                            _own_emails.add(_imap_val)
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        if email_clean in _own_emails:
            return (f" Нельзя сохранять собственный адрес ({email_clean}) как контакт — "
                    f"это ваша почта или почта агента. Найди внешний email реального человека.")

        # ── GUARD: не приглашать/сохранять уже зарегистрированного пользователя платформы ──
        _existing_platform_user = session.query(User).filter(
            User.id != user.id,
            User.email == email_clean,
        ).first()
        if _existing_platform_user:
            return (f"⚠️ {email_clean} — уже зарегистрирован на платформе ASI Biont "
                    f"(пользователь @{_existing_platform_user.username or _existing_platform_user.first_name or '?'}). "
                    f"Приглашать его бессмысленно — он уже с нами. Ищи НОВЫХ людей за пределами платформы.")

        # ── GUARD: не сохранять контакт с telegram_id владельца ──
        _owner_tg_id = str(user.telegram_id) if user.telegram_id else ''
        _owner_username = (user.username or '').strip().lower()
        _owner_first = (user.first_name or '').strip().lower()
        _name_clean = (name or '').strip().lower()
        # Если имя контакта содержит telegram_id владельца — это он сам
        if _owner_tg_id and _owner_tg_id in _name_clean:
            return (f"⛔ Это telegram_id владельца ({_owner_tg_id}). "
                    "Нельзя сохранять владельца как контакт. Ищи внешних людей.")
        # Если имя точно совпадает с username или first_name владельца и email тоже его
        if _owner_username and _name_clean == _owner_username:
            logger.warning("[SELF-SAVE] agent tried to save owner username=%s as contact", _owner_username)

        # Check duplicate
        existing = session.query(EmailContact).filter_by(
            user_id=user.id, email=email_clean
        ).first()
        if existing:
            # Update existing
            _prev_status = existing.status or 'new'
            if name:
                existing.name = name.strip()
            if company:
                existing.company = company.strip()
            if position:
                existing.position = position.strip()
            if notes:
                existing.notes = notes.strip()
            if status:
                # Запрет понижать или вручную ставить replied/interested — только система
                _RESERVED_STATUSES = ('replied', 'interested')
                if status in _RESERVED_STATUSES and _prev_status not in _RESERVED_STATUSES:
                    pass  # игнорируем: агент не может вручную повысить до replied/interested
                else:
                    existing.status = status
            session.commit()
            _cur_status = existing.status or _prev_status
            # Информативный ответ: агент видит реальное состояние и знает следующий шаг
            _contact_label = f"{existing.name or email_clean} ({email_clean})"
            if _cur_status == 'contacted':
                try:
                    import datetime as _dt_ec
                    _age = (
                        _dt_ec.datetime.now(_dt_ec.timezone.utc) -
                        (existing.updated_at or existing.created_at).replace(
                            tzinfo=getattr((existing.updated_at or existing.created_at), 'tzinfo', None) or _dt_ec.timezone.utc
                        )
                    ).days
                except Exception:
                    _age = 0
                if _age >= 3:
                    return (
                        f"ℹ️ {_contact_label} — статус contacted, уже {_age} д. без ответа. "
                        f"Стоит отправить follow-up: новый угол/ценность, не копия первого письма. "
                        f"Используй send_outreach_email с другой темой."
                    )
                return (
                    f"ℹ️ {_contact_label} — статус contacted (письмо отправлено {_age} д. назад). "
                    f"Подожди ответа. Если не ответит через {3 - _age} д. — отправь follow-up с иным pitch."
                )
            if _cur_status in ('replied', 'interested'):
                return (
                    f"✅ {_contact_label} — статус {_cur_status}, уже в диалоге! "
                    f"Это твой приоритет: negotiate_by_email — développe личный диалог, "
                    f"выясни потребность, предложи конкретный следующий шаг."
                )
            if _cur_status == 'unsubscribed':
                return (
                    f"🔕 {_contact_label} — статус unsubscribed. "
                    f"Прямые письма не нужны. Если контакт ценен — взаимодействуй через другой канал "
                    f"(LinkedIn, сообщество, пост) или оставь для органического интереса."
                )
            # Статус 'new' или другой: контакт в базе, ещё не написали — это приоритет
            return (
                f"ℹ️ {_contact_label} уже в базе (статус: {_cur_status} — письмо не отправлялось). "
                f"→ Следующий шаг: send_outreach_email с персональным питчем."
            )

        # Авто-определение source по контексту (если агент не указал)
        _effective_source = source or 'manual'
        if _effective_source in ('manual', 'outreach'):
            _all_fields = f"{notes or ''} {company or ''} {position or ''}".lower()
            if 'github' in _all_fields or 'repos,' in _all_fields or 'followers' in _all_fields:
                _effective_source = 'github'
            elif 'web_search' in _all_fields or 'найден через поиск' in _all_fields:
                _effective_source = 'web_search'

        # Статусы 'interested'/'replied' нельзя ставить вручную при создании контакта.
        # Они устанавливаются только системой при получении реального email-ответа.
        _MANUAL_FORBIDDEN_STATUSES = ('interested', 'replied')
        _effective_status = status or 'new'
        if _effective_status in _MANUAL_FORBIDDEN_STATUSES:
            _effective_status = 'new'
            logger.info('[SAVE_CONTACT] Blocked manual status=%s → reset to new for %s', status, email_clean)

        contact = EmailContact(
            user_id=user.id,
            email=email_clean,
            name=(name or '').strip() or None,
            company=(company or '').strip() or None,
            position=(position or '').strip() or None,
            notes=(notes or '').strip() or None,
            source=_effective_source,
            # Дефолт 'new' — агент сохраняет найденный контакт, это НЕ означает что он ответил
            status=_effective_status,
        )
        session.add(contact)
        session.commit()
        return f" Контакт сохранён: {email_clean}" + (f" ({name.strip()})" if name else "")
    except Exception as e:
        logger.error(f"[SAVE_EMAIL_CONTACT] Error: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def update_email_contact_status(
    email: str = None,
    status: str = None,
    reason: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Обновить статус email-контакта (напр. unsubscribed) и почистить follow-up."""
    if not session:
        session = Session()
        close_session = True
    try:
        from models import User, EmailContact, EmailOutreach
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        email_clean = (email or '').strip().lower()
        if not email_clean or '@' not in email_clean:
            return "Некорректный email"
        valid_statuses = ('new', 'contacted', 'replied', 'interested', 'unsubscribed', 'bounced')
        if status not in valid_statuses:
            return f"Некорректный статус. Допустимые: {', '.join(valid_statuses)}"

        contact = session.query(EmailContact).filter_by(
            user_id=user.id, email=email_clean
        ).first()
        if not contact:
            return f"Контакт {email_clean} не найден"

        old_status = contact.status
        contact.status = status
        if reason:
            existing_notes = (contact.notes or '').strip()
            contact.notes = f"{existing_notes}\n[{status}] {reason}".strip()

        # При unsubscribed — отменяем все follow-up
        if status == 'unsubscribed':
            outreaches = session.query(EmailOutreach).filter(
                EmailOutreach.recipient_email == email_clean,
                EmailOutreach.user_id == user.id,
                EmailOutreach.next_follow_up_at.isnot(None),
            ).all()
            for o in outreaches:
                o.next_follow_up_at = None
                o.status = 'unsubscribed'

        session.commit()
        msg = f"Контакт {email_clean}: {old_status} → {status}"
        if status == 'unsubscribed':
            msg += ". Follow-up отменены. Больше не пишем этому контакту."
        return msg
    except Exception as e:
        logger.error(f"[UPDATE_EMAIL_CONTACT_STATUS] Error: {e}", exc_info=True)
        session.rollback()
        return f"Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def list_email_contacts(
    status_filter: str = 'all',
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Список email-контактов из справочника пользователя."""
    if not session:
        session = Session()
        close_session = True
    try:
        from models import User, EmailContact
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        query = session.query(EmailContact).filter_by(user_id=user.id)
        if status_filter and status_filter != 'all':
            query = query.filter_by(status=status_filter)
        total_count = query.count()
        contacts = query.order_by(EmailContact.created_at.desc()).limit(15).all()

        if not contacts:
            return " Справочник контактов пуст. Добавь через save_email_contact или на дашборде → Контакты."

        lines = [f" Email-контакты (показано {len(contacts)} из {total_count}):"]
        for c in contacts:
            parts = [c.email]
            if c.name:
                parts.append(c.name)
            if c.company:
                parts.append(c.company)
            status_emoji = {'new': '🆕', 'contacted': '', 'replied': '', 'interested': '', 'bounced': '', 'unsubscribed': ''}.get(c.status, '')
            line = f"{status_emoji} {' — '.join(parts)}"
            if c.notes:
                line += f" ({c.notes[:50]})"
            lines.append(line)
        if total_count > 15:
            lines.append(f"\n... и ещё {total_count - 15} контактов. Используй send_email / start_email_campaign для работы с ними.")
        lines.append("\n⚠️ Не пересылай этот список пользователю. Используй контакты для отправки писем.")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[LIST_EMAIL_CONTACTS] Error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def publish_to_discord(
    content: str,
    image_url: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
    force: bool = False,
):
    """ ПУБЛИКАЦИЯ В DISCORD канал пользователя через webhook.
    Требования: discord_webhook должен быть указан в профиле (Настройки → Discord).
    """
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        if not user.discord_webhook:
            return (
                " Discord webhook не настроен.\n"
                "Чтобы публиковать в Discord канал:\n"
                "1. Открой нужный канал в Discord → Настройки канала → Интеграции → Webhooks\n"
                "2. Создай webhook и скопируй URL\n"
                "3. Вставь URL в дашборде: Настройки профиля → Discord webhook\n"
                "Ссылка: https://asibiont.com/dashboard"
            )

        if not user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
            return " Некорректный Discord webhook URL. Убедись, что URL начинается с https://discord.com/api/webhooks/"

        # ── GUARD: не публиковать внутренние отчёты в публичный канал ──
        _content_lower = (content or '').lower()
        _INTERNAL_MARKERS = (
            'проверил', 'проверила', 'обновила прогресс', 'обновил прогресс',
            'update_goal_progress', 'goal_progress', 'save_email_contact',
            'отправил письм', 'отправила письм', 'нашёл контакт', 'нашла контакт',
            'сохранила контакт', 'сохранил контакт', 'добавила в crm', 'добавил в crm',
            'делегиру', 'delegate[',
        )
        _PUBLIC_MARKERS = (
            'тренд', 'обзор', 'кейс', 'инсайт', 'аналитик', 'исследован',
            'стратеги', 'индустри', 'рынок', 'технолог',
        )
        _has_internal = sum(1 for m in _INTERNAL_MARKERS if m in _content_lower)
        _has_public = sum(1 for m in _PUBLIC_MARKERS if m in _content_lower)
        if _has_internal >= 2 and _has_public == 0:
            logger.warning('[DISCORD_GUARD] Blocked internal report from public channel: %.100s', content)
            return (
                "⛔ Этот текст похож на внутренний отчёт, а не на публичный пост. "
                "Discord-канал — для аудитории: инсайты, кейсы, аналитика. "
                "Переформулируй контент как экспертный пост для подписчиков."
            )

        # Лимит: 1 пост в Discord в день (можно обойти force=True если пользователь явно просит)
        if not force:
            import pytz as _pytz_dc
            import datetime as _dt_dc
            _utz_dc = _pytz_dc.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
            _today_dc = _dt_dc.datetime.now(_utz_dc).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_pytz_dc.UTC).replace(tzinfo=None)
            try:
                from models import AgentActivityLog as _AAL
                _discord_today = session.query(_AAL).filter(
                    _AAL.user_id == user.id,
                    _AAL.activity_type == 'post_discord',
                    _AAL.created_at >= _today_dc,
                    _AAL.status == 'published',
                ).count()
                if _discord_today >= 3:
                    return f" Сегодня в Discord уже {_discord_today} постов (anti-spam лимит — 3 в день)."
            except Exception as _lim_e:
                logger.warning(f"[DISCORD_LIMIT] {_lim_e}")

        import aiohttp as _aiohttp

        # Sanitize token hallucinations
        from ai_integration.conversation_history import sanitize_token_hallucinations
        content = sanitize_token_hallucinations(content)

        # Если есть картинка — публикуем через embed
        if image_url:
            payload = {
                "content": content,
                "embeds": [{"image": {"url": image_url}}]
            }
        else:
            payload = {"content": content}

        async with _aiohttp.ClientSession() as http:
            resp = await http.post(
                user.discord_webhook,
                json=payload,
                timeout=_aiohttp.ClientTimeout(total=15)
            )
            if resp.status in (200, 204):
                try:
                    from models import AgentActivityLog
                    log = AgentActivityLog(
                        user_id=user.id,
                        activity_type='post_discord',
                        title=content[:80] + ('...' if len(content) > 80 else ''),
                        content=content,
                        target='Discord канал',
                        status='published',
                    )
                    session.add(log)
                    session.commit()
                except Exception as _le:
                    logger.warning(f"[DISCORD] Failed to log: {_le}")
                server = getattr(user, 'discord_server_name', None) or 'Discord канал'
                img_note = " с изображением" if image_url else ""
                return f" Пост опубликован{img_note} в {server}"
            else:
                err = await resp.text()
                return f" Ошибка Discord webhook: {resp.status} — {err[:200]}"
    except Exception as e:
        logger.error(f"[PUBLISH_DISCORD] Error: {e}", exc_info=True)
        return f" Ошибка публикации в Discord: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── publish_to_vk ──────────────────────────────────────────────────────
async def publish_to_vk(
    content: str,
    image_url: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Публикация поста на стене ВКонтакте через VK API."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        vk_token, vk_owner_id = None, None
        try:
            from models import UserAgent as _UA_vk
            for _ag in session.query(_UA_vk).filter(
                _UA_vk.author_id == user.id, _UA_vk.status != 'disabled',
                _UA_vk.user_api_keys.isnot(None),
            ).all():
                _env = {}
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        _env[_k.strip().upper()] = _v.strip()
                if _env.get('VK_TOKEN'):
                    vk_token = _env['VK_TOKEN']
                    vk_owner_id = _env.get('VK_OWNER_ID', '')
                    break
        except Exception as _e:
            logger.debug(f"[VK] agent lookup: {_e}")

        if not vk_token:
            return (
                "VK_TOKEN не настроен.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "VK_TOKEN=ваш_токен\n"
                "VK_OWNER_ID=id_страницы_или_группы\n"
                "Получить: vk.com/dev → Мои приложения → Standalone → Получить токен"
            )

        import urllib.parse
        import aiohttp as _aiohttp
        url = (
            f"https://api.vk.com/method/wall.post?"
            f"owner_id={vk_owner_id}&message={urllib.parse.quote(content)}"
            f"&access_token={vk_token}&v=5.199"
        )
        if image_url:
            url += f"&attachments={urllib.parse.quote(image_url)}"

        async with _aiohttp.ClientSession() as http:
            async with http.get(url, timeout=_aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()

        if 'error' in data:
            return f"Ошибка VK: {data['error'].get('error_msg', str(data['error']))}"

        post_id = data.get('response', {}).get('post_id', '?')
        try:
            from models import AgentActivityLog
            log = AgentActivityLog(
                user_id=user.id, activity_type='post_vk',
                title=content[:80], content=content,
                target=f'VK {vk_owner_id}', status='published',
            )
            session.add(log)
            session.commit()
        except Exception as _le:
            logger.warning(f"[VK] log: {_le}")

        return f"Пост #{post_id} опубликован в ВКонтакте"
    except Exception as e:
        logger.error(f"[PUBLISH_VK] Error: {e}", exc_info=True)
        return f"Ошибка публикации в VK: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── publish_to_twitter ─────────────────────────────────────────────────
async def publish_to_twitter(
    content: str,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Публикация твита в Twitter/X через OAuth 1.0a."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        tw_keys = {}
        try:
            from models import UserAgent as _UA_tw
            for _ag in session.query(_UA_tw).filter(
                _UA_tw.author_id == user.id, _UA_tw.status != 'disabled',
                _UA_tw.user_api_keys.isnot(None),
            ).all():
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        _ku = _k.strip().upper()
                        if _ku.startswith('TWITTER_') or _ku.startswith('X_'):
                            tw_keys[_ku] = _v.strip()
                if tw_keys:
                    break
        except Exception as _e:
            logger.debug(f"[TWITTER] agent lookup: {_e}")

        api_key = tw_keys.get('TWITTER_API_KEY') or tw_keys.get('X_API_KEY', '')
        api_secret = tw_keys.get('TWITTER_API_SECRET') or tw_keys.get('X_API_SECRET', '')
        access_token = tw_keys.get('TWITTER_ACCESS_TOKEN') or tw_keys.get('X_ACCESS_TOKEN', '')
        access_secret = tw_keys.get('TWITTER_ACCESS_SECRET') or tw_keys.get('X_ACCESS_SECRET', '')

        if not all([api_key, api_secret, access_token, access_secret]):
            return (
                "Twitter API не настроен.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "TWITTER_API_KEY=...\nTWITTER_API_SECRET=...\n"
                "TWITTER_ACCESS_TOKEN=...\nTWITTER_ACCESS_SECRET=...\n"
                "Получить: developer.twitter.com → Projects & Apps"
            )

        if len(content) > 280:
            content = content[:277] + '...'

        import hashlib, hmac, time, urllib.parse, uuid, json as _json
        import aiohttp as _aiohttp

        # OAuth 1.0a signature
        method = 'POST'
        url = 'https://api.twitter.com/2/tweets'
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time()))

        oauth_params = {
            'oauth_consumer_key': api_key,
            'oauth_nonce': nonce,
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': timestamp,
            'oauth_token': access_token,
            'oauth_version': '1.0',
        }
        sig_base = '&'.join([
            method,
            urllib.parse.quote(url, safe=''),
            urllib.parse.quote('&'.join(f'{k}={urllib.parse.quote(v, safe="")}' for k, v in sorted(oauth_params.items())), safe=''),
        ])
        sig_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(access_secret, safe='')}"
        import base64
        signature = base64.b64encode(hmac.new(sig_key.encode(), sig_base.encode(), hashlib.sha1).digest()).decode()

        auth_header = 'OAuth ' + ', '.join(
            f'{k}="{urllib.parse.quote(v, safe="")}"' for k, v in
            {**oauth_params, 'oauth_signature': signature}.items()
        )

        async with _aiohttp.ClientSession() as http:
            async with http.post(
                url,
                json={"text": content},
                headers={
                    'Authorization': auth_header,
                    'Content-Type': 'application/json',
                },
                timeout=_aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if resp.status in (200, 201):
            tweet_id = data.get('data', {}).get('id', '?')
            try:
                from models import AgentActivityLog
                log = AgentActivityLog(
                    user_id=user.id, activity_type='post_twitter',
                    title=content[:80], content=content,
                    target='Twitter/X', status='published',
                )
                session.add(log)
                session.commit()
            except Exception as _le:
                logger.warning(f"[TWITTER] log: {_le}")
            return f"Твит опубликован (ID: {tweet_id})"
        else:
            err = data.get('detail') or data.get('title') or str(data)
            return f"Ошибка Twitter: {err[:200]}"
    except Exception as e:
        logger.error(f"[PUBLISH_TWITTER] Error: {e}", exc_info=True)
        return f"Ошибка публикации в Twitter: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── publish_to_linkedin ────────────────────────────────────────────────
async def publish_to_linkedin(
    content: str,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Публикация поста в LinkedIn через Marketing API."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        li_token = None
        try:
            from models import UserAgent as _UA_li
            for _ag in session.query(_UA_li).filter(
                _UA_li.author_id == user.id, _UA_li.status != 'disabled',
                _UA_li.user_api_keys.isnot(None),
            ).all():
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        if _k.strip().upper() == 'LINKEDIN_ACCESS_TOKEN':
                            li_token = _v.strip()
                            break
                if li_token:
                    break
        except Exception as _e:
            logger.debug(f"[LINKEDIN] agent lookup: {_e}")

        if not li_token:
            return (
                "LINKEDIN_ACCESS_TOKEN не настроен.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "LINKEDIN_ACCESS_TOKEN=ваш_токен\n"
                "Получить: linkedin.com/developers → Create App → OAuth 2.0"
            )

        import aiohttp as _aiohttp
        import json as _json

        # Get user profile URN
        async with _aiohttp.ClientSession() as http:
            async with http.get(
                'https://api.linkedin.com/v2/userinfo',
                headers={'Authorization': f'Bearer {li_token}'},
                timeout=_aiohttp.ClientTimeout(total=10),
            ) as prof_resp:
                if prof_resp.status != 200:
                    return f"Ошибка LinkedIn авторизации: {prof_resp.status}"
                prof_data = await prof_resp.json()

        person_id = prof_data.get('sub', '')
        if not person_id:
            return "Не удалось получить LinkedIn profile ID"

        post_body = {
            "author": f"urn:li:person:{person_id}",
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

        async with _aiohttp.ClientSession() as http:
            async with http.post(
                'https://api.linkedin.com/v2/ugcPosts',
                json=post_body,
                headers={
                    'Authorization': f'Bearer {li_token}',
                    'Content-Type': 'application/json',
                    'X-Restli-Protocol-Version': '2.0.0',
                },
                timeout=_aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 201:
                    try:
                        from models import AgentActivityLog
                        log = AgentActivityLog(
                            user_id=user.id, activity_type='post_linkedin',
                            title=content[:80], content=content,
                            target='LinkedIn', status='published',
                        )
                        session.add(log)
                        session.commit()
                    except Exception as _le:
                        logger.warning(f"[LINKEDIN] log: {_le}")
                    return "Пост опубликован в LinkedIn"
                else:
                    err = await resp.text()
                    return f"Ошибка LinkedIn: {resp.status} — {err[:200]}"
    except Exception as e:
        logger.error(f"[PUBLISH_LINKEDIN] Error: {e}", exc_info=True)
        return f"Ошибка публикации в LinkedIn: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── publish_to_notion ──────────────────────────────────────────────────
async def publish_to_notion(
    title: str,
    content: str,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Создание страницы в Notion через API."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        notion_token, notion_db_id = None, None
        try:
            from models import UserAgent as _UA_nt
            for _ag in session.query(_UA_nt).filter(
                _UA_nt.author_id == user.id, _UA_nt.status != 'disabled',
                _UA_nt.user_api_keys.isnot(None),
            ).all():
                _env = {}
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        _env[_k.strip().upper()] = _v.strip()
                if _env.get('NOTION_TOKEN'):
                    notion_token = _env['NOTION_TOKEN']
                    notion_db_id = _env.get('NOTION_DB_ID', '')
                    break
        except Exception as _e:
            logger.debug(f"[NOTION] agent lookup: {_e}")

        if not notion_token or not notion_db_id:
            return (
                "NOTION_TOKEN / NOTION_DB_ID не настроены.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "NOTION_TOKEN=secret_xxx\n"
                "NOTION_DB_ID=id_базы_данных\n"
                "Notion → Settings → Integrations → New Integration"
            )

        import aiohttp as _aiohttp

        children = []
        for p in content.split('\n\n'):
            p = p.strip()
            if p:
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": p[:2000]}}]}
                })
        if not children:
            children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}}]

        page_data = {
            "parent": {"database_id": notion_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": title}}]},
            },
            "children": children[:100],
        }

        async with _aiohttp.ClientSession() as http:
            async with http.post(
                'https://api.notion.com/v1/pages',
                json=page_data,
                headers={
                    'Authorization': f'Bearer {notion_token}',
                    'Notion-Version': '2022-06-28',
                    'Content-Type': 'application/json',
                },
                timeout=_aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if resp.status in (200, 201):
            page_url = data.get('url', '')
            try:
                from models import AgentActivityLog
                log = AgentActivityLog(
                    user_id=user.id, activity_type='post_notion',
                    title=title[:80], content=content[:300],
                    target='Notion', status='published',
                )
                session.add(log)
                session.commit()
            except Exception as _le:
                logger.warning(f"[NOTION] log: {_le}")
            return f"Страница «{title}» создана в Notion: {page_url}"
        else:
            err = data.get('message') or str(data)[:200]
            return f"Ошибка Notion: {err}"
    except Exception as e:
        logger.error(f"[PUBLISH_NOTION] Error: {e}", exc_info=True)
        return f"Ошибка создания страницы в Notion: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── publish_to_youtube ─────────────────────────────────────────────────
async def publish_to_youtube(
    action: str = 'analytics',
    video_id: str = None,
    content: str = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Аналитика YouTube-канала или публикация комментария."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        yt_key, yt_channel = None, None
        try:
            from models import UserAgent as _UA_yt
            for _ag in session.query(_UA_yt).filter(
                _UA_yt.author_id == user.id, _UA_yt.status != 'disabled',
                _UA_yt.user_api_keys.isnot(None),
            ).all():
                _env = {}
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        _env[_k.strip().upper()] = _v.strip()
                if _env.get('YOUTUBE_API_KEY'):
                    yt_key = _env['YOUTUBE_API_KEY']
                    yt_channel = _env.get('YOUTUBE_CHANNEL_ID', '')
                    break
        except Exception as _e:
            logger.debug(f"[YOUTUBE] agent lookup: {_e}")

        if not yt_key:
            return (
                "YOUTUBE_API_KEY не настроен.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "YOUTUBE_API_KEY=AIza...\n"
                "YOUTUBE_CHANNEL_ID=UCxxx...\n"
                "Получить: console.cloud.google.com → YouTube Data API v3"
            )

        import aiohttp as _aiohttp

        if action == 'analytics':
            if not yt_channel:
                return "YOUTUBE_CHANNEL_ID не указан. Добавьте в API-ключи агента."
            async with _aiohttp.ClientSession() as http:
                async with http.get(
                    f'https://www.googleapis.com/youtube/v3/channels?part=statistics,snippet&id={yt_channel}&key={yt_key}',
                    timeout=_aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
            items = data.get('items', [])
            if not items:
                return "Канал не найден. Проверьте YOUTUBE_CHANNEL_ID."
            ch = items[0]
            stats = ch.get('statistics', {})
            name = ch.get('snippet', {}).get('title', '?')
            return (
                f"YouTube: {name}\n"
                f"Подписчиков: {stats.get('subscriberCount', '?')}\n"
                f"Видео: {stats.get('videoCount', '?')}\n"
                f"Просмотров: {stats.get('viewCount', '?')}"
            )
        else:
            return f"Неизвестное действие: {action}. Используйте 'analytics'."
    except Exception as e:
        logger.error(f"[YOUTUBE] Error: {e}", exc_info=True)
        return f"Ошибка YouTube: {str(e)}"
    finally:
        if close_session:
            session.close()


# ── initiate_phone_call ────────────────────────────────────────────────
async def initiate_phone_call(
    to_phone: str,
    message: str,
    language: str = 'ru-RU',
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Инициировать телефонный звонок через Twilio с TTS-сообщением."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        twilio_sid, twilio_token, twilio_from = None, None, None
        try:
            from models import UserAgent as _UA_tw
            for _ag in session.query(_UA_tw).filter(
                _UA_tw.author_id == user.id, _UA_tw.status != 'disabled',
                _UA_tw.user_api_keys.isnot(None),
            ).all():
                _env = {}
                for _ln in (_ag.user_api_keys or '').splitlines():
                    _ln = _ln.strip()
                    if '=' in _ln and not _ln.startswith('#'):
                        _k, _, _v = _ln.partition('=')
                        _env[_k.strip().upper()] = _v.strip()
                if _env.get('TWILIO_ACCOUNT_SID'):
                    twilio_sid = _env['TWILIO_ACCOUNT_SID']
                    twilio_token = _env.get('TWILIO_AUTH_TOKEN', '')
                    twilio_from = _env.get('TWILIO_FROM', '')
                    break
        except Exception as _e:
            logger.debug(f"[CALL] agent lookup: {_e}")

        if not all([twilio_sid, twilio_token, twilio_from]):
            return (
                "Twilio не настроен.\n"
                "Добавьте в настройках агента (API-ключи):\n"
                "TWILIO_ACCOUNT_SID=ACxxx\n"
                "TWILIO_AUTH_TOKEN=xxx\n"
                "TWILIO_FROM=+1234567890\n"
                "Получить: console.twilio.com → Account Info"
            )

        import re
        if not re.match(r'^\+\d{10,15}$', to_phone):
            return f"Некорректный номер: {to_phone}. Формат: +79991234567"

        # Escape XML special chars in message for TwiML
        import html as _html_mod
        safe_message = _html_mod.escape(message)

        twiml = f'<Response><Say language="{language}" voice="alice">{safe_message}</Say></Response>'

        import aiohttp as _aiohttp
        import base64, urllib.parse
        auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Calls.json"

        async with _aiohttp.ClientSession() as http:
            async with http.post(
                url,
                data=urllib.parse.urlencode({
                    'From': twilio_from,
                    'To': to_phone,
                    'Twiml': twiml,
                }),
                headers={
                    'Authorization': f'Basic {auth}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                timeout=_aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()

        if resp.status in (200, 201):
            call_sid = data.get('sid', '?')
            try:
                from models import AgentActivityLog
                log = AgentActivityLog(
                    user_id=user.id, activity_type='phone_call',
                    title=f'Звонок на {to_phone}',
                    content=message[:300],
                    target=to_phone, status='published',
                )
                session.add(log)
                session.commit()
            except Exception as _le:
                logger.warning(f"[CALL] log: {_le}")
            return f"Звонок инициирован на {to_phone} (SID: {call_sid})"
        else:
            err = data.get('message') or str(data)[:200]
            return f"Ошибка Twilio: {err}"
    except Exception as e:
        logger.error(f"[PHONE_CALL] Error: {e}", exc_info=True)
        return f"Ошибка звонка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def generate_image(
    prompt: str,
    style: str = None,
    aspect_ratio: str = "1:1",
    user_id: int = None,
    session=None,
    close_session: bool = True,
    send_to_telegram: bool = True,
) -> str:
    """Генерация изображения через Replicate (Flux). send_to_telegram=False — только URL, без отправки в TG."""
    if not session:
        session = Session()
        close_session = True
    try:
        from config import REPLICATE_API_TOKEN as _platform_replicate_key, TELEGRAM_TOKEN

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден."

        # Личный REPLICATE_API_TOKEN из user_api_keys агентов пользователя имеет приоритет
        REPLICATE_API_TOKEN = _platform_replicate_key
        try:
            from models import UserAgent as _UA_rep
            for _ag_rep in session.query(_UA_rep).filter(
                _UA_rep.author_id == user.id,
                _UA_rep.status != 'disabled',
                _UA_rep.user_api_keys.isnot(None),
            ).all():
                _env_rep = {}
                for _ln_rep in (_ag_rep.user_api_keys or '').splitlines():
                    _ln_rep = _ln_rep.strip()
                    if '=' in _ln_rep and not _ln_rep.startswith('#'):
                        _k_rep, _, _v_rep = _ln_rep.partition('=')
                        _env_rep[_k_rep.strip().upper()] = _v_rep.strip()
                if _env_rep.get('REPLICATE_API_TOKEN'):
                    REPLICATE_API_TOKEN = _env_rep['REPLICATE_API_TOKEN']
                    import logging as _log_rep
                    _log_rep.getLogger(__name__).info(
                        f'[GENERATE_IMAGE] Using personal REPLICATE_API_TOKEN from agent {_ag_rep.name}'
                    )
                    break
        except Exception as _rep_err:
            import logging as _log_rep2
            _log_rep2.getLogger(__name__).debug(f'[GENERATE_IMAGE] Personal Replicate lookup: {_rep_err}')

        if not REPLICATE_API_TOKEN:
            return " Replicate API не настроен. Добавьте REPLICATE_API_TOKEN в настройки агента (API-ключи)."

        full_prompt = f"{prompt}, {style} style" if style else prompt

        import aiohttp as _aiohttp
        import asyncio as _asyncio

        model = "black-forest-labs/flux-schnell"
        input_data = {
            "prompt": full_prompt,
            "aspect_ratio": aspect_ratio or "1:1",
            "width": 550,
            "height": 550,
            "output_format": "webp",
            "output_quality": 80,
        }

        headers = {
            "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
            "Content-Type": "application/json",
            "Prefer": "wait",  # ждём результат синхронно (до 60с)
        }

        async with _aiohttp.ClientSession() as http:
            # Запускаем генерацию
            resp = await http.post(
                f"https://api.replicate.com/v1/models/{model}/predictions",
                headers=headers,
                json={"input": input_data},
                timeout=_aiohttp.ClientTimeout(total=90),
            )
            data = await resp.json()

            if resp.status not in (200, 201):
                err = data.get("detail", str(data))
                return f" Ошибка Replicate: {err}"

            output = data.get("output")
            prediction_id = data.get("id")

            # Если Prefer:wait не сработал — опрашиваем статус
            if output is None and prediction_id:
                for _ in range(30):
                    await _asyncio.sleep(3)
                    poll = await http.get(
                        f"https://api.replicate.com/v1/predictions/{prediction_id}",
                        headers=headers,
                        timeout=_aiohttp.ClientTimeout(total=15),
                    )
                    poll_data = await poll.json()
                    status = poll_data.get("status")
                    if status == "succeeded":
                        output = poll_data.get("output")
                        break
                    elif status in ("failed", "canceled"):
                        err = poll_data.get("error", "Unknown error")
                        return f" Генерация не удалась: {err}"

            if not output:
                return " Изображение не сгенерировано (таймаут)."

            # output — URL или список URL
            image_url = output[0] if isinstance(output, list) else output

            # Отправляем фото в Telegram (только если send_to_telegram=True)
            send_data = {"ok": False}
            if send_to_telegram:
                _photo_payload = {"chat_id": user.telegram_id, "photo": image_url}
                send_resp = await http.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    json=_photo_payload,
                    timeout=_aiohttp.ClientTimeout(total=30),
                )
                send_data = await send_resp.json()

        if send_to_telegram and send_data.get("ok"):
            # Telegram получил фото — возвращаем без URL чтобы не было дублирования
            result_msg = f" Изображение отправлено!"
        else:
            # Web-контекст или Telegram не принял — возвращаем markdown-изображение для рендеринга
            result_msg = f" Готово!\n\n![изображение]({image_url})"

        return result_msg

    except Exception as e:
        logger.error(f"[GENERATE_IMAGE] Error: {e}", exc_info=True)
        return f" Ошибка генерации изображения: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════
# КОНТЕНТ-КАМПАНИИ — автономная публикация постов
# ═══════════════════════════════════════════════════════

async def get_system_status(
    user_id: int = None,
    session=None,
    close_session: bool = True,
) -> dict:
    """Получить текущее состояние всех сервисов и квоты пользователя.

    Используй когда:
    — пользователь спрашивает почему что-то не работает
    — перед началом рассылки или публикации
    — при ошибке email/API чтобы объяснить причину

    Returns структуру:
        {
            'overall': 'ok' | 'degraded',
            'summary': '...',
            'services': {...},
            'email_quota': {'sent_today': N, 'daily_limit': 50, 'remaining': N, 'exhausted': bool},
            'token_balance': {'balance': N, 'low': bool},
        }
    """
    from .service_health import get_all_services_report

    report = get_all_services_report(user_id=user_id)

    # Добавьём остаток токенов
    try:
        from token_service import get_balance
        balance = get_balance(user_id) if user_id else 0
        report['token_balance'] = {
            'balance': balance,
            'low': balance < 50,
        }
    except Exception:
        report['token_balance'] = None

    # Статистика email-кампаний
    if user_id and not report.get('email_quota'):
        try:
            if not session:
                session = Session()
                close_session = True
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                from models import EmailCampaign
                active_campaigns = session.query(EmailCampaign).filter(
                    EmailCampaign.user_id == user.id,
                    EmailCampaign.status == 'active',
                ).count()
                report['active_email_campaigns'] = active_campaigns
        except Exception as _e:
            logger.debug(f"[SYSTEM_STATUS] campaign count error: {_e}")
        finally:
            if close_session and session:
                session.close()

    # ── Формируем человекочитаемый текст (AI не должен показывать raw JSON) ──
    lines = []
    overall = report.get('overall', '?')
    lines.append(f"Общий статус: {'✅ Всё работает' if overall == 'ok' else '⚠️ Есть проблемы'}")

    svcs = report.get('services', {})
    if svcs:
        for _svc_key, _svc in svcs.items():
            _label = _svc.get('label', _svc_key)
            _st = _svc.get('status', '?')
            _icon = '✅' if _st == 'ok' else '❌'
            _line = f"  {_icon} {_label}"
            if _st != 'ok' and _svc.get('message'):
                _line += f" — {_svc['message']}"
            lines.append(_line)

    eq = report.get('email_quota')
    if eq:
        lines.append(f"Email: отправлено {eq.get('sent_today', '?')}/{eq.get('daily_limit', 50)}, осталось {eq.get('remaining', '?')}")

    tb = report.get('token_balance')
    if tb and tb.get('balance') is not None:
        lines.append(f"Токены: {tb['balance']}" + (" ⚠️ мало" if tb.get('low') else ""))

    ac = report.get('active_email_campaigns')
    if ac is not None:
        lines.append(f"Активных email-кампаний: {ac}")

    report['_human_summary'] = '\n'.join(lines)
    return report


async def start_content_campaign(
    name: str,
    goal: str,
    platforms: list = None,
    topics: str = None,
    tone: str = 'professional',
    frequency: str = 'daily',
    post_time: str | None = None,
    max_posts: int = 0,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Создать контент-кампанию для автономной публикации постов.

    AI-агент будет автономно:
    1. Генерировать контент по заданной стратегии и темам
    2. Публиковать в выбранные площадки (лента/TG/Discord)
    3. Соблюдать расписание и лимиты
    """
    if not session:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        if not name or not goal:
            return " Укажи название и цель кампании"

        # Если время не указано — найти свободный слот и ПРЕДЛОЖИТЬ пользователю
        if not post_time:
            _suggested_time = '10:00'
            try:
                _tasks_today = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['active', 'pending']),
                    Task.reminder_time.isnot(None),
                ).all()
                _busy_hours = set()
                for _t in _tasks_today:
                    _rt = _t.reminder_time
                    if _rt and hasattr(_rt, 'hour'):
                        _busy_hours.add(_rt.hour)
                for _candidate in ['10:00', '18:00', '09:00', '19:00', '12:00', '15:00']:
                    _ch = int(_candidate.split(':')[0])
                    if _ch not in _busy_hours:
                        _suggested_time = _candidate
                        break
            except Exception:
                pass
            _busy_info = f" (занято: {', '.join(f'{h}:00' for h in sorted(_busy_hours))})" if _busy_hours else ""
            return (
                f"У тебя свободно в {_suggested_time}{_busy_info}. "
                f"Запускаю кампанию «{name}» на {_suggested_time}? "
                f"Или скажи другое время (например 09:00, 18:00, 21:00)."
            )

        if platforms is None:
            platforms = ['feed']

        # Валидация площадок
        valid_platforms = {'feed', 'telegram', 'discord'}
        platforms = [p for p in platforms if p in valid_platforms]
        if not platforms:
            platforms = ['feed']

        # Проверяем наличие каналов для выбранных площадок
        warnings = []
        if 'telegram' in platforms and not user.telegram_channel:
            warnings.append(" Telegram-канал не настроен — посты в TG публиковаться не будут. Укажи канал командой /settings.")
        if 'discord' in platforms and not getattr(user, 'discord_webhook', None):
            warnings.append(" Discord webhook не настроен — посты в Discord публиковаться не будут. Настрой в дашборде.")

        # Проверяем дубликаты (активная кампания с похожим названием)
        from models import ContentCampaign
        existing = session.query(ContentCampaign).filter(
            ContentCampaign.user_id == user.id,
            ContentCampaign.status == 'active',
        ).all()
        for ex in existing:
            if ex.name and name.lower() in ex.name.lower():
                return f" Уже есть активная кампания «{ex.name}» (#{ex.id}). Используй manage_content_campaign чтобы обновить."

        # Лимит активных кампаний
        if len(existing) >= 5:
            return " Максимум 5 активных контент-кампаний. Заверши или отмени старые."

        # Валидация частоты
        valid_freq = {'daily', 'every_2_days', 'every_3_days', 'weekly'}
        if frequency not in valid_freq:
            frequency = 'daily'

        # Валидация времени
        try:
            h, m = map(int, post_time.split(':'))
            if h < 0 or h > 23 or m < 0 or m > 59:
                return " Невалидное время. Спроси пользователя удобное время для публикации (HH:MM)."
        except (ValueError, AttributeError):
            return " Время должно быть в формате HH:MM (09:00, 18:00, 21:30). Спроси пользователя какое время удобно."

        import json as _json_cc
        campaign = ContentCampaign(
            user_id=user.id,
            name=name[:300],
            goal=goal[:2000],
            topics=(topics or '')[:1000],
            platforms=_json_cc.dumps(platforms),
            tone=tone or 'professional',
            language=getattr(user, 'language', 'ru') or 'ru',
            frequency=frequency,
            post_time=post_time,
            daily_limit=1,
            max_posts=max_posts if max_posts and max_posts > 0 else 0,
            status='active',
            posts_published=0,
        )
        session.add(campaign)
        session.commit()

        freq_map = {
            'daily': 'каждый день',
            'every_2_days': 'раз в 2 дня',
            'every_3_days': 'раз в 3 дня',
            'weekly': 'раз в неделю',
        }
        platforms_ru = {
            'feed': 'лента новостей',
            'telegram': f'TG канал {user.telegram_channel or "?"}',
            'discord': 'Discord',
        }
        platforms_str = ', '.join(platforms_ru.get(p, p) for p in platforms)

        result = (
            f" Контент-кампания «{name}» запущена! (#{campaign.id})\n\n"
            f" Площадки: {platforms_str}\n"
            f" Частота: {freq_map.get(frequency, frequency)} в {post_time}\n"
            f" Цель: {goal[:150]}\n"
        )
        if topics:
            result += f" Темы: {topics[:150]}\n"
        if max_posts and max_posts > 0:
            result += f" Всего постов: {max_posts}\n"
        else:
            result += " Без ограничения по количеству\n"

        result += "\nАгент будет автономно генерировать и публиковать посты по расписанию."

        if warnings:
            result += "\n\n" + "\n".join(warnings)

        # Логируем в AgentActivityLog → отображается в «Активность» на дашборде
        try:
            from models import AgentActivityLog
            activity = AgentActivityLog(
                user_id=user.id,
                activity_type='content_campaign',
                title=f"Контент-кампания «{name[:80]}» запущена",
                content=f"Площадки: {platforms_str} | Частота: {freq_map.get(frequency, frequency)} | Цель: {goal[:200]}",
                target=platforms_str,
                status='active',
                ref_id=campaign.id,
            )
            session.add(activity)
            session.commit()
        except Exception as _ae:
            logger.warning(f"[CONTENT_CAMPAIGN] Failed to log activity: {_ae}")

        logger.info(f"[CONTENT_CAMPAIGN] Created #{campaign.id} «{name}» for user {user_id}: {platforms}, {frequency}")
        return result

    except Exception as e:
        logger.error(f"[CONTENT_CAMPAIGN] Error creating: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка создания кампании: {str(e)}"
    finally:
        if close_session:
            session.close()


async def manage_content_campaign(
    action: str,
    campaign_id: int = None,
    updates: dict = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Управление контент-кампанией: пауза, возобновление, отмена, обновление."""
    if not session:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        from models import ContentCampaign

        # Находим кампанию
        if campaign_id:
            campaign = session.query(ContentCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            # Последняя активная/paused
            campaign = session.query(ContentCampaign).filter(
                ContentCampaign.user_id == user.id,
                ContentCampaign.status.in_(['active', 'paused'])
            ).order_by(ContentCampaign.created_at.desc()).first()

        if not campaign:
            return " Контент-кампания не найдена. Создай новую с помощью start_content_campaign."

        if action == 'pause':
            if campaign.status == 'paused':
                return f" Кампания «{campaign.name}» уже на паузе."
            campaign.status = 'paused'
            session.commit()
            return f" Кампания «{campaign.name}» (#{campaign.id}) поставлена на паузу. Публикация остановлена."

        elif action == 'resume':
            if campaign.status == 'active':
                return f"▶ Кампания «{campaign.name}» уже активна."
            if campaign.status in ('completed', 'cancelled'):
                return f" Кампания «{campaign.name}» завершена/отменена. Создай новую."
            campaign.status = 'active'
            session.commit()
            return f"▶ Кампания «{campaign.name}» (#{campaign.id}) возобновлена! Публикация продолжится по расписанию."

        elif action == 'cancel':
            campaign.status = 'cancelled'
            session.commit()
            return f" Кампания «{campaign.name}» (#{campaign.id}) отменена. Опубликовано {campaign.posts_published or 0} постов."

        elif action == 'update':
            if not updates:
                return " Укажи параметры для обновления (updates)."

            import json as _json_upd
            changed = []
            if 'name' in updates:
                campaign.name = str(updates['name'])[:300]
                changed.append(f"название → {campaign.name}")
            if 'goal' in updates:
                campaign.goal = str(updates['goal'])[:2000]
                changed.append("цель обновлена")
            if 'topics' in updates:
                campaign.topics = str(updates['topics'])[:1000]
                changed.append(f"темы → {campaign.topics[:100]}")
            if 'tone' in updates:
                campaign.tone = str(updates['tone'])
                changed.append(f"тон → {campaign.tone}")
            if 'frequency' in updates:
                valid_freq = {'daily', 'every_2_days', 'every_3_days', 'weekly'}
                freq = str(updates['frequency'])
                if freq in valid_freq:
                    campaign.frequency = freq
                    changed.append(f"частота → {freq}")
            if 'post_time' in updates:
                campaign.post_time = str(updates['post_time'])[:10]
                changed.append(f"время → {campaign.post_time}")
            if 'max_posts' in updates:
                campaign.max_posts = int(updates['max_posts'])
                changed.append(f"макс.постов → {campaign.max_posts}")
            if 'platforms' in updates:
                valid_p = {'feed', 'telegram', 'discord'}
                new_p = [p for p in updates['platforms'] if p in valid_p]
                if new_p:
                    campaign.platforms = _json_upd.dumps(new_p)
                    changed.append(f"площадки → {', '.join(new_p)}")

            if not changed:
                return " Нет распознанных параметров для обновления."

            session.commit()
            return f" Кампания «{campaign.name}» (#{campaign.id}) обновлена:\n" + "\n".join(f" • {c}" for c in changed)

        else:
            return f" Неизвестное действие: {action}. Доступны: pause, resume, cancel, update."

    except Exception as e:
        logger.error(f"[CONTENT_CAMPAIGN] Error managing: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════
# КАМПАНИИ ДЕЛЕГИРОВАНИЯ — массовое автономное делегирование
# ═══════════════════════════════════════════════════════

async def start_delegation_campaign(
    name: str,
    goal: str,
    target_audience: str,
    task_template: str = None,
    offer: str = None,
    tone: str = 'professional',
    max_delegations: int = 10,
    daily_limit: int = 3,
    default_deadline_hours: int = 48,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Создать кампанию делегирования для автономного распределения задач.

    AI-агент будет автономно:
    1. Находить подходящих исполнителей по навыкам/интересам
    2. Создавать задачи и делегировать
    3. Отправлять мотивирующие уведомления
    4. Отслеживать принятие/отклонение
    """
    if not session:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        if not name or not goal or not target_audience:
            return " Укажи название, цель и целевую аудиторию кампании"

        # Проверяем дубликаты (семантически: точный substring + пересечение слов)
        from models import DelegationCampaign
        existing = session.query(DelegationCampaign).filter(
            DelegationCampaign.user_id == user.id,
            DelegationCampaign.status == 'active',
        ).all()
        _stop_d = {'и', 'в', 'на', 'для', 'по', 'с', 'к', 'или', 'что', 'при', 'the', 'and', 'for', 'of', 'to'}
        _new_name_words = {w for w in name.lower().split() if len(w) > 3} - _stop_d
        _new_goal_words = {w for w in goal.lower().split() if len(w) > 3} - _stop_d
        for ex in existing:
            if ex.name and name.lower() in ex.name.lower():
                return f"⚠️ Уже есть активная кампания «{ex.name}» (#{ex.id}). Используй manage_delegation_campaign чтобы обновить."
            _ex_name_words = {w for w in (ex.name or '').lower().split() if len(w) > 3} - _stop_d
            _ex_goal_words = {w for w in (ex.goal or '').lower().split() if len(w) > 3} - _stop_d
            if _new_name_words and _ex_name_words and len(_new_name_words & _ex_name_words) >= 2:
                return f"⚠️ Похожая кампания делегирования уже существует: «{ex.name}» (#{ex.id}). Используй manage_delegation_campaign для обновления."
            if _new_goal_words and _ex_goal_words and len(_new_goal_words & _ex_goal_words) >= 3:
                return f"⚠️ Кампания с похожей целью уже существует: «{ex.name}» (#{ex.id}). Используй manage_delegation_campaign для обновления."

        # Лимит активных кампаний
        if len(existing) >= 5:
            return " Максимум 5 активных кампаний делегирования. Заверши или отмени старые."

        campaign = DelegationCampaign(
            user_id=user.id,
            name=name[:300],
            goal=goal[:2000],
            target_audience=target_audience[:1000],
            task_template=(task_template or '')[:1000],
            offer=(offer or '')[:500],
            tone=tone or 'professional',
            max_delegations=max_delegations if max_delegations and max_delegations > 0 else 10,
            daily_limit=daily_limit if daily_limit and daily_limit > 0 else 3,
            max_follow_ups=2,
            default_deadline_hours=default_deadline_hours if default_deadline_hours and default_deadline_hours > 0 else 48,
            status='active',
            delegations_sent=0,
            delegations_accepted=0,
            delegations_completed=0,
            delegations_rejected=0,
        )
        session.add(campaign)
        session.commit()

        result = (
            f"Кампания делегирования «{name}» запущена (#{campaign.id})\n\n"
            f"Цель: {goal[:150]}\n"
            f"Аудитория: {target_audience[:150]}\n"
            f"Макс. делегирований: {max_delegations}\n"
            f"Лимит в день: {daily_limit}\n"
            f"Дедлайн задач: {default_deadline_hours}ч\n"
        )
        if task_template:
            result += f"Шаблон: {task_template[:100]}\n"
        if offer:
            result += f"Мотивация: {offer[:100]}\n"

        result += "\nАгент будет автономно находить подходящих исполнителей и делегировать задачи."

        # Логируем в AgentActivityLog → отображается в «Активность» на дашборде
        try:
            from models import AgentActivityLog
            activity = AgentActivityLog(
                user_id=user.id,
                activity_type='delegation_campaign',
                title=f"Кампания делегирования «{name[:80]}» запущена",
                content=f"Цель: {goal[:200]} | Аудитория: {target_audience[:200]}",
                target=target_audience[:200],
                status='active',
                ref_id=campaign.id,
            )
            session.add(activity)
            session.commit()
        except Exception as _ae:
            logger.warning(f"[DELEGATION_CAMPAIGN] Failed to log activity: {_ae}")

        logger.info(f"[DELEGATION_CAMPAIGN] Created #{campaign.id} «{name}» for user {user_id}")
        return result

    except Exception as e:
        logger.error(f"[DELEGATION_CAMPAIGN] Error creating: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка создания кампании: {str(e)}"
    finally:
        if close_session:
            session.close()


async def manage_delegation_campaign(
    action: str,
    campaign_id: int = None,
    updates: dict = None,
    user_id: int = None,
    session=None,
    close_session: bool = True,
):
    """Управление кампанией делегирования: пауза, возобновление, отмена, обновление."""
    if not session:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден"

        from models import DelegationCampaign

        if campaign_id:
            campaign = session.query(DelegationCampaign).filter_by(
                id=campaign_id, user_id=user.id
            ).first()
        else:
            campaign = session.query(DelegationCampaign).filter(
                DelegationCampaign.user_id == user.id,
                DelegationCampaign.status.in_(['active', 'paused'])
            ).order_by(DelegationCampaign.created_at.desc()).first()

        if not campaign:
            return "Кампания делегирования не найдена. Создай новую с помощью start_delegation_campaign."

        if action == 'pause':
            if campaign.status == 'paused':
                return f" Кампания «{campaign.name}» уже на паузе."
            campaign.status = 'paused'
            session.commit()
            return (
                f" Кампания «{campaign.name}» (#{campaign.id}) на паузе.\n"
                f" Отправлено: {campaign.delegations_sent or 0}, принято: {campaign.delegations_accepted or 0}"
            )

        elif action == 'resume':
            if campaign.status == 'active':
                return f"▶ Кампания «{campaign.name}» уже активна."
            if campaign.status in ('completed', 'cancelled'):
                return f" Кампания «{campaign.name}» завершена/отменена. Создай новую."
            campaign.status = 'active'
            session.commit()
            return f"▶ Кампания «{campaign.name}» (#{campaign.id}) возобновлена!"

        elif action == 'cancel':
            campaign.status = 'cancelled'
            session.commit()
            return (
                f" Кампания «{campaign.name}» (#{campaign.id}) отменена.\n"
                f" Итого: отправлено {campaign.delegations_sent or 0}, "
                f"принято {campaign.delegations_accepted or 0}, "
                f"завершено {campaign.delegations_completed or 0}"
            )

        elif action == 'update':
            if not updates:
                return " Укажи параметры для обновления (updates)."

            changed = []
            if 'name' in updates:
                campaign.name = str(updates['name'])[:300]
                changed.append(f"название → {campaign.name}")
            if 'goal' in updates:
                campaign.goal = str(updates['goal'])[:2000]
                changed.append("цель обновлена")
            if 'target_audience' in updates:
                campaign.target_audience = str(updates['target_audience'])[:1000]
                changed.append(f"аудитория → {campaign.target_audience[:100]}")
            if 'task_template' in updates:
                campaign.task_template = str(updates['task_template'])[:1000]
                changed.append("шаблон задачи обновлён")
            if 'offer' in updates:
                campaign.offer = str(updates['offer'])[:500]
                changed.append(f"мотивация → {campaign.offer[:100]}")
            if 'tone' in updates:
                campaign.tone = str(updates['tone'])
                changed.append(f"тон → {campaign.tone}")
            if 'max_delegations' in updates:
                campaign.max_delegations = int(updates['max_delegations'])
                changed.append(f"макс.делегирований → {campaign.max_delegations}")
            if 'daily_limit' in updates:
                campaign.daily_limit = int(updates['daily_limit'])
                changed.append(f"лимит в день → {campaign.daily_limit}")
            if 'default_deadline_hours' in updates:
                campaign.default_deadline_hours = int(updates['default_deadline_hours'])
                changed.append(f"дедлайн → {campaign.default_deadline_hours}ч")

            if not changed:
                return " Нет распознанных параметров для обновления."

            session.commit()
            return f" Кампания «{campaign.name}» (#{campaign.id}) обновлена:\n" + "\n".join(f" • {c}" for c in changed)

        else:
            return f" Неизвестное действие: {action}. Доступны: pause, resume, cancel, update."

    except Exception as e:
        logger.error(f"[DELEGATION_CAMPAIGN] Error managing: {e}", exc_info=True)
        session.rollback()
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


# ═══════════════════════════════════════════════════════
# MARKETPLACE: Агенты и скрипты
# ═══════════════════════════════════════════════════════

async def list_marketplace(category: str = None, search: str = None,
                           item_type: str = 'agents',
                           user_id: int = None, session=None) -> str:
    """Показывает маркетплейс: активных агентов или скрипты."""
    close_session = False
    if not session:
        session = Session()
        close_session = True
    try:
        from models import UserAgent
        try:
            from models import UserScript as _UserScript
        except ImportError:
            _UserScript = None
        import json as _json

        if item_type == 'scripts':
            if _UserScript is None:
                return " Раздел скриптов временно недоступен."
            q = session.query(_UserScript).filter_by(status='active')
            if category:
                q = q.filter(_UserScript.category == category)
            if search:
                q = q.filter(_UserScript.name.ilike(f'%{search}%'))
            items = q.order_by(_UserScript.installs_count.desc()).limit(10).all()
            if not items:
                return " Скриптов пока нет. Будьте первым — создайте скрипт!"
            lines = [" **Маркетплейс скриптов:**\n"]
            for s in items:
                lines.append(f"• **{s.name}** (#{s.id}) — {s.price_per_run} токенов/запуск | {s.installs_count} установок\n  {s.description or ''}")
            return "\n".join(lines)
        else:
            q = session.query(UserAgent).filter_by(status='active')
            if category:
                q = q.filter(UserAgent.specialization == category)
            if search:
                q = q.filter(UserAgent.name.ilike(f'%{search}%'))
            items = q.order_by(UserAgent.subscribers_count.desc()).limit(10).all()
            if not items:
                return " Агентов пока нет. Создай первого!"
            lines = [" **Маркетплейс агентов:**\n"]
            for a in items:
                rating = round(a.rating_sum / a.rating_count, 1) if a.rating_count else "—"
                lines.append(f"• **{a.name}** (@{a.slug}) — {a.price_per_message} токенов/сообщение | {rating} | {a.subscribers_count} подписчиков\n {a.description or ''}")
            return "\n".join(lines)
    except Exception as e:
        logger.error(f"[MARKETPLACE] list error: {e}", exc_info=True)
        return f" Ошибка загрузки маркетплейса: {str(e)}"
    finally:
        if close_session:
            session.close()


async def switch_agent(agent_slug: str = None, reset: bool = False,
                       user_id: int = None, session=None) -> str:
    """Переключает пользователя на кастомного агента или сбрасывает на основного."""
    close_session = False
    if not session:
        session = Session()
        close_session = True
    try:
        from models import UserAgent, AgentSubscription, User
        from .user_agents import set_user_active_agent, bill_agent_message

        if reset:
            set_user_active_agent(user_id, None)
            return " Возвращаюсь в стандартный режим ASI Biont."

        if not agent_slug:
            return " Укажи slug или имя агента (например @crypto-alex или «Марк»)"

        slug = agent_slug.lstrip('@').strip()

        # Поиск по slug (приоритет), затем по name (case-insensitive) — для поддержки @Имя
        agent = session.query(UserAgent).filter_by(slug=slug, status='active').first()
        if not agent:
            agent = session.query(UserAgent).filter(
                UserAgent.name.ilike(slug),
                UserAgent.status == 'active',
            ).first()
        # Дополнительный поиск среди собственных агентов пользователя (status active/paused)
        if not agent:
            user_obj = session.query(User).filter_by(telegram_id=user_id).first()
            if user_obj:
                agent = session.query(UserAgent).filter(
                    UserAgent.author_id == user_obj.id,
                    UserAgent.name.ilike(slug),
                    UserAgent.status.in_(['active', 'paused']),
                ).first()
        if not agent:
            return f" Агент «{slug}» не найден. Проверь имя или slug в разделе Маркетплейс."

        # Проверяем/создаём подписку
        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        if not user_obj:
            return " Пользователь не найден."

        sub = session.query(AgentSubscription).filter_by(
            user_id=user_obj.id, agent_id=agent.id).first()
        is_new = not sub
        if is_new:
            sub = AgentSubscription(user_id=user_obj.id, agent_id=agent.id)
            session.add(sub)
            agent.subscribers_count = (agent.subscribers_count or 0) + 1
            session.commit()

        set_user_active_agent(user_id, agent.id)

        return (f" Подключён агент **{agent.name}**!\n"
                f"Цена: {agent.price_per_message} токенов/сообщение.\n"
                f"Чтобы вернуться к стандартному режиму — скажи «переключись на ASI Biont».")
    except Exception as e:
        logger.error(f"[MARKETPLACE] switch_agent error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()

async def run_user_script(script_id: int = None, script_slug: str = None,
                          params: dict = None,
                          user_id: int = None, session=None) -> str:
    """Запускает установленный скрипт из маркетплейса в sandbox."""
    close_session = False
    if not session:
        session = Session()
        close_session = True
    try:
        from models import UserScript, ScriptInstall, User
        from .user_agents import run_script_sandbox, bill_script_run

        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        if not user_obj:
            return " Пользователь не найден."

        # Ищем скрипт по id или slug
        if script_id:
            script = session.query(UserScript).filter_by(id=script_id, status='active').first()
        elif script_slug:
            script = session.query(UserScript).filter_by(slug=script_slug, status='active').first()
        else:
            return " Укажи id или slug скрипта."

        if not script:
            return " Скрипт не найден или недоступен."

        # Проверяем установку
        install = session.query(ScriptInstall).filter_by(
            user_id=user_obj.id, script_id=script.id).first()
        if not install:
            return (f" Скрипт «{script.name}» не установлен. "
                    f"Установи его в маркетплейсе за {script.price_per_run} токенов/запуск.")

        # Запускаем в sandbox
        run_params = params or {}
        exec_result = run_script_sandbox(script.code, run_params)

        # Биллинг
        bill_script_run(
            user_id=user_id, script_id=script.id,
            params=run_params, result=exec_result['result'],
            success=exec_result['success'], exec_ms=exec_result['exec_ms'],
        )

        if exec_result['success']:
            return f" Скрипт «{script.name}» выполнен за {exec_result['exec_ms']}мс:\n\n{exec_result['result']}"
        else:
            return f" Скрипт «{script.name}» завершился с ошибкой:\n{exec_result['error']}"

    except Exception as e:
        logger.error(f"[MARKETPLACE] run_script error: {e}", exc_info=True)
        return f" Ошибка запуска скрипта: {str(e)}"
    finally:
        if close_session:
            session.close()


async def install_script(script_id: int = None, script_slug: str = None,
                         user_id: int = None, session=None) -> str:
    """Устанавливает скрипт из маркетплейса."""
    close_session = False
    if not session:
        session = Session()
        close_session = True
    try:
        from models import UserScript, ScriptInstall, User

        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        if not user_obj:
            return " Пользователь не найден."

        if script_id:
            script = session.query(UserScript).filter_by(id=script_id, status='active').first()
        elif script_slug:
            script = session.query(UserScript).filter_by(slug=script_slug, status='active').first()
        else:
            return " Укажи id или slug скрипта."

        if not script:
            return " Скрипт не найден."

        existing = session.query(ScriptInstall).filter_by(
            user_id=user_obj.id, script_id=script.id).first()
        if existing:
            return f"ℹ Скрипт «{script.name}» уже установлен."

        install = ScriptInstall(user_id=user_obj.id, script_id=script.id)
        session.add(install)
        script.installs_count = (script.installs_count or 0) + 1
        session.commit()

        return (f" Скрипт «{script.name}» установлен!\n"
                f"Цена: {script.price_per_run} токенов/запуск.\n"
                f"Запусти его: «запусти скрипт {script.slug}»")
    except Exception as e:
        session.rollback()
        logger.error(f"[MARKETPLACE] install_script error: {e}", exc_info=True)
        return f" Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


async def run_agent_action(user_id: int, action: str, params: dict = None,
                           session=None, close_session: bool = True) -> str:
    """Запускает действие через скрипт активного кастомного агента пользователя.

    Делегирует в HybridAutonomousAgent._run_external_action.
    Доступен только когда у пользователя активен агент с настроенным python_code.

    Args:
        user_id: Telegram ID пользователя
        action: Название действия (строка, передаётся агенту через AGENT_ACTION)
        params: Словарь параметров действия (передаются как AGENT_PARAM_* env vars)
    """
    from .autonomous_agent import get_autonomous_agent
    agent = get_autonomous_agent()

    # Убеждаемся, что данные агента загружены в кеш агента
    if user_id not in agent._active_agent_data:
        try:
            from .user_agents import get_user_active_agent, load_agent_personality
            aid = get_user_active_agent(user_id)
            if aid:
                adata = load_agent_personality(aid)
                if adata:
                    agent._active_agent_data[user_id] = adata
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

    if user_id not in agent._active_agent_data:
        return " Нет активного агента со скриптом. Активируй агента через /dashboard → Агенты."

    # ── GUARD: cooldown для перегретых action (>4x за 48ч) ──
    try:
        from models import AgentActivityLog as _AAL_cd, User as _User_cd
        _cd_cut = datetime.now(timezone.utc) - timedelta(hours=48)
        _cd_action_l = (action or '').strip().lower()
        _cd_sess = session or Session()
        _cd_close = session is None
        try:
            # user_id в run_agent_action — telegram_id, AAL.user_id — internal id
            _cd_user = _cd_sess.query(_User_cd.id).filter_by(telegram_id=user_id).first()
            _cd_uid = _cd_user[0] if _cd_user else None
            if _cd_uid:
                # title в AAL: "AgentName · action_name", ищем по суффиксу
                _cd_count = _cd_sess.query(func.count(_AAL_cd.id)).filter(
                    _AAL_cd.user_id == _cd_uid,
                    _AAL_cd.activity_type == 'run_agent_action',
                    func.lower(_AAL_cd.title).like(f'%· {_cd_action_l}%'),
                    _AAL_cd.created_at >= _cd_cut,
                ).scalar() or 0
                # Short-window cooldown: 2+ за 2 часа — антилуп
                _cd_cut_short = datetime.now(timezone.utc) - timedelta(hours=2)
                _cd_count_short = _cd_sess.query(func.count(_AAL_cd.id)).filter(
                    _AAL_cd.user_id == _cd_uid,
                    _AAL_cd.activity_type == 'run_agent_action',
                    func.lower(_AAL_cd.title).like(f'%· {_cd_action_l}%'),
                    _AAL_cd.created_at >= _cd_cut_short,
                ).scalar() or 0
            else:
                _cd_count = 0
                _cd_count_short = 0
        finally:
            if _cd_close:
                _cd_sess.close()
        if _cd_count >= 4:
            return (
                f"⛔ Действие «{action}» заблокировано: уже вызывалось {_cd_count}x за 48ч. "
                f"Используй ДРУГОЙ инструмент или стратегию."
            )
        if _cd_count_short >= 2:
            return (
                f"⛔ Действие «{action}» заблокировано: уже вызывалось {_cd_count_short}x за 2ч. "
                f"Слишком часто. Используй ДРУГОЙ инструмент или стратегию."
            )
    except Exception as _cd_e:
        logger.debug("run_agent_action cooldown check: %s", _cd_e)

    # Адаптивная нормализация action под конкретного пользователя/агента/интеграции.
    # Без жёстких правил: комбинируем similarity + сигналы интеграций + эмпирику DecisionLog.
    try:
        import re as _re_ra
        from difflib import SequenceMatcher as _SM_ra
        from models import DecisionLog as _DL_ra

        _adata = agent._active_agent_data.get(user_id) or {}
        _agent_name = (_adata.get('name') or '').strip()
        _py_code = (_adata.get('python_code') or '').strip()
        _tools_allowed_raw = (_adata.get('tools_allowed') or '').strip()
        _api_keys_raw = (_adata.get('user_api_keys') or '').strip()
        _supported = []
        if _py_code:
            for _m in _re_ra.finditer(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", _py_code):
                _a = _m.group(1).strip()
                if _a and _a not in _supported:
                    _supported.append(_a)
            for _m in _re_ra.finditer(r"ACTION\s+in\s*\(([^)]+)\)", _py_code):
                for _part in _m.group(1).split(','):
                    _a = _part.strip().strip("'\" ").strip()
                    if _a and _a not in _supported:
                        _supported.append(_a)

        _supported_l = [s.lower() for s in _supported]
        _orig_action = (action or '').strip()
        _action_l = _orig_action.lower()

        # Платформенные action (send_email, search_users) обрабатываются _run_external_action
        # напрямую — нормализация их превращает в чужие действия (search_users → search_contacts).
        _PLATFORM_HANDLED_RA = {'send_email', 'search_users'}

        if _orig_action and _supported and _action_l not in _supported_l and _action_l not in _PLATFORM_HANDLED_RA:
            _context_hint = ' '.join([
                _orig_action,
                str(params or ''),
                _agent_name,
                _tools_allowed_raw,
                _api_keys_raw,
                _py_code[:1200],
            ]).lower()

            def _tokens(_txt: str) -> set:
                return {t for t in _re_ra.findall(r'[a-zA-Zа-яА-Я0-9_]{3,}', (_txt or '').lower())}

            _signal_map = {
                'email': ('email', 'gmail', 'imap', 'inbox', 'outreach', 'reply', 'письм', 'почт'),
                'rss': ('rss', 'news', 'feed', 'хабр', 'новост', 'стать'),
                'market': ('market', 'finance', 'alpha', 'vantage', 'stock', 'crypto', 'рын', 'котиров'),
                'social': ('telegram', 'discord', 'post', 'publish', 'канал', 'пост', 'публик'),
                'code': ('github', 'repo', 'commit', 'issue', 'pull', 'код', 'разработ'),
                'crm': ('crm', 'amocrm', 'contact', 'contacts', 'lead', 'сделк', 'контакт', 'воронк'),
            }

            def _signals(_txt: str) -> set:
                _s = set()
                _low = (_txt or '').lower()
                for _k, _kws in _signal_map.items():
                    if any(_kw in _low for _kw in _kws):
                        _s.add(_k)
                return _s

            _req_tokens = _tokens(_action_l)
            _req_signals = _signals(_context_hint)
            _cand_score_map = {}

            for _cand in _supported:
                _cand_l = _cand.lower().strip()
                _cand_tokens = _tokens(_cand_l)
                _inter = len(_req_tokens & _cand_tokens)
                _union = max(1, len(_req_tokens | _cand_tokens))
                _token_jacc = _inter / _union
                _lex_sim = _SM_ra(None, _action_l, _cand_l).ratio()
                _cand_signals = _signals(_cand_l)
                _signal_overlap = len(_req_signals & _cand_signals)
                _score = (_lex_sim * 0.55) + (_token_jacc * 0.30) + (_signal_overlap * 0.20)
                _cand_score_map[_cand_l] = _score

            # Эмпирический буст: что у этого пользователя и этого агента реально работало
            _hist_session = session
            _hist_close = False
            try:
                if _hist_session is None:
                    _hist_session = Session()
                    _hist_close = True
                _cut = datetime.now(timezone.utc) - timedelta(days=30)
                _q = _hist_session.query(
                    _DL_ra.chosen_action,
                    func.avg(_DL_ra.outcome_score).label('avg_score'),
                    func.count(_DL_ra.id).label('n_rows'),
                ).filter(
                    _DL_ra.user_id == user_id,
                    _DL_ra.decision_type == 'tool_selection',
                    _DL_ra.outcome_score.isnot(None),
                    _DL_ra.created_at >= _cut,
                )
                if _agent_name:
                    _q = _q.filter(_DL_ra.context_summary.ilike(f"{_agent_name}:%"))
                _hist_rows = _q.group_by(_DL_ra.chosen_action).all()

                for _chosen, _avg, _n in _hist_rows:
                    _chosen_l = (_chosen or '').strip().lower()
                    if '·' in _chosen_l:
                        _chosen_l = _chosen_l.split('·', 1)[-1].strip().lower()
                    if _chosen_l in _cand_score_map:
                        _weight = min(1.0, float(_n or 0) / 8.0)
                        _emp_adj = (float(_avg or 0.5) - 0.5) * 0.8 * _weight
                        _cand_score_map[_chosen_l] += _emp_adj
            finally:
                if _hist_close and _hist_session is not None:
                    _hist_session.close()

            _replacement_l = max(_cand_score_map, key=lambda _k: _cand_score_map.get(_k, -1.0))
            _replacement = next((s for s in _supported if s.lower() == _replacement_l), _supported[0])
            logger.info(
                "[RUN_AGENT_ACTION] adaptive normalize: %s -> %s (user=%s agent=%s score=%.3f)",
                _orig_action,
                _replacement,
                user_id,
                _agent_name or '?',
                _cand_score_map.get(_replacement_l, 0.0),
            )
            # ── Signal-family mismatch guard ──
            # Если сигналы запроса и лучшего кандидата не пересекаются,
            # нормализация семантически неверна (github → email и т.п.)
            _req_action_signals = _signals(_orig_action)
            _best_signals = _signals(_replacement_l)
            if _req_action_signals and _best_signals and not (_req_action_signals & _best_signals):
                logger.warning(
                    "[RUN_AGENT_ACTION] signal mismatch: %s (%s) vs %s (%s) — skip normalize",
                    _orig_action, _req_action_signals, _replacement, _best_signals,
                )
            else:
                action = _replacement
    except Exception as _norm_e:
        logger.debug("[RUN_AGENT_ACTION] action normalize skipped: %s", _norm_e)

    raw_params = {'action': action, 'params': params or {}}
    result = await agent._run_external_action(raw_params, user_id)

    if isinstance(result, dict):
        if result.get('status') == 'success':
            output = result.get('output', '')
            return f" Действие «{action}» выполнено:\n{output}"
        else:
            err = result.get('error', 'неизвестная ошибка')
            return f" Ошибка при выполнении «{action}»: {err}"
    return str(result)


# ─── Universal HTTP API Request ───────────────────────────────────

async def http_api_request(user_id: int, url: str, method: str = 'GET',
                           headers: dict = None, body: dict = None,
                           auth_key: str = None, auth_scheme: str = 'Bearer',
                           agent_name: str = None,
                           session=None, close_session: bool = True) -> str:
    """Универсальный HTTP-запрос к любому внешнему API с автоподстановкой API-ключей агента."""
    import aiohttp
    import ipaddress
    from urllib.parse import urlparse

    if not url or not isinstance(url, str):
        return "Ошибка: URL не указан"
    url = url.strip()
    if not url.startswith('https://') and not url.startswith('http://'):
        return "Ошибка: URL должен начинаться с https:// или http://"

    # Block internal/private IPs
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ''
        if hostname in ('localhost', '127.0.0.1', '0.0.0.0', '::1', ''):
            return "Ошибка: запросы к локальным адресам запрещены"
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return "Ошибка: запросы к приватным адресам запрещены"
        except ValueError:
            pass  # hostname, not IP
    except Exception:
        return "Ошибка: некорректный URL"

    method = (method or 'GET').upper()
    if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
        return "Ошибка: метод должен быть GET, POST, PUT, PATCH или DELETE"

    # Resolve API key from agent config
    req_headers = dict(headers or {})
    if auth_key:
        _key_value = _resolve_agent_api_key(user_id, auth_key.strip(), agent_name)
        if _key_value:
            scheme = (auth_scheme or 'Bearer').strip()
            if scheme:
                req_headers['Authorization'] = f'{scheme} {_key_value}'
            else:
                req_headers['Authorization'] = _key_value
        else:
            return f"Ошибка: API-ключ '{auth_key}' не найден в настройках агента. Добавь его в дашборде: https://asibiont.com/dashboard → Агенты → API-ключи"

    if 'Content-Type' not in req_headers and method in ('POST', 'PUT', 'PATCH'):
        req_headers['Content-Type'] = 'application/json'

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            kwargs = {'headers': req_headers}
            if body and method in ('POST', 'PUT', 'PATCH'):
                kwargs['json'] = body

            async with http_session.request(method, url, **kwargs) as resp:
                status = resp.status
                try:
                    resp_text = await resp.text(encoding='utf-8')
                except Exception:
                    resp_text = await resp.read()
                    resp_text = resp_text.decode('utf-8', errors='replace')

                if len(resp_text) > 3000:
                    resp_text = resp_text[:3000] + '\n... (обрезано, всего ' + str(len(resp_text)) + ' символов)'

                if 200 <= status < 300:
                    return f"✅ HTTP {status}\n{resp_text}"
                else:
                    return f"❌ HTTP {status}\n{resp_text}"
    except aiohttp.ClientError as e:
        return f"Ошибка HTTP-запроса: {e}"
    except Exception as e:
        return f"Ошибка: {e}"


def _resolve_agent_api_key(user_id: int, key_name: str, agent_name: str = None) -> str | None:
    """Находит значение API-ключа из настроек агента пользователя."""
    from .autonomous_agent import get_autonomous_agent, _decrypt_keys

    agent = get_autonomous_agent()
    agent_data = None

    if agent_name:
        try:
            from models import Session as _S, UserAgent as _UA
            _s = _S()
            try:
                _tg_user = _s.execute(
                    __import__('sqlalchemy').text("SELECT id FROM users WHERE telegram_id = :tid"),
                    {"tid": user_id}
                ).fetchone()
                if _tg_user:
                    _req_lower = agent_name.lower().strip()
                    for _ca in _s.query(_UA).filter(
                        _UA.author_id == _tg_user[0],
                        _UA.status.in_(('active', 'draft', 'paused')),
                    ).all():
                        if (_ca.name or '').lower() == _req_lower:
                            agent_data = {'user_api_keys': _ca.user_api_keys or ''}
                            break
            finally:
                _s.close()
        except Exception:
            pass

    if not agent_data:
        agent_data = agent._active_agent_data.get(user_id)

    if not agent_data:
        try:
            from .user_agents import get_user_active_agent, load_agent_personality
            aid = get_user_active_agent(user_id)
            if aid:
                agent_data = load_agent_personality(aid)
        except Exception:
            pass

    if not agent_data:
        return None

    api_keys_raw = _decrypt_keys(agent_data.get('user_api_keys', '') or '')
    key_name_upper = key_name.upper()
    for line in api_keys_raw.splitlines():
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, _, v = line.partition('=')
            if k.strip().upper() == key_name_upper:
                return v.strip()
    return None