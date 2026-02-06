"""
Тест для проверки конкретности создаваемых задач
Проверяет что агент создает конкретные задачи с конкретным временем
"""
import asyncio
import os
from datetime import datetime
from ai_integration.autonomous_agent import HybridAutonomousAgent

# Set test environment
os.environ['LOCAL'] = '1'

async def test_task_specificity():
    """Тест конкретности задач"""
    print("\n" + "="*60)
    print("ТЕСТ КОНКРЕТНОСТИ ЗАДАЧ")
    print("="*60)
    
    agent = HybridAutonomousAgent()
    test_user_id = 123456789
    
    tests = [
        {
            "name": "Напоминание с контекстом про реферальную программу",
            "context": "Диалог про реферальную программу для партнеров",
            "message": "напомни через 5 минут заняться этим вопросом",
            "expected_title_keywords": ["реферальн", "партнер", "программ"],
            "expected_has_description": True,
            "expected_has_time": True,
        },
        {
            "name": "Напоминание с прямым указанием задачи",
            "context": "",
            "message": "напомни через 10 минут про маркетинг",
            "expected_title_keywords": ["маркетинг"],
            "expected_has_description": False,  # может быть или нет
            "expected_has_time": True,
        },
        {
            "name": "Напоминание 'про это' после обсуждения ML",
            "context": "Обсуждали курс по машинному обучению",
            "message": "напомни про это через час",
            "expected_title_keywords": ["машин", "обучени", "курс", "ML"],
            "expected_has_description": True,
            "expected_has_time": True,
        },
        {
            "name": "Конкретное напоминание на завтра",
            "context": "",
            "message": "создай задачу позвонить клиенту завтра в 10:00",
            "expected_title_keywords": ["позвон", "клиент"],
            "expected_has_description": False,
            "expected_has_time": True,
        },
    ]
    
    results = []
    
    for i, test in enumerate(tests, 1):
        print(f"\n{i}. {test['name']}")
        print(f"   Контекст: {test['context']}")
        print(f"   Сообщение: '{test['message']}'")
        
        try:
            # Планирование
            plan = await agent.plan_strategy(
                user_message=test['message'],
                context=test['context']
            )
            
            # Проверки
            checks = []
            
            # Проверка что это tool_execution
            if plan['intent'] != 'tool_execution':
                checks.append(f"❌ intent должен быть tool_execution, получен {plan['intent']}")
            else:
                checks.append(f"✅ intent = tool_execution")
            
            # Проверка что есть действия
            actions = plan.get('actions', [])
            if not actions:
                checks.append(f"❌ Нет действий в плане")
            else:
                checks.append(f"✅ Есть {len(actions)} действие(й)")
                
                # Проверяем первое действие (должно быть add_task)
                action = actions[0]
                if action['tool'] != 'add_task':
                    checks.append(f"❌ Инструмент должен быть add_task, получен {action['tool']}")
                else:
                    checks.append(f"✅ Инструмент = add_task")
                    
                    params = action.get('params', {})
                    title = params.get('title', '')
                    description = params.get('description', '')
                    reminder_time = params.get('reminder_time', '')
                    
                    print(f"   Создаваемая задача:")
                    print(f"     - title: '{title}'")
                    print(f"     - description: '{description}'")
                    print(f"     - reminder_time: '{reminder_time}'")
                    
                    # Проверка конкретности названия
                    generic_titles = ["заняться вопросом", "сделать это", "та задача", "вопрос"]
                    is_generic = any(generic.lower() in title.lower() for generic in generic_titles)
                    
                    if is_generic:
                        checks.append(f"❌ НЕКОНКРЕТНОЕ название: '{title}'")
                    else:
                        # Проверка что название содержит ожидаемые ключевые слова
                        has_keywords = any(
                            keyword.lower() in title.lower() 
                            for keyword in test['expected_title_keywords']
                        )
                        if has_keywords or len(title) > 15:  # или достаточно длинное
                            checks.append(f"✅ КОНКРЕТНОЕ название")
                        else:
                            checks.append(f"⚠️ Название короткое, но не расплывчатое: '{title}'")
                    
                    # Проверка описания
                    if test['expected_has_description']:
                        if description and len(description) > 10:
                            checks.append(f"✅ Есть информативное описание ({len(description)} символов)")
                        else:
                            checks.append(f"⚠️ Описание короткое или отсутствует")
                    
                    # Проверка времени
                    if test['expected_has_time']:
                        if reminder_time:
                            checks.append(f"✅ Указано время: '{reminder_time}'")
                        else:
                            checks.append(f"❌ Время не указано")
                    
            # Подсчет успеха
            failed_checks = [c for c in checks if c.startswith("❌")]
            warning_checks = [c for c in checks if c.startswith("⚠️")]
            
            if not failed_checks:
                status = "✅ УСПЕХ"
                results.append(True)
            elif failed_checks:
                status = f"❌ ПРОВАЛ ({len(failed_checks)} ошибок)"
                results.append(False)
            else:
                status = f"⚠️ ЧАСТИЧНО ({len(warning_checks)} предупреждений)"
                results.append(True)  # считаем успехом
            
            print(f"\n   {status}")
            for check in checks:
                print(f"   {check}")
                
        except Exception as e:
            print(f"   ❌ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    # Итоговый результат
    print("\n" + "="*60)
    success_count = sum(results)
    total_count = len(results)
    percentage = (success_count / total_count * 100) if total_count > 0 else 0
    
    if success_count == total_count:
        print(f"✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ: {success_count}/{total_count} (100%)")
    else:
        print(f"⚠️ РЕЗУЛЬТАТ: {success_count}/{total_count} ({percentage:.1f}%)")
    
    print("="*60)
    
    return success_count == total_count

if __name__ == "__main__":
    success = asyncio.run(test_task_specificity())
    exit(0 if success else 1)
