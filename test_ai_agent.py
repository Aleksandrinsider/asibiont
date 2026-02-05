#!/usr/bin/env python3
"""
Тест AI-агента с реальными запросами пользователей
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

# Тестовые запросы пользователей
TEST_QUERIES = [
    "Создай задачу на завтра в 10 утра: подготовить презентацию для клиента",
    "Какие у меня задачи на сегодня?",
    "Заверши задачу 'подготовить презентацию'",
    "Напомни мне через час позвонить маме",
    "Найди контакты для помощи с разработкой мобильного приложения",
    "Обнови мой профиль: я работаю в IT, люблю спорт и путешествия",
    "Какие новости сегодня?",
    "Создай повторяющуюся задачу: каждый день в 8 утра делать зарядку",
    "Перенеси задачу 'позвонить маме' на вечер",
    "Расскажи о себе и что ты умеешь"
]

async def test_ai_agent():
    """Тестирование AI-агента с реальными запросами"""

    logger.info("🚀 Начинаем тестирование AI-агента...")

    # Создаем тестового пользователя
    session = Session()
    try:
        # Ищем существующего пользователя или создаем нового
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
            logger.info("✅ Создан тестовый пользователь")

        # Создаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                interests="программирование, спорт, путешествия",
                skills="Python, JavaScript, управление проектами",
                goals="стать senior разработчиком, путешествовать по миру",
                company="Tech Startup",
                city="Москва"
            )
            session.add(profile)
            session.commit()
            logger.info("✅ Создан профиль пользователя")

        logger.info(f"👤 Тестируем для пользователя: {user.username} (ID: {user.telegram_id})")

        # Тестируем каждый запрос
        for i, query in enumerate(TEST_QUERIES, 1):
            logger.info(f"\n{'='*50}")
            logger.info(f"🧪 Тест {i}/{len(TEST_QUERIES)}: {query}")
            logger.info(f"{'='*50}")

            try:
                # Вызываем AI-агента
                response = await chat_with_ai(
                    message=query,
                    user_id=user.telegram_id,
                    context=None,
                    message_type="text"
                )

                logger.info(f"🤖 Ответ агента: {response[:200]}{'...' if len(response) > 200 else ''}")

                # Небольшая пауза между запросами
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке запроса '{query}': {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        logger.error(f"❌ Ошибка в тесте: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

    logger.info("\n🎉 Тестирование завершено!")

if __name__ == "__main__":
    # Проверяем наличие API ключей
    if not DEEPSEEK_API_KEY:
        logger.error("❌ DEEPSEEK_API_KEY не найден в конфигурации")
        sys.exit(1)

    if not TELEGRAM_TOKEN:
        logger.warning("⚠️  TELEGRAM_TOKEN не найден, но для теста он не обязателен")

    # Запускаем тест
    asyncio.run(test_ai_agent())