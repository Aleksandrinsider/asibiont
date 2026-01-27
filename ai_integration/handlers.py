# Task and profile handler functions

import logging
import json
import re
from datetime import datetime, timezone, timedelta
import pytz
from models import Session, Task, User, UserProfile, SubscriptionTier
from sqlalchemy import or_, and_, func

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

            # Check if time is relative
            if isinstance(reminder_time, str) and "через" in reminder_time.lower():
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
                    return f"❌ Не удалось распознать время '{reminder_time}'. Попробуйте: 'через 5 минут', 'через 2 часа', 'завтра в 10:00'"
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
                    if isinstance(reminder_time, str):
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
                        # If reminder_time is already a datetime object, use it directly
                        if isinstance(reminder_time, datetime):
                            task.reminder_time = reminder_time.astimezone(pytz.UTC) if reminder_time.tzinfo else user_tz.localize(reminder_time).astimezone(pytz.UTC)
                            logging.info(f"Task {title} datetime used directly: {reminder_time} -> UTC: {task.reminder_time}")
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
                                return f"❌ Неизвестная ошибка: не удалось распознать время '{reminder_time}'"
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
        logger.info(f"[COMPLETE_TASK] Searching for task with title '{task_title}', words: {words}, user_id: {user.id}")
        
        # Get all user tasks and delegated tasks
        user_tasks = session.query(Task).filter(
            or_(
                Task.user_id == user.id,
                and_(
                    Task.delegated_to_username.ilike((user.username or '').replace('@', '')),
                    Task.delegation_status == "accepted"
                )
            )
        ).all()
        
        # Find task by matching words (case-insensitive) - prefer pending tasks
        task = None
        pending_task = None
        for t in user_tasks:
            task_title_lower = t.title.lower()
            if any(word in task_title_lower for word in words):
                if t.status == "pending":
                    pending_task = t
                    logger.info(f"[COMPLETE_TASK] Found pending matching task: {t.title}")
                    break  # Prefer pending tasks, stop at first match
                elif task is None:  # Keep first completed task as fallback
                    task = t
                    logger.info(f"[COMPLETE_TASK] Found completed matching task: {t.title}")
        
        # Use pending task if found, otherwise use completed task
        if pending_task:
            task = pending_task
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

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
        
        session.commit()
        logger.info(f"[COMPLETE_TASK] Task {task.id} status set to 'completed', committed to database")

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


async def reschedule_task(task_title=None, new_time=None, user_id=None, session=None):
    """Reschedule task to a new time"""
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

    # Find task by title using ILIKE for flexible search
    if task_title:
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike(f"%{task_title}%")
        ).first()
    else:
        if close_session:
            session.close()
        return "Не указано название задачи."

    if task:
        try:
            # Parse new time
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            current_time = datetime.now(user_tz)
            
            # Try parsing relative time first ("через 2 часа", "завтра в 10:00")
            from ai_integration.utils import parse_relative_time
            parsed_relative = parse_relative_time(new_time, current_time)
            
            if parsed_relative:
                # Relative time parsed successfully
                local_dt = parsed_relative
                logger.info(f"[RESCHEDULE_TASK] Parsed relative time '{new_time}' to {local_dt}")
            elif " " in new_time:  # Full datetime
                local_dt = datetime.strptime(new_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
            elif ":" in new_time:  # Time only, assume today
                time_dt = datetime.strptime(new_time, "%H:%M")
                local_dt = current_time.replace(hour=time_dt.hour, minute=time_dt.minute, second=0, microsecond=0)
                if local_dt < current_time:
                    local_dt += timedelta(days=1)  # Next day if time has passed
            else:
                if close_session:
                    session.close()
                return "Некорректный формат времени. Используйте HH:MM, YYYY-MM-DD HH:MM, или относительное время ('через 2 часа', 'завтра в 10:00')."

            task.reminder_time = local_dt.astimezone(pytz.UTC)
            session.commit()

            result = f"Задача '{task.title}' перенесена на {local_dt.strftime('%d.%m.%Y %H:%M')}."

        except ValueError as e:
            result = f"Ошибка формата времени: {e}. Используйте формат HH:MM или YYYY-MM-DD HH:MM."
    else:
        result = f"Задача '{task_title}' не найдена."

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
            import asyncio
            from .chat import chat_with_ai
            advice = asyncio.run(chat_with_ai(user_id, prompt))
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
                    
                    return f"@{recipient_username} не готов принимать задачи от вас. Попробуйте делегировать задачу другому пользователю."
            except (json.JSONDecodeError, Exception) as e:
                logging.error(f"Error checking blocked contacts: {e}")

        # If delegating to self, return error marker
        if recipient.id == delegator.id:
            return "SELF_DELEGATION_ERROR: Нельзя делегировать задачу самому себе"

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

        session.close()
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


def get_delegation_progress_for_task(task_id, user_id=None):
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
                if isinstance(reminder_time, str) and ("через" in reminder_time.lower() or "на" in reminder_time.lower()):
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
    
    # Light and Standard tier users cannot see Premium tier users
    if user.subscription_tier and user.subscription_tier.value in ['LIGHT', 'STANDARD']:
        from models import SubscriptionTier
        profile_query = profile_query.filter(User.subscription_tier != SubscriptionTier.PREMIUM)
    
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


async def generate_delegation_notification_async(delegator_username, recipient_username, task_title, task_description, deadline, delegation_details, recipient_telegram_id):
    """Async wrapper for delegation notification generation and sending"""
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
            message += f"\nНапишите боту 'принять задачу' для подтверждения или 'отклонить задачу' для отказа."

        await bot.send_message(recipient_telegram_id, message)

    except Exception as e:
        logging.error(f"Failed to send delegation notification: {e}")


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


async def generate_delegation_response_notification_async(task_title, response, delegator_telegram_id, delegatee_username):
    """Send notification to delegator about task acceptance/rejection"""
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
                #     #             f"⚠️ Делегированная задача просрочена!\n\n"
                #     #             f"Задача: {task.title}\n"
                #     #             f"Исполнитель: @{recipient.username}\n"
                #     #             f"Просрочена на: {days_overdue} дней\n\n"
                #     #             f"Рекомендую связаться с исполнителем для уточнения статуса."
                #     #         ))
                #     #     logger.info(f"Notified delegator {delegator.username} about overdue task {task.id}")
                #     # except Exception as e:
                #     #     logger.error(f"Failed to notify delegator: {e}")

                # End of task processing
                pass

            except Exception as e:
                logger.error(f"Error processing overdue task {task.id}: {e}")

        session.close()
    except Exception as e:
        logger.error(f"Error in check_delegation_deadlines: {e}")
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
        if should_close and 'session' in locals():
            session.close()
def delete_task_sync(task_id=None, task_title=None, reason=None, user_id=None, session=None, confirmed=False):
    """Delete a task by ID or title"""
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
        # Search by words in title (including delegated tasks)
        words = task_title.lower().split()
        conditions = [func.lower(Task.title).like(f"%{word}%") for word in words]
        
        # Build query with optional delegated task search
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

        # Save reason and mark as deleted
        task.skipped_reason = deletion_reason
        task.status = "deleted"
        session.commit()
        
        # Actually delete the task from database
        task_title = task.title
        session.delete(task)
        session.commit()

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


def brainstorm_ideas(topic=None, num_ideas=5, user_id=None, session=None):
    """Generate creative ideas for a topic using AI"""
    import asyncio
    
    if not topic:
        return "Не указана тема для генерации идей."
    
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

        # Get user profile for context
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        user_context = ""
        if profile:
            skills = profile.skills or ""
            interests = profile.interests or ""
            goals = profile.goals or ""
            user_context = f"Учитывай навыки пользователя: {skills}. Интересы: {interests}. Цели: {goals}."

        # Generate ideas using AI
        prompt = f"""Сгенерируй {num_ideas} креативных идей для темы: "{topic}"

{user_context}

Требования к идеям:
1. Будь конкретным и практичным
2. Учитывай навыки и интересы пользователя
3. Каждая идея должна быть реализуемой
4. Формат: номер + краткое название + описание

Примеры хороших идей:
1. Онлайн-курс по Python - Создать серию видеоуроков на YouTube с практическими заданиями
2. Фитнес-трекер - Разработать мобильное приложение для отслеживания прогресса тренировок"""

        try:
            import asyncio
            from .chat import chat_with_ai
            ideas = asyncio.run(chat_with_ai(user_id, prompt))
            result = f"💡 Идеи для темы '{topic}':\n\n{ideas}"
            
            if close_session:
                session.close()
            return result
            
        except Exception as e:
            logger.error(f"Error generating brainstorm ideas: {e}")
            if close_session:
                session.close()
            return f"Не удалось сгенерировать идеи для темы '{topic}'. Попробуйте позже."

    except Exception:
        if close_session:
            session.close()
def get_task_details(task_id=None, user_id=None, session=None):
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
            
            details = f"📋 Подробная информация о задаче:\n\n"
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
                        details += f"💡 Рекомендации AI:\n"
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
        if close_session and 'session' in locals():
            session.close()
        return f"Ошибка при получении деталей задачи: {str(e)}"

def get_all_delegation_progress(user_id=None, session=None):
    """Get progress status of all delegated tasks for the user"""
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

        # Find all tasks delegated by this user
        delegated_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None)
        ).all()

        if not delegated_tasks:
            if close_session:
                session.close()
            return "DELEGATION_REPORT: У вас нет делегированных задач."

        status_info = "📋 Отчет по делегированным задачам:\n\n"

        for task in delegated_tasks:
            status_info += f"🆔 ID: {task.id}\n"
            status_info += f"📝 Название: {task.title}\n"
            status_info += f"👤 Делегирована: @{task.delegated_to_username}\n"
            status_info += f"📋 Статус: {task.delegation_status or 'Ожидает принятия'}\n"

            if task.delegation_status == "accepted":
                status_info += f"✅ Задача принята исполнителем\n"
            elif task.delegation_status == "rejected":
                status_info += f"❌ Задача отклонена исполнителем\n"
            elif task.delegation_status == "completed":
                status_info += f"✅ Задача выполнена!\n"
                if task.actual_completion_time:
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    local_completion = task.actual_completion_time.astimezone(user_tz)
                    status_info += f"🕒 Время выполнения: {local_completion.strftime('%d.%m.%Y %H:%M')}\n"
            else:
                status_info += f"⏳ Ожидает решения исполнителя\n"

            if task.completion_notes and task.delegation_status == "completed":
                completion_notes = decrypt_data(task.completion_notes) if task.completion_notes.startswith('gAAAAA') else task.completion_notes
                status_info += f"📝 Результат выполнения: {completion_notes}\n"

            status_info += "---\n"

        if close_session:
            session.close()
        return f"DELEGATION_REPORT: {status_info}"

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка при получении статуса делегирования: {str(e)}"



def suggest_alternatives(task_id=None, reason=None, user_id=None, session=None):
    """Suggest alternatives for an uncompleted task: postpone, break down, delegate, find partner"""
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

        if not task_id:
            if close_session:
                session.close()
            return "Не указан ID задачи."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        # Find task
        task = session.query(Task).filter(
            or_(
                and_(Task.id == task_id_int, Task.user_id == user.id),
                and_(Task.id == task_id_int, Task.delegated_to_username.ilike((user.username or '').replace('@', '')), Task.delegation_status == "accepted")
            )
        ).first()

        if not task:
            if close_session:
                session.close()
            return "Задача не найдена."

        # Generate alternatives based on task characteristics
        alternatives = []
        task_title = task.title

        # Alternative 1: Postpone
        if task.reminder_time:
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            current_time = datetime.now(user_tz)
            if task.reminder_time > current_time:
                alternatives.append("⏰ Перенести задачу на более удобное время")
            else:
                alternatives.append("⏰ Перенести задачу на завтра или выходные")

        # Alternative 2: Break down
        if len(task.title.split()) > 3 or (task.description and len(task.description) > 50):
            alternatives.append("🔨 Разбить задачу на более мелкие подзадачи")

        # Alternative 3: Delegate (if user has appropriate subscription)
        user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if user_profile and user_profile.subscription_tier in ['standard', 'premium']:
            alternatives.append("👥 Делегировать задачу другому пользователю")
        else:
            alternatives.append("⭐ Рассмотреть премиум-подписку для возможности делегирования")

        # Alternative 4: Find partner
        alternatives.append("🤝 Найти партнёра для совместного выполнения")

        # Alternative 5: Get AI suggestions
        alternatives.append("💡 Получить идеи и советы от AI")

        # Alternative 6: Set reminders
        if not task.reminder_time:
            alternatives.append("🔔 Настроить напоминания для регулярного выполнения")

        response = f"💡 Альтернативы для невыполненной задачи '{task_title}':\n\n"
        for i, alt in enumerate(alternatives, 1):
            response += f"{i}. {alt}\n"

        if reason:
            response += f"\n📝 Учитывая причину '{reason}', рекомендую начать с наиболее подходящих вариантов."

        if close_session:
            session.close()
        return response

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка при генерации альтернатив: {str(e)}"


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
        user_id=user.id,
        title=title,
        description=encrypt_data(description),
        delegated_to_username=delegated_to_username,
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
    
    session.add(task)
    session.commit()
    
    if close_session:
        session.close()
    
    return f"Задача '{title}' делегирована пользователю @{delegated_username}"


def edit_task_with_session(task_id=None, task_title=None, title=None, description=None, reminder_time=None, user_id=None, session=None):
    """Edit an existing task"""
    logger.info(f"[EDIT_TASK] Called with task_id={task_id}, task_title='{task_title}', user_id={user_id}")
    
    if user_id is None:
        logger.error("[EDIT_TASK] ERROR: user_id is None!")
        return "ERROR: user_id is required"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    # Find task
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден"
    
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=task_id, user_id=user.id).first()
    elif task_title:
        # Find by title (case insensitive partial match)
        from sqlalchemy import func
        task = session.query(Task).filter(
            Task.user_id == user.id,
            func.lower(Task.title).contains(func.lower(task_title))
        ).first()
    
    if not task:
        if close_session:
            session.close()
        return f"Задача не найдена: {task_title or f'ID {task_id}'}"
    
    # Update fields
    updated_fields = []
    if title:
        task.title = title
        updated_fields.append("название")
    
    if description:
        task.description = encrypt_data(description)
        updated_fields.append("описание")
    
    if reminder_time:
        try:
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            # Try to parse reminder_time - add ISO format support
            for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%H:%M"]:
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
                        if user.timezone and fmt != "%Y-%m-%dT%H:%M:%S%z":  # ISO already has timezone
                            local_dt = user_tz.localize(local_dt)
                    
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                    updated_fields.append("время")
                    break
                except ValueError:
                    continue
        except Exception as e:
            logger.warning(f"[EDIT_TASK] Could not parse reminder_time '{reminder_time}': {e}")
    
    session.commit()
    
    if close_session:
        session.close()
    
    if updated_fields:
        return f"Задача '{task.title}' обновлена: {', '.join(updated_fields)}"
    else:
        return f"Задача '{task.title}' не была изменена"


def delete_task_legacy(task_id=None, task_title=None, reason=None, user_id=None, session=None):
    """Delete a task (legacy version)"""
    logger.info(f"[DELETE_TASK] Called with task_id={task_id}, task_title='{task_title}', user_id={user_id}")
    
    if user_id is None:
        logger.error("[DELETE_TASK] ERROR: user_id is None!")
        return "ERROR: user_id is required"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    
    # Find task
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден"
    
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=task_id, user_id=user.id).first()
    elif task_title:
        # Find by title (case insensitive partial match)
        from sqlalchemy import func
        task = session.query(Task).filter(
            Task.user_id == user.id,
            func.lower(Task.title).contains(func.lower(task_title))
        ).first()
    
    if not task:
        if close_session:
            session.close()
        return f"Задача не найдена: {task_title or f'ID {task_id}'}"
    
    task_title_saved = task.title
    session.delete(task)
    session.commit()
    
    if close_session:
        session.close()
    
    response = f"Задача '{task_title_saved}' удалена"
    if reason:
        response += f" ({reason})"
    
    return response


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


async def update_profile(user_id: int, city: str = None, interests: str = None, skills: str = None, goals: str = None, company: str = None, position: str = None, session=None, close_session: bool = True) -> str:
    """
    Обновляет профиль пользователя с новыми данными.

    Args:
        user_id: ID пользователя (telegram_id)
        city: Город пользователя (опционально)
        interests: Интересы пользователя (опционально)
        skills: Навыки пользователя (опционально)
        goals: Цели пользователя (опционально)
        company: Компания пользователя (опционально)
        position: Должность пользователя (опционально)
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
        if city is not None:
            profile.city = city
            updates.append(f"город: {city}")
        if interests is not None:
            profile.interests = interests
            updates.append(f"интересы: {interests}")
        if skills is not None:
            profile.skills = skills
            updates.append(f"навыки: {skills}")
        if goals is not None:
            profile.goals = goals
            updates.append(f"цели: {goals}")
        if company is not None:
            profile.company = company
            updates.append(f"компания: {company}")
        if position is not None:
            profile.position = position
            updates.append(f"должность: {position}")

        # Обновляем время последнего обновления
        profile.updated_at = datetime.utcnow()

        session.commit()

        if updates:
            return f"Профиль успешно обновлен: {', '.join(updates)}"
        else:
            return "Профиль проверен, изменений не требуется"

    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при обновлении профиля пользователя {user_id}: {e}")
        raise

    finally:
        if close_session:
            session.close()


async def suggest_alternatives_async(task_title: str, reason: str, user_id: int = None, session=None, close_session: bool = True) -> str:
    """
    Предложить альтернативы при проблемах с задачей.

    Args:
        task_title: Название проблемной задачи
        reason: Причина проблемы
        user_id: ID пользователя (опционально)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Предложения альтернатив
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Получить профиль пользователя для персонализации
        profile = None
        if user_id:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Сгенерировать предложения на основе причины проблемы
        alternatives = []

        if "время" in reason.lower() or "задержка" in reason.lower():
            alternatives.extend([
                "Разбить задачу на меньшие подзадачи с отдельными дедлайнами",
                "Делегировать часть работы коллегам или фрилансерам",
                "Перенести дедлайн с уведомлением заинтересованных сторон",
                "Найти более эффективный инструмент или метод выполнения"
            ])

        elif "ресурсы" in reason.lower() or "деньги" in reason.lower():
            alternatives.extend([
                "Найти бесплатные или более дешевые альтернативы",
                "Обратиться за спонсорством или грантами",
                "Использовать существующие ресурсы более эффективно",
                "Отложить задачу до лучших времен"
            ])

        elif "навыки" in reason.lower() or "знания" in reason.lower():
            alternatives.extend([
                "Пройти онлайн-курс или обучение по теме",
                "Найти ментора или консультанта",
                "Начать с более простой версии задачи",
                "Объединиться с кем-то, кто имеет нужные навыки"
            ])

        else:
            alternatives.extend([
                "Проанализировать причину более детально",
                "Обратиться за советом к экспертам",
                "Искать похожие кейсы и их решения",
                "Рассмотреть полную отмену задачи, если она не критична"
            ])

        # Добавить персонализацию на основе профиля
        personalized_suggestions = ""
        if profile and profile.interests:
            personalized_suggestions = f"\n\nУчитывая твои интересы ({profile.interests}), рекомендую также рассмотреть:"

            if "технологии" in profile.interests.lower() or "программирование" in profile.interests.lower():
                personalized_suggestions += "\n- Использовать автоматизацию или скрипты для упрощения процесса"
            elif "бизнес" in profile.interests.lower():
                personalized_suggestions += "\n- Провести анализ ROI перед продолжением"
            elif "творчество" in profile.interests.lower():
                personalized_suggestions += "\n- Взять творческий перерыв для свежих идей"

        response = f"Проблема с задачей '{task_title}': {reason}\n\nПредлагаю следующие альтернативы:\n"
        for i, alt in enumerate(alternatives[:5], 1):  # Ограничим до 5 предложений
            response += f"{i}. {alt}\n"

        response += personalized_suggestions

        return response

    except Exception as e:
        logger.error(f"Ошибка при генерации альтернатив для задачи {task_title}: {e}")
        return f"Не удалось сгенерировать альтернативы: {e}"

    finally:
        if close_session:
            session.close()


async def update_user_memory_async(memory_type: str, content: str, user_id: int = None, session=None, close_session: bool = True) -> str:
    """
    Сохранить информацию в память пользователя.

    Args:
        memory_type: Тип информации (preference, project, contact, etc.)
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

        # Обновить память пользователя
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


async def suggest_trends_and_opportunities_async(focus_area: str, num_suggestions: int = 3, user_id: int = None, session=None, close_session: bool = True) -> str:
    """
    Предложить тренды и возможности в определенной области.

    Args:
        focus_area: Область интереса (career, technology, business, etc.)
        num_suggestions: Количество предложений
        user_id: ID пользователя (опционально)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Предложения трендов и возможностей
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Получить профиль пользователя для персонализации
        profile = None
        if user_id:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Генерировать тренды на основе области
        trends = []

        if focus_area.lower() in ["технологии", "technology", "it", "программирование"]:
            trends = [
                "Искусственный интеллект и машинное обучение - особенно в автоматизации рутинных задач",
                "Веб3 и блокчейн технологии - децентрализованные приложения и NFT",
                "Кибербезопасность - растущий спрос на специалистов по защите данных",
                "Облачные технологии и serverless архитектуры",
                "Интернет вещей (IoT) and edge computing",
                "Низко-кодовая/без-кодовая разработка для быстрого прототипирования"
            ]

        elif focus_area.lower() in ["бизнес", "business", "предпринимательство"]:
            trends = [
                "Электронная коммерция и омниканальные продажи",
                "Устойчивый бизнес и ESG (экология, социальная ответственность, управление)",
                "Гиг-экономика и фриланс-платформы",
                "Цифровая трансформация традиционных отраслей",
                "Персонализация продуктов и услуг с помощью данных",
                "Социальное предпринимательство и impact-инвестиции"
            ]

        elif focus_area.lower() in ["карьера", "career", "работа"]:
            trends = [
                "Удаленная работа и гибридные форматы",
                "Непрерывное обучение и переквалификация",
                "Фриланс и портфельная карьера",
                "Софт-скиллы: эмоциональный интеллект, адаптивность, креативность",
                "Международная мобильность и remote-first компании",
                "Этический ИИ и ответственное развитие технологий"
            ]

        elif focus_area.lower() in ["покер", "poker", "игры"]:
            trends = [
                "Онлайн-покер с мобильными приложениями для коротких сессий",
                "ИИ-ассистенты для анализа раздач и стратегий",
                "Живые турниры с элементами развлечений",
                "Образовательный контент: стримы, подкасты, курсы",
                "Благотворительные турниры и социальная ответственность",
                "Кроссоверы с другими играми и развлечениями"
            ]

        else:
            trends = [
                f"Цифровая трансформация в области {focus_area}",
                f"Устойчивость и экологичность в {focus_area}",
                f"Персонализация и клиентский опыт в {focus_area}",
                f"Автоматизация и ИИ в {focus_area}",
                f"Глобализация и международное сотрудничество в {focus_area}"
            ]

        # Ограничить количество предложений
        selected_trends = trends[:num_suggestions]

        response = f"🔥 Тренды и возможности в области '{focus_area}':\n\n"
        for i, trend in enumerate(selected_trends, 1):
            response += f"{i}. {trend}\n"

        # Добавить персонализацию
        if profile and profile.interests:
            response += f"\n💡 Учитывая твои интересы ({profile.interests}), рекомендую обратить внимание на пересечения с этой областью."

        return response

    except Exception as e:
        logger.error(f"Ошибка при генерации трендов для области {focus_area}: {e}")
        return f"Не удалось сгенерировать тренды: {e}"

    finally:
        if close_session:
            session.close()


async def brainstorm_ideas_async(topic: str, context: str = None, user_id: int = None, session=None, close_session: bool = True) -> str:
    """
    Мозговой штурм идей по теме.

    Args:
        topic: Тема для мозгового штурма
        context: Дополнительный контекст
        user_id: ID пользователя (опционально)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Список идей по теме
    """
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        # Получить профиль пользователя для персонализации
        profile = None
        if user_id:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        # Генерировать идеи на основе темы и контекста
        ideas = []

        # Общие идеи для мозгового штурма
        base_ideas = [
            "Комбинировать существующие подходы новыми способами",
            "Посмотреть на проблему с другой стороны или перспективы",
            "Упростить сложное до основных элементов",
            "Добавить элемент неожиданности или креативности",
            "Использовать аналогии из других областей",
            "Обратиться к первоисточникам и фундаментальным принципам",
            "Рассмотреть крайние случаи и сценарии",
            "Привлечь разные точки зрения и мнения"
        ]

        # Специфические идеи в зависимости от темы
        if "бизнес" in topic.lower() or "стартап" in topic.lower():
            ideas.extend([
                "Создать MVP и протестировать на небольшой аудитории",
                "Найти нишевый рынок с меньшей конкуренцией",
                "Партнерство с существующими игроками рынка",
                "Фокус на проблеме, а не на решении",
                "Бутстрэппинг вместо привлечения инвестиций"
            ])

        elif "технологии" in topic.lower() or "продукт" in topic.lower():
            ideas.extend([
                "Открытый исходный код для привлечения контрибьюторов",
                "API-first подход для интеграций",
                "Модульная архитектура для гибкости",
                "Фокус на UX/UI для лучшего пользовательского опыта",
                "Автоматизация рутинных процессов"
            ])

        elif "маркетинг" in topic.lower() or "продвижение" in topic.lower():
            ideas.extend([
                "Контент-маркетинг с ценным и полезным контентом",
                "Сторителлинг и эмоциональная связь с аудиторией",
                "Вовлечение сообщества и пользовательского контента",
                "Персонализация коммуникаций",
                "Омниканальность - интеграция всех каналов"
            ])

        else:
            ideas.extend(base_ideas)

        # Добавить контекст, если он есть
        if context:
            ideas.append(f"Учитывая контекст '{context}': адаптировать идеи под конкретные условия")

        # Ограничить до 8 идей
        selected_ideas = ideas[:8]

        response = f"🧠 Мозговой штурм по теме '{topic}'"
        if context:
            response += f" (контекст: {context})"
        response += ":\n\n"

        for i, idea in enumerate(selected_ideas, 1):
            response += f"{i}. {idea}\n"

        # Добавить персонализацию
        if profile and profile.interests:
            response += f"\n💡 Учитывая твои интересы ({profile.interests}), некоторые идеи могут быть особенно актуальными."

        return response

    except Exception as e:
        logger.error(f"Ошибка при мозговом штурме по теме {topic}: {e}")
        return f"Не удалось сгенерировать идеи: {e}"

    finally:
        if close_session:
            session.close()


async def delete_task(task_id=None, task_title=None, reason=None, user_id=None, session=None, close_session=True) -> str:
    """
    Удалить задачу по ID или названию.

    Args:
        task_id: ID задачи для удаления (опционально)
        task_title: Название задачи для удаления (опционально)
        reason: Причина удаления (опционально)
        user_id: ID пользователя (опционально)
        session: Сессия базы данных (опционально)
        close_session: Закрывать ли сессию после выполнения

    Returns:
        Сообщение об успешном удалении
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

        # Найти задачу по ID или названию
        task = None
        if task_id:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter_by(
                    id=task_id_int,
                    user_id=user.id
                ).first()
            except (ValueError, TypeError):
                return f"Некорректный ID задачи: {task_id}"
        elif task_title:
            # Search by words in title (like complete_task)
            words = task_title.lower().split()
            logger.info(f"[DELETE_TASK] Searching for task with title '{task_title}', words: {words}, user_id: {user.id}")
            
            # Get all user tasks
            user_tasks = session.query(Task).filter(
                Task.user_id == user.id
            ).all()
            
            # Find task by matching words (case-insensitive) - prefer pending tasks
            for t in user_tasks:
                task_title_lower = t.title.lower()
                if any(word in task_title_lower for word in words):
                    if t.status == 'pending':
                        task = t
                        break
                    elif not task:
                        task = t
        else:
            return "Необходимо указать ID или название задачи"

        if not task:
            identifier = task_id or task_title
            return f"Задача '{identifier}' не найдена"

        # Удалить задачу
        session.delete(task)
        session.commit()

        response = f"✅ Задача '{task.title}' удалена"
        if reason:
            response += f" (причина: {reason})"

        return response

    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при удалении задачи {task_id or task_title}: {e}")
        return f"❌ Ошибка при удалении задачи: {e}"

    finally:
        if close_session:
            session.close()


async def get_task_details_async(task_title: str, user_id: int = None, session=None, close_session: bool = True) -> str:
    """Показать детали конкретной задачи"""
    logger.info(f"[GET_TASK_DETAILS] Called with task_title='{task_title}', user_id={user_id}")
    
    if user_id is None:
        logger.error("[GET_TASK_DETAILS] user_id is None")
        return "❌ Ошибка: user_id не может быть None"
    
    if not task_title or task_title.strip() == "":
        logger.error("[GET_TASK_DETAILS] task_title is empty")
        return "❌ Ошибка: Не указано название задачи"
    
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "❌ Пользователь не найден."

        # Search by words in title (including delegated tasks)
        words = task_title.lower().split()
        logger.info(f"[GET_TASK_DETAILS] Searching for task with title '{task_title}', words: {words}, user_id: {user.id}")
        
        # Get all user tasks and delegated tasks
        user_tasks = session.query(Task).filter(
            or_(
                Task.user_id == user.id,
                and_(
                    Task.delegated_to_username.ilike((user.username or '').replace('@', '')),
                    Task.delegation_status == "accepted"
                )
            )
        ).all()
        
        # Find task by matching words (case-insensitive)
        task = None
        for t in user_tasks:
            task_title_lower = t.title.lower()
            if any(word in task_title_lower for word in words):
                task = t
                logger.info(f"[GET_TASK_DETAILS] Found matching task: {t.title}")
                break
        
        if not task:
            return f"❌ Задача с названием '{task_title}' не найдена."

        # Format task details
        title = task.title
        description = decrypt_data(task.description) if task.description else "Нет описания"
        status = task.status
        created_at = task.created_at.strftime("%d.%m.%Y %H:%M") if task.created_at else "Неизвестно"
        due_date = task.due_date.strftime("%d.%m.%Y %H:%M") if task.due_date else "Не установлена"
        reminder_time = task.reminder_time.strftime("%d.%m.%Y %H:%M") if task.reminder_time else "Не установлено"

        # Delegation info
        delegation_info = ""
        if task.delegated_to_username:
            delegation_status = task.delegation_status or "pending"
            delegation_info = f"\nДелегирована: @{task.delegated_to_username} (статус: {delegation_status})"

        # Completion info
        completion_info = ""
        if task.actual_completion_time:
            completion_note = decrypt_data(task.completion_notes) if task.completion_notes else ""
            completion_info = f"\nВыполнена: {task.actual_completion_time.strftime('%d.%m.%Y %H:%M')}"
            if completion_note:
                completion_info += f"\nЗаметка: {completion_note}"

        details = f"""📋 Детали задачи:

Название: {title}
Описание: {description}
Статус: {status}
Создана: {created_at}
Срок выполнения: {due_date}
Напоминание: {reminder_time}{delegation_info}{completion_info}"""

        return details

    except Exception as e:
        logger.error(f"[GET_TASK_DETAILS] Error: {e}")
        return f"❌ Ошибка при получении деталей задачи: {e}"

    finally:
        if close_session:
            session.close()


def delegate_task(title, description="", reminder_time=None, delegated_to_username=None, delegation_details="", user_id=None):
    """Делегировать задачу другому пользователю"""
    logger.info(f"[DELEGATE_TASK] Called with title='{title}', delegated_to='{delegated_to_username}', user_id={user_id}")

    if not user_id:
        return "❌ Ошибка: пользователь не найден"

    if not delegated_to_username:
        return "❌ Ошибка: укажите username получателя"

    if not title:
        return "❌ Ошибка: укажите название задачи"

    session = Session()
    try:
        # Найти пользователя-отправителя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "❌ Ошибка: пользователь не найден"

        logger.info(f"[DELEGATE_TASK] Delegator found: {user.username} (ID: {user.telegram_id})")

        # Проверить, существует ли уже такая задача
        existing_task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title == title,
            Task.status == 'pending'
        ).first()

        if existing_task:
            # Обновить существующую задачу
            existing_task.delegated_by = user.id
            existing_task.delegated_to_username = delegated_to_username
            existing_task.delegation_status = 'pending'
            if delegation_details:
                existing_task.delegation_details = encrypt_data(delegation_details)
            if description:
                existing_task.description = encrypt_data(description)
            if reminder_time:
                try:
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    existing_task.reminder_time = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    pass
            session.commit()
            task = existing_task
        else:
            # Создать новую задачу
            if not reminder_time:
                return "❌ Ошибка: укажите время выполнения задачи"

            task = Task(
                user_id=user.id,
                title=title,
                description=encrypt_data(description),
                delegated_by=user.id,
                delegated_to_username=delegated_to_username,
                delegation_status='pending',
                delegation_details=encrypt_data(delegation_details) if delegation_details else None
            )

            if reminder_time:
                try:
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    return f"❌ Ошибка: неверный формат времени '{reminder_time}'. Используйте формат YYYY-MM-DD HH:MM"

            session.add(task)
            session.commit()

        # Отправить уведомление получателю (асинхронно)
        try:
            # Найти telegram_id получателя
            recipient = session.query(User).filter_by(username=delegated_to_username).first()
            if recipient:
                from .handlers import generate_delegation_notification_async
                import asyncio
                asyncio.create_task(generate_delegation_notification_async(
                    delegator_username=user.username or f"ID:{user.telegram_id}",
                    recipient_username=delegated_to_username,
                    task_title=title,
                    task_description=description,
                    deadline=reminder_time,
                    delegation_details=delegation_details,
                    recipient_telegram_id=recipient.telegram_id
                ))
        except Exception as e:
            logger.warning(f"[DELEGATE_TASK] Could not send notification: {e}")

        return f"✅ Задача '{title}' успешно делегирована пользователю @{delegated_to_username}. Ожидается подтверждение от получателя."

    except Exception as e:
        logger.error(f"[DELEGATE_TASK] Error: {e}")
        session.rollback()
        return f"❌ Ошибка при делегировании задачи: {e}"
    finally:
        session.close()


def get_delegation_progress(user_id=None, session=None):
    """Показать статус делегированных задач"""
    logger.info(f"[GET_DELEGATION_PROGRESS] Called for user_id={user_id}")

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        if not user_id:
            return "❌ Ошибка: пользователь не найден"

        # Найти делегированные задачи (отправленные)
        delegated_tasks = session.query(Task).filter(
            Task.delegated_by == user_id,
            Task.delegation_status.isnot(None)
        ).all()

        # Найти полученные делегированные задачи
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user and user.username:
            received_tasks = session.query(Task).filter(
                Task.delegated_to_username == user.username,
                Task.delegation_status.isnot(None)
            ).all()
        else:
            received_tasks = []

        if not delegated_tasks and not received_tasks:
            return "У вас нет делегированных задач."

        result = "📋 Статус делегирования задач:\n\n"

        if delegated_tasks:
            result += "📤 Отправленные задачи:\n"
            for task in delegated_tasks:
                status_emoji = {
                    'pending': '⏳',
                    'accepted': '✅',
                    'rejected': '❌',
                    'completed': '🎉'
                }.get(task.delegation_status, '❓')

                result += f"{status_emoji} '{task.title}' → @{task.delegated_to_username}\n"

        if received_tasks:
            result += "\n📥 Полученные задачи:\n"
            for task in received_tasks:
                status_emoji = {
                    'pending': '⏳',
                    'accepted': '✅',
                    'rejected': '❌',
                    'completed': '🎉'
                }.get(task.delegation_status, '❓')

                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                delegator_name = delegator.username or f"ID:{delegator.telegram_id}" if delegator else "Неизвестен"

                result += f"{status_emoji} '{task.title}' от @{delegator_name}\n"

        return result

    except Exception as e:
        logger.error(f"[GET_DELEGATION_PROGRESS] Error: {e}")
        return f"❌ Ошибка при получении статуса делегирования: {e}"
    finally:
        if close_session:
            session.close()


def accept_delegated_task(task_title, user_id=None):
    """Принять делегированную задачу"""
    logger.info(f"[ACCEPT_DELEGATED_TASK] Called with task_title='{task_title}', user_id={user_id}")

    if not user_id:
        return "❌ Ошибка: пользователь не найден"

    if not task_title:
        return "❌ Ошибка: укажите название задачи"

    session = Session()
    try:
        # Найти пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "❌ Ошибка: пользователь не найден"

        # Найти задачу, делегированную этому пользователю
        task = session.query(Task).filter(
            Task.delegated_to_username == user.username,
            Task.delegation_status == 'pending'
        ).filter(
            func.lower(Task.title).like(f'%{task_title.lower()}%')
        ).first()

        if not task:
            return f"❌ Задача '{task_title}' не найдена среди делегированных вам задач."

        # Принять задачу
        task.delegation_status = 'accepted'
        task.user_id = user.id  # Передать задачу пользователю
        task.delegated_to_username = None  # Очистить поле делегирования
        task.delegated_by = None
        session.commit()

        # Отправить уведомление отправителю
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator:
                from .handlers import generate_delegation_response_notification_async
                import asyncio
                asyncio.create_task(generate_delegation_response_notification_async(
                    task_title=task.title,
                    response="accepted",
                    delegator_telegram_id=delegator.telegram_id,
                    delegatee_username=user.username or f"ID:{user.telegram_id}"
                ))
        except Exception as e:
            logger.warning(f"[ACCEPT_DELEGATED_TASK] Could not send notification: {e}")

        return f"✅ Задача '{task.title}' принята! Она добавлена в ваш список задач."

    except Exception as e:
        logger.error(f"[ACCEPT_DELEGATED_TASK] Error: {e}")
        session.rollback()
        return f"❌ Ошибка при принятии задачи: {e}"
    finally:
        session.close()


def reject_delegated_task(task_title, reason="", user_id=None):
    """Отклонить делегированную задачу"""
    logger.info(f"[REJECT_DELEGATED_TASK] Called with task_title='{task_title}', user_id={user_id}")

    if not user_id:
        return "❌ Ошибка: пользователь не найден"

    if not task_title:
        return "❌ Ошибка: укажите название задачи"

    session = Session()
    try:
        # Найти пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "❌ Ошибка: пользователь не найден"

        # Найти задачу, делегированную этому пользователю
        task = session.query(Task).filter(
            Task.delegated_to_username == user.username,
            Task.delegation_status == 'pending'
        ).filter(
            func.lower(Task.title).like(f'%{task_title.lower()}%')
        ).first()

        if not task:
            return f"❌ Задача '{task_title}' не найдена среди делегированных вам задач."

        # Отклонить задачу
        task.delegation_status = 'rejected'
        if reason:
            task.completion_notes = encrypt_data(f"Отклонено: {reason}")
        session.commit()

        # Удалить задачу, так как она отклонена
        session.delete(task)
        session.commit()

        # Отправить уведомление отправителю
        try:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            if delegator:
                from .handlers import generate_delegation_response_notification_async
                import asyncio
                rejection_reason = f" (причина: {reason})" if reason else ""
                asyncio.create_task(generate_delegation_response_notification_async(
                    task_title=task.title,
                    response=f"rejected{rejection_reason}",
                    delegator_telegram_id=delegator.telegram_id,
                    delegatee_username=user.username or f"ID:{user.telegram_id}"
                ))
        except Exception as e:
            logger.warning(f"[REJECT_DELEGATED_TASK] Could not send notification: {e}")

        rejection_msg = f"✅ Задача '{task.title}' отклонена."
        if reason:
            rejection_msg += f" Причина: {reason}"

        return rejection_msg

    except Exception as e:
        logger.error(f"[REJECT_DELEGATED_TASK] Error: {e}")
        session.rollback()
        return f"❌ Ошибка при отклонении задачи: {e}"
    finally:
        session.close()
