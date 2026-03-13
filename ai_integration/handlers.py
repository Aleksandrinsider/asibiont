# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timedelta, timezone
import pytz
import requests
import aiohttp
from models import Session, Task, User, UserProfile, Subscription, Goal, Post, PostLike, PostView, Comment, UserMessage, EmailCampaign, EmailOutreach, Anchor, AnchorPriority
from sqlalchemy import or_, and_, func

from .memory import encrypt_data, decrypt_data, LongTermMemory
from .utils import parse_time_to_datetime, generate_unified_recommendations
from .task_search import find_task_flexible
from .dialog_context import get_user_context, resolve_task_reference
from . import marketing_agent
from config import OPENWEATHERMAP_API_KEY, NEWSAPI_API_KEY, encrypt_token, decrypt_token

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

    # САНИТИЗАЦИЯ: убираем утечки system prompt из title
    import re as _re_san
    # Паттерны утечек: [РОЛЬ], [АВТОПИЛОТ], ТВОЯ РОЛЬ:, etc.
    if _re_san.match(r'^\[?(РОЛЬ|АВТОПИЛОТ|ЦЕЛИ|КОНКРЕТНЫЙ ПЛАН)\]?', title, _re_san.IGNORECASE):
        title = _re_san.sub(r'^\[?(РОЛЬ|АВТОПИЛОТ|ЦЕЛИ|КОНКРЕТНЫЙ ПЛАН)\]?\s*:?\s*(Ты:?\s*)?', '', title, flags=_re_san.IGNORECASE).strip()
    # Если после санитизации title стал > 80 символов или содержит personality-текст — берём первые 8 слов
    if len(title) > 80 or any(p in title.lower() for p in ('специалист в команде', 'циник,', 'pr служба', 'координатор команды')):
        _words = [w for w in title.split() if len(w) > 2][:8]
        title = ' '.join(_words) if _words else title[:80]
    if not title:
        return 'Название задачи пустое после очистки.'

    # УМНОЕ СОКРАЩЕНИЕ НАЗВАНИЯ: если слишком длинное, пытаемся извлечь суть
    original_title = title
    word_count = len(title.split())
    if len(title) > 120 or word_count > 15:
        logger.warning(f"[ADD_TASK] Title too long ({len(title)} chars, {word_count} words), attempting smart extraction")
        # Попытка извлечь ключевые слова (простая эвристика)
        # Убираем стоп-слова и берём первые 8 значимых слов
        stop_words = ['нужно', 'надо', 'необходимо', 'давай', 'создай', 'добавь', 'напомни', 'поставь', 'я', 'мне', 'для', 'чтобы', 'как']
        words = [w for w in title.split() if w.lower() not in stop_words and len(w) > 2]
        if len(words) > 8:
            title = ' '.join(words[:8])
            logger.info(f"[ADD_TASK] Title shortened: '{original_title}' -> '{title}'")
        else:
            title = ' '.join(words)
            logger.info(f"[ADD_TASK] Title cleaned: '{original_title}' -> '{title}'")

    # УМНОЕ СОКРАЩЕНИЕ ОПИСАНИЯ: максимум 200 символов
    if description and len(description) > 200:
        original_desc = description
        description = description[:197] + "..."
        logger.warning(f"[ADD_TASK] Description truncated from {len(original_desc)} to 200 chars")

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
    _stop_t = {'для', 'или', 'что', 'как', 'это', 'при', 'через', 'the', 'and', 'for'}
    _new_t_sig = {w for w in _title_lc.split() if len(w) > 3} - _stop_t
    def _task_is_dup(t):
        _et = t.title.lower().strip()
        if _et == _title_lc:
            return True
        # contains-check (одно вложено в другое)
        if _title_lc in _et or _et in _title_lc:
            return True
        # 3+ общих значимых слова
        _et_sig = {w for w in _et.split() if len(w) > 3} - _stop_t
        return len(_new_t_sig & _et_sig) >= 3
    existing = next((t for t in existing_tasks if _task_is_dup(t)), None)
    if existing:
        logger.warning(f"[ADD_TASK] Duplicate pending task found: '{existing.title}' (id={existing.id})")
        if close_session:
            session.close()
        return _t(user_id, 'task_duplicate', title=existing.title)
    
    # Create new task — время ОБЯЗАТЕЛЬНО
    if not reminder_time:
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

    # Automation: Real-time триггер для задач (доступно всем, оплата токенами)
    try:
        from ai_integration.premium_simple import trigger_premium_automation_realtime
        import asyncio
        
        logger.info(f"[ADD_TASK] Triggering automation for task {task_id}")
        asyncio.create_task(
            trigger_premium_automation_realtime(
                premium_user_id=user.telegram_id,
                task_id=task_id,
                task_description=f"{title}. {description}" if description else title
            )
        )
        logger.info(f"[ADD_TASK] Automation triggered for task {task_id}")
        
        # Проверяем рекомендации от других пользователей
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
                            action_type='started',
                            task_title=title,
                            original_goal=None
                        )
                        logger.info(f"[ADD_TASK] Notified {recommender_id} about partner {user.telegram_id} starting task")
            except Exception as e:
                logger.warning(f"[ADD_TASK] Failed to notify about partner progress: {e}")
    except Exception as e:
        logger.warning(f"[ADD_TASK] Failed to trigger automation: {e}")

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

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg

# set_recurring_task removed - feature not critical, required subscription

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
                    content=completion_note[:200] if completion_note else None,
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
                        except Exception:
                            pass

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
    
    if not delegated_to_username or delegated_to_username.strip() == "":
        logger.error("[DELEGATE] delegated_to_username is empty or None")
        return "ERROR: Получатель не указан"
    
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
        _recip_check = delegated_to_username.replace("@", "").lower().strip()
        try:
            from models import UserAgent as _UA_chk, AgentSubscription as _AS_chk
            import json as _jj
            import re as _ren
            from .autonomous_agent import _exec_agent_for_director as _exec_dir
            from .autonomous_agent import _save_interaction_for_director as _save_ifd
            import json as _json_ag

            # Поддержка нескольких имён: "Кристина и Марк", "Кристина, Марк" → ['кристина', 'марк']
            _name_parts = [p.strip() for p in _ren.split(r'\s+и\s+|\s+and\s+|,\s*|;\s*', _recip_check) if p.strip() and len(p.strip()) > 1]
            if not _name_parts:
                _name_parts = [_recip_check]
            logger.info(f"[DELEGATE] Looking for agents: {_name_parts} (user_db_id={delegator.id})")

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
                    except Exception:
                        pass
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
                            _agent_task = Task(
                                user_id=delegator.id,
                                title=title[:255],
                                description=encrypt_data(description[:500] if description else ''),
                                source='agent',
                                created_by_agent_id=_agent_recipient.id,
                                delegated_to_username=_agent_name,
                                status='pending',
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
                            _dir_json = _json_ag.dumps({
                                '__agent': {
                                    'name': 'ASI Biont',
                                    'id': 0,
                                    'avatar_url': '/static/asibiont.svg',
                                },
                                'text': f'{_agent_name}, {_agent_task_text[:300]}',
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
                        except Exception:
                            pass

                    # Очищаем DSML-теги из ответа
                    try:
                        from .utils import clean_technical_details as _ctd_r
                        _result = _ctd_r(_result).strip() or _result
                    except Exception:
                        pass

                    # Очищаем чрезмерное форматирование (bullet-списки, лишние пробелы)
                    _result = _ren.sub(r'\n{3,}', '\n\n', _result)  # не более 2 переносов подряд
                    _result = _ren.sub(r'^\s*[•\-\*]\s*', '', _result, flags=_ren.MULTILINE)  # убираем маркеры списков

                    # Записываем ответ агента в чат (видно на дашборде с аватаркой)
                    try:
                        _av = _agent_dict.get('avatar_url', '')
                        _resp_json = _json_ag.dumps({
                            '__agent': {
                                'name': _agent_name,
                                'id': _agent_recipient.id,
                                'avatar_url': _av,
                            },
                            'text': _result,
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
                        ))
                        # Помечаем Task как выполненную
                        if _agent_task_id:
                            _at = session.query(Task).get(_agent_task_id)
                            if _at:
                                _at.status = 'completed'
                                _at.completion_notes = _result[:500]
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
            return "SELF_DELEGATION_ERROR: Нельзя делегировать задачу самому себе"

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
            except Exception:
                pass

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
            except Exception:
                pass

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
                    result += f"'{task.title}' просрочена на {delay_str}"
                    if i < len(priority_tasks) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting priority task time: {e}")
                    result += f"'{task.title}'"
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
                    result += f"'{task.title}' в {time_str}"
                    if i < len(today_tasks[:5]) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting today task time: {e}")
                    result += f"'{task.title}'"
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
                    result += f"'{task.title}' в {time_str}"
                    if i < len(upcoming_tasks[:3]) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting upcoming task time: {e}")
                    result += f"'{task.title}'"
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
                        result += f"'{task.title}' {time_str}"
                    else:
                        result += f"'{task.title}'"
                    if i < len(remaining_later) - 1:
                        result += ", "
                    else:
                        result += ". "
                except Exception as e:
                    logger.warning(f"[TASKLIST] Error formatting later task time: {e}")
                    result += f"'{task.title}'"
                    if i < len(remaining_later) - 1:
                        result += ", "
                    else:
                        result += ". "
        
        # Показываем задачи без времени — это проблема!
        if no_time_tasks:
            result += f" ЗАДАЧИ БЕЗ ВРЕМЕНИ (нужно установить напоминание!): "
            for i, task in enumerate(no_time_tasks):
                result += f"'{task.title}'"
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
        if partner_user:
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


def update_goal_progress(goal_title=None, progress=None, status=None, notes=None, metric_current=None, user_id=None, session=None):
    """Обновить прогресс или статус цели
    
    Args:
        goal_title: Название или часть названия цели для поиска
        progress: Новый процент прогресса (0-100) — для целей без метрики
        status: Новый статус (active, completed, paused, cancelled)
        notes: Заметки о прогрессе
        metric_current: Текущее значение метрики (авто-расчёт процента)
        user_id: Telegram ID
        session: SQLAlchemy session
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
        
        # Обработка metric_current — автоматический расчёт процента
        if metric_current is not None and matched.metric_target:
            try:
                mc = float(metric_current)
                # GUARD: metric_current должен увеличиться хотя бы на 1 целую единицу
                _old_mc = float(matched.metric_current or 0)
                if mc <= _old_mc:
                    return f"metric_current ({mc}) не больше текущего ({_old_mc}). Обновляй ТОЛЬКО когда нашёл РЕАЛЬНОГО нового пользователя/контакт."
                if mc - _old_mc < 1.0:
                    return f"Прирост метрики слишком мал ({mc - _old_mc:.1f}). Увеличивай на целые единицы — 1 единица = 1 реальный найденный пользователь."
                # RATE LIMIT: максимум 1 обновление метрики за 3 часа
                try:
                    from models import AgentActivityLog as _AAL_rl
                    _recent_updates = session.query(_AAL_rl).filter(
                        _AAL_rl.user_id == user.id,
                        _AAL_rl.activity_type == 'goal_updated',
                        _AAL_rl.ref_id == matched.id,
                        _AAL_rl.created_at >= datetime.now() - timedelta(hours=3),
                    ).count()
                    if _recent_updates >= 1:
                        return f"Метрика цели '{matched.title}' уже обновлялась менее 3ч назад. Подожди перед следующим обновлением. Метрика обновляется только при РЕАЛЬНОМ новом результате."
                except Exception:
                    pass
                matched.metric_current = mc
                pct = int(mc / matched.metric_target * 100)
                pct = max(0, min(100, pct))
                matched.progress_percentage = pct
                changes.append(f"метрика: {int(mc)}/{int(matched.metric_target)} {matched.metric_unit or ''} ({pct}%)")
                if pct >= 100 and matched.status == 'active':
                    matched.status = 'completed'
                    matched.completed_at = datetime.now()
                    changes.append("статус: завершено! ")
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
                    if abs(pct - actual_pct) > 10:
                        return f"У цели '{matched.title}' есть числовая метрика ({int(matched.metric_current or 0)}/{int(matched.metric_target)}). Обновляй через metric_current, а не progress."
                matched.progress_percentage = pct
                changes.append(f"прогресс: {pct}%")
                if pct == 100 and matched.status == 'active':
                    matched.status = 'completed'
                    matched.completed_at = datetime.now()
                    changes.append("статус: завершено! ")
            except (ValueError, TypeError):
                pass
        
        if status:
            valid = {'active', 'completed', 'paused', 'cancelled'}
            if status in valid:
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
            except Exception:
                pass
            # Очистить conversation_history чтобы бот не цитировал старые цели
            try:
                from .conversation_history import clear_conversation_history
                clear_conversation_history(user_id)
            except Exception:
                pass
            session.commit()
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
        except Exception:
            pass
        
        # Очистить conversation_history чтобы бот не цитировал удалённую цель
        try:
            from .conversation_history import clear_conversation_history
            clear_conversation_history(user_id)
        except Exception:
            pass
        
        session.commit()
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
                except Exception:
                    pass
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
                except Exception:
                    pass
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
                except Exception:
                    pass
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
        return f"Запомнил: «{rule[:120]}»"
    except Exception as e:
        logger.warning(f"[SAVE_RULE] Failed: {e}")
        return "Не удалось сохранить правило."
    finally:
        if close_session:
            session.close()


def find_relevant_contacts_for_task(task_description: str, user_id: int = None, limit: int = 5, session=None) -> str:
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
                    except Exception:
                        pass
                # Чекпоинты
                for ctype in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    cjob = f"task_overdue_{task_db_id}_{ctype}_{user_id}"
                    try:
                        if REMINDER_SERVICE.scheduler.get_job(cjob):
                            REMINDER_SERVICE.scheduler.remove_job(cjob)
                            logger.info(f"[DELETE_TASK] Removed checkpoint job {cjob}")
                    except Exception:
                        pass
                cp13 = f"task_checkpoint_{task_db_id}_1_3_{user_id}"
                try:
                    if REMINDER_SERVICE.scheduler.get_job(cp13):
                        REMINDER_SERVICE.scheduler.remove_job(cp13)
                except Exception:
                    pass
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
            return " Сегодня пост уже опубликован (лимит — 1 пост в день). Следующий можно опубликовать завтра."

        post = Post(
            user_id=user.id,
            username=user.username or user.first_name or f"user_{user.telegram_id}",
            content=content.strip(),
            image_url=(image_url.strip() if image_url and image_url.strip() else None),
            created_at=dt.datetime.now(dt.timezone.utc)
        )
        
        session.add(post)
        session.commit()
        
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
        except Exception:
            pass
        
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
        if total_channel_posts_today >= 1 and not force:
            channel = user.telegram_channel or 'канал'
            if not channel.startswith('@') and not channel.startswith('-'):
                channel = f"@{channel}"
            return (
                f" Сегодня пост в {channel} уже был опубликован.\n"
                f"Лимит — 1 пост в канал в день, чтобы не спамить подписчиков.\n"
                f"Следующий пост можно опубликовать завтра."
            )
        
        # Если content это JSON строка от generate_marketing_content, парсим
        try:
            import json
            content_data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            content_data = content
        
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


async def web_search(query: str, user_id: int = None, session=None):
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
            return f" Лимит: максимум 3 сообщения в день одному пользователю. Уже отправлено: {sent_today}"
        
        # Генерируем сообщение через AI
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
        
        intent_labels = {
            'meeting': 'согласование встречи',
            'collaboration': 'предложение сотрудничества',
            'idea': 'обмен идеей / предложение',
            'project_invite': 'приглашение в проект',
            'question': 'вопрос'
        }
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
        
        limit = min(max(limit, 1), 5)
        
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
            return " Всем подходящим пользователям уже отправлены сообщения сегодня. Попробуй завтра или расширь поиск."
        
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
            return " Дневной лимит исходящих сообщений (50) исчерпан. Попробуй завтра."
        
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
    """Отправляет сообщение в Telegram асинхронно."""
    from config import TELEGRAM_TOKEN
    import aiohttp
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(url, json={"chat_id": chat_id, "text": text}, 
                                      timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                text_body = await resp.text()
                raise Exception(f"Telegram API error: {resp.status} {text_body[:200]}")


def _send_telegram_message_sync(chat_id, text):
    """Sync wrapper — runs async version. Отправляет сообщение в Telegram."""
    from config import TELEGRAM_TOKEN
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"Telegram API error: {resp.status_code} {resp.text[:200]}")


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
            
            line = f"{status_icon} {sender_name} ({intent_str}, {time_ago}): {msg.message_text[:150]}{'...' if len(msg.message_text) > 150 else ''}"
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
    'info', 'contact', 'contacts', 'hello', 'hi', 'support', 'sales',
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
    'community', 'social', 'director', 'manager', 'commercial',
    'advertising', 'ads', 'advert', 'adv', 'ad', 'reklama',
    'booking', 'reservations',
    'customerservice', 'cs', 'tech', 'technical', 'ops', 'operations',
    'compliance', 'procurement', 'reception', 'frontdesk', 'helpdesk',
    'itsupport', 'it', 'devops', 'sysadmin', 'accounts', 'accounting',
    'finance', 'payroll', 'hq', 'headquarters', 'main', 'central',
    'web', 'website', 'webteam', 'digital', 'online',
    'noc', 'network', 'infra', 'infrastructure', 'platform',
    'dev', 'development', 'design', 'creative', 'ux', 'ui', 'product',
}

# Паттерны в email-prefix которые указывают на корпоративный/generic email
_GENERIC_PATTERNS = {'contact', 'support', 'info', 'admin', 'sales', 'help',
                     'press', 'media', 'billing', 'noreply', 'service',
                     'newsletter', 'unsubscribe', 'notification',
                     'partner', 'business', 'marketing', 'event',
                     'booking', 'advertis', 'commercial', 'investor'}


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
    if domain in ('example.com', 'test.com', 'localhost', 'email.com',
                  'domain.com', 'yoursite.com', 'website.com', 'site.com',
                  'company.com', 'placeholder.com'):
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

    return False


# Кэш MX-проверок домена: domain → bool (имеет MX)
_mx_cache: dict[str, bool] = {}


async def _check_mx_record(domain: str) -> bool:
    """Проверяет наличие MX-записей у домена через DNS. Кэширует результат."""
    domain = domain.lower().strip('.')
    if domain in _mx_cache:
        return _mx_cache[domain]

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

    _mx_cache[domain] = has_mx
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
                "ГЛАВНЫЙ КРИТЕРИЙ: выбирай платформы, где САМИ СПЕЦИАЛИСТЫ публикуют свои "
                "контакты (email, сайт), потому что хотят быть нанятыми или найденными клиентами.\n"
                "Это НЕ биржи вакансий (hh.ru, superjob) — там только HR.\n"
                "НУЖНЫ: каталоги специалистов, фриланс-биржи, профессиональные соцсети.\n\n"
                "Примеры платформ по нишам (используй как образец, адаптируй под аудиторию):\n"
                "  QA/тестировщики → career.habr.com/resumes?q=qa, fl.ru/users/?skills=тестирование, kwork.ru/search?query=qa+тестирование&type=seller;\n"
                "  разработчики/IT → career.habr.com/resumes, fl.ru/users, habr.com/ru/search/?target_type=users;\n"
                "  фрилансеры (любые) → fl.ru/users, kwork.ru/seller, freelancehunt.com/freelancers;\n"
                "  психологи/коучи → b17.ru/specialists, psycabi.net/psy, profi.ru/psiholog;\n"
                "  маркетологи/SMM → tenchat.ru, cossa.ru/people, vc.ru/@;\n"
                "  дизайнеры → behance.net/search, tenchat.ru, kwork.ru/search?query=дизайн&type=seller;\n"
                "  предприниматели → spark.ru/startup/search, vc.ru/search, tenchat.ru;\n"
                "  любые специалисты → profi.ru/search, youdo.com/user, repetitors.info."
            )
        else:
            _lang_instruction = (
                "Audience is English-speaking. Use international platforms.\n\n"
                "KEY CRITERION: choose platforms where SPECIALISTS THEMSELVES publish their "
                "email/contact because they want to be hired or found by clients.\n"
                "NOT job boards (indeed, glassdoor) — those only have HR contacts.\n"
                "NEED: personal profile directories, freelance marketplaces, specialist catalogs.\n\n"
                "Examples by niche (adapt to the actual audience):\n"
                "  QA/testers → upwork.com/search/profiles/?q=qa+tester, github.com/search?q=qa+automation, testlio.com;\n"
                "  developers → upwork.com/freelancers, freelancer.com/users, github.com/search;\n"
                "  coaches/therapists → psychologytoday.com/us/therapists, noomii.com/coaches;\n"
                "  lawyers → avvo.com/find-a-lawyer, martindale.com;\n"
                "  designers → behance.net/search, dribbble.com/designers;\n"
                "  marketers → clarity.fm/search, marketingprofs.com/experts;\n"
                "  any professionals → bark.com/professionals, thumbtack.com/pro, about.me/search."
            )
        _prompt = (
            f"Target audience: {target_audience[:300]}\n"
            f"Campaign goal: {goal[:150]}\n"
            f"Offer/product: {offer[:150]}\n\n"
            f"{_lang_instruction}\n\n"
            f"Analyze the audience and generate 10 direct search/listing URLs where people "
            f"of THIS EXACT audience type have PUBLIC email addresses on their profiles.\n"
            f"Think about: what platforms do THESE PEOPLE use? Where do they list their contacts?\n"
            f"Use keyword '{_kw_first}' in search URLs where applicable (URL-encode spaces as +).\n"
            f"Return ONLY valid JSON array: "
            f'[{{"url": "https://...", "desc": "platform + why these users have public emails"}}]'
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
            except Exception:
                pass
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
            _agents_with_keys = session.query(_UA_gl).filter(
                _UA_gl.author_id == user.id,
                _UA_gl.user_api_keys.isnot(None),
            ).all()
            for _ag_gl in _agents_with_keys:
                for _line in (_ag_gl.user_api_keys or '').splitlines():
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

    # Определяем, техническая ли аудитория (GitHub полезен только для tech)
    _all_text = f"{target_audience} {goal} {offer}".lower()
    _tech_markers = [
        'python', 'javascript', 'typescript', 'react', 'node', 'django',
        'fastapi', 'flask', 'telegram', 'bot', 'ai', 'ml', 'machine learning',
        'data science', 'blockchain', 'web3', 'devops', 'mobile', 'ios',
        'android', 'rust', 'golang', 'java', 'php', 'ruby', 'swift',
        'flutter', 'vue', 'angular', 'nextjs', 'developer', 'разработ',
        'программист', 'engineer', 'инженер', 'api', 'backend', 'frontend',
        'fullstack', 'open source', 'github', 'code', 'coding', 'software',
        # QA / тестировщики — IT-роль, GitHub и Habr Career релевантны
        'тестировщ', 'tester', 'testing', 'qa ', 'quality assurance',
        'автоматизац', 'selenium', 'cypress', 'appium', 'pytest',
        # Аналитики / продуктовые роли
        'аналитик', 'analyst', 'продуктолог', 'product manager', 'agile', 'scrum',
        # Общие IT-маркеры
        'it-', 'it специал', 'ит специал', 'технолог', 'startup', 'стартап',
        'saas', 'app ', 'приложен', 'платформ', 'сервис для',
    ]
    _is_tech_audience = any(t in _all_text for t in _tech_markers)

    # ══════════════════════════════════════════════════════════════════════
    # PASS 0: GitHub API — ТОЛЬКО для технической аудитории
    # (маркетологи, дизайнеры, бизнес и т.д. — GitHub бесполезен)
    # ══════════════════════════════════════════════════════════════════════
    github_leads = []
    if _is_tech_audience:
        try:
            gh_queries = []
            _found_techs = [t for t in _tech_markers if t in _all_text and len(t) > 2]
            
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
                gh_queries.append(f"{_gh_techs[0]}")
                for tech in _gh_techs[:3]:
                    gh_queries.append(f"{tech} developer")
            elif core_kw:
                gh_queries.append(core_kw)
            if _has_cyrillic:
                # Для русской аудитории — приоритет на RU-локацию
                _gh_main = _gh_techs[0] if _gh_techs else core_kw
                gh_queries.insert(0, f"{_gh_main} location:Russia")
                gh_queries.append(f"{_gh_main} location:Moscow")
                gh_queries.append(f"{_gh_main} location:Saint Petersburg")
            
            gh_queries = gh_queries[:6]  # увеличили лимит для RU
            if gh_queries:
                # Rotate GitHub page: page 1 for first 15 emails_sent, page 2 for next 15, etc.
                # This ensures each email_need_leads run explores a fresh set of users
                _gh_page = max(1, (campaign.emails_sent or 0) // 15 + 1)
                logger.info(f"[AUTO_LEADS] Tech audience → GitHub search page={_gh_page}: {gh_queries}")
                github_leads = await api.github_multi_search(
                    queries=gh_queries,
                    max_users_per_query=20,
                    page=_gh_page,
                    github_token=github_token or None,
                )
                for lead in github_leads:
                    em = lead.get('email', '').lower().strip('.')
                    if em and not _is_generic_email(em):
                        all_emails_raw.add(em)
                logger.info(f"[AUTO_LEADS] GitHub found {len(github_leads)} users with email")
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

            # AI определяет: полезен ли hh.ru для этой кампании, и какой запрос использовать
            # B2B (клиенты, партнёры, компании) → hh даёт HR/CTO = релевантные контакты
            # B2C (индивидуальные специалисты, тестировщики, фрилансеры) → hh не поможет
            _hh_decide_prompt = (
                f"Campaign goal: {goal[:200]}\n"
                f"Target audience: {target_audience[:200]}\n"
                f"Offer: {offer[:100]}\n\n"
                f"Task: decide if hh.ru job vacancy API is useful for this campaign.\n"
                f"hh.ru API returns: HR managers and hiring contacts at companies — NOT individual job seekers.\n\n"
                f"Return JSON: {{\"use_hh\": true/false, \"queries\": [\"query1\", \"query2\"], \"reason\": \"...\"}}\n"
                f"Set use_hh=true ONLY if the target is companies/employers/HR/business decision-makers.\n"
                f"Set use_hh=false if target is individual professionals, freelancers, end-users, or consumers.\n"
                f"queries: 1-2 Russian hh.ru job search queries to find companies in the right niche.\n"
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
                except Exception:
                    pass

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
                    except Exception:
                        pass

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
        except Exception:
            pass
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
                f"Generate 6 web search queries to find personal email addresses of people matching:\n"
                f"Target audience: {target_audience[:200]}\n"
                f"Goal: {goal[:150]}\n"
                f"Language preference: {_q_lang}\n"
                f"Rules:\n"
                f"- Each query must target pages where people show their email (personal sites, portfolios, contact pages)\n"
                f"- Include: site:about.me, личный блог, portfolio, 'email' or 'написать мне', профессиональные профили\n"
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
                except Exception:
                    pass
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
                except Exception:
                    pass

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
        except Exception:
            pass
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
4. SKIP: info@, contact@, support@, sales@, admin@, noreply@, and any corporate/generic emails.
5. SKIP: emails from unrelated people (random commenters, unrelated authors, etc.)
6. Better to return 3 RELEVANT leads than 15 irrelevant ones.{_lang_filter_hint}

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
3. SKIP: info@, contact@, support@, sales@, admin@, noreply@ — only PERSONAL emails.
4. If you can't determine why a person matches the target audience, DON'T include them.
5. Better to return 0 leads than add irrelevant people.

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
        except Exception:
            pass

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
                parsed_leads.append({
                    'email': em,
                    'name': '',
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
        except Exception:
            pass
        if not _os_leads.getenv('RESEND_API_KEY') and not _has_personal_resend_h:
            _intg_hints.append(
                "💡 Для отправки писем нужен RESEND_API_KEY "
                "(добавь в настройки агента → API-ключи → RESEND_API_KEY=re_...). "
                "Регистрация бесплатна: resend.com"
            )
        _hint_msg = ("\n⚠️ Интеграции для улучшения поиска: \n" + "\n".join(_intg_hints)) if _intg_hints else ""
        return 0, _hint_msg

    logger.info(f"[AUTO_LEADS] Found {len(parsed_leads)} leads for campaign #{campaign.id}: "
                f"{[l.get('email') for l in parsed_leads[:10]]}")

    # Добавляем через add_email_leads (централизованная логика с дедупом)
    leads_json = json.dumps(parsed_leads[:30], ensure_ascii=False)  # Увеличили лимит до 30
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

    return count, ""

async def start_email_campaign(
    name: str,
    goal: str,
    target_audience: str,
    offer: str,
    sender_name: str = None,
    sender_email: str = None,
    tone: str = 'professional',
    max_emails: int = 0,
    daily_limit: int = 50,
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
                    ex.daily_limit = min(daily_limit, 50)
                if max_emails and max_emails > (ex.max_emails or 0):
                    ex.max_emails = max_emails
                session.commit()
                lang = getattr(user, 'language_code', 'ru') or 'ru'
                if lang == 'en':
                    return f" Campaign #{ex.id} «{ex.name}» already exists and is active! Updated daily_limit to {ex.daily_limit}. Leads will be found automatically."
                return f" Кампания #{ex.id} «{ex.name}» уже существует и активна! Обновил daily_limit до {ex.daily_limit}. Лиды будут найдены автоматически."

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
            daily_limit=min(daily_limit, 50),
            status='active',
        )
        session.add(campaign)
        session.commit()

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
            campaign.daily_limit = min(max(1, int(daily_limit)), 50)
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

        # ── GUARD: не отправлять email на адрес самого пользователя ──
        _rcpt = (recipient_email or '').strip().lower()
        _user_email = (getattr(user, 'email', '') or '').strip().lower()
        if _rcpt and _user_email and _rcpt == _user_email:
            return f" Нельзя отправлять outreach на собственный email ({_rcpt}). Найди другого получателя."

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
            return " Resend API не настроен. Добавьте RESEND_API_KEY в настройки агента (API-ключи) или Railway Variables."

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
            return f" Дневной лимит ({campaign.daily_limit} писем) исчерпан. Попробуй завтра."

        # Глобальный дневной лимит: не более 50 УНИКАЛЬНЫХ получателей на пользователя в сутки
        GLOBAL_DAILY_LIMIT = 50
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
            return f" Достигнут лимит: сегодня уже написали {global_recipients_today} новым получателям (макс {GLOBAL_DAILY_LIMIT}/день). Продолжим завтра."

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
        # 1. Не слать тому, кому уже отправляли из другой кампании последние 30 дней
        CROSS_CAMPAIGN_COOLDOWN_DAYS = 30
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

        # MX-проверка домена получателя
        mx_valid, mx_err = _validate_email_domain(recipient_email)
        if not mx_valid:
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
                        'text': body,
                        'headers': {'List-Unsubscribe': f'<{_unsub_url}>'},
                    },
                    timeout=_aiohttp.ClientTimeout(total=15),
                )
                resp_data = await resp.json()
                if resp.status in (200, 201):
                    resend_id = resp_data.get('id')
                    logger.info(f"[EMAIL_OUTREACH] Sent to {recipient_email}: {resend_id}")
                    # Сбрасываем запись ошибки при успехе
                    try:
                        from .service_health import clear_error as _clr_svc
                        _clr_svc('resend')
                    except Exception:
                        pass
                else:
                    err = resp_data.get('message', str(resp_data))
                    logger.error(f"[EMAIL_OUTREACH] Resend error: {resp.status} {err}")
                    try:
                        from .service_health import record_error as _rec_svc
                        _rec_svc('resend', f'HTTP {resp.status}: {err}', code=resp.status, detail=str(resp_data)[:300])
                    except Exception:
                        pass
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
            )
            session.add(outreach)
        campaign.emails_sent = (campaign.emails_sent or 0) + 1
        # Ставим follow-up через 3 дня
        outreach.next_follow_up_at = dt.now(tz.utc) + timedelta(days=3)

        # Логируем в AgentActivityLog для ленты активности
        try:
            from models import AgentActivityLog
            _name_part = f" ({recipient_name})" if recipient_name else ""
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='email',
                title=f"Outreach → {recipient_email}{_name_part}",
                content=f"Тема: {subject}\n\n{body[:500]}",
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
                    last_contacted_at=dt.now(tz.utc),
                ))
            else:
                _ec_existing.last_contacted_at = dt.now(tz.utc)
                if recipient_name and not _ec_existing.name:
                    _ec_existing.name = recipient_name.strip()
        except Exception as _ec_err:
            logger.warning(f"[EMAIL_OUTREACH] Auto-save contact error: {_ec_err}")

        session.commit()

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
            outreach = session.query(EmailOutreach).filter_by(
                user_id=user.id, recipient_email=recipient_email, status='replied'
            ).order_by(EmailOutreach.reply_at.desc()).first()

        if not outreach:
            return " Не найдено письмо для ответа."

        # Защита от дублей: если уже отвечали — не отправлять повторно
        if outreach.ai_reply_sent_at:
            sent_str = outreach.ai_reply_sent_at.strftime('%d.%m %H:%M')
            return f"ℹ️ Ответ этому контакту уже был отправлен {sent_str}. Повторная отправка пропущена."

        campaign = session.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
        if not campaign:
            return " Кампания не найдена."

        if not reply_body:
            return " Нужен текст ответа (reply_body)."

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
            except Exception:
                pass
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
                except Exception:
                    pass
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
                    except Exception:
                        pass
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

        # Логируем в AgentActivityLog
        try:
            from models import AgentActivityLog
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='email',
                title=f"Reply → {outreach.recipient_email}",
                content=f"Re: {outreach.subject}\n\n{reply_body[:500]}",
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
                        except Exception:
                            pass
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
        _user_email_lower = (getattr(user, 'email', '') or '').strip().lower()
        for lead in parsed:
            email = lead.get('email', '').strip().lower()
            if not email or '@' not in email:
                continue
            # ── GUARD: не добавлять email самого пользователя как лид ──
            if _user_email_lower and email == _user_email_lower:
                skipped += 1
                continue
            # Отклоняем generic-адреса через полный фильтр
            if _is_generic_email(email):
                skipped_generic += 1
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
            CROSS_CAMPAIGN_COOLDOWN_DAYS = 30
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
                for r in recent_replies[:3]:
                    block += f"\n  → {r.recipient_email}: «{r.reply_text[:80]}...»"
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
            except Exception:
                pass
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
                except Exception:
                    pass
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
                    except Exception:
                        pass
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
                content=f"{subject}\n\n{body[:500]}",
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

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return " Пользователь не найден."

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
            except Exception:
                pass
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
            except Exception:
                pass

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
            return ("У тебя не подключена почта. Попроси пользователя добавить почтовые ключи "
                    "(GMAIL_USER, YANDEX_USER/YANDEX_PASS или MAILRU_USER/MAILRU_PASS) "
                    "в настройках агента на дашборде.")

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
        _known_emails: set = set()
        try:
            from models import EmailContact as _EC_ce
            _known_emails = {r.email.lower() for r in session.query(_EC_ce.email).filter_by(user_id=user.id).all() if r.email}
        except Exception:
            pass

        if chosen['type'] == 'gmail_oauth':
            result = await _check_emails_gmail_api(chosen['token_data'], limit, user, session, _known_emails, _my_email)
        elif chosen['type'] in ('smtp', 'gmail_server'):
            result = await _check_emails_imap(chosen, limit, _known_emails, _my_email)
        elif chosen['type'] == 'resend':
            return "Resend — сервис только для отправки, входящие не поддерживаются."
        else:
            return f"Тип интеграции '{chosen['type']}' не поддерживает чтение входящих."

        # Автоматически сохраняем новые контакты из входящих в EmailContact
        _no_new_keywords = ('нет новых писем', 'входящих писем нет', 'нет писем', 'no new', 'нет входящих')
        if result and not any(kw in result.lower() for kw in _no_new_keywords):
            import re as _re_ce
            _found_em = set(e.lower() for e in _re_ce.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', result, _re_ce.IGNORECASE))
            _new_auto = _found_em - _known_emails - {_my_email}
            for _new_em in list(_new_auto)[:5]:
                try:
                    import datetime as _dt_ce
                    from models import EmailContact as _EC_ce2
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
                        logger.info(f'[CHECK_EMAILS] Auto-saved contact: {_new_em} for user {user.id}')
                except Exception as _e_save:
                    logger.debug(f'[CHECK_EMAILS] auto-save contact failed: {_e_save}')
                    try:
                        session.rollback()
                    except Exception:
                        pass

        return result
    except Exception as e:
        logger.error(f"[CHECK_EMAILS] Error: {e}", exc_info=True)
        return f"Ошибка при проверке почты: {e}"
    finally:
        if close_session:
            session.close()


async def _check_emails_gmail_api(token_data: dict, limit: int, user, session, known_emails: set = None, my_email: str = '') -> str:
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
            for msg_ref in msgs[:limit]:
                msg_resp = await _h.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_ref['id']}",
                    headers={'Authorization': f'Bearer {tok}'},
                    params={'format': 'metadata', 'metadataHeaders': 'From,Subject,Date'},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                if msg_resp.status != 200:
                    continue
                msg_data = await msg_resp.json()
                headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
                snippet = msg_data.get('snippet', '')[:200]
                from_hdr = headers.get('From', '?')
                # Фильтруем уже-известные контакты
                if known_emails:
                    _gm_ems = _re_gm.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', from_hdr, _re_gm.IGNORECASE)
                    _gm_em_low = _gm_ems[0].lower() if _gm_ems else ''
                    if _gm_em_low and _gm_em_low in known_emails and _gm_em_low != my_email:
                        skipped_known_g.append(from_hdr)
                        continue
                results.append(
                    f"От: {from_hdr}\n"
                    f"Тема: {headers.get('Subject', '(без темы)')}\n"
                    f"Дата: {headers.get('Date', '?')}\n"
                    f"Превью: {snippet}\n"
                )
            if not results:
                if skipped_known_g:
                    return (f"Нет новых писем от незнакомых контактов. Уже в базе: {len(skipped_known_g)} контакт(а). "
                            f"Переключись на задачу: start_email_campaign или send_outreach_email для поиска новых.")
                return "Входящих писем нет."
            return f"Новые входящие ({gmail_email}, {len(results)} новых):\n\n" + "\n---\n".join(results)

    result = await _fetch(access_token)
    if result is None:
        # Token expired → refresh
        if await _refresh():
            result = await _fetch(access_token)
        if result is None:
            return "Не удалось авторизоваться в Gmail. Пользователю нужно переподключить Google OAuth."
    return result


async def _check_emails_imap(integration: dict, limit: int, known_emails: set = None, my_email: str = '') -> str:
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
                if known_emails:
                    _from_ems = _re_imap.findall(r'[\w\.\+\-]+@[\w\-]+\.[a-z]{2,10}', from_addr, _re_imap.IGNORECASE)
                    _from_low = _from_ems[0].lower() if _from_ems else ''
                    if _from_low and _from_low in known_emails and _from_low != my_email:
                        skipped_known.append(from_addr)
                        continue

                # Snippet: BODY.PEEK[TEXT] первые 200 символов
                _st, _dt2 = mail.fetch(mid, '(BODY.PEEK[TEXT]<0.500>)')
                snippet = ''
                if _st == 'OK' and _dt2[0] and len(_dt2[0]) > 1:
                    raw_body = _dt2[0][1]
                    try:
                        snippet = raw_body.decode('utf-8', errors='replace')[:200].strip()
                    except Exception:
                        snippet = str(raw_body[:200])
                results.append(
                    f"От: {from_addr}\n"
                    f"Тема: {subject}\n"
                    f"Дата: {date}\n"
                    f"Превью: {snippet}\n"
                )
            mail.logout()
            if not results:
                if skipped_known:
                    return (f"Нет новых писем от незнакомых контактов. Уже в базе: {len(skipped_known)} контакт(а) "
                            f"({', '.join(s[:50] for s in skipped_known[:3])}). "
                            f"Переключись на задачу: start_email_campaign или send_outreach_email для поиска новых контактов.")
                return "Входящих писем нет."
            return f"Новые входящие ({email_user}, {len(results)} новых):\n\n" + "\n---\n".join(results)
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
            try:
                from models import AgentActivityLog as _AAL_goa
                session.add(_AAL_goa(
                    user_id=user.id, activity_type='email',
                    title=f"Email → {to_clean}",
                    content=f"Тема: {subject}\n\n{body[:500]}",
                    target=to_clean, status='sent',
                ))
                session.commit()
            except Exception as _aal_goa_e:
                logger.warning(f'[SEND_EMAIL] Activity log (gmail_oauth) error: {_aal_goa_e}')
                try: session.rollback()
                except Exception: pass
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
            except Exception:
                pass
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
            # Логируем в AgentActivityLog для хронологии
            try:
                from models import AgentActivityLog as _AAL_gm
                session.add(_AAL_gm(
                    user_id=user.id,
                    activity_type='email',
                    title=f"Email → {to_clean}",
                    content=f"Тема: {subject}\n\n{body[:500]}",
                    target=to_clean,
                    status='sent',
                ))
                session.commit()
            except Exception as _aal_gm_e:
                logger.warning(f'[SEND_EMAIL] Activity log (gmail) error: {_aal_gm_e}')
                try:
                    session.rollback()
                except Exception:
                    pass
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
                    except Exception:
                        pass
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
                    logger.info(f'[SEND_EMAIL] Sent via user Resend from {sender_email} to {to_clean}')
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

        # Логируем в AgentActivityLog для хронологии активности
        try:
            from models import AgentActivityLog as _AAL_se
            _aal = _AAL_se(
                user_id=user.id,
                activity_type='email',
                title=f"Email → {to_clean}",
                content=f"Тема: {subject}\n\n{body[:500]}",
                target=to_clean,
                status='sent',
            )
            session.add(_aal)
            session.commit()
        except Exception as _aal_e:
            logger.warning(f"[SEND_EMAIL] Activity log error: {_aal_e}")
            try:
                session.rollback()
            except Exception:
                pass

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

        # Блокируем generic/корпоративные адреса
        if _is_generic_email(email_clean):
            return f" {email_clean} — это корпоративный/generic адрес. Сохраняй только личные email конкретных людей."

        # Check duplicate
        existing = session.query(EmailContact).filter_by(
            user_id=user.id, email=email_clean
        ).first()
        if existing:
            # Update existing
            if name:
                existing.name = name.strip()
            if company:
                existing.company = company.strip()
            if position:
                existing.position = position.strip()
            if notes:
                existing.notes = notes.strip()
            session.commit()
            return f" Контакт {email_clean} обновлён"

        contact = EmailContact(
            user_id=user.id,
            email=email_clean,
            name=(name or '').strip() or None,
            company=(company or '').strip() or None,
            position=(position or '').strip() or None,
            notes=(notes or '').strip() or None,
            source=source or 'manual',
            status=status or 'replied',
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
        contacts = query.order_by(EmailContact.created_at.desc()).limit(100).all()

        if not contacts:
            return " Справочник контактов пуст. Добавь через save_email_contact или на дашборде → Контакты."

        lines = [f" Email-контакты ({len(contacts)}):"]
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
                if _discord_today >= 1:
                    return " Сегодня пост в Discord уже опубликован (лимит — 1 в день). Следующий можно завтра."
            except Exception as _lim_e:
                logger.warning(f"[DISCORD_LIMIT] {_lim_e}")

        import aiohttp as _aiohttp
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

        # Строим полный промпт (всегда на английском для лучшего качества)
        full_prompt = prompt
        if style:
            full_prompt = f"{prompt}, {style} style"

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
                send_resp = await http.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                    json={
                        "chat_id": user.telegram_id,
                        "photo": image_url,
                    },
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

        if not post_time:
            return " Время публикации не указано. Спроси пользователя: в какое время публиковать (например, '09:00', '18:00', '21:00'). Не используй 12:00 по умолчанию — пользователь должен указать время явно."

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
            return " Укажи slug агента (например @crypto-alex)"

        slug = agent_slug.lstrip('@').strip()
        agent = session.query(UserAgent).filter_by(slug=slug, status='active').first()
        if not agent:
            return f" Агент @{slug} не найден или ещё не опубликован."

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
        except Exception:
            pass

    if user_id not in agent._active_agent_data:
        return " Нет активного агента со скриптом. Активируй агента через /dashboard → Агенты."

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