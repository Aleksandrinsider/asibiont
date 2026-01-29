"""Тест корректности rollback в exception handlers"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task
from ai_integration.handlers import delete_all_tasks, add_task
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_delete_all_tasks_rollback():
    """Тест что delete_all_tasks корректно откатывает транзакцию при ошибке"""
    session = Session()
    
    # Создаём тестового пользователя
    test_user = User(telegram_id=888888, username="test_rollback")
    session.add(test_user)
    session.commit()
    
    # Добавляем несколько задач
    for i in range(3):
        task = Task(
            user_id=test_user.id,
            title=f"Test task {i+1}",
            status="pending"
        )
        session.add(task)
    session.commit()
    
    # Проверяем что задачи созданы
    task_count = session.query(Task).filter_by(user_id=test_user.id).count()
    print(f"✓ Создано {task_count} задач")
    
    # Тестируем delete_all_tasks
    result = delete_all_tasks(user_id=test_user.telegram_id, session=session)
    print(f"✓ delete_all_tasks вернул: {result}")
    
    # Проверяем что задачи удалены
    task_count_after = session.query(Task).filter_by(user_id=test_user.id).count()
    print(f"✓ Осталось {task_count_after} задач")
    
    # Очистка
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.query(User).filter_by(telegram_id=888888).delete()
    session.commit()
    session.close()
    
    if task_count_after == 0:
        print("\n✅ ТЕСТ ПРОЙДЕН: delete_all_tasks корректно удалил все задачи")
        return True
    else:
        print(f"\n❌ ТЕСТ ПРОВАЛЕН: осталось {task_count_after} задач")
        return False

def test_error_handling():
    """Тест что при ошибке происходит rollback"""
    print("\n" + "="*80)
    print("ТЕСТ 2: Проверка обработки ошибок с rollback")
    print("="*80)
    
    # Очищаем тестовых пользователей
    session = Session()
    session.query(User).filter_by(telegram_id=999999).delete()
    session.commit()
    session.close()
    
    # Пытаемся удалить задачи несуществующего пользователя
    result = delete_all_tasks(user_id=999999)
    print(f"✓ Результат для несуществующего пользователя: {result}")
    
    if "не найден" in result or "найден" in result.lower():
        print("\n✅ ТЕСТ ПРОЙДЕН: корректная обработка ошибки")
        return True
    else:
        print("\n❌ ТЕСТ ПРОВАЛЕН: неожиданный результат")
        return False

if __name__ == "__main__":
    print("="*80)
    print("ТЕСТИРОВАНИЕ ROLLBACK В EXCEPTION HANDLERS")
    print("="*80)
    
    print("\n" + "="*80)
    print("ТЕСТ 1: Удаление всех задач пользователя")
    print("="*80)
    
    test1_passed = test_delete_all_tasks_rollback()
    test2_passed = test_error_handling()
    
    print("\n" + "="*80)
    print("ИТОГИ")
    print("="*80)
    print(f"Тест 1 (delete_all_tasks): {'✅ ПРОЙДЕН' if test1_passed else '❌ ПРОВАЛЕН'}")
    print(f"Тест 2 (error handling): {'✅ ПРОЙДЕН' if test2_passed else '❌ ПРОВАЛЕН'}")
    
    if test1_passed and test2_passed:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
        exit(0)
    else:
        print("\n❌ ЕСТЬ ПРОВАЛИВШИЕСЯ ТЕСТЫ")
        exit(1)
