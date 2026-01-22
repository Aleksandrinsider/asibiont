# -*- coding: utf-8 -*-
"""Тест AI агента на продвинутые функции"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal
import asyncio

async def test_advanced_features():
    """Тестируем продвинутые функции AI"""

    # Создаем сессию БД
    db_session = SessionLocal()

    try:
        # Тестовые сообщения для проверки продвинутых функций
        test_messages = [
            "Мне нужно найти людей для совместного проекта по разработке ИИ",
            "Я выполнил задачу по изучению Python, что дальше?",
            "У меня мало свободного времени, как эффективно использовать его?",
            "Расскажи о своих возможностях по поиску контактов"
        ]

        print("Тестируем продвинутые функции AI:\n")

        for i, message in enumerate(test_messages, 1):
            print(f"Тест {i}: '{message}'")
            try:
                response = await chat_with_ai(
                    message=message,
                    user_id=146333757,  # Реальный ID пользователя
                    db_session=db_session
                )
                print(f"Ответ: {response}")
                print("-" * 80)
            except Exception as e:
                print(f"Ошибка: {e}")
                print("-" * 80)

    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_advanced_features())