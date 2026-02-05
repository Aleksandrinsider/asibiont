#!/usr/bin/env python3
"""
Быстрый тест основных функций AI-агента
"""

import asyncio
import logging
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from models import Session, User, UserProfile
from config import TELEGRAM_TOKEN, DEEPSEEK_API_KEY
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Быстрые тестовые запросы
QUICK_TESTS = [
    "Создай задачу: купить продукты",
    "Какие у меня задачи?",
    "Привет, как дела?"
]

async def quick_test():
    """Быстрое тестирование основных функций"""

    logger.info("🚀 Быстрое тестирование AI-агента...")

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

        # Тестируем только 3 быстрых запроса
        for i, query in enumerate(QUICK_TESTS, 1):
            logger.info(f"\n🧪 Тест {i}/3: {query}")

            try:
                # Вызываем AI-агента
                response = await chat_with_ai(
                    message=query,
                    user_id=user.telegram_id,
                    context=None,
                    message_type="text"
                )

                # Проверяем, что получили ответ
                if isinstance(response, dict) and 'response' in response:
                    logger.info(f"✅ Ответ получен: {response['response'][:100]}{'...' if len(response['response']) > 100 else ''}")
                else:
                    logger.info(f"✅ Ответ получен: {str(response)[:100]}{'...' if len(str(response)) > 100 else ''}")

            except Exception as e:
                logger.error(f"❌ Ошибка: {e}")
                return False

    except Exception as e:
        logger.error(f"❌ Ошибка в тесте: {e}")
        return False
    finally:
        session.close()

    logger.info("\n🎉 Быстрое тестирование завершено успешно!")
    return True

if __name__ == "__main__":
    # Проверяем наличие API ключей
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не найден")
        sys.exit(1)

    # Запускаем быстрый тест
    success = asyncio.run(quick_test())
    sys.exit(0 if success else 1)