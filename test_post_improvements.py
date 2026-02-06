"""
Тест после улучшений промптов - проверка конкретности задач
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import asyncio
import os
from ai_integration.autonomous_agent import HybridAutonomousAgent

os.environ['LOCAL'] = '1'

async def test_improved_prompts():
    """Тест улучшенных промптов"""
    print("="*60)
    print("ТЕСТ ПОСЛЕ УЛУЧШЕНИЙ ПРОМПТОВ")
    print("="*60)
    
    agent = HybridAutonomousAgent()
    test_user_id = 123456789
    
    tests = [
        {
            "name": "Контекст: реферальная программа + 'заняться этим вопросом'",
            "message": "напомни через 5 минут заняться этим вопросом",
            "context": "Обсуждали реферальную программу для партнеров",
            "should_not_contain": ["заняться вопросом", "вопрос", "это"],
            "should_contain": ["реферальн", "партнер", "программ"]
        },
        {
            "name": "Короткое название без контекста: 'зарядка'",
            "message": "создай задачу зарядка каждый день в 7:00",
            "context": "",
            "should_not_contain": [],
            "min_title_length": 15,
            "should_have_description": True
        },
        {
            "name": "Короткое название: 'созвон'",
            "message": "напомни про созвон в 14:00",
            "context": "",
            "should_not_contain": [],
            "min_title_length": 15,
            "should_have_description": True
        },
        {
            "name": "Контекст: новая фича + 'напомни про это'",
            "message": "напомни завтра утром про это",
            "context": "Работаем над новой фичей в продукте",
            "should_not_contain": ["это", "вопрос"],
            "should_contain": ["фич", "продукт"]
        },
    ]
    
    results = []
    issues = []
    
    for i, test in enumerate(tests, 1):
        print(f"\n{i}. {test['name']}")
        print(f"   Сообщение: '{test['message']}'")
        if test['context']:
            print(f"   Контекст: '{test['context']}'")
        
        try:
            # Планирование
            plan = await agent.plan_strategy(
                user_message=test['message'],
                user_id=test_user_id,
                context=test['context']
            )
            
            if plan['intent'] != 'tool_execution':
                print(f"   ❌ ОШИБКА: intent = {plan['intent']}, ожидался tool_execution")
                issues.append(f"Тест {i}: неправильный intent")
                results.append(False)
                continue
            
            actions = plan.get('actions', [])
            if not actions or actions[0]['tool'] != 'add_task':
                print(f"   ❌ ОШИБКА: нет add_task действия")
                issues.append(f"Тест {i}: нет add_task")
                results.append(False)
                continue
            
            params = actions[0]['params']
            title = params.get('title', '')
            description = params.get('description', '')
            
            print(f"\n   Созданная задача:")
            print(f"     Title: '{title}'")
            print(f"     Description: '{description}'")
            
            # Проверки
            failed = []
            
            # 1. Запрещенные слова
            if 'should_not_contain' in test:
                for word in test['should_not_contain']:
                    if word.lower() in title.lower():
                        failed.append(f"Содержит запрещенное '{word}'")
            
            # 2. Обязательные слова (из контекста)
            if 'should_contain' in test:
                found = False
                for word in test['should_contain']:
                    if word.lower() in title.lower():
                        found = True
                        break
                if not found:
                    failed.append(f"Не извлечен контекст (нужно одно из: {test['should_contain']})")
            
            # 3. Минимальная длина
            if 'min_title_length' in test:
                if len(title) < test['min_title_length'] and (not description or len(description) < 10):
                    failed.append(f"Title короткий ({len(title)} < {test['min_title_length']}) и нет описания")
            
            # 4. Описание обязательно
            if test.get('should_have_description') and (not description or len(description) < 10):
                failed.append(f"Отсутствует информативное описание")
            
            if failed:
                print(f"   ❌ ПРОВАЛ:")
                for f in failed:
                    print(f"      - {f}")
                issues.extend([f"Тест {i}: {f}" for f in failed])
                results.append(False)
            else:
                print(f"   ✅ УСПЕХ")
                results.append(True)
                
        except Exception as e:
            print(f"   ❌ ИСКЛЮЧЕНИЕ: {e}")
            import traceback
            traceback.print_exc()
            issues.append(f"Тест {i}: исключение {e}")
            results.append(False)
    
    # Итог
    print("\n" + "="*60)
    success = sum(results)
    total = len(results)
    print(f"РЕЗУЛЬТАТ: {success}/{total} ({success/total*100:.1f}%)")
    
    if issues:
        print(f"\n⚠️ НАЙДЕНО ПРОБЛЕМ: {len(issues)}")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    
    print("="*60)
    
    return success == total

if __name__ == "__main__":
    success = asyncio.run(test_improved_prompts())
    exit(0 if success else 1)
