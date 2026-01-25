# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timezone, timedelta
import pytz
from models import Session, Task, User, UserProfile, Interaction
from sqlalchemy import or_, and_, func
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

from .memory import encrypt_data, decrypt_data
from .utils import parse_relative_time, parse_natural_time, parse_time_to_datetime, generate_task_recommendations

logger = logging.getLogger(__name__)


def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    """Add a new task"""
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}")
    
    if user_id is None:
        logger.error(f"[ADD_TASK] ERROR: user_id is None! Cannot create task without user_id")
        return "ERROR: user_id is required but was None"
    
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

    # Check if task with same title exists (case insensitive)
    from sqlalchemy import func
    existing_task = session.query(Task).filter(
        Task.user_id == user.id,
        func.lower(Task.title) == func.lower(title)
    ).first()
    if existing_task:
        # Update existing task
        if reminder_time:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                existing_task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        if description:
            existing_task.description = encrypt_data(description)
        existing_task.status = "pending"  # Reset to pending when updating
        session.commit()
        task_id = existing_task.id
        task = existing_task
    else:
        # Create new task - ТРЕБУЕТСЯ время напоминания
        if not reminder_time:
            if close_session:
                session.close()
            logger.info(f"[ADD_TASK] Task '{title}' NOT created - no reminder_time provided")
            return "NEED_TIME: У каждой задачи должно быть время напоминания. Когда напомнить?"
        
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

                # Check if time is relative
                if "через" in reminder_time.lower():
                    current_time = datetime.now(user_tz)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        # parsed_time уже в правильной timezone от parse_relative_time
                        if parsed_time.tzinfo is None:
                            parsed_time = user_tz.localize(parsed_time)
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        logging.info(
                            f"Task {title} relative time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
                    else:
                        logging.warning(f"Could not parse relative time '{reminder_time}' for task {title}")
                        if close_session:
                            session.close()
                        return f"Не удалось распознать время '{reminder_time}'. Попробуйте 'через 5 минут' или 'через 2 часа'"
                else:
                    # Try natural time parsing first
                    current_time = datetime.now(user_tz)
                    parsed_time = parse_natural_time(reminder_time, current_time)
                    if parsed_time:
                        if parsed_time.tzinfo is None:
                            parsed_time = user_tz.localize(parsed_time)
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        logging.info(
                            f"Task {title} natural time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
                    else:
                        # Try simple HH:MM format first
                        simple_time_match = re.match(r'^(\d{1,2}):(\d{2})$', reminder_time.strip())
                        if simple_time_match:
                            h, m = int(simple_time_match.group(1)), int(simple_time_match.group(2))
                            current_time = datetime.now(user_tz)
                            # Create time for today
                            today_time = current_time.replace(hour=h, minute=m, second=0, microsecond=0)
                            # If time has passed, schedule for tomorrow
                            if today_time <= current_time:
                                today_time = today_time + timedelta(days=1)
                            task.reminder_time = today_time.astimezone(pytz.UTC)
                            logging.info(
                                f"Task {title} simple time parsed: '{reminder_time}' -> local: {today_time} -> UTC: {task.reminder_time}")
                        else:
                            # Fallback to absolute time format
                            try:
                                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                                local_dt = user_tz.localize(local_dt)
                                task.reminder_time = local_dt.astimezone(pytz.UTC)
                                logging.info(
                                    f"Task {title} absolute time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
                            except ValueError:
                                logging.warning(f"Could not parse reminder_time '{reminder_time}' for task {title}")
                                # Don't create task without valid time
                                if close_session:
                                    session.close()
                                return f"Неизвестная ошибка: не удалось распознать время '{reminder_time}'"
            except Exception as e:
                logging.warning(f"Error processing reminder_time '{reminder_time}' for task {title}: {e}")
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
            return "Пользователь не найден."

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
        return f"Удалено {task_count} задач."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задач: {str(e)}"


async def complete_task(task_id=None, task_title=None, completion_note=None, user_id=None, session=None):
    """Mark task as completed
    
    Args:
        task_id: ID задачи
        task_title: Название задачи (если нет ID)
        completion_note: Заметка о результате выполнения
        user_id: ID пользователя
        session: Сессия БД
    """
    logger.info(f"[COMPLETE_TASK] Called with task_id={task_id}, completion_note='{completion_note}', user_id={user_id}")
    
    if user_id is None:
        logger.error("[COMPLETE_TASK] user_id is None")
        return "ERROR: user_id не может быть None"
    
    if task_id is None and (task_title is None or task_title.strip() == ""):
        logger.error("[COMPLETE_TASK] Both task_id and task_title are None/empty") 
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
                or_(
                    and_(Task.id == task_id_int, Task.user_id == user.id),
                    and_(Task.id == task_id_int, Task.delegated_to_username.ilike((user.username or '').replace('@', '')), Task.delegation_status == "accepted")
                )
            )
            .first()
        )
    elif task_title:
        # Search by words in title (including delegated tasks) - include completed tasks to check status
        words = task_title.lower().split()
        # Use func.lower() for case-insensitive search
        conditions = [func.lower(Task.title).like(f"%{word}%") for word in words]
        
        # Build query with optional delegated task search - include all statuses
        query_conditions = [and_(Task.user_id == user.id, or_(*conditions))]
        
        if user.username:
            query_conditions.append(
                and_(
                    Task.delegated_to_username.ilike((user.username or '').replace('@', '')),
                    Task.delegation_status == "accepted",
                    or_(*conditions)
                )
            )
        
        task = session.query(Task).filter(or_(*query_conditions)).first()
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        if task.status == "completed":
            if close_session:
                session.close()
            return f"Задача '{task.title}' уже выполнена."
        
        task.status = "completed"
        task.actual_completion_time = datetime.now(timezone.utc)
        
        # Сохраняем заметку о результате выполнения
        if completion_note:
            task.completion_notes = encrypt_data(completion_note)
            logger.info(f"[COMPLETE_TASK] Saved completion note for task {task.id}")
        
        session.commit()

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

        # Если задача была делегирована этому пользователю, отправляем отчет делегировавшему
        if task.delegated_to_username and task.delegation_status == "accepted":
            # Проверяем, является ли текущий пользователь получателем делегированной задачи
            if task.delegated_to_username.replace('@', '').lower() == (user.username or '').replace('@', '').lower():
                # Находим пользователя, который делегировал задачу
                delegator = session.query(User).filter_by(id=task.user_id).first()
                if delegator:
                    # Отправляем сообщение делегировавшему пользователю
                    try:
                        from main import bot
                        from ai_integration.chat import generate_result_check
                        if bot:
                            result_check_text = await generate_result_check(delegator.telegram_id, task.title)
                            report_message = f"👤 @{user.username} выполнил(а) делегированную задачу:\n📋 '{task.title}'\n\n{result_check_text}"
                            await bot.send_message(chat_id=delegator.telegram_id, text=report_message)
                            logging.info(f"Sent completion report to delegator {delegator.username} for task {task.id}")
                    except Exception as e:
                        logging.error(f"Failed to send completion report to delegator: {e}")

                    # Для делегированных задач добавляем дополнительный запрос
                    result += f" Это также поможет @{delegator.username} оценить качество выполненной работы."

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result


async def skip_task(task_id=None, task_title=None, user_id=None, session=None):
    """Mark task as skipped"""
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
    """Restore task to pending status"""
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


async def reschedule_task(task_id=None, new_date=None, user_id=None, session=None):
    """Reschedule task to a new date"""
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
        try:
            # Parse new date
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            if " " in new_date:  # Full datetime
                local_dt = datetime.strptime(new_date, "%Y-%m-%d %H:%M")
            else:  # Date only, keep existing time
                local_dt = datetime.strptime(new_date, "%Y-%m-%d")
                if task.reminder_time:
                    existing_time = task.reminder_time.astimezone(user_tz).time()
                    local_dt = datetime.combine(local_dt.date(), existing_time)
                else:
                    local_dt = local_dt.replace(hour=9, minute=0)  # Default to 9 AM

            local_dt = user_tz.localize(local_dt)
            task.reminder_time = local_dt.astimezone(pytz.UTC)
            session.commit()

            result = f"Задача '{task.title}' перенесена на {local_dt.strftime('%d.%m.%Y %H:%M')}."

            # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
        except ValueError as e:
            result = f"Ошибка формата даты: {e}. Используйте формат YYYY-MM-DD или YYYY-MM-DD HH:MM."
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result


async def get_task_advice(task_id=None, user_id=None, session=None):
    """Get AI advice for a task"""
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
        prompt = f"""Дай полезный совет по выполнению этой задачи:

Задача: {title}
Описание: {description}
Статус: {status}

Дай конкретные, практические рекомендации по:
1. Как лучше подойти к выполнению
2. Возможные сложности и как их избежать
3. Советы по эффективности

Ответ должен быть кратким и полезным."""

        try:
            from ..ai_integration import chat_with_ai
            advice = asyncio.run(chat_with_ai(user_id, prompt, max_tokens=500))
            result = f"Совет по задаче '{title}':\n\n{advice}"

            # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
        except Exception as e:
            logger.error(f"Error getting AI advice: {e}")
            result = f"Не удалось получить совет по задаче '{title}'. Попробуйте позже."
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result


def analyze_task(task_id=None, user_id=None, session=None):
    """Analyze task with AI and provide recommendations"""
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

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike((user.username or "").replace('@', '')))
            )
            .first()
        )
    else:
        if close_session:
            session.close()
        return "Не указан ID задачи."

    if task:
        # Get user profile for context
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Collect task info for analysis
        task_info = f"""
        ЗАДАЧА ДЛЯ АНАЛИЗА:
        Название: {task.title}
        Описание: {task.description or 'Не указано'}
        Статус: {task.status}
        Время напоминания: {task.reminder_time.strftime('%Y-%m-%d %H:%M') if task.reminder_time else 'Не установлено'}
        Делегирована: {'Да' if task.delegated_to_username else 'Нет'}
        """

        # Add profile info
        profile_info = ""
        if profile:
            profile_info = f"""
        ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:
        Навыки: {profile.skills or 'Не указаны'}
        Интересы: {profile.interests or 'Не указаны'}
        Цели: {profile.goals or 'Не указаны'}
        Город: {profile.city or 'Не указан'}
        """

        # AI analysis prompt
        analysis_prompt = f"""{task_info}{profile_info}

        Проанализируй эту задачу и дай полезные рекомендации:
        1. Оцени сложность и реалистичность сроков
        2. Предложи шаги для выполнения
        3. Дай советы по оптимизации
        4. Учитывай навыки и интересы пользователя при рекомендациях

        Будь конкретным и полезным в ответе."""

        try:
            from .chat import chat_with_ai
            # Create event loop for sync call of async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            analysis_result = loop.run_until_complete(chat_with_ai(analysis_prompt, [], user_id))
            loop.close()

            # Save result to interaction history
            interaction = Interaction(
                user_id=user.id,
                message_type="ai",
                content=f"Анализ задачи '{task.title}':\n\n{analysis_result}")
            session.add(interaction)
            session.commit()

            result = f"Анализ задачи '{task.title}':\n\n{analysis_result}"

        except Exception as e:
            logger.error(f"Error analyzing task {task_id}: {e}")
            result = f"Ошибка при анализе задачи '{task.title}': {str(e)}"
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result


def set_reminder(task_id, reminder_time, user_id=None):
    """Set reminder for a task"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        task = session.query(Task).filter_by(id=task_id_int, user_id=user.id).first()
        if task:
            try:
                # Try to parse as absolute time first
                try:
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    reminder_time_parsed = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    # If that fails, try parsing as relative time
                    parsed_time = parse_time_to_datetime(reminder_time, user_id)
                    if parsed_time:
                        # parse_time_to_datetime returns string, parse it
                        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                        local_dt = datetime.strptime(parsed_time, "%Y-%m-%d %H:%M")
                        local_dt = user_tz.localize(local_dt)
                        reminder_time_parsed = local_dt.astimezone(pytz.UTC)
                    else:
                        return f"Не удалось распознать время '{reminder_time}'. Используйте формат 'YYYY-MM-DD HH:MM' или естественный язык ('завтра в 10:00', 'сегодня в 15:30')."
                
                task.reminder_time = reminder_time_parsed
                session.commit()
                
                # Format time for display in user's timezone
                display_time = reminder_time_parsed.astimezone(user_tz).strftime("%Y-%m-%d %H:%M")
                result = f"Установлено напоминание для '{task.title}' на {display_time}."
            except Exception as e:
                logger.error(f"[SET_REMINDER] Error parsing time: {e}")
                result = f"Ошибка при установке напоминания: {str(e)}"
        else:
            result = "Задача не найдена."
        return result
    finally:
        session.close()


def delegate_task(
    title, reminder_time=None, delegated_to_username=None, user_id=None, description="", delegation_details=""
):
    """Create a delegated task that requires acceptance by the recipient"""
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
        # Check if delegator has Bronze tier - Bronze users can only receive delegated tasks
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."
        
        # Log tier for debugging
        logger.info(f"[DELEGATE] User {user_id} tier: {delegator.subscription_tier.value if delegator.subscription_tier else 'None'}")
        
        # Skip subscription check in FREE_ACCESS_MODE
        if not FREE_ACCESS_MODE and delegator.subscription_tier and delegator.subscription_tier.value == 'BRONZE':
            return ("🥉 Делегирование задач доступно только на тарифах **Серебро** и **Золото**. "
                    "На тарифе Бронза вы можете получать делегированные задачи от других пользователей, "
                    "но не можете делегировать свои задачи. Обновите тариф для доступа к делегированию.")
        
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
                    
                    return f"@{recipient_username} не готов принимать задачи от вас. Попробуйте делегировать задачу другому пользователю."
            except (json.JSONDecodeError, Exception) as e:
                logging.error(f"Error checking blocked contacts: {e}")

        # If delegating to self, create regular task
        if recipient.id == delegator.id:
            task = Task(user_id=delegator.id, title=title, description=encrypt_data(description), status="pending")
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

            # Schedule reminder
            if task.reminder_time:
                try:
                    from reminder_service import REMINDER_SERVICE
                    if REMINDER_SERVICE:
                        REMINDER_SERVICE.schedule_reminder(
                            task_id=task.id,
                            reminder_time=task.reminder_time,
                            user_id=delegator.telegram_id,
                            task_title=task.title,
                        )
                except Exception as e:
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
            description=encrypt_data(description),
            delegated_by=None,
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
                notification_text = asyncio.run(generate_delegation_notification(
                    delegator.username,
                    recipient_username,
                    title,
                    description,
                    reminder_time,
                    delegation_details,
                    recipient.telegram_id
                ))

                if notification_text:
                    message = notification_text
                else:
                    # Fallback to template if AI generation fails
                    message = f"Новое предложение задачи от @{delegator.username}:\n\n"
                    message += f"Задача: {title}\n"
                    if description:
                        message += f"Описание: {description}\n"
                    if reminder_time:
                        message += f"Дедлайн: {reminder_time}\n"
                    if delegation_details:
                        message += f"Детали: {delegation_details}\n"
                    message += f"\nНапишите боту 'принять задачу' для подтверждения или 'отклонить задачу' для отказа."

                import asyncio
                asyncio.create_task(bot.send_message(recipient.telegram_id, message))

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

        session.close()
        return f"Предложение задачи отправлено @{recipient_username}. Ожидается подтверждение."
    except Exception as e:
        session.close()
        return f"Ошибка при создании делегированной задачи: {str(e)}"


def suggest_alternatives(task_id, reason="", user_id=None):
    """Suggest alternatives for uncompleted task via AI"""
    import asyncio
    return asyncio.run(_suggest_alternatives_async(task_id, reason, user_id))


async def _suggest_alternatives_async(task_id, reason="", user_id=None):
    """Async implementation of suggest_alternatives"""
    from config import DEEPSEEK_API_KEY
    from .prompts import get_optimized_system_prompt
    from .utils import clean_technical_details
    import aiohttp

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
        if not task:
            return "Задача не найдена."

        # Get user memory
        user_memory = ""
        if user.memory:
            try:
                user_memory = f"\nИнформация о пользователе: {decrypt_data(user.memory)}"
            except Exception as e:
                logger.warning(f"Failed to decrypt user memory: {e}")
                user_memory = ""

        # Generate alternatives via AI
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_optimized_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory},
            {
                "role": "user",
                "content": f"Предложи 3-5 альтернативных подходов к задаче '{task.title}'. Причина невыполнения: '{reason}'. Будь практичным и конкретным.",
            },
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages, "max_tokens": 500}

        async with aiohttp.ClientSession() as aio_session:
            async with aio_session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    return content
                else:
                    return "Не удалось сгенерировать альтернативы."

    except Exception as e:
        return f"Ошибка при генерации альтернатив: {str(e)}"
    finally:
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

        # Update delegation status
        task.delegation_status = "accepted"
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

        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user.username} принял задачу: {task.title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы приняли задачу '{task.title}'. Она добавлена в ваш список задач."
    except Exception as e:
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

        # Notify delegator
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot
                if bot:
                    message = f"@{user.username} отклонил задачу: {task.title}"
                    import asyncio
                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы отклонили задачу '{task.title}'."
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def get_delegation_progress(task_id, user_id=None):
    """Get progress report for a delegated task"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task or not task.delegated_to_username:
            return "Делегированная задача не найдена."

        if task.delegation_status == "pending":
            status_msg = f"@{task.delegated_to_username} еще не ответил на предложение."
        elif task.delegation_status == "accepted":
            if task.status == "completed":
                status_msg = f"Задача выполнена @{task.delegated_to_username}!"
            else:
                status_msg = (
                    f"@{task.delegated_to_username} принял задачу и работает над ней (статус: {task.status})."
                )
        elif task.delegation_status == "rejected":
            status_msg = f"@{task.delegated_to_username} отклонил эту задачу."
        else:
            status_msg = "Статус неизвестен."

        session.close()
        return f"Задача: {task.title}\n{status_msg}"
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def cancel_delegation(task_id, user_id=None):
    """Cancel delegation of a task, returning it to the initiator"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            session.close()
            return "Ошибка: Пользователь не найден."

        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task:
            session.close()
            return "Задача не найдена."

        if not task.delegated_to_username:
            session.close()
            return "Эта задача не делегирована."

        # Check if task is already completed or in progress
        if task.delegation_status == "accepted" and task.status == "completed":
            session.close()
            return "Нельзя отменить делегирование выполненной задачи."

        # Cancel delegation
        task_title = task.title
        task.delegated_to_username = None
        task.delegation_status = None
        task.delegated_by = None
        task.delegation_details = None

        session.commit()
        session.close()

        return f"Делегирование задачи '{task_title}' отменено. Задача возвращена вам."
    except Exception as e:
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
    """Edit task properties"""
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
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id)).first()
    elif task_title:
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike(f"%{task_title}%")
        ).first()

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
                if "через" in reminder_time.lower() or "на" in reminder_time.lower():
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    current_time = datetime.now(user_tz)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        logger.info(f"Task {task.id} relative time updated: '{reminder_time}' -> {parsed_time} (from current time {current_time})")
                    else:
                        session.close()
                        return "Не удалось распарсить относительное время."
                else:
                    # Parse time as local time in user's timezone, then convert to UTC
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    # Localize to user timezone
                    reminder_time_local = user_tz.localize(reminder_time_parsed)
                    # Convert to UTC for storage
                    task.reminder_time = reminder_time_local.astimezone(pytz.UTC)
                    logger.info(f"Task {task.id} absolute time updated: {reminder_time} (local) -> {task.reminder_time} (UTC)")
            except ValueError:
                if close_session:
                    session.close()
                return "Неверный формат времени. Используйте YYYY-MM-DD HH:MM или 'через X минут'."
        session.commit()
        result = f"TASK_UPDATED: Задача '{task.title}' обновлена."
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result


def get_task_details(task_id, user_id=None):
    """Get task details"""
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."

    task = session.query(Task).filter_by(id=int(task_id)).first()
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
            return "У вас нет прав на просмотр этой задачи."

        session.close()
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    session.close()
    return "Задача не найдена."


def set_priority(task_id, priority, user_id=None):
    """Set task priority - stub function for backward compatibility"""
    # This function is referenced in __init__.py but not actually used
    return "Функция set_priority временно недоступна"


def brainstorm_ideas(topic, num_ideas=5, user_id=None):
    """Generate ideas for a problem or improvement"""
    import requests
    from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

    prompt = f"""
    Сгенерируй {num_ideas} креативных идей для темы: "{topic}"

    Идеи должны быть:
    - Конкретными и реализуемыми
    - Разнообразными
    - Учитывать практические аспекты

    Формат ответа: пронумерованный список идей, каждая с кратким описанием почему она хороша.
    """

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
            "temperature": 0.7
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        ideas = result["choices"][0]["message"]["content"].strip()
        return f"Идеи для темы '{topic}':\n\n{ideas}"
    except Exception as e:
        return f"Ошибка генерации идей: {str(e)}"


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
        delegated_by_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and user_username_lower and t.delegated_to_username.lower() != user_username_lower
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

        return result.strip()
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return "Ошибка получения списка задач"
    finally:
        if close_session:
            session.close()


def enrich_task_list_with_insights(task_list_text, user_id):
    """Enrich task list with valuable insights and analysis"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return task_list_text

        # Get tasks for analysis
        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status != "completed"
        ).all()

        # Analyze patterns
        insights = []

        # 1. Analyze workload
        task_count = len(tasks)
        if task_count == 0:
            insights.append(
                "Отличная работа - все задачи выполнены! Раньше ты мог часами вспоминать, что нужно сделать, теперь все под контролем.")
        elif task_count == 1:
            insights.append(
                "Одна задача - идеально для фокуса. Раньше ты мог теряться в длинных списках, теперь приоритет ясен.")
        elif task_count > 5:
            insights.append(
                f"{task_count} задач - стоит приоритизировать. Я помогу организовать, чтобы не терять время на хаос.")

        # 2. Analyze overdue tasks
        overdue_count = 0
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)

        for task in tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_count += 1
                except Exception as e:
                    logger.warning(f"Failed to process reminder time for task {task.id}: {e}")
                    pass

        if overdue_count > 0:
            insights.append(
                f"{overdue_count} просроченных задач. Раньше это могло вызвать стресс и потерю времени - теперь давай исправим ситуацию.")

        # 3. Analyze delegation
        delegated_count = sum(1 for t in tasks if t.delegated_to_username)
        if delegated_count > 0:
            insights.append(
                f"Ты делегируешь {delegated_count} задач - умный подход! Раньше все приходилось делать самому, теперь команда помогает.")

        # 4. Optimization suggestions
        tasks_without_time = sum(1 for t in tasks if not t.reminder_time)
        if tasks_without_time > 0:
            insights.append(
                f"{tasks_without_time} задач без времени - добавим сроки, чтобы избежать спешки в последний момент.")

        # Format final response
        result = task_list_text
        if insights:
            result += "\n\nАнализ ситуации: " + ", ".join(insights[:3])
            result += "\n\nЧто приоритизируем? Или может найдем партнеров для совместной работы над похожими задачами?"

        # Add social suggestions based on profile
        user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if user_profile and (user_profile.interests or user_profile.skills):
            social_suggestions = []

            if user_profile.interests:
                interests_list = [i.strip() for i in user_profile.interests.split(',')]
                if any(i.lower() in ['бег', 'спорт', 'фитнес', 'йога'] for i in interests_list):
                    social_suggestions.append("Вижу интерес к спорту - могу найти партнеров для совместных тренировок")
                if any(i.lower() in ['программирование', 'it', 'разработка'] for i in interests_list):
                    social_suggestions.append(
                        "Занимаешься IT - найдем коллег для обмена опытом или совместных проектов")
                if any(i.lower() in ['путешествия', 'кино', 'театр', 'музыка'] for i in interests_list):
                    social_suggestions.append(
                        "Любишь культурные мероприятия - подберу компанию для походов в кино или театр")

            if social_suggestions:
                result += "\n\nСоциальные возможности: " + ", ".join(social_suggestions[:2])
                result += "\n\nХочешь найти единомышленников прямо сейчас?"

        return result

    except Exception as e:
        logger.error(f"Error enriching task list: {e}")
        return task_list_text
    finally:
        session.close()


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
    
    # Bronze and Silver tier users cannot see Gold tier users
    if user.subscription_tier and user.subscription_tier.value in ['BRONZE', 'SILVER']:
        from models import SubscriptionTier
        profile_query = profile_query.filter(User.subscription_tier != SubscriptionTier.GOLD)
    
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
            
            # Точное совпадение интересов
            if user_interests & profile_interests:
                has_match = True
                match_reasons.append(f"interests exact: {user_interests & profile_interests}")
            else:
                # Проверка вхождения одного интереса в другой (например "спорт" в "пляжный спорт")
                for user_interest in user_interests:
                    user_clean = user_interest.strip()
                    # Пропускаем слишком короткие слова
                    if len(user_clean) < 4:
                        continue
                    for profile_interest in profile_interests:
                        profile_clean = profile_interest.strip()
                        # Проверяем вхождение как целого слова
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
        is_favorite = False
        is_blocked = False
        
        if user_profile.favorite_contacts:
            favorite_usernames = [u.strip().lower().replace('@', '') for u in user_profile.favorite_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in favorite_usernames:
                is_favorite = True
                has_match = True  # Принудительно показываем избранных
                match_reasons.append("favorite contact")
                
        if user_profile.blocked_contacts:
            blocked_usernames = [u.strip().lower().replace('@', '') for u in user_profile.blocked_contacts.split(',')]
            if profile_user.username and profile_user.username.replace('@', '').lower() in blocked_usernames:
                is_blocked = True
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
    for task in user_tasks:
        if task.title:
            # Простая токенизация: разбиваем на слова, убираем короткие
            words = [w.lower().strip() for w in task.title.split() if len(w) > 3]
            user_task_keywords.update(words)
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
            if partner.interests and not partner.task_relevance:
                partner_interest_words = set()
                for interest in partner.interests.split(','):
                    interest_words = [w.lower().strip() for w in interest.split() if len(w) > 3]
                    partner_interest_words.update(interest_words)
                
                task_interest_match = user_task_keywords & partner_interest_words
                if task_interest_match:
                    partner.task_relevance = f"интересы для задач: {', '.join(list(task_interest_match)[:3])}"
                    partner.task_relevance_score += len(task_interest_match) * 2
            
            # Проверяем совпадение задач партнера с задачами пользователя
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

    # Get partners list
    partners = get_partners_list(user.id, session)

    if not partners:
        if close_session:
            session.close()
        return "По твоему профилю пока не нашлось подходящих людей. Заполни профиль (интересы, навыки, город), и я найду единомышленников!"

    # Format response
    response = "Нашёл подходящих людей:\n"
    for idx, p in enumerate(partners[:3], 1):
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

        # Get username
        partner_user = session.query(User).filter_by(id=p.user_id).first()
        if partner_user and partner_user.username:
            response += f"{idx}. @{partner_user.username}\n   {info_str}\n"

    if close_session:
        session.close()

    return response


def update_profile(
    skills=None,
    interests=None,
    goals=None,
    city=None,
    current_plans=None,
    timezone=None,
    company=None,
    position=None,
    bio=None,
    languages=None,
    user_id=None,
    session=None,
):
    """Update user profile"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[UPDATE_PROFILE] Called with: skills={skills}, interests={interests}, goals={goals}, city={city}, user_id={user_id}")
    
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

    updates_made = []

    def update_list_field(field, value, field_name):
        logger.info(f"[UPDATE_LIST_FIELD] field_name={field_name}, field='{field}', value='{value}', type(value)={type(value)}")
        if value is None:
            logger.info(f"[UPDATE_LIST_FIELD] value is None, returning field unchanged")
            return field, None, False
        if value == "":
            logger.info(f"[UPDATE_LIST_FIELD] value is empty string, clearing {field_name}")
            return None, f"cleared_{field_name}", False

        current = set((field or "").split(", ")) - {""}
        logger.info(f"[UPDATE_LIST_FIELD] current before update: {current}")
        action = None

        if value.startswith("только "):
            current = set()
            value = value[7:].strip()
            new_items_list = [item.strip() for item in value.split(",") if item.strip()]
            for item in new_items_list:
                current.add(item)
            if new_items_list:
                action = f"cleared_and_added_{field_name}:{', '.join(new_items_list)}"
        elif value.startswith("+"):
            new_item = value[1:].strip()
            if new_item:
                current.add(new_item)
                action = f"added_{field_name}:{new_item}"
        elif value.startswith("-"):
            remove_item = value[1:].strip()
            if remove_item in current:
                current.discard(remove_item)
                action = f"removed_{field_name}:{remove_item}"
        else:
            new_items_list = [item.strip() for item in value.split(",") if item.strip()]
            logger.info(f"[UPDATE_LIST_FIELD] new_items_list: {new_items_list}")
            for item in new_items_list:
                if item not in current:
                    current.add(item)
            if new_items_list:
                action = f"added_{field_name}:{', '.join(new_items_list)}"

        result = ", ".join(sorted(current)) if current else None
        logger.info(f"[UPDATE_LIST_FIELD] result: '{result}', action: {action}")
        return result, action, False

    if skills is not None:
        new_value, action, _ = update_list_field(profile.skills, skills, "skills")
        profile.skills = new_value
        if action:
            updates_made.append(action)

    if interests is not None:
        old_interests = profile.interests
        new_value, action, _ = update_list_field(profile.interests, interests, "interests")
        profile.interests = new_value
        logger.info(f"[UPDATE_PROFILE] Interests updated: old='{old_interests}', new='{new_value}', action={action}, input={interests}")
        if action:
            updates_made.append(action)

    if goals is not None:
        new_value, action, _ = update_list_field(profile.goals, goals, "goals")
        profile.goals = new_value
        if action:
            updates_made.append(action)

    if city is not None:
        old_city = profile.city
        # Не сохраняем пустые строки и строки из пробелов
        if city and city.strip():
            profile.city = city.strip()
        else:
            profile.city = None
        logger.info(f"[UPDATE_PROFILE] City updated: old='{old_city}', new='{profile.city}', input='{city}'")
        updates_made.append(f"changed_city:{old_city}->{profile.city if profile.city else 'cleared'}")

    if current_plans:
        profile.current_plans = current_plans
        updates_made.append("updated_plans")

    if hasattr(profile, "company") and company is not None:
        old_company = profile.company
        profile.company = company.strip() if company and company.strip() else None
        updates_made.append(f"changed_company:{old_company}->{profile.company if profile.company else 'cleared'}")

    if hasattr(profile, "position") and position is not None:
        old_position = profile.position
        profile.position = position.strip() if position and position.strip() else None
        updates_made.append(f"changed_position:{old_position}->{profile.position if profile.position else 'cleared'}")

    if hasattr(profile, "bio") and bio is not None:
        old_bio = profile.bio
        profile.bio = bio.strip() if bio and bio.strip() else None
        updates_made.append(f"changed_bio:{old_bio}->{profile.bio if profile.bio else 'cleared'}")

    if hasattr(profile, "languages") and languages is not None:
        old_languages = profile.languages
        profile.languages = languages.strip() if languages and languages.strip() else None
        updates_made.append(f"changed_languages:{old_languages}->{profile.languages if profile.languages else 'cleared'}")

    if timezone:
        user.timezone = timezone
        updates_made.append(f"updated_timezone:{timezone}")

    session.commit()

    if close_session:
        session.close()

    if updates_made:
        return f"Профиль обновлен: {'; '.join(updates_made)}"
    else:
        return "Профиль не изменен."


async def generate_delegation_notification(delegator_username, recipient_username, task_title, task_description, deadline, delegation_details, user_id):
    """Generate personalized delegation notification using AI"""
    import aiohttp
    from config import DEEPSEEK_API_KEY
    from .prompts import get_optimized_system_prompt
    from .utils import clean_technical_details

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_optimized_system_prompt()

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
    """Generate AI-powered progress request for delegated task"""
    import aiohttp
    from config import DEEPSEEK_API_KEY
    from .prompts import get_optimized_system_prompt
    from .utils import clean_technical_details

    try:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_optimized_system_prompt()

        prompt = f"""Создай запрос о прогрессе выполнения делегированной задачи.

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
                # Calculate days overdue
                days_overdue = (current_time - task.reminder_time).days

                # Get delegator and recipient info
                delegator = session.query(User).filter_by(id=task.user_id).first()
                recipient = session.query(User).filter(User.username.ilike(task.delegated_to_username)).first()

                if delegator and recipient:
                    # Generate AI-powered reminder
                    import asyncio
                    reminder_text = asyncio.run(generate_progress_reminder(
                        task.title,
                        delegator.username,
                        days_overdue,
                        recipient.telegram_id
                    ))

                    if reminder_text:
                        # Send reminder to recipient
                        from main import bot
                        if bot:
                            try:
                                asyncio.run(bot.send_message(
                                    recipient.telegram_id,
                                    f"🔔 Напоминание о делегированной задаче:\n\n{reminder_text}\n\nЗадача: {task.title}"
                                ))
                                logger.info(f"Sent overdue reminder for task {task.id} to @{recipient.username}")
                            except Exception as e:
                                logger.error(f"Failed to send reminder to recipient: {e}")

                        # Notify delegator about overdue task
                        try:
                            asyncio.run(bot.send_message(
                                delegator.telegram_id,
                                f"⚠️ Делегированная задача просрочена!\n\n"
                                f"Задача: {task.title}\n"
                                f"Исполнитель: @{recipient.username}\n"
                                f"Просрочена на: {days_overdue} дней\n\n"
                                f"Рекомендую связаться с исполнителем для уточнения статуса."
                            ))
                            logger.info(f"Notified delegator {delegator.username} about overdue task {task.id}")
                        except Exception as e:
                            logger.error(f"Failed to notify delegator: {e}")

            except Exception as e:
                logger.error(f"Error processing overdue task {task.id}: {e}")

        session.close()
    except Exception as e:
        logger.error(f"Error in check_delegation_deadlines: {e}")
        session.close()


async def delete_task(task_id=None, task_title=None, user_id=None, session=None, confirmed=False, deletion_reason=None):
    """Delete a task by ID or title. Requires confirmation unless confirmed=True.
    
    Args:
        confirmed: If True, skip confirmation and delete immediately (for API calls with user confirmation)
        deletion_reason: Optional reason for deletion to save for AI analysis
    """
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
        # КРИТИЧЕСКИ ВАЖНО: AI АГЕНТ ДОЛЖЕН СПРОСИТЬ ПОДТВЕРЖДЕНИЕ
        # Если confirmed=False - возвращаем специальный код для AI
        if not confirmed:
            task_info = f"{task.title} (время: {task.reminder_time.strftime('%d.%m %H:%M') if task.reminder_time else 'не указано'})"
            result = f"CONFIRMATION_REQUIRED: {task_info}"
            if close_session:
                session.close()
            return result
        
        # Confirmed - сохраняем причину удаления, затем удаляем
        task_title = task.title
        
        # Сохраняем причину удаления в историю (для будущего анализа AI)
        if deletion_reason:
            task.skipped_reason = deletion_reason
            task.status = "deleted"
            session.commit()
        
        # Отменяем все запланированные джобы для этой задачи
        try:
            from reminder_service import REMINDER_SERVICE
            if REMINDER_SERVICE and REMINDER_SERVICE.scheduler:
                # Отменяем напоминание
                reminder_job_id = f"reminder_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(reminder_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(reminder_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled reminder job for task {task.id}")
                
                # Отменяем проверку результата
                result_check_job_id = f"result_check_{task.id}"
                if REMINDER_SERVICE.scheduler.get_job(result_check_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(result_check_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled result check job for task {task.id}")
                
                # Отменяем чекпоинты задач
                for checkpoint_type in ["overdue_1_3", "overdue_2_3", "overdue_3_3", "pre_deadline"]:
                    checkpoint_job_id = f"task_overdue_{task.id}_{checkpoint_type}_{user.telegram_id}"
                    if REMINDER_SERVICE.scheduler.get_job(checkpoint_job_id):
                        REMINDER_SERVICE.scheduler.remove_job(checkpoint_job_id)
                        logger.info(f"[DELETE_TASK] Cancelled checkpoint job {checkpoint_type} for task {task.id}")
                
                # Отменяем чекпоинт 1/3
                checkpoint_1_3_job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                if REMINDER_SERVICE.scheduler.get_job(checkpoint_1_3_job_id):
                    REMINDER_SERVICE.scheduler.remove_job(checkpoint_1_3_job_id)
                    logger.info(f"[DELETE_TASK] Cancelled 1/3 checkpoint job for task {task.id}")
        except Exception as e:
            logger.warning(f"[DELETE_TASK] Could not cancel scheduled jobs for task {task.id}: {e}")

        session.delete(task)
        session.commit()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.total_tasks_created = (profile.total_tasks_created or 0) - 1  # Decrement created tasks when deleting
            session.commit()

        # Возвращаем ответ с флагом для AI
        if task.status == "completed":
            result = ""  # Не отправлять сообщение для выполненных задач при удалении
        elif not deletion_reason:
            result = f"TASK_DELETED_ASK_REASON: Задача '{task_title}' удалена."
        else:
            result = f"Задача '{task_title}' удалена. Понял, что причина: {deletion_reason}."

        # НЕ сохраняем в БД здесь - это сделает chat_with_ai с финальным AI-ответом
    else:
        result = "Задача не найдена."

    if close_session:
        session.close()
    return result




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
        if should_close and 'session' in locals():
            session.close()
        return f"Ошибка при обновлении памяти: {str(e)}"


def create_subscription_payment(user_id=None):
    """Create subscription payment"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id)
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
