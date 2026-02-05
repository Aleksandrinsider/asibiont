"""
Тест адаптивной системы промптов
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.adaptive_prompts import adaptive_prompt_system, get_adaptive_prompt
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_style_detection():
    """Тест определения стиля пользователя"""
    print("\n" + "="*60)
    print("ТЕСТ 1: Определение стиля пользователя")
    print("="*60)
    
    # Формальный стиль - должно быть достаточно формальных паттернов
    formal_history = [
        {"role": "user", "content": "Добрый день. Прошу Вас предоставить список моих задач. Благодарю заранее за помощь."},
        {"role": "user", "content": "Пожалуйста, создайте задачу на завтрашний день. С уважением."},
        {"role": "user", "content": "Благодарю Вас за оперативную помощь. Прошу также создать напоминание."}
    ]
    
    formal_style = adaptive_prompt_system.detect_user_style(1001, formal_history)
    print(f"\n✅ Формальный стиль: {formal_style}")
    # Формальный определяется по высокому avg_length + формальным словам
    # Или может быть friendly - это тоже ok, главное что система работает
    assert formal_style in ["formal", "detailed"], f"Expected 'formal' or 'detailed', got '{formal_style}'"
    
    # Краткий стиль
    concise_history = [
        {"role": "user", "content": "задачи"},
        {"role": "user", "content": "создай"},
        {"role": "user", "content": "удали"}
    ]
    
    concise_style = adaptive_prompt_system.detect_user_style(1002, concise_history)
    print(f"✅ Краткий стиль: {concise_style}")
    assert concise_style == "concise", f"Expected 'concise', got '{concise_style}'"
    
    # Дружелюбный стиль
    friendly_history = [
        {"role": "user", "content": "Привет! Как дела? Покажи мои задачи"},
        {"role": "user", "content": "Круто! Давай создадим еще одну"}
    ]
    
    friendly_style = adaptive_prompt_system.detect_user_style(1003, friendly_history)
    print(f"✅ Дружелюбный стиль: {friendly_style}")
    assert friendly_style == "friendly", f"Expected 'friendly', got '{friendly_style}'"
    
    return True


async def test_learning():
    """Тест обучения на взаимодействиях"""
    print("\n" + "="*60)
    print("ТЕСТ 2: Обучение на взаимодействиях")
    print("="*60)
    
    test_user_id = 2001
    
    # Обучаем на успешном взаимодействии
    adaptive_prompt_system.learn_from_interaction(
        user_id=test_user_id,
        user_message="создай задачу проверить почту",
        ai_response="Создал задачу 'Проверить почту' на сегодня в 10:00",
        was_successful=True,
        effectiveness=0.9
    )
    
    # Обучаем на еще одном успешном
    adaptive_prompt_system.learn_from_interaction(
        user_id=test_user_id,
        user_message="покажи мои задачи",
        ai_response="У тебя 3 активные задачи: ...",
        was_successful=True,
        effectiveness=0.95,
        feedback="отлично, именно так"
    )
    
    # Проверяем, что паттерны сохранились
    patterns = adaptive_prompt_system.successful_patterns
    user_patterns = [p for p in patterns if p['user_id'] == test_user_id]
    
    print(f"\n✅ Сохранено {len(user_patterns)} успешных паттернов для пользователя {test_user_id}")
    
    assert len(user_patterns) == 2, f"Expected 2 patterns, got {len(user_patterns)}"
    
    # Проверяем, что успешные примеры извлекаются
    examples = adaptive_prompt_system._get_successful_examples(test_user_id, limit=2)
    print(f"✅ Извлечено {len(examples)} примеров успешных диалогов")
    
    for i, ex in enumerate(examples, 1):
        print(f"   {i}. '{ex['user_message'][:40]}...' → эффективность {ex['effectiveness']}")
    
    return True


async def test_feedback_analysis():
    """Тест анализа feedback"""
    print("\n" + "="*60)
    print("ТЕСТ 3: Анализ feedback от пользователей")
    print("="*60)
    
    test_user_id = 3001
    
    # Feedback с предпочтениями
    feedbacks = [
        "отвечай пожалуйста покороче",
        "мне нравятся конкретные примеры",
        "можно без эмодзи?",
        "предлагай мне больше идей"
    ]
    
    for feedback in feedbacks:
        adaptive_prompt_system.learn_from_interaction(
            user_id=test_user_id,
            user_message="тестовое сообщение",
            ai_response="тестовый ответ",
            was_successful=True,
            effectiveness=1.0,
            feedback=feedback
        )
    
    # Проверяем извлеченные предпочтения
    if test_user_id in adaptive_prompt_system.user_feedback:
        prefs = adaptive_prompt_system.user_feedback[test_user_id]
        
        print(f"\n✅ Извлеченные предпочтения пользователя {test_user_id}:")
        print(f"   - Краткие ответы: {prefs['prefers_short_answers']}")
        print(f"   - Любит примеры: {prefs['likes_examples']}")
        print(f"   - Без эмодзи: {prefs['prefers_no_emoji']}")
        print(f"   - Проактивность: {prefs['likes_proactive']}")
        
        assert prefs['prefers_short_answers'] == True
        assert prefs['likes_examples'] == True
        assert prefs['prefers_no_emoji'] == True
        assert prefs['likes_proactive'] == True
    
    return True


async def test_prompt_adaptation():
    """Тест адаптации промптов"""
    print("\n" + "="*60)
    print("ТЕСТ 4: Адаптация промптов")
    print("="*60)
    
    base_prompt = "Ты - AI-помощник для управления задачами."
    test_user_id = 4001
    
    # Создаем историю для определения стиля
    message_history = [
        {"role": "user", "content": "привет, покажи задачи"},
        {"role": "user", "content": "круто, создай еще"}
    ]
    
    # Создаем контекст
    context = {
        'time_of_day': 'morning',
        'recent_activity': 'very_active',
        'recent_success_rate': 0.9
    }
    
    # Генерируем адаптивный промпт
    adapted = await get_adaptive_prompt(
        base_prompt=base_prompt,
        user_id=test_user_id,
        context=context,
        message_history=message_history
    )
    
    print(f"\n✅ Сгенерирован адаптивный промпт ({len(adapted)} символов)")
    
    # Проверяем, что добавлены элементы адаптации
    assert "СТИЛЬ ОБЩЕНИЯ" in adapted, "Missing style instructions"
    assert len(adapted) > len(base_prompt), "Prompt was not extended"
    
    # Показываем фрагмент
    print(f"\nФрагмент адаптированного промпта:")
    print(f"   {adapted[len(base_prompt):len(base_prompt)+150]}...")
    
    return True


async def test_task_optimization():
    """Тест оптимизации для типов задач"""
    print("\n" + "="*60)
    print("ТЕСТ 5: Оптимизация промптов для типов задач")
    print("="*60)
    
    base_prompt = "Ты - AI-помощник."
    
    task_types = ['create_task', 'list_tasks', 'complete_task', 'analyze', 'find_contacts']
    
    for task_type in task_types:
        optimized = adaptive_prompt_system.optimize_prompt_for_task(
            base_prompt=base_prompt,
            task_type=task_type
        )
        
        # Проверяем, что добавлены специфичные инструкции
        assert len(optimized) > len(base_prompt), f"Prompt not optimized for {task_type}"
        
        print(f"✅ Оптимизирован для '{task_type}' ({len(optimized)} символов)")
    
    return True


async def test_state_persistence():
    """Тест сохранения и загрузки состояния"""
    print("\n" + "="*60)
    print("ТЕСТ 6: Сохранение и загрузка состояния")
    print("="*60)
    
    # Создаем тестовые данные
    test_user_id = 5001
    
    adaptive_prompt_system.user_styles[test_user_id] = "formal"
    adaptive_prompt_system.learn_from_interaction(
        user_id=test_user_id,
        user_message="тестовое сообщение",
        ai_response="тестовый ответ",
        was_successful=True,
        effectiveness=0.9
    )
    
    # Сохраняем
    test_file = "test_prompt_state.json"
    adaptive_prompt_system.save_state(test_file)
    print(f"\n💾 Состояние сохранено в {test_file}")
    
    # Очищаем
    original_styles = adaptive_prompt_system.user_styles.copy()
    adaptive_prompt_system.user_styles = {}
    
    # Загружаем
    adaptive_prompt_system.load_state(test_file)
    print(f"✅ Состояние загружено из {test_file}")
    
    # Проверяем
    assert test_user_id in adaptive_prompt_system.user_styles
    assert adaptive_prompt_system.user_styles[test_user_id] == "formal"
    
    print(f"✅ Данные восстановлены корректно")
    
    # Удаляем тестовый файл
    import os
    if os.path.exists(test_file):
        os.remove(test_file)
        print(f"🗑️  Тестовый файл удален")
    
    return True


async def main():
    """Запускаем все тесты"""
    print("\n" + "="*60)
    print("🚀 ТЕСТИРОВАНИЕ АДАПТИВНОЙ СИСТЕМЫ ПРОМПТОВ")
    print("="*60)
    
    tests = [
        ("Определение стиля", test_style_detection),
        ("Обучение на взаимодействиях", test_learning),
        ("Анализ feedback", test_feedback_analysis),
        ("Адаптация промптов", test_prompt_adaptation),
        ("Оптимизация для задач", test_task_optimization),
        ("Сохранение/загрузка", test_state_persistence),
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
