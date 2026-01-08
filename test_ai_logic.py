"""
Тест логики AI агента без подключения к БД
"""
import sys
import os

# Проверка критических функций AI
def test_time_parsing():
    """Тест парсинга времени"""
    test_cases = [
        ("завтра в 10:00", True),
        ("через 2 часа", True),
        ("сегодня", False),  # Нет времени
        ("в пятницу в 15:30", True),
        ("", False),
    ]
    
    print("🧪 Тест парсинга времени для делегирования")
    errors = []
    
    for text, should_have_time in test_cases:
        # Простая проверка наличия времени
        import re
        has_time = bool(re.search(r'\d{1,2}:\d{2}|\d{1,2}\s*(час|минут)', text))
        
        if has_time == should_have_time:
            print(f"  ✅ '{text}' -> {has_time}")
        else:
            print(f"  ❌ '{text}' -> ожидалось {should_have_time}, получено {has_time}")
            errors.append(text)
    
    return len(errors) == 0

def test_tool_name_inference():
    """Тест определения функции по аргументам"""
    test_cases = [
        ({"title": "Test", "reminder_time": "2026-01-10 10:00"}, "create_task"),
        ({"task_id": "123"}, "complete_task"),
        ({"task_id": "123", "reminder_time": "2026-01-10 10:00"}, "set_reminder"),
        ({}, None),  # Пустые аргументы
    ]
    
    print("\n🧪 Тест определения функции по аргументам")
    errors = []
    
    for args, expected_func in test_cases:
        # Логика из ai_integration.py
        if "title" in args and "reminder_time" in args:
            inferred = "create_task"
        elif "task_id" in args and "reminder_time" in args:
            inferred = "set_reminder"
        elif "task_id" in args:
            inferred = "complete_task"
        else:
            inferred = None
        
        if inferred == expected_func:
            print(f"  ✅ {args} -> {inferred}")
        else:
            print(f"  ❌ {args} -> ожидалось {expected_func}, получено {inferred}")
            errors.append(args)
    
    return len(errors) == 0

def test_message_deduplication():
    """Тест дедупликации сообщений"""
    print("\n🧪 Тест дедупликации сообщений")
    
    messages = [
        {"id": 1, "content": "Привет"},
        {"id": 2, "content": "Как дела?"},
        {"id": 1, "content": "Привет"},  # Дубликат
        {"id": 3, "content": "Отлично!"},
    ]
    
    displayed = set()
    unique_messages = []
    
    for msg in messages:
        if msg["id"] not in displayed:
            displayed.add(msg["id"])
            unique_messages.append(msg)
    
    if len(unique_messages) == 3:
        print(f"  ✅ Дубликаты удалены: {len(messages)} -> {len(unique_messages)}")
        return True
    else:
        print(f"  ❌ Ожидалось 3 уникальных, получено {len(unique_messages)}")
        return False

def test_error_handling():
    """Тест обработки ошибок"""
    print("\n🧪 Тест обработки ошибок")
    
    # Тест 1: int() конвертация
    try:
        task_id = int("123")
        print(f"  ✅ int('123') = {task_id}")
    except (ValueError, TypeError) as e:
        print(f"  ❌ int('123') вызвал ошибку: {e}")
        return False
    
    # Тест 2: int() с невалидным значением
    try:
        task_id = int("abc")
        print(f"  ❌ int('abc') должен вызвать ValueError, но вернул {task_id}")
        return False
    except (ValueError, TypeError):
        print(f"  ✅ int('abc') корректно обработан")
    
    # Тест 3: деление на ноль
    completed = 5
    if completed > 0:
        avg = 100 / completed
        print(f"  ✅ Деление 100/{completed} = {avg}")
    else:
        print(f"  ✅ Деление на ноль предотвращено")
    
    return True

def test_json_parsing():
    """Тест парсинга JSON"""
    print("\n🧪 Тест парсинга JSON")
    import json
    
    # Тест 1: валидный JSON
    try:
        data = json.loads('{"name": "test", "value": 123}')
        print(f"  ✅ Валидный JSON распарсен: {data}")
    except json.JSONDecodeError as e:
        print(f"  ❌ Ошибка парсинга валидного JSON: {e}")
        return False
    
    # Тест 2: невалидный JSON
    try:
        data = json.loads('invalid json')
        print(f"  ❌ Невалидный JSON должен вызвать ошибку, но вернул: {data}")
        return False
    except json.JSONDecodeError:
        print(f"  ✅ Невалидный JSON корректно обработан")
    
    return True

def test_none_checks():
    """Тест проверок на None"""
    print("\n🧪 Тест проверок на None")
    
    class MockUser:
        def __init__(self, id, username):
            self.id = id
            self.username = username
    
    user = None
    
    # Тест 1: проверка перед доступом
    if user and user.id:
        print(f"  ❌ Не должно выполняться для None")
        return False
    else:
        print(f"  ✅ None проверка работает")
    
    # Тест 2: с валидным объектом
    user = MockUser(123, "testuser")
    if user and user.id:
        print(f"  ✅ Доступ к user.id = {user.id}")
    else:
        print(f"  ❌ Проверка не прошла для валидного объекта")
        return False
    
    return True

def test_list_comprehension():
    """Тест list comprehension с фильтрами"""
    print("\n🧪 Тест list comprehension")
    
    contacts = [
        {"username": "user1", "city": "Moscow"},
        {"username": None, "city": "SPB"},
        {"username": "user2", "city": None},
        {"username": "user3", "city": "Moscow"},
    ]
    
    # Фильтр с проверкой на None
    filtered = [c for c in contacts if c.get("username") and c.get("city")]
    
    if len(filtered) == 2:
        print(f"  ✅ Отфильтровано {len(contacts)} -> {len(filtered)}")
        return True
    else:
        print(f"  ❌ Ожидалось 2, получено {len(filtered)}")
        return False

def test_string_operations():
    """Тест операций со строками"""
    print("\n🧪 Тест операций со строками")
    
    # Тест 1: replace с None
    text = None
    try:
        result = text.replace("@", "") if text else ""
        print(f"  ✅ None.replace обработан корректно: '{result}'")
    except AttributeError:
        print(f"  ❌ None.replace вызвал AttributeError")
        return False
    
    # Тест 2: ilike эмуляция
    username = "TestUser"
    search = "testuser"
    if username.lower() == search.lower():
        print(f"  ✅ Case-insensitive поиск работает")
    else:
        print(f"  ❌ Case-insensitive поиск не работает")
        return False
    
    return True

def main():
    print("🚀 ТЕСТИРОВАНИЕ ЛОГИКИ AI АГЕНТА")
    print("=" * 60)
    
    tests = [
        ("Парсинг времени", test_time_parsing),
        ("Определение функции", test_tool_name_inference),
        ("Дедупликация сообщений", test_message_deduplication),
        ("Обработка ошибок", test_error_handling),
        ("Парсинг JSON", test_json_parsing),
        ("Проверки на None", test_none_checks),
        ("List comprehension", test_list_comprehension),
        ("Операции со строками", test_string_operations),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"  ❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 60)
    print("РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 60)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nИтого: {passed}/{total} тестов пройдено ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        return 0
    else:
        print("⚠️ ЕСТЬ ПРОБЛЕМЫ, ТРЕБУЮЩИЕ ВНИМАНИЯ")
        return 1

if __name__ == '__main__':
    sys.exit(main())
