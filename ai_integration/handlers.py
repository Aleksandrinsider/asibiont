# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timedelta
import pytz
import requests
import aiohttp
from models import Session, Task, User, UserProfile, Subscription, Goal, Post, PostLike, PostView, Comment
from sqlalchemy import or_, and_, func

from .memory import encrypt_data, decrypt_data, LongTermMemory
from .utils import parse_time_to_datetime, generate_unified_recommendations
from .task_search import find_task_flexible
from .dialog_context import get_user_context, resolve_task_reference
from . import marketing_agent
from config import OPENWEATHERMAP_API_KEY, ALPHA_VANTAGE_API_KEY, NEWSAPI_API_KEY, SERPER_API_KEY

logger = logging.getLogger(__name__)


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
    'москва': 'Europe/Moscow',
    
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
    'ке мерово': 'Asia/Novosibirsk',
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
            return f"⚠️ КОНФЛИКТ ВРЕМЕНИ:\n{conflict_msg}\n\n💡 ПРЕДЛАГАЮ: {suggested_time}"
        else:
            return "✅ Время свободно, можно создавать задачу"
            
    except Exception as e:
        logger.error(f"Error in async check_time_conflicts: {e}")
        if session and 'close_session' in locals() and close_session:
            session.close()
        return f"Ошибка при проверке времени: {str(e)}"

async def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None, ignore_conflicts=False, is_recurring=False, recurrence_pattern=None, recurrence_interval=1):
    """Add a new task"""
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}, is_recurring={is_recurring} (type: {type(is_recurring)}), recurrence_pattern={recurrence_pattern}, recurrence_interval={recurrence_interval}")
    
    if user_id is None:
        logger.error("[ADD_TASK] ERROR: user_id is None! Cannot create task without user_id")
        return "ERROR: user_id is required but was None"
    
    # Валидация: название не может быть пустым
    if not title or not title.strip():
        logger.error("[ADD_TASK] ERROR: title is empty or whitespace only")
        return "ERROR: Название задачи не может быть пустым"
    
    title = title.strip()
    
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

    # ПРОВЕРКА ДУБЛИКАТОВ ОТКЛЮЧЕНА - создаем задачи даже с одинаковыми названиями
    # Это позволяет создавать несколько задач подряд без конфликтов
    # Если пользователь действительно хочет обновить задачу - он может использовать edit_task
    
    # Create new task - ОБЯЗАТЕЛЬНО требуется время
    if not reminder_time:
        if close_session:
            session.close()
        logger.info(f"[ADD_TASK] Task '{title}' NOT created - no reminder_time provided")
        return "NEED_TIME_FOR_TASK: Когда напомнить? Укажи время: завтра в 10:00, через час, сегодня в 15:00"
    
    task = Task(user_id=user.id, title=title, description=encrypt_data(description))
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
                    return f"❌ Не удалось распознать время '{reminder_time}'. Попробуй: 'завтра в 10:00', 'через 2 часа', '15:30'"
        except Exception as e:
            logging.warning(f"Error processing reminder_time '{reminder_time}' for task {title}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()
            if close_session:
                session.close()
            return f"❌ Ошибка обработки времени '{reminder_time}': {e}. Попробуй: 'завтра в 10:00', 'через 2 часа', '15:30'"
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
                logger.info(f"[ADD_TASK] Time conflict at {original_str}, NOT creating task. Suggested: {suggested_time_str}")
                if close_session:
                    session.close()
                return (f"TIME_CONFLICT: На {original_str} уже запланировано:\n{conflict_msg}\n"
                        f"Ближайшее свободное время: {suggested_time_str}. "
                        f"Уточни у пользователя: создать на {suggested_time_str} или выбрать другое время?")
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
    result_msg = f"Добавлена задача '{title}'"
    if task.reminder_time:
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        local_time = _utc_to_local(task.reminder_time, user_tz)
        time_str = local_time.strftime('%H:%M')
        date_str = local_time.strftime('%d.%m.%Y')
        result_msg += f" с напоминанием на {date_str} в {time_str}"

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
        return f"Задача не найдена: {task_title or task_id}"

    if task:
        if task.status == "completed":
            if close_session:
                session.close()
            return f"✅ Задача '{task.title}' уже выполнена"
        
        task.status = "completed"
        task.actual_completion_time = datetime.now(pytz.UTC)
        
        # Сохраняем заметку о результате выполнения
        if completion_note:
            task.completion_notes = encrypt_data(completion_note)
            logger.info(f"[COMPLETE_TASK] Saved completion note for task {task.id}")
        
        try:
            session.commit()
            logger.info(f"[COMPLETE_TASK] Task {task.id} status set to 'completed', committed to database")
            
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
                        f"📝 Расскажи о результатах выполнения задачи:\n"
                        f"'{task.title}'\n\n"
                        f"Опиши что было сделано, какие результаты достигнуты, "
                        f"были ли сложности. Это важно для @{delegator.username}, "
                        f"который поручил тебе эту задачу."
                    )
                    await bot.send_message(chat_id=user.telegram_id, text=result_request)
                    logger.info(f"[COMPLETE_TASK] Requested completion results from user {user.username} for task {task.id}")
                    
                    # Сохраняем флаг что нужно отправить отчет делегатору после получения результатов
                    # Используем поле completion_notes для временного хранения ID делегатора
                    task.pending_delegator_report = delegator.telegram_id
                    session.commit()
                    
                    # Обновляем сообщение для пользователя
                    result = f"✅ Задача '{task.title}' завершена! Теперь опиши результаты выполнения для @{delegator.username}"
                    
            except Exception as e:
                logger.error(f"[COMPLETE_TASK] Failed to request completion results from executor: {e}")

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

async def skip_task(task_id=None, task_title=None, user_id=None, session=None):
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "❌ Пользователь не найден"

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
            import traceback
            traceback.print_exc()
            session.rollback()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.skipped_tasks = (profile.skipped_tasks or 0) + 1
            session.commit()
        result = f"Задача '{task.title}' отмечена как пропущенная."

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
                Task.status == "completed",  # Only restore completed tasks
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
                and_(Task.user_id == user.id, Task.status == "completed", or_(*conditions)),
                and_(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.status == "completed",
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

        result = f"Задача '{task.title}' восстановлена в работу."

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result

async def reschedule_task(task_title=None, new_time=None, user_id=None, session=None):
    from models import User  # Явный импорт для избежания конфликтов области видимости
    logger.info(f"[RESCHEDULE_TASK] Called with task_title='{task_title}', new_time='{new_time}', user_id={user_id}")
    logger.info(f"[RESCHEDULE_TASK] task_title type: {type(task_title)}, repr: {repr(task_title)}, bytes: {task_title.encode('utf-8') if task_title else None}")
    
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
                Task.status != 'completed',
                Task.status != 'deleted'
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

            result = f"Задача '{task.title}' перенесена на {local_dt.strftime('%d.%m.%Y %H:%M')}."

        except ValueError as e:
            logger.error(f"[RESCHEDULE_TASK] ValueError: {e}")
            result = f"Ошибка формата времени: {e}. Используйте формат HH:MM или YYYY-MM-DD HH:MM."
        except Exception as e:
            logger.error(f"[RESCHEDULE_TASK] Unexpected error: {e}", exc_info=True)
            result = f"Ошибка при переносе задачи: {str(e)}"
    else:
        result = f"Задача '{task_title}' не найдена."

    if close_session:
        session.close()
    return result

def delegate_task(
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
            return f"Пользователь @{recipient_username} не найден в системе. Убедитесь, что он зарегистрирован в боте."

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
            report.append("📤 ВАШИ ДЕЛЕГИРОВАННЫЕ ЗАДАЧИ:")
            for task in delegated_by_user[:10]:  # Ограничим 10 задачами
                status_emoji = {
                    None: "⏳",
                    "pending": "⏳",
                    "accepted": "✅",
                    "rejected": "❌",
                    "completed": "🎉"
                }.get(task.delegation_status, "❓")

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
            report.append("📥 ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ВАМ:")
            for task in delegated_to_user[:10]:
                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                delegator_name = f"@{delegator.username}" if delegator and delegator.username else "неизвестный"

                status_emoji = {
                    "pending": "⏳",
                    "accepted": "✅",
                    "rejected": "❌",
                    "completed": "🎉"
                }.get(task.delegation_status, "❓")

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
                    logger.info(f"[EDIT_TASK] Time updated: '{reminder_time}' -> {task.reminder_time} UTC")
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
            active_tasks_query = base_query.filter(Task.status != 'completed').limit(MAX_TASKS_TO_LOAD)
        
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
                result += f"✓ {task.title}{completed_info}\n"
            
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
                later_tasks.append(task)  # Без времени - в конец
        
        # Сортируем по времени внутри каждой группы
        priority_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        today_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        upcoming_tasks.sort(key=lambda t: t.reminder_time or datetime.min.replace(tzinfo=pytz.UTC))
        
        # Объединяем: сначала важные
        sorted_tasks = priority_tasks + today_tasks + upcoming_tasks + later_tasks
        
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

        logger.info(f"[LIST_TASKS] Returning {len(active_tasks)} active tasks for user {user_id}")
        return result.strip()
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return "Ошибка получения списка задач"
    finally:
        if close_session:
            session.close()

# Function removed

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
        for task in delegated_to_me:
            delegated_user = session.query(User).filter_by(id=task.user_id).first()
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
    
    all_profiles = profile_query.all()

    logger.info(f"[PARTNERS] Found {len(all_profiles)} profiles with data")

    # Get current user profile for comparison
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        if close_session:
            session.close()
        return []

    # Filter only those with matches
    partners = []
    for profile in all_profiles:
        profile_user = session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user or not profile_user.username:
            continue

        has_match = False
        match_reasons = []  # Для логирования причин совпадения

        # Check skills - улучшенная логика с частичным совпадением
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(","))
            
            # Стоп-слова
            stop_words = {'в', 'и', 'с', 'на', 'по', 'для', 'от', 'к', 'о', 'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}
            
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

        # Check interests - улучшенная логика с частичным совпадением
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(","))
            
            # Стоп-слова которые игнорируем при частичном совпадении
            stop_words = {'в', 'и', 'с', 'на', 'по', 'для', 'от', 'к', 'о', 'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}
            
            # Семантические группы для расширения совпадений
            sport_keywords = {'спорт', 'бег', 'пробежка', 'йога', 'фитнес', 'тренировка', 'велоспорт', 'плавание', 
                            'футбол', 'баскетбол', 'теннис', 'волейбол', 'хоккей', 'кроссфит', 'гимнастика',
                            'марафон', 'триатлон', 'бадминтон', 'сквош', 'гольф', 'бильярд', 'пилатес'}
            business_keywords = {'бизнес', 'стартап', 'предпринимательство', 'инвестиции', 'маркетинг', 
                               'продажи', 'финансы', 'управление', 'менеджмент', 'e-commerce'}
            
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

        # Check current_plans for interest matches
        if user_profile.interests and profile.current_plans:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            for interest in user_interests:
                interest_words = interest.strip().lower().split()
                if any(word in profile.current_plans.lower() for word in interest_words):
                    has_match = True
                    match_reasons.append(f"current_plans: {interest}")
                    break

        # Check goals (text from UserProfile)
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True
                match_reasons.append(f"goals: {user_goals & profile_goals}")

        # Check structured Goals from Goal table
        if not has_match:
            try:
                user_goals_db = session.query(Goal).filter(
                    Goal.user_id == user.id,
                    Goal.status.in_(['active', 'in_progress'])
                ).all()
                partner_goals_db = session.query(Goal).filter(
                    Goal.user_id == profile.user_id,
                    Goal.status.in_(['active', 'in_progress'])
                ).all()
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

        # Check company
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
                    match_reasons.append(f"company: {profile.company}")

        # ВАЖНО: Всегда показывать избранные и заблокированные контакты
        
        # Все пользователи видят всех (токенная модель, без тарифных ограничений)
        
        if user_profile.favorite_contacts:
            favorite_usernames = [u.strip().lower().replace('@', '') for u in user_profile.favorite_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in favorite_usernames:
                has_match = True  # Принудительно показываем избранных
                match_reasons.append("favorite contact")
                
        if user_profile.blocked_contacts:
            blocked_usernames = [u.strip().lower().replace('@', '') for u in user_profile.blocked_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in blocked_usernames:
                has_match = True  # Принудительно показываем заблокированных
                match_reasons.append("blocked contact")

        if has_match:
            logger.info(f"[PARTNERS] Match found: @{profile_user.username} - {', '.join(match_reasons)}")
            partners.append(profile)
        else:
            logger.debug(f"[PARTNERS] No match: @{profile_user.username}")

    logger.info(f"[PARTNERS] Total partners found: {len(partners)}")

# НОВАЯ ЛОГИКА СОРТИРОВКИ: способствовать росту пользователя через всю базу данных
    # Приоритет: (1) релевантность, (2) город (бонус, но не ограничение), (3) Premium, (4) рейтинг
    user_city = user_profile.city.lower() if user_profile.city else None

    def sort_key(p):
        relevance_score = 0  # Инициализируем счетчик релевантности
        
        # Совпадения навыков дают высокий балл
        if user_profile.skills and p.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in p.skills.split(","))
            skill_matches = len(user_skills & profile_skills)
            relevance_score += skill_matches * 3  # Каждый совпадающий навык = 3 балла

        # Совпадения интересов дают средний балл
        if user_profile.interests and p.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in p.interests.split(","))
            interest_matches = len(user_interests & profile_interests)
            relevance_score += interest_matches * 2  # Каждый совпадающий интерес = 2 балла

        # Совпадения целей дают высокий балл
        if user_profile.goals and p.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in p.goals.split(","))
            goal_matches = len(user_goals & profile_goals)
            relevance_score += goal_matches * 4  # Каждая совпадающая цель = 4 балла

        # Бонус за совпадение структурированных целей (Goal table)
        try:
            user_goals_db = session.query(Goal).filter(
                Goal.user_id == user.id, Goal.status.in_(['active', 'in_progress'])
            ).all()
            partner_goals_db = session.query(Goal).filter(
                Goal.user_id == p.user_id, Goal.status.in_(['active', 'in_progress'])
            ).all()
            if user_goals_db and partner_goals_db:
                u_cats = set(g.category.lower().strip() for g in user_goals_db if g.category)
                p_cats = set(g.category.lower().strip() for g in partner_goals_db if g.category)
                relevance_score += len(u_cats & p_cats) * 5
        except Exception as e:
            logger.debug(f"Failed to compare goal categories: {e}")

        # Бонус за тот же город (но не блокировка)
        city_bonus = 0
        partner_city = p.city.lower() if p.city else None
        if user_city and partner_city == user_city:
            city_bonus = 1  # Небольшой бонус за локальность

        return (-relevance_score, -city_bonus, -(p.average_rating or 0))

    # Сортируем по новой логике
    partners.sort(key=sort_key)

    # Логируем результаты для анализа
    top_partners = partners[:5]  # Показываем топ-5 для логирования
    for i, p in enumerate(top_partners):
        partner_user = session.query(User).filter_by(id=p.user_id).first()
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
    try:
        user_goals_db = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.status.in_(['active', 'in_progress'])
        ).all()
        for g in user_goals_db:
            if g.title:
                user_task_keywords.update(w.lower() for w in g.title.split() if len(w) >= 4)
            if g.category:
                user_task_keywords.add(g.category.lower().strip())
        if user_goals_db:
            logger.info(f"[PARTNERS] Added {len(user_goals_db)} goal keywords")
    except Exception as e:
        logger.debug(f"Failed to extract goal keywords: {e}")
    
    # Добавляем информацию об общих интересах, навыках, целях и задачах
    user_interests = set(i.strip().lower() for i in user_profile.interests.split(',')) if user_profile.interests else set()
    user_skills = set(s.strip().lower() for s in user_profile.skills.split(',')) if user_profile.skills else set()
    user_goals = set(g.strip().lower() for g in user_profile.goals.split(',')) if user_profile.goals else set()
    
    for partner in partners:
        # Common interests
        if partner.interests:
            partner_interests = set(i.strip().lower() for i in partner.interests.split(','))
            common = user_interests & partner_interests
            partner.common_interests = ', '.join(common) if common else None
        else:
            partner.common_interests = None
            
        # Common skills
        if partner.skills:
            partner_skills = set(s.strip().lower() for s in partner.skills.split(','))
            common_skills = user_skills & partner_skills
            partner.common_skills = ', '.join(common_skills) if common_skills else None
        else:
            partner.common_skills = None
            
        # Common goals
        if partner.goals:
            partner_goals = set(g.strip().lower() for g in partner.goals.split(','))
            common_goals = user_goals & partner_goals
            partner.common_goals = ', '.join(common_goals) if common_goals else None
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
                    logger.info(f"[PARTNERS] @{session.query(User).filter_by(id=partner.user_id).first().username if session.query(User).filter_by(id=partner.user_id).first() else 'unknown'} relevant for tasks: {task_skill_match}")
            
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
                    logger.info(f"[PARTNERS] @{session.query(User).filter_by(id=partner.user_id).first().username if session.query(User).filter_by(id=partner.user_id).first() else 'unknown'} task relevance: {task_interest_match}")
            
            # Проверяем совпадение задач партнера с задачами пользователя (схожие активности)
            partner_user = session.query(User).filter_by(id=partner.user_id).first()
            if partner_user:
                partner_tasks = session.query(Task).filter(
                    Task.user_id == partner_user.id,
                    Task.status.in_(['active', 'pending', 'in_progress'])
                ).all()
                
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
    partners.sort(key=lambda p: (
        -p.task_relevance_score,  # релевантность
        0 if (user_city and (p.city.lower() if p.city else None) == user_city) else 1,  # город
        -(p.average_rating or 0)  # рейтинг
    ))
    
    # Подсчитываем партнеров с релевантностью для задач
    relevant_count = sum(1 for p in partners if p.task_relevance_score > 0)
    not_relevant_count = len(partners) - relevant_count
    logger.info(f"[PARTNERS] Task-relevant partners: {relevant_count}, other: {not_relevant_count}")
    
    for partner in partners[:5]:  # Log top 5
        partner_user = session.query(User).filter_by(id=partner.user_id).first()
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
            user_tasks = session.query(Task).filter_by(user_id=user.id).all()
            user_task_titles = set(t.title.lower().strip() for t in user_tasks if t.title)
            
            partner_tasks = session.query(Task).filter_by(user_id=partner.user_id).all()
            partner_task_titles = set(t.title.lower().strip() for t in partner_tasks if t.title)
            
            common_task_titles = user_task_titles & partner_task_titles
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
    for partner in partners[:10]:  # Топ-10 партнеров
        partner_user = session.query(User).filter_by(id=partner.user_id).first()
        if not partner_user or not partner_user.username:
            continue
        
        # Получаем активные задачи партнера
        partner_tasks = session.query(Task).filter(
            Task.user_id == partner_user.id,
            Task.status.in_(['pending', 'active', 'in_progress']),
            Task.reminder_time.isnot(None),
            Task.reminder_time >= base_now,
            Task.reminder_time <= base_now + timedelta(hours=48)
        ).order_by(Task.reminder_time.asc()).limit(5).all()
        
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
        return f"👥 @{activity['username']} {activity['activity']} {activity['time']}. Присоединяйся?"
    
    # Если нет конкретных задач, анализируем goals
    if profile.goals:
        user_goals = set(g.strip().lower() for g in profile.goals.split(','))
        for partner in partners[:5]:
            partner_profile = session.query(UserProfile).filter_by(user_id=partner.user_id).first()
            if partner_profile and partner_profile.goals:
                partner_user = session.query(User).filter_by(id=partner.user_id).first()
                if partner_user and partner_user.username:
                    partner_goals = set(g.strip().lower() for g in partner_profile.goals.split(','))
                    common_goals = user_goals & partner_goals
                    if common_goals:
                        goal = list(common_goals)[0]
                        return f"🎯 @{partner_user.username} тоже хочет '{goal}'. Можете объединиться!"
    
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
    for task in all_recent_tasks:
        task_text = f"{task.title} {task.description or ''}".lower()
        words = [w.strip('.,!?;:()[]{}') for w in task_text.split()]
        
        # Берем только значимые слова (>= 4 символа, не стоп-слова)
        significant_words = [w for w in words if len(w) >= 4 and w not in stop_words]
        
        task_user = session.query(User).filter_by(id=task.user_id).first()
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
        
        return f"💡 {count} человек работают над задачами связанными с '{topic}' — организовать обсуждение? Участники: {', '.join(usernames)}"
    
    return None


def create_goal(title=None, description=None, category=None, priority=None, target_date=None, success_criteria=None, user_id=None, session=None):
    """Создать новую цель пользователя
    
    Args:
        title: Название цели
        description: Описание цели
        category: Категория (work, personal, health, learning, finance, social)
        priority: Приоритет (low, medium, high, critical)
        target_date: Целевая дата достижения
        success_criteria: Критерии успеха
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
            return "❌ У тебя уже 20 активных целей. Заверши или отмени старые перед созданием новых."
        
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
                if existing and title not in existing:
                    profile.goals = f"{existing}; {title}"
                elif not existing:
                    profile.goals = title
                session.commit()
                logger.info(f"[CREATE_GOAL] Synced profile.goals: {profile.goals}")
        except Exception as e:
            logger.warning(f"[CREATE_GOAL] Failed to sync profile.goals: {e}")
        
        result = f"🎯 Цель создана: **{goal.title}**"
        if goal.category:
            result += f"\n📂 Категория: {goal.category}"
        if goal.priority and goal.priority != 'medium':
            result += f"\n⚡ Приоритет: {goal.priority}"
        if parsed_date:
            result += f"\n📅 Дедлайн: {parsed_date.strftime('%d.%m.%Y')}"
        if goal.success_criteria:
            result += f"\n✅ Критерии: {goal.success_criteria}"
        result += f"\n\nТеперь можешь привязывать задачи к этой цели — так ты увидишь прогресс!"
        
        return result
    
    except Exception as e:
        logger.error(f"Error creating goal for user {user_id}: {e}")
        return f"❌ Ошибка при создании цели: {str(e)}"
    finally:
        if close_session:
            session.close()


def update_goal_progress(goal_title=None, progress=None, status=None, notes=None, user_id=None, session=None):
    """Обновить прогресс или статус цели
    
    Args:
        goal_title: Название или часть названия цели для поиска
        progress: Новый процент прогресса (0-100)
        status: Новый статус (active, completed, paused, cancelled)
        notes: Заметки о прогрессе
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
        
        if progress is not None:
            try:
                pct = int(progress)
                pct = max(0, min(100, pct))
                matched.progress_percentage = pct
                changes.append(f"прогресс: {pct}%")
                if pct == 100 and matched.status == 'active':
                    matched.status = 'completed'
                    matched.completed_at = datetime.now()
                    changes.append("статус: завершено! 🎉")
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
        
        result = f"🎯 **{matched.title}** обновлена:\n"
        result += ", ".join(changes)
        result += f"\n📊 Прогресс: {matched.progress_percentage}%"
        
        # Связанные задачи
        linked_tasks = session.query(Task).filter_by(user_id=user.id, goal_id=matched.id, status='pending').count()
        if linked_tasks:
            result += f"\n📋 Связанных задач: {linked_tasks}"
        
        return result
    
    except Exception as e:
        logger.error(f"Error updating goal for user {user_id}: {e}")
        return f"❌ Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


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
        
        priority_emoji = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢'}
        status_emoji = {'active': '🎯', 'completed': '✅', 'paused': '⏸️', 'cancelled': '❌'}
        
        result = "🎯 **Твои цели:**\n\n"
        for g in goals:
            emoji = status_emoji.get(g.status, '🎯')
            pri = priority_emoji.get(g.priority, '')
            progress_bar = _progress_bar(g.progress_percentage)
            
            result += f"{emoji} **{g.title}** {pri}\n"
            result += f"   {progress_bar} {g.progress_percentage}%"
            
            if g.category:
                result += f" | {g.category}"
            if g.target_date:
                days = g.days_until_target()
                if days is not None:
                    if days < 0:
                        result += f" | ⚠️ просрочено на {abs(days)} дн."
                    elif days == 0:
                        result += f" | 🔥 дедлайн сегодня!"
                    elif days <= 7:
                        result += f" | ⏳ {days} дн. осталось"
                    else:
                        result += f" | до {g.target_date.strftime('%d.%m.%Y')}"
            
            # Связанные задачи
            linked = session.query(Task).filter_by(user_id=user.id, goal_id=g.id).all()
            if linked:
                done = sum(1 for t in linked if t.status == 'completed')
                total = len(linked)
                result += f" | задачи: {done}/{total}"
            
            result += "\n"
        
        return result
    
    except Exception as e:
        logger.error(f"Error listing goals for user {user_id}: {e}")
        return f"❌ Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def _progress_bar(pct):
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

        result = "📋 **Твой профиль:**\n\n"

        # Основная информация
        if user.username:
            result += f"👤 Имя: @{user.username}\n"
        if user.first_name:
            result += f"📛 Имя: {user.first_name}\n"

        if profile:
            if profile.city:
                result += f"🏙️ Город: {profile.city}\n"
            if profile.company:
                result += f"🏢 Компания: {profile.company}\n"
            if profile.position:
                result += f"👔 Должность: {profile.position}\n"
            if profile.interests:
                result += f"💡 Интересы: {profile.interests}\n"
            if profile.skills:
                result += f"🛠️ Навыки: {profile.skills}\n"
            if profile.goals:
                result += f"🎯 Цели: {profile.goals}\n"
            if profile.birthdate:
                result += f"🎂 Дата рождения: {profile.birthdate}\n"
        else:
            result += "\n⚠️ Профиль ещё не заполнен. Расскажи о себе — город, интересы, навыки, цели — и я всё запомню!"

        # Подписка / токены
        token_balance = getattr(user, 'token_balance', 0) or 0
        result += f"\n💠 Баланс: {token_balance} токенов"

        # Timezone
        if user.timezone:
            result += f"\n🕐 Часовой пояс: {user.timezone}"

        return result

    except Exception as e:
        logger.error(f"Ошибка при показе профиля пользователя {user_id}: {e}")
        return f"❌ Ошибка: {str(e)}"
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
                return f"✅ Добавлен интерес: {content}"
            return f"Интерес '{content}' уже есть в профиле."

        elif memory_type in ('skill', 'skills'):
            existing = set(s.strip().lower() for s in (profile.skills or '').split(',') if s.strip())
            if content.lower() not in existing:
                profile.skills = (profile.skills + ', ' + content) if profile.skills else content
                session.commit()
                return f"✅ Добавлен навык: {content}"
            return f"Навык '{content}' уже есть в профиле."

        elif memory_type in ('goal', 'goals'):
            existing = set(g.strip().lower() for g in (profile.goals or '').split(',') if g.strip())
            if content.lower() not in existing:
                profile.goals = (profile.goals + ', ' + content) if profile.goals else content
                session.commit()
                return f"✅ Добавлена цель: {content}"
            return f"Цель '{content}' уже есть в профиле."

        else:
            # Для остальных типов — сохраняем в общую память
            from .memory import update_user_memory as _update_memory
            return _update_memory(f"[{memory_type or 'info'}] {content}", user_id=user_id)

    except Exception as e:
        logger.error(f"Ошибка при обновлении памяти пользователя {user_id}: {e}")
        return f"❌ Ошибка: {str(e)}"
    finally:
        if close_session:
            session.close()


def find_partners(user_id=None, session=None):
    """Find potential partners based on user profile - FULL implementation here"""
    # Due to size limit, implementing key part only
    # Full implementation is in ai_integration.py lines 2457-2720
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

    # Get user profile
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()

    # Get partners list
    partners = get_partners_list(user.id, session)

    if not partners:
        if close_session:
            session.close()
        return "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, город), и я найду единомышленников!"

    # НОВАЯ ЛОГИКА: показываем топ релевантных контактов для роста пользователя
    # Партнеры уже отсортированы по релевантности в get_partners_list

    response = "Нашел интересных людей для твоего роста и развития:\n\n"

    # Показываем топ-5 наиболее релевантных контактов
    for idx, p in enumerate(partners[:5], 1):
        partner_user = session.query(User).filter_by(id=p.user_id).first()
        if partner_user and partner_user.username:
            info_parts = []

            # Добавляем информацию о релевантности
            relevance_indicators = []
            if user_profile.skills and p.skills:
                user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
                profile_skills = set(s.strip().lower() for s in p.skills.split(","))
                if user_skills & profile_skills:
                    relevance_indicators.append("⚡ общие навыки")

            if user_profile.interests and p.interests:
                user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
                profile_interests = set(i.strip().lower() for i in p.interests.split(","))
                if user_interests & profile_interests:
                    relevance_indicators.append("🎯 общие интересы")

            if user_profile.goals and p.goals:
                user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
                profile_goals = set(g.strip().lower() for g in p.goals.split(","))
                if user_goals & profile_goals:
                    relevance_indicators.append("🚀 общие цели")

            # Основная информация
            if hasattr(p, "current_plans") and p.current_plans:
                info_parts.append(f"сейчас: {p.current_plans}")
            if p.interests:
                info_parts.append(f"интересы: {p.interests}")
            if hasattr(p, "position") and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, "company") and p.company:
                info_parts.append(f"компания: {p.company}")
            if p.city:
                info_parts.append(f"город: {p.city}")

            info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"

            # Собираем строку контакта
            contact_line = f"{idx}. @{partner_user.username}"
            if relevance_indicators:
                contact_line += f" {' • '.join(relevance_indicators)}"
            contact_line += f"\n   {info_str}\n"

            response += contact_line

    if len(partners) > 5:
        response += f"\n💡 Это топ-5 самых релевантных контактов. Используй всю базу данных для максимального роста!"

    if not partners:
        response = "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, цели), и я найду единомышленников для твоего развития!"

    if close_session:
        session.close()

    return response

def find_relevant_contacts_for_task(task_description: str, user_id: int = None, limit: int = 5, session=None) -> str:
    """
    Найти контакты релевантные для конкретной задачи.
    Используется AI агентом для рекомендации людей при создании/обсуждении задач.
    
    Args:
        task_description: Описание задачи или активности
        user_id: ID пользователя (telegram_id)
        limit: Максимальное количество контактов
        session: SQLAlchemy сессия
        
    Returns:
        Строка с рекомендациями контактов
    """
    logger.info(f"[FIND_RELEVANT] Searching contacts for task: '{task_description}', user_id={user_id}")
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    # Получить пользователя
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "❌ Пользователь не найден"
    
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
    user_city = user_profile.city.lower().strip() if user_profile and user_profile.city else None
    
    # Определить тип активности (оффлайн = город критичен)
    offline_keywords = {'пробежка', 'бег', 'бегать', 'тренировка', 'зал', 'спорт', 'йога', 'плавание', 
                        'встреча', 'кофе', 'прогулка', 'футбол', 'баскетбол', 'волейбол', 'теннис'}
    is_offline_activity = bool(task_keywords & offline_keywords)
    
    # Получить всех потенциальных партнеров
    all_partners = get_partners_list(user_id=user.id, session=session)
    
    if not all_partners:
        if close_session:
            session.close()
        return """❌ В сети пока нет контактов для этой задачи.

💡 Рекомендации:
• Заполни профиль (интересы, навыки, цели)
• Добавь информацию о своем городе
• Опиши, чем можешь помочь другим

Когда профили будут заполнены, я смогу предложить подходящих людей для сотрудничества."""
    
    # Найти релевантные контакты
    relevant_contacts = []
    
    for partner in all_partners:
        relevance_score = 0
        match_reasons = []
        
        # ПРИОРИТЕТ 1: Город (особенно для оффлайн активностей)
        partner_city = partner.city.lower().strip() if partner.city else None
        same_city = user_city and partner_city and user_city == partner_city
        
        if same_city:
            if is_offline_activity:
                relevance_score += 15  # Критично для спорта/встреч
                match_reasons.append(f"тот же город ({partner.city})")
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
                match_reasons.append(f"навыки: {', '.join(list(skill_match)[:2])}")
        
        # ПРИОРИТЕТ 3: Интересы
        if hasattr(partner, 'interests') and partner.interests:
            partner_interests = set(i.lower().strip() for i in partner.interests.split(','))
            interest_match = task_keywords & partner_interests
            if interest_match:
                relevance_score += len(interest_match) * 4
                match_reasons.append(f"интересы: {', '.join(list(interest_match)[:2])}")
        
        # ПРИОРИТЕТ 4: Цели контакта совпадают с задачей пользователя
        if hasattr(partner, 'goals') and partner.goals:
            partner_goals = set(g.lower().strip() for g in partner.goals.split(','))
            goal_match = task_keywords & partner_goals
            if goal_match:
                relevance_score += len(goal_match) * 6  # Цели важны
                match_reasons.append(f"цели: {', '.join(list(goal_match)[:2])}")
        
        # ПРИОРИТЕТ 4.5: Структурированные цели (Goal table)
        try:
            partner_goals_db = session.query(Goal).filter(
                Goal.user_id == partner.user_id,
                Goal.status.in_(['active', 'in_progress'])
            ).all()
            if partner_goals_db:
                for pg in partner_goals_db:
                    goal_text = ((pg.title or '') + ' ' + (pg.description or '') + ' ' + (pg.category or '')).lower()
                    goal_words = set(w for w in goal_text.split() if len(w) >= 4)
                    goal_kw_match = task_keywords & goal_words
                    if goal_kw_match:
                        relevance_score += len(goal_kw_match) * 5
                        match_reasons.append(f"цель «{pg.title[:30]}»")
                        break  # Одного совпадения достаточно
        except Exception as e:
            logger.debug(f"Failed to compare partner goals: {e}")
        
        # Используем уже вычисленную релевантность из get_partners_list
        if hasattr(partner, 'task_relevance_score') and partner.task_relevance_score > 0:
            relevance_score += partner.task_relevance_score
            if hasattr(partner, 'task_relevance') and partner.task_relevance:
                match_reasons.append(partner.task_relevance)
        
        if relevance_score > 0:
            partner_user = session.query(User).filter_by(id=partner.user_id).first()
            if partner_user and partner_user.username:
                relevant_contacts.append({
                    'username': partner_user.username,
                    'name': partner_user.username,
                    'interests': partner.interests or '',
                    'skills': partner.skills or '',
                    'city': partner.city or '',
                    'score': relevance_score,
                    'reasons': match_reasons
                })
    
    # НОВАЯ ЛОГИКА СОРТИРОВКИ: способствовать росту через всю базу данных
    # Город - бонус, но не ограничение для максимального развития

    # Сортируем по релевантности: (1) score, (2) город (бонус)
    def contact_sort_key(contact):
        # Основной скор релевантности
        base_score = contact['score']

        # Бонус за тот же город (для оффлайн активностей)
        city_bonus = 0
        contact_city = contact['city'].lower().strip() if contact['city'] else None
        if user_city and contact_city and user_city == contact_city:
            if is_offline_activity:
                city_bonus = 3  # Бонус для оффлайн активностей
            else:
                city_bonus = 1  # Маленький бонус для онлайн

        return (base_score + city_bonus, base_score, city_bonus)

    sorted_contacts = sorted(relevant_contacts, key=contact_sort_key, reverse=True)

    logger.info(f"[FIND_RELEVANT] Total relevant contacts found: {len(sorted_contacts)} (using full database for growth)")
    
    if close_session:
        session.close()
    
    # ДВУСТОРОННИЙ АНАЛИЗ: кому пользователь может помочь
    reverse_matches = []
    if user_profile and user_profile.skills:
        user_skills_set = set(s.strip().lower() for s in user_profile.skills.split(','))
        for partner in all_partners:
            partner_user = session.query(User).filter_by(id=partner.user_id).first()
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
                    reasons.append(f"нуждается в твоих навыках: {', '.join(list(overlap)[:2])}")
            # Навыки пользователя совпадают с интересами контакта
            if hasattr(partner, 'interests') and partner.interests:
                partner_interests_set = set(i.strip().lower() for i in partner.interests.split(','))
                overlap = user_skills_set & partner_interests_set
                if overlap:
                    score += len(overlap) * 2
                    reasons.append(f"интересуется тем, в чем ты эксперт")
            
            if score > 0:
                reverse_matches.append({
                    'username': partner_user.username,
                    'city': partner.city or '',
                    'score': score,
                    'reasons': reasons
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
                    partner_user = session.query(User).filter_by(id=partner.user_id).first()
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
    
    # Формирование ответа
    result_lines = []
    
    if sorted_contacts:
        result_lines.append("💡 Кто может помочь тебе:")
        top_contacts = sorted_contacts[:min(3, limit)]
        for i, contact in enumerate(top_contacts, 1):
            line = f"• @{contact['username']}"
            if contact['reasons']:
                line += f" — {', '.join(contact['reasons'][:2])}"
            if contact['city']:
                line += f" | {contact['city']}"
            result_lines.append(line)
    
    if reverse_matches:
        if result_lines:
            result_lines.append("")
        result_lines.append("🤝 Кому ты можешь помочь:")
        for i, contact in enumerate(reverse_matches[:min(3, limit)], 1):
            line = f"• @{contact['username']}"
            if contact['reasons']:
                line += f" — {', '.join(contact['reasons'][:2])}"
            if contact['city']:
                line += f" | {contact['city']}"
            result_lines.append(line)
    
    # Добавить предложения для существующих задач пользователя
    if user_tasks_suggestions:
        if result_lines:
            result_lines.append("")
        result_lines.append("💡 Также для твоих задач:")
        for suggestion in user_tasks_suggestions:
            contacts_str = ', '.join(f"@{c}" for c in suggestion['contacts'])
            result_lines.append(f"• {suggestion['task']}: {contacts_str}")
    
    if result_lines:
        return '\n'.join(result_lines)
    else:
        return "Не нашел подходящих контактов для этой задачи. Попробуй заполнить больше информации в профиле."

async def generate_delegation_notification_async(delegator_username, recipient_username, task_title, task_description, deadline, delegation_details, recipient_telegram_id):
    try:
        from main import bot
        if not bot:
            return

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

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None, None, None, None, None, None, user_id)

        prompt = """Создай персонализированное и мотивирующее уведомление о делегированной задаче.

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

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None, None, None, None, None, None, user_id)

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
            message = f"🎉 Отлично! Пользователь @{delegatee_username} принял вашу задачу '{task_title}' и добавил её в свой список задач."
        elif response.startswith("rejected"):
            reason = response.replace("rejected", "").strip()
            if reason:
                message = f"❌ Пользователь @{delegatee_username} отклонил задачу '{task_title}'. Причина: {reason}"
            else:
                message = f"❌ Пользователь @{delegatee_username} отклонил задачу '{task_title}'."
        else:
            message = f"📝 Статус задачи '{task_title}' изменён пользователем @{delegatee_username}: {response}"

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
        
        # Отменяем напоминание если есть
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and hasattr(REMINDER_SERVICE, 'scheduler'):
                job_id = f"reminder_{task_db_id}"
                try:
                    REMINDER_SERVICE.scheduler.remove_job(job_id)
                    logger.info(f"[DELETE_TASK] Removed reminder job {job_id}")
                except Exception as e:
                    logger.debug(f"Reminder job {job_id} not found or already removed: {e}")
        except ImportError:
            pass
        
        # Удаляем задачу
        session.delete(task)
        session.commit()
        
        logger.info(f"[DELETE_TASK] Task '{task_name}' (ID: {task_db_id}) deleted successfully")
        
        reason_text = f" Причина: {reason}" if reason else ""
        return f"Задача '{task_name}' удалена.{reason_text}"
    
    except Exception as e:
        logger.error(f"[DELETE_TASK] Error: {e}")
        import traceback
        traceback.print_exc()
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
            
            details = "📋 Подробная информация о задаче:\n\n"
            details += f"🆔 ID: {task.id}\n"
            details += f"📝 Название: {task.title}\n"
            
            if task.description:
                details += f"📄 Описание: {decrypt_data(task.description)}\n"
            
            details += f"📊 Статус: {task.status}\n"
            
            if task.reminder_time:
                local_time = _utc_to_local(task.reminder_time, user_tz)
                details += f"⏰ Время напоминания: {local_time.strftime('%d.%m.%Y %H:%M')} ({user_tz.zone})\n"
            
            if task.due_date:
                local_due = _utc_to_local(task.due_date, user_tz)
                details += f"📅 Дедлайн: {local_due.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.delegated_to_username:
                details += f"👤 Делегирована: @{task.delegated_to_username}\n"
                details += f"📋 Статус делегирования: {task.delegation_status or 'Не определён'}\n"
                if task.delegation_details:
                    details += f"📋 Детали делегирования: {task.delegation_details}\n"
            
            if task.completion_notes:
                details += f"✅ Заметки о выполнении: {decrypt_data(task.completion_notes)}\n"
            
            if task.actual_completion_time:
                local_completion = _utc_to_local(task.actual_completion_time, user_tz)
                details += f"✅ Фактическое время выполнения: {local_completion.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.recommendations:
                try:
                    import json
                    recs = json.loads(task.recommendations)
                    if recs:
                        details += "💡 Рекомендации AI:\n"
                        for i, rec in enumerate(recs[:3], 1):
                            details += f"  {i}. {rec}\n"
                except Exception as e:
                    logger.warning(f"[TASKDETAILS] Error parsing recommendations: {e}")
            
            details += f"🕒 Создана: {_utc_to_local(task.created_at, user_tz).strftime('%d.%m.%Y %H:%M')}\n"
            
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
            # Try different formats
            for fmt in ["%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%H:%M"]:
                try:
                    if isinstance(reminder_time, str) and "завтра" in reminder_time.lower():
                        local_dt = datetime.now(user_tz) + timedelta(days=1)
                        time_part = reminder_time.lower().replace("завтра", "").strip()
                        if time_part:
                            time_dt = datetime.strptime(time_part, "%H:%M")
                            local_dt = local_dt.replace(hour=time_dt.hour, minute=time_dt.minute)
                    elif isinstance(reminder_time, str) and "сегодня" in reminder_time.lower():
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
    
    # Фильтруем дубликаты
    added_items = []
    for new_item in new_items:
        new_item_lower = new_item.lower()
        if new_item_lower not in current_items_lower:
            added_items.append(new_item)
            current_items_lower.append(new_item_lower)
    
    if not added_items:
        return current_value, False
    
    # Объединяем со старыми
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
            profile.company = company
            updates.append(f"компания: {company}")
        if position is not None:
            profile.position = position
            updates.append(f"должность: {position}")
        
        # Списочные поля (добавляются или заменяются в зависимости от replace_mode)
        if interests is not None:
            # Валидация
            if len(interests.strip()) < 2 or len(interests.strip()) > 100:
                logger.warning(f"Invalid interests length: {len(interests)}")
            elif any(char in interests.lower() for char in ['<', '>', 'script', 'http']):
                logger.warning(f"Invalid interests content: {interests}")
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
            # Валидация (исключаем вредоносный контент, но разрешаем JavaScript)
            if len(skills.strip()) < 2 or len(skills.strip()) > 200:
                logger.warning(f"Invalid skills length: {len(skills)}")
            elif any(pattern in skills.lower() for pattern in ['<script', 'http://', 'https://', 'onclick', 'onerror']):
                logger.warning(f"Invalid skills content (suspicious): {skills}")
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
            # Валидация - для replace_mode позволяем пустые строки (удаление)
            if replace_mode and goals.strip() == "":
                # Разрешаем пустую строку для удаления
                profile.goals = goals
                updates.append(f"цели заменены: {goals}")
            elif len(goals.strip()) < 2 or len(goals.strip()) > 200:
                logger.warning(f"Invalid goals length: {len(goals)}")
            elif any(pattern in goals.lower() for pattern in ['<script', 'http://', 'https://', 'onclick', 'onerror']):
                logger.warning(f"Invalid goals content (suspicious): {goals}")
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

        result_parts = []
        if added:
            result_parts.append(f"✅ Добавлено: {', '.join(added)}")
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
                result = f"✅ {field_names[field]} заменены: {value}"
            elif action == 'merge' and field == 'goals':
                # Умное объединение только для целей
                new_value, was_changed, change_desc = _merge_similar_goals(getattr(profile, field), value)
                if was_changed:
                    setattr(profile, field, new_value)
                    result = f"✅ {change_desc}"
                else:
                    result = f"ℹ️ {field_names[field]} уже актуальны"
            else:  # add
                new_value, was_added = _add_to_list_field(getattr(profile, field), value)
                if was_added:
                    setattr(profile, field, new_value)
                    result = f"✅ Добавлено в {field_names[field]}: {value}"
                else:
                    result = f"ℹ️ '{value}' уже есть в {field_names[field]}"
        else:
            # Простые поля
            setattr(profile, field, value)
            result = f"✅ {field_names[field]} обновлен: {value}"
            
            # Специальная обработка для города - обновляем timezone
            if field == 'city':
                tz = CITY_TIMEZONE_MAP.get(value.lower())
                if tz:
                    user.timezone = tz
                    result += f" | timezone: {tz}"

        # Обновляем время последнего обновления
        profile.updated_at = datetime.utcnow()
        session.commit()
        
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
                return f"✅ Обновил уведомление об активности '{activity_type}'. Теперь буду автоматически сообщать когда кто-то планирует такую активность!"
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
            return f"✅ Настроил автоматическое уведомление! Буду следить за активностями '{activity_type}'{location_str}. Когда кто-то создаст задачу по ключевым словам ({keywords_str}), я естественно упомяну это в нашем следующем диалоге. Никаких навязчивых уведомлений!"
        
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
                return f"✅ Обновил уведомление о '{filter_str}'. Буду автоматически сообщать когда зарегистрируются подходящие специалисты!"
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
            return f"✅ Настроил автоматическое уведомление! Буду следить за новыми пользователями ({filter_str}). Когда кто-то подходящий зарегистрируется или обновит профиль, я естественно упомяну это в нашем следующем диалоге. Никаких навязчивых уведомлений!"
        
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
        
        return f"✅ Время автопостинга установлено на {post_time}! Каждый день в это время я буду автоматически публиковать контент в ваш канал. Следующий пост: завтра в {post_time}."
        
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
    🔍 ПОИСК И АНАЛИЗ актуальной информации по теме
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
        
        # Create auto-post from research results (для разнообразия ленты новостей)
        analysis_data = result.get('analysis') if isinstance(result, dict) else None
        if isinstance(result, dict) and result.get('success') and isinstance(analysis_data, dict):
            try:
                from auto_post_service import generate_research_post, create_auto_post
                
                post_content = await generate_research_post(
                    user_id=user_id,
                    query=query,
                    analysis=analysis_data,
                    session=session
                )
                
                if post_content:
                    await create_auto_post(user_id, post_content, session, notify=True, post_type='research')
                    logger.info(f"[RESEARCH] Auto-post created and user notified: {user_id}")
            except Exception as post_error:
                logger.warning(f"[RESEARCH] Could not create auto-post: {post_error}")
                # Не прерываем основной flow, продолжаем нормально
        
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

async def set_content_strategy(strategy: str, user_id: int, session):
    """
    🎯 СОХРАНИТЬ СТРАТЕГИЮ КОНТЕНТА для автоматического маркетинга
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
        session.commit()
        
        logger.info(f"[CONTENT_STRATEGY] ✅ Saved: {strategy[:100]}...")
        
        return f"✅ Стратегия контента сохранена!\n\n{strategy}\n\nТеперь автоматический маркетинг будет генерировать посты на основе твоей стратегии. Для запуска автопостинга настрой telegram_channel и включи Premium функции."
        
    except Exception as e:
        logger.error(f"[CONTENT_STRATEGY] Error: {e}", exc_info=True)
        session.rollback()
        return f"Ошибка сохранения стратегии: {str(e)}"
    finally:
        if close_session:
            session.close()

async def toggle_autonomous_feature(feature: str, enabled: bool, user_id: int, session):
    """
    ⚙️ УПРАВЛЕНИЕ АВТОНОМНЫМИ ФУНКЦИЯМИ
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
            status_emoji = "✅" if enabled else "⛔"
            action = "включён" if enabled else "выключен"
            status_parts.append(f"{status_emoji} Автопостинг: {action}")
        
        if feature == 'delegation' or feature == 'all':
            profile.auto_delegation_enabled = enabled
            status_emoji = "✅" if enabled else "⛔"
            action = "включено" if enabled else "выключено"
            status_parts.append(f"{status_emoji} Автоделегирование: {action}")
        
        session.commit()
        
        response = "⚙️ Настройки автономных функций обновлены!\n\n" + "\n".join(status_parts)
        
        if not enabled:
            response += "\n\n💡 Ты всегда можешь включить обратно используя эту же команду."
        
        logger.info(f"[AUTONOMOUS_TOGGLE] ✅ Updated for user {user_id}")
        
        return response
        
    except Exception as e:
        logger.error(f"[AUTONOMOUS_TOGGLE] Error: {e}", exc_info=True)
        session.rollback()
        return f"Ошибка обновления настроек: {str(e)}"
    finally:
        if close_session:
            session.close()


async def create_post(content: str, user_id: int, session=None):
    """
    📝 ПУБЛИКАЦИЯ ПОСТА В ЛЕНТУ НОВОСТЕЙ
    
    Создаёт пост от имени пользователя в общую ленту новостей,
    которую видят все пользователи платформы.
    
    Args:
        content: Текст поста
        user_id: Telegram ID пользователя
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
        
        if not content or not content.strip():
            return "Текст поста не может быть пустым."
        
        import datetime as dt
        post = Post(
            user_id=user.id,
            username=user.username or user.first_name or f"user_{user.telegram_id}",
            content=content.strip(),
            created_at=dt.datetime.now(dt.timezone.utc)
        )
        
        session.add(post)
        session.commit()
        
        post_preview = content[:80] + '...' if len(content) > 80 else content
        logger.info(f"[CREATE_POST] User {user_id} published post #{post.id}: '{post_preview}'")
        return f"✅ Пост #{post.id} опубликован в ленту новостей!\n\n«{post_preview}»\n\nТеперь все пользователи его увидят."
        
    except Exception as e:
        logger.error(f"[CREATE_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f"❌ Ошибка публикации поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def edit_post(new_content: str, user_id: int, post_id: int = None, session=None):
    """
    ✏️ РЕДАКТИРОВАНИЕ ПОСТА В ЛЕНТЕ
    
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
        return f"✅ Пост #{post.id} обновлён!\n\nБыло: «{old_preview}»\nСтало: «{new_preview}»"
        
    except Exception as e:
        logger.error(f"[EDIT_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f"❌ Ошибка редактирования поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def get_posts(user_id: int, limit: int = 5, session=None):
    """
    📋 СПИСОК ПОСТОВ ПОЛЬЗОВАТЕЛЯ
    
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
        
        result_lines = [f"📋 Твои посты ({len(posts)} из последних):\n"]
        
        for post in posts:
            likes_count = session.query(PostLike).filter_by(post_id=post.id).count()
            views_count = session.query(PostView).filter_by(post_id=post.id).count()
            comments_count = session.query(Comment).filter_by(post_id=post.id).count()
            
            preview = post.content[:60] + '...' if len(post.content) > 60 else post.content
            # Формат даты
            date_str = post.created_at.strftime('%d.%m.%Y %H:%M') if post.created_at else '?'
            
            result_lines.append(
                f"#{post.id} ({date_str}) — 👁 {views_count} | ❤ {likes_count} | 💬 {comments_count}\n«{preview}»\n"
            )
        
        logger.info(f"[GET_POSTS] User {user_id} listed {len(posts)} posts")
        return '\n'.join(result_lines)
        
    except Exception as e:
        logger.error(f"[GET_POSTS] Error: {e}", exc_info=True)
        return f"❌ Ошибка получения постов: {str(e)}"
    finally:
        if close_session:
            session.close()


async def delete_post(user_id: int, post_id: int = None, session=None):
    """
    🗑 УДАЛЕНИЕ ПОСТА из ленты
    
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
        return f"✅ Пост #{post_id_deleted} удалён: «{post_preview}»"
        
    except Exception as e:
        logger.error(f"[DELETE_POST] Error: {e}", exc_info=True)
        session.rollback()
        return f"❌ Ошибка удаления поста: {str(e)}"
    finally:
        if close_session:
            session.close()


async def publish_to_telegram(content: str, user_id: int, session):
    """
    📢 ПУБЛИКАЦИЯ В TELEGRAM канал пользователя
    Требует: STANDARD или PREMIUM подписку
    
    Требования:
    - Пользователь должен указать telegram_channel в профиле
    - Бот должен быть админом канала
    
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
        # Все функции открыты — оплата токенами
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден. Напишите /start."
        
        logger.info(f"[PUBLISH] Starting for user {user_id}")
        
        # Если content это JSON строка от generate_marketing_content, парсим
        try:
            import json
            content_data = json.loads(content)
        except:
            content_data = content
        
        result = await marketing_agent.publish_to_telegram(
            content=content_data,
            user_id=user_id,
            session=session
        )
        
        # Проверяем результат публикации
        if isinstance(result, dict):
            if result.get('success'):
                return result.get('message', '✅ Пост успешно опубликован')
            else:
                # Публикация не удалась - возвращаем детальное сообщение об ошибке
                return result.get('message', '❌ Не удалось опубликовать пост')
        else:
            return str(result)
        
    except Exception as e:
        logger.error(f"[PUBLISH] Error in handler: {e}", exc_info=True)
        return f"Ошибка публикации: {str(e)}"
    finally:
        if close_session:
            session.close()

async def quick_topic_search(topic: str, user_id: int = None, session=None):
    """
    🔍 БЫСТРЫЙ ПОИСК ПО ТЕМЕ (LIGHT+)
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
        
        results = await api.serper_search(topic, num=3)
        if not results:
            return f"🔍 По запросу '{topic}' не найдено результатов"
        
        result_text = f"🔍 **Быстрый поиск**: {topic}\n\n"
        for i, r in enumerate(results, 1):
            result_text += f"{i}. **{r['title']}**\n"
            snippet = r['snippet']
            if snippet:
                result_text += f"   {snippet[:150]}{'...' if len(snippet) > 150 else ''}\n"
            result_text += f"   🔗 [Читать далее]({r['link']})\n\n"
        
        # AI анализ для всех тарифов
        try:
            context = "\n\n".join([f"**{r['title']}**\n{r['snippet']}" for r in results[:3]])
            prompt = f"""На основе этих результатов поиска по теме "{topic}":

{context}

Сделай краткий практичный вывод в 2-3 предложениях: суть темы, ключевой факт, и что с этим делать. Не пересказывай, а синтезируй."""
            ai_analysis = await api.deepseek_analyze(prompt, system_prompt="Ты эксперт-аналитик. Давай конкретику и практическую пользу.", max_tokens=200)
            if ai_analysis:
                result_text += f"🤖 **AI анализ**: {ai_analysis}\n\n"
        except Exception as e:
            logger.warning(f"[QUICK_SEARCH] AI analysis failed: {e}")
        
        result_text += "💡 **Подсказка**: Для более детального анализа используйте функцию research_topic."
        return result_text
        
    except Exception as e:
        logger.error(f"Error in quick_topic_search: {e}")
        return f"❌ Ошибка поиска по теме: {topic}"
    finally:
        if close_session:
            session.close()

async def check_topic_relevance(topic: str, user_id: int = None, session=None):
    """
    📊 ПРОВЕРКА АКТУАЛЬНОСТИ ТЕМЫ (LIGHT+)
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
        
        results = await api.serper_search(f"{topic} {current_year} тренды актуальность", num=7)
        
        if not results:
            return f"📊 **Проверка актуальности**: {topic}\n\n❌ Информация по теме не найдена"
        
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
        
        result = f"📊 **Проверка актуальности**: {topic}\n\n"
        if analysis:
            result += f"{analysis}\n\n"
        result += f"Найдено {len(results)} свежих источников по теме."
        
        return result
    except Exception as e:
        logger.error(f"Error in check_topic_relevance: {e}")
        return f"❌ Ошибка проверки темы: {topic}"
    finally:
        if close_session:
            session.close()

async def get_news_trends(topic: str, period: str = "week", focus: str = "trends", user_id: int = None, session=None):
    """
    📰 ПОЛУЧЕНИЕ НОВОСТЕЙ И АНАЛИЗ ТРЕНДОВ
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
        return f"❌ Ошибка получения новостей: {str(e)}"
    finally:
        if close_session:
            session.close()

async def research_and_plan(query: str, user_id: int = None, session=None):
    """
    🔍 КОМПЛЕКСНЫЙ АНАЛИЗ РЫНКА И ПЛАН ДЕЙСТВИЙ (STANDARD+)

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

        all_results = await api.serper_multi_search(search_queries, num_per_query=5)

        if not all_results:
            return f"❌ Не удалось найти информацию по запросу '{query}'"

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
            return f"❌ Ошибка AI анализа"

        # Форматируем ответ
        if isinstance(analysis, dict):
            result = f"🔍 **АНАЛИЗ: {query.upper()}**\n\n"
            
            summary = analysis.get('summary') or analysis.get('market_summary', '')
            if summary:
                result += f"📊 **ОБЗОР**\n{summary}\n\n"

            findings = analysis.get('key_findings') or analysis.get('key_trends', [])
            if findings:
                result += "📝 **КЛЮЧЕВЫЕ ФАКТЫ**\n"
                for item in findings[:3]:
                    result += f"• {item}\n"
                result += "\n"

            existing = analysis.get('what_exists') or []
            if existing:
                result += "🔎 **ЧТО УЖЕ ЕСТЬ**\n"
                for item in existing[:3]:
                    result += f"• {item}\n"
                result += "\n"
            elif analysis.get('competitor_analysis'):
                comp = analysis['competitor_analysis']
                players = comp.get('main_players') or comp.get('main_competitors', [])
                if players:
                    result += "🔎 **ОСНОВНЫЕ ИГРОКИ**\n"
                    for player in players[:3]:
                        result += f"• {player}\n"
                    result += "\n"

            opps = analysis.get('gaps_or_opportunities') or analysis.get('opportunities_for_user') or analysis.get('opportunities', [])
            if opps:
                result += "🚀 **ВОЗМОЖНОСТИ ДЛЯ ТЕБЯ**\n"
                for opp in opps[:3]:
                    result += f"• {opp}\n"
                result += "\n"

            advice = analysis.get('personalized_advice', '')
            if advice:
                result += f"💡 **ПЕРСОНАЛЬНЫЙ СОВЕТ**\n{advice}\n\n"

            plan = analysis.get('action_plan') or analysis.get('actionable_plan', {})
            if isinstance(plan, dict):
                steps = plan.get('this_week') or plan.get('immediate_steps', [])
                if steps:
                    result += "⚡ **НА ЭТОЙ НЕДЕЛЕ**\n"
                    for step in steps[:3]:
                        result += f"• {step}\n"
                    result += "\n"
                month = plan.get('this_month') or plan.get('short_term_goals', [])
                if month:
                    result += "📅 **НА МЕСЯЦ**\n"
                    for goal in month[:2]:
                        result += f"• {goal}\n"
                    result += "\n"

            risks = analysis.get('risks_or_caveats') or analysis.get('risks', [])
            if risks:
                if isinstance(risks, str):
                    risks = [risks]
                result += "⚠️ **НЮАНСЫ**\n"
                for risk in risks[:2]:
                    result += f"• {risk}\n"
                result += "\n"

            if analysis.get('recommended_tasks'):
                result += "📋 **РЕКОМЕНДУЕМЫЕ ЗАДАЧИ**\n"
                for task in analysis['recommended_tasks'][:2]:
                    if isinstance(task, dict):
                        result += f"• **{task.get('title', '')}** — {task.get('description', '')}\n"
                    else:
                        result += f"• {task}\n"
                result += "\n"

            result += f"🔗 Анализ основан на {len(all_results)} актуальных источниках"

            return result
        else:
            # Если JSON не распарсился — вернём текстовый ответ
            return f"🔍 **Анализ: {query}**\n\n{analysis}"

    except Exception as e:
        logger.error(f"[RESEARCH_PLAN] Error: {e}", exc_info=True)
        return f"❌ Ошибка комплексного исследования: {str(e)}"
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
            return f"❌ Не удалось получить погоду для города '{city}'"
        
        temp = data['temp']
        feels = data['feels_like']
        desc = data['description']
        humidity = data['humidity']
        wind = data['wind_speed']
        
        result = f"🌤️ **Погода в {data['city_name']}:**\n"
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
            result += f"\n⚠️ {', '.join(tips).capitalize()}\n"
        
        return result

    except Exception as e:
        logger.error(f"[WEATHER] Error: {e}")
        return f"❌ Ошибка получения погоды: {str(e)}"

async def get_stock_info(symbol: str, user_id: int = None, session=None) -> str:
    """Получить информацию о котировках акций"""
    from .api_client import get_api_client
    
    try:
        api = get_api_client()
        data = await api.get_stock(symbol)
        
        if not data:
            return f"❌ Акция '{symbol}' не найдена или данные недоступны"
        
        price = float(data['price'])
        change = data['change']
        change_pct = data['change_percent']
        
        # Определяем направление
        try:
            change_val = float(str(change).replace('%', ''))
            emoji = "📈" if change_val >= 0 else "📉"
            signal = "рост" if change_val >= 0 else "падение"
        except (ValueError, TypeError):
            emoji = "📈"
            signal = ""
        
        result = f"{emoji} **{data['symbol']}** — ${price:.2f}\n"
        result += f"• Изменение: {change} ({change_pct}) {signal}\n"
        result += f"• Объём торгов: {data['volume']}\n"
        result += f"• Дата: {data['trading_day']}\n"
        return result

    except Exception as e:
        logger.error(f"[STOCK] Error: {e}")
        return f"❌ Ошибка получения котировок: {str(e)}"

async def get_news_info(topic: str = None, user_id: int = None, session=None) -> str:
    """Получить новости по теме с AI-анализом"""
    from .api_client import get_api_client
    
    try:
        api = get_api_client()
        articles = await api.get_news(topic=topic, page_size=7)
        
        if not articles:
            return f"❌ Новости по теме '{topic}' не найдены"
        
        if topic and topic.lower() not in ['общие', 'главные', 'главное', 'новости']:
            result = f"📰 **Новости: {topic}**\n\n"
        else:
            result = "📰 **Главные новости:**\n\n"
        
        # AI-синтез главного
        articles_text = "\n".join([
            f"- {a.get('title', '')}" for a in articles[:7]
        ])
        
        try:
            prompt = f"""Вот заголовки новостей по теме "{topic or 'главные'}":

{articles_text}

Выдели главное в 2-3 предложениях: о чём все эти новости, какой общий тренд, что важно."""
            
            summary = await api.deepseek_analyze(
                prompt=prompt,
                system_prompt="Ты новостной аналитик. Кратко и по сути.",
                max_tokens=200
            )
            if summary:
                result += f"📝 **Главное:** {summary}\n\n"
        except Exception as e:
            logger.warning(f"Failed to summarize news: {e}")
        
        for i, article in enumerate(articles[:5], 1):
            title = article.get('title', 'Без заголовка')
            source = article.get('source', '')
            url = article.get('url', '')
            
            result += f"**{i}. {title}**\n"
            if source:
                result += f"📰 {source}"
            if url:
                result += f" | 🔗 {url}"
            result += "\n\n"
        
        return result

    except Exception as e:
        logger.error(f"[NEWS] Error: {e}")
        return f"❌ Ошибка получения новостей: {str(e)}"

async def web_search(query: str, user_id: int = None, session=None) -> str:
    """Выполнить веб-поиск с AI-синтезом результатов"""
    from .api_client import get_api_client
    
    try:
        api = get_api_client()
        results = await api.serper_search(query, num=7)
        
        if not results:
            return f"❌ Результаты поиска по '{query}' не найдены"
        
        # AI-синтез результатов
        context = "\n\n".join([
            f"**{r['title']}**\n{r['snippet']}"
            for r in results[:7]
        ])
        
        prompt = f"""На основе результатов поиска по запросу "{query}":

{context}

Составь краткий ответ (3-5 предложений) на запрос пользователя, используя данные из поиска. Извлеки суть, цифры, конкретику. Не пересказывай каждый результат отдельно — синтезируй в конкретный ответ."""
        
        try:
            synthesis = await api.deepseek_analyze(
                prompt=prompt,
                system_prompt="Ты исследователь. Извлекай суть из данных, не пересказывай.",
                max_tokens=400
            )
        except Exception:
            synthesis = None
        
        result = f"🔍 **По запросу: {query}**\n\n"
        
        if synthesis:
            result += f"{synthesis}\n\n"
        
        # Добавляем топ-3 источника для проверки
        result += "🔗 **Источники:**\n"
        for i, item in enumerate(results[:3], 1):
            result += f"{i}. [{item['title']}]({item['link']})\n"
        
        return result

    except Exception as e:
        logger.error(f"[SEARCH] Error: {e}")
        return f"❌ Ошибка поиска: {str(e)}"

async def analyze_situation_and_suggest_tasks(user_id: int = None, session=None) -> str:
    """
    Умный анализ ситуации пользователя и предложение релевантных задач.
    Анализирует профиль, контакты, тренды и предлагает персонализированные задачи.
    """
    if not user_id:
        return "❌ Не указан ID пользователя"

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
            return "❌ Пользователь не найден"

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
        print(f"[DEBUG] analysis_data['active_tasks'] exists: {'active_tasks' in analysis_data}")  # ВРЕМЕННЫЙ ДЕБАГ
        if 'active_tasks' in analysis_data:
            print(f"[DEBUG] active_tasks count: {len(analysis_data['active_tasks'])}")  # ВРЕМЕННЫЙ ДЕБАГ
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
                for st in similar_tasks[:5]:  # Ограничим для логов
                    st_user = session.query(User).filter_by(id=st.user_id).first()
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
                        match_user = session.query(User).filter_by(id=match['user_id']).first()
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
                trends_result = await get_news_info(primary_interest, user_id, session)
                if trends_result and "❌" not in trends_result and len(trends_result.strip()) > 10:
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
            for contact in analysis_data['relevant_contacts'][:3]:
                partner = session.query(User).filter_by(id=contact.user_id).first()
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
                result = "💪 **Вижу у тебя есть задачи. Вот что предлагаю:**\n\n"
            else:
                result = "🧠 **Вот что можно сделать прямо сейчас:**\n\n"
            
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
            result = "Расскажи, чем занимаешься или что планируешь — помогу разобраться 🤝"
        
        return result

    except Exception as e:
        logger.error(f"[SITUATION_ANALYSIS] Error: {e}")
        if close_session:
            session.close()
        return f"❌ Ошибка анализа ситуации: {str(e)}"
    finally:
        if close_session:
            session.close()
