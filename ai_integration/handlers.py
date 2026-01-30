# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timezone, timedelta
import pytz
from models import Session, Task, User, UserProfile, SubscriptionTier, Subscription
from sqlalchemy import or_, and_, func

from .memory import encrypt_data, decrypt_data
from .utils import parse_relative_time, parse_natural_time, parse_time_to_datetime, generate_task_recommendations
from .task_search import find_task_flexible

logger = logging.getLogger(__name__)


def check_time_conflicts(user_db_id, parsed_time, session):
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
            
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        
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
            
            task_list = "\n".join([f"• {task.title} ({task.reminder_time.astimezone(user_tz).strftime('%H:%M')})" for task in conflicting_tasks])
            
            conflict_message = f"В это время у тебя уже запланированы задачи:\n{task_list}"
            
            if suggested_time:
                suggested_str = suggested_time.astimezone(user_tz).strftime('%H:%M')
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
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        
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
        existing_times = [task.reminder_time.astimezone(user_tz) for task in existing_tasks]
        target_local = target_time.astimezone(user_tz)
        
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
                return check_time.replace(tzinfo=user_tz)
        
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
                return check_time.replace(tzinfo=user_tz)
                
    except Exception as e:
        logger.warning(f"Error finding free slot: {e}")
    
    return None


def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None, ignore_conflicts=False):
    """Add a new task"""
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}")
    
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
    if len(title) > 60 or word_count > 10:
        logger.warning(f"[ADD_TASK] Title too long ({len(title)} chars, {word_count} words), attempting smart extraction")
        # Попытка извлечь ключевые слова (простая эвристика)
        # Убираем стоп-слова и берём первые 5 значимых слов
        stop_words = ['нужно', 'надо', 'необходимо', 'давай', 'создай', 'добавь', 'напомни', 'поставь', 'я', 'мне', 'для', 'чтобы', 'как']
        words = [w for w in title.split() if w.lower() not in stop_words and len(w) > 2]
        if len(words) > 5:
            title = ' '.join(words[:5])
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
            # Get user timezone
            user_tz = pytz.UTC
            if user.timezone:
                try:
                    user_tz = pytz.timezone(user.timezone)
                except pytz.exceptions.UnknownTimeZoneError:
                    logging.warning(f"Unknown timezone {user.timezone}, using UTC")
                    user_tz = pytz.UTC

            # Use AI-powered flexible time parser
            from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
            
            current_time = datetime.now(user_tz)
            logger.info(f"[ADD_TASK] Parsing time '{reminder_time}' with AI, current: {current_time}")
            
            parsed_time = parse_time_with_ai(reminder_time, current_time)
            
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
        if due_date:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(due_date, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.due_date = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        session.add(task)

        # Generate recommendations
        try:
            logger.info(f"[ADD_TASK] Generating recommendations for task '{title}'")
            recommendations = generate_task_recommendations(title, description, user.telegram_id)
            logger.info(f"[ADD_TASK] Generated {len(recommendations) if recommendations else 0} recommendations")
            if recommendations:
                task.recommendations = json.dumps(recommendations, ensure_ascii=False)
                logger.info(f"[ADD_TASK] Saved recommendations to task: {task.recommendations}")
        except Exception as e:
            logging.warning(f"Could not generate recommendations for task {title}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        session.commit()
        task_id = task.id
        logger.info(f"[ADD_TASK] Task '{title}' created successfully with ID {task_id}, reminder_time: {task.reminder_time}")

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
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        time_str = local_time.strftime('%H:%M')
        date_str = local_time.strftime('%d.%m.%Y')
        result_msg += f" с напоминанием на {date_str} в {time_str}"

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg


def set_recurring_task(title, description="", recurrence_pattern=None, recurrence_interval=1, first_reminder_time=None, recurrence_end_date=None, user_id=None, session=None):
    """Create a recurring task that generates instances automatically"""
    logger.info(f"[SET_RECURRING_TASK] Called with title='{title}', pattern='{recurrence_pattern}', interval={recurrence_interval}, user_id={user_id}")

    if user_id is None:
        logger.error("[SET_RECURRING_TASK] ERROR: user_id is None!")
        return "ERROR: user_id is required"

    if not recurrence_pattern:
        logger.error("[SET_RECURRING_TASK] ERROR: recurrence_pattern is required!")
        return "ERROR: Не указан паттерн повторения (daily/weekly/monthly/yearly)"

    if not first_reminder_time:
        logger.error("[SET_RECURRING_TASK] ERROR: first_reminder_time is required!")
        return "ERROR: Не указано время первого напоминания"

    # Validate recurrence pattern
    valid_patterns = ['daily', 'weekly', 'monthly', 'yearly']
    if recurrence_pattern not in valid_patterns:
        logger.error(f"[SET_RECURRING_TASK] Invalid pattern: {recurrence_pattern}")
        return f"ERROR: Неправильный паттерн повторения. Допустимые: {', '.join(valid_patterns)}"

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Check if user exists
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id)
            session.add(user)
            session.commit()

        # Parse first reminder time
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        current_time = datetime.now(user_tz)

        from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback

        parsed_time = parse_time_with_ai(first_reminder_time, current_time)
        if not parsed_time:
            parsed_time = parse_time_simple_fallback(first_reminder_time, current_time)

        if not parsed_time:
            if close_session:
                session.close()
            return f"ERROR: Не удалось распарсить время '{first_reminder_time}'"

        # Parse recurrence end date if provided
        end_date = None
        if recurrence_end_date:
            end_parsed = parse_time_with_ai(recurrence_end_date, current_time)
            if not end_parsed:
                end_parsed = parse_time_simple_fallback(recurrence_end_date, current_time)
            if end_parsed:
                end_date = end_parsed.astimezone(pytz.UTC)

        # Create the recurring task template
        recurring_task = Task(
            user_id=user.id,
            title=title,
            description=encrypt_data(description),
            is_recurring=True,
            recurrence_pattern=recurrence_pattern,
            recurrence_interval=recurrence_interval,
            recurrence_end_date=end_date,
            reminder_time=parsed_time.astimezone(pytz.UTC)
        )

        session.add(recurring_task)
        session.commit()

        # Create first instance immediately
        first_instance = Task(
            user_id=user.id,
            title=title,
            description=encrypt_data(description),
            reminder_time=parsed_time.astimezone(pytz.UTC),
            parent_task_id=recurring_task.id
        )

        session.add(first_instance)
        session.commit()

        # Schedule reminder for first instance
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE:
                REMINDER_SERVICE.schedule_reminder(
                    task_id=first_instance.id,
                    reminder_time=first_instance.reminder_time,
                    user_id=user.telegram_id,
                    task_title=first_instance.title
                )
                logger.info(f"[SET_RECURRING_TASK] Scheduled first reminder for recurring task {first_instance.id}")
        except Exception as e:
            logger.warning(f"Could not schedule reminder for recurring task instance {first_instance.id}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        # Format result message
        pattern_text = {
            'daily': f'каждый {recurrence_interval} день' if recurrence_interval > 1 else 'каждый день',
            'weekly': f'каждые {recurrence_interval} недели' if recurrence_interval > 1 else 'каждую неделю',
            'monthly': f'каждые {recurrence_interval} месяца' if recurrence_interval > 1 else 'каждый месяц',
            'yearly': f'каждый {recurrence_interval} год' if recurrence_interval > 1 else 'каждый год'
        }.get(recurrence_pattern, recurrence_pattern)

        result_msg = f"Создана повторяющаяся задача '{title}' ({pattern_text})"

        if end_date:
            end_local = end_date.astimezone(user_tz)
            end_str = end_local.strftime('%d.%m.%Y')
            result_msg += f" до {end_str}"

        logger.info(f"[SET_RECURRING_TASK] Recurring task created successfully: {result_msg}")

        if close_session:
            session.close()

        return result_msg

    except Exception as e:
        logger.error(f"[SET_RECURRING_TASK] Error creating recurring task: {e}")
        if close_session:
            session.close()
        return f"ERROR: Не удалось создать повторяющуюся задачу: {str(e)}"


def delete_all_tasks(user_id=None, session=None):
    """Delete all tasks for a user"""
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

        # Count tasks before deletion
        task_count = session.query(Task).filter_by(user_id=user.id).count()

        # Delete all tasks
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Reset profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.total_tasks_created = 0
            profile.completed_tasks = 0
            profile.skipped_tasks = 0
            session.commit()

        if close_session:
            session.close()
        return f"🗑️ Удалено {task_count} задач"

    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        if close_session:
            session.close()
        return f"❌ Ошибка удаления задач: {str(e)}"


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
    
    # ТРЕБУЕМ task_id или task_title - не используем "последнюю задачу" автоматически
    if task_id_int is None and (task_title is None or task_title.strip() == ""):
        logger.warning("[COMPLETE_TASK] No task_id or task_title provided")
        return "ERROR: Укажите какую задачу нужно завершить (ID или название)"
    
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
        # Если task_title не указан, завершаем последнюю активную задачу
        if not task_title or not task_title.strip():
            logger.info("[COMPLETE_TASK] No task_title provided, completing the most recent active task")
            
            # Найти последнюю активную задачу пользователя
            recent_task = session.query(Task).filter(
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
        task.actual_completion_time = datetime.now(timezone.utc)
        
        # Сохраняем заметку о результате выполнения
        if completion_note:
            task.completion_notes = encrypt_data(completion_note)
            logger.info(f"[COMPLETE_TASK] Saved completion note for task {task.id}")
        
        try:
            session.commit()
            logger.info(f"[COMPLETE_TASK] Task {task.id} status set to 'completed', committed to database")
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

        # Schedule result check - уточнение результата выполнения через 1 час
        result_check_time = datetime.now(timezone.utc) + timedelta(hours=1)
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
                datetime.now(timezone.utc) - task.created_at.replace(tzinfo=timezone.utc)
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
                and_(Task.user_id == user.id, or_(*conditions)),
                and_(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
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
        if close_session:
            session.close()
        return "Не указано название задачи."

    if task:
        try:
            # Parse new time with AI (flexible!)
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            current_time = datetime.now(user_tz)
            logger.info(f"[RESCHEDULE_TASK] Parsing time '{new_time}', current time: {current_time}")
            
            # Use AI for flexible time parsing
            from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
            
            local_dt = None
            try:
                local_dt = parse_time_with_ai(new_time, current_time)
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
            session.commit()
            logger.info(f"[RESCHEDULE_TASK] ✅ Task {task.id} updated, new time (UTC): {task.reminder_time}, local: {local_dt}")

            # Перепланируем напоминание (создается новое или обновляется существующее)
            try:
                from reminder_service import REMINDER_SERVICE
                if REMINDER_SERVICE:
                    REMINDER_SERVICE.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title
                    )
                    logger.info(f"[RESCHEDULE_TASK] ✅ Reminder rescheduled for task {task.id} at {task.reminder_time}")
                else:
                    logger.warning(f"[RESCHEDULE_TASK] REMINDER_SERVICE not initialized, cannot reschedule reminder")
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


async def get_task_advice(task_id=None, user_id=None, session=None):
    import asyncio

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

    # Find task by ID
    if task_id:
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        task = session.query(Task).filter(Task.id == task_id_int, Task.user_id == user.id).first()
    else:
        if close_session:
            session.close()
        return "Не указан task_id."

    if task:
        # Get task details
        title = task.title
        description = decrypt_data(task.description) if task.description else ""
        status = task.status

        # Generate advice using AI
        prompt = """Дай полезный совет по выполнению этой задачи:

Задача: {title}
Описание: {description}
Статус: {status}

Дай конкретные, практические рекомендации по:
1. Как лучше подойти к выполнению
2. Возможные сложности и как их избежать
3. Советы по эффективности

Ответ должен быть кратким и полезным."""

        try:
            import asyncio
            from .chat import chat_with_ai
            ai_result = asyncio.run(chat_with_ai(user_id, prompt))
            advice = ai_result['response']
            result = f"Совет по задаче '{title}':\n\n{advice}"

            # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
        except Exception as e:
            logger.error(f"Error getting AI advice: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()
            result = f"Не удалось получить совет по задаче '{title}'. Попробуйте позже."
    else:
        result = "Задача не найдена."

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
        # Check if delegator has Light tier - Light users can only receive delegated tasks
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."
        
        # Log tier for debugging
        logger.info(f"[DELEGATE] User {user_id} tier: {delegator.subscription_tier.value if delegator.subscription_tier else 'None'}")
        
        # Skip subscription check in FREE_ACCESS_MODE
        if not FREE_ACCESS_MODE and delegator.subscription_tier and delegator.subscription_tier not in [SubscriptionTier.STANDARD, SubscriptionTier.PREMIUM]:
            return "DELEGATION_SUBSCRIPTION_REQUIRED: Делегирование задач доступно только на тарифах Standard и Premium. Обновите подписку: https://asibiont.ru/subscription_tiers"
        
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
                if delegator.username.lower().replace('@', '') in [b.lower().replace('@', '') for b in blocked_list]:
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
                user_tz = pytz.timezone(recipient.timezone) if recipient.timezone else pytz.UTC
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
            session.close()
        return f"ERROR: Произошла ошибка при делегировании задачи: {str(e)}"

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





def accept_delegated_task(task_id, user_id=None):
    """Accept a delegated task"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

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
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status and task status
        task.delegation_status = "accepted"
        task.status = "in_progress"  # Задача теперь в работе
        session.commit()

        # Schedule reminder
        if task.reminder_time:
            try:
                from reminder_service import REMINDER_SERVICE
                if REMINDER_SERVICE:
                    REMINDER_SERVICE.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title,
                    )
            except Exception as e:
                logging.error(f"Failed to schedule reminder: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()

        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user.username} принял задачу: {task.title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        session.close()
        return f"Вы приняли задачу '{task.title}'. Она добавлена в ваш список задач."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        session.close()
        return f"Ошибка: {str(e)}"


def reject_delegated_task(task_id=None, task_title=None, user_id=None):
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

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"
        session.commit()

        # Отменяем все запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Отменяем напоминание
                reminder_job_id = f"reminder_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled reminder job for task {task.id}")
                
                # Отменяем проверку результата
                result_check_job_id = f"result_check_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled result check job for task {task.id}")
                
                # Отменяем чекпоинты задач
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task.id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[REJECT_DELEGATED_TASK] Cancelled checkpoint job {checkpoint_type} for task {task.id}")
                
                # Отменяем чекпоинт 1/3
                checkpoint_1_3_job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[REJECT_DELEGATED_TASK] Cancelled 1/3 checkpoint job for task {task.id}")
        except Exception as e:
            logger.warning(f"[REJECT_DELEGATED_TASK] Could not cancel scheduled jobs for task {task.id}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user.username} отклонил задачу: {task.title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        session.close()
        return f"Вы отклонили задачу '{task.title}'."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        session.close()
        return f"Ошибка: {str(e)}"



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

        return f"DELEGATION_REPORT: {report}"

    except Exception as e:
        logger.error(f"Error getting delegation progress for user {user_id}: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        if should_close:
            session.close()
        return f"Ошибка при получении отчета о делегировании: {str(e)}"


def cancel_delegation(task_id, user_id=None):
    """Cancel delegation of a task, returning it to the initiator"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            session.close()
            return "Ошибка: Пользователь не найден."

        # Ищем задачу где текущий пользователь является делегатором
        task = session.query(Task).filter_by(id=int(task_id), delegated_by=user.id).first()
        if not task:
            session.close()
            return "Задача не найдена или вы не являетесь делегатором этой задачи."

        if not task.delegated_to_username:
            session.close()
            return "Эта задача не делегирована."

        # Check if task is already completed
        if task.status == "completed":
            session.close()
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
        session.close()

        return f"Делегирование задачи '{task_title}' для @{delegated_to} отменено. Задача возвращена в ваш список."
    except Exception as e:
        import traceback
        traceback.print_exc()
        session.rollback()
        session.close()
        return f"Ошибка при отмене делегирования: {str(e)}"


def edit_task(
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
            session.close()
            return "У вас нет прав на редактирование этой задачи."

        if title:
            task.title = title
        if description is not None:
            # Only encrypt if not already encrypted (prevents double encryption)
            if description and not description.startswith('gAAAAA'):
                task.description = encrypt_data(description)
            else:
                task.description = description
        if reminder_time:
            try:
                # Use AI-powered flexible time parser
                from ai_integration.time_parser import parse_time_with_ai, parse_time_simple_fallback
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                current_time = datetime.now(user_tz)
                logger.info(f"[EDIT_TASK] Parsing time '{reminder_time}' with AI, current: {current_time}")
                
                parsed_time = parse_time_with_ai(reminder_time, current_time)
                
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


def list_tasks(user_id=None, session=None, include_completed=False):
    """Return list of user's tasks in plain text format
    
    Args:
        user_id: Telegram ID пользователя
        session: Database session (опционально)
        include_completed: Если True, показывает только выполненные задачи. По умолчанию False (активные)
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

        # Get user tasks or delegated tasks
        query = session.query(Task).filter(Task.user_id == user.id)
        if user.username and user.username.strip():
            query = query.union(
                session.query(Task).filter(Task.delegated_to_username.ilike((user.username or "").replace('@', '')))
            )
        tasks = query.all()

        if not tasks:
            return "У вас нет задач" if include_completed else "У вас нет активных задач. Добавьте первую задачу - просто напишите что нужно сделать!"

        # Format detailed list
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
        
        # Если запрошены выполненные задачи, показываем только их
        if include_completed:
            if not completed_tasks:
                return "У вас пока нет выполненных задач"
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
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
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
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
                    import traceback
                    traceback.print_exc()
                    session.rollback()
                    pass

        # Format brief response
        if not active_tasks:
            return "Нет активных задач. Что планируете?"

        # Правильный подсчёт: только личные незавершённые задачи
        result = f"У вас {len(my_tasks)} {'задача' if len(my_tasks) == 1 else ('задачи' if 2 <= len(my_tasks) <= 4 else 'задач')}"
        if delegated_to_me:
            result += f" + {len(delegated_to_me)} делегированных"
        result += "\n\n"

        # Show first 10 tasks instead of 3
        tasks_to_show = my_tasks[:10]
        if tasks_to_show:
            result += "Ваши задачи:\n"
            for task in tasks_to_show:
                reminder_info = ""
                status_marker = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            delta = now - reminder_dt
                            days = delta.days
                            hours = (delta.seconds // 3600)
                            status_marker = " [ПРОСРОЧЕНО]"
                            if days > 0:
                                reminder_info = f" - просрочено на {days} д {hours} ч" if hours else f" - просрочено на {days} д"
                            else:
                                reminder_info = f" - просрочено на {hours} ч"
                        else:
                            status_marker = " [АКТУАЛЬНО]"
                            # Добавляем часовой пояс к времени для ясности
                            tz_name = user_tz.zone if user_tz != pytz.UTC else 'UTC'
                            reminder_info = f" - {reminder_dt.strftime('%d.%m.%Y %H:%M')} ({tz_name})"
                    except Exception as e:
                        logger.warning(f"Failed to process reminder time for task {task.id}: {e}")
                        pass
                result += f"- {task.title}{status_marker}{reminder_info}\n"

            if len(my_tasks) > 10:
                result += f"...и ещё {len(my_tasks) - 10}\n"
        
        # Show delegated tasks
        if delegated_to_me:
            result += "\nДелегированные мне:\n"
            for task in delegated_to_me[:5]:
                # Get delegator info
                delegator_info = "неизвестно"
                if task.delegated_by:
                    delegator = session.query(User).filter_by(id=task.delegated_by).first()
                    if delegator and delegator.username:
                        delegator_info = f"@{delegator.username}"
                
                # Add delegation status indicator
                delegation_status_text = ""
                if task.delegation_status == "pending":
                    delegation_status_text = " [ОЖИДАЕТ ПРИНЯТИЯ]"
                elif task.delegation_status == "accepted":
                    delegation_status_text = " [ПРИНЯТО]"
                elif task.delegation_status == "rejected":
                    delegation_status_text = " [ОТКЛОНЕНО]"
                
                # Add time status indicator
                time_status_text = ""
                reminder_info = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            delta = now - reminder_dt
                            days = delta.days
                            hours = (delta.seconds // 3600)
                            time_status_text = " [ПРОСРОЧЕНО]"
                            if days > 0:
                                reminder_info = f" - просрочено на {days} д {hours} ч" if hours else f" - просрочено на {days} д"
                            else:
                                reminder_info = f" - просрочено на {hours} ч"
                        else:
                            time_status_text = " [АКТУАЛЬНО]"
                            tz_name = user_tz.zone if user_tz != pytz.UTC else 'UTC'
                            reminder_info = f" - {reminder_dt.strftime('%d.%m.%Y %H:%M')} ({tz_name})"
                    except Exception as e:
                        logger.warning(f"Failed to process reminder time for delegated task {task.id}: {e}")
                        pass
                
                result += f"- {task.title} (от {delegator_info}){delegation_status_text}{time_status_text}{reminder_info}\n"

        # Brief recommendation
        if overdue_count > 0:
            result += f"\n\n{overdue_count} просроченных - стоит разобраться"
        elif len(active_tasks) == 1:
            result += "\n\nОдна задача - отличный фокус"
        elif len(active_tasks) > 5:
            result += "\n\nМного задач - приоритизируй"

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

        # Check goals
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True
                match_reasons.append(f"goals: {user_goals & profile_goals}")

        # Check company
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
                    match_reasons.append(f"company: {profile.company}")

        # ВАЖНО: Всегда показывать избранные и заблокированные контакты
        
        # Получаем АКТУАЛЬНЫЕ тарифы из таблицы Subscription (не из User.subscription_tier!)
        profile_user_subscription = session.query(Subscription).filter_by(user_id=profile_user.id, status='active').first()
        user_subscription = session.query(User).filter_by(id=user.id).first()
        user_subscription_obj = session.query(Subscription).filter_by(user_id=user.id, status='active').first()
        
        profile_user_tier = profile_user_subscription.tier.value if profile_user_subscription and profile_user_subscription.tier else 'LIGHT'
        user_tier = user_subscription_obj.tier.value if user_subscription_obj and user_subscription_obj.tier else 'LIGHT'
        
        logger.info(f"[PARTNERS] Checking {profile_user.username}: profile_tier={profile_user_tier}, user_tier={user_tier}")
        
        # КРИТИЧНАЯ ФИЛЬТРАЦИЯ ПО ТАРИФАМ:
        # LIGHT: видят LIGHT + STANDARD (не видят PREMIUM)
        # STANDARD: видят LIGHT + STANDARD (не видят PREMIUM)
        # PREMIUM: видят всех
        
        if user_tier in ['LIGHT', 'STANDARD'] and profile_user_tier == 'PREMIUM':
            logger.info(f"[PARTNERS] Skipping PREMIUM user {profile_user.username} for {user_tier} user")
            continue
        
        # Специальное правило для PREMIUM: они видят ВСЕХ (даже без совпадений)
        if user_tier == 'PREMIUM':
            has_match = True  # PREMIUM видит всех
            match_reasons.append("premium-sees-all")
        
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

    # Sort: first users from same city, then others
    user_city = user_profile.city.lower() if user_profile.city else None
    partners_same_city = []
    partners_other_city = []

    for partner in partners:
        partner_city = partner.city.lower() if partner.city else None
        if user_city and partner_city == user_city:
            partners_same_city.append(partner)
        else:
            partners_other_city.append(partner)

    # Sort each group by average rating
    partners_same_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)
    partners_other_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)

    # Combine: first from same city, then others
    sorted_partners = partners_same_city + partners_other_city
    
    logger.info(f"[PARTNERS] Sorted results: {len(partners_same_city)} from same city, {len(partners_other_city)} from other cities")
    
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
    
    # Добавляем информацию об общих интересах, навыках, целях и задачах
    user_interests = set(i.strip().lower() for i in user_profile.interests.split(',')) if user_profile.interests else set()
    user_skills = set(s.strip().lower() for s in user_profile.skills.split(',')) if user_profile.skills else set()
    user_goals = set(g.strip().lower() for g in user_profile.goals.split(',')) if user_profile.goals else set()
    
    for partner in sorted_partners:
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
    
    # Пересортируем партнеров с учетом релевантности для задач
    # Сначала релевантные для задач (по убыванию score), потом остальные
    relevant_for_tasks = [p for p in sorted_partners if p.task_relevance_score > 0]
    relevant_for_tasks.sort(key=lambda p: p.task_relevance_score, reverse=True)
    
    not_relevant_for_tasks = [p for p in sorted_partners if p.task_relevance_score == 0]
    
    sorted_partners = relevant_for_tasks + not_relevant_for_tasks
    
    logger.info(f"[PARTNERS] Task-relevant partners: {len(relevant_for_tasks)}, other: {len(not_relevant_for_tasks)}")
    
    for partner in sorted_partners[:5]:  # Log top 5
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

    if close_session:
        session.close()

    return sorted_partners[:50]  # Увеличено с 20 до 50


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

    # Разделяем партнеров на избранные и рекомендованные
    favorite_partners = []
    recommended_partners = []
    
    for p in partners:
        partner_user = session.query(User).filter_by(id=p.user_id).first()
        if partner_user and partner_user.username:
            # Проверяем, является ли контакт избранным
            is_favorite = False
            if user_profile.favorite_contacts:
                favorite_usernames = [u.strip().lower().replace('@', '') for u in user_profile.favorite_contacts.split(',')]
                if partner_user.username.replace('@', '').lower() in favorite_usernames:
                    is_favorite = True
            
            if is_favorite:
                favorite_partners.append(p)
            else:
                recommended_partners.append(p)

    # Format response
    response = ""
    
    # Сначала показываем избранные контакты
    if favorite_partners:
        response += "⭐ Избранные контакты:\n"
        for idx, p in enumerate(favorite_partners[:2], 1):  # Максимум 2 избранных
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            if partner_user and partner_user.username:
                info_parts = []
                if p.interests:
                    info_parts.append(f"интересы: {p.interests}")
                if hasattr(p, "bio") and p.bio:
                    bio_short = p.bio[:80] + "..." if len(p.bio) > 80 else p.bio
                    info_parts.append(f"сфера деятельности: {bio_short}")
                if hasattr(p, "position") and p.position:
                    info_parts.append(f"{p.position}")
                if hasattr(p, "company") and p.company:
                    info_parts.append(f"компания: {p.company}")
                if p.city:
                    info_parts.append(f"город: {p.city}")

                info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"
                response += f"⭐ @{partner_user.username}\n   {info_str}\n"
        
        if recommended_partners:
            response += "\n"
    
    # Затем показываем рекомендованных
    if recommended_partners:
        response += "💡 Рекомендованные контакты:\n"
        for idx, p in enumerate(recommended_partners[:3], 1):  # Максимум 3 рекомендованных
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            if partner_user and partner_user.username:
                info_parts = []
                if p.interests:
                    info_parts.append(f"интересы: {p.interests}")
                if hasattr(p, "bio") and p.bio:
                    bio_short = p.bio[:80] + "..." if len(p.bio) > 80 else p.bio
                    info_parts.append(f"сфера деятельности: {bio_short}")
                if hasattr(p, "position") and p.position:
                    info_parts.append(f"{p.position}")
                if hasattr(p, "company") and p.company:
                    info_parts.append(f"компания: {p.company}")
                if p.city:
                    info_parts.append(f"город: {p.city}")

                info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"
                response += f"{idx}. @{partner_user.username}\n   {info_str}\n"
    
    if not favorite_partners and not recommended_partners:
        response = "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, город), и я найду единомышленников!"

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
    stop_words = {'я', 'мне', 'нужно', 'надо', 'хочу', 'буду', 'пойду', 'сделать', 'в', 'на', 'с', 'для', 'от', 'к', 'по'}
    
    # Синонимы для расширения поиска
    synonyms = {
        'пробежка': ['бег', 'бегать', 'running', 'jogging'],
        'тренировка': ['фитнес', 'спорт', 'gym', 'workout'],
        'йога': ['yoga', 'медитация', 'растяжка'],
        'плавание': ['бассейн', 'swimming', 'плавать'],
        'футбол': ['football', 'soccer'],
        'стартап': ['startup', 'бизнес', 'предпринимательство'],
        'инвестиции': ['invest', 'финансы', 'вложения'],
        'программирование': ['coding', 'разработка', 'development', 'python', 'javascript'],
    }
    
    words = [w.lower().strip() for w in task_description.split() if len(w) > 3 and w.lower() not in stop_words]
    task_keywords.update(words)
    
    # Добавить синонимы
    for word in words:
        for key, syns in synonyms.items():
            if key in word or any(syn in word for syn in syns):
                task_keywords.update([key] + syns)
    
    logger.info(f"[FIND_RELEVANT] Task keywords: {task_keywords}")
    
    # Получить всех потенциальных партнеров
    all_partners = get_partners_list(user_id=user.id, session=session)
    
    if not all_partners:
        if close_session:
            session.close()
        return "В вашей сети пока нет контактов. Заполните профиль (интересы, навыки) чтобы найти людей со схожими интересами."
    
    # Найти релевантные контакты
    relevant_contacts = []
    
    for partner in all_partners:
        relevance_score = 0
        match_reasons = []
        
        # Проверка интересов
        if hasattr(partner, 'interests') and partner.interests:
            partner_interests = set(i.lower().strip() for i in partner.interests.split(','))
            interest_match = task_keywords & partner_interests
            if interest_match:
                relevance_score += len(interest_match) * 3
                match_reasons.append(f"интересы: {', '.join(list(interest_match)[:2])}")
        
        # Проверка навыков
        if hasattr(partner, 'skills') and partner.skills:
            partner_skills = set(s.lower().strip() for s in partner.skills.split(','))
            skill_match = task_keywords & partner_skills
            if skill_match:
                relevance_score += len(skill_match) * 5  # Навыки важнее
                match_reasons.append(f"навыки: {', '.join(list(skill_match)[:2])}")
        
        # Используем уже вычисленную релевантность из get_partners_list
        if hasattr(partner, 'task_relevance_score') and partner.task_relevance_score > 0:
            relevance_score += partner.task_relevance_score
            if hasattr(partner, 'task_relevance') and partner.task_relevance:
                match_reasons.append(partner.task_relevance)
        
        if relevance_score > 0:
            # Получить username пользователя
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
    
    # Сортировка по релевантности
    relevant_contacts.sort(key=lambda x: x['score'], reverse=True)
    
    if close_session:
        session.close()
    
    # Формирование ответа
    if not relevant_contacts:
        return "Не нашел подходящих контактов для этой задачи. Попробуйте заполнить больше информации в профиле или создайте задачу с более конкретным описанием."
    
    # Ограничить до limit контактов
    top_contacts = relevant_contacts[:limit]
    
    result_lines = [f"🎯 Нашел {len(top_contacts)} подходящих контактов для этой задачи:\n"]
    
    for i, contact in enumerate(top_contacts, 1):
        line = f"{i}. @{contact['username']}"
        if contact['name'] != contact['username']:
            line += f" ({contact['name']})"
        
        if contact['reasons']:
            line += f" - {', '.join(contact['reasons'][:2])}"
        
        if contact['city']:
            line += f" | {contact['city']}"
        
        result_lines.append(line)
    
    result_lines.append("\n💡 Совет: вы можете делегировать задачу или пригласить их к совместной активности через раздел 'Контакты' в дашборде.")
    
    return '\n'.join(result_lines)


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
    from config import DEEPSEEK_API_KEY
    from .prompts import get_extended_system_prompt
    from .utils import clean_technical_details

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None)

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

        data = {"model": "deepseek-chat", "messages": messages, "temperature": 0.8, "max_tokens": 200}

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
    from config import DEEPSEEK_API_KEY
    from .prompts import get_extended_system_prompt
    from .utils import clean_technical_details

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_extended_system_prompt(None, "", "", "system", "", "", None, None, None, None)

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

        data = {"model": "deepseek-chat", "messages": messages, "temperature": 0.7, "max_tokens": 150}

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

        current_time = datetime.now(timezone.utc)
        
        # Ensure deadline is timezone-aware
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        
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
        current_time = datetime.now(timezone.utc)

        # Find accepted delegated tasks that are overdue
        overdue_tasks = session.query(Task).filter(
            Task.delegation_status == "accepted",
            Task.status != "completed",
            Task.reminder_time < current_time
        ).all()

        for task in overdue_tasks:
            try:
                # Get delegator and recipient info
                # Reminder functionality disabled - function doesn't exist
                # if delegator and recipient:
                #     # Generate AI-powered reminder - DISABLED: function doesn't exist
                #     # import asyncio
                #     # reminder_text = asyncio.run(generate_progress_reminder(
                #     #     task.title,
                #     #     delegator.username,
                #     #     days_overdue,
                #     #     recipient.telegram_id
                #     # ))

                #     # if reminder_text:
                #     #     # Send reminder to recipient
                #     #     from main import bot
                #     #     if bot:
                #     #         try:
                #     #             asyncio.run(bot.send_message(
                #     #         recipient.telegram_id,
                #     #         f"🔔 Напоминание о делегированной задаче:\n\n{reminder_text}\n\nЗадача: {task.title}"
                #     #     ))
                #     #         logger.info(f"Sent overdue reminder for task {task.id} to @{recipient.username}")
                #     #         except Exception as e:
                #     #             logger.error(f"Failed to send reminder to recipient: {e}")

                #     # # Notify delegator about overdue task
                #     # try:
                #     #     asyncio.run(bot.send_message(
                #     #         delegator.telegram_id,
                #     #             "⚠️ Делегированная задача просрочена!\n\n"
                #     #             f"Задача: {task.title}\n"
                #     #             f"Исполнитель: @{recipient.username}\n"
                #     #             f"Просрочена на: {days_overdue} дней\n\n"
                #     #             "Рекомендую связаться с исполнителем для уточнения статуса."
                #     #         ))
                #     #     logger.info(f"Notified delegator {delegator.username} about overdue task {task.id}")
                #     # except Exception as e:
                #     #     logger.error(f"Failed to notify delegator: {e}")

                # End of task processing
                pass

            except Exception as e:
                logger.error(f"Error processing overdue task {task.id}: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()

        session.close()
    except Exception as e:
        logger.error(f"Error in check_delegation_deadlines: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        session.close()


def update_user_memory(info=None, user_id=None, session=None):
    """Обновить память пользователя"""
    try:
        if not session:
            session = Session()
            should_close = True
        else:
            should_close = False

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if should_close:
                session.close()
            return "Пользователь не найден"

        # Зашифровать и сохранить информацию
        encrypted_info = encrypt_data(info)
        user.memory = encrypted_info
        session.commit()

        if should_close:
            session.close()

        return "Память пользователя обновлена"

    except Exception as e:
        logger.error(f"Error updating user memory for user {user_id}: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        if should_close and 'session' in locals():
            session.close()
def delete_task_sync(task_id=None, task_title=None, reason=None, user_id=None, session=None, confirmed=False):
    """Delete a task by ID or title"""
    from models import User  # Явный импорт для избежания конфликтов области видимости
    logger.info(f"[DELETE_TASK] Called with task_id={task_id}, task_title='{task_title}', reason='{reason}', user_id={user_id}, confirmed={confirmed}")
    
    if user_id is None:
        logger.error("[DELETE_TASK] user_id is None")
        return "ERROR: user_id не может быть None"
    
    if task_id is None and (task_title is None or task_title.strip() == ""):
        logger.error("[DELETE_TASK] Both task_id and task_title are None/empty") 
        return "ERROR: Не указан идентификатор или название задачи"
    
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
                logger.info(f"[DELETE_TASK] Using current task: '{current_task.title}' for pronoun '{task_title}'")
                task = current_task
                # Пропускаем обычный поиск
            else:
                logger.warning(f"[DELETE_TASK] No current task set for pronoun '{task_title}'")
                task = None
        else:
            task = None  # Будет найден через find_task_flexible
    else:
        task = None

    # Если задача не найдена через контекст, используем обычный поиск
    if task is None:
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
            include_completed=True,
            include_delegated=True
        )
    if task:
        # Check if task is already completed - allow deletion but with different message
        was_completed = task.status == "completed"
        
        # If not confirmed and task is active, ask for confirmation
        if not confirmed and task.status in ["pending", "active", "in_progress"]:
            if close_session:
                session.close()
            return f"CONFIRM_DELETE: Вы уверены, что хотите удалить задачу '{task.title}'? Это действие нельзя отменить."
        
        # Save deletion reason for analytics
        deletion_reason = reason or "Пользователь удалил задачу"
        
        # Cancel all scheduled jobs for this task
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Cancel reminder
                reminder_job_id = f"reminder_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled reminder job for task {task.id}")
                
                # Cancel result check
                result_check_job_id = f"result_check_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled result check job for task {task.id}")
                
                # Cancel task checkpoints
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task.id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[DELETE_TASK] Cancelled checkpoint job {checkpoint_type} for task {task.id}")
                
                # Cancel 1/3 checkpoint
                checkpoint_1_3_job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled 1/3 checkpoint job for task {task.id}")
        except Exception as e:
            logger.warning(f"[DELETE_TASK] Could not cancel scheduled jobs for task {task.id}: {e}")
            import traceback
            traceback.print_exc()
            session.rollback()

        # ВАЖНО: Обнулить current_task_id если удаляемая задача является текущей
        if user.current_task_id == task.id:
            user.current_task_id = None
            session.commit()
            logger.info(f"[DELETE_TASK] Cleared current_task_id for user {user.id}")

        # Delete the task from database
        task_title = task.title
        print(f"[DEBUG] delete_task_sync: deleting task {task.id} '{task.title}'")
        session.delete(task)
        session.commit()
        print(f"[DEBUG] delete_task_sync: committed deletion")

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            if was_completed:
                # If task was completed, decrement completed count
                if profile.completed_tasks and profile.completed_tasks > 0:
                    profile.completed_tasks -= 1
            else:
                # If task was not completed, decrement created count
                if profile.total_tasks_created and profile.total_tasks_created > 0:
                    profile.total_tasks_created -= 1
            session.commit()

        # Return appropriate message
        if was_completed:
            result = f"Задача '{task_title}' удалена из истории выполненных задач."
        else:
            result = f"Задача '{task_title}' удалена."

        if close_session:
            session.close()
        return result
    else:
        if close_session:
            session.close()
        return "Задача не найдена."


def create_subscription_payment(tier=None, user_id=None, session=None):
    """Create subscription payment"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        tier = tier or 'light'  # Default to light if not specified
        payment_url = create_sub_payment(user_id, tier)
        return f"Ссылка на оплату месячной подписки создана: {payment_url}"
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
    """Async wrapper for delete_task_sync"""
    return delete_task_sync(
        task_id=task_id,
        task_title=task_title,
        reason=reason,
        user_id=user_id,
        session=session,
        confirmed=True  # Auto-confirm for AI agent
    )


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
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            
            details = "📋 Подробная информация о задаче:\n\n"
            details += f"🆔 ID: {task.id}\n"
            details += f"📝 Название: {task.title}\n"
            
            if task.description:
                description = decrypt_data(task.description) if task.description.startswith('gAAAAA') else task.description
                details += f"📄 Описание: {description}\n"
            
            details += f"📊 Статус: {task.status}\n"
            
            if task.reminder_time:
                local_time = task.reminder_time.astimezone(user_tz)
                details += f"⏰ Время напоминания: {local_time.strftime('%d.%m.%Y %H:%M')} ({user_tz.zone})\n"
            
            if task.due_date:
                local_due = task.due_date.astimezone(user_tz)
                details += f"📅 Дедлайн: {local_due.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.delegated_to_username:
                details += f"👤 Делегирована: @{task.delegated_to_username}\n"
                details += f"📋 Статус делегирования: {task.delegation_status or 'Не определён'}\n"
                if task.delegation_details:
                    details += f"📋 Детали делегирования: {task.delegation_details}\n"
            
            if task.completion_notes:
                completion_notes = decrypt_data(task.completion_notes) if task.completion_notes.startswith('gAAAAA') else task.completion_notes
                details += f"✅ Заметки о выполнении: {completion_notes}\n"
            
            if task.actual_completion_time:
                local_completion = task.actual_completion_time.astimezone(user_tz)
                details += f"✅ Фактическое время выполнения: {local_completion.strftime('%d.%m.%Y %H:%M')}\n"
            
            if task.recommendations:
                try:
                    import json
                    recs = json.loads(task.recommendations)
                    if recs:
                        details += "💡 Рекомендации AI:\n"
                        for i, rec in enumerate(recs[:3], 1):
                            details += f"  {i}. {rec}\n"
                except:
                    pass
            
            details += f"🕒 Создана: {task.created_at.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')}\n"
            
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
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
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


# Function removed


async def update_user_memory_async(memory_type: str, content: str, user_id: int = None, session=None, close_session: bool = True) -> str:
    """
    Сохранить информацию в память пользователя.

    Args:
        memory_type: Тип информации (preference, project, contact, interest, etc.)
        content: Что запомнить
        user_id: ID пользователя (опционально)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Сообщение об успешном сохранении
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        if not user_id:
            return "Необходимо указать ID пользователя"

        # Получить пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Получить или создать профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)

        # Нормализуем content - убираем лишние слова
        content_clean = content.lower()
        for phrase in ['хочу заняться', 'хочу научиться', 'интересуюсь', 'люблю', 'увлекаюсь', 'занимаюсь', 'умею', 'владею', 'моя цель', 'хочу достичь']:
            content_clean = content_clean.replace(phrase, '').strip()
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА ДЛЯ ИНТЕРЕСОВ
        if memory_type.lower() in ['interest', 'interests', 'интерес', 'интересы', 'хобби', 'увлечение']:
            new_value, was_added = _add_to_list_field(profile.interests, content_clean)
            if was_added:
                profile.interests = new_value
                session.commit()
                return f"✅ Добавил в интересы: {content_clean}"
            else:
                return f"Интерес '{content_clean}' уже есть в профиле"
        
        # ОБРАБОТКА ДЛЯ НАВЫКОВ
        elif memory_type.lower() in ['skill', 'skills', 'навык', 'навыки']:
            new_value, was_added = _add_to_list_field(profile.skills, content_clean)
            if was_added:
                profile.skills = new_value
                session.commit()
                return f"✅ Добавил в навыки: {content_clean}"
            else:
                return f"Навык '{content_clean}' уже есть в профиле"
        
        # ОБРАБОТКА ДЛЯ ЦЕЛЕЙ
        elif memory_type.lower() in ['goal', 'goals', 'цель', 'цели']:
            new_value, was_added = _add_to_list_field(profile.goals, content_clean)
            if was_added:
                profile.goals = new_value
                session.commit()
                return f"✅ Добавил в цели: {content_clean}"
            else:
                return f"Цель '{content_clean}' уже есть в профиле"

        # Обычное сохранение в память
        current_memory = user.memory or ""

        # Добавить новую информацию с типом
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        new_memory_entry = f"[{timestamp}] {memory_type.upper()}: {content}"

        if current_memory:
            user.memory = current_memory + "\n" + new_memory_entry
        else:
            user.memory = new_memory_entry

        session.commit()

        return f"✅ Запомнил: {memory_type} - {content}"

    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при сохранении памяти пользователя {user_id}: {e}")
        return f"❌ Ошибка при сохранении: {e}"

    finally:
        if close_session:
            session.close()


