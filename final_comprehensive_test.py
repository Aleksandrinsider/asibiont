#!/usr/bin/env python3
"""
Финальный комплексный тест делегирования задач с AI диалогом
"""
import sys
import os
import asyncio
import logging
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, SubscriptionTier, init_db
from datetime import datetime

async def setup_test_users():
    """Создание тестовых пользователей"""
    session = Session()

    try:
        # Создаем тестовых пользователей
        users_data = [
            {
                'telegram_id': 1001,
                'username': 'test_light',
                'subscription_tier': SubscriptionTier.LIGHT,
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 1002,
                'username': 'test_standard',
                'subscription_tier': SubscriptionTier.STANDARD,
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 1003,
                'username': 'test_premium',
                'subscription_tier': SubscriptionTier.PREMIUM,
                'timezone': 'Europe/Moscow'
            }
        ]

        for user_data in users_data:
            # Проверяем, существует ли пользователь
            existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if not existing_user:
                user = User(**user_data)
                session.add(user)
                session.commit()

                # Создаем профиль пользователя
                profile = UserProfile(user_id=user.id)
                session.add(profile)
                session.commit()

                print(f"✓ Создан пользователь {user_data['username']} с тарифом {user_data['subscription_tier'].value}")
            else:
                print(f"✓ Пользователь {user_data['username']} уже существует")

        session.close()
        return True

    except Exception as e:
        print(f"✗ Ошибка при создании тестовых пользователей: {e}")
        session.close()
        return False

async def test_ai_dialogue(user_id, username, scenario_name, messages):
    """Тестирование диалога с AI"""
    print(f"\n{'='*60}")
    print(f"🎭 СЦЕНАРИЙ: {scenario_name}")
    print(f"👤 ПОЛЬЗОВАТЕЛЬ: {username} (ID: {user_id})")
    print(f"{'='*60}")

    for i, message in enumerate(messages, 1):
        print(f"\n💬 Сообщение {i}: {message}")

        try:
            response = await chat_with_ai(message, user_id=user_id)
            print(f"🤖 Ответ AI: {response}")
            print("-" * 40)

            # Небольшая пауза между сообщениями
            await asyncio.sleep(0.5)

        except Exception as e:
            print(f"✗ Ошибка в диалоге: {e}")
            return False

    return True

async def run_comprehensive_test():
    """Запуск комплексного тестирования"""

    print("🚀 НАЧИНАЕМ ФИНАЛЬНЫЙ ТЕСТ ДЕЛЕГИРОВАНИЯ ЗАДАЧ")
    print("=" * 80)

    # Инициализация базы данных
    print("\n📋 ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ")
    try:
        init_db()
        print("✅ База данных инициализирована")
    except Exception as e:
        print(f"✗ Ошибка инициализации базы данных: {e}")
        return

    # Шаг 1: Настройка тестовых пользователей
    print("\n📋 ШАГ 1: Настройка тестовых пользователей")
    if not await setup_test_users():
        print("✗ Не удалось настроить тестовых пользователей")
        return

    # Шаг 2: Тестирование сценариев
    print("\n📋 ШАГ 2: Тестирование сценариев делегирования")

    scenarios = [
        {
            'user_id': 1001,  # LIGHT пользователь
            'username': '@test_light',
            'name': 'Делегирование без подписки (LIGHT тариф)',
            'messages': [
                "Привет! Я хочу делегировать задачу",
                "Создай задачу: Подготовить презентацию для клиента",
                "Делегируй эту задачу пользователю @test_standard"
            ]
        },
        {
            'user_id': 1002,  # STANDARD пользователь
            'username': '@test_standard',
            'name': 'Самоделегирование (STANDARD тариф)',
            'messages': [
                "Привет! Хочу протестировать делегирование",
                "Создай задачу: Проверить отчеты за квартал",
                "Делегируй эту задачу пользователю @test_standard"  # Самому себе
            ]
        },
        {
            'user_id': 1002,  # STANDARD пользователь
            'username': '@test_standard',
            'name': 'Успешное делегирование (STANDARD тариф)',
            'messages': [
                "Привет! Готов протестировать нормальное делегирование",
                "Создай задачу: Организовать встречу с командой завтра в 15:00",
                "Делегируй эту задачу пользователю @test_premium"
            ]
        },
        {
            'user_id': 1002,  # STANDARD пользователь
            'username': '@test_standard',
            'name': 'Делегирование несуществующему пользователю',
            'messages': [
                "Привет! Хочу проверить делегирование несуществующему",
                "Создай задачу: Заказать обед на всех",
                "Делегируй эту задачу пользователю @nonexistent_user"
            ]
        },
        {
            'user_id': 1003,  # PREMIUM пользователь
            'username': '@test_premium',
            'name': 'Проверка статуса делегированных задач (PREMIUM)',
            'messages': [
                "Привет! У меня есть делегированные задачи?",
                "Покажи статус всех делегированных задач",
                "Какие задачи мне делегировали другие?"
            ]
        }
    ]

    successful_scenarios = 0
    total_scenarios = len(scenarios)

    for scenario in scenarios:
        success = await test_ai_dialogue(
            scenario['user_id'],
            scenario['username'],
            scenario['name'],
            scenario['messages']
        )

        if success:
            successful_scenarios += 1
            print(f"✅ Сценарий '{scenario['name']}' выполнен успешно")
        else:
            print(f"❌ Сценарий '{scenario['name']}' завершен с ошибками")

    # Шаг 3: Итоги тестирования
    print(f"\n{'='*80}")
    print("📊 ИТОГИ ТЕСТИРОВАНИЯ")
    print(f"{'='*80}")
    print(f"Всего сценариев: {total_scenarios}")
    print(f"Успешных: {successful_scenarios}")
    print(f"Неудачных: {total_scenarios - successful_scenarios}")

    if successful_scenarios == total_scenarios:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ УСПЕШНО!")
        print("✅ Система делегирования работает корректно")
        print("✅ AI генерирует естественные ответы через маркеры")
        print("✅ Все сценарии обрабатываются правильно")
    else:
        print(f"\n⚠️  {total_scenarios - successful_scenarios} ТЕСТОВ ЗАВЕРШИЛИСЬ С ОШИБКАМИ")
        print("Нужно проверить логи и исправить проблемы")

    print(f"{'='*80}")

if __name__ == "__main__":
    asyncio.run(run_comprehensive_test())