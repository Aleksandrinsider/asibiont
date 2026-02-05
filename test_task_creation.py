#!/usr/bin/env python3
"""
Тест создания задач AI-агентом
"""

import asyncio
import logging
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from models import Session, User, UserProfile, Task
from config import TELEGRAM_TOKEN, DEEPSEEK_API_KEY
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_task_creation():
    """Тестирование создания задач"""

    logger.info("🧪 Тестирование создания задач AI-агентом...")

    # Создаем тестового пользователя
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=123456789).first()
        if not user:
            user = User(
                telegram_id=123456789,
                username="test_user",
                first_name="Test",
                last_name="User"
            )
            session.add(user)
            session.commit()

        logger.info(f"👤 Тестируем для пользователя: {user.username}")

        # Тестовые запросы на создание задач
        test_queries = [
            "Создай задачу: купить продукты через 2 часа",
            "Напомни мне позвонить маме завтра в 10 утра",
            "Создай повторяющуюся задачу: делать зарядку каждый день в 8 утра"
        ]

        for i, query in enumerate(test_queries, 1):
            logger.info(f"\n📝 Тест {i}/3: {query}")

            try:
                # Вызываем AI-агента
                response = await chat_with_ai(
                    message=query,
                    user_id=user.telegram_id,
                    context=None,
                    message_type="text"
                )

                logger.info(f"🤖 Ответ: {response['response'][:150]}{'...' if len(response['response']) > 150 else ''}")

                # Проверяем, создалась ли задача
                tasks = session.query(Task).filter_by(user_id=user.id).all()
                logger.info(f"📊 Всего задач пользователя: {len(tasks)}")

                if tasks:
                    for task in tasks[-3:]:  # Показываем последние 3 задачи
                        logger.info(f"  - {task.title}: {task.reminder_time} (ID: {task.id})")

            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        logger.error(f"❌ Ошибка в тесте: {e}")
        return False
    finally:
        session.close()

    logger.info("\n✅ Тестирование создания задач завершено!")
    return True

if __name__ == "__main__":
    # Проверяем наличие API ключей
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не найден")
        sys.exit(1)

    # Запускаем тест
    success = asyncio.run(test_task_creation())
    sys.exit(0 if success else 1)