#!/usr/bin/env python3
"""
Тест для проверки проактивных сообщений
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import generate_proactive_message

async def test_proactive_messages():
    """Тестируем генерацию проактивных сообщений"""

    print("🧪 ТЕСТИРОВАНИЕ ПРОАКТИВНЫХ СООБЩЕНИЙ")
    print("=" * 50)

    # Тестовые сценарии
    test_cases = [
        ("general", 0, 0, None, "Общий контекст"),
        ("no_tasks", 0, 0, None, "Нет задач"),
        ("few_tasks", 3, 0, None, "Мало задач"),
        ("many_tasks", 15, 0, None, "Много задач"),
        ("overdue_tasks", 5, 2, None, "Просроченные задачи"),
    ]

    for context, task_count, overdue_count, tasks_list, description in test_cases:
        print(f"\n📋 ТЕСТ: {description} (context='{context}', tasks={task_count}, overdue={overdue_count})")
        print("-" * 60)

        try:
            # Генерируем сообщение
            message = await generate_proactive_message(
                user_id=123456789,  # Тестовый ID
                context=context,
                task_count=task_count,
                overdue_count=overdue_count,
                tasks_list=tasks_list
            )

            print(f"📝 Сгенерированное сообщение:")
            print(f"'{message}'")
            print()

            # Проверки качества
            issues = []

            if len(message) < 20:
                issues.append("Слишком короткое сообщение")

            if "посмотрю твои задачи" in message.lower():
                issues.append("Содержит неконкретную фразу 'посмотрю твои задачи'")

            if "я сейчас" in message.lower() and "задач" in message.lower():
                issues.append("Начинается с действия без конкретных советов")

            # Проверяем наличие конкретных советов
            has_concrete_advice = any(keyword in message.lower() for keyword in [
                "предлагаю", "рекомендую", "можешь", "стоит", "давай",
                "поработать", "запланировать", "проверить", "обновить"
            ])

            if not has_concrete_advice:
                issues.append("Отсутствуют конкретные предложения или советы")

            if issues:
                print(f"⚠️  ПРОБЛЕМЫ НАЙДЕНЫ:")
                for issue in issues:
                    print(f"   - {issue}")
            else:
                print("✅ СООБЩЕНИЕ КАЧЕСТВЕННОЕ")

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")

    print("\n" + "=" * 50)
    print("🏁 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")

if __name__ == "__main__":
    asyncio.run(test_proactive_messages())