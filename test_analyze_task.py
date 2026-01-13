#!/usr/bin/env python3
"""
Тест новой функции анализа задач
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import analyze_task
from models import Session, Task, User, UserProfile
from datetime import datetime, timezone

def test_analyze_task():
    """Тестируем функцию анализа задач"""
    print("=== Тестирование функции analyze_task ===")

    session = Session()
    try:
        # Найдем тестового пользователя
        user = session.query(User).filter_by(telegram_id=123456789).first()
        if not user:
            print("❌ Тестовый пользователь не найден")
            return

        # Найдем задачу пользователя
        task = session.query(Task).filter_by(user_id=user.id).first()
        if not task:
            print("❌ Задача пользователя не найдена")
            return

        print(f"📋 Анализируем задачу: '{task.title}' (ID: {task.id})")

        # Вызываем функцию анализа
        result = analyze_task(task_id=task.id, user_id=user.telegram_id)
        print(f"✅ Результат анализа:\n{result}")

        # Проверяем, что результат содержит полезную информацию
        if "Анализ задачи" in result and len(result) > 50:
            print("✅ Анализ содержит полезную информацию")
        else:
            print("⚠️ Анализ может быть недостаточно информативным")

    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    test_analyze_task()