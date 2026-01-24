#!/usr/bin/env python3
"""
Тест для проверки разнообразия ответов AI
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

async def test_ai_response_variety():
    """Тест разнообразия ответов AI"""
    print("=== ТЕСТ: Разнообразие ответов AI ===")

    # Инициализация БД
    init_db()

    # Создаем тестового пользователя
    user_id = 123456789

    # Тестовые сообщения для проверки разнообразия
    test_messages = [
        "Создай задачу проверить почту",
        "Заверши задачу проверить почту",
        "Удали задачу проверить почту",
        "Перенеси задачу проверить почту на завтра",
    ]

    responses = []

    for message in test_messages:
        print(f"\n--- Тестируем: {message} ---")
        try:
            result = await chat_with_ai(message, context=[], user_id=user_id)
            responses.append(result)
            print(f"Ответ: {result[:100]}...")

            # Проверки на однообразие
            forbidden_phrases = [
                "увеличить доход в 10x",
                "уже ночь",
                "хорошее время отдохнуть",
                "Отлично!",
                "Замечательно!",
                "Конечно!"
            ]

            issues = []
            for phrase in forbidden_phrases:
                if phrase.lower() in result.lower():
                    issues.append(f"Найдена запрещенная фраза: '{phrase}'")

            if issues:
                print(f"❌ Проблемы: {issues}")
            else:
                print("✅ Ответ выглядит разнообразным")

        except Exception as e:
            print(f"❌ Ошибка: {e}")

    # Проверка уникальности ответов
    unique_responses = len(set(responses))
    total_responses = len(responses)

    print("\n=== СТАТИСТИКА ===")
    print(f"Всего ответов: {total_responses}")
    print(f"Уникальных ответов: {unique_responses}")

    if unique_responses == total_responses:
        print("✅ Все ответы уникальны")
    else:
        print("❌ Есть повторяющиеся ответы")

    print("✅ Тест завершен")

if __name__ == "__main__":
    asyncio.run(test_ai_response_variety())