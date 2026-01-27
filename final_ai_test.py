#!/usr/bin/env python3
"""
Финальный тест AI для проверки удаления задач
"""

import sys
import os
sys.path.append('.')

from ai_integration.chat import chat_with_ai
import asyncio

async def final_ai_test():
    """Финальный тест AI с командой удаления задачи"""
    print("=" * 60)
    print("ФИНАЛЬНЫЙ ТЕСТ AI: УДАЛЕНИЕ ЗАДАЧ")
    print("=" * 60)

    # Шаг 1: Создаем тестовую задачу
    print("\n1. Создаем тестовую задачу...")
    result1 = await chat_with_ai('создай задачу "финальная тестовая задача" на завтра в 15:00', user_id=123456789)
    print("Результат создания:")
    print(result1[:300] + "..." if len(result1) > 300 else result1)

    # Шаг 2: Проверяем список задач
    print("\n2. Проверяем список задач...")
    result2 = await chat_with_ai('покажи мои задачи', user_id=123456789)
    print("Текущие задачи:")
    print(result2[:500] + "..." if len(result2) > 500 else result2)

    # Шаг 3: Удаляем задачу
    print("\n3. Удаляем задачу командой 'удали тестовую задачу'...")
    result3 = await chat_with_ai('удали тестовую задачу', user_id=123456789)
    print("Результат удаления:")
    print(result3[:500] + "..." if len(result3) > 500 else result3)

    # Шаг 4: Проверяем список задач после удаления
    print("\n4. Проверяем список задач после удаления...")
    result4 = await chat_with_ai('покажи мои задачи', user_id=123456789)
    print("Задачи после удаления:")
    print(result4[:500] + "..." if len(result4) > 500 else result4)

    print("\n" + "=" * 60)
    print("ТЕСТ ЗАВЕРШЕН")
    print("=" * 60)

    # Анализ результатов
    success_indicators = [
        "удален" in result3.lower(),
        "задача" in result3.lower() and ("удали" in result3.lower() or "убра" in result3.lower()),
        "у вас нет активных задач" in result4.lower() or "нет задач" in result4.lower()
    ]

    if all(success_indicators):
        print("✅ ТЕСТ ПРОЙДЕН: AI успешно удалил задачу!")
    else:
        print("❌ ТЕСТ НЕ ПРОЙДЕН: AI не смог удалить задачу")
        print(f"Индикаторы успеха: {success_indicators}")

if __name__ == "__main__":
    asyncio.run(final_ai_test())