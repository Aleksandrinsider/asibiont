#!/usr/bin/env python3
"""
Тест производительности оптимизированного чата
"""
import asyncio
import time
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_chat_performance():
    """Тестируем производительность чата"""
    print("🚀 Тестируем производительность оптимизированного чата...")

    # Создаем тестовую сессию БД
    db_session = Session()

    try:
        # Ищем тестового пользователя
        user = db_session.query(User).filter_by(telegram_id="test_user").first()
        if not user:
            print("❌ Тестовый пользователь не найден")
            return

        test_messages = [
            "Привет, как дела?",
            "Создай задачу: купить продукты",
            "Покажи мои задачи",
            "Удалить все задачи"
        ]

        total_time = 0
        successful_responses = 0

        for i, message in enumerate(test_messages):
            print(f"\n📝 Тест {i+1}: '{message}'")

            start_time = time.time()

            try:
                result = await chat_with_ai(
                    message=message,
                    user_id=user.telegram_id,
                    db_session=db_session
                )

                end_time = time.time()
                response_time = end_time - start_time
                total_time += response_time

                if result and 'response' in result:
                    successful_responses += 1
                    print(f"✅ Ответ за {response_time:.2f} сек: {result['response'][:50]}...")
                else:
                    print(f"❌ Пустой ответ за {response_time:.2f} сек")

            except Exception as e:
                end_time = time.time()
                response_time = end_time - start_time
                total_time += response_time
                print(f"❌ Ошибка за {response_time:.2f} сек: {e}")

        # Результаты
        avg_time = total_time / len(test_messages)
        success_rate = (successful_responses / len(test_messages)) * 100

        print("\n📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
        print(f"⏱️  Среднее время ответа: {avg_time:.2f} сек")
        print(f"📈 Успешность: {success_rate:.1f}%")
        print(f"✅ Успешные ответы: {successful_responses}/{len(test_messages)}")

        # Оценка производительности
        if avg_time < 5.0:
            print("🎉 ОТЛИЧНО! Производительность восстановлена!")
        elif avg_time < 15.0:
            print("👍 ХОРОШО! Производительность улучшена!")
        elif avg_time < 30.0:
            print("⚠️ СРЕДНЕ! Нужно дальнейшая оптимизация")
        else:
            print("❌ ПЛОХО! Производительность не улучшилась")

    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        import traceback
        traceback.print_exc()

    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_chat_performance())