#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Комплексное тестирование всех функций AI агента"""

import sys
import os
import asyncio

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

from models import Session, User, Task, UserProfile, Interaction
from datetime import datetime, timezone
from ai_integration import chat_with_ai

async def test_comprehensive_ai_agent():
    """Комплексное тестирование всех функций AI агента"""
    print("🚀 Начинаем комплексное тестирование AI агента...")

    # Создаем тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(
            telegram_id=123456789,
            username="test_user",
            first_name="Test",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()
    else:
        # Обновляем timezone если не установлен
        if not user.timezone:
            user.timezone = "Europe/Moscow"
            session.commit()
    telegram_id = user.telegram_id
    session.close()

    # Очищаем старые задачи и профиль
    session = Session()
    session.query(Task).filter_by(user_id=user.id).delete()
    session.query(UserProfile).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()

    test_results = []

    # Тест 1: Создание задач с различными форматами времени
    print("\n📝 Тест 1: Создание задач с различными форматами времени")
    scenarios_1 = [
        {
            "name": "Создание задачи с абсолютным временем",
            "messages": ["Создай задачу: купить продукты завтра в 10 утра"]
        },
        {
            "name": "Создание задачи с относительным временем",
            "messages": ["Создай задачу: позвонить маме через 2 часа"]
        },
        {
            "name": "Создание задачи без времени, затем уточнение",
            "messages": ["Создай задачу: сходить в спортзал", "Завтра в 8 вечера"]
        }
    ]

    for scenario in scenarios_1:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Тест 2: Просмотр и управление задачами
    print("\n👀 Тест 2: Просмотр и управление задачами")
    scenarios_2 = [
        {
            "name": "Просмотр всех задач",
            "messages": ["Покажи мои задачи"]
        },
        {
            "name": "Завершение задачи",
            "messages": ["Заверши задачу купить продукты"]
        },
        {
            "name": "Обновление задачи",
            "messages": ["Измени время задачи 'позвонить маме' на послезавтра в 11 утра"]
        }
    ]

    for scenario in scenarios_2:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Тест 3: Работа с профилем
    print("\n👤 Тест 3: Работа с профилем")
    scenarios_3 = [
        {
            "name": "Обновление навыков",
            "messages": ["Мои навыки: Python, JavaScript, SQL, машинное обучение"]
        },
        {
            "name": "Обновление интересов",
            "messages": ["Мои интересы: искусственный интеллект, разработка ПО, чтение технической литературы"]
        },
        {
            "name": "Обновление информации о работе",
            "messages": ["Я работаю в IT компании, занимаюсь разработкой веб-приложений"]
        },
        {
            "name": "Просмотр профиля",
            "messages": ["Расскажи о моем профиле"]
        }
    ]

    for scenario in scenarios_3:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Тест 4: Делегирование задач
    print("\n👥 Тест 4: Делегирование задач")
    scenarios_4 = [
        {
            "name": "Создание задачи для делегирования",
            "messages": ["Создай задачу: подготовить презентацию для клиента", "Завтра в 14:00"]
        },
        {
            "name": "Делегирование задачи",
            "messages": ["Делегируй задачу 'подготовить презентацию' пользователю @testuser с инструкцией 'сделать красивые слайды'"]
        },
        {
            "name": "Просмотр делегированных задач",
            "messages": ["Какие задачи я делегировал?"]
        }
    ]

    for scenario in scenarios_4:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Тест 5: Анализ и рекомендации
    print("\n📊 Тест 5: Анализ и рекомендации")
    scenarios_5 = [
        {
            "name": "Анализ задач",
            "messages": ["Проанализируй мои задачи и дай советы"]
        },
        {
            "name": "Рекомендации по продуктивности",
            "messages": ["Как мне стать продуктивнее?"]
        },
        {
            "name": "Планирование дня",
            "messages": ["Помоги спланировать мой день"]
        }
    ]

    for scenario in scenarios_5:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Тест 6: Обработка ошибок и edge cases
    print("\n⚠️ Тест 6: Обработка ошибок и edge cases")
    scenarios_6 = [
        {
            "name": "Неизвестная команда",
            "messages": ["Сделай что-то непонятное"]
        },
        {
            "name": "Попытка завершить несуществующую задачу",
            "messages": ["Заверши задачу 'несуществующая задача 123'"]
        },
        {
            "name": "Некорректное время",
            "messages": ["Создай задачу: тест в неправильное время"]
        }
    ]

    for scenario in scenarios_6:
        print(f"  ▶️ {scenario['name']}")
        try:
            for i, message in enumerate(scenario['messages']):
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"    Пользователь: {message}")
                print(f"    AI: {response[:100]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)
            test_results.append(f"✅ {scenario['name']}")
        except Exception as e:
            print(f"    ❌ Ошибка: {e}")
            test_results.append(f"❌ {scenario['name']}: {e}")

    # Итоги тестирования
    print("\n" + "="*60)
    print("📋 ИТОГИ КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ")
    print("="*60)

    success_count = sum(1 for result in test_results if result.startswith("✅"))
    total_count = len(test_results)

    print(f"Всего тестов: {total_count}")
    print(f"Успешных: {success_count}")
    print(f"Неудачных: {total_count - success_count}")

    print("\nПодробные результаты:")
    for result in test_results:
        print(f"  {result}")

    if success_count == total_count:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ УСПЕШНО! AI агент готов к работе!")
    else:
        print(f"\n⚠️ {total_count - success_count} тестов не прошли. Нужно доработать.")

    return success_count == total_count

if __name__ == "__main__":
    result = asyncio.run(test_comprehensive_ai_agent())
    sys.exit(0 if result else 1)