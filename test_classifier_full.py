"""Тест классификатора на разных типах запросов"""
import asyncio
from ai_integration.intent_classifier_ultra_minimal import IntentClassifierUltraMinimal

async def test_classifier():
    test_cases = [
        # Базовые операции
        ("Напомни купить хлеб завтра", "add_task"),
        ("Готово, купил", "complete_task"),
        ("Покажи мои задачи", "list_tasks"),
        ("Расскажи про задачу звонок", "get_task_details"),
        
        # Удаление
        ("Удали задачу про встречу", "delete_task"),
        ("Удали все задачи", "delete_all_tasks"),
        
        # Редактирование
        ("Перенеси на завтра", "reschedule_task"),
        ("Измени задачу: добавь описание", "edit_task"),
        
        # Делегирование
        ("Поручи задачу @ivanov", "delegate_task"),
        ("Соглашусь выполнить", "accept_delegated_task"),
        ("Откажусь от поручения", "reject_delegated_task"),
        ("Где моя делегированная задача", "get_delegation_progress"),
        
        # Социальные функции
        ("Найди единомышленников", "find_partners"),
        ("Кто может помочь с дизайном", "find_relevant_contacts_for_task"),
        
        # Профиль и память
        ("Я из Казани", "update_profile"),
        ("Запомни что я люблю кофе", "update_user_memory"),
        
        # Общение
        ("Привет", "conversation"),
        ("Спасибо", "conversation"),
    ]
    
    print("🔍 ТЕСТИРОВАНИЕ КЛАССИФИКАТОРА\n")
    passed = 0
    failed = 0
    
    for message, expected in test_cases:
        result = await IntentClassifierUltraMinimal.classify_intent(message, user_id=999)
        status = "✅" if result == expected else "❌"
        
        if result == expected:
            passed += 1
        else:
            failed += 1
            
        print(f"{status} '{message}' → {result} (ожидалось: {expected})")
    
    print(f"\n{'='*70}")
    print(f"📊 ИТОГО: {passed}/{len(test_cases)} ({passed*100//len(test_cases)}%)")
    print(f"{'='*70}")
    
    return passed == len(test_cases)

if __name__ == "__main__":
    success = asyncio.run(test_classifier())
    exit(0 if success else 1)
