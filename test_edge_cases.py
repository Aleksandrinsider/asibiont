"""
Тест граничных случаев интерпретации
"""
import os
os.environ['LOCAL'] = '1'

from ai_integration.utils import parse_multiple_tasks

# Edge cases to test
EDGE_CASES = [
    # 1. Смешанные разделители
    {
        "message": "создай задачу позвонить маме в 10:00, купить продукты и сходить в банк завтра",
        "expected": 3,
        "description": "Запятая + и + время"
    },
    
    # 2. Только союз "и" без других разделителей
    {
        "message": "напомни позвонить маме и купить продукты",
        "expected": 2,
        "description": "Простой союз и"
    },
    
    # 3. Задачи с предлогами, похожими на команды
    {
        "message": "создай задачу сделай отчет",
        "expected": 1,
        "title_contains": "сделай отчет",
        "description": "Команда внутри задачи"
    },
    
    # 4. Пустые части после разбиения
    {
        "message": "добавь позвонить маме, , и купить продукты",
        "expected": 2,
        "description": "Пустые части между разделителями"
    },
    
    # 5. Длинные списки
    {
        "message": "создай задачу A, B, C, D, E",
        "expected": 5,
        "description": "Много задач через запятую"
    },
    
    # 6. Задачи с временем
    {
        "message": "добавь позвонить маме в 10:00 и купить продукты в 14:00",
        "expected": 2,
        "description": "Каждая задача со временем"
    },
    
    # 7. "и" внутри названия задачи
    {
        "message": "создай задачу купить хлеб и молоко",
        "expected": 2,  # Will split into 2 tasks
        "description": "и как часть описания (будет разбито)"
    },
    
    # 8. Очень короткие задачи
    {
        "message": "создай А и Б",
        "expected": 2,
        "description": "Очень короткие названия"
    },
    
    # 9. Задачи с числами
    {
        "message": "добавь задачу 1, задачу 2, задачу 3",
        "expected": 3,
        "description": "Задачи с номерами"
    },
    
    # 10. Только команда без задачи
    {
        "message": "создай задачу",
        "expected": 0,  # Should not create empty task
        "description": "Пустая команда"
    },
]


def test_edge_cases():
    print("=" * 60)
    print("EDGE CASES TEST")
    print("=" * 60)
    
    failures = []
    
    for i, case in enumerate(EDGE_CASES, 1):
        message = case["message"]
        expected = case.get("expected")
        description = case["description"]
        
        print(f"\n{i}. {description}")
        print(f"   Message: '{message}'")
        
        tasks = parse_multiple_tasks(message)
        
        # Check count
        if expected is not None:
            if len(tasks) != expected:
                failures.append(f"Case {i}: expected {expected} tasks, got {len(tasks)}")
                print(f"   ❌ Expected {expected} tasks, got {len(tasks)}")
            else:
                print(f"   ✅ Got {len(tasks)} tasks")
        
        # Print tasks
        for j, task in enumerate(tasks, 1):
            title = task['title']
            time = task.get('reminder_time')
            time_str = f" @ {time}" if time else ""
            print(f"      {j}. '{title}'{time_str}")
            
            # Check title content if specified
            if 'title_contains' in case:
                if case['title_contains'] not in title:
                    failures.append(f"Case {i}: title should contain '{case['title_contains']}'")
                    print(f"         ❌ Should contain '{case['title_contains']}'")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if failures:
        print(f"\n❌ {len(failures)} failures:\n")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("\n✅ All edge cases passed!")
    
    return len(failures)


if __name__ == "__main__":
    exit_code = test_edge_cases()
    exit(exit_code)
