#!/usr/bin/env python3
"""
Тест умного поведения агента - проверка гибкости и контекстности
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai

async def test_smart_agent_behavior():
    """Тестирование умного поведения агента"""

    print("🧠 Тестирование умного поведения агента...")

    # Создаем тестового пользователя
    from models import Session, User, SubscriptionTier
    session = Session()
    try:
        # Создаем пользователя если не существует
        user = session.query(User).filter_by(telegram_id=99999).first()
        if not user:
            user = User(
                telegram_id=99999,
                username="test_smart",
                first_name="Test Smart",
                subscription_tier=SubscriptionTier.STANDARD
            )
            session.add(user)
            session.commit()
            print("✅ Создан тестовый пользователь 99999")
    finally:
        session.close()

    # Тест 1: Приветствие ночью (должно быть кратким)
    print("\n1. 🌓 Тест приветствия ночью:")
    response = await chat_with_ai("Привет", user_id=99999)
    print(f"Ответ: {response['response'][:300]}...")
    print(f"Инструменты: {len(response.get('tool_calls', []))}")

    # Тест 2: Приветствие днем без задач
    print("\n2. 🌅 Тест приветствия днем:")
    response = await chat_with_ai("Привет", user_id=99999)
    print(f"Ответ: {response['response'][:300]}...")
    print(f"Инструменты: {len(response.get('tool_calls', []))}")

    # Тест 3: Конкретный запрос
    print("\n3. 🎯 Тест конкретного запроса:")
    response = await chat_with_ai("Что нового в AI?", user_id=99999)
    print(f"Ответ: {response['response'][:300]}...")
    print(f"Инструменты: {len(response.get('tool_calls', []))}")

    # Тест 4: Общий вопрос "что нового"
    print("\n4. ❓ Тест общего вопроса:")
    response = await chat_with_ai("Что нового?", user_id=99999)
    print(f"Ответ: {response['response'][:300]}...")
    print(f"Инструменты: {len(response.get('tool_calls', []))}")

    print("\n✅ Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_smart_agent_behavior())