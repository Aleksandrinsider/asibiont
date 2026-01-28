#!/usr/bin/env python3
"""
Test script for minimal intent classification with real user queries
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.intent_classifier_minimal import IntentClassifierMinimal

async def test_minimal_intent_classification():
    """Test minimal intent classification on real user queries"""

    test_queries = [
        # Create task queries
        ("Создай задачу на завтра в 10 утра: позвонить маме", "add_task"),
        ("Напомни мне купить молоко сегодня вечером", "add_task"),
        ("Нужно сделать отчет к пятнице", "add_task"),
        ("Запланируй встречу с клиентом на среду", "add_task"),

        # List tasks queries
        ("Какие у меня задачи?", "list_tasks"),
        ("Покажи мои дела", "list_tasks"),
        ("Что мне нужно сделать?", "list_tasks"),
        ("Список задач", "list_tasks"),

        # Complete task queries
        ("Я сделал задачу 'позвонить маме'", "complete_task"),
        ("Завершил покупку молока", "complete_task"),
        ("Готово с отчетом", "complete_task"),
        ("Выполнил встречу с клиентом", "complete_task"),

        # Delete task queries
        ("Удалить задачу 'позвонить маме'", "delete_task"),
        ("Убери задачу о молоке", "delete_task"),
        ("Сотри встречу с клиентом", "delete_task"),

        # Reschedule queries
        ("Перенеси задачу на завтра", "reschedule_task"),
        ("Изменить время на 15:00", "reschedule_task"),
        ("Поставь на понедельник", "reschedule_task"),

        # Profile updates
        ("Обнови мой профиль", "update_profile"),
        ("Я работаю в IT", "update_profile"),
        ("Мои навыки: Python, JavaScript", "update_profile"),

        # Find partners
        ("Найди партнеров для проекта", "find_partners"),
        ("Ищу коллег для работы", "find_partners"),

        # Conversation
        ("Привет!", "conversation"),
        ("Как дела?", "conversation"),
        ("Спасибо", "conversation"),
        ("Расскажи о себе", "conversation"),
    ]

    print("Testing MINIMAL intent classification (no keywords)...")
    print("=" * 60)

    correct = 0
    total = len(test_queries)

    for query, expected_intent in test_queries:
        try:
            result = await IntentClassifierMinimal.classify_intent(query, user_id=123)
            status = "✓" if result == expected_intent else "✗"
            print(f"{status} '{query}' -> {result} (expected: {expected_intent})")

            if result == expected_intent:
                correct += 1
        except Exception as e:
            print(f"✗ '{query}' -> ERROR: {e}")

    print("=" * 60)
    accuracy = (correct / total) * 100
    print(".1f")

    return accuracy

if __name__ == "__main__":
    asyncio.run(test_minimal_intent_classification())