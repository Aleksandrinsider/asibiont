import asyncio
import sys
import os
import traceback
from datetime import datetime, timedelta

# Установить кодировку UTF-8 для вывода
sys.stdout.reconfigure(encoding='utf-8')

# Установить FREE_ACCESS_MODE для тестирования
os.environ['FREE_ACCESS_MODE'] = '1'

sys.path.append(os.path.dirname(__file__))

from ai_integration import chat_with_ai
from models import Session, User, Task

async def comprehensive_test():
    """Комплексный тест всех функций агента"""
    
    test_user_id = 123456789
    
    # Очистка тестовых данных
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=test_user_id).first()
        if user:
            session.query(Task).filter_by(user_id=user.id).delete()
            session.commit()
            print("[OK]OK] Очищены старые тестовые данные\n")
    finally:
        session.close()
    
    tests = [
        # 1. Добавление задач с разными форматами времени
        {
            "name": "Добавление задачи с относительным временем (через 5 минут)",
            "message": "Напомни через 5 минут заказать продукты",
            "expected": ["напомн", ":"]
        },
        {
            "name": "Добавление задачи с относительным временем (через 2 часа)",
            "message": "Добавь задачу позвонить клиенту через 2 часа",
            "expected": ["позвонить клиенту"]
        },
        {
            "name": "Добавление задачи без времени",
            "message": "Добавь задачу купить молоко",
            "expected": ["когда", "напомн"]
        },
        
        # 2. Просмотр задач
        {
            "name": "Показать все задачи",
            "message": "Покажи мои задачи",
            "expected": ["задач"]
        },
        
        # 3. Редактирование задач
        {
            "name": "Изменить приоритет задачи",
            "message": "Поставь высокий приоритет для задачи заказать продукты",
            "expected": ["приоритет", "заказать продукты"]
        },
        
        # 4. Завершение задач
        {
            "name": "Завершить задачу",
            "message": "Заверши задачу купить молоко",
            "expected": ["молоко"]
        },
        
        # 5. Удаление задач
        {
            "name": "Удалить задачу",
            "message": "Удали задачу позвонить клиенту",
            "expected": ["удал", "задач"]
        },
        
        # 6. Проверка ответов на естественность
        {
            "name": "Общий вопрос без команды",
            "message": "Как мои дела с задачами?",
            "expected": ["задач"]
        },
        
        # 7. Поиск партнеров
        {
            "name": "Найти партнера",
            "message": "Помоги найти партнера по программированию",
            "expected": ["программирован"]
        },
        
        # 8. Профиль
        {
            "name": "Обновить профиль",
            "message": "Добавь в мои интересы машинное обучение",
            "expected": ["машинное обучение", "интерес"]
        },
    ]
    
    results = {
        "passed": 0,
        "failed": 0,
        "errors": []
    }
    
    print("=== ЗАПУСК КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ АГЕНТА ===\n")
    
    for i, test in enumerate(tests, 1):
        print(f"[Тест {i}/{len(tests)}] {test['name']}")
        print(f"Запрос: {test['message']}")
        
        try:
            response = await chat_with_ai(test['message'], user_id=test_user_id)
            print(f"Ответ: {response[:200]}...")
            
            # Диагностика: проверка БД после каждого теста
            session_check = Session()
            user_check = session_check.query(User).filter_by(telegram_id=test_user_id).first()
            if user_check:
                tasks_count = session_check.query(Task).filter_by(user_id=user_check.id).count()
                print(f"[DEBUG] Задач в БД после теста: {tasks_count}")
            session_check.close()
            
            # Проверка ожидаемых ключевых слов
            passed = all(keyword.lower() in response.lower() for keyword in test['expected'])
            
            if passed:
                print("[OK]OK] УСПЕШНО\n")
                results["passed"] += 1
            else:
                print(f"[FAIL]FAIL] ПРОВАЛЕНО: Не найдены ключевые слова {test['expected']}\n")
                results["failed"] += 1
                results["errors"].append({
                    "test": test['name'],
                    "message": test['message'],
                    "response": response,
                    "expected": test['expected']
                })
        except Exception as e:
            print(f"[ERROR]ERROR] ОШИБКА: {e}")
            traceback.print_exc()
            print()
            results["failed"] += 1
            results["errors"].append({
                "test": test['name'],
                "message": test['message'],
                "error": str(e)
            })
        
        # Пауза между тестами
        await asyncio.sleep(2)
    
    # Итоговый отчет
    print("\n=== РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ ===")
    print(f"Пройдено: {results['passed']}/{len(tests)}")
    print(f"Провалено: {results['failed']}/{len(tests)}")
    print(f"Процент успеха: {(results['passed']/len(tests)*100):.1f}%")
    
    if results['errors']:
        print("\n=== ДЕТАЛИ ОШИБОК ===")
        for error in results['errors']:
            print(f"\nТест: {error['test']}")
            print(f"Запрос: {error.get('message', 'N/A')}")
            if 'error' in error:
                print(f"Ошибка: {error['error']}")
            if 'expected' in error:
                print(f"Ожидалось: {error['expected']}")
                print(f"Получено: {error.get('response', 'N/A')[:200]}...")
    
    return results

if __name__ == "__main__":
    asyncio.run(comprehensive_test())