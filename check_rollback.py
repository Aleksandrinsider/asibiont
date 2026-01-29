"""
Скрипт для проверки и исправления всех проблем с rollback в handlers.py
"""
import re

# Читаем файл
with open('ai_integration/handlers.py', 'r', encoding='utf-8') as f:
    content = f.read()
    lines = content.split('\n')

# Ищем все except Exception блоки
issues = []
for i, line in enumerate(lines, 1):
    if 'except Exception as e:' in line:
        # Проверяем следующие 20 строк на наличие rollback и close_session
        next_lines = lines[i:min(i+20, len(lines))]
        next_text = '\n'.join(next_lines)
        
        has_rollback = 'session.rollback()' in next_text
        has_close_check = 'if close_session:' in next_text
        has_traceback = 'traceback.print_exc()' in next_text
        has_commit = 'session.commit()' in next_text  # если есть commit ПОСЛЕ except - это проблема
        
        # Определяем функцию
        func_name = "Unknown"
        for j in range(max(0, i-50), i):
            if lines[j].startswith('def ') or lines[j].startswith('async def '):
                func_name = lines[j].split('(')[0].replace('def ', '').replace('async ', '').strip()
        
        # Проверяем является ли это функцией с транзакцией
        is_transaction_func = False
        for j in range(max(0, i-100), i):
            if 'session = Session()' in lines[j] or 'if session is None:' in lines[j]:
                is_transaction_func = True
                break
        
        if is_transaction_func and not has_rollback:
            issues.append({
                'line': i,
                'func': func_name,
                'has_rollback': has_rollback,
                'has_close_check': has_close_check,
                'has_traceback': has_traceback,
                'severity': 'HIGH' if has_commit else 'MEDIUM'
            })

print("="*80)
print("ПРОВЕРКА ОБРАБОТКИ ОШИБОК В handlers.py")
print("="*80)
print(f"\nНайдено except Exception блоков: {len([l for l in lines if 'except Exception as e:' in l])}")
print(f"Проблем с rollback: {len(issues)}\n")

if issues:
    print("ПРОБЛЕМЫ:\n")
    for issue in issues:
        severity = "CRITICAL" if issue['severity'] == 'HIGH' else "WARNING"
        print(f"{severity} Строка {issue['line']}: {issue['func']}")
        if not issue['has_rollback']:
            print(f"   - Отсутствует session.rollback()")
        if not issue['has_traceback']:
            print(f"   ! Отсутствует traceback.print_exc()")
        print()
else:
    print("OK: Все except блоки обрабатывают ошибки корректно")

# Список функций которые нужно исправить
critical_functions = [
    'add_task',
    'set_recurring_task', 
    'delete_all_tasks',
    'complete_task',
    'delete_task_sync',
    'edit_task',
    'reschedule_task',
    'delegate_task',
    'update_profile',
    'list_tasks'
]

print("="*80)
print("КРИТИЧНЫЕ ФУНКЦИИ ДЛЯ ПРОВЕРКИ:")
print("="*80)
for func in critical_functions:
    func_issues = [i for i in issues if func in i['func']]
    if func_issues:
        print(f"[!] {func}: {len(func_issues)} проблем")
    else:
        print(f"[OK] {func}: OK")
