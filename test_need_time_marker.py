#!/usr/bin/env python3
"""
Тест для проверки генерации NEED_TIME_FOR_TASK маркера
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import init_db, User, Task
from config import DATABASE_URL
import logging

logging.basicConfig(level=logging.INFO)

async def test_need_time_generation():
    """Тест генерации NEED_TIME_FOR_TASK"""
    print("=== ТЕСТ: Генерация NEED_TIME_FOR_TASK ===")

    # Инициализация БД
    init_db()

    # Создаем тестового пользователя
    user_id = 123456789

    # Тестовое сообщение без времени
    message = "создай задачу купить продукты"

    try:
        result = await chat_with_ai(message, context=[], user_id=user_id)

        print(f"Сообщение: {message}")
        print(f"Ответ AI: {result}")

        # Проверяем, содержит ли ответ NEED_TIME_FOR_TASK
        if "NEED_TIME_FOR_TASK" in result:
            print("✅ NEED_TIME_FOR_TASK маркер найден в ответе")
            # Извлекаем заголовок задачи
            if "купить продукты" in result:
                print("✅ Заголовок задачи правильно извлечен")
            else:
                print("❌ Заголовок задачи не найден")
        else:
            print("❌ NEED_TIME_FOR_TASK маркер НЕ найден")

    except Exception as e:
        print(f"❌ Ошибка в тесте: {e}")

if __name__ == "__main__":
    asyncio.run(test_need_time_generation())