#!/usr/bin/env python3
"""
Тест для проверки улучшенного режима всех типов сообщений AI
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем LOCAL=1 для использования SQLite в тестах
os.environ["LOCAL"] = "1"

from ai_integration import chat_with_ai
from models import Base, engine, Session, User, UserProfile

async def test_all_message_types():
    """Тестируем все типы сообщений с улучшенными требованиями"""

    # Инициализируем базу данных для тестов
    print("Создание таблиц...")
    Base.metadata.create_all(engine)
    print("Готово!")

    # Создаем тестового пользователя
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(
            telegram_id=123456789,
            username="test_user",
            timezone="Europe/Moscow"
        )
        session.add(test_user)
        session.commit()
        
        # Создаем профиль
        profile = UserProfile(user_id=test_user.id, city="Москва")
        session.add(profile)
        session.commit()
    session.close()

    print("🧪 ТЕСТИРОВАНИЕ УЛУЧШЕННОГО РЕЖИМА ВСЕХ ТИПОВ СООБЩЕНИЙ")
    print("=" * 70)

    # Тестовые сообщения для каждого типа
    test_cases = [
        {
            'type': 'reminder',
            'message': 'Напоминание о задаче: "Позвонить клиенту" в 14:30',
            'description': 'Напоминание о задаче'
        },
        {
            'type': 'proactive',
            'message': 'Вижу, что у тебя свободное время. Что планируешь делать?',
            'description': 'Проактивное сообщение'
        },
        {
            'type': 'result_check',
            'message': 'Как прошло выполнение задачи "Подготовить отчет"?',
            'description': 'Проверка результата'
        },
        {
            'type': 'daily_report',
            'message': 'Подведем итоги дня: выполнено 3 задачи, осталось 2',
            'description': 'Ежедневный отчет'
        },
        {
            'type': 'overdue',
            'message': 'У тебя просрочена задача "Встреча с партнером" (была на 15:00)',
            'description': 'Просроченные задачи'
        },
        {
            'type': 'system',
            'message': 'TASK_COMPLETED: Задача "Позвонить клиенту" завершена.',
            'description': 'Системное сообщение'
        }
    ]

    for test_case in test_cases:
        print(f"\n📋 ТЕСТ ТИПА '{test_case['type'].upper()}': {test_case['description']}")
        print(f"Сообщение: '{test_case['message']}'")
        print("-" * 60)

        try:
            # Отправляем сообщение с указанием типа
            response = await chat_with_ai(
                message=test_case['message'],
                user_id=123456789,
                context=None,
                file_content=None,
                message_type=test_case['type']  # Указываем тип сообщения
            )

            print(f"🤖 AI Response: '{response}'")

            # Проверки качества для всех типов сообщений
            issues = []

            # Длина: 2-4 абзаца, примерно 200-800 символов
            if len(response) < 200:
                issues.append("Ответ слишком короткий (<200 символов)")
            if len(response) > 800:
                issues.append("Ответ слишком длинный (>800 символов)")

            # Эмодзи не в начале текста
            forbidden_starts = ['✅', '🗑️', '✓', '🔄', '📅', '⏰', '🎯', '📋', '💡', '🚀', '🎉', '😊', '👍', '👎', '🤔', '💭', '📝', '🔍', '⚡', '🌟', '📌', '🔥', '💪', '🎊', '🎈', '🎁', '🏆', '⭐', '🌈', '💯']
            if any(response.startswith(emoji) for emoji in forbidden_starts):
                issues.append("Эмодзи в начале текста")

            # Минимум 2 абзаца
            paragraphs = [p.strip() for p in response.split('\n\n') if p.strip()]
            if len(paragraphs) < 2:
                issues.append("Меньше 2 абзацев")

            # Избегать шаблонных фраз
            template_phrases = ["отлично", "замечательно", "конечно", "хорошо", "понятно", "ясно", "отлично!", "замечательно!", "конечно!", "хорошо!", "понятно!", "ясно!"]
            if any(phrase in response.lower() for phrase in template_phrases):
                issues.append("Содержит шаблонные фразы")

            # Должен быть conversational тон
            conversational_words = [
                "интересно", "кстати", "знаешь", "представь", "вообще", "смотри", "слушай",
                "давай", "можешь", "стоит", "думаю", "чувствую", "вижу", "понимаю",
                "вообще-то", "на самом деле", "если честно", "понимаешь", "смотришь"
            ]
            has_personal_touch = any(word in response.lower() for word in conversational_words)
            if not has_personal_touch:
                issues.append("Отсутствует персональный, conversational тон")

            # Специфические проверки для каждого типа
            if test_case['type'] == 'reminder':
                # Для напоминаний должен быть вопрос о готовности
                has_question = any(word in response.lower() for word in ["готов", "начн", "сможешь", "план", "как насчет"])
                if not has_question:
                    issues.append("Напоминание без вопроса о готовности выполнить")

            elif test_case['type'] == 'proactive':
                # Проактивные сообщения должны содержать совет или идею
                has_advice = any(word in response.lower() for word in ["можно", "стоит", "попробуй", "предлагаю", "идея", "вариант"])
                if not has_advice:
                    issues.append("Проактивное сообщение без совета или идеи")

            elif test_case['type'] == 'result_check':
                # Проверка результата должна содержать интерес к деталям
                has_interest = any(word in response.lower() for word in ["как прошло", "что получилось", "результат", "успешно", "сложности"])
                if not has_interest:
                    issues.append("Проверка результата без интереса к деталям")

            elif test_case['type'] == 'daily_report':
                # Отчет должен содержать анализ или итоги
                has_analysis = any(word in response.lower() for word in ["итог", "анализ", "достижен", "прогресс", "завтра", "следующий"])
                if not has_analysis:
                    issues.append("Ежедневный отчет без анализа или итогов")

            elif test_case['type'] == 'overdue':
                # Просроченные задачи должны содержать предложения по исправлению
                has_solution = any(word in response.lower() for word in ["перенести", "отложить", "исправить", "решение", "вариант"])
                if not has_solution:
                    issues.append("Сообщение о просрочке без предложений по исправлению")

            elif test_case['type'] == 'system':
                # Системные сообщения должны содержать контекст и предложения
                has_context = any(word in response.lower() for word in ["теперь", "далее", "следующ", "альтернатив", "вариант"])
                if not has_context:
                    issues.append("Системное сообщение без контекста или предложений")

            if issues:
                print("⚠️  ПРОБЛЕМЫ НАЙДЕНЫ:")
                for issue in issues:
                    print(f"   - {issue}")
            else:
                print("✅ СООБЩЕНИЕ КАЧЕСТВЕННОЕ")

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")

    print("\n" + "=" * 70)
    print("🏁 ТЕСТИРОВАНИЕ ВСЕХ ТИПОВ СООБЩЕНИЙ ЗАВЕРШЕНО")

if __name__ == "__main__":
    asyncio.run(test_all_message_types())