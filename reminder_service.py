from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from aiogram import Bot
from models import Session
from models import Task, User
from config import DATABASE_URL
from datetime import datetime, timedelta
import pytz
import logging
import json
from config import DAILY_REPORT_HOUR, PROACTIVE_CHECK_INTERVAL_MINUTES, OVERDUE_CHECK_INTERVAL_MINUTES, PROACTIVE_CHECK_AHEAD_MINUTES, LAST_INTERACTION_THRESHOLD_MINUTES, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_NO_SEND_END_HOUR, PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES, PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES

logger = logging.getLogger(__name__)

# Singleton reference used by jobstore-safe wrapper functions
REMINDER_SERVICE = None

async def _send_reminder_job(task_id: int):
    from models import Session, Task
    db = Session()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task or not task.user:
            logger.warning(f"_send_reminder_job: task {task_id} or user not found")
            return
        user_id = task.user.telegram_id
        task_title = task.title
    finally:
        db.close()

    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_reminder(user_id, task_title, task_id)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send reminder")

async def _send_result_check_job(task_id: int):
    from models import Session, Task
    db = Session()
    try:
        task = db.query(Task).filter_by(id=task_id).first()
        if not task or not task.user:
            logger.warning(f"_send_result_check_job: task {task_id} or user not found")
            return
        user_id = task.user.telegram_id
        task_title = task.title
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



class ReminderService:
    def __init__(self, bot: Bot, ai_service=None):
        self.bot = bot
        self.ai_service = ai_service
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

    def schedule_existing_reminders(self):
        import logging
        logger = logging.getLogger(__name__)
        db = Session()
        try:
            tasks = db.query(Task).filter(Task.reminder_time.isnot(None), Task.reminder_sent == False).all()
            logger.info(f"Found {len(tasks)} tasks with reminders to schedule")
            for task in tasks:
                if task.reminder_time > datetime.now(pytz.UTC).replace(tzinfo=None):
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
                    if result_check_time > datetime.now(pytz.UTC).replace(tzinfo=None):
                        self.schedule_result_check(task.id, result_check_time, task.user.telegram_id, task.title)
        finally:
            db.close()

    def schedule_reminder(self, task_id: int, reminder_time: datetime, user_id: int, task_title: str):
        import logging
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
        import logging
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
            result_text = await self.ai_service.generate_result_check(user_id, task_title)
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
                        from datetime import datetime, timezone
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

    async def send_reminder(self, user_id: int, task_title: str, task_id: int):
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.info("=== STARTING REMINDER SEND ===")
        logger.info(f"Sending reminder for task {task_id}, user telegram_id {user_id}, title: {task_title}")
        from subscription_service import check_subscription
        from models import Interaction
        
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
                from models import User
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if not user:
                    logger.warning(f"User with telegram_id {user_id} not found in database - aborting reminder for task {task_id}")
                    return
                # Проверяем режим 'не беспокоить' для пользователя
                if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                    logger.info(f"User {user_id} in DND until {user.do_not_disturb_until}, skipping reminder for task {task_id}")
                    return
            finally:
                db.close()

            logger.info(f"Generating reminder text for task {task_id}...")
            reminder_text = await self.ai_service.generate_reminder(user_id, task_title)
            logger.info(f"Reminder text generated: {reminder_text[:100]}...")
            
            # Сохранить напоминание в историю чата
            db = Session()
            try:
                # Найти user.id по telegram_id
                from models import User
                user = db.query(User).filter_by(telegram_id=user_id).first()
                if user:
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
        """Планирование ежедневных отчетов в 22:00 по времени пользователя"""
        from models import Session
        from models import User
        
        db = Session()
        try:
            users = db.query(User).all()
            logger.info(f"Scheduling daily reports for {len(users)} users")
            for user in users:
                job_id = f"daily_report_{user.telegram_id}"
                
                # Проверяем, существует ли уже такой джоб
                if self.scheduler.get_job(job_id):
                    logger.debug(f"Daily report job {job_id} already exists, skipping")
                    continue
                
                # Получить timezone пользователя
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Планируем ежедневный отчет в 22:00 по времени пользователя
                # Use jobstore-safe wrapper function
                self.scheduler.add_job(
                    _send_daily_report_job,
                    trigger="cron",
                    hour=DAILY_REPORT_HOUR,
                    minute=0,
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=30
                )
                logger.debug(f"Scheduled daily report for user {user.telegram_id}")
        finally:
            db.close()

    async def send_daily_report(self, user_id: int):
        """Отправка ежедневного отчета пользователю"""
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять отчет
        if not check_subscription(user_id):
            return
        
        try:
            report_text = await self.ai_service.generate_daily_report(user_id)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=report_text
                )
            else:
                logger.info(f"[DAILY REPORT] To user {user_id}: {report_text}")
        except Exception as e:
            import logging
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
            import logging
            logging.error(f"Failed to send delegation progress update: {e}")
        finally:
            db.close()
    def schedule_proactive_checks(self):
        """Планирование проактивных проверок с динамическим интервалом"""
        from models import Session
        from models import User
        from apscheduler.triggers.interval import IntervalTrigger
        
        db = Session()
        try:
            users = db.query(User).all()
            logger.info(f"Scheduling proactive checks for {len(users)} users")
            for user in users:
                job_id = f"proactive_check_{user.telegram_id}"
                
                # Проверяем, существует ли уже такой джоб
                if self.scheduler.get_job(job_id):
                    logger.debug(f"Proactive check job {job_id} already exists, skipping")
                    continue
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Вычисляем параметры cron триггера на основе интервала
                interval = PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES
                if interval >= 60:
                    hour_step = interval // 60
                    minute = '0'
                    hour = f'10-21/{hour_step}' if hour_step > 1 else '10-21'
                else:
                    minute = f'*/{interval}'
                    hour = '10-21'
                
                # Планируем проактивные проверки с интервалом без задач (по умолчанию)
                # Use jobstore-safe wrapper for proactive checks
                self.scheduler.add_job(
                    _check_and_send_proactive_job,
                    trigger="cron",
                    minute=minute,
                    hour=hour,
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=job_id,
                    replace_existing=True,
                    misfire_grace_time=30
                )
                logger.debug(f"Scheduled proactive check for user {user.telegram_id} with {PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES}min interval")
        finally:
            db.close()

    async def check_and_send_proactive(self, user_id: int):
        """Проверка и отправка проактивного сообщения, если нет задач на ближайший час"""
        from models import Session
        from models import Task, User, Interaction
        from datetime import timedelta
        from config import FREE_ACCESS_MODE
        
        # Проверить подписку - если нет доступа, не отправлять проактивное сообщение
        from subscription_service import check_subscription
        if not check_subscription(user_id):
            return
        
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            # Проверить время - не отправлять с 22:00 до 10:00 по времени пользователя
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            now_user_time = datetime.now(user_tz)
            current_hour = now_user_time.hour
            
            # Проверить, находится ли текущее время в периоде запрета (22:00 - 10:00)
            if PROACTIVE_NO_SEND_START_HOUR <= current_hour or current_hour < PROACTIVE_NO_SEND_END_HOUR:
                # Время запрета, перепланировать следующий чек с правильным интервалом
                await self._reschedule_proactive_check(user_id, has_tasks=False)  # Без задач, так как не проверяли
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
            
            # Если есть задачи с reminder_time в ближайшие 60 минут, не отправлять проактивное сообщение
            if tasks_in_60_min > 0:
                await self._reschedule_proactive_check(user_id, has_tasks=True)
                return
            
            if tasks_in_60_min == 0:
                # Нет задач на ближайший час - отправить проактивное сообщение, чтобы вернуть пользователя к активности
                await self.send_proactive_message(user_id)
                await self._reschedule_proactive_check(user_id, has_tasks=False)
        finally:
            db.close()

    async def _reschedule_proactive_check(self, user_id: int, has_tasks: bool):
        """Перепланирование следующей проактивной проверки с правильным интервалом"""
        from models import Session
        from models import User
        
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            job_id = f"proactive_{user.telegram_id}"
            
            # Выбрать интервал в зависимости от наличия задач
            interval_minutes = PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES if has_tasks else PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES
            
            # Вычисляем параметры cron триггера на основе интервала
            if interval_minutes >= 60:
                hour_step = interval_minutes // 60
                minute = '0'
                hour = f'10-21/{hour_step}' if hour_step > 1 else '10-21'
            else:
                minute = f'*/{interval_minutes}'
                hour = '10-21'
            
            # Перепланировать джоб с новым интервалом
            self.scheduler.add_job(
                self.check_and_send_proactive,
                trigger="cron",
                minute=minute,
                hour=hour,
                timezone=user_tz,
                args=[user.telegram_id],
                id=job_id,
                replace_existing=True
            )
            logger.debug(f"Rescheduled proactive check for user {user.telegram_id} with {interval_minutes}min interval (has_tasks={has_tasks})")
        finally:
            db.close()

    def schedule_overdue_checks(self):
        """Планирование проверок просроченных задач каждые 15 минут"""
        from models import Session
        from models import User
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
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Планируем проверки просроченных задач каждые 15 минут
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

    async def send_proactive_message(self, user_id: int):
        """Отправка проактивного сообщения пользователю с проверками условий"""
        from models import Session
        from models import Task, User, Interaction
        from datetime import timedelta
        from config import FREE_ACCESS_MODE
        
        # Проверить подписку - если нет доступа, не отправлять проактивное сообщение
        from subscription_service import check_subscription
        if not check_subscription(user_id):
            return
        
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
            
            # Отправить проактивное сообщение с номером для разнообразия
            try:
                proactive_text = await self.ai_service.generate_proactive_message(user_id)
                
                if self.bot:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=proactive_text
                    )
                else:
                    logger.info(f"[PROACTIVE] To user {user_id}: {proactive_text}")
            except Exception as e:
                import logging
                logging.error(f"Failed to send proactive message to user {user_id}: {e}")
        finally:
            db.close()

    async def check_and_send_overdue_reminder(self, user_id: int):
        """Проверка и отправка напоминания о просроченных задачах"""
        from models import Session
        from models import Task
        from datetime import datetime
        
        db = Session()
        try:
            now = datetime.utcnow()
            
            # Находим просроченные задачи пользователя
            overdue_tasks = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status.in_(['pending', 'in_progress']),
                Task.due_date.isnot(None),
                Task.due_date < now
            ).all()
            
            if overdue_tasks:
                # Есть просроченные задачи - отправляем напоминание
                await self.send_overdue_reminder(user_id, overdue_tasks)
        finally:
            db.close()

    async def send_overdue_reminder(self, user_id: int, overdue_tasks: list):
        """Отправка напоминания о просроченных задачах с эскалацией"""
        from models import Session, Task
        
        db = Session()
        try:
            # Обновляем счётчики напоминаний для просроченных задач
            for task in overdue_tasks:
                task.overdue_reminders_sent = (task.overdue_reminders_sent or 0) + 1
            db.commit()
            
            # Генерируем текст напоминания с учётом эскалации
            max_reminders = max(task.overdue_reminders_sent for task in overdue_tasks)
            overdue_text = await self.ai_service.generate_overdue_reminder(user_id, overdue_tasks, escalation_level=max_reminders)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=overdue_text
                )
            else:
                logger.info(f"[OVERDUE] To user {user_id}: {overdue_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send overdue reminder to user {user_id}: {e}")
        finally:
            db.close()

