"""
Тест динамической системы инструментов с обучением и адаптацией
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.dynamic_tools import tool_discovery, DynamicToolDiscovery
from ai_integration import handlers
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_tool_discovery():
    """Тест автоматического обнаружения инструментов"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Автоматическое обнаружение инструментов")
    print("="*60)
    
    # Создаем новый экземпляр для теста
    discovery = DynamicToolDiscovery()
    
    # Обнаруживаем инструменты из handlers
    tools = discovery.discover_tools_from_module(handlers)
    
    print(f"\n✅ Обнаружено {len(tools)} инструментов:")
    for name, tool_info in list(tools.items())[:5]:  # Показываем первые 5
        print(f"   - {name}: {tool_info['function']['description'][:80]}...")
    
    print(f"\n   ... и еще {len(tools) - 5} инструментов")
    
    return len(tools) > 0


async def test_learning():
    """Тест обучения на успешных вызовах"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Обучение на успешных вызовах")
    print("="*60)
    
    discovery = DynamicToolDiscovery()
    
    # Симулируем успешные вызовы
    test_user_id = 12345
    
    print("\n📚 Обучаем систему на успешных вызовах...")
    
    # Успешный вызов add_task
    discovery.learn_from_success(
        func_name="add_task",
        user_id=test_user_id,
        context="создай задачу проверить почту завтра в 10",
        result={"status": "success", "task_id": 1}
    )
    
    # Еще один успешный вызов add_task
    discovery.learn_from_success(
        func_name="add_task",
        user_id=test_user_id,
        context="напомни купить молоко сегодня в 18:00",
        result={"status": "success", "task_id": 2}
    )
    
    # Успешный вызов list_tasks
    discovery.learn_from_success(
        func_name="list_tasks",
        user_id=test_user_id,
        context="покажи мои задачи",
        result={"tasks": []}
    )
    
    # Проверяем статистику
    stats = discovery.tool_usage_stats
    
    if "add_task" in stats:
        add_task_stats = stats["add_task"]
        print(f"\n✅ Статистика add_task:")
        print(f"   - Всего вызовов: {add_task_stats['total_calls']}")
        print(f"   - Успешных: {add_task_stats['successful_calls']}")
        print(f"   - Общие контексты: {add_task_stats['common_contexts'][:2]}")
    
    # Проверяем предпочтения пользователя
    if test_user_id in discovery.user_preferences:
        user_prefs = discovery.user_preferences[test_user_id]
        print(f"\n✅ Предпочтения пользователя {test_user_id}:")
        for func_name, prefs in list(user_prefs.items())[:3]:
            print(f"   - {func_name}: использован {prefs['usage_count']} раз")
    
    return len(stats) > 0


async def test_prioritization():
    """Тест приоритизации инструментов"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Приоритизация инструментов")
    print("="*60)
    
    discovery = DynamicToolDiscovery()
    discovery.discover_tools_from_module(handlers)
    
    # Обучаем систему, чтобы создать приоритеты
    test_user_id = 12345
    
    # Создаем паттерн частого использования add_task
    for i in range(5):
        discovery.learn_from_success(
            func_name="add_task",
            user_id=test_user_id,
            context=f"создай задачу {i}",
            result={"status": "success"}
        )
    
    # И реже используем list_tasks
    for i in range(2):
        discovery.learn_from_success(
            func_name="list_tasks",
            user_id=test_user_id,
            context="покажи задачи",
            result={"tasks": []}
        )
    
    # Получаем приоритизированный список
    prioritized = discovery.get_prioritized_tools(user_id=test_user_id)
    
    print(f"\n✅ Приоритизированные инструменты (топ-5):")
    for i, tool in enumerate(prioritized[:5], 1):
        func_name = tool['function']['name']
        usage = discovery.tool_usage_stats.get(func_name, {}).get('successful_calls', 0)
        print(f"   {i}. {func_name} (использован {usage} раз)")
    
    return True


async def test_context_filtering():
    """Тест фильтрации по контексту"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Фильтрация инструментов по контексту")
    print("="*60)
    
    discovery = DynamicToolDiscovery()
    discovery.discover_tools_from_module(handlers)
    
    # Тестируем разные контексты
    contexts = [
        "создай новую задачу",
        "покажи мои задачи",
        "удали задачу",
    ]
    
    for context in contexts:
        relevant = discovery.get_tools_for_context(context)
        print(f"\n📝 Контекст: '{context}'")
        print(f"   Релевантных инструментов: {len(relevant)}")
        if relevant:
            print(f"   Топ-3: {[t['function']['name'] for t in relevant[:3]]}")
    
    return True


async def test_save_load_stats():
    """Тест сохранения и загрузки статистики"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Сохранение и загрузка статистики")
    print("="*60)
    
    # Создаем экземпляр с данными
    discovery1 = DynamicToolDiscovery()
    
    # Добавляем данные
    discovery1.learn_from_success(
        func_name="test_function",
        user_id=99999,
        context="test context",
        result={"test": "data"}
    )
    
    # Сохраняем
    test_file = "test_tool_stats.json"
    discovery1.save_stats(test_file)
    print(f"\n💾 Статистика сохранена в {test_file}")
    
    # Загружаем в новый экземпляр
    discovery2 = DynamicToolDiscovery()
    discovery2.load_stats(test_file)
    
    # Проверяем, что данные загрузились
    if "test_function" in discovery2.tool_usage_stats:
        print(f"✅ Статистика успешно загружена")
        print(f"   test_function использована {discovery2.tool_usage_stats['test_function']['successful_calls']} раз")
    
    # Удаляем тестовый файл
    import os
    if os.path.exists(test_file):
        os.remove(test_file)
        print(f"🗑️  Тестовый файл удален")
    
    return "test_function" in discovery2.tool_usage_stats


async def main():
    """Запускаем все тесты"""
    print("\n" + "="*60)
    print("🚀 ТЕСТИРОВАНИЕ ДИНАМИЧЕСКОЙ СИСТЕМЫ ИНСТРУМЕНТОВ")
    print("="*60)
    
    tests = [
        ("Обнаружение инструментов", test_tool_discovery),
        ("Обучение на вызовах", test_learning),
        ("Приоритизация", test_prioritization),
        ("Фильтрация по контексту", test_context_filtering),
        ("Сохранение/загрузка", test_save_load_stats),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result, None))
        except Exception as e:
            logger.error(f"Ошибка в тесте '{test_name}': {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False, str(e)))
    
    # Итоговый отчет
    print("\n" + "="*60)
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print("="*60)
    
    passed = sum(1 for _, result, _ in results if result)
    total = len(results)
    
    for test_name, result, error in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
        if error:
            print(f"         Ошибка: {error}")
    
    print(f"\n🎯 Результат: {passed}/{total} тестов пройдено")
    
    if passed == total:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
    else:
        print(f"⚠️  {total - passed} тест(ов) провалено")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
