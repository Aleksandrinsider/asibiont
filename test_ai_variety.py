# -*- coding: utf-8 -*-
"""Тест AI агента на разнообразие ответов"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal
import asyncio

async def test_ai_responses():
    """Тестируем разнообразие ответов AI"""

    # Создаем сессию БД
    db_session = SessionLocal()

    try:
        # Тестовые сообщения
        test_messages = [
            "Привет!",
            "Привет, как дела?",
            "Здравствуй",
            "Хай",
            "Привет! Что нового?"
        ]

        print("Тестируем разнообразие приветственных ответов:\n")

        for i, message in enumerate(test_messages, 1):
            print(f"Тест {i}: '{message}'")
            try:
                response = await chat_with_ai(
                    message=message,
                    user_id=146333757,  # Реальный ID пользователя
                    db_session=db_session
                )
                print(f"Ответ: {response}")
                print("-" * 50)
            except Exception as e:
                print(f"Ошибка: {e}")
                print("-" * 50)

    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_responses())