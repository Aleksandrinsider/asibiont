from .base_command import BaseCommand
from models import Session, Task, User
from reminder_service import REMINDER_SERVICE
import logging

logger = logging.getLogger(__name__)

class DeleteWorkerTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        try:
            # Находим пользователя
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return "Пользователь не найден"

            # Находим все существующие worker задачи
            existing_workers = db_session.query(Task).filter(
                Task.user_id == user.id,
                Task.title.like("Worker:%")
            ).all()
            
            if not existing_workers:
                return "У вас нет активных фоновых задач."

            # Удаляем все задачи из scheduler
            if REMINDER_SERVICE:
                for worker_task in existing_workers:
                    job_id = f"worker_{worker_task.id}_{user_id}"
                    try:
                        REMINDER_SERVICE.scheduler.remove_job(job_id)
                        logger.info(f"Worker job {job_id} removed from scheduler")
                    except Exception as e:
                        logger.warning(f"Could not remove job {job_id}: {e}")

            # Удаляем все задачи из БД
            deleted_count = 0
            for worker_task in existing_workers:
                db_session.delete(worker_task)
                deleted_count += 1
            
            db_session.commit()

            return f"Удалено {deleted_count} фоновых задач. Теперь вы можете создать новые."

        except Exception as e:
            logger.error(f"Error deleting worker task: {e}")
            return f"Ошибка при удалении фоновой задачи: {str(e)}"