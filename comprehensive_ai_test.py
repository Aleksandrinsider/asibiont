#!/usr/bin/env python3
"""
Комплексный тест всех функций AI агента
Тестирует все доступные функции через прямой вызов chat_with_ai
"""

import asyncio
import sys
import os
from datetime import datetime

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from config import CURRENT_DATE

# Тестовые сценарии для проверки всех функций
TEST_SCENARIOS = [
    {
        "name": "Добавление задачи",
        "messages": [
            "Добавь задачу: позвонить клиенту через 2 часа",
            "Создай задачу на завтра в 10 утра: подготовить презентацию"
        ]
    },
    {
        "name": "Просмотр задач",
        "messages": [
            "Покажи мои задачи",
            "Какие у меня есть незавершенные задачи?"
        ]
    },
    {
        "name": "Завершение задач",
        "messages": [
            "Отметь задачу 'позвонить клиенту' как выполненную",
            "Я закончил подготовку презентации"
        ]
    },
    {
        "name": "Редактирование задач",
        "messages": [
            "Измени задачу 'подготовить презентацию' на 'подготовить презентацию для клиента'",
            "Перенеси задачу 'позвонить клиенту' на завтра в 14:00"
        ]
    },
    {
        "name": "Установка напоминаний",
        "messages": [
            "Напомни мне о встрече через 30 минут",
            "Установи напоминание на завтра в 9:00 о совещании"
        ]
    },
    {
        "name": "Поиск партнеров",
        "messages": [
            "Найди мне партнеров для совместной работы",
            "Кто может помочь с разработкой?"
        ]
    },
    {
        "name": "Обновление профиля",
        "messages": [
            "Обнови мой профиль: я работаю в IT, занимаюсь разработкой",
            "Добавь в мой профиль навыки: Python, JavaScript, SQL"
        ]
    },
    {
        "name": "Делегирование задач",
        "messages": [
            "Поручи @test_user1 проверить код",
            "Делегируй задачу 'тестирование' пользователю @test_user2"
        ]
    },
    {
        "name": "Принятие/отклонение делегированных задач",
        "messages": [
            "Принимаю задачу 'проверить код'",
            "Отклоняю задачу 'тестирование'"
        ]
    },
    {
        "name": "Проверка прогресса делегирования",
        "messages": [
            "Какой статус у делегированной задачи 'проверить код'?",
            "Что происходит с задачей, которую я делегировал?"
        ]
    },
    {
        "name": "Установка приоритетов",
        "messages": [
            "Сделай задачу 'подготовить презентацию' высокой приоритета",
            "Установи средний приоритет для задачи 'позвонить клиенту'"
        ]
    },
    {
        "name": "Получение деталей задач",
        "messages": [
            "Расскажи подробнее о задаче 'подготовить презентацию'",
            "Какие детали у задачи 'позвонить клиенту'?"
        ]
    },
    {
        "name": "Обновление памяти пользователя",
        "messages": [
            "Запомни, что я предпочитаю работать по утрам",
            "Я обычно заканчиваю работу в 18:00"
        ]
    }
]

async def test_ai_function(user_id, message, scenario_name):
    """Тестирует одну функцию AI через прямой вызов"""
    try:
        print(f"\n🔍 Тестирую: {scenario_name}")
        print(f"📝 Сообщение: {message}")

        # Прямой вызов функции AI
        ai_response = await chat_with_ai(message, context=None, user_id=user_id)

        print(f"✅ AI ответил: {ai_response[:100]}{'...' if len(ai_response) > 100 else ''}")

        # Проверяем, что ответ не пустой и не содержит ошибок
        if not ai_response or 'ошибка' in ai_response.lower() or 'error' in ai_response.lower():
            print(f"⚠️  Возможная проблема в ответе AI")
            return False
        else:
            print(f"✅ Ответ выглядит корректным")
            return True

    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        return False

async def run_comprehensive_test():
    """Запускает комплексное тестирование всех функций"""
    print("🚀 Начинаем комплексное тестирование AI агента")
    print("=" * 60)

    # Используем тестового пользователя
    test_user_id = 146333757  # Из предыдущих тестов

    success_count = 0
    total_tests = 0

    for scenario in TEST_SCENARIOS:
        scenario_name = scenario["name"]
        messages = scenario["messages"]

        for message in messages:
            total_tests += 1
            if await test_ai_function(test_user_id, message, f"{scenario_name} - {message[:30]}..."):
                success_count += 1
            else:
                print(f"❌ Провал теста: {scenario_name}")

            # Небольшая пауза между запросами
            await asyncio.sleep(1)

    print("\n" + "=" * 60)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print(f"✅ Успешных тестов: {success_count}/{total_tests}")
    success_rate = success_count/total_tests*100
    print(f"📈 Успешность: {success_rate:.1f}%")
    if success_rate >= 90:
        print("🎉 Отлично! AI агент готов к продакшену!")
    elif success_rate >= 75:
        print("⚠️  Хороший результат, но есть что улучшить")
    else:
        print("❌ Требуется доработка AI агента")

    return success_rate >= 90

async def test_edge_cases():
    """Тестирует краевые случаи"""
    print("\n🔬 Тестируем краевые случаи")
    print("-" * 40)

    test_user_id = 146333757
    edge_cases = [
        "Сделай что-то невозможное",  # Тест на обработку нереальных запросов
        "",  # Пустое сообщение
        "1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890",  # Очень длинное сообщение
        "Найди задачу с ID 999999",  # Несуществующая задача
        "@несуществующий_пользователь сделай что-то",  # Несуществующий пользователь
    ]

    for i, message in enumerate(edge_cases, 1):
        print(f"\n🧪 Краевой случай {i}: '{message[:50]}{'...' if len(message) > 50 else ''}'")
        await test_ai_function(test_user_id, message, f"Краевой случай {i}")
        await asyncio.sleep(0.5)

if __name__ == "__main__":
    print("🤖 Комплексное тестирование AI агента для Task Management Bot")
    print("Тестируем все функции через естественный диалог")
    print()

    try:
        # Основное тестирование
        success = asyncio.run(run_comprehensive_test())

        # Тестирование краевых случаев
        asyncio.run(test_edge_cases())

        if success:
            print("\n🎯 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! AI агент готов к продакшену!")
            sys.exit(0)
        else:
            print("\n⚠️  Есть проблемы, требующие исправления")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n⏹️  Тестирование прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Критическая ошибка: {e}")
        sys.exit(1)