import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_all_tools():
    """Комплексный тест всех инструментов на реальных запросах"""

    print("🔧 КОМПЛЕКСНЫЙ ТЕСТ ВСЕХ ИНСТРУМЕНТОВ\n")

    # Получаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Тестовые сценарии для разных инструментов
    test_scenarios = [
        # 1. Приветствие - должен вызывать list_tasks
        {
            "query": "Привет! Как дела?",
            "expected_tools": ["list_tasks"],
            "description": "Приветствие"
        },

        # 2. Исследование - должен вызывать research_topic
        {
            "query": "Расскажи о последних трендах в искусственном интеллекте",
            "expected_tools": ["research_topic"],
            "description": "Исследование AI трендов"
        },

        # 3. Поиск партнеров - должен вызывать find_partners
        {
            "query": "Где найти единомышленников по Python разработке?",
            "expected_tools": ["find_partners"],
            "description": "Поиск партнеров по Python"
        },

        # 4. Создание задачи - должен вызывать add_task
        {
            "query": "Создай задачу: изучить асинхронное программирование в Python завтра в 10:00",
            "expected_tools": ["add_task"],
            "description": "Создание задачи с временем"
        },

        # 5. Комплексный анализ - должен вызывать research_and_plan (если PREMIUM)
        {
            "query": "Проанализируй рынок мобильных приложений и составь план продвижения",
            "expected_tools": ["research_and_plan"],
            "description": "Комплексный анализ рынка"
        },

        # 6. Завершение задачи - должен вызывать complete_task
        {
            "query": "Я закончил изучение Python основ",
            "expected_tools": ["complete_task"],
            "description": "Завершение задачи"
        },

        # 7. Обновление профиля - должен вызывать update_profile
        {
            "query": "Обнови мой профиль: я теперь senior Python разработчик с опытом 5 лет",
            "expected_tools": ["update_profile"],
            "description": "Обновление профиля"
        },

        # 8. Показ профиля - должен вызывать show_profile
        {
            "query": "Покажи мой профиль",
            "expected_tools": ["show_profile"],
            "description": "Показ профиля"
        }
    ]

    results = []

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🧪 Тест {i}/{len(test_scenarios)}: {scenario['description']}")
        print(f"   ❓ '{scenario['query']}'")

        try:
            # Вызываем AI
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.telegram_id
            )

            if isinstance(result, dict):
                tools_used = result.get('tools_used', [])
                response = result.get('response', '')

                print(f"   📝 Ответ: {len(response)} символов")
                print(f"   🔧 Вызванные инструменты: {tools_used}")
                print(f"   ✅ Ожидались: {scenario['expected_tools']}")

                # Проверяем результат
                success = any(tool in tools_used for tool in scenario['expected_tools'])
                if success:
                    print("   ✅ УСПЕХ: Правильный инструмент вызван")
                else:
                    print("   ❌ ПРОВАЛ: Ожидаемый инструмент не вызван")

                results.append({
                    "scenario": scenario["description"],
                    "query": scenario["query"],
                    "expected": scenario["expected_tools"],
                    "actual": tools_used,
                    "success": success,
                    "response_length": len(response)
                })

            else:
                print("   ❌ ОШИБКА: Неправильный формат ответа")
                results.append({
                    "scenario": scenario["description"],
                    "query": scenario["query"],
                    "expected": scenario["expected_tools"],
                    "actual": [],
                    "success": False,
                    "error": "Wrong response format"
                })

        except Exception as e:
            print(f"   ❌ ОШИБКА: {str(e)}")
            results.append({
                "scenario": scenario["description"],
                "query": scenario["query"],
                "expected": scenario["expected_tools"],
                "actual": [],
                "success": False,
                "error": str(e)
            })

    # Итоговый отчет
    print("\n" + "="*60)
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print("="*60)

    successful = sum(1 for r in results if r["success"])
    total = len(results)

    print(f"✅ Успешных тестов: {successful}/{total} ({successful/total*100:.1f}%)")

    print("\n📋 Детали по тестам:")
    for i, result in enumerate(results, 1):
        status = "✅" if result["success"] else "❌"
        print(f"{i}. {status} {result['scenario']}")
        print(f"   Ожидалось: {result['expected']}")
        print(f"   Получено: {result['actual']}")
        if "error" in result:
            print(f"   Ошибка: {result['error']}")

    # Рекомендации
    if successful == total:
        print("\n🎉 ОТЛИЧНО! Все инструменты работают корректно!")
    elif successful >= total * 0.8:
        print(f"\n👍 ХОРОШО! {successful}/{total} инструментов работают. Можно использовать в продакшене.")
    else:
        print(f"\n⚠️  ТРЕБУЕТСЯ ДОРАБОТКА! Только {successful}/{total} инструментов работают корректно.")

if __name__ == '__main__':
    asyncio.run(test_all_tools())