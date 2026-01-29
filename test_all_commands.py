"""Полный тест всех команд агента с проверкой через AI"""

import asyncio
from ai_integration.intent_classifier_ultra_minimal import IntentClassifierUltraMinimal

# Тестовые кейсы для ВСЕХ функций
test_cases = [
    # 1. add_task
    {"name": "1. add_task", "message": "напомни купить молоко завтра в 10:00", "expected": "add_task"},
    
    # 2. complete_task
    {"name": "2. complete_task", "message": "готово купить молоко", "expected": "complete_task"},
    
    # 3. delete_task
    {"name": "3. delete_task", "message": "удали задачу про отчет", "expected": "delete_task"},
    
    # 4. delete_all_tasks
    {"name": "4. delete_all_tasks", "message": "удали все задачи", "expected": "delete_all_tasks"},
    
    # 5. list_tasks
    {"name": "5. list_tasks", "message": "покажи мои задачи", "expected": "list_tasks"},
    
    # 6. reschedule_task
    {"name": "6. reschedule_task", "message": "перенеси встречу на завтра", "expected": "reschedule_task"},
    
    # 7. delegate_task
    {"name": "7. delegate_task", "message": "делегируй задачу проверить код пользователю @ivan", "expected": "delegate_task"},
    
    # 8. set_recurring_task
    {"name": "8. set_recurring_task", "message": "напоминай мне каждый день в 9:00 делать зарядку", "expected": "set_recurring_task"},
    
    # 9. find_partners
    {"name": "9. find_partners", "message": "найди партнеров по программированию", "expected": "find_partners"},
    
    # 10. update_profile (город)
    {"name": "10. update_profile (город)", "message": "я из москвы", "expected": "update_profile"},
    
    # 11. update_profile (навыки)
    {"name": "11. update_profile (навыки)", "message": "работаю программистом", "expected": "update_profile"},
    
    # 12. get_task_details
    {"name": "12. get_task_details", "message": "расскажи подробнее о задаче презентация", "expected": "get_task_details"},
    
    # 13. update_user_memory
    {"name": "13. update_user_memory", "message": "запомни что я предпочитаю работать по утрам", "expected": "update_user_memory"},
    
    # 14. Разговор (не команда)
    {"name": "14. conversation", "message": "как продвигать telegram бот?", "expected": "conversation"},
]


async def test_all_intents():
    """Проверка классификации всех интентов через AI"""
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║                    ПОЛНЫЙ ТЕСТ ВСЕХ КОМАНД АГЕНТА                         ║
║                        14 функций через AI классификацию                   ║
╚═══════════════════════════════════════════════════════════════════════════╝
""")
    
    passed = 0
    failed = 0
    results = []
    
    for case in test_cases:
        try:
            # Вызываем AI классификатор
            intent = await IntentClassifierUltraMinimal.classify_intent(case['message'], user_id=123456)
            
            expected = case['expected']
            actual = intent
            
            # Для conversation проверяем что это НЕ команда
            if expected == 'conversation':
                is_correct = actual == 'conversation' or actual not in [
                    'add_task', 'complete_task', 'delete_task', 'delete_all_tasks',
                    'list_tasks', 'reschedule_task', 'delegate_task', 'set_recurring_task',
                    'find_partners', 'update_profile', 'get_task_details', 'update_user_memory'
                ]
            else:
                is_correct = actual == expected
            
            status = "✓" if is_correct else "✗"
            if is_correct:
                passed += 1
            else:
                failed += 1
            
            result = {
                'name': case['name'],
                'message': case['message'],
                'expected': expected,
                'actual': actual,
                'status': status,
                'correct': is_correct
            }
            results.append(result)
            
            # Выводим результат
            print(f"{status} {case['name']:<35} | Ожидается: {expected:<20} | Получено: {actual:<20}")
            
        except Exception as e:
            print(f"✗ {case['name']:<35} | ОШИБКА: {e}")
            failed += 1
            results.append({
                'name': case['name'],
                'message': case['message'],
                'expected': case['expected'],
                'actual': f"ERROR: {e}",
                'status': '✗',
                'correct': False
            })
    
    # Итоговая статистика
    print("\n" + "="*80)
    print(f"РЕЗУЛЬТАТ: {passed}/{len(test_cases)} пройдено ({passed/len(test_cases)*100:.1f}%)")
    print("="*80)
    
    # Детальный вывод провалов
    if failed > 0:
        print("\n❌ ПРОВАЛИВШИЕСЯ ТЕСТЫ:")
        for r in results:
            if not r['correct']:
                print(f"\n  {r['name']}")
                print(f"  Сообщение: '{r['message']}'")
                print(f"  Ожидалось: {r['expected']}")
                print(f"  Получено: {r['actual']}")
    
    if failed == 0:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    else:
        print(f"\n⚠️  Требуется доработка ({failed} тестов провалено)")
    
    return passed, failed


async def main():
    await test_all_intents()


if __name__ == "__main__":
    asyncio.run(main())
