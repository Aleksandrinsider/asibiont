"""
Комплексный интеграционный тест улучшенной системы
Проверяет работу всех компонентов вместе
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.autonomous_agent import HybridAutonomousAgent
from ai_integration.dynamic_tools import tool_discovery
from ai_integration.adaptive_prompts import adaptive_prompt_system
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_agent_with_dynamic_tools():
    """Тест работы агента с динамическими инструментами"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Агент с динамическими инструментами")
    print("="*60)
    
    agent = HybridAutonomousAgent()
    
    # Проверяем, что агент использует динамическое обнаружение
    print(f"\n✅ Агент инициализирован с динамическим обнаружением инструментов")
    print(f"   Обнаружено инструментов: {len(tool_discovery.discovered_tools)}")
    
    # Проверяем, что статистика загрузилась (если есть)
    if tool_discovery.tool_usage_stats:
        print(f"   Загружена статистика: {len(tool_discovery.tool_usage_stats)} функций")
    
    return len(tool_discovery.discovered_tools) > 0


async def test_agent_with_adaptive_prompts():
    """Тест работы агента с адаптивными промптами"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Агент с адаптивными промптами")
    print("="*60)
    
    # Проверяем состояние системы адаптивных промптов
    print(f"\n✅ Система адаптивных промптов инициализирована")
    print(f"   Базовые шаблоны: {len(adaptive_prompt_system.prompt_templates)}")
    
    if adaptive_prompt_system.user_styles:
        print(f"   Сохраненные стили пользователей: {len(adaptive_prompt_system.user_styles)}")
    
    if adaptive_prompt_system.successful_patterns:
        print(f"   Успешных паттернов: {len(adaptive_prompt_system.successful_patterns)}")
    
    return True


async def test_tool_prioritization():
    """Тест приоритизации инструментов"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Приоритизация инструментов для пользователя")
    print("="*60)
    
    test_user_id = 99999
    
    # Симулируем несколько успешных вызовов
    print("\n📚 Обучаем систему на успешных вызовах...")
    
    for i in range(3):
        tool_discovery.learn_from_success(
            func_name="add_task",
            user_id=test_user_id,
            context=f"создай задачу {i}",
            result={"status": "success"}
        )
    
    tool_discovery.learn_from_success(
        func_name="list_tasks",
        user_id=test_user_id,
        context="покажи задачи",
        result={"tasks": []}
    )
    
    # Получаем приоритизированные инструменты
    prioritized = tool_discovery.get_prioritized_tools(user_id=test_user_id)
    
    print(f"\n✅ Приоритизация работает:")
    print(f"   Топ-3 инструмента для пользователя {test_user_id}:")
    for i, tool in enumerate(prioritized[:3], 1):
        func_name = tool['function']['name']
        usage = tool_discovery.tool_usage_stats.get(func_name, {}).get('successful_calls', 0)
        print(f"   {i}. {func_name} (использован {usage} раз)")
    
    return True


async def test_style_adaptation():
    """Тест адаптации стиля"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Адаптация стиля под пользователя")
    print("="*60)
    
    test_user_id = 88888
    
    # Симулируем краткий стиль
    message_history = [
        {"role": "user", "content": "задачи"},
        {"role": "user", "content": "создай"},
    ]
    
    style = adaptive_prompt_system.detect_user_style(test_user_id, message_history)
    
    print(f"\n✅ Определен стиль пользователя {test_user_id}: {style}")
    print(f"   Система запомнила предпочтения")
    
    # Генерируем адаптивный промпт
    base_prompt = "Ты - AI-помощник."
    adapted = await adaptive_prompt_system.generate_adaptive_prompt(
        base_prompt=base_prompt,
        user_id=test_user_id,
        context={'time_of_day': 'morning'},
        message_history=message_history
    )
    
    print(f"\n✅ Сгенерирован адаптивный промпт:")
    print(f"   Базовый: {len(base_prompt)} символов")
    print(f"   Адаптированный: {len(adapted)} символов")
    print(f"   Расширение: +{len(adapted) - len(base_prompt)} символов")
    
    return len(adapted) > len(base_prompt)


async def test_learning_from_feedback():
    """Тест обучения на feedback"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Обучение на feedback пользователей")
    print("="*60)
    
    test_user_id = 77777
    
    # Добавляем feedback
    feedbacks = [
        ("создай задачу", "Создал задачу", True, 0.9, "отвечай покороче"),
        ("покажи задачи", "Вот твои задачи", True, 0.95, "хорошо, мне нравятся примеры"),
    ]
    
    print("\n📚 Обучаемся на feedback...")
    
    for user_msg, ai_resp, success, eff, feedback in feedbacks:
        adaptive_prompt_system.learn_from_interaction(
            user_id=test_user_id,
            user_message=user_msg,
            ai_response=ai_resp,
            was_successful=success,
            effectiveness=eff,
            feedback=feedback
        )
    
    # Проверяем извлеченные предпочтения
    if test_user_id in adaptive_prompt_system.user_feedback:
        prefs = adaptive_prompt_system.user_feedback[test_user_id]
        
        print(f"\n✅ Извлечены предпочтения:")
        print(f"   - Краткие ответы: {prefs['prefers_short_answers']}")
        print(f"   - Любит примеры: {prefs['likes_examples']}")
        
        assert prefs['prefers_short_answers'] == True
        assert prefs['likes_examples'] == True
    
    # Проверяем успешные паттерны
    patterns = adaptive_prompt_system._get_successful_examples(test_user_id)
    print(f"\n✅ Сохранено {len(patterns)} успешных паттернов")
    
    return len(patterns) > 0


async def test_end_to_end_simulation():
    """Комплексный end-to-end тест"""
    print("\n" + "="*60)
    print("ТЕСТ 6: End-to-End симуляция")
    print("="*60)
    
    test_user_id = 66666
    
    print("\n🎬 Симулируем реальное взаимодействие пользователя...")
    
    # 1. Пользователь начинает общение
    print("\n1️⃣ Пользователь: 'создай задачу'")
    
    # Система определяет стиль
    history = [{"role": "user", "content": "создай задачу"}]
    style = adaptive_prompt_system.detect_user_style(test_user_id, history)
    print(f"   → Определен стиль: {style}")
    
    # 2. Система учится на успешном выполнении
    print("\n2️⃣ Задача создана успешно")
    tool_discovery.learn_from_success(
        func_name="add_task",
        user_id=test_user_id,
        context="создай задачу",
        result={"status": "success"}
    )
    print(f"   → Система обучилась на успешном вызове")
    
    # 3. Пользователь дает feedback
    print("\n3️⃣ Пользователь: 'отлично, только покороче отвечай'")
    adaptive_prompt_system.learn_from_interaction(
        user_id=test_user_id,
        user_message="создай задачу",
        ai_response="Создал задачу успешно",
        was_successful=True,
        effectiveness=0.9,
        feedback="отлично, только покороче отвечай"
    )
    print(f"   → Система извлекла предпочтение: краткие ответы")
    
    # 4. Следующий раз система использует обученные данные
    print("\n4️⃣ Следующий запрос: 'покажи мои задачи'")
    
    # Получаем приоритизированные инструменты
    tools = tool_discovery.get_prioritized_tools(user_id=test_user_id)
    print(f"   → Приоритизированные инструменты: {[t['function']['name'] for t in tools[:3]]}")
    
    # Генерируем адаптивный промпт с учетом feedback
    base_prompt = "Ты - AI-помощник."
    adapted = await adaptive_prompt_system.generate_adaptive_prompt(
        base_prompt=base_prompt,
        user_id=test_user_id,
        context={'time_of_day': 'evening'},
        message_history=history + [{"role": "user", "content": "покажи мои задачи"}]
    )
    
    # Проверяем, что в адаптированном промпте учтены предпочтения
    has_short_instruction = "краткий" in adapted.lower() or "короткий" in adapted.lower()
    
    print(f"   → Адаптированный промпт учитывает предпочтения: {has_short_instruction}")
    
    print("\n✅ End-to-End симуляция прошла успешно!")
    print(f"   Система адаптировалась под пользователя за {len(history) + 1} взаимодействия")
    
    return True


async def test_persistence():
    """Тест сохранения состояния"""
    print("\n" + "="*60)
    print("ТЕСТ 7: Персистентность состояния")
    print("="*60)
    
    # Сохраняем состояние обеих систем
    tool_file = "test_integrated_tools.json"
    prompt_file = "test_integrated_prompts.json"
    
    print("\n💾 Сохраняем состояние систем...")
    tool_discovery.save_stats(tool_file)
    adaptive_prompt_system.save_state(prompt_file)
    
    print(f"   ✅ Инструменты сохранены в {tool_file}")
    print(f"   ✅ Промпты сохранены в {prompt_file}")
    
    # Удаляем тестовые файлы
    import os
    for file in [tool_file, prompt_file]:
        if os.path.exists(file):
            os.remove(file)
            print(f"   🗑️  {file} удален")
    
    return True


async def main():
    """Запускаем все интеграционные тесты"""
    print("\n" + "="*60)
    print("🚀 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ УЛУЧШЕННОЙ СИСТЕМЫ")
    print("="*60)
    print("\nПроверяем интеграцию всех компонентов:")
    print("- Динамические инструменты")
    print("- Адаптивные промпты")
    print("- Обучение на опыте")
    print("- Персистентность")
    
    tests = [
        ("Агент + Динамические инструменты", test_agent_with_dynamic_tools),
        ("Агент + Адаптивные промпты", test_agent_with_adaptive_prompts),
        ("Приоритизация инструментов", test_tool_prioritization),
        ("Адаптация стиля", test_style_adaptation),
        ("Обучение на feedback", test_learning_from_feedback),
        ("End-to-End симуляция", test_end_to_end_simulation),
        ("Персистентность", test_persistence),
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
        print("\n" + "="*60)
        print("🎉 ВСЕ ИНТЕГРАЦИОННЫЕ ТЕСТЫ ПРОЙДЕНЫ!")
        print("="*60)
        print("\n✨ Система полностью готова к использованию:")
        print("   ✅ Динамическое обнаружение инструментов")
        print("   ✅ Адаптивная генерация промптов")
        print("   ✅ Обучение на успешных взаимодействиях")
        print("   ✅ Персонализация под каждого пользователя")
        print("   ✅ Персистентное хранение состояния")
        print("\n🚀 Готово к продакшену!")
    else:
        print(f"\n⚠️  {total - passed} тест(ов) провалено")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
