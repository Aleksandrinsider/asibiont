from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot
from database import SessionLocal
from models import Task, User
from datetime import datetime, timedelta
import pytz

class ReminderService:
    def __init__(self, bot: Bot, ai_service=None):
        self.bot = bot
        self.ai_service = ai_service
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)

    def start(self):
        self.scheduler.start()
        self.schedule_existing_reminders()
        self.schedule_daily_reports()
        self.schedule_proactive_checks()
        self.schedule_overdue_checks()

    def schedule_existing_reminders(self):
        db = SessionLocal()
        try:
            tasks = db.query(Task).filter(Task.reminder_time.isnot(None), Task.reminder_sent == False).all()
            for task in tasks:
                if task.reminder_time > datetime.utcnow():
                    # Безопасная проверка наличия user
                    if task.user and task.user.telegram_id:
                        self.schedule_reminder(task.id, task.reminder_time, task.user.telegram_id, task.title)
            
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
        # Конвертируем naive datetime в aware с UTC
        if reminder_time.tzinfo is None:
            reminder_time = pytz.UTC.localize(reminder_time)
        
        trigger = DateTrigger(run_date=reminder_time, timezone=pytz.UTC)
        self.scheduler.add_job(
            self.send_reminder,
            trigger=trigger,
            args=[user_id, task_title, task_id],
            id=f"reminder_{task_id}",
            replace_existing=True
        )

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
                print(f"\n[RESULT CHECK SENT] To user {user_id}: {result_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send result check for task {task_id}: {e}")
        
        # Обновить статус в БД
        db = SessionLocal()
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
                    print(f"Устанавливаем pending_action: {user.pending_action}")
                    db.commit()
                    db.refresh(user)  # Обновляем объект из БД
                    print(f"После commit pending_action: {user.pending_action}")
                    print("pending_action установлен")
                else:
                    print(f"Пользователь с telegram_id {user_id} не найден")
        except Exception as e:
            import logging
            logging.error(f"Failed to update result_check_sent for task {task_id}: {e}")
            db.rollback()
        finally:
            db.close()

    async def send_reminder(self, user_id: int, task_title: str, task_id: int):
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять напоминание
        if not check_subscription(user_id):
            return
        
        try:
            reminder_text = await self.ai_service.generate_reminder(user_id, task_title)
            
            if self.bot:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=reminder_text
                )
            else:
                # Для тестов - вывод в консоль
                print(f"\n[REMINDER SENT] To user {user_id}: {reminder_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send reminder for task {task_id}: {e}")
        
        # Обновить статус в БД даже если отправка не удалась
        db = SessionLocal()
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
        from database import SessionLocal
        from models import User
        
        db = SessionLocal()
        try:
            users = db.query(User).all()
            for user in users:
                # Получить timezone пользователя
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Планируем ежедневный отчет в 22:00 по времени пользователя
                self.scheduler.add_job(
                    self.send_daily_report,
                    trigger="cron",
                    hour=22,
                    minute=0,
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=f"daily_report_{user.telegram_id}",
                    replace_existing=True
                )
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
                print(f"[DAILY REPORT] To user {user_id}: {report_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send daily report to user {user_id}: {e}")

    def schedule_proactive_checks(self):
        """Планирование проактивных проверок: первое через 30 мин, второе через 1 час, потом каждые 6 часов по времени пользователя"""
        from database import SessionLocal
        from models import User
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
        from datetime import timedelta
        
        db = SessionLocal()
        try:
            users = db.query(User).all()
            for user in users:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Получить текущее время в UTC
                now_utc = datetime.now(pytz.UTC)
                
                # Первое проактивное сообщение через 30 минут
                first_message_time = now_utc + timedelta(minutes=30)
                
                # Второе через 1 час
                second_message_time = now_utc + timedelta(hours=1)
                
                # Планируем первое сообщение
                self.scheduler.add_job(
                    self.send_proactive_message,
                    trigger=DateTrigger(run_date=first_message_time, timezone=pytz.UTC),
                    args=[user.telegram_id, 1],  # 1 - номер сообщения
                    id=f"proactive_{user.telegram_id}_1",
                    replace_existing=True
                )
                
                # Планируем второе сообщение
                self.scheduler.add_job(
                    self.send_proactive_message,
                    trigger=DateTrigger(run_date=second_message_time, timezone=pytz.UTC),
                    args=[user.telegram_id, 2],  # 2 - номер сообщения
                    id=f"proactive_{user.telegram_id}_2",
                    replace_existing=True
                )
                
                # Планируем последующие сообщения каждые 6 часов, начиная с 7 часов от сейчас
                for hours in range(7, 24, 6):  # 7, 13, 19 часов
                    message_time = now_utc + timedelta(hours=hours)
                    message_num = (hours // 6) + 2  # 3, 4, 5...
                    
                    self.scheduler.add_job(
                        self.send_proactive_message,
                        trigger=DateTrigger(run_date=message_time, timezone=pytz.UTC),
                        args=[user.telegram_id, message_num],
                        id=f"proactive_{user.telegram_id}_{message_num}",
                        replace_existing=True
                    )
        finally:
            db.close()

    async def check_and_send_proactive(self, user_id: int):
        """Проверка и отправка проактивного сообщения, если нет задач на ближайший час"""
        from database import SessionLocal
        from models import Task, User, Interaction
        from datetime import timedelta
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять проактивное сообщение
        if not check_subscription(user_id):
            return
        
        db = SessionLocal()
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
                if time_since_last < timedelta(minutes=15):
                    # Недавно общались, пропустить проактивное сообщение
                    return
            
            # Проверить режим "не беспокоить"
            if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                # Пользователь в режиме "не беспокоить", пропустить
                return
            
            # Получить текущее время в UTC
            now_utc = datetime.now(pytz.UTC)
            
            # Проверить задачи на ближайшие 15 минут (в UTC)
            next_15_min_utc = now_utc + timedelta(minutes=15)
            
            # Получить все pending задачи с reminder_time
            pending_tasks = db.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'pending',
                Task.reminder_time.isnot(None)
            ).all()
            
            # Проверить, есть ли задачи с reminder_time в ближайшие 15 минут
            tasks_in_15_min = 0
            for task in pending_tasks:
                # Сделать reminder_time aware с UTC, если он naive
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = pytz.UTC.localize(reminder_time)
                
                if now_utc <= reminder_time < next_15_min_utc:
                    tasks_in_15_min += 1
            
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
            
            # Если пользователь занят (больше 10 минут в ближайшие 15 мин), не отправлять
            if tasks_in_15_min > 0 or busy_time > 10:
                return
            
            if tasks_in_15_min == 0:
                # Нет задач - отправить проактивное сообщение
                await self.send_proactive_message(user_id)
        finally:
            db.close()

    def schedule_overdue_checks(self):
        """Планирование проверок просроченных задач каждые 15 минут"""
        from database import SessionLocal
        from models import User
        from apscheduler.triggers.interval import IntervalTrigger
        
        db = SessionLocal()
        try:
            users = db.query(User).all()
            for user in users:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                
                # Планируем проверки просроченных задач каждые 15 минут
                self.scheduler.add_job(
                    self.check_and_send_overdue_reminder,
                    trigger="cron",
                    minute="*/15",
                    timezone=user_tz,
                    args=[user.telegram_id],
                    id=f"overdue_{user.telegram_id}",
                    replace_existing=True
                )
        finally:
            db.close()

    async def send_proactive_message(self, user_id: int, message_num: int = 1):
        """Отправка проактивного сообщения пользователю с проверками условий"""
        from database import SessionLocal
        from models import Task, User, Interaction
        from datetime import timedelta
        from subscription_service import check_subscription
        
        # Проверить подписку - если нет доступа, не отправлять проактивное сообщение
        if not check_subscription(user_id):
            return
        
        db = SessionLocal()
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
                if time_since_last < timedelta(minutes=15):
                    # Недавно общались, пропустить проактивное сообщение
                    return
            
            # Проверить режим "не беспокоить"
            if user.do_not_disturb_until and datetime.now(pytz.UTC) < user.do_not_disturb_until.replace(tzinfo=pytz.UTC):
                # Пользователь в режиме "не беспокоить", пропустить
                return
            
            # Получить текущее время в UTC
            now_utc = datetime.now(pytz.UTC)
            
            # Проверить задачи на ближайшие 15 минут (в UTC)
            next_15_min_utc = now_utc + timedelta(minutes=15)
            
            # Получить все pending задачи с reminder_time
            pending_tasks = db.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'pending',
                Task.reminder_time.isnot(None)
            ).all()
            
            # Проверить, есть ли задачи с reminder_time в ближайшие 15 минут
            tasks_in_15_min = 0
            for task in pending_tasks:
                # Сделать reminder_time aware с UTC, если он naive
                reminder_time = task.reminder_time
                if reminder_time.tzinfo is None:
                    reminder_time = pytz.UTC.localize(reminder_time)
                
                if now_utc <= reminder_time < next_15_min_utc:
                    tasks_in_15_min += 1
            
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
            
            # Если пользователь занят (больше 10 минут в ближайшие 15 мин), не отправлять
            if tasks_in_15_min > 0 or busy_time > 10:
                return
            
            # Отправить проактивное сообщение с номером для разнообразия
            try:
                proactive_text = await self.ai_service.generate_proactive_message(user_id, message_num)
                
                if self.bot:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=proactive_text
                    )
                else:
                    print(f"[PROACTIVE] To user {user_id}: {proactive_text}")
            except Exception as e:
                import logging
                logging.error(f"Failed to send proactive message to user {user_id}: {e}")
        finally:
            db.close()

    async def check_and_send_overdue_reminder(self, user_id: int):
        """Проверка и отправка напоминания о просроченных задачах"""
        from database import SessionLocal
        from models import Task
        from datetime import datetime
        
        db = SessionLocal()
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
                print(f"[OVERDUE] To user {user_id}: {overdue_text}")
        except Exception as e:
            import logging
            logging.error(f"Failed to send overdue reminder to user {user_id}: {e}")