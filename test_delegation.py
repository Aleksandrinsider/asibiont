#!/usr/bin/env python3
"""
Тест делегирования задач
"""
import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai

async def test_delegation():
    user_id = 999999999

    print("=== ТЕСТ ДЕЛЕГИРОВАНИЯ ===\n")

    # Создаем задачу
    print("1. Создание задачи:")
    message1 = "Создай задачу: Подготовить отчет завтра в 10:00"
    print(f"Сообщение: {message1}")
    response1 = await chat_with_ai(message1, user_id=user_id)
    print(f"Ответ AI: {response1}\n")

    # Делегируем задачу
    print("2. Делегирование задачи:")
    message2 = 'Делегируй задачу "Подготовить отчет" пользователю @test1'
    print(f"Сообщение: {message2}")
    response2 = await chat_with_ai(message2, user_id=user_id)
    print(f"Ответ AI: {response2}\n")

if __name__ == "__main__":
    asyncio.run(test_delegation())