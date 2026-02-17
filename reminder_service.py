from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from models import Session
from models import Task, User, Interaction
from config import DATABASE_URL
from datetime import datetime, timedelta, timezone
import pytz
import logging
import json
import asyncio
from collections import defaultdict
from config import DATABASE_URL, OVERDUE_CHECK_INTERVAL_MINUTES, PROACTIVE_CHECK_AHEAD_MINUTES, LAST_INTERACTION_THRESHOLD_MINUTES, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR, PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES, PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES, PROACTIVE_CHECK_INTERVAL_MINUTES
from ai_integration import check_delegation_deadlines, generate_proactive_message

logger = logging.getLogger(__name__)

# Singleton reference used by jobstore-safe wrapper functions
REMINDER_SERVICE = None

# Global locks dictionary to prevent duplicate proactive messages
# Key: user_id, Value: asyncio.Lock
_proactive_locks = defaultdict(asyncio.Lock)

async def _send_reminder_job(task_id: int):
    db = Session()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            logger.warning(f"_send_reminder_job: task {task_id} not found")
            return
        
        # Проверяем статус задачи - не отправляем напоминание для завершенных задач
        if task.status == 'completed':
            logger.info(f"_send_reminder_job: task {task_id} is already completed, skipping reminder")
            return
            
        # Проверяем, не было ли уже отправлено напоминание
        if task.reminder_sent:
            logger.info(f"_send_reminder_job: reminder already sent for task {task_id}")
            return
            
        if not task.user:
            logger.warning(f"_send_reminder_job: user not found for task {task_id}")
            return
        if not hasattr(task.user, 'telegram_id') or task.user.telegram_id is None:
            logger.warning(f"_send_reminder_job: telegram_id is None for task {task_id}")
            return
        user_id = task.user.telegram_id
        task_title = task.title or "Без названия"
        
        # Помечаем, что напоминание отправлено
        task.reminder_sent = True
        db.commit()
        
    finally:
        db.close()

    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_reminder(user_id, task_title, task_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send reminder")

async def _send_followup_reminder_job(task_id: int):
    """Повторное напоминание через 15 минут если задача не выполнена"""
    db = Session()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            logger.warning(f"_send_followup_reminder_job: task {task_id} not found")
            return
        
        # Проверяем статус задачи - отправляем только для невыполненных
        if task.status in ['completed', 'deleted']:
            logger.info(f"_send_followup_reminder_job: task {task_id} status {task.status}, skipping")
            return
            
        # Проверяем, не было ли уже отправлено повторное напоминание
        if task.followup_reminder_sent:
            logger.info(f"_send_followup_reminder_job: followup already sent for task {task_id}")
            return
            
        if not task.user:
            logger.warning(f"_send_followup_reminder_job: user not found for task {task_id}")
            return
        if not hasattr(task.user, 'telegram_id') or task.user.telegram_id is None:
            logger.warning(f"_send_followup_reminder_job: telegram_id is None for task {task_id}")
            return
        user_id = task.user.telegram_id
        task_title = task.title or "Без названия"
        
        # Помечаем, что повторное напоминание отправлено
        task.followup_reminder_sent = True
        db.commit()
        
    finally:
        db.close()

    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_followup_reminder(user_id, task_title, task_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send followup reminder")

async def _send_result_check_job(task_id: int):
    db = Session()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task:
            logger.warning(f"_send_result_check_job: task {task_id} not found")
            return
        
        # Проверяем статус задачи - не отправляем проверку результата для завершенных задач
        if task.status == 'completed':
            logger.info(f"_send_result_check_job: task {task_id} is already completed, skipping result check")
            return
            
        # Проверяем, не была ли уже отправлена проверка результата
        if task.result_check_sent:
            logger.info(f"_send_result_check_job: result check already sent for task {task_id}")
            return
            
        if not task.user:
            logger.warning(f"_send_result_check_job: user not found for task {task_id}")
            return
        if not hasattr(task.user, 'telegram_id') or task.user.telegram_id is None:
            logger.warning(f"_send_result_check_job: telegram_id is None for task {task_id}")
            return
        user_id = task.user.telegram_id
        task_title = task.title or "Без названия"
        
        # Помечаем, что проверка результата отправлена
        task.result_check_sent = True
        db.commit()
        
    finally:
        db.close()

    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_result_check(user_id, task_title, task_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send result check")

async def _send_daily_report_job(user_id: int):
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_daily_report(user_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send daily report")

async def _check_and_send_proactive_job(user_id: int):
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.check_and_send_proactive(user_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot run proactive check")

async def _check_and_send_overdue_reminder_job(user_id: int):
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.check_and_send_overdue_reminder(user_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot check overdue")


async def _update_user_avatars_job():
    """Jobstore-safe wrapper for updating user avatars from Telegram"""
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.update_user_avatars()
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot update avatars")


async def _send_task_checkpoint_job(user_id: int, checkpoint_type: str = "general"):
    """Jobstore-safe wrapper for task checkpoint messages"""
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_task_checkpoint_message(user_id, checkpoint_type)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send task checkpoint message")


async def _schedule_recurring_tasks_job():
    """Jobstore-safe wrapper for scheduling recurring tasks"""
    if REMINDER_SERVICE:
        REMINDER_SERVICE.schedule_recurring_tasks()
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot schedule recurring tasks")


async def _send_delegation_check_job(task_id: int, delegator_id: int, recipient_id: int, check_type: str = "progress_request"):
    """Jobstore-safe wrapper for delegation check"""
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_delegation_check(task_id, delegator_id, recipient_id, check_type)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send delegation check")



class ReminderService:
    def __init__(self, bot=None, ai_service=None):
        self.bot = bot
        # Import AI functions directly
        from ai_integration import generate_reminder, generate_result_check, generate_daily_report, generate_proactive_message, generate_overdue_reminder
        self.generate_reminder = generate_reminder
        self.generate_result_check = generate_result_check
        self.generate_daily_report = generate_daily_report
        self.generate_proactive_message = generate_proactive_message
        self.generate_overdue_reminder = generate_overdue_reminder
        # Use persistent jobstore (SQLAlchemy) to survive restarts
        jobstores = {
            'default': SQLAlchemyJobStore(url=DATABASE_URL)
        }
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC, jobstores=jobstores)
        # Register singleton reference for jobstore-safe wrappers
        global REMINDER_SERVICE
        REMINDER_SERVICE = self

    async def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
        self.schedule_existing_reminders()
        self.schedule_daily_reports()
        self.schedule_proactive_checks()
        self.schedule_overdue_checks()
        self.schedule_delegation_checks()
        self.schedule_avatar_updates()
        self.schedule_recurring_task_checks()

    def schedule_existing_reminders(self):
        logger = logging.getLogger(__name__)
        db = Session()
        try:
            tasks = db.query(Task).filter(Task.reminder_time.isnot(None), Task.reminder_sent == False).all()
            logger.info(f"Found {len(tasks)} tasks with reminders to schedule")
            for task in tasks:
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = reminder_time.replace(tzinfo=pytz.UTC)
                if reminder_time > datetime.now(pytz.UTC):
                    # Безопасная проверка наличия user
                    if task.user and task.user.telegram_id:
                        logger.info(f"Scheduling reminder for task {task.id} at {task.reminder_time}")
                        self.schedule_reminder(task.id, task.reminder_time, task.user.telegram_id, task.title)
                    else:
                        logger.warning(f"Task {task.id} has no user or telegram_id")
                else:
                    logger.info(f"Task {task.id} reminder time {task.reminder_time} is in the past")
            
            # Планируем проверки результатов для задач с reminder_sent=True и estimated_duration
            result_tasks = db.query(Task).filter(
                Task.reminder_sent == True,
                Task.result_check_sent == False,
                Task.estimated_duration.isnot(None),
                Task.status.in_(['pending', 'in_progress'])
            ).all()
            
            for task in result_tasks:
                if task.user and task.user.telegram_id:
                    result_check_time = task.reminder_time + timedelta(minutes=task.estimated_duration)
                    if result_check_time.tzinfo is None:
                        result_check_time = result_check_time.replace(tzinfo=pytz.UTC)
                    if result_check_time > datetime.now(pytz.UTC):
                        self.schedule_result_check(task.id, result_check_time, task.user.telegram_id, task.title)
        finally:
            db.close()

    def schedule_reminder(self, task_id: int, reminder_time: datetime, user_id: int, task_title: str):
        logger = logging.getLogger(__name__)
        # Конвертируем naive datetime в aware с UTC
        if reminder_time.tzinfo is None:
            reminder_time = pytz.UTC.localize(reminder_time)

        logger.info(f"Scheduling reminder for task {task_id}, user {user_id}, time: {reminder_time}")

        # Проверяем, запущен ли scheduler
        if not self.scheduler.running:
            # Scheduler не запущен в тестах - это нормально
            return

        trigger = DateTrigger(run_date=reminder_time, timezone=pytz.UTC)
        # Use jobstore-safe module-level wrapper to avoid pickling scheduler-bound instances
        self.scheduler.add_job(
            _send_reminder_job,
            trigger=trigger,
            args=[task_id],
            id=f"reminder_{task_id}",
            replace_existing=True
        )
        logger.info(f"Reminder scheduled for {reminder_time} (in {(reminder_time - datetime.now(pytz.UTC)).total_seconds() / 60:.1f} minutes)")
        
        # Планируем повторное напоминание через 15 минут
        followup_time = reminder_time + timedelta(minutes=15)
        self.schedule_followup_reminder(task_id, followup_time, user_id, task_title)
    
    def schedule_followup_reminder(self, task_id: int, followup_time: datetime, user_id: int, task_title: str):
        """Планирует повторное напоминание через 15 минут после основного"""
        logger = logging.getLogger(__name__)
        
        # Конвертируем naive datetime в aware с UTC
        if followup_time.tzinfo is None:
            followup_time = pytz.UTC.localize(followup_time)
        
        logger.info(f"Scheduling followup reminder for task {task_id}, time: {followup_time}")
        
        if not self.scheduler.running:
            return
        
        trigger = DateTrigger(run_date=followup_time, timezone=pytz.UTC)
        self.scheduler.add_job(
            _send_followup_reminder_job,
            trigger=trigger,
            args=[task_id],
            id=f"followup_{task_id}",
            replace_existing=True
        )
        logger.info(f"Followup reminder scheduled for {followup_time}")

    def schedule_result_check(self, task_id: int, result_check_time: datetime, user_id: int, task_title: str):
        # Конвертируем naive datetime в aware с UTC
        if result_check_time.tzinfo is None:
            result_check_time = pytz.UTC.localize(result_check_time)
        
        trigger = DateTrigger(run_date=result_check_time, timezone=pytz.UTC)
        # Use jobstore-safe wrapper
        self.scheduler.add_job(
            _send_result_check_job,
            trigger=trigger,
            args=[task_id],
            id=f"result_check_{task_id}",
            replace_existing=True
        )

    async def send_result_check(self, user_id: int, task_title: str, task_id: int):
        import traceback
        logger = logging.getLogger(__name__)
        logger.info("=== STARTING RESULT CHECK SEND ===")
        logger.info(f"Sending result check for task {task_id}, user telegram_id {user_id}, title: {task_title}")
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять проверку результата
        if not check_subscription(user_id):
            logger.info(f"Subscription check failed for user {user_id}, skipping result check")
            return
        
        result_check_sent_successfully = False
        result_text = None
        
        try:
            logger.info(f"Generating result check text for task {task_id}...")
            result_text = await self.generate_result_check(user_id, task_title)
            logger.info(f"Result check text generated: {result_text[:100]}...")
            
            if self.bot:
                logger.info(f"Attempting to send result check via Telegram to chat_id {user_id}...")
                try:
                    result = await self.bot.send_message(
                        chat_id=user_id,
                        text=result_text
                    )
                    logger.info(f"✅ Result check sent successfully to user {user_id} for task {task_id}, message_id: {result.message_id}")
                    result_check_sent_successfully = True
                        
                except Exception as send_error:
                    logger.error(f"❌ Failed to send Telegram message to user {user_id}: {type(send_error).__name__}: {send_error}")
                    logger.error(f"Full traceback: {traceback.format_exc()}")
                    result_check_sent_successfully = False
            else:
                # Для тестов - вывод в консоль
                logger.info(f"[RESULT CHECK SENT] To user {user_id}: {result_text}")
                result_check_sent_successfully = True
        except Exception as e:
            logger.error(f"❌ Critical error in send_result_check for task {task_id}: {type(e).__name__}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            result_check_sent_successfully = False
        
        # Обновить статус в БД ТОЛЬКО если отправка успешна
        if result_check_sent_successfully:
            db = Session()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    task.result_check_sent = True
                    db.commit()
                    logger.info(f"Task {task_id} marked as result_check_sent=True")
                    
                    # Установить pending_action для обработки ответа пользователя
                    user = db.query(User).filter(User.telegram_id == user_id).first()
                    if user:
                        pending_data = {
                            "type": "result_check_response",
                            "task_id": task_id,
                            "task_title": task_title,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                        user.pending_action = json.dumps(pending_data)
                        logger.info(f"Set pending_action: {user.pending_action}")
                        db.commit()
                        db.refresh(user)
                        logger.info(f"After commit pending_action: {user.pending_action}")
                    else:
                        logger.warning(f"User with telegram_id {user_id} not found for setting pending_action")
            except Exception as e:
                logger.error(f"Failed to update result_check_sent for task {task_id}: {e}")
                db.rollback()
            finally:
                db.close()
        else:
            logger.warning(f"Task {task_id} NOT marked as result_check_sent due to delivery failure")

    async def send_followup_reminder(self, user_id: int, task_title: str, task_id: int):
        """Отправка повторного напоминания (эскалация)"""
        logger = logging.getLogger(__name__)
        logger.info(f"=== FOLLOWUP REMINDER for task {task_id}, user {user_id} ===")
        from subscription_service import check_subscription
        
        if not check_subscription(user_id):
            logger.info(f"Subscription check failed for user {user_id}, skipping followup")
            return
        
        try:
            # Генерируем текст с эскалацией (более настойчивый тон)
            reminder_text = await self.generate_reminder(user_id, task_title, task_id, escalation_level=2)
            logger.info(f"Followup reminder text: {reminder_text[:100]}...")
            
            # Сохраняем в историю
            db = Session()
            try:
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    task = db.query(Task).filter_by(id=task_id).first()
                    if task:
                        user.current_task_id = task_id
                    interaction = Interaction(
                        user_id=user.id,
                        message_type="ai",
                        content=reminder_text
                    )
                    db.add(interaction)
                    db.commit()
            finally:
                db.close()
            
            if self.bot:
                await self.bot.send_message(user_id, reminder_text)
                logger.info(f"✅ Followup reminder sent to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send followup reminder: {e}", exc_info=True)

    async def send_reminder(self, user_id: int, task_title: str, task_id: int):
        import traceback
        logger = logging.getLogger(__name__)
        logger.info("=== STARTING REMINDER SEND ===")
        logger.info(f"Sending reminder for task {task_id}, user telegram_id {user_id}, title: {task_title}")
        from subscription_service import check_subscription
        
        # Для напоминаний всегда отправляем, независимо от подписки
        # if not check_subscription(user_id):
        #     logger.info(f"Subscription check failed for user {user_id}")
        #     return
        
        reminder_sent_successfully = False
        reminder_text = None
        
        try:
            # Перед генерацией текста напоминания проверяем состояние пользователя
            db = Session()
            try:
                from models import User, Task
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if not user:
                    logger.warning(f"User with telegram_id {user_id} not found in database - aborting reminder for task {task_id}")
                    return
                
                # Проверяем статус задачи - не отправляем напоминание для завершенных задач
                task = db.query(Task).filter_by(id=task_id).first()
                if not task:
                    logger.warning(f"Task {task_id} not found - aborting reminder")
                    return
                
                if task.status in ['completed', 'cancelled', 'deleted']:
                    logger.info(f"Task {task_id} has status '{task.status}' - skipping reminder")
                    return
                
                # Проверяем режим 'не беспокоить' для пользователя
                if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                    logger.info(f"User {user_id} in DND until {user.do_not_disturb_until}, skipping reminder for task {task_id}")
                    return
            finally:
                db.close()

            logger.info(f"Generating reminder text for task {task_id}...")
            reminder_text = await self.generate_reminder(user_id, task_title, task_id)
            logger.info(f"Reminder text generated: {reminder_text[:100]}...")
            
            # Сохранить напоминание в таблицу Interaction
            db = Session()
            try:
                # Найти user.id по telegram_id
                from models import User, Task
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    # УСТАНАВЛИВАЕМ КОНТЕКСТ ТЕКУЩЕЙ ЗАДАЧИ при отправке напоминания
                    task = db.query(Task).filter_by(id=task_id).first()
                    if task:
                        user.current_task_id = task_id
                        logger.info(f"[CONTEXT] Set current_task_id={task_id} ({task.title}) for user {user_id} during reminder")
                    
                    interaction = Interaction(
                        user_id=user.id,
                        message_type="ai",
                        content=reminder_text
                    )
                    db.add(interaction)
                    db.commit()
                    logger.info(f"Reminder saved to interaction history for user {user_id}")
                else:
                    logger.warning(f"User with telegram_id {user_id} not found in database")
            finally:
                db.close()
            
            if self.bot:
                logger.info(f"Attempting to send reminder via Telegram to chat_id {user_id}...")
                try:
                    result = await self.bot.send_message(
                        chat_id=user_id,
                        text=reminder_text
                    )
                    logger.info(f"✅ Reminder sent successfully to user {user_id} for task {task_id}, message_id: {result.message_id}")
                    reminder_sent_successfully = True
                except Exception as send_error:
                    err_text = str(send_error)
                    logger.error(f"❌ Failed to send Telegram message to user {user_id}: {type(send_error).__name__}: {err_text}")
                    logger.error(f"Full traceback: {traceback.format_exc()}")

                    # Для серверных ошибок (5xx): планируем повторы через 10 минут
                    if any(code in err_text for code in ['500','502','503','504']):
                        retry_time = datetime.now(pytz.UTC) + timedelta(minutes=10)
                        try:
                            self.scheduler.add_job(
                                _send_reminder_job,
                                trigger=DateTrigger(run_date=retry_time, timezone=pytz.UTC),
                                args=[task_id],
                                id=f"retry_reminder_{task_id}_{int(retry_time.timestamp())}",
                                replace_existing=False
                            )
                            logger.info(f"Scheduled retry for reminder {task_id} at {retry_time}")
                        except Exception as sched_err:
                            logger.error(f"Failed to schedule retry for reminder {task_id}: {sched_err}")
                    reminder_sent_successfully = False
            else:
                # Для тестов - вывод в консоль
                logger.info(f"[REMINDER SENT] To user {user_id}: {reminder_text}")
                reminder_sent_successfully = True
        except Exception as e:
            logger.error(f"❌ Critical error in send_reminder for task {task_id}: {type(e).__name__}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            reminder_sent_successfully = False
        
        # Обновить статус в БД ТОЛЬКО если отправка успешна
        if reminder_sent_successfully:
            db = Session()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    task.reminder_sent = True
                    db.commit()
                    logger.info(f"Task {task_id} marked as reminder_sent=True")
            except Exception as e:
                logger.error(f"Failed to update reminder_sent for task {task_id}: {e}")
                db.rollback()
            finally:
                db.close()
        else:
            logger.warning(f"Task {task_id} NOT marked as sent due to delivery failure - will retry on next schedule")





    def schedule_daily_reports(self):
        """Планирование ежедневных отчетов в 22:00 по времени пользователя - ОТКЛЮЧЕНО"""
        logger.info("Daily reports are disabled")
        return

    async def send_daily_report(self, user_id: int):
        """Отправка ежедневного отчета пользователю"""
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять отчет
        if not check_subscription(user_id):
            return
        
        try:
            report_text = await self.generate_daily_report(user_id)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=report_text
                )
            else:
                logger.info(f"[DAILY REPORT] To user {user_id}: {report_text}")
        except Exception as e:
            logging.error(f"Failed to send daily report to user {user_id}: {e}")    
    async def send_delegation_progress_update(self, task_id: int, update_type: str = "status"):
        """Send simple progress update about delegated task to delegator"""
        db = Session()
        try:
            task = db.query(Task).filter_by(id=task_id).first()
            if not task or not task.delegated_by or task.delegation_status != 'accepted':
                return
            
            delegator = db.query(User).filter_by(id=task.delegated_by).first()
            recipient = db.query(User).filter_by(id=task.user_id).first()
            
            if not delegator or not recipient:
                return
            
            # Простое уведомление без AI-генерации
            if update_type == "completed":
                message = f"Задача '{task.title}' выполнена @{recipient.username}"
            else:
                message = f"Напоминание: задача '{task.title}' для @{recipient.username}, дедлайн: {task.reminder_time.strftime('%d.%m %H:%M') if task.reminder_time else 'не указан'}"
            
            if self.bot:
                await self.bot.send_message(delegator.telegram_id, message)
        except Exception as e:
            logging.error(f"Failed to send delegation progress update: {e}")
        finally:
            db.close()
    def schedule_proactive_checks(self):
        """Планирование начальных проактивных проверок для всех пользователей"""
        
        db = Session()
        try:
            users = db.query(User).all()
            logger.info(f"Scheduling initial proactive checks for {len(users)} users")
            for user in users:
                # Получить количество активных задач для пользователя
                task_count = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress'])
                ).count()
                
                # Запланировать начальную проактивную проверку
                import asyncio
                asyncio.create_task(self._reschedule_proactive_check(user.telegram_id, task_count))
        finally:
            db.close()

    async def send_task_checkpoint_message(self, user_id: int, checkpoint_type: str = "general"):
        """Отправка сообщения для чекпоинта задачи (1/3, 2/3, overdue)"""
        from subscription_service import check_subscription
        
        # Проверить подписку
        if not check_subscription(user_id):
            return
        
        # MUTEX: Проверить, не отправляется ли уже проактивное сообщение этому пользователю
        lock = _proactive_locks[user_id]
        if lock.locked():
            logger.info(f"[MUTEX] Checkpoint message already being sent (or proactive in progress) to user {user_id}, skipping duplicate")
            return
        
        async with lock:
            db = Session()
            try:
                user = db.query(User).filter(User.telegram_id == user_id).first()
                if not user:
                    return
            
                # Проверить последнее взаимодействие - если было в последние 15 минут, не отправлять
                last_interaction = db.query(Interaction).filter(
                    Interaction.user_id == user.id
                ).order_by(Interaction.created_at.desc()).first()
                
                if last_interaction:
                    time_since_last = datetime.now(pytz.UTC) - last_interaction.created_at.replace(tzinfo=pytz.UTC)
                    if time_since_last < timedelta(minutes=LAST_INTERACTION_THRESHOLD_MINUTES):
                        return
                
                # Проверить режим "не беспокоить"
                if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                    return
                
                # Получить активные задачи
                # Основные задачи пользователя
                user_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress'])
                )
                
                # Задачи, делегированные пользователем другим
                delegated_by_user = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.status.in_(['pending', 'in_progress'])
                )
            
                # Задачи, делегированные пользователю
                delegated_to_user = db.query(Task).filter(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.delegation_status == 'accepted',
                    Task.status.in_(['pending', 'in_progress'])
                )
                
                all_active_tasks = user_tasks.union(delegated_by_user).union(delegated_to_user).order_by(Task.reminder_time).all()
                
                # Добавить просроченные задачи (только основные и делегированные пользователю)
                now_utc = datetime.now(pytz.UTC)
                overdue_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'pending',
                    Task.reminder_time < now_utc
                ).union(
                    db.query(Task).filter(
                        Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                        Task.delegation_status == 'accepted',
                        Task.status == 'pending',
                        Task.reminder_time < now_utc
                    )
                ).order_by(Task.reminder_time).all()
                
                all_tasks = all_active_tasks + overdue_tasks
                
                # Проверить, когда было последнее проактивное сообщение (не чаще чем раз в 30 минут)
                last_proactive = db.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type.in_(["ai", "proactive"]),
                    Interaction.created_at > now_utc - timedelta(minutes=30)
                ).order_by(Interaction.created_at.desc()).first()
                
                if last_proactive:
                    logger.info(f"Skipping checkpoint message for user {user_id} - last proactive message was {last_proactive.created_at}")
                    return
                
                # Определить параметры для генерации сообщения
                task_count = len(all_active_tasks)
                overdue_count = len(overdue_tasks)
                context = checkpoint_type
                
                # Отправить чекпоинт-сообщение
                proactive_text = await self.generate_proactive_message(user_id, context, task_count, overdue_count, all_tasks)
                
                # Сохранить в таблицу Interaction
                interaction = Interaction(
                    user_id=user.id,
                    message_type="ai",
                    content=proactive_text
                )
                db.add(interaction)
                db.commit()
                logger.info(f"Saved checkpoint message to interaction history for user {user_id}")
                
                if self.bot:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=proactive_text
                    )
                    logger.info(f"Sent checkpoint message to user {user_id}")
                else:
                    logger.info(f"[CHECKPOINT] To user {user_id}: {proactive_text}")
            except Exception as e:
                logging.error(f"Failed to send checkpoint message to user {user_id}: {e}")
                db.rollback()
            finally:
                db.close()

    async def check_and_send_proactive(self, user_id: int):
        """Проверка и отправка проактивного сообщения.
        
        Упрощённая логика: все anti-spam проверки остаются,
        но выбор контента полностью делегирован AI (generate_proactive_message).
        """
        from datetime import timedelta
        from config import FREE_ACCESS_MODE
        
        # Проверить подписку
        from subscription_service import check_subscription
        if not check_subscription(user_id):
            return
        
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            # Проверить время — не отправлять с 22:00 до 10:00
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            now_user_time = datetime.now(user_tz)
            current_hour = now_user_time.hour
            
            if PROACTIVE_NO_SEND_START_HOUR <= current_hour or current_hour < PROACTIVE_SEND_START_HOUR:
                await self._reschedule_proactive_check(user_id, task_count=0)
                return
            
            # Проверить последнее взаимодействие — 15 мин порог
            last_interaction = db.query(Interaction).filter(
                Interaction.user_id == user.id
            ).order_by(Interaction.created_at.desc()).first()
            
            if last_interaction:
                time_since = datetime.now(pytz.UTC) - last_interaction.created_at.replace(tzinfo=pytz.UTC)
                if time_since < timedelta(minutes=LAST_INTERACTION_THRESHOLD_MINUTES):
                    return
            
            # Проверить DND
            if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                return
            
            now_utc = datetime.now(pytz.UTC)
            
            # Получить pending задачи
            pending_tasks = db.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'pending',
                Task.reminder_time.isnot(None)
            ).all()
            
            total_active = len(pending_tasks)
            
            # Если есть задачи в ближайшие 60 мин — не отправлять (скоро будет напоминание)
            next_60_min = now_utc + timedelta(minutes=PROACTIVE_CHECK_AHEAD_MINUTES)
            tasks_in_60_min = 0
            for task in pending_tasks:
                rt = task.reminder_time
                if rt.tzinfo is None:
                    rt = pytz.UTC.localize(rt)
                if now_utc <= rt < next_60_min:
                    tasks_in_60_min += 1
            
            if tasks_in_60_min > 0:
                await self._reschedule_proactive_check(user_id, task_count=total_active)
                return
            
            # Anti-spam: не чаще 1 раза в час
            recent_proactive = db.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type.in_(['ai', 'proactive']),
                Interaction.created_at >= now_utc - timedelta(hours=1)
            ).first()
            
            if recent_proactive:
                logger.debug(f"Proactive already sent in last hour for user {user_id}")
                await self._reschedule_proactive_check(user_id, task_count=total_active)
                return
            
            # Anti-spam: не после недавних напоминаний (1 час)
            recent_reminders = db.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'reminder',
                Interaction.created_at >= now_utc - timedelta(hours=1)
            ).first()
            
            if recent_reminders:
                logger.debug(f"Recent reminder found for user {user_id}, skipping proactive")
                await self._reschedule_proactive_check(user_id, task_count=total_active)
                return
            
            # Определяем context hint для AI (просроченные задачи — приоритет)
            overdue_tasks = [t for t in pending_tasks
                            if t.reminder_time and (t.reminder_time if t.reminder_time.tzinfo else pytz.UTC.localize(t.reminder_time)) < now_utc]
            
            context = "overdue_tasks" if overdue_tasks else "general"
            
            # Отправляем проактивное сообщение — AI сам выберет, что полезнее
            await self.send_proactive_message(
                user_id, context=context,
                task_count=total_active,
                overdue_count=len(overdue_tasks)
            )
            await self._reschedule_proactive_check(user_id, task_count=total_active)
        finally:
            db.close()

    def schedule_task_checkpoints(self, user_id: int):
        """Планирование чекпоинтов для задач пользователя по принципу 1/3-2/3-3/3 ПОСЛЕ ПРОСРОЧКИ
        
        Для просроченных задач:
        - 1/3 estimated_duration после просрочки: первый чекпоинт
        - 2/3 estimated_duration после просрочки: второй чекпоинт  
        - 3/3 estimated_duration после просрочки: третий чекпоинт (финальный)
        
        Для задач с приближающимся дедлайном:
        - 1/3 времени до reminder_time: предварительный чекпоинт
        """
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            # Получить все pending задачи с reminder_time
            pending_tasks = db.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'pending',
                Task.reminder_time.isnot(None)
            ).all()
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            current_time = datetime.now(pytz.UTC)
            
            for task in pending_tasks:
                # Сделать reminder_time aware с UTC
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = pytz.UTC.localize(reminder_time)
                
                # Пропустить задачи, которые уже просрочены более чем на день
                if reminder_time < current_time - timedelta(days=1):
                    continue
                
                time_until_reminder = reminder_time - current_time
                
                if time_until_reminder.total_seconds() <= 0:
                    # Задача просрочена - чекпоинты на 1/3, 2/3, 3/3 estimated_duration ПОСЛЕ просрочки
                    overdue_duration = task.estimated_duration or 60  # по умолчанию 1 час
                    
                    # Рассчитываем чекпоинты относительно reminder_time
                    checkpoint_1 = reminder_time + timedelta(minutes=overdue_duration // 3)  # 1/3
                    checkpoint_2 = reminder_time + timedelta(minutes=(overdue_duration * 2) // 3)  # 2/3  
                    checkpoint_3 = reminder_time + timedelta(minutes=overdue_duration)  # 3/3
                    
                    # Планируем все три чекпоинта, если они в будущем
                    checkpoints = [
                        (checkpoint_1, "overdue_1_3"),
                        (checkpoint_2, "overdue_2_3"), 
                        (checkpoint_3, "overdue_3_3")
                    ]
                    
                    for check_time, check_type in checkpoints:
                        if check_time > current_time:
                            job_id = f"task_overdue_{task.id}_{check_type}_{user.telegram_id}"
                            
                            # Удалить существующий джоб
                            if self.scheduler.get_job(job_id):
                                self.scheduler.remove_job(job_id)
                            
                            # Запланировать чекпоинт
                            self.scheduler.add_job(
                                _send_task_checkpoint_job,
                                trigger="date",
                                run_date=check_time,
                                args=[user.telegram_id, check_type],
                                id=job_id,
                                replace_existing=True,
                                misfire_grace_time=300,  # 5 минут на опоздание
                                max_instances=1
                            )
                            
                            logger.debug(f"Scheduled overdue checkpoint {check_type} for task {task.id} at {check_time} (user {user.telegram_id})")
                else:
                    # Задача не просрочена - чекпоинт на 1/3 времени до reminder_time
                    if time_until_reminder.total_seconds() > 0:
                        check_time = current_time + (time_until_reminder * 1 / 3)
                        if check_time > current_time:
                            job_id = f"task_checkpoint_{task.id}_1_3_{user.telegram_id}"
                            
                            # Удалить существующий джоб
                            if self.scheduler.get_job(job_id):
                                self.scheduler.remove_job(job_id)
                            
                            # Запланировать чекпоинт
                            self.scheduler.add_job(
                                _send_task_checkpoint_job,
                                trigger="date",
                                run_date=check_time,
                                args=[user.telegram_id, "pre_deadline"],
                                id=job_id,
                                replace_existing=True,
                                misfire_grace_time=300,
                                max_instances=1
                            )
                            
                            logger.debug(f"Scheduled pre-deadline checkpoint for task {task.id} at {check_time} (user {user.telegram_id})")
            
            # НЕ создаём дублирующий no_tasks_checkpoint — проактивные сообщения
            # уже планируются через _reschedule_proactive_check (job_id=proactive_{user_id}).
            # Два джоба на одно время = два сообщения одновременно.
            logger.debug(f"Skipping no-tasks checkpoint for user {user.telegram_id} — handled by proactive scheduler")
                
        finally:
            db.close()

    async def _reschedule_proactive_check(self, user_id: int, task_count: int = 0):
        """Перепланирование следующей проактивной проверки с СОКРАЩЁННЫМИ интервалами.
        
        Новые интервалы (были 4-10ч, стали 2-4ч):
        - 0 задач: 2ч (мотивация к планированию)
        - 1-3: 2.5ч
        - 4-7: 3ч
        - 8-12: 3.5ч
        - 13+: 4ч
        """
        import random
        
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
            job_id = f"proactive_{user.telegram_id}"
            
            # Сокращённые адаптивные интервалы
            if task_count == 0:
                base_hours = 2
            elif task_count <= 3:
                base_hours = 2.5
            elif task_count <= 7:
                base_hours = 3
            elif task_count <= 12:
                base_hours = 3.5
            else:
                base_hours = 4
            
            # Случайность ±30 минут
            random_offset_minutes = random.randint(-30, 30)
            hours_with_variance = base_hours + (random_offset_minutes / 60.0)
            
            # Привязка к последнему сообщению пользователя
            last_user_message = db.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == "user"
            ).order_by(Interaction.created_at.desc()).first()
            
            if last_user_message:
                next_check_time = last_user_message.created_at.replace(tzinfo=pytz.UTC) + timedelta(hours=hours_with_variance)
            else:
                next_check_time = datetime.now(pytz.UTC) + timedelta(hours=hours_with_variance)
            
            # Убедимся, что время в будущем
            now_utc = datetime.now(pytz.UTC)
            if next_check_time <= now_utc:
                next_check_time = now_utc + timedelta(hours=hours_with_variance)
            
            # Проверка разрешённого диапазона (10:00 - 22:00)
            next_check_local = next_check_time.astimezone(user_tz)
            
            if next_check_local.hour < PROACTIVE_SEND_START_HOUR:
                next_check_local = next_check_local.replace(hour=PROACTIVE_SEND_START_HOUR, minute=0, second=0, microsecond=0)
            elif next_check_local.hour >= PROACTIVE_NO_SEND_START_HOUR:
                next_check_local = (next_check_local + timedelta(days=1)).replace(hour=PROACTIVE_SEND_START_HOUR, minute=0, second=0, microsecond=0)
            
            if next_check_local <= datetime.now(user_tz):
                next_check_local = (datetime.now(user_tz) + timedelta(days=1)).replace(hour=PROACTIVE_SEND_START_HOUR, minute=0, second=0, microsecond=0)
            
            self.scheduler.add_job(
                _check_and_send_proactive_job,
                trigger=DateTrigger(run_date=next_check_local, timezone=user_tz),
                args=[user.telegram_id],
                id=job_id,
                replace_existing=True,
                max_instances=1
            )
            logger.debug(f"Rescheduled proactive for user {user.telegram_id} at {next_check_local} ({base_hours}h base + {random_offset_minutes}m, {task_count} tasks)")
        finally:
            db.close()

    def schedule_overdue_checks(self):
        """Планирование проверок просроченных задач каждые 15 минут"""
        from apscheduler.triggers.interval import IntervalTrigger
        
        db = Session()
        try:
            users = db.query(User).all()
            logger.info(f"Scheduling overdue checks for {len(users)} users")
            for user in users:
                job_id = f"overdue_{user.telegram_id}"
                
                # Проверяем, существует ли уже такой джоб
                if self.scheduler.get_job(job_id):
                    logger.debug(f"Overdue check job {job_id} already exists, skipping")
                    continue
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
                
                # Планируем проверки просроченных задач
                # Если интервал >= 60 минут, используем часовой триггер
                if OVERDUE_CHECK_INTERVAL_MINUTES >= 60:
                    hours_interval = OVERDUE_CHECK_INTERVAL_MINUTES // 60
                    self.scheduler.add_job(
                        _check_and_send_overdue_reminder_job,
                        trigger="cron",
                        hour=f"*/{hours_interval}",
                        minute="0",
                        timezone=user_tz,
                        args=[user.telegram_id],
                        id=job_id,
                        replace_existing=True,
                        misfire_grace_time=30
                    )
                else:
                    # Для интервалов < 60 минут используем минутный триггер
                    self.scheduler.add_job(
                        _check_and_send_overdue_reminder_job,
                        trigger="cron",
                        minute=f"*/{OVERDUE_CHECK_INTERVAL_MINUTES}",
                        timezone=user_tz,
                        args=[user.telegram_id],
                        id=job_id,
                        replace_existing=True,
                        misfire_grace_time=30
                    )
                logger.debug(f"Scheduled overdue check for user {user.telegram_id}")
        finally:
            db.close()

    async def send_proactive_message(self, user_id: int, context: str = "general", task_count: int = 0, overdue_count: int = 0):
        """Отправка проактивного сообщения пользователю с проверками условий
        
        Args:
            user_id: ID пользователя
            context: Контекст сообщения (no_tasks, few_tasks, many_tasks, overdue_tasks, general)
            task_count: Количество задач
            overdue_count: Количество просроченных задач
        """
        from config import FREE_ACCESS_MODE
        
        # Проверить подписку - если нет доступа, не отправлять проактивное сообщение
        from subscription_service import check_subscription
        if not check_subscription(user_id):
            return
        
        # MUTEX: Проверить, не отправляется ли уже проактивное сообщение этому пользователю
        lock = _proactive_locks[user_id]
        if lock.locked():
            logger.info(f"[MUTEX] Proactive message already being sent to user {user_id}, skipping duplicate")
            return
        
        async with lock:
            db = Session()
            try:
                user = db.query(User).filter(User.telegram_id == user_id).first()
                if not user:
                    return
            
                # Проверить последнее взаимодействие - если было в последние 15 минут, не отправлять
                last_interaction = db.query(Interaction).filter(
                    Interaction.user_id == user.id
                ).order_by(Interaction.created_at.desc()).first()
                
                if last_interaction:
                    time_since_last = datetime.now(pytz.UTC) - last_interaction.created_at.replace(tzinfo=pytz.UTC)
                    if time_since_last < timedelta(minutes=LAST_INTERACTION_THRESHOLD_MINUTES):
                        # Недавно общались, пропустить проактивное сообщение
                        return
                
                # Проверить режим "не беспокоить"
                if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                    # Пользователь в режиме "не беспокоить", пропустить
                    return
                
                # Получить текущее время в UTC
                now_utc = datetime.now(pytz.UTC)
                
                # Проверить задачи на ближайшие 60 минут (в UTC)
                next_60_min_utc = now_utc + timedelta(minutes=PROACTIVE_CHECK_AHEAD_MINUTES)
                
                # Получить все pending задачи с reminder_time
                pending_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'pending',
                    Task.reminder_time.isnot(None)
                ).all()
                
                # Проверить, есть ли задачи с reminder_time в ближайшие 60 минут
                tasks_in_60_min = 0
                for task in pending_tasks:
                    # Сделать reminder_time aware с UTC, если он naive
                    reminder_time = task.reminder_time
                    if reminder_time.tzinfo is None:
                        reminder_time = pytz.UTC.localize(reminder_time)
                    
                    if now_utc <= reminder_time < next_60_min_utc:
                        tasks_in_60_min += 1
                
                # Также проверить активные задачи с estimated_duration (пользователь может быть занят)
                active_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress']),
                    Task.estimated_duration.isnot(None)
                ).all()
                
                busy_time = 0
                for task in active_tasks:
                    # Если задача создана недавно (последние 30 минут), учитывать её время
                    if task.created_at and (now_utc - task.created_at.replace(tzinfo=pytz.UTC)).total_seconds() < 1800:  # 30 мин
                        busy_time += task.estimated_duration or 0
                
                # Если пользователь занят (больше 10 минут в ближайшие 60 мин), не отправлять
                if tasks_in_60_min > 0 or busy_time > 10:
                    return
                
                # Получить все активные задачи для передачи в AI
                # Основные задачи пользователя
                user_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress'])
                )
                
                # Задачи, делегированные пользователем другим
                delegated_by_user = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.status.in_(['pending', 'in_progress'])
                )
                
                # Задачи, делегированные пользователю
                delegated_to_user = db.query(Task).filter(
                    Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                    Task.delegation_status == 'accepted',
                    Task.status.in_(['pending', 'in_progress'])
                )
                
                all_active_tasks = user_tasks.union(delegated_by_user).union(delegated_to_user).order_by(Task.reminder_time).all()
                
                # Добавить просроченные задачи (только основные и делегированные пользователю)
                overdue_tasks = db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'pending',
                    Task.reminder_time < now_utc
                ).union(
                    db.query(Task).filter(
                        Task.delegated_to_username.ilike((user.username or "").replace('@', '')),
                        Task.delegation_status == 'accepted',
                        Task.status == 'pending',
                        Task.reminder_time < now_utc
                    )
                ).order_by(Task.reminder_time).all()
                
                all_tasks = all_active_tasks + overdue_tasks
                
                # Проверить, когда было последнее проактивное сообщение (не чаще чем раз в 2 часа)
                last_proactive = db.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type.in_(["proactive", "ai"]),
                    Interaction.created_at > now_utc - timedelta(hours=2)
                ).order_by(Interaction.created_at.desc()).first()
                
                if last_proactive:
                    logger.info(f"Skipping proactive message for user {user_id} - last proactive/ai message was {last_proactive.created_at}")
                    return
                
                # Отправить проактивное сообщение с номером для разнообразия
                proactive_text = await self.generate_proactive_message(user_id, "general", task_count, overdue_count, all_tasks)
                
                # Сохранить проактивное сообщение в таблицу Interaction
                interaction = Interaction(
                    user_id=user.id,
                    message_type="proactive",
                    content=proactive_text
                )
                db.add(interaction)
                db.commit()
                logger.info(f"Saved proactive message to interaction history for user {user_id}")
                
                if self.bot:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=proactive_text
                    )
                else:
                    logger.info(f"[PROACTIVE] To user {user_id}: {proactive_text}")
            except Exception as e:
                logging.error(f"Failed to send proactive message to user {user_id}: {e}")
                db.rollback()
            finally:
                db.close()

    async def check_and_send_overdue_reminder(self, user_id: int):
        """Проверка и отправка напоминания о просроченных задачах
        
        Note: user_id here is actually telegram_id (passed from scheduler).
        """
        from datetime import datetime
        
        db = Session()
        try:
            # user_id is telegram_id from scheduler — resolve to internal DB user first
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                logger.warning(f"[OVERDUE_CHECK] User with telegram_id {user_id} not found")
                return
            
            now = datetime.utcnow()
            
            # Находим просроченные задачи пользователя (по внутреннему DB id)
            overdue_tasks = db.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress']),
                Task.due_date.isnot(None),
                Task.due_date < now
            ).all()
            
            logger.info(f"[OVERDUE_CHECK] User {user_id} (db id {user.id}): Found {len(overdue_tasks)} overdue tasks")
            for task in overdue_tasks:
                logger.info(f"[OVERDUE_CHECK] Task: {task.title}, due_date: {task.due_date}, status: {task.status}")
            
            if overdue_tasks:
                # Есть просроченные задачи - отправляем напоминание (user_id = telegram_id for bot.send_message)
                await self.send_overdue_reminder(user_id, overdue_tasks)
        finally:
            db.close()

    async def send_overdue_reminder(self, user_id: int, overdue_tasks: list):
        """Отправка напоминания о просроченных задачах с эскалацией"""
        from datetime import datetime
        
        db = Session()
        try:
            now = datetime.utcnow()
            
            # Перепроверяем просроченные задачи на момент отправки
            current_overdue_tasks = []
            for task in overdue_tasks:
                fresh_task = db.query(Task).filter_by(id=task.id).first()
                if fresh_task and fresh_task.status in ['pending', 'in_progress'] and fresh_task.due_date and fresh_task.due_date < now:
                    current_overdue_tasks.append(fresh_task)
            
            if not current_overdue_tasks:
                logger.info(f"[OVERDUE_SEND] No current overdue tasks for user {user_id}, skipping reminder")
                return
            
            logger.info(f"[OVERDUE_SEND] Sending reminder for {len(current_overdue_tasks)} tasks to user {user_id}")
            for task in current_overdue_tasks:
                logger.info(f"[OVERDUE_SEND] Task: {task.title}")
            
            # Обновляем счётчики напоминаний для просроченных задач
            for task in current_overdue_tasks:
                task.overdue_reminders_sent = (task.overdue_reminders_sent or 0) + 1
            db.commit()
            
            # Генерируем текст напоминания с учётом эскалации
            max_reminders = max(task.overdue_reminders_sent for task in current_overdue_tasks)
            overdue_text = await self.generate_overdue_reminder(user_id, current_overdue_tasks, escalation_level=max_reminders)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=overdue_text
                )
            else:
                logger.info(f"[OVERDUE] To user {user_id}: {overdue_text}")
        except Exception as e:
            logging.error(f"Failed to send overdue reminder to user {user_id}: {e}")
        finally:
            db.close()

    def schedule_delegation_check(self, task_id: int, check_time: datetime, delegator_id: int, recipient_id: int, task_title: str, check_type: str = "progress_request"):
        """Schedule delegation progress check"""
        logger = logging.getLogger(__name__)

        # Конвертируем naive datetime в aware с UTC
        if check_time.tzinfo is None:
            check_time = pytz.UTC.localize(check_time)

        logger.info(f"Scheduling delegation check for task {task_id}, type: {check_type}, delegator {delegator_id}, recipient {recipient_id}, time: {check_time}")

        # Проверяем, запущен ли scheduler
        if not self.scheduler.running:
            return

        job_id = f"delegation_check_{task_id}_{check_type}_{int(check_time.timestamp())}"
        trigger = DateTrigger(run_date=check_time, timezone=pytz.UTC)

        # Use jobstore-safe module-level wrapper
        self.scheduler.add_job(
            _send_delegation_check_job,
            trigger=trigger,
            args=[task_id, delegator_id, recipient_id, check_type],
            id=job_id,
            replace_existing=True
        )
        logger.info(f"Delegation check scheduled for {check_time}")

    async def send_delegation_check(self, task_id: int, delegator_id: int, recipient_id: int, check_type: str = "progress_request"):
        """Send delegation progress check/reminder"""
        import traceback
        logger = logging.getLogger(__name__)
        logger.info(f"=== STARTING DELEGATION CHECK for task {task_id}, type: {check_type} ===")

        from ai_integration.handlers import check_delegation_deadlines, generate_progress_request
        import asyncio

        db = Session()
        try:
            task = db.query(Task).filter_by(id=task_id).first()
            if not task:
                logger.warning(f"Task {task_id} not found for delegation check")
                return

            # Check if task is still delegated and not completed
            if task.delegation_status != "accepted" or task.status == "completed":
                logger.info(f"Task {task_id} no longer needs delegation check (status: {task.delegation_status}, task status: {task.status})")
                return

            if check_type == "progress_request":
                # Request progress update from recipient
                try:
                    current_time = datetime.now(timezone.utc)
                    time_until_deadline = task.reminder_time - current_time
                    hours_remaining = int(time_until_deadline.total_seconds() / 3600)

                    if hours_remaining > 24:
                        time_desc = f"{hours_remaining // 24} дней"
                    else:
                        time_desc = f"{hours_remaining} часов"

                    # Generate AI-powered progress request
                    progress_request = await generate_progress_request(
                        task.title,
                        "delegator",  # We'll get the actual username from DB
                        time_desc,
                        recipient_id
                    )

                    if progress_request:
                        message = f"📊 {progress_request}\n\nЗадача: {task.title}"
                    else:
                        message = f"🤔 Как продвигается задача '{task.title}'?\n\nОсталось времени: {time_desc}\n\nПожалуйста, обнови статус выполнения."

                    if self.bot:
                        await self.bot.send_message(
                            chat_id=recipient_id,
                            text=message
                        )
                        logger.info(f"Sent progress request to recipient {recipient_id} for task {task_id}")
                        
                        # Also notify delegator about the progress check
                        try:
                            delegator_message = f"📋 Отправлен запрос о прогрессе по задаче '{task.title}'\n\nОжидаем ответа от исполнителя."
                            await self.bot.send_message(
                                chat_id=delegator_id,
                                text=delegator_message
                            )
                            logger.info(f"Notified delegator {delegator_id} about progress request for task {task_id}")
                                
                        except Exception as e:
                            logger.error(f"Failed to notify delegator: {e}")

                except Exception as e:
                    logger.error(f"Failed to send progress request: {e}")

            elif check_type == "overdue_reminder":
                # Handle overdue tasks (existing logic)
                logger.info(f"Running overdue check for task {task_id}")
                check_delegation_deadlines()

            else:
                logger.warning(f"Unknown check_type: {check_type} for task {task_id}")

        except Exception as e:
            logger.error(f"❌ Critical error in send_delegation_check for task {task_id}: {type(e).__name__}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
        finally:
            db.close()

    async def update_user_avatars(self):
        """Update avatars for all users from Telegram API"""
        from main import get_user_avatar_url
        logger = logging.getLogger(__name__)

        if not self.bot:
            logger.debug("Bot not available, skipping avatar updates")
            return

        db = Session()
        try:
            users = db.query(User).filter(User.telegram_id.isnot(None)).all()
            logger.info(f"Updating avatars for {len(users)} users")

            updated_count = 0
            for user in users:
                try:
                    updated_avatar_url = await get_user_avatar_url(self.bot, user.telegram_id)
                    if updated_avatar_url and updated_avatar_url != user.photo_url:
                        user.photo_url = updated_avatar_url
                        db.commit()
                        updated_count += 1
                        logger.info(f"Updated avatar for user {user.telegram_id}")
                except Exception as e:
                    logger.error(f"Error updating avatar for user {user.telegram_id}: {e}")

            logger.info(f"Avatar update completed: {updated_count} users updated")

        except Exception as e:
            logger.error(f"Error in update_user_avatars: {e}")
        finally:
            db.close()

    def schedule_avatar_updates(self):
        """Schedule periodic avatar updates from Telegram"""
        from apscheduler.triggers.cron import CronTrigger
        logger = logging.getLogger(__name__)

        job_id = "avatar_update"

        # Проверяем, существует ли уже такой джоб
        if self.scheduler.get_job(job_id):
            logger.debug(f"Avatar update job {job_id} already exists, skipping")
            return

        # Schedule avatar updates once a day at 3 AM
        self.scheduler.add_job(
            _update_user_avatars_job,
            trigger=CronTrigger(hour=3, minute=0),
            id=job_id,
            name="Update user avatars from Telegram (daily)",
            replace_existing=True
        )
        logger.info("Scheduled avatar updates once a day at 3:00 AM")

    def schedule_delegation_checks(self):
        """Schedule periodic delegation deadline checks"""
        from apscheduler.triggers.interval import IntervalTrigger
        logger = logging.getLogger(__name__)

        job_id = "delegation_deadline_check"

        # Проверяем, существует ли уже такой джоб
        if self.scheduler.get_job(job_id):
            logger.debug(f"Delegation check job {job_id} already exists, skipping")
            return

        # Schedule daily delegation deadline checks at 9 AM UTC
        self.scheduler.add_job(
            check_delegation_deadlines,
            trigger="cron",
            hour=9,
            minute=0,
            id=job_id,
            replace_existing=True
        )
        logger.info("Scheduled daily delegation deadline checks at 9:00 UTC")

    def schedule_recurring_tasks(self):
        """Schedule creation of new instances for recurring tasks"""
        logger = logging.getLogger(__name__)
        db = Session()
        try:
            # Find all recurring tasks that are still active
            recurring_tasks = db.query(Task).filter(
                Task.is_recurring == True,
                Task.recurrence_end_date.is_(None) | (Task.recurrence_end_date > datetime.now(pytz.UTC))
            ).all()

            logger.info(f"Found {len(recurring_tasks)} active recurring tasks")

            for recurring_task in recurring_tasks:
                try:
                    self._schedule_next_recurring_instance(recurring_task, db)
                except Exception as e:
                    logger.error(f"Error scheduling recurring task {recurring_task.id}: {e}")

        except Exception as e:
            logger.error(f"Error in schedule_recurring_tasks: {e}")
        finally:
            db.close()

    def _schedule_next_recurring_instance(self, recurring_task, db):
        """Schedule the next instance of a recurring task"""
        logger = logging.getLogger(__name__)

        # Find the last created instance of this recurring task
        last_instance = db.query(Task).filter(
            Task.parent_task_id == recurring_task.id
        ).order_by(Task.reminder_time.desc()).first()

        if not last_instance:
            # No instances yet, create first one
            next_time = recurring_task.reminder_time
        else:
            # Calculate next time based on recurrence pattern
            next_time = self._calculate_next_recurrence_time(
                last_instance.reminder_time,
                recurring_task.recurrence_pattern,
                recurring_task.recurrence_interval
            )

        # Check if we're still within the recurrence period
        if recurring_task.recurrence_end_date and next_time > recurring_task.recurrence_end_date:
            logger.info(f"Recurring task {recurring_task.id} has reached end date")
            return

        # Check if instance already exists for this time
        existing_instance = db.query(Task).filter(
            Task.parent_task_id == recurring_task.id,
            Task.reminder_time == next_time
        ).first()

        if existing_instance:
            logger.info(f"Instance already exists for recurring task {recurring_task.id} at {next_time}")
            return

        # Create new instance
        new_instance = Task(
            user_id=recurring_task.user_id,
            title=recurring_task.title,
            description=recurring_task.description,
            reminder_time=next_time,
            parent_task_id=recurring_task.id
        )

        db.add(new_instance)
        db.commit()

        # Schedule reminder for the new instance
        if recurring_task.user and recurring_task.user.telegram_id:
            self.schedule_reminder(
                new_instance.id,
                next_time,
                recurring_task.user.telegram_id,
                new_instance.title
            )

        logger.info(f"Created new instance {new_instance.id} for recurring task {recurring_task.id} at {next_time}")

    def _calculate_next_recurrence_time(self, last_time, pattern, interval):
        """Calculate the next occurrence time based on pattern"""
        if pattern == 'daily':
            return last_time + timedelta(days=interval)
        elif pattern == 'weekly':
            return last_time + timedelta(weeks=interval)
        elif pattern == 'monthly':
            # Simple monthly calculation - add interval months
            year = last_time.year
            month = last_time.month + interval
            day = last_time.day

            # Handle month overflow
            while month > 12:
                year += 1
                month -= 12

            # Handle invalid days (e.g., Feb 30 -> Feb 28/29)
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            if day > last_day:
                day = last_day

            return last_time.replace(year=year, month=month, day=day)
        elif pattern == 'yearly':
            return last_time.replace(year=last_time.year + interval)
        else:
            # Default to daily
            return last_time + timedelta(days=interval)

    def schedule_recurring_task_checks(self):
        """Schedule periodic checks for creating new recurring task instances"""
        job_id = "recurring_tasks_check"
        self.scheduler.add_job(
            _schedule_recurring_tasks_job,
            trigger="interval",
            hours=1,  # Check every hour
            id=job_id,
            replace_existing=True
        )
        logger.info("Scheduled recurring tasks check every hour")

