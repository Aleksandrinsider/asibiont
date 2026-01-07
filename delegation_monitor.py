"""
Система мониторинга делегированных задач
Автоматически:
1. Отправляет помощь получателю задачи
2. Отправляет отчёты делегировавшему о прогрессе
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

class DelegationMonitor:
    def __init__(self, bot, ai_service=None):
        self.bot = bot
        self.ai_service = ai_service
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
    
    async def start(self):
        """Запуск планировщика мониторинга"""
        self.scheduler.start()
        self.schedule_delegation_checks()
        logger.info("Delegation monitor started")
    
    def schedule_delegation_checks(self):
        """Планирует регулярные проверки делегированных задач"""
        # Проверка каждые 2 часа
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(minute=0, hour='*/2', timezone=pytz.UTC)
        self.scheduler.add_job(
            self.check_delegated_tasks,
            trigger=trigger,
            id='delegation_check',
            replace_existing=True
        )
    
    async def check_delegated_tasks(self):
        """Проверяет все активные делегированные задачи и отправляет обновления"""
        from models import Session, Task, User
        from ai_integration import generate_delegation_update
        
        session = Session()
        try:
            # Находим все делегированные задачи со статусом accepted или pending
            delegated_tasks = session.query(Task).filter(
                Task.delegated_to_username.isnot(None),
                Task.delegation_status.in_(['accepted', 'pending']),
                Task.reminder_time.isnot(None)
            ).all()
            
            now = datetime.now(pytz.UTC)
            
            for task in delegated_tasks:
                try:
                    # Получаем делегировавшего (создателя задачи)
                    delegator = session.query(User).filter_by(id=task.user_id).first()
                    if not delegator:
                        continue
                    
                    # Получаем получателя
                    recipient = session.query(User).filter(
                        User.username.ilike(task.delegated_to_username.replace('@', ''))
                    ).first()
                    
                    if not recipient:
                        continue
                    
                    # Проверяем время до дедлайна
                    time_to_deadline = task.reminder_time - now
                    hours_to_deadline = time_to_deadline.total_seconds() / 3600
                    
                    # 1. Помощь получателю задачи
                    if task.delegation_status == 'accepted':
                        # Отправляем помощь получателю за день до дедлайна
                        if 20 < hours_to_deadline <= 26 and not task.helper_sent:
                            await self.send_helper_to_recipient(task, recipient)
                            task.helper_sent = True
                            session.commit()
                    
                    # 2. Отчёты делегировавшему
                    # Отчёт за 2 часа до дедлайна
                    if 1 < hours_to_deadline <= 3 and not task.approaching_notification_sent:
                        await self.send_progress_report(task, delegator, recipient, 'approaching_deadline')
                        task.approaching_notification_sent = True
                        session.commit()
                    
                    # Отчёт на середине пути (если до дедлайна больше 2 дней)
                    total_time = (task.reminder_time - task.created_at).total_seconds() / 3600
                    if total_time > 48:  # Если задача дольше 2 дней
                        midpoint_time = total_time / 2
                        if abs(hours_to_deadline - midpoint_time) < 2 and not task.midpoint_notification_sent:
                            await self.send_progress_report(task, delegator, recipient, 'midpoint')
                            task.midpoint_notification_sent = True
                            session.commit()
                    
                except Exception as e:
                    logger.error(f"Error processing delegated task {task.id}: {e}")
                    continue
        
        finally:
            session.close()
    
    async def send_helper_to_recipient(self, task, recipient):
        """Отправляет AI-помощь получателю делегированной задачи"""
        try:
            from ai_integration import generate_delegation_helper
            
            message = await generate_delegation_helper(
                recipient.telegram_id,
                task.title,
                task.description or "",
                task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else ""
            )
            
            await self.bot.send_message(
                chat_id=recipient.telegram_id,
                text=f"💡 Помощь по задаче:\n\n{message}"
            )
            logger.info(f"Sent helper to recipient {recipient.telegram_id} for task {task.id}")
        
        except Exception as e:
            logger.error(f"Error sending helper to recipient: {e}")
    
    async def send_progress_report(self, task, delegator, recipient, update_type):
        """Отправляет отчёт о прогрессе делегировавшему"""
        try:
            from ai_integration import generate_delegation_update
            
            message = await generate_delegation_update(
                user_id=delegator.telegram_id,
                task_title=task.title,
                recipient_username=recipient.username,
                task_status=task.delegation_status,
                reminder_time=task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else "",
                update_type=update_type
            )
            
            await self.bot.send_message(
                chat_id=delegator.telegram_id,
                text=f"📊 Отчёт по делегированной задаче:\n\n{message}"
            )
            logger.info(f"Sent progress report to delegator {delegator.telegram_id} for task {task.id}")
        
        except Exception as e:
            logger.error(f"Error sending progress report: {e}")
    
    async def notify_task_completed(self, task_id):
        """Уведомляет делегировавшего о завершении задачи"""
        from models import Session, Task, User
        from ai_integration import generate_delegation_update
        
        session = Session()
        try:
            task = session.query(Task).filter_by(id=task_id).first()
            if not task or not task.delegated_to_username:
                return
            
            delegator = session.query(User).filter_by(id=task.user_id).first()
            recipient = session.query(User).filter(
                User.username.ilike(task.delegated_to_username.replace('@', ''))
            ).first()
            
            if not delegator or not recipient:
                return
            
            message = await generate_delegation_update(
                user_id=delegator.telegram_id,
                task_title=task.title,
                recipient_username=recipient.username,
                task_status='completed',
                reminder_time=task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else "",
                update_type='completed'
            )
            
            await self.bot.send_message(
                chat_id=delegator.telegram_id,
                text=f"✅ Задача завершена:\n\n{message}"
            )
            logger.info(f"Notified delegator {delegator.telegram_id} about task {task_id} completion")
        
        except Exception as e:
            logger.error(f"Error notifying task completion: {e}")
        finally:
            session.close()
