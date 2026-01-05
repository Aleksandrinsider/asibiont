"""
Тестирование AI-агента локально
"""
import asyncio
import sys
import os

# Установить переменные окружения перед импортом
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'

# Импортировать только нужные функции
from ai_integration import chat_with_ai, add_task, list_tasks, complete_task

async def test_chat():
    """Тестирование основных сценариев работы агента"""
    
    # Тестовый user_id (можно взять из локальной БД)
    test_user_id = 1234567890
    
    test_cases = [
        # Тест 1: Приветствие
        {
            "message": "Привет",
            "expected": "естественное приветствие",
            "context": None
        },
        # Тест 2: Добавление задачи без времени (должен спросить)
        {
            "message": "Добавь задачу позвонить клиенту",
            "expected": "вопрос о времени",
            "context": None
        },
        # Тест 3: Добавление задачи с относительным временем
        {
            "message": "Напомни через 30 минут купить молоко",
            "expected": "подтверждение с вызовом add_task",
            "context": None
        },
        # Тест 4: Список задач
        {
            "message": "Покажи мои задачи",
            "expected": "вызов list_tasks",
            "context": None
        },
        # Тест 5: Завершение задачи
        {
            "message": "Завершил задачу купить молоко",
            "expected": "вызов complete_task",
            "context": None
        }
    ]
    
    print("\n" + "="*60)
    print("ТЕСТИРОВАНИЕ AI-АГЕНТА")
    print("="*60 + "\n")
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n{'-'*60}")
        print(f"ТЕСТ #{i}: {test['message']}")
        print(f"{'-'*60}")
        
        try:
            response = await chat_with_ai(
                message=test['message'],
                context=test.get('context'),
                user_id=test_user_id
            )
            
            print(f"\n[OK] ОТВЕТ АГЕНТА:\n{response}\n")
            print(f"ОЖИДАЕМОЕ ПОВЕДЕНИЕ: {test['expected']}")
            
        except Exception as e:
            print(f"\n[ERROR] ОШИБКА: {str(e)}\n")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*60}")
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test_chat())
