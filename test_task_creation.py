"""
Тест создания задач без контекста последней задачи
"""
import asyncio
import logging
from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_task_creation():
    """Тест создания новой задачи"""
    db_session = Session()
    try:
        # Получаем или создаем тестового пользователя
        user = db_session.query(User).filter_by(id=1).first()
        if not user:
            user = User(id=1, username="test_user", telegram_id=12345)
            db_session.add(user)
            db_session.commit()
        
        # Подсчитываем задачи до теста
        tasks_before = db_session.query(Task).filter_by(user_id=1).count()
        logger.info(f"Задач до теста: {tasks_before}")
        
        # Получаем последнюю задачу для контекста
        last_task = db_session.query(Task).filter_by(user_id=1).order_by(Task.created_at.desc()).first()
        if last_task:
            logger.info(f"Последняя задача: ID={last_task.id}, title='{last_task.title}'")
        
        # Тестируем создание новой задачи
        test_message = "напомни купить хлеб через 5 минут"
        logger.info(f"\n{'='*50}")
        logger.info(f"ТЕСТ: Отправка сообщения '{test_message}'")
        logger.info(f"{'='*50}\n")
        
        response = await chat_with_ai(test_message, user_id=1, db_session=db_session)
        
        logger.info(f"\nОтвет AI: {response}\n")
        
        # Проверяем результат - обновляем сессию
        db_session.expire_all()
        tasks_after = db_session.query(Task).filter_by(user_id=1).count()
        logger.info(f"Задач после теста: {tasks_after}")
        
        new_task = db_session.query(Task).filter_by(user_id=1).order_by(Task.created_at.desc()).first()
        
        if tasks_after > tasks_before:
            logger.info(f"✅ УСПЕХ: Новая задача создана!")
            logger.info(f"   ID: {new_task.id}")
            logger.info(f"   Название: '{new_task.title}'")
            logger.info(f"   Напоминание: {new_task.reminder_time}")
            logger.info(f"   Создана: {new_task.created_at}")
        else:
            logger.error(f"❌ ОШИБКА: Задача не создана!")
            last_task_id = last_task.id if last_task else None
            new_task_id = new_task.id if new_task else None
            if last_task_id and new_task_id and last_task_id == new_task_id:
                logger.error(f"   Последняя задача не изменилась (ID={last_task_id})")
                logger.error(f"   Вероятно был вызван edit_task вместо add_task")
        
        return tasks_after > tasks_before
        
    except Exception as e:
        logger.error(f"Ошибка в тесте: {e}", exc_info=True)
        return False
    finally:
        db_session.close()

async def test_similar_task():
    """Тест создания похожей задачи (проверка дубликатов)"""
    db_session = Session()
    try:
        tasks_before = db_session.query(Task).filter_by(user_id=1).count()
        
        test_message = "напомни купить хлеб через 10 минут"
        logger.info(f"\n{'='*50}")
        logger.info(f"ТЕСТ 2: Попытка создать дубликат '{test_message}'")
        logger.info(f"{'='*50}\n")
        
        response = await chat_with_ai(test_message, user_id=1, db_session=db_session)
        
        logger.info(f"\nОтвет AI: {response}\n")
        
        tasks_after = db_session.query(Task).filter_by(user_id=1).count()
        
        if tasks_after == tasks_before:
            logger.info(f"✅ УСПЕХ: Дубликат обнаружен и не создан")
        else:
            logger.warning(f"⚠️ Создана еще одна задача (возможно, это нормально)")
        
    except Exception as e:
        logger.error(f"Ошибка в тесте 2: {e}", exc_info=True)
    finally:
        db_session.close()

async def main():
    logger.info("Запуск тестов создания задач\n")
    
    # Тест 1: Создание новой задачи
    success = await test_task_creation()
    
    if success:
        # Тест 2: Проверка дубликатов
        await asyncio.sleep(1)
        await test_similar_task()

if __name__ == "__main__":
    asyncio.run(main())
