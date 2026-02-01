#!/usr/bin/env python3
import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

# Включаем бесплатный доступ для тестов
os.environ["FREE_ACCESS_MODE"] = "1"
os.environ["LOCAL"] = "1"

from ai_integration.chat import chat_with_ai

async def test_no_tasks():
    """Тест приветствия когда задач нет"""

    message = "Покажи мои задачи"

    try:
        response = await chat_with_ai(
            message=message,
            context=None,
            user_id=123456,
            db_session=None,
            message_type="text"
        )

        print("🎯 ТЕСТ ЗАПРОСА ЗАДАЧ (КОГДА ИХ НЕТ)")
        print(f"📝 Сообщение: {message}")
        print(f"💬 Ответ AI: {response['response']}")
        print("\n" + "="*50)

    except Exception as e:
        print(f"❌ Ошибка: {e}")

async def test_greeting():
    """Тест приветствия без задач"""

    message = "Привет"

    try:
        response = await chat_with_ai(
            message=message,
            context=None,
            user_id=123456,
            db_session=None,
            message_type="text"
        )

        print("🎯 ТЕСТ ПРИВЕТСТВИЯ")
        print(f"📝 Сообщение: {message}")
        print(f"💬 Ответ AI: {response['response']}")
        print("\n" + "="*50)

    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_greeting())
    asyncio.run(test_no_tasks())