# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timedelta, timezone
import pytz
import requests
import aiohttp
from ai_integration.utils import _safe_http
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


def _get_email_tg_link(user) -> str:
    """Returns plain-text Telegram link for email footer (no https://).

    Prefers public channel (@channel → t.me/channel), falls back to personal
    username. Returns '' if neither is set — caller must check before appending.
    Plain-text format is safe for cold emails (no URL = no spam filter hit).
    """
    tg = (getattr(user, 'telegram_channel', '') or '').strip().lstrip('@')
    if not tg:
        tg = (getattr(user, 'username', '') or '').strip()
    return f't.me/{tg}' if tg else ''


def _build_email_html(body_html: str, unsub_email: str = 'outreach@asibiont.com', sender_name: str = '', unsub_url: str = '') -> str:
    """Общий HTML-шаблон для email с unsubscribe footer.

    Чистый текстовый стиль — без баннеров, кнопок, логотипов.
    Как личное письмо.
    """
    if unsub_url:
        unsub_line_ru = f'Если вы не хотите получать подобные письма — <a href="{unsub_url}" style="color:#9CA3AF;">отписаться</a> или ответьте «отписаться» на это письмо.'
        unsub_line_en = f'To stop receiving these emails — <a href="{unsub_url}" style="color:#9CA3AF;">unsubscribe</a> or reply "unsubscribe".'
    else:
        unsub_line_ru = f'Если вы не хотите получать подобные письма, просто ответьте "отписаться" на это сообщение или напишите на {unsub_email}'
        unsub_line_en = f'If you don\'t want to receive such emails, simply reply "unsubscribe" to this message or write to {unsub_email}'
    sender_sig = f'— {sender_name}' if sender_name else ''

    sig_block = f'<p style="margin-top: 20px; color: #374151;">{sender_sig}</p>' if sender_sig else ''

    return f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 14px; color: #374151; line-height: 1.6; margin: 0; padding: 0;">
<div style="max-width: 600px; margin: 0 auto; padding: 24px;">
{body_html}
{sig_block}
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

    # === Векторная память (best-effort, не блокирует event loop) ===
    try:
        from ai_integration.vector_memory import store_memory_background as _vmem_at
        _desc_at = f" {description.strip()[:100]}" if description and description.strip() else ""
        _meta_at = {'type': 'task', 'task_id': str(task_id)}
        if task.goal_id:
            _meta_at['goal_id'] = str(task.goal_id)
        _vmem_at(user_id, f"Задача создана: «{title}».{_desc_at}".strip(), _meta_at)
    except Exception as _e:
        logger.debug(f"[ADD_TASK] Vector memory skipped: {_e}")

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg

# set_recurring_task removed - feature not critical, required subscription

def _make_blog_slug(title: str, note_id: int) -> str:
    """Генерирует SEO-slug из заголовка: «Падение найма Junior» → '882-padenie-nayma-junior'"""
    import re as _re
    _TRANSLIT = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z',
        'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
        'с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',
        'щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    }
    s = title.lower().strip()
    result = ''
    for ch in s:
        result += _TRANSLIT.get(ch, ch)
    result = _re.sub(r'[^a-z0-9]+', '-', result)
    result = result.strip('-')[:60].rstrip('-')
    if not result:
        result = 'post'
    return f"{note_id}-{result}"


async def _translate_blog_post_to_en(note_id: int, title: str, content: str) -> None:
    """Переводит блог-пост: RU→EN или EN→RU (автоопределение языка оригинала)."""
    try:
        from ai_integration.api_client import get_api_client
        _api_client = get_api_client()
        from models import Session, Note

        # Определяем язык оригинала — если >60% ASCII букв → английский
        _alpha_chars = [c for c in (title + ' ' + content[:500]) if c.isalpha()]
        _ascii_ratio = sum(1 for c in _alpha_chars if ord(c) < 128) / max(len(_alpha_chars), 1)
        _is_en_original = _ascii_ratio > 0.6

        # Limit content passed to API to avoid huge token cost
        content_truncated = content[:4000] if len(content) > 4000 else content

        if _is_en_original:
            # EN→RU: оригинал на английском — переводим на русский
            prompt = (
                f"Translate the following blog post from English to Russian.\n"
                f"Return ONLY a JSON object with two keys: \"title\" and \"content\".\n"
                f"Preserve markdown formatting.\n\n"
                f"TITLE: {title}\n\n"
                f"CONTENT:\n{content_truncated}"
            )
        else:
            # RU→EN: оригинал на русском — переводим на английский
            prompt = (
                f"Translate the following blog post from Russian to English.\n"
                f"Return ONLY a JSON object with two keys: \"title\" and \"content\".\n"
                f"Preserve markdown formatting.\n\n"
                f"TITLE: {title}\n\n"
                f"CONTENT:\n{content_truncated}"
            )

        result = await _api_client.deepseek_analyze(
            prompt=prompt,
            system_prompt="You are a professional translator. Return only valid JSON with keys 'title' and 'content'.",
            temperature=0.3,
            max_tokens=3000,
            parse_json=True,
            timeout=90,
        )
        if not result or not isinstance(result, dict):
            # Try extracting JSON from string response
            import json as _json, re as _re_tr
            if isinstance(result, str):
                m = _re_tr.search(r'\{.*\}', result, _re_tr.DOTALL)
                if m:
                    result = _json.loads(m.group(0))
        if not result or not isinstance(result, dict):
            import logging as _log
            _log.getLogger(__name__).warning(f"[BLOG_TRANSLATE] Failed to parse translation for note_id={note_id}")
            return

        translated_title = result.get('title', '').strip()
        translated_content = result.get('content', '').strip()
        if not translated_title or not translated_content:
            return

        with Session() as db:
            note = db.query(Note).filter_by(id=note_id).first()
            if note:
                if _is_en_original:
                    # Оригинал EN → сохраняем EN в title_en/content_en, перевод RU в title/content
                    note.title_en = title
                    note.content_en = content
                    note.title = translated_title
                    note.content = translated_content
                else:
                    # Оригинал RU → перевод EN в title_en/content_en
                    note.title_en = translated_title
                    note.content_en = translated_content
                db.commit()
                _dir = 'EN→RU' if _is_en_original else 'RU→EN'
                import logging as _log
                _log.getLogger(__name__).info(f"[BLOG_TRANSLATE] {_dir} saved for note_id={note_id}: «{translated_title[:60]}»")
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"[BLOG_TRANSLATE] Error translating note_id={note_id}: {e}")


async def search_notes(
    query: str = None,
    limit: int = 3,
    user_id: int = None,
    session=None,
    close_session: bool = True,
) -> str:
    """Поиск и чтение заметок команды. Возвращает полный контент совпадающих заметок."""
    if not session:
        session = Session()
        close_session = True
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        from models import Note as _Note_sr
        from datetime import datetime as _dt_sr, timezone as _tz_sr, timedelta as _td_sr
        import re as _re_sr

        notes_q = session.query(_Note_sr).filter(
            _Note_sr.user_id == user.id,
            _Note_sr.source == 'chat',
        )

        # Фильтр по запросу — ищем в заголовке и контенте
        if query and query.strip():
            _kws = [w.lower() for w in query.strip().split() if len(w) > 2]
            if _kws:
                from sqlalchemy import or_, func as _func_sr
                _conditions = []
                for _kw in _kws[:5]:
                    _conditions.append(_Note_sr.title.ilike(f'%{_kw}%'))
                    _conditions.append(_Note_sr.content.ilike(f'%{_kw}%'))
                notes_q = notes_q.filter(or_(*_conditions))

        notes = notes_q.order_by(_Note_sr.created_at.desc()).limit(max(1, min(limit, 20))).all()

        if not notes:
            _hint = f" по запросу «{query}»" if query else ""
            return f"Заметок{_hint} не найдено."

        lines = [f"📝 Найдено заметок: {len(notes)}\n"]
        for n in notes:
            _ts = n.created_at.strftime('%d.%m.%Y %H:%M') if n.created_at else '?'
            _title = (n.title or '').strip()
            _content = (n.content or '').strip()
            lines.append(f"──────────────────")
            if _title:
                lines.append(f"📌 {_title}")
            lines.append(f"🕐 {_ts}")
            lines.append(_content[:1500] + ('…' if len(_content) > 1500 else ''))
            lines.append('')

        return '\n'.join(lines)
    except Exception as e:
        logger.error(f"[SEARCH_NOTES] Error: {e}")
        return f"Ошибка чтения заметок: {e}"
    finally:
        if close_session and session:
            session.close()


async def save_note(content: str, title: str = None, user_id: int = None, session=None, source: str = 'chat') -> str:
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

        # --- Rate limit: автопилот не должен спамить заметками ---
        _since_1h = _dt_sn.datetime.utcnow() - _dt_sn.timedelta(hours=1)
        _since_24h = _dt_sn.datetime.utcnow() - _dt_sn.timedelta(hours=24)
        _recent_notes_1h = session.query(Note).filter(
            Note.user_id == user.id,
            Note.created_at >= _since_1h,
            Note.source == 'chat',
        ).count()
        if _recent_notes_1h >= 12:
            logger.info(f"[SAVE_NOTE] Rate limit: {_recent_notes_1h} notes in last hour for user {user.id}")
            return "[INTERNAL] Лимит заметок: уже создано 12+ за последний час. Сохраняй только финальные выводы с конкретными данными — не промежуточные шаги."

        # --- Дедуп: проверяем похожий заголовок за последние 4ч (было 24ч — слишком агрессивно) ---
        _since_dedup = _dt_sn.datetime.utcnow() - _dt_sn.timedelta(hours=4)
        _recent_notes = session.query(Note).filter(
            Note.user_id == user.id,
            Note.created_at >= _since_dedup,
        ).all()

        # Сравнение по заголовку (>70% слов) + по содержимому (>70% слов в 1-м предложении)
        _title_words = set(w for w in _note_title.lower().split() if len(w) > 2)
        _content_first = content.strip().split('.')[0].lower()
        _content_head_words = set(w for w in _content_first.split() if len(w) > 3)
        for _rn in _recent_notes:
            _rn_words = set(w for w in (_rn.title or '').lower().split() if len(w) > 2)
            if _title_words and _rn_words:
                _overlap = len(_title_words & _rn_words) / max(len(_title_words), len(_rn_words))
                if _overlap > 0.70:
                    logger.info(f"[SAVE_NOTE] Dedup title: similar note exists (id={_rn.id}, overlap={_overlap:.0%}): «{_rn.title}»")
                    return f"Похожая заметка уже есть: «{_rn.title}» — новая не создана."
            # Дополнительно: совпадение по началу контента
            if _content_head_words and len(_content_head_words) >= 4:
                _rn_content_head = set(w for w in (_rn.content or '').split('.')[0].lower().split() if len(w) > 3)
                if _rn_content_head:
                    _c_overlap = len(_content_head_words & _rn_content_head) / max(len(_content_head_words), len(_rn_content_head))
                    if _c_overlap > 0.70:
                        logger.info(f"[SAVE_NOTE] Dedup content: similar note exists (id={_rn.id}, c_overlap={_c_overlap:.0%})")
                        return f"Похожая заметка уже есть: «{_rn.title}» — новая не создана."

        # --- Quality filter: блокируем мусорные заметки от автопилота ---
        import re as _re_sn
        _content_strip = content.strip()
        _content_lc = _content_strip.lower()
        # 1. Дампы email-адресов — сохраняем как список контактов, не блокируем
        _emails_sn = _re_sn.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', _content_strip)
        if len(_emails_sn) >= 3:
            # Это реальные контакты — сохраняем с пометкой типа
            _note_title_contacts = (title or f'Контакты ({len(_emails_sn)} email)').strip()
            note = Note(
                user_id=user.id,
                title=_note_title_contacts,
                content=content.strip(),
                source=source if source in ('chat', 'blog') else 'chat',
            )
            session.add(note)
            session.commit()
            return f"Список контактов сохранён ({len(_emails_sn)} email-адресов): «{_note_title_contacts}»"
        # 2. Намерения агента без результата («выполняю», «запускаю» и т.п.)
        _JUNK_INTENTS_SN = (
            'запускаю ', 'выполняю ', 'начинаю ', 'приступаю ', 'погружусь',
            'сейчас проверю', 'проверяю ', 'ищу контакт', 'анализирую запрос',
            'продолжаю работу', 'продолжаю поиск', 'приступаю к выполнению',
        )
        if any(_content_lc.startswith(j) for j in _JUNK_INTENTS_SN):
            return "[INTERNAL] Заметка отклонена: намерения агента записывать не нужно."
        # 3. Слишком короткий / бессодержательный текст
        if len(_content_strip) < 25:
            return "[INTERNAL] Заметка слишком короткая — минимум 25 символов содержательного текста."
        # 4. Список ссылок без пояснений (3+ URL и мало текста)
        _url_count_sn = len(_re_sn.findall(r'https?://\S+', _content_strip))
        _non_url_sn = _re_sn.sub(r'https?://\S+', '', _content_strip).strip()
        if _url_count_sn >= 3 and len(_non_url_sn) < 40:
            return "[INTERNAL] Заметка отклонена: список ссылок без пояснения — добавь аннотацию."

        _source_val = source if source in ('chat', 'blog') else 'chat'

        # ── Граничная проверка блог-публикации: отклоняем выдуманные рыночные данные ──
        if _source_val == 'blog':
            import re as _re_blog_guard
            # Паттерны выдуманных котировок: "$103.13", "Brent $103", "BTC $45 000", "€1 200", "¥12000"
            _price_pattern = _re_blog_guard.compile(
                r'(?:Brent|WTI|нефт[ьи]|баррел[ья]|BTC|биткоин|ETH|S&P|Nasdaq|ММВБ|RTS|индекс)'
                r'.{0,30}?'
                r'(?:\$|€|¥|₽|USD|EUR)\s*\d[\d\s]*(?:[.,]\d+)?'
                r'|(?:\$|€|¥)\s*\d{2,}(?:[.,\s]\d+)?(?:\s*(?:тыс|млн|k|K|M))?(?!\s*(?:в\s+месяц|/мес|рублей|руб\b))',
                _re_blog_guard.IGNORECASE,
            )
            _matches = _price_pattern.findall(content)
            if _matches:
                logger.warning(
                    "[SAVE_NOTE] Blog rejected: fabricated market price detected — %s",
                    _matches[:3],
                )
                return (
                    "[INTERNAL] Статья отклонена: обнаружены конкретные рыночные котировки без источника "
                    f"({', '.join(_matches[:2])}). Рыночные цены меняются каждую минуту — не пиши их без "
                    "вызова инструмента (web_search / http_api_request). Перепиши статью без цифр которых у тебя нет."
                )

        note = Note(
            user_id=user.id,
            title=_note_title,
            content=content.strip(),
            source=_source_val,
        )
        session.add(note)
        session.commit()
        if _source_val == 'blog':
            note.slug = _make_blog_slug(_note_title, note.id)
            session.commit()
            logger.info(f"[SAVE_NOTE] Blog post published: id={note.id}, slug={note.slug!r}, title={_note_title[:60]!r}")
            # Fire-and-forget EN translation
            try:
                import asyncio as _aio_blog
                _aio_blog.get_running_loop().create_task(
                    _translate_blog_post_to_en(note.id, _note_title, content.strip())
                )
            except Exception as _te:
                logger.debug(f"[SAVE_NOTE] EN translation task skipped: {_te}")
            return f"Статья опубликована в блог ASI Biont: «{_note_title}» (https://asibiont.com/blog/{note.slug})"
        # === Векторная память (best-effort, не блокирует event loop) ===
        try:
            from ai_integration.vector_memory import store_memory_background as _vmem_sn
            _vmem_sn(user_id, f"Заметка: «{note.title}». {content[:200]}", {'type': 'note', 'note_id': str(note.id)})
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

            # === Авто-обновление прогресса цели ===
            if task.goal_id:
                try:
                    from models import Goal as _GoalCT
                    _goal_ct = session.query(_GoalCT).filter_by(id=task.goal_id, user_id=user.id).first()
                    if _goal_ct and _goal_ct.status == 'active':
                        # Count total and completed tasks for this goal
                        _total_gt = session.query(Task).filter(
                            Task.user_id == user.id, Task.goal_id == task.goal_id
                        ).count()
                        _done_gt = session.query(Task).filter(
                            Task.user_id == user.id, Task.goal_id == task.goal_id,
                            Task.status == 'completed'
                        ).count()
                        if _total_gt > 0 and not (_goal_ct.metric_target and _goal_ct.metric_target > 0):
                            # Only auto-update % for non-metric goals
                            _new_pct = min(99, int(_done_gt / _total_gt * 100))
                            if _new_pct > (_goal_ct.progress_percentage or 0):
                                _goal_ct.progress_percentage = _new_pct
                                session.commit()
                                logger.info(f"[COMPLETE_TASK] Auto-updated goal '{_goal_ct.title}' progress → {_new_pct}%")
                except Exception as _gct_e:
                    logger.warning(f"[COMPLETE_TASK] Goal progress auto-update failed: {_gct_e}")

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

        # === Векторная память: фиксируем завершение задачи без висящих фоновых task ===
        try:
            _task_mem_text = f"Завершена задача: '{task.title}'"
            if completion_note:
                _task_mem_text += f". Результат: {completion_note[:150]}"
            if task.goal_id:
                from models import Goal as _GoalVM
                _g_vm = session.query(_GoalVM).filter_by(id=task.goal_id, user_id=user.id).first()
                if _g_vm:
                    _task_mem_text += f". Цель: {_g_vm.title}"
            from ai_integration.vector_memory import store_memory as _store_memory_vm
            await _store_memory_vm(
                user.telegram_id,
                _task_mem_text,
                {'type': 'achievement', 'task_id': str(task.id)}
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
                # Берём первые 2-3 содержательные строки (не структурные заголовки) для контекста
                _content_lines = []
                for _ln in _task_lines:
                    _ln_lc = _ln.lower().rstrip(' :')
                    if any(_ln_lc.startswith(h) for h in _STRUCT_HEADERS):
                        continue
                    if _ln.endswith(':') and len(_ln) < 80:
                        continue
                    _content_lines.append(_ln)
                    if len(_content_lines) >= 3:
                        break
                _title_line = _content_lines[0] if _content_lines else ''
                # Склеиваем до 2-3 предложений для показа контекста (не только первая строка)
                _multi = ' '.join(_content_lines).strip()
                _base = _multi or _strip_structured_text(_task_text, _max_len=320)
                # Обрезаем структурные разделители — но ТОЛЬКО если они НЕ в начале строки
                # (чтобы не уничтожать задачи типа "На основе анализа трендов разработай...")
                _split_m = _ren.split(
                    r'(?i)(?<=[а-яёa-z.,])\s+\b(?:используй|детали|описание|данные\s+для\s+работы|ключевые\s+данные|нужно\s+найти|нужно\s+сделать)\b',
                    _base,
                    maxsplit=1,
                )
                if len(_split_m) > 1 and len(_split_m[0].strip()) >= 18:
                    _base = _split_m[0].strip(' ,;:.-')
                if len(_base) < 18 and len(_task_lines) > 1:
                    _base = _strip_structured_text('\n'.join(_task_lines[:3]), _max_len=320)
                _base = _ren.sub(rf'^\s*{_ren.escape(_agent_name)}\s*,?\s*', '', _base, flags=_ren.IGNORECASE).strip(' ,;:.-')
                _is_fem = (_agent_name or '')[-1:] in 'аяАЯ'
                _generic = f'{_agent_name}, продолжи работу по текущей задаче.'
                if not _base:
                    return _generic
                # Показываем до 300 символов — достаточно для 2-3 информативных предложений
                _base = _truncate_by_word(_base, 300)
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
                        'разработать': 'разработай', 'подключить': 'подключи',
                        'выбрать': 'выбери', 'протестировать': 'протестируй',
                        'описать': 'опиши', 'подобрать': 'подбери',
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
                _txt = _strip_structured_text(_txt, _max_len=400)
                _sent = [s.strip() for s in _ren.split(r'(?<=[.!?])\s+', _txt) if s.strip()]
                _txt = ' '.join(_sent[:2]).strip() if _sent else _txt
                _txt = _truncate_by_word(_txt, 280)
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

                # Добавляем активные цели чтобы агент не спрашивал «какую цель обновить?»
                try:
                    from models import Goal as _Goal_del
                    _active_goals_del = session.query(_Goal_del).filter(
                        _Goal_del.user_id == delegator.id,
                        _Goal_del.status == 'active',
                    ).limit(5).all()
                    if _active_goals_del:
                        _goals_str_del = '\n'.join(
                            f"  • {g.title} ({g.progress_percentage or 0}%)" for g in _active_goals_del
                        )
                        _agent_task_text += f"\n\nАктивные цели пользователя (сопоставь по теме задачи; уточни у пользователя только если совсем непонятно):\n{_goals_str_del}"
                except Exception as _ge:
                    logger.debug("[DELEGATE] goals ctx error: %s", _ge)

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
            # GUARD: цели с numeric metric_target не трогаем — прогресс только через metric_current
            if matched.metric_target and matched.metric_target > 0:
                return "OK"  # silently skip — metric goal manages its own percentage
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
                # Полный запрет: агент НЕ должен вручную ставить progress на цели с метриками
                if matched.metric_target and matched.metric_target > 0:
                    actual_pct = int((matched.metric_current or 0) / matched.metric_target * 100)
                    # Self-heal: если в БД завышенный progress — исправляем автоматически
                    if matched.progress_percentage != actual_pct:
                        matched.progress_percentage = actual_pct
                        try:
                            session.commit()
                        except Exception:
                            try:
                                session.rollback()
                            except Exception:
                                pass
                    return (
                        f"У цели '{matched.title}' есть числовая метрика "
                        f"({int(matched.metric_current or 0)}/{int(matched.metric_target)} {matched.metric_unit or ''}, {actual_pct}%). "
                        f"Прогресс рассчитывается автоматически из metric_current. "
                        f"Используй update_goal_progress(metric_current=N) вместо progress=N."
                    )
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

    # ── Специальный случай: "тёплые / открывали / активные" → replied/interested из EmailContact ──
    import re as _re_frct
    _td_low = task_description.lower()
    _WARM_KW = ('открывал', 'проявил активность', 'проявили интерес', 'тёплые', 'теплые',
                'горячие', 'warm contact', 'interested', 'replied', 'ответили', 'ответил')
    if any(kw in _td_low for kw in _WARM_KW):
        try:
            from models import EmailContact as _ECW
            _warm = session.query(_ECW).filter(
                _ECW.user_id == user.id,
                _ECW.status.in_(['replied', 'interested']),
            ).order_by(_ECW.created_at.desc()).limit(limit).all()
            if _warm:
                _lines = [f"{c.name or '?'} <{c.email}> [статус: {c.status}]" for c in _warm]
                result = (
                    f"Тёплые контакты (ответили/заинтересованы), {len(_warm)} чел.:\n"
                    + '\n'.join(_lines)
                    + "\n\nℹ️ Email open-tracking отсутствует — это единственные «активные» контакты в системе. "
                    "Используй send_outreach_email чтобы написать каждому персональное письмо."
                )
                if close_session:
                    session.close()
                return result
            else:
                if close_session:
                    session.close()
                return ("Тёплых контактов (replied/interested) пока нет. "
                        "Email open-tracking отсутствует — отслеживаем только ответившие контакты. "
                        "Используй send_outreach_email для отправки новым контактам.")
        except Exception as _e_w:
            logger.debug("[FIND_RELEVANT] warm contacts lookup failed: %s", _e_w)

    # ── Спецкейс: задача про outreach/email/лиды → из EmailContact (внешние контакты) ──
    import re as _re_frct2
    _OUTREACH_KW = (
        'email', 'outreach', 'лид', 'lead', 'контакт', 'contact', 'рассылк',
        'привлечь', 'найти пользовател', 'найти клиент', 'база',
        'cold email', 'холодн', 'потенциальн', 'новых пользовател',
        'github', 'хабр', 'dev.to', 'разработчик', 'developer',
        # тематические ключевые слова аудиторий (трейдеры, финансы, биотех и т.'д.)
        'трейдер', 'trader', 'финанс', 'financ', 'инвестор', 'investor',
        'предприниматель', 'entrepreneur', 'стартап', 'startup',
        'аудитория', 'целевая', 'потенциальны', 'новых людей',
        'привлеч', 'поиск людей', 'список',
    )
    if any(kw in _td_low for kw in _OUTREACH_KW):
        try:
            from models import EmailContact as _ECO
            _all_ext = session.query(_ECO).filter(
                _ECO.user_id == user.id,
                _ECO.status.notin_(['bounced', 'unsubscribed']),
            ).order_by(_ECO.created_at.desc()).limit(limit).all()
            _new_contacts = [c for c in _all_ext if (c.status or 'new') == 'new']
            _contacted_cs = [c for c in _all_ext if c.status == 'contacted']
            _replied_cs   = [c for c in _all_ext if c.status in ('replied', 'interested')]
            if _all_ext:
                _parts = []
                if _new_contacts:
                    _lines_n = [
                        f"{c.name or '?'} <{c.email}> (src={c.source or '?'})"
                        for c in _new_contacts
                    ]
                    _parts.append(
                        f"✅ Новые контакты (ещё НЕ писали, {len(_new_contacts)} чел.):\n"
                        + '\n'.join(_lines_n)
                    )
                if _replied_cs:
                    _lines_r = [
                        f"{c.name or '?'} <{c.email}> [{c.status}]"
                        for c in _replied_cs
                    ]
                    _parts.append(
                        f"🔥 Ответившие (тёплые, {len(_replied_cs)} чел.):\n"
                        + '\n'.join(_lines_r)
                    )
                if _contacted_cs:
                    _names_c = ', '.join(
                        (c.name or c.email) for c in _contacted_cs[:10]
                    ) + (f" и ещё {len(_contacted_cs)-10}" if len(_contacted_cs) > 10 else '')
                    _parts.append(
                        f"⚠️ Уже получили письмо ({len(_contacted_cs)} чел.) — "
                        f"только follow-up, НЕ новое холодное письмо: {_names_c}"
                    )
                if close_session:
                    session.close()
                return (
                    '\n\n'.join(_parts)
                    + "\n\nℹ️ Это email-контакты из EmailContact (НЕ пользователи платформы). "
                    "Пиши ТОЛЬКО новым контактам (статус new). "
                    "⚠️ contacted = им уже написано, send_outreach_email заблокирует повтор — используй follow-up."
                )
            else:
                # 0 контактов — не падаем дальше на поиск пользователей платформы — сразу отвечаем четко
                if close_session:
                    session.close()
                return (
                    f"База внешних outreach-контактов пуста. "
                    f"Чтобы найти новых людей для '{task_description[:60]}': "
                    f"используй web_search (например, поиск на LinkedIn/GitHub/dev.to/Хабр), "
                    f"затем save_email_contact для каждого найденного человека, "
                    f"затем send_outreach_email. "
                    f"НЕ выдумывай адреса email — используй только реальные адреса из поиска."
                )
        except Exception as _e_o:
            logger.debug("[FIND_RELEVANT] outreach contacts lookup failed: %s", _e_o)


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
    
    stop_words = {'в', 'и', 'с', 'на', 'по', 'для', 'от', 'к', 'о', 'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'что', 'как', 'это', 'не', 'из', 'за', 'то', 'но', 'а'}
    task_keywords = set()

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

        async with _safe_http() as aio_session:
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

        async with _safe_http() as aio_session:
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

        # Очистка от markdown-звёздочек и эмодзи для публичного блога
        import re
        # Убираем **жирный текст** и *курсив*
        content = re.sub(r'\*\*([^\*]+)\*\*', r'\1', content)  # **текст** → текст
        content = re.sub(r'\*([^\*]+)\*', r'\1', content)      # *текст* → текст
        # Убираем эмодзи (Unicode диапазоны)
        content = re.sub(
            r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF\U0001FA70-\U0001FAFF]+',
            '', content
        )
        content = content.strip()
        
        if not content:
            return "Текст поста не может быть пустым после очистки."

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
        if posts_today >= 2 and not force:
            return "[INTERNAL] Пост в ленту уже опубликован (2/день). НЕ сообщай пользователю — переключись на другую задачу (email, research, задачи)."

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
        # Сохраняем поля до любых await — после publish_to_telegram/publish_to_discord
        # сессия может сделать commit с expire_on_commit=True, что детачит объект.
        # Обращение к post.id / post.image_url после этого → DetachedInstanceError.
        _post_id = post.id
        _post_image_url = post.image_url

        post_preview = content[:80] + '...' if len(content) > 80 else content
        has_img = bool(_post_image_url)
        logger.info(f"[CREATE_POST] User {user_id} published post #{_post_id}: '{post_preview}' image={has_img}")

        # ── Кросс-постинг в TG и Discord с той же картинкой ──
        cross_notes = []
        try:
            if getattr(user, 'telegram_channel', None):
                _tg_result = await publish_to_telegram(
                    content=content.strip(),
                    image_url=_post_image_url,
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
                    image_url=_post_image_url,
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

        # Создаём блог-пост (Note source='blog') с прямой ссылкой /blog/{slug}
        _blog_url = 'https://asibiont.com/dashboard'
        try:
            from models import Note as _NoteCP
            _blog_title = content.strip().split('\n')[0][:120].strip()
            if not _blog_title or len(_blog_title) < 5:
                _blog_title = post_preview
            _blog_note = _NoteCP(
                user_id=user.id,
                title=_blog_title,
                content=content.strip(),
                source='blog',
            )
            session.add(_blog_note)
            session.commit()
            _blog_note.slug = _make_blog_slug(_blog_title, _blog_note.id)
            session.commit()
            _blog_url = f'https://asibiont.com/blog/{_blog_note.slug}'
            logger.info(f"[CREATE_POST] Blog note created: id={_blog_note.id}, slug={_blog_note.slug!r}")
            # Fire-and-forget EN translation
            try:
                import asyncio as _aio_cp
                _aio_cp.get_running_loop().create_task(
                    _translate_blog_post_to_en(_blog_note.id, _blog_title, content.strip())
                )
            except Exception:
                pass
        except Exception as _bn_err:
            logger.warning(f"[CREATE_POST] Blog note creation failed: {_bn_err}")

        return (
            f" Пост #{_post_id} опубликован в блог{cross_line}!{' ' if has_img else ''}\n\n"
            f"«{post_preview}»\n\nСсылка на блог: {_blog_url}"
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
        return f" Пост #{post.id} обновлён!\n\nБыло: «{old_preview}»\nСтало: «{new_preview}»\n\nСсылка на ленту: https://asibiont.com/dashboard#feed"
        
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
        # 2 поста в канал в день
        if total_channel_posts_today >= 2 and not force:
            channel = user.telegram_channel or 'канал'
            if not channel.startswith('@') and not channel.startswith('-'):
                channel = f"@{channel}"
            return (
                f"[INTERNAL] В {channel} уже 2 поста сегодня (лимит 2/день). "
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
        return (
            f"По запросу «{query}» ничего не найдено.\n"
            "Как улучшить результат:\n"
            "• Сократи запрос до 2-3 ключевых слов\n"
            "• Убери site: ограничение — оно резко сужает выдачу\n"
            "• Попробуй английские термины (имена, профессии, компании)\n"
            "• Используй research_topic — он умеет анализировать без поиска\n"
            "Пример: «site:github.com python ai developer москва» → «python developer ai москва» или «python AI developer Russia»"
        )

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
                # Загружаем ВСЕ агенты с непустыми ключами, декриптим на Python-стороне
                # (DB-фильтр .contains() не работает если ключи зашифрованы Fernet/obf)
                from ai_integration.autonomous_agent import _decrypt_keys as _dk_av
                _all_agents = _db_sess.query(_UA_av).filter(
                    _UA_av.author_id == _db_user_id,
                    _UA_av.user_api_keys.isnot(None),
                ).all()
                for _ag in _all_agents:
                    _raw_keys = _ag.user_api_keys or ''
                    _decrypted = _dk_av(_raw_keys)
                    for _line in _decrypted.splitlines():
                        _line = _line.strip()
                        if _line.startswith('ALPHAVANTAGE_API_KEY=') or _line.startswith('ALPHA_VANTAGE_API_KEY='):
                            _val = _line.split('=', 1)[1].strip()
                            if _val and len(_val) > 4 and _val.lower() not in ('none', 'null', 'your_key_here', 'xxx', '...'):
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
            "⚠️ Котировки недоступны: ALPHAVANTAGE_API_KEY не настроен.\n"
            "Получи бесплатный ключ на alphavantage.co → добавь в настройки агента → API-ключи:\n"
            "ALPHAVANTAGE_API_KEY=твой_ключ"
        )

    def _check_av_ratelimit(d: dict) -> str | None:
        """Возвращает сообщение если Alpha Vantage вернул ошибку лимита/ключа."""
        info = d.get("Information") or d.get("Note") or d.get("Error Message")
        if not info:
            return None
        info_lc = info.lower()
        if "rate limit" in info_lc or "per day" in info_lc or "standard api" in info_lc or "per minute" in info_lc:
            return "⏳ Лимит запросов Alpha Vantage исчерпан на сегодня (25 запросов/день бесплатно). Лимит сбрасывается в 00:00 UTC."
        if "invalid api key" in info_lc or "invalid" in info_lc:
            return "❌ Неверный ALPHAVANTAGE_API_KEY. Проверь ключ на alphavantage.co"
        return f"⚠️ Alpha Vantage API: {info[:200]}"

    symbol = symbol.strip().upper()
    
    try:
        # Alpha Vantage убрал commodity эндпоинты (BRENT/WTI) из бесплатного API
        if data_type == "oil" or symbol in ("BRENT", "WTI"):
            return (
                f"⚠️ Alpha Vantage не поддерживает прямые котировки нефти ({symbol}) в бесплатном API.\n\n"
                "Альтернативы:\n"
                "1️⃣ web_search(query='цена нефти Brent сегодня') — актуальная цена из новостей\n"
                "2️⃣ get_stock_price(symbol='BNO', data_type='quote') — ETF следующий за Brent\n"
                "3️⃣ get_stock_price(symbol='USO', data_type='quote') — ETF следующий за WTI\n"
                "4️⃣ http_api_request к другим API: oilpriceapi.com, tradingeconomics.com"
            )
        
        if data_type == "forex" or "/" in symbol:
            url = (
                f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE"
                f"&from_currency={symbol}&to_currency=USD&apikey={_api_key}"
            )
            req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urllib_req.urlopen(req, timeout=15) as r:
                d = _json.loads(r.read().decode())
            info = d.get("Realtime Currency Exchange Rate", {})
            _rl2 = _check_av_ratelimit(d)
            if _rl2:
                return _rl2
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
            _rl3 = _check_av_ratelimit(d)
            if _rl3:
                return _rl3
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
        generated_message = await _generate_user_