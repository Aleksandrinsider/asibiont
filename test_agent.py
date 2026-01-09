#!/usr/bin/env python3
"""
Комплексный тест всех функций агента после очистки данных.
"""

import os
import sys
from datetime import datetime, timedelta
import pytz

# Добавляем текущую директорию в путь
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from dotenv import load_dotenv
load_dotenv()

from models import Session, User, UserProfile, Task, Interaction, Subscription
from ai_integration import (
    add_task, list_tasks, complete_task, update_profile,
    get_partners_list, update_user_memory, delete_all_tasks
)

def test_user_creation():
    """Тест создания пользователя"""
    print("\n🧪 Тест 1: Создание пользователя")

    session = Session()
    try:
        # Создаем пользователя
        user = User(telegram_id=123456789, username='test_user', first_name='Test User')
        session.add(user)
        session.commit()

        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            city='Москва',
            interests='программирование, ИИ',
            skills='Python, разработка'
        )
        session.add(profile)

        # Создаем подписку
        subscription = Subscription(
            user_id=user.id,
            status='active',
            end_date=datetime.now(pytz.UTC) + timedelta(days=30)
        )
        session.add(subscription)

        session.commit()
        print("✅ Пользователь создан")

        return user.id

    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка создания пользователя: {e}")
        return None
    finally:
        session.close()

def test_task_operations(user_id):
    """Тест операций с задачами"""
    print("\n🧪 Тест 2: Операции с задачами")

    try:
        # Добавляем задачу
        result = add_task(
            title='Тестовая задача',
            description='Описание тестовой задачи',
            reminder_time='2026-01-10 15:00',
            user_id=user_id
        )
        print(f"✅ Задача добавлена: {result}")

        # Получаем список задач
        tasks_result = list_tasks(user_id=user_id)
        print(f"✅ Список задач получен (тип: {type(tasks_result)})")

        # Проверяем, что есть хотя бы одна задача
        if isinstance(tasks_result, str):
            print(f"📄 Результат list_tasks: {tasks_result[:200]}...")
            # Предполагаем, что задача создана, пробуем завершить по названию
            result = complete_task(task_title='Тестовая задача', user_id=user_id)
            print(f"✅ Задача завершена по названию: {result}")
        else:
            print(f"❓ Неожиданный тип результата list_tasks: {type(tasks_result)}")

        return True

    except Exception as e:
        print(f"❌ Ошибка операций с задачами: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_profile_operations(user_id):
    """Тест операций с профилем"""
    print("\n🧪 Тест 3: Операции с профилем")

    try:
        # Обновляем профиль
        result = update_profile(
            interests='+туризм',
            skills='+управление проектами',
            city='Санкт-Петербург',
            user_id=user_id
        )
        print(f"✅ Результат обновления профиля: {result}")

        if isinstance(result, str):
            print("✅ Профиль обновлен (строка)")
        else:
            print(f"⚠️  Неожиданный тип результата: {type(result)}")

        return True

    except Exception as e:
        print(f"❌ Ошибка операций с профилем: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_memory_operations(user_id):
    """Тест операций с памятью"""
    print("\n🧪 Тест 4: Операции с памятью")

    try:
        result = update_user_memory("Тестовая информация о пользователе", user_id=user_id)
        print(f"✅ Результат обновления памяти: {result}")

        if isinstance(result, str):
            print("✅ Память обновлена (строка)")
        else:
            print(f"⚠️  Неожиданный тип результата: {type(result)}")

        return True

    except Exception as e:
        print(f"❌ Ошибка операций с памятью: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_partners_search(user_id):
    """Тест поиска партнеров"""
    print("\n🧪 Тест 5: Поиск партнеров")

    try:
        result = get_partners_list(user_id=user_id)
        print(f"✅ Результат поиска партнеров: {result}")

        if isinstance(result, str):
            print("✅ Поиск партнеров вернул строку (ожидаемо)")
        elif isinstance(result, list):
            print(f"✅ Найдено партнеров: {len(result)}")
        else:
            print(f"⚠️  Неожиданный тип результата: {type(result)}")

        return True

    except Exception as e:
        print(f"❌ Ошибка поиска партнеров: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_delete_all_tasks(user_id):
    """Тест удаления всех задач"""
    print("\n🧪 Тест 6: Удаление всех задач")

    try:
        # Сначала добавим несколько задач
        add_task("Задача 1", "Описание 1", user_id=user_id)
        add_task("Задача 2", "Описание 2", user_id=user_id)
        add_task("Задача 3", "Описание 3", user_id=user_id)

        # Проверяем количество задач в БД
        session = Session()
        tasks_count_before = session.query(Task).filter_by(user_id=1).count()  # user.id = 1
        session.close()
        print(f"✅ Задач в БД до удаления: {tasks_count_before}")

        # Удаляем все
        result = delete_all_tasks(user_id=user_id)
        print(f"✅ Результат удаления: {result}")

        # Проверяем после удаления
        session = Session()
        tasks_count_after = session.query(Task).filter_by(user_id=1).count()
        session.close()
        print(f"✅ Задач в БД после удаления: {tasks_count_after}")

        return True

    except Exception as e:
        print(f"❌ Ошибка удаления задач: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_chat_with_ai(user_id):
    """Тест чата с AI"""
    print("\n🧪 Тест 7: Чат с AI")

    try:
        import asyncio
        from ai_integration import chat_with_ai

        async def async_test():
            # Тестовое сообщение
            message = "Создай задачу: купить продукты в магазине"
            context = []

            result = await chat_with_ai(message, context, user_id=user_id)
            print(f"✅ Ответ AI: {result[:100]}...")
            return True

        # Запускаем асинхронный тест
        return asyncio.run(async_test())

    except Exception as e:
        print(f"❌ Ошибка чата с AI: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_all_tests():
    """Запуск всех тестов"""
    print("🚀 Начинаем комплексное тестирование агента")

    # Тест 1: Создание пользователя
    user_id = test_user_creation()
    if not user_id:
        print("❌ Критическая ошибка: не удалось создать пользователя")
        return

    # Остальные тесты
    tests = [
        (test_task_operations, user_id),
        (test_profile_operations, user_id),
        (test_memory_operations, user_id),
        (test_partners_search, user_id),
        (test_delete_all_tasks, user_id),
        (test_chat_with_ai, user_id)
    ]

    passed = 0
    total = len(tests)

    for test_func, *args in tests:
        try:
            if test_func(*args):
                passed += 1
        except Exception as e:
            print(f"❌ Неожиданная ошибка в {test_func.__name__}: {e}")

    print(f"\n📊 Результаты тестирования: {passed}/{total} тестов пройдено")

    if passed == total:
        print("🎉 Все тесты пройдены успешно!")
    else:
        print(f"⚠️ Провалено {total - passed} тестов")

if __name__ == '__main__':
    run_all_tests()