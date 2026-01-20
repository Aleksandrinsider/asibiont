# Task and profile handler functions

import logging
import json
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
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()

    # Check if task with same title exists
    existing_task = session.query(Task).filter_by(user_id=user.id, title=title).first()
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
                        if parsed_time.tzinfo is None:
                            parsed_time = user_tz.localize(parsed_time)
                        task.reminder_time = parsed_time.astimezone(pytz.UTC)
                        logging.info(
                            f"Task {title} relative time parsed: '{reminder_time}' -> local: {parsed_time} -> UTC: {task.reminder_time}")
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
                        # Fallback to absolute time format
                        try:
                            local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                            local_dt = user_tz.localize(local_dt)
                            task.reminder_time = local_dt.astimezone(pytz.UTC)
                            logging.info(
                                f"Task {title} absolute time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
                        except ValueError:
                            logging.warning(f"Could not parse reminder_time '{reminder_time}' for task {title}")
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

    # Schedule reminder if specified
    if task.reminder_time:
        try:
            from main import reminder_service
            if reminder_service:
                reminder_service.schedule_reminder(
                    task_id=task.id, reminder_time=task.reminder_time, user_id=user.telegram_id, task_title=task.title
                )
        except Exception as e:
            logging.warning(f"Could not schedule reminder for task {task_id}: {e}")

    # Update profile analytics
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()

    # Format result message
    result_msg = f"Добавлена задача '{title}' (ID: {task_id})"
    if task.reminder_time:
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        result_msg += f" с напоминанием на {local_time.strftime('%d.%m.%Y %H:%M')}"

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg


async def delete_task(task_id=None, task_title=None, user_id=None, reason="", session=None):
    """Delete a specific task by ID or title"""
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

        task = None
        if task_id:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter(Task.id == task_id_int, Task.user_id == user.id).first()
            except (ValueError, TypeError):
                pass

        if not task and task_title:
            task = session.query(Task).filter(Task.user_id == user.id, Task.title.ilike(f"%{task_title}%")).first()

        if not task:
            if close_session:
                session.close()
            return "Задача не найдена."

        # Сохраняем информацию о удалении для аналитики
        deletion_info = f"Задача '{task.title}' удалена. Причина: {reason}"
        
        # Если причина связана с ошибками или дубликатами, можем использовать для улучшения
        if reason and any(keyword in reason.lower() for keyword in ['ошибк', 'дублир', 'актуальн']):
            # Можно добавить логику для анализа паттернов удаления задач
            pass

        # Delete the task
        session.delete(task)
        session.commit()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.total_tasks_created:
            profile.total_tasks_created = max(0, (profile.total_tasks_created or 0) - 1)
            session.commit()

        if close_session:
            session.close()
        return f"Задача '{task.title}' удалена."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задачи: {str(e)}"


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


async def complete_task(task_id=None, task_title=None, user_id=None, session=None):
    """Mark task as completed"""
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
                    and_(Task.id == task_id_int, Task.delegated_to_username.ilike(user.username.replace('@', '')), Task.delegation_status == "accepted")
                )
            )
            .first()
        )
    elif task_title:
        # Search by words in title (including delegated tasks)
        words = task_title.lower().split()
        # Use func.lower() for case-insensitive search
        conditions = [func.lower(Task.title).like(f"%{word}%") for word in words]
        
        # Build query with optional delegated task search
        query_conditions = [and_(Task.user_id == user.id, Task.status != "completed", or_(*conditions))]
        
        if user.username:
            query_conditions.append(
                and_(
                    Task.delegated_to_username.ilike(user.username.replace('@', '')),
                    Task.delegation_status == "accepted",
                    Task.status != "completed",
                    or_(*conditions)
                )
            )
        
        task = session.query(Task).filter(or_(*query_conditions)).first()
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        task.status = "completed"
        task.actual_completion_time = datetime.now(timezone.utc)
        session.commit()

        # Schedule result check - уточнение результата выполнения через 1 час
        result_check_time = datetime.now(timezone.utc) + timedelta(hours=1)
        try:
            from main import reminder_service
            if reminder_service:
                reminder_service.schedule_result_check(
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
            prev_avg = profile.average_completion_time or 0
            if profile.completed_tasks > 0:
                profile.average_completion_time = (
                    (prev_avg * (profile.completed_tasks - 1)) + completion_time
                ) / profile.completed_tasks
            session.commit()
        result = f"Завершена задача '{task.title}'."

        # Генерируем запрос на уточнение результатов через AI
        try:
            from ai_integration.chat import generate_result_check
            result_check_text = await generate_result_check(user.telegram_id, task.title)
            result += f"\n\n{result_check_text}"
        except Exception as e:
            logging.warning(f"Could not generate result check text: {e}")
            # Fallback to default text
            result += f"\n\nРасскажите, пожалуйста, о результатах выполнения задачи '{task.title}'. Что было сделано? Какие возникли сложности? Это поможет улучшить планирование будущих задач."

        # Если задача была делегирована этому пользователю, отправляем отчет делегировавшему
        if task.delegated_to_username and task.delegation_status == "accepted":
            # Проверяем, является ли текущий пользователь получателем делегированной задачи
            if task.delegated_to_username.replace('@', '').lower() == user.username.replace('@', '').lower():
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

        # Save to interaction history
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
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
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username.replace('@', '')))
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
                    Task.delegated_to_username.ilike(user.username.replace('@', '')),
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

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.skipped_tasks = (profile.skipped_tasks or 0) + 1
            session.commit()
        result = f"Задача '{task.title}' отмечена как пропущенная."

        # Save to interaction history
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
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
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username.replace('@', '')))
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
                    Task.delegated_to_username.ilike(user.username.replace('@', '')),
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

        # Save to interaction history
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
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

            # Save to interaction history
            interaction = Interaction(user_id=user.id, message_type="ai", content=result)
            session.add(interaction)
            session.commit()
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

            # Save to interaction history
            interaction = Interaction(user_id=user.id, message_type="ai", content=result)
            session.add(interaction)
            session.commit()
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
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username.replace('@', '')))
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
                reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                task.reminder_time = reminder_time_parsed
                session.commit()
                result = f"Установлено напоминание для '{task.title}' на {reminder_time_parsed}."
            except ValueError:
                result = "Неверный формат времени."
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
    session = Session()
    try:
        # Check if delegator has Bronze tier - Bronze users can only receive delegated tasks
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."
        
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
                    from main import reminder_service
                    if reminder_service:
                        reminder_service.schedule_reminder(
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
                    message += f"\nНапишите боту 'принять задачу {task_id}' для подтверждения или 'отклонить задачу {task_id}' для отказа."

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


def create_subscription_payment(user_id=None):
    """Create subscription payment"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id)
        return f"Ссылка на оплату месячной подписки создана: {payment_url}"
    except Exception as e:
        return f"Ошибка создания платежа: {str(e)}"


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
                Task.delegated_to_username.ilike(user.username.replace('@', '')),
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
                from main import reminder_service
                if reminder_service:
                    reminder_service.schedule_reminder(
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


def reject_delegated_task(task_id, user_id=None):
    """Reject a delegated task"""
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
                Task.delegated_to_username.ilike(user.username.replace('@', '')),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"
        session.commit()

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
                if "через" in reminder_time.lower():
                    current_time = datetime.now(pytz.UTC)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time
                        logger.info(f"Task {task.id} relative time updated: '{reminder_time}' -> {parsed_time}")
                    else:
                        session.close()
                        return "Не удалось распарсить относительное время."
                else:
                    reminder_time_parsed = datetime.strptime(
                        reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    task.reminder_time = reminder_time_parsed
                    logger.info(f"Task {task.id} absolute time updated: {reminder_time_parsed}")
            except ValueError:
                if close_session:
                    session.close()
                return "Неверный формат времени. Используйте YYYY-MM-DD HH:MM или 'через X минут'."
        session.commit()
        result = f"Обновлена задача '{task.title}'."
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


def list_tasks(user_id=None, session=None):
    """Return list of user's tasks in plain text format"""
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Get user tasks or delegated tasks
        query = session.query(Task).filter(Task.user_id == user.id)
        if user.username and user.username.strip():
            query = query.union(
                session.query(Task).filter(Task.delegated_to_username.ilike(user.username.replace('@', '')))
            )
        tasks = query.all()

        if not tasks:
            return "У вас нет активных задач. Добавьте первую задачу - просто напишите что нужно сделать!"

        # Format detailed list
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
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

        result = f"У вас {len(active_tasks)} {'задача' if len(active_tasks) == 1 else 'задач'}\n\n"

        # Show first 10 tasks instead of 3
        tasks_to_show = my_tasks[:10]
        if tasks_to_show:
            result += "Ваши задачи:\n"
            for task in tasks_to_show:
                reminder_info = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            delta = now - reminder_dt
                            days = delta.days
                            hours = (delta.seconds // 3600)
                            if days > 0:
                                reminder_info = f" - просрочено на {days} д {hours} ч" if hours else f" - просрочено на {days} д"
                            else:
                                reminder_info = f" - просрочено на {hours} ч"
                        else:
                            reminder_info = f" - {reminder_dt.strftime('%d.%m %H:%M')}"
                    except Exception as e:
                        logger.warning(f"Failed to process reminder time for task {task.id}: {e}")
                        pass
                result += f"- {task.title}{reminder_info}\n"

            if len(my_tasks) > 10:
                result += f"...и ещё {len(my_tasks) - 10}\n"
        
        # Show delegated tasks
        if delegated_to_me:
            result += "\nДелегированные мне:\n"
            for task in delegated_to_me[:5]:
                result += f"- {task.title} (от @{task.delegated_by if hasattr(task, 'delegated_by') else 'неизвестно'})\n"

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

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        logger.warning(f"[PARTNERS] User not found for telegram_id: {user_id}")
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
                Task.delegated_to_username.ilike(user.username.replace('@', '')), Task.delegation_status.in_(["pending", "accepted"])
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

        # Check skills
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(","))
            if user_skills & profile_skills:
                has_match = True

        # Check interests
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(","))
            if user_interests & profile_interests:
                has_match = True

        # Check current_plans for interest matches
        if user_profile.interests and profile.current_plans:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            for interest in user_interests:
                interest_words = interest.strip().lower().split()
                if any(word in profile.current_plans.lower() for word in interest_words):
                    has_match = True
                    break

        # Check goals
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True

        # Check company
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True

        if has_match:
            partners.append(profile)

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

    if close_session:
        session.close()

    return sorted_partners[:20]


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
    partners = get_partners_list(user_id, session)

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
        if value is None:
            return field, None, False
        if value == "":
            return None, f"cleared_{field_name}", False

        current = set((field or "").split(", ")) - {""}
        action = None

        if value.startswith("+"):
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
            for item in new_items_list:
                if item not in current:
                    current.add(item)
            if new_items_list:
                action = f"added_{field_name}:{', '.join(new_items_list)}"

        return ", ".join(sorted(current)), action, False

    if skills is not None:
        new_value, action, _ = update_list_field(profile.skills, skills, "skills")
        profile.skills = new_value
        if action:
            updates_made.append(action)

    if interests is not None:
        new_value, action, _ = update_list_field(profile.interests, interests, "interests")
        profile.interests = new_value
        logger.info(f"[UPDATE_PROFILE] Interests updated: old={profile.interests}, new={new_value}, action={action}")
        if action:
            updates_made.append(action)

    if goals is not None:
        new_value, action, _ = update_list_field(profile.goals, goals, "goals")
        profile.goals = new_value
        if action:
            updates_made.append(action)

    if city is not None:
        old_city = profile.city
        profile.city = city if city else None
        updates_made.append(f"changed_city:{old_city}->{city if city else 'cleared'}")

    if current_plans:
        profile.current_plans = current_plans
        updates_made.append("updated_plans")

    if hasattr(profile, "company") and company is not None:
        old_company = profile.company
        profile.company = company if company else None
        updates_made.append(f"changed_company:{old_company}->{company if company else 'cleared'}")

    if hasattr(profile, "position") and position is not None:
        old_position = profile.position
        profile.position = position if position else None
        updates_made.append(f"changed_position:{old_position}->{position if position else 'cleared'}")

    if hasattr(profile, "bio") and bio is not None:
        old_bio = profile.bio
        profile.bio = bio if bio else None
        updates_made.append(f"changed_bio:{old_bio}->{bio if bio else 'cleared'}")

    if hasattr(profile, "languages") and languages is not None:
        old_languages = profile.languages
        profile.languages = languages if languages else None
        updates_made.append(f"changed_languages:{old_languages}->{languages if languages else 'cleared'}")

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
        from main import reminder_service
        if not reminder_service:
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

                reminder_service.schedule_delegation_check(
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
            reminder_service.schedule_delegation_check(
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
