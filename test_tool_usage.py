import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_tool_usage():
    print("🔧 ТЕСТ ИСПОЛЬЗОВАНИЯ ИНСТРУМЕНТОВ\n")

    # Use existing test user (from database)
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()  # Existing test user
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Test queries that should trigger tools
    test_scenarios = [
        {
            'query': 'Как приготовить пасту карбонара?',
            'expected_tools': ['research_topic'],  # Для поиска лучших рецептов
            'description': 'Кулинария - должен использовать research_topic'
        },
        {
            'query': 'Какие фильмы посмотреть на выходных?',
            'expected_tools': ['research_topic'],  # Для актуальных рекомендаций
            'description': 'Фильмы - должен использовать research_topic'
        },
        {
            'query': 'Рекомендуй музыку для пробежки',
            'expected_tools': ['research_topic'],  # Для плейлистов и трендов
            'description': 'Музыка - должен использовать research_topic'
        },
        {
            'query': 'Как найти новых друзей в этом городе?',
            'expected_tools': ['find_partners'],  # Для поиска людей
            'description': 'Знакомства - должен использовать find_partners'
        },
        {
            'query': 'Упражнения для дома без оборудования',
            'expected_tools': ['research_topic'],  # Для проверенных советов
            'description': 'Здоровье - должен использовать research_topic'
        },
        {
            'query': 'Привет! Что у меня запланировано?',
            'expected_tools': ['list_tasks'],  # Приветствие должно вызывать list_tasks
            'description': 'Приветствие - должен использовать list_tasks'
        }
    ]

    total_tests = len(test_scenarios)
    successful_tests = 0

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"🧪 Тест {i}/{total_tests}: {scenario['description']}")
        print(f"   ❓ {scenario['query']}")

        try:
            session = SessionLocal()
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.telegram_id,
                db_session=session
            )

            response = result['response']
            tool_calls = result.get('tool_calls', [])
            used_tools = [call.get('function', {}).get('name', '') for call in tool_calls]

            print(f"   📝 Ответ: {len(response)} символов")
            print(f"   🔧 Использованные инструменты: {used_tools if used_tools else 'НИ ОДНОГО'}")

            # Check if expected tools were used
            expected_used = any(tool in used_tools for tool in scenario['expected_tools'])

            if expected_used:
                print("   ✅ ИНСТРУМЕНТЫ ИСПОЛЬЗОВАНЫ ПРАВИЛЬНО")
                successful_tests += 1
            else:
                print(f"   ⚠️ ОЖИДАЛИСЬ: {scenario['expected_tools']}, но получили: {used_tools}")

            session.close()

        except Exception as e:
            print(f"   ✗ Ошибка: {e}")

        print()

    # Final results
    success_rate = (successful_tests / total_tests) * 100
    print(f"🎯 РЕЗУЛЬТАТ: {successful_tests}/{total_tests} ({success_rate:.1f}%)")

    if success_rate >= 80:
        print("🏆 ОТЛИЧНО! Агент активно использует инструменты!")
    elif success_rate >= 50:
        print("👍 ХОРОШО! Есть прогресс, но можно лучше.")
    else:
        print("🔧 НУЖНО УЛУЧШИТЬ использование инструментов.")

if __name__ == "__main__":
    asyncio.run(test_tool_usage())