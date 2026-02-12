import asyncio
import sys
import logging
sys.path.append('.')

# Отключаем лишние логи для чистого вывода
logging.getLogger('ai_integration.handlers').setLevel(logging.WARNING)
logging.getLogger('ai_integration.utils').setLevel(logging.WARNING)
logging.getLogger('ai_integration.autonomous_agent').setLevel(logging.WARNING)

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def quick_function_test():
    """Быстрый тест ключевых функций на реальных запросах"""

    print("🧪 БЫСТРЫЙ ТЕСТ КЛЮЧЕВЫХ ФУНКЦИЙ ASI BIONT")
    print("=" * 50)

    # Получаем тестового пользователя
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=99999).first()
    if not user:
        print("❌ Test user not found!")
        return
    session.close()

    # Ключевые реальные запросы
    test_cases = [
        ("Привет! Что у меня запланировано?", ["list_tasks"], "Приветствие"),
        ("Расскажи о трендах AI в 2026", ["research_topic"], "Исследование"),
        ("Где найти Python разработчиков?", ["find_partners"], "Поиск партнеров"),
        ("Создай задачу: изучить FastAPI завтра в 10:00", ["check_time_conflicts", "add_task"], "Создание задачи"),
        ("Покажи мой профиль", ["show_profile"], "Просмотр профиля"),
        ("Я закончил задачу по документации", ["complete_task"], "Завершение задачи"),
    ]

    results = []

    for i, (query, expected_tools, category) in enumerate(test_cases, 1):
        print(f"\n{i}. {category}: '{query}'")

        try:
            result = await chat_with_ai(message=query, user_id=user.telegram_id)

            if isinstance(result, dict):
                tools_used = result.get('tools_used', [])
                response_len = len(result.get('response', ''))

                # Проверяем успех - хотя бы один из ожидаемых инструментов должен быть использован
                success = any(tool in tools_used for tool in expected_tools)

                status = "✅" if success else "❌"
                print(f"   {status} Инструменты: {tools_used} (ожидались: {expected_tools})")

                results.append({
                    "query": query,
                    "success": success,
                    "tools_used": tools_used,
                    "expected": expected_tools
                })
            else:
                print("   ❌ Ошибка формата ответа")
                results.append({"query": query, "success": False, "error": "Wrong format"})

        except Exception as e:
            print(f"   ❌ Ошибка: {str(e)}")
            results.append({"query": query, "success": False, "error": str(e)})

    # Итоги
    print("\n" + "=" * 50)
    successful = sum(1 for r in results if r.get("success", False))
    total = len(results)

    print(f"📊 РЕЗУЛЬТАТЫ: {successful}/{total} успешных тестов ({successful/total*100:.1f}%)")

    if successful == total:
        print("🎉 ОТЛИЧНО! Все функции работают корректно!")
    elif successful >= total * 0.8:
        print("👍 ХОРОШО! Большинство функций работает.")
    else:
        print("⚠️ Требуется доработка некоторых функций.")

    return results

if __name__ == '__main__':
    asyncio.run(quick_function_test())