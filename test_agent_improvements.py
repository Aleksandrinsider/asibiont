#!/usr/bin/env python3
"""
Простой тест для проверки улучшений агента
"""
import asyncio
import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai

async def test_agent_improvements():
    """Тестирование улучшений агента"""
    print("🧪 Тестирование улучшений агента...")

    # Тест 1: Приветствие
    print("\n1. Тест приветствия:")
    response = await chat_with_ai("Привет", user_id=12345)
    print(f"Ответ: {response}")

    # Тест 2: Общий вопрос
    print("\n2. Тест общего вопроса:")
    response = await chat_with_ai("Что ты умеешь?", user_id=12345)
    print(f"Ответ: {response}")

    # Тест 3: Интерес к партнерству
    print("\n3. Тест интереса к партнерству:")
    response = await chat_with_ai("Ищу партнеров для бизнеса", user_id=12345)
    print(f"Ответ: {response}")

    print("\n✅ Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_agent_improvements())