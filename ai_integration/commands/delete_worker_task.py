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

            # Находим существующего worker'а
            existing_worker = db_session.query(Task).filter(
                Task.user_id == user.id,
                Task.title.like("Worker:%")
            ).first()
            
            if not existing_worker:
                return "У вас нет активных фоновых задач."

            # Удаляем задачу из scheduler
            if REMINDER_SERVICE:
                job_id = f"worker_{existing_worker.id}_{user_id}"
                try:
                    REMINDER_SERVICE.scheduler.remove_job(job_id)
                    logger.info(f"Worker job {job_id} removed from scheduler")
                except Exception as e:
                    logger.warning(f"Could not remove job {job_id}: {e}")

            # Удаляем задачу из БД
            db_session.delete(existing_worker)
            db_session.commit()

            return "Фоновая задача удалена. Теперь вы можете создать новую."

        except Exception as e:
            logger.error(f"Error deleting worker task: {e}")
            return f"Ошибка при удалении фоновой задачи: {str(e)}"