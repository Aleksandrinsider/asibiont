import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_agent_quick():
    """Быстрый тест агента на ключевых сценариях"""

    print("🤖 БЫСТРЫЙ ТЕСТ АГЕНТА ASI BIONT\n")

    # Получаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Ключевые сценарии
    test_scenarios = [
        {
            "query": "Привет!",
            "description": "Приветствие",
            "expected_contains": ["привет"]
        },
        {
            "query": "Расскажи о AI",
            "description": "Запрос информации",
            "expected_contains": ["ai", "искусственный"]
        },
        {
            "query": "Где найти Python разработчиков?",
            "description": "Поиск контактов",
            "expected_contains": ["контакт", "python"]
        },
        {
            "query": "Создай задачу: тест завтра в 10:00",
            "description": "Создание задачи",
            "expected_contains": ["задач", "создан"]
        },
        {
            "query": "Что у меня запланировано?",
            "description": "Проверка задач",
            "expected_contains": ["задач"]
        }
    ]

    results = []

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🧪 Тест {i}/5: {scenario['description']}")
        print(f"   ❓ '{scenario['query']}'")

        try:
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.telegram_id
            )

            if isinstance(result, dict):
                response = result.get('response', '')
                tools_used = result.get('tools_used', [])

                print(f"   📝 Ответ: {len(response)} символов")
                print(f"   🔧 Инструменты: {tools_used}")

                # Проверки
                response_lower = response.lower()
                expected_found = any(keyword.lower() in response_lower
                                    for keyword in scenario['expected_contains'])
                has_errors = any(error_word in response_lower
                               for error_word in ['ошибка', 'error', 'exception'])
                has_content = len(response.strip()) > 5

                success = expected_found and not has_errors and has_content

                status = "✅ УСПЕХ" if success else "❌ ПРОВАЛ"
                print(f"   {status}")

                results.append({
                    "scenario": scenario["description"],
                    "success": success,
                    "response_length": len(response),
                    "tools_used": len(tools_used)
                })

            else:
                print("   ❌ ОШИБКА: Неправильный формат ответа")
                results.append({"scenario": scenario["description"], "success": False})

        except Exception as e:
            print(f"   ❌ ИСКЛЮЧЕНИЕ: {str(e)}")
            results.append({"scenario": scenario["description"], "success": False})

    # Итоги
    successful = sum(1 for r in results if r["success"])
    total = len(results)

    print("\n📊 РЕЗУЛЬТАТЫ:")
    print(f"✅ Успешно: {successful}/{total} ({successful/total*100:.1f}%)")

    if successful == total:
        print("🎉 АГЕНТ ОТРАБАТЫВАЕТ 100% ЗАПРОСОВ!")
    elif successful >= total * 0.8:
        print("👍 АГЕНТ РАБОТАЕТ ХОРОШО!")
    else:
        print("⚠️ ТРЕБУЮТСЯ ДОРАБОТКИ!")

if __name__ == '__main__':
    asyncio.run(test_agent_quick())