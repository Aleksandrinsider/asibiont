import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_agent_comprehensive():
    """Комплексный тест агента на реальных сценариях использования"""

    print("🤖 КОМПЛЕКСНЫЙ ТЕСТ АГЕНТА ASI BIONT\n")

    # Получаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Реальные сценарии использования
    test_scenarios = [
        # 1. Приветствие
        {
            "query": "Привет! Как дела?",
            "description": "Приветствие",
            "expected_contains": ["привет", "задач"]
        },

        # 2. Запрос информации
        {
            "query": "Расскажи о последних трендах в искусственном интеллекте",
            "description": "Запрос информации",
            "expected_contains": ["тренд", "AI", "искусственный"]
        },

        # 3. Поиск контактов
        {
            "query": "Где найти единомышленников по Python разработке?",
            "description": "Поиск контактов",
            "expected_contains": ["контакт", "python", "разработчик"]
        },

        # 4. Создание задачи
        {
            "query": "Создай задачу: изучить асинхронное программирование в Python завтра в 10:00",
            "description": "Создание задачи",
            "expected_contains": ["задач", "создан", "10:00"]
        },

        # 5. Проверка задач
        {
            "query": "Что у меня запланировано?",
            "description": "Проверка задач",
            "expected_contains": ["задач", "план"]
        },

        # 6. Обновление профиля
        {
            "query": "Обнови мой профиль: я senior Python разработчик с опытом 5 лет",
            "description": "Обновление профиля",
            "expected_contains": ["профиль", "обнов"]
        },

        # 7. Показ профиля
        {
            "query": "Покажи мой профиль",
            "description": "Показ профиля",
            "expected_contains": ["профиль", "навык"]
        },

        # 8. Завершение задачи
        {
            "query": "Я закончил изучение Python основ",
            "description": "Завершение задачи",
            "expected_contains": ["задач", "заверш"]
        },

        # 9. Анализ рынка
        {
            "query": "Проанализируй рынок мобильных приложений",
            "description": "Анализ рынка",
            "expected_contains": ["анализ", "рынок", "приложени"]
        },

        # 10. Общий вопрос
        {
            "query": "Что ты умеешь?",
            "description": "Общий вопрос о возможностях",
            "expected_contains": ["ум", "могу", "помочь"]
        }
    ]

    results = []
    total_tests = len(test_scenarios)

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"\n🧪 Тест {i}/{total_tests}: {scenario['description']}")
        print(f"   ❓ '{scenario['query']}'")

        try:
            # Вызываем агента
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.telegram_id
            )

            if isinstance(result, dict):
                response = result.get('response', '')
                tools_used = result.get('tools_used', [])
                tool_calls = result.get('tool_calls', [])

                print(f"   📝 Ответ: {len(response)} символов")
                print(f"   🔧 Инструменты: {tools_used}")

                # Проверяем корректность ответа
                response_lower = response.lower()
                expected_found = any(keyword.lower() in response_lower
                                    for keyword in scenario['expected_contains'])

                # Проверяем отсутствие ошибок
                has_errors = any(error_word in response_lower
                               for error_word in ['ошибка', 'error', 'exception', 'traceback'])

                # Проверяем что ответ не пустой
                has_content = len(response.strip()) > 10

                success = expected_found and not has_errors and has_content

                if success:
                    print("   ✅ УСПЕХ: Корректный ответ")
                else:
                    print("   ❌ ПРОВАЛ: " +
                          ("Нет ожидаемого контента" if not expected_found else "") +
                          ("Есть ошибки" if has_errors else "") +
                          ("Пустой ответ" if not has_content else ""))

                results.append({
                    "scenario": scenario["description"],
                    "query": scenario["query"],
                    "success": success,
                    "response_length": len(response),
                    "tools_used": len(tools_used),
                    "has_errors": has_errors,
                    "has_expected_content": expected_found,
                    "has_content": has_content
                })

                # Показываем первые 100 символов ответа для отладки
                if not success:
                    print(f"   📄 Ответ: {response[:100]}{'...' if len(response) > 100 else ''}")

            else:
                print("   ❌ ОШИБКА: Неправильный формат ответа")
                results.append({
                    "scenario": scenario["description"],
                    "query": scenario["query"],
                    "success": False,
                    "error": "Wrong response format"
                })

        except Exception as e:
            print(f"   ❌ ИСКЛЮЧЕНИЕ: {str(e)}")
            results.append({
                "scenario": scenario["description"],
                "query": scenario["query"],
                "success": False,
                "error": str(e)
            })

    # Итоговый отчет
    print("\n" + "="*70)
    print("📊 ИТОГОВЫЙ ОТЧЕТ ПО АГЕНТУ ASI BIONT")
    print("="*70)

    successful = sum(1 for r in results if r.get("success", False))
    total = len(results)

    success_rate = successful / total * 100

    print(f"🎯 ОБЩАЯ УСПЕШНОСТЬ: {successful}/{total} ({success_rate:.1f}%)")

    # Детальный анализ
    print("
📈 АНАЛИЗ ПО КАТЕГОРИЯМ:"    print(f"✅ Успешные ответы: {successful}")
    print(f"❌ Неудачные ответы: {total - successful}")

    errors = sum(1 for r in results if r.get("has_errors", False))
    empty_responses = sum(1 for r in results if not r.get("has_content", True))
    missing_content = sum(1 for r in results if not r.get("has_expected_content", True))

    print(f"🚨 Ответы с ошибками: {errors}")
    print(f"📭 Пустые ответы: {empty_responses}")
    print(f"🎯 Пропущен ожидаемый контент: {missing_content}")

    # Средняя длина ответов
    avg_length = sum(r.get("response_length", 0) for r in results) / total
    print(".0f"
    # Использование инструментов
    avg_tools = sum(r.get("tools_used", 0) for r in results) / total
    print(".1f"
    print("
📋 ПОДРОБНЫЕ РЕЗУЛЬТАТЫ:"    for i, result in enumerate(results, 1):
        status = "✅" if result.get("success", False) else "❌"
        scenario = result.get("scenario", "Unknown")
        tools = result.get("tools_used", 0)
        length = result.get("response_length", 0)

        error_info = ""
        if result.get("has_errors"):
            error_info += " [ОШИБКА]"
        if not result.get("has_content"):
            error_info += " [ПУСТОЙ]"
        if not result.get("has_expected_content"):
            error_info += " [НЕТ КОНТЕНТА]"

        print(f"{i}. {status} {scenario} (🔧{tools}, 📝{length}симв){error_info}")

    # Рекомендации
    print("
🎯 РЕКОМЕНДАЦИИ:"    if success_rate >= 95:
        print("🎉 ОТЛИЧНО! Агент работает на высшем уровне!")
    elif success_rate >= 85:
        print("👍 ХОРОШО! Агент готов к продакшену с минорными доработками.")
    elif success_rate >= 70:
        print("⚠️ ТРЕБУЕТСЯ ДОРАБОТКА! Есть проблемы с некоторыми сценариями.")
    else:
        print("🚨 КРИТИЧНЫЕ ПРОБЛЕМЫ! Агент нуждается в серьезной доработке.")

    if errors > 0:
        print(f"   - Исправить {errors} ответов с ошибками")
    if empty_responses > 0:
        print(f"   - Добавить контент в {empty_responses} пустых ответов")
    if missing_content > 0:
        print(f"   - Улучшить релевантность {missing_content} ответов")

if __name__ == '__main__':
    asyncio.run(test_agent_comprehensive())