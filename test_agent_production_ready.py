#!/usr/bin/env python3
"""
РЕАЛЬНЫЙ ТЕСТ АГЕНТА В ПРОДАКШЕНЕ
Запускает агента с реальными данными и проверяет его поведение
"""

import sys
import os
import asyncio
import json
from datetime import datetime, timezone

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.chat import chat_with_ai
from models import init_db
from models import Session, User, UserProfile, Task, SubscriptionTier

def setup_test_data():
    """Создаём тестовые данные в БД"""

    # Инициализируем БД
    init_db()

    session = Session()

    try:
        # Создаём тестового пользователя
        test_user = session.query(User).filter_by(telegram_id=999999999).first()
        if not test_user:
            test_user = User(
                telegram_id=999999999,
                username="test_agent_user",
                timezone="Europe/Moscow",
                subscription_tier=SubscriptionTier.LIGHT
            )
            session.add(test_user)
            session.commit()

        # Создаём профиль
        profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=test_user.id,
                interests="AI, Python, разработка, стартапы",
                skills="программирование, анализ данных, машинное обучение",
                goals="создать AI-агента, найти команду, привлечь инвестиции",
                city="Москва"
            )
            session.add(profile)

        # Создаём тестовые задачи
        existing_tasks = session.query(Task).filter_by(user_id=test_user.id).count()
        if existing_tasks == 0:
            tasks_data = [
                ("Изучить Python асинхронность", datetime(2026, 2, 13, 10, 0, tzinfo=timezone.utc)),
                ("Найти дизайнера для UI", datetime(2026, 2, 12, 15, 0, tzinfo=timezone.utc)),
                ("Проанализировать конкурентов", None),
                ("Подготовить презентацию продукта", datetime(2026, 2, 14, 9, 0, tzinfo=timezone.utc))
            ]

            for title, reminder in tasks_data:
                task = Task(
                    user_id=test_user.id,
                    title=title,
                    reminder_time=reminder,
                    status='pending'
                )
                session.add(task)

        session.commit()
        print("✅ Тестовые данные созданы")

        return test_user.telegram_id

    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка создания тестовых данных: {e}")
        return None
    finally:
        session.close()

async def test_real_agent_behavior():
    """Тестируем агента с реальными данными"""

    print("🚀 РЕАЛЬНЫЙ ТЕСТ АГЕНТА С ПРОДАКШЕН ДАННЫМИ")
    print("=" * 70)

    # Создаём тестовые данные
    user_id = setup_test_data()
    if not user_id:
        return False

    test_scenarios = [
        {
            "message": "Привет",
            "expected_contains": ["задач", "на сегодня"],
            "forbidden_phrases": ["Чем помочь?", "Хочешь", "Могу предложить", "Что тебя интересует?"],
            "description": "Приветствие - должен показать задачи и дать идеи"
        },
        {
            "message": "Найди специалистов по AI",
            "expected_contains": ["нашёл", "@"],
            "forbidden_phrases": ["Хочешь", "Могу найти", "Что дальше?"],
            "description": "Поиск партнёров - должен вызвать find_partners()"
        },
        {
            "message": "Что происходит в сфере AI?",
            "expected_contains": ["тренд", "AI"],
            "forbidden_phrases": ["Могу проверить", "Хочешь узнать", "Посмотрим"],
            "description": "Анализ рынка - должен вызвать quick_topic_search()"
        },
        {
            "message": "У меня слишком много задач",
            "expected_contains": ["задач", "приоритет"],
            "forbidden_phrases": ["Хочешь", "Могу помочь", "Что делать?"],
            "description": "Анализ задач - должен вызвать list_tasks() и дать советы"
        },
        {
            "message": "Создать задачу 'Тестирование API' на завтра в 14:00",
            "expected_contains": ["создал", "завтра", "14:00"],
            "forbidden_phrases": ["На какое время", "Когда", "Подтверди"],
            "description": "Создание задачи - должен вызвать add_task() без вопросов"
        }
    ]

    results = []

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🎯 ТЕСТ {i}: {scenario['message']}")
        print(f"Описание: {scenario['description']}")

        try:
            # Вызываем реального агента
            result = await chat_with_ai(
                message=scenario['message'],
                user_id=user_id,
                db_session=None  # Агент сам создаст сессию
            )

            response = result.get('response', '') if isinstance(result, dict) else str(result)
            print(f"Ответ агента: {response[:200]}...")

            # Проверяем обязательные фразы
            has_expected = any(expected.lower() in response.lower() for expected in scenario['expected_contains'])
            if not has_expected:
                print(f"❌ НЕТ ожидаемого контента: {scenario['expected_contains']}")
                results.append(False)
                continue

            # Проверяем запрещенные фразы
            violations = []
            for forbidden in scenario['forbidden_phrases']:
                if forbidden.lower() in response.lower():
                    violations.append(forbidden)

            if violations:
                print(f"❌ ЗАПРЕЩЕННЫЕ ФРАЗЫ: {violations}")
                results.append(False)
            else:
                print("✅ ПРОШЁЛ: Корректное поведение агента")
                results.append(True)

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            results.append(False)

    # РЕЗУЛЬТАТЫ
    print("\n" + "=" * 70)
    print("📊 ИТОГИ РЕАЛЬНОГО ТЕСТИРОВАНИЯ")

    passed = sum(results)
    total = len(results)

    print(f"✅ Пройдено: {passed}/{total}")

    if passed == total:
        print("🎉 АГЕНТ ГОТОВ К ПРОДАКШЕНУ!")
        print("Он действительно действует автономно и даёт конкретные ответы.")
        return True
    else:
        print("⚠️ АГЕНТ НУЖДАЕТСЯ В ДОРАБОТКЕ ПРОМПТА")
        print("Он всё ещё предлагает варианты вместо конкретных действий.")
        return False

async def cleanup_test_data():
    """Очищаем тестовые данные"""

    session = Session()
    try:
        test_user = session.query(User).filter_by(telegram_id=999999999).first()
        if test_user:
            # Удаляем задачи
            session.query(Task).filter_by(user_id=test_user.id).delete()
            # Удаляем профиль
            session.query(UserProfile).filter_by(user_id=test_user.id).delete()
            # Удаляем пользователя
            session.delete(test_user)
            session.commit()
            print("🧹 Тестовые данные очищены")
    except Exception as e:
        print(f"❌ Ошибка очистки: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    try:
        # Запускаем реальный тест
        success = asyncio.run(test_real_agent_behavior())

        # Очищаем тестовые данные
        asyncio.run(cleanup_test_data())

        if success:
            print("\n🎊 ПРОДАКШЕН ГОТОВ!")
            sys.exit(0)
        else:
            print("\n🔧 НУЖНА ДОРАБОТКА ПРОМПТА")
            sys.exit(1)

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        sys.exit(1)