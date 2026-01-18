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
from ai_integration import check_delegation_deadlines

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


async def _send_delegation_check_job(task_id: int, delegator_id: int, recipient_id: int, check_type: str = "progress_request"):
    """Jobstore-safe wrapper for delegation check"""
    if REMINDER_SERVICE:
        await REMINDER_SERVICE.send_delegation_check(task_id, delegator_id, recipient_id, check_type)
    else:
        logger.error("REMINDER_SERVICE not initialized; cannot send delegation check")



class ReminderService:
    def __init__(self, bot: Bot, ai_service=None):
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
                    
                    # Сохранить result check в историю чата (Redis)
                    try:
                        import json
                        from config import redis_client
                        if redis_client:
                            context_data = await redis_client.get(f"context:{user_id}")
                            if context_data:
                                context = json.loads(context_data.decode('utf-8'))
                            else:
                                context = []
                            
                            # Добавляем result check как сообщение от AI
                            context.append({"user": "", "agent": result_text})
                            if len(context) > 10:
                                context = context[-10:]
                            
                            await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                            logger.info(f"Saved result check to chat context for user {user_id}")
                    except Exception as e:
                        logger.error(f"Failed to save result check to context: {e}")
                        
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
            reminder_text = await self.generate_reminder(user_id, task_title)
            logger.info(f"Reminder text generated: {reminder_text[:100]}...")
            
            # Сохранить напоминание в историю чата (Redis)
            try:
                import json
                from config import redis_client
                if redis_client:
                    context_data = await redis_client.get(f"context:{user_id}")
                    if context_data:
                        context = json.loads(context_data.decode('utf-8'))
                    else:
                        context = []
                    
                    # Добавляем напоминание как сообщение от AI
                    context.append({"user": "", "agent": reminder_text})
                    if len(context) > 10:
                        context = context[-10:]
                    
                    await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                    logger.info(f"Saved reminder to chat context for user {user_id}")
            except Exception as e:
                logger.error(f"Failed to save reminder to context: {e}")
            
            # Сохранить напоминание в таблицу Interaction
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
            report_text = await self.generate_daily_report(user_id)
            
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
        """Планирование чекпоинтов для задач по принципу 1/3-2/3 времени вместо периодических проверок"""
        from models import Session
        from models import User
        
        db = Session()
        try:
            users = db.query(User).all()
            logger.info(f"Scheduling task checkpoints for {len(users)} users")
            for user in users:
                self.schedule_task_checkpoints(user.telegram_id)
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
                await self._reschedule_proactive_check(user_id, has_tasks=False, urgent=False)  # Без задач, так как не проверяли
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
            
            # Проверить, не было ли уже отправлено проактивное сообщение в ближайший час
            recent_proactive = db.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'proactive',  # Предполагаем, что есть поле message_type
                Interaction.created_at >= now_utc - timedelta(hours=1)
            ).first()
            
            if recent_proactive:
                logger.debug(f"Proactive message was already sent in the last hour for user {user_id}, skipping")
                return
            
            # Новая логика: чекпоинты на 1/3, 2/3 и просроченные задачи
            total_pending_tasks = len(pending_tasks)
            
            # 1. Если есть задачи в ближайший час - не отправлять (пользователь активен)
            if tasks_in_60_min > 0:
                return
            
            # 2. Проверить, есть ли просроченные задачи (3/3 - overdue)
            overdue_tasks = []
            for task in pending_tasks:
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = pytz.UTC.localize(reminder_time)
                
                if reminder_time < now_utc:
                    overdue_tasks.append(task)
            
            if overdue_tasks:
                # Есть просроченные задачи - отправить напоминание
                await self.send_proactive_message(user_id)
                return
            
            # 3. Если есть задачи, но не просроченные - отправить обычное проактивное сообщение
            if total_pending_tasks > 0:
                await self.send_proactive_message(user_id)
                return
            
            # 4. Если совсем нет задач - отправить предложение создать задачу (не чаще раза в час)
            if total_pending_tasks == 0:
                await self.send_proactive_message(user_id)
        finally:
            db.close()

    def schedule_task_checkpoints(self, user_id: int):
        """Планирование чекпоинтов для задач пользователя по принципу 1/3-2/3 времени
        
        Аналогично делегированным задачам:
        - 1/3 времени до reminder_time: первый чекпоинт
        - 2/3 времени до reminder_time: второй чекпоинт  
        - Через 1 час после reminder_time: чекпоинт для просроченных задач
        """
        from models import Session
        from models import Task, User
        
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
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            current_time = datetime.now(pytz.UTC)
            
            for task in pending_tasks:
                # Сделать reminder_time aware с UTC
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = pytz.UTC.localize(reminder_time)
                
                # Пропустить задачи, которые уже просрочены более чем на день
                if reminder_time < current_time - timedelta(days=1):
                    continue
                
                # Рассчитать чекпоинты по принципу 1/3-2/3 от времени до reminder_time
                time_until_reminder = reminder_time - current_time
                
                if time_until_reminder.total_seconds() <= 0:
                    # Задача просрочена - чекпоинт через 1 час после reminder_time
                    checkpoint_time = reminder_time + timedelta(hours=1)
                    checkpoint_type = "overdue"
                else:
                    # Задача не просрочена - чекпоинты на 1/3 и 2/3 времени до reminder_time
                    check_times = [
                        current_time + (time_until_reminder * 1 / 3),  # 1/3 point
                        current_time + (time_until_reminder * 2 / 3),  # 2/3 point
                    ]
                    
                    # Планировать оба чекпоинта
                    for i, check_time in enumerate(check_times, 1):
                        if check_time > current_time:
                            job_id = f"task_checkpoint_{task.id}_{i}_{user.telegram_id}"
                            
                            # Удалить существующий джоб для этой задачи и чекпоинта
                            if self.scheduler.get_job(job_id):
                                self.scheduler.remove_job(job_id)
                            
                            # Запланировать новый чекпоинт
                            self.scheduler.add_job(
                                _check_and_send_proactive_job,
                                trigger="date",
                                run_date=check_time,
                                args=[user.telegram_id],
                                id=job_id,
                                replace_existing=True,
                                misfire_grace_time=300  # 5 минут на опоздание
                            )
                            
                            logger.debug(f"Scheduled task checkpoint {i}/2 for task {task.id} at {check_time} (user {user.telegram_id})")
                    
                    # Для просроченных задач - чекпоинт через 1 час после reminder_time
                    checkpoint_time = reminder_time + timedelta(hours=1)
                    checkpoint_type = "overdue"
                
                # Планировать чекпоинт для просроченных задач
                if checkpoint_time > current_time:
                    job_id = f"task_overdue_{task.id}_{user.telegram_id}"
                    
                    # Удалить существующий джоб
                    if self.scheduler.get_job(job_id):
                        self.scheduler.remove_job(job_id)
                    
                    # Запланировать чекпоинт для просроченных задач
                    self.scheduler.add_job(
                        _check_and_send_proactive_job,
                        trigger="date",
                        run_date=checkpoint_time,
                        args=[user.telegram_id],
                        id=job_id,
                        replace_existing=True,
                        misfire_grace_time=300
                    )
                    
                    logger.debug(f"Scheduled overdue checkpoint for task {task.id} at {checkpoint_time} (user {user.telegram_id})")
            
            # Также запланировать общий чекпоинт для случаев без задач (раз в час)
            no_tasks_job_id = f"no_tasks_checkpoint_{user.telegram_id}"
            if not self.scheduler.get_job(no_tasks_job_id):
                next_hour = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                self.scheduler.add_job(
                    _check_and_send_proactive_job,
                    trigger="date", 
                    run_date=next_hour,
                    args=[user.telegram_id],
                    id=no_tasks_job_id,
                    replace_existing=True
                )
                logger.debug(f"Scheduled no-tasks checkpoint for user {user.telegram_id} at {next_hour}")
                
        finally:
            db.close()

    async def _reschedule_proactive_check(self, user_id: int, has_tasks: bool, urgent: bool = False):
        """Перепланирование следующей проактивной проверки с правильным интервалом
        
        Args:
            user_id: ID пользователя
            has_tasks: Есть ли задачи у пользователя
            urgent: Задачи в urgent состоянии (осталось <1/3 времени до дедлайна)
        """
        from models import Session
        from models import User
        
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                return
            
            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
            job_id = f"proactive_{user.telegram_id}"
            
            # Выбрать интервал в зависимости от наличия задач и urgency
            if urgent:
                # Urgent задачи - более частые проверки (каждые 15 минут)
                interval_minutes = 15
            elif has_tasks:
                # Обычные задачи - стандартный интервал
                interval_minutes = PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES
            else:
                # Нет задач - более редкие проверки
                interval_minutes = PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES
            
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
                _check_and_send_proactive_job,
                trigger="cron",
                minute=minute,
                hour=hour,
                timezone=user_tz,
                args=[user.telegram_id],
                id=job_id,
                replace_existing=True
            )
            logger.debug(f"Rescheduled proactive check for user {user.telegram_id} with {interval_minutes}min interval (has_tasks={has_tasks}, urgent={urgent})")
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
                proactive_text = await self.generate_proactive_message(user_id)
                
                # Сохранить проактивное сообщение в историю чата (Redis)
                try:
                    import json
                    from config import redis_client
                    if redis_client:
                        context_data = await redis_client.get(f"context:{user_id}")
                        if context_data:
                            context = json.loads(context_data.decode('utf-8'))
                        else:
                            context = []
                        
                        # Добавляем проактивное сообщение как сообщение от AI
                        context.append({"user": "", "agent": proactive_text})
                        if len(context) > 10:
                            context = context[-10:]
                        
                        await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                        logger.info(f"Saved proactive message to chat context for user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to save proactive message to context: {e}")
                
                # Сохранить проактивное сообщение в таблицу Interaction
                try:
                    interaction = Interaction(
                        user_id=user.id,
                        message_type="proactive",  # Отмечаем как проактивное сообщение
                        content=proactive_text
                    )
                    db.add(interaction)
                    db.commit()
                    logger.info(f"Saved proactive message to interaction history for user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to save proactive message to interactions: {e}")
                    db.rollback()
                
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
            overdue_text = await self.generate_overdue_reminder(user_id, overdue_tasks, escalation_level=max_reminders)
            
            # Сохранить overdue reminder в историю чата (Redis)
            try:
                import json
                from config import redis_client
                if redis_client:
                    context_data = await redis_client.get(f"context:{user_id}")
                    if context_data:
                        context = json.loads(context_data.decode('utf-8'))
                    else:
                        context = []
                    
                    # Добавляем overdue reminder как сообщение от AI
                    context.append({"user": "", "agent": overdue_text})
                    if len(context) > 10:
                        context = context[-10:]
                    
                    await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                    logger.info(f"Saved overdue reminder to chat context for user {user_id}")
            except Exception as e:
                logger.error(f"Failed to save overdue reminder to context: {e}")
            
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

    def schedule_delegation_check(self, task_id: int, check_time: datetime, delegator_id: int, recipient_id: int, task_title: str, check_type: str = "progress_request"):
        """Schedule delegation progress check"""
        import logging
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
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.info(f"=== STARTING DELEGATION CHECK for task {task_id}, type: {check_type} ===")

        from models import Session, Task
        from ai_integration.handlers import check_delegation_deadlines, generate_progress_request
        from ai_integration import chat_with_ai
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
                        
                        # Сохранить progress request в историю чата получателя (Redis)
                        try:
                            import json
                            from config import redis_client
                            if redis_client:
                                context_data = await redis_client.get(f"context:{recipient_id}")
                                if context_data:
                                    context = json.loads(context_data.decode('utf-8'))
                                else:
                                    context = []
                                
                                # Добавляем progress request как сообщение от AI
                                context.append({"user": "", "agent": message})
                                if len(context) > 10:
                                    context = context[-10:]
                                
                                await redis_client.set(f"context:{recipient_id}", json.dumps(context).encode('utf-8'))
                                logger.info(f"Saved progress request to chat context for recipient {recipient_id}")
                        except Exception as e:
                            logger.error(f"Failed to save progress request to recipient context: {e}")

                        # Also notify delegator about the progress check
                        try:
                            delegator_message = f"📋 Отправлен запрос о прогрессе по задаче '{task.title}'\n\nОжидаем ответа от исполнителя."
                            await self.bot.send_message(
                                chat_id=delegator_id,
                                text=delegator_message
                            )
                            logger.info(f"Notified delegator {delegator_id} about progress request for task {task_id}")
                            
                            # Сохранить уведомление delegator'у в историю чата (Redis)
                            try:
                                if redis_client:
                                    context_data = await redis_client.get(f"context:{delegator_id}")
                                    if context_data:
                                        context = json.loads(context_data.decode('utf-8'))
                                    else:
                                        context = []
                                    
                                    # Добавляем уведомление как сообщение от AI
                                    context.append({"user": "", "agent": delegator_message})
                                    if len(context) > 10:
                                        context = context[-10:]
                                    
                                    await redis_client.set(f"context:{delegator_id}", json.dumps(context).encode('utf-8'))
                                    logger.info(f"Saved delegator notification to chat context for delegator {delegator_id}")
                            except Exception as e:
                                logger.error(f"Failed to save delegator notification to context: {e}")
                                
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

    def schedule_delegation_checks(self):
        """Schedule periodic delegation deadline checks"""
        from apscheduler.triggers.interval import IntervalTrigger
        import logging
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

