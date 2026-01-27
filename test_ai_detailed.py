#!/usr/bin/env python3
"""
Детальный тест AI с проверкой выполнения инструментов
"""

import asyncio
import sys
import os
import logging

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, Task, User

async def test_ai_execution():
    """Тестируем AI с проверкой реального выполнения"""

    # Очищаем старые задачи тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=12345).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    session.close()

    test_cases = [
        ("напомни мне позвонить маме завтра в 10 утра", "add_task"),
        ("что у меня запланировано", "list_tasks"),
        ("я только что закончил отчет, готово", "complete_task"),
    ]

    print("🧪 Детальное тестирование выполнения AI команд")
    print("=" * 60)

    for i, (message, expected_action) in enumerate(test_cases, 1):
        print(f"\n📝 Тест {i}: '{message}'")
        print(f"Ожидаемое действие: {expected_action}")
        print("-" * 40)

        # Проверяем состояние ДО
        session = Session()
        user = session.query(User).filter_by(telegram_id=12345).first()
        tasks_before = session.query(Task).filter_by(user_id=user.id).count() if user else 0
        print(f"Задач до выполнения: {tasks_before}")

        try:
            # Выполняем запрос
            response = await chat_with_ai(
                message=message,
                user_id=12345,
                message_type='normal'
            )

            print(f"🤖 Ответ AI: {response[:150]}{'...' if len(response) > 150 else ''}")

            # Проверяем состояние ПОСЛЕ
            session = Session()
            tasks_after = session.query(Task).filter_by(user_id=user.id).count() if user else 0
            print(f"Задач после выполнения: {tasks_after}")

            # Проверяем изменение
            if expected_action == "add_task" and tasks_after > tasks_before:
                print("✅ Задача действительно создана!")
            elif expected_action == "complete_task" and tasks_after < tasks_before:
                print("✅ Задача действительно завершена!")
            elif expected_action == "list_tasks":
                print("✅ Список задач показан")
            else:
                print("⚠️  Изменений в БД не обнаружено")

            # Показываем текущие задачи
            if user:
                tasks = session.query(Task).filter_by(user_id=user.id).all()
                if tasks:
                    print("Текущие задачи:")
                    for task in tasks[:3]:  # Показываем первые 3
                        status = "✅" if task.status == "completed" else "⏳"
                        print(f"  {status} {task.title} ({task.reminder_time})")

        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()

        finally:
            session.close()

        print()

if __name__ == "__main__":
    asyncio.run(test_ai_execution())