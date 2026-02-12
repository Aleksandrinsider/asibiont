import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_motivational_approach():
    print("🎯 ТЕСТ МОТИВАЦИОННОГО ПОДХОДА\n")

    # Create test user
    user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')

    # Test queries that should motivate and use tools
    test_scenarios = [
        {
            'query': 'Как приготовить пасту карбонара?',
            'expected_tools': ['research_topic'],
            'motivation_check': ['тренд', 'крут', 'интерес', 'сейчас', 'посмотри'],
            'description': 'Кулинария - должен мотивировать через тренды'
        },
        {
            'query': 'Какие фильмы посмотреть на выходных?',
            'expected_tools': ['research_topic'],
            'motivation_check': ['рекоменд', 'эпичн', 'крут', 'посмотри'],
            'description': 'Фильмы - должен мотивировать через интерес'
        },
        {
            'query': 'Как найти новых друзей в этом городе?',
            'expected_tools': ['find_partners'],
            'motivation_check': ['единомышленник', 'похож', 'обмен', 'знаком'],
            'description': 'Знакомства - должен мотивировать через связи'
        },
        {
            'query': 'Упражнения для дома без оборудования',
            'expected_tools': ['research_topic'],
            'motivation_check': ['доказан', 'методик', 'результат', 'попробуй'],
            'description': 'Здоровье - должен мотивировать через результаты'
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
                user_id=user.id,
                db_session=session
            )

            response = result['response']
            tool_calls = result.get('tool_calls', [])
            used_tools = [call.get('function', {}).get('name', '') for call in tool_calls]

            print(f"   📝 Ответ: {len(response)} символов")
            print(f"   🔧 Инструменты: {used_tools if used_tools else 'нет'}")

            # Check tool usage
            tools_used = any(tool in used_tools for tool in scenario['expected_tools'])

            # Check motivation (voluntary task creation suggestion)
            motivated = any(word in response.lower() for word in scenario['motivation_check'])

            # Check if suggests task creation voluntarily
            task_suggestion = 'создай' in response.lower() or 'задач' in response.lower()

            success = tools_used and motivated

            if success:
                print("   ✅ МОТИВАЦИОННЫЙ ПОДХОД РАБОТАЕТ")
                if task_suggestion:
                    print("   💡 + добровольное предложение задач")
                successful_tests += 1
            else:
                issues = []
                if not tools_used: issues.append("инструменты не использованы")
                if not motivated: issues.append("нет мотивации")
                print(f"   ⚠️ ПРОБЛЕМЫ: {', '.join(issues)}")

            session.close()

        except Exception as e:
            print(f"   ✗ Ошибка: {e}")

        print()

    # Final results
    success_rate = (successful_tests / total_tests) * 100
    print(f"🎯 РЕЗУЛЬТАТ: {successful_tests}/{total_tests} ({success_rate:.1f}%)")

    if success_rate >= 80:
        print("🏆 ОТЛИЧНО! Агент мотивирует и использует инструменты!")
    elif success_rate >= 50:
        print("👍 ХОРОШО! Есть прогресс в мотивации.")
    else:
        print("🔧 НУЖНО УЛУЧШИТЬ мотивационный подход.")

if __name__ == "__main__":
    asyncio.run(test_motivational_approach())