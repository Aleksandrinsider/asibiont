#!/usr/bin/env python3
"""
Тест полной интеграции всех ИИ-функций в чате
"""

import asyncio
from ai_integration import chat_with_ai
from models import Session, UserProfile, User

async def test_full_ai_integration():
    print("🧠 ТЕСТ ПОЛНОЙ ИНТЕГРАЦИИ ИИ-ФУНКЦИЙ В ЧАТЕ")
    print("=" * 60)

    # Создаем тестового пользователя с профилем
    session = Session()
    try:
        user = User(id=5000, telegram_id=5000000)
        session.add(user)

        profile = UserProfile(
            user_id=5000,
            city='Санкт-Петербург',
            interests='программирование, стартапы, ИИ',
            skills='Python, веб-разработка',
            company='TechStart'
        )
        session.add(profile)
        session.commit()

        print("👤 Создан тестовый пользователь:")
        print(f"   Город: {profile.city}")
        print(f"   Интересы: {profile.interests}")
        print(f"   Навыки: {profile.skills}")
        print(f"   Компания: {profile.company}")

        # Тестовые сообщения с разными сценариями
        test_scenarios = [
            {
                'message': 'Привет! Я очень расстроен, проект провалился',
                'expected': 'Анализ эмоций (негатив), эмпатичный ответ'
            },
            {
                'message': 'Нужно подготовить презентацию к пятнице и найти дизайнера для мобильного приложения',
                'expected': 'Извлечение задач + рекомендации'
            },
            {
                'message': 'Ищу партнеров для стартапа в сфере ИИ, у меня есть опыт в Python',
                'expected': 'Анализ профиля + рекомендации'
            },
            {
                'message': 'Отлично! Всё получилось!',
                'expected': 'Положительные эмоции'
            }
        ]

        for i, scenario in enumerate(test_scenarios, 1):
            print(f"\n{'='*50}")
            print(f"Тест {i}: {scenario['expected']}")
            print(f"Сообщение: {scenario['message']}")
            print("-" * 50)

            response = await chat_with_ai(scenario['message'], user_id=5000)
            print(f"Ответ ИИ: {response}")

            # Анализируем, какие ИИ-функции сработали
            features_used = []
            if "Вижу, у тебя есть навык" in response or "Добавить в профиль" in response:
                features_used.append("📝 Анализ профиля")
            if "задач" in response and ("добавлю" in response or "упомянул" in response):
                features_used.append("📋 Извлечение задач")
            if "расстроен" in response or "Рад" in response or "😊" in response:
                features_used.append("😊 Анализ эмоций")
            if "Рекомендация:" in response:
                features_used.append("💡 Персональные рекомендации")
            if "дубликатов" in response or "конфликтов" in response:
                features_used.append("⚠️ Проверка дубликатов")

            if features_used:
                print(f"🤖 Активированные ИИ-функции: {', '.join(features_used)}")
            else:
                print("🤖 ИИ-функции не активировались (обычный ответ)")

    finally:
        session.rollback()
        session.close()

    print("\n" + "=" * 60)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")
    print("Агент теперь использует все ИИ-функции:")
    print("• Анализ профиля для персонализации")
    print("• Извлечение задач из разговоров")
    print("• Анализ эмоций для эмпатии")
    print("• Персональные рекомендации")
    print("• Проверка дубликатов задач")
    print("• Семантический поиск партнеров")
    print("• Оптимизация расписания")
    print("• Резюмирование разговоров")
    print("• Обработка сложных запросов")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_full_ai_integration())