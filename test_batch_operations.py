"""
Тест Batch Operations Agent
Демонстрирует работу массовых операций с задачами
"""

import asyncio
import os
os.environ["LOCAL"] = "1"

from models import Session, User, Task
from datetime import datetime, timedelta
import pytz
from ai_integration.batch_operations import BatchOperationsAgent, is_batch_operation


async def test_batch_operations():
    """Тестирует batch operations агента"""
    
    print("\n🎯 ТЕСТ BATCH OPERATIONS AGENT")
    print("=" * 70)
    
    # Подготовка: создаём тестовые данные
    user_id = 999999998
    session = Session()
    
    # Создаём/находим пользователя
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username="batch_test", timezone="Europe/Moscow")
        session.add(user)
        session.commit()
    
    # Очищаем старые задачи
    old_tasks = session.query(Task).filter_by(user_id=user.id).all()
    for t in old_tasks:
        session.delete(t)
    session.commit()
    
    # Создаём тестовые задачи
    test_tasks = [
        {"title": "Купить молоко", "status": "completed", "days_ago": 5},
        {"title": "Позвонить Петрову", "status": "completed", "days_ago": 10},
        {"title": "Написать отчёт", "status": "completed", "days_ago": 35},
        {"title": "Купить хлеб", "status": "pending", "days_ago": 2},
        {"title": "Встреча с командой", "status": "pending", "days_ago": 1},
        {"title": "Работа над проектом X", "status": "pending", "days_ago": 0},
        {"title": "Работа над проектом Y", "status": "active", "days_ago": 0},
    ]
    
    for task_data in test_tasks:
        created_at = datetime.now(pytz.UTC) - timedelta(days=task_data["days_ago"])
        task = Task(
            title=task_data["title"],
            user_id=user.id,
            status=task_data["status"],
            created_at=created_at,
            reminder_time=datetime.now(pytz.UTC) + timedelta(hours=24)
        )
        session.add(task)
    session.commit()
    
    print(f"✅ Создано {len(test_tasks)} тестовых задач")
    print()
    
    # ТЕСТ 1: Проверка распознавания batch команд
    print("─" * 70)
    print("ТЕСТ 1: Распознавание batch команд")
    print("─" * 70)
    
    test_messages = [
        ("Удали все завершённые задачи", True),
        ("Перенеси все задачи с 'работа' на завтра", True),
        ("Покажи мои задачи", False),
        ("Отметь все задачи старше 30 дней выполненными", True),
        ("Создай задачу позвонить маме", False),
    ]
    
    for msg, expected in test_messages:
        result = is_batch_operation(msg)
        status = "✅" if result == expected else "❌"
        print(f"{status} '{msg}' → {result} (ожидалось {expected})")
    
    print()
    
    # ТЕСТ 2: Планирование batch операции
    print("─" * 70)
    print("ТЕСТ 2: Планирование операции 'удали все завершённые'")
    print("─" * 70)
    
    agent = BatchOperationsAgent()
    plan = await agent.plan_batch_operation(
        "Удали все завершённые задачи", 
        user_id
    )
    
    print(f"Intent: {plan['intent']}")
    print(f"Actions: {len(plan.get('actions', []))}")
    if plan.get('actions'):
        action = plan['actions'][0]
        print(f"Operation: {action['params']['operation']}")
        print(f"Filters: {action['params']['filters']}")
        print(f"Reason: {action['reason']}")
    
    print()
    
    # ТЕСТ 3: Выполнение batch операции (с запросом подтверждения)
    print("─" * 70)
    print("ТЕСТ 3: Выполнение 'удали все завершённые' (без подтверждения)")
    print("─" * 70)
    
    result = await agent.execute_batch(
        operation="delete_all",
        filters={"status": "completed"},
        additional_params={},
        user_id=user_id,
        confirmed=False  # Первый вызов без подтверждения
    )
    
    if result.get('requires_confirmation'):
        print(f"⚠️ Требуется подтверждение")
        print(f"Найдено задач: {result['task_count']}")
        print(f"Примеры:")
        for i, task in enumerate(result['preview_tasks'], 1):
            print(f"  {i}. {task['title']} ({task['status']})")
    else:
        print(f"✅ Операция выполнена без подтверждения")
    
    print()
    
    # ТЕСТ 4: Выполнение с подтверждением
    print("─" * 70)
    print("ТЕСТ 4: Выполнение 'удали все завершённые' (с подтверждением)")
    print("─" * 70)
    
    result = await agent.execute_batch(
        operation="delete_all",
        filters={"status": "completed"},
        additional_params={},
        user_id=user_id,
        confirmed=True  # С подтверждением
    )
    
    if result.get('success'):
        print(f"✅ Успешно выполнено")
        print(f"📊 Обработано: {result['processed']}")
        print(f"✅ Успешно: {result['successful']}")
        print(f"❌ Ошибки: {result['failed']}")
    else:
        print(f"❌ Ошибка: {result.get('error')}")
    
    print()
    
    # ТЕСТ 5: Проверяем результат в БД
    print("─" * 70)
    print("ТЕСТ 5: Проверка результата в БД")
    print("─" * 70)
    
    session = Session()
    remaining_tasks = session.query(Task).filter_by(user_id=user.id).all()
    completed_tasks = [t for t in remaining_tasks if t.status == "completed"]
    session.close()
    
    print(f"Осталось задач: {len(remaining_tasks)}")
    print(f"Из них завершённых: {len(completed_tasks)}")
    
    if len(completed_tasks) == 0:
        print("✅ Все завершённые задачи удалены!")
    else:
        print(f"⚠️ Остались завершённые задачи: {[t.title for t in completed_tasks]}")
    
    print()
    
    # ТЕСТ 6: Фильтр по ключевым словам
    print("─" * 70)
    print("ТЕСТ 6: Удаление задач с ключевым словом 'работа'")
    print("─" * 70)
    
    result = await agent.execute_batch(
        operation="delete_all",
        filters={"keywords": ["работа"]},
        additional_params={},
        user_id=user_id,
        confirmed=True
    )
    
    if result.get('success'):
        print(f"✅ Успешно выполнено")
        print(f"📊 Обработано: {result['processed']}")
        print(f"✅ Успешно: {result['successful']}")
        
        # Проверяем что удалилось
        session = Session()
        work_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike('%работа%')
        ).all()
        session.close()
        
        print(f"Осталось задач с 'работа': {len(work_tasks)}")
        if len(work_tasks) == 0:
            print("✅ Все задачи с 'работа' удалены!")
        
    print()
    
    # ТЕСТ 7: Форматирование результата через AI
    print("─" * 70)
    print("ТЕСТ 7: Форматирование результата через AI")
    print("─" * 70)
    
    mock_result = {
        "success": True,
        "operation": "delete_all",
        "processed": 3,
        "successful": 3,
        "failed": 0
    }
    
    formatted = await agent.format_batch_result(
        mock_result,
        "Удали все завершённые задачи",
        user_id
    )
    
    print("Форматированный ответ:")
    print(formatted)
    
    print()
    print("=" * 70)
    print("🎉 ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_batch_operations())
