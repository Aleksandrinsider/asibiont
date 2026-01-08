from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot
from models import Session
from models import Task, User
from datetime import datetime, timedelta
import pytz
import logging
from config import DAILY_REPORT_HOUR, PROACTIVE_CHECK_INTERVAL_MINUTES, OVERDUE_CHECK_INTERVAL_MINUTES, PROACTIVE_CHECK_AHEAD_MINUTES, LAST_INTERACTION_THRESHOLD_MINUTES

logger = logging.getLogger(__name__)

class ReminderService:
    def __init__(self, bot: Bot, ai_service=None):
        self.bot = bot
        self.ai_service = ai_service
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)

    async def start(self):
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
                if task.reminder_time > datetime.utcnow():
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
                    if result_check_time > datetime.utcnow():
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
        trigger = DateTrigger(run_date=reminder_time, timezone=pytz.UTC)
        self.scheduler.add_job(
            self.send_reminder,
            trigger=trigger,
            args=[user_id, task_title, task_id],
            id=f"reminder_{task_id}",
            replace_existing=True
        )
        logger.info(f"Reminder scheduled for {reminder_time} (in {(reminder_time - datetime.now(pytz.UTC)).total_seconds() / 60:.1f} minutes)")

    def schedule_result_check(self, task_id: int, result_check_time: datetime, user_id: int, task_title: str):
        # Конвертируем naive datetime в aware с UTC
        if result_check_time.tzinfo is None:
            result_check_time = pytz.UTC.localize(result_check_time)
        
        trigger = DateTrigger(run_date=result_check_time, timezone=pytz.UTC)
        self.scheduler.add_job(
            self.send_result_check,
            trigger=trigger,
            args=[user_id, task_title, task_id],
            id=f"result_check_{task_id}",
            replace_existing=True
        )

    async def send_result_check(self, user_id: int, task_title: str, task_id: int):
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять проверку результата
        if not check_subscription(user_id):
            return
        
        try:
            result_text = await self.ai_service.generate_result_check(user_id, task_title)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=result_text
                )
            else:
                # Для тестов - вывод в консоль
                logger.info(f"[RESULT CHECK SENT] To user {user_id}: {result_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send result check for task {task_id}: {e}")
        
        # Обновить статус в БД
        db = Session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.result_check_sent = True
                db.commit()
                
                # Установить pending_action для обработки ответа пользователя
                user = db.query(User).filter(User.telegram_id == user_id).first()
                if user:
                    import json
                    pending_data = {
                        "type": "result_check_response",
                        "task_id": task_id,
                        "task_title": task_title
                    }
                    user.pending_action = json.dumps(pending_data)
                    logger.info(f"Устанавливаем pending_action: {user.pending_action}")
                    db.commit()
                    db.refresh(user)  # Обновляем объект из БД
                    logger.info(f"После commit pending_action: {user.pending_action}")
                    logger.info("pending_action установлен")
                else:
                    logger.info(f"Пользователь с telegram_id {user_id} не найден")
        except Exception as e:
            import logging
            logging.error(f"Failed to update result_check_sent for task {task_id}: {e}")
            db.rollback()
        finally:
            db.close()

    async def send_reminder(self, user_id: int, task_title: str, task_id: int):
        import logging
        logger = logging.getLogger(__name__)
        logger.info("=== STARTING REMINDER SEND ===")
        logger.info(f"Sending reminder for task {task_id}, user {user_id}, title: {task_title}")
        from subscription_service import check_subscription
        from models import Interaction
        
        # Для напоминаний всегда отправляем, независимо от подписки
        # if not check_subscription(user_id):
        #     logger.info(f"Subscription check failed for user {user_id}")
        #     return
        
        try:
            reminder_text = await self.ai_service.generate_reminder(user_id, task_title)
            
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
            finally:
                db.close()
            
            if self.bot:
                result = await self.bot.send_message(
                    chat_id=user_id,
                    text=reminder_text
                )
                import logging
                logging.info(f"Reminder sent successfully to user {user_id} for task {task_id}, message_id: {result.message_id}")
            else:
                # Для тестов - вывод в консоль
                logger.info(f"[REMINDER SENT] To user {user_id}: {reminder_text}")
                import logging
                logging.info(f"Reminder printed to console for user {user_id} for task {task_id}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send reminder for task {task_id}: {e}")
        
        # Обновить статус в БД даже если отправка не удалась
        db = Session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.reminder_sent = True
                db.commit()
        except Exception as e:
            import logging
            logging.error(f"Failed to update reminder_sent for task {task_id}: {e}")
            db.rollback()
        finally:
            db.close()

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
                self.scheduler.add_job(
                    self.send_daily_report,
                    trigger="cron",
                    hour=DAILY_REPORT_HOUR,
                    minute=0,
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=job_id,
                    replace_existing=True
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
                message = f"✅ Задача '{task.title}' выполнена @{recipient.username}"
            else:
                message = f"📊 Напоминание: задача '{task.title}' для @{recipient.username}, дедлайн: {task.reminder_time.strftime('%d.%m %H:%M') if task.reminder_time else 'не указан'}"
            
            if self.bot:
                await self.bot.send_message(delegator.telegram_id, message)
        except Exception as e:
            import logging
            logging.error(f"Failed to send delegation progress update: {e}")
        finally:
            db.close()
    def schedule_proactive_checks(self):
        """Планирование проактивных проверок каждые 30 минут"""
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
                
                # Планируем проактивные проверки каждые 30 минут
                self.scheduler.add_job(
                    self.check_and_send_proactive,
                    trigger="cron",
                    minute=f"*/{PROACTIVE_CHECK_INTERVAL_MINUTES}",
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=job_id,
                    replace_existing=True
                )
                logger.debug(f"Scheduled proactive check for user {user.telegram_id}")
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
            
            if tasks_in_60_min == 0:
                # Нет задач - отправить проактивное сообщение
                await self.send_proactive_message(user_id)
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
                    self.check_and_send_overdue_reminder,
                    trigger="cron",
                    minute=f"*/{OVERDUE_CHECK_INTERVAL_MINUTES}",
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=job_id,
                    replace_existing=True
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
        """Отправка напоминания о просроченных задачах"""
        try:
            overdue_text = await self.ai_service.generate_overdue_reminder(user_id, overdue_tasks)
            
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

