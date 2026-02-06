"""
Упрощенный тест работы с БД
Проверяет корректность создания/обновления задач без длительных AI вызовов
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from models import Session, User, Task, UserProfile
from datetime import datetime, timedelta
from sqlalchemy import text
import pytz

# Тестовый пользователь
TEST_USER_ID = 999888777

def setup_test_user(session):
    """Создание тестового пользователя"""
    # Очистка
    session.execute(text("DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM users WHERE telegram_id = :tid"), {"tid": TEST_USER_ID})
    session.commit()
    
    # Создание
    user = User(telegram_id=TEST_USER_ID, username="test_user", timezone="Europe/Moscow")
    session.add(user)
    session.commit()
    
    profile = UserProfile(
        user_id=user.id,
        city="Пермь",
        company="ASI Biont",
        position="Founder",
        goals="Продвигать агента на рынке"
    )
    session.add(profile)
    session.commit()
    
    print(f"[+] Создан пользователь ID={user.id}")
    return user

def test_task_operations(session, user):
    """Тест операций с задачами"""
    print("\n=== ТЕСТ ОПЕРАЦИЙ С ЗАДАЧАМИ ===\n")
    
    # 1. Создание задач
    print("1. Создание задач...")
    tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(tz)
    
    task1 = Task(
        user_id=user.id,
        title="Позвонить клиенту",
        description="Обсудить условия сотрудничества",
        reminder_time=now + timedelta(hours=2),
        status="pending"
    )
    task2 = Task(
        user_id=user.id,
        title="Купить молоко",
        reminder_time=now + timedelta(hours=1),
        status="pending"
    )
    task3 = Task(
        user_id=user.id,
        title="Работа над реферальной программой",
        description="Найти первых партнеров, обсудить условия",
        reminder_time=now + timedelta(minutes=30),
        status="pending"
    )
    
    session.add_all([task1, task2, task3])
    session.commit()
    
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"   Создано задач: {len(tasks)}")
    for t in tasks:
        print(f"   - {t.title} ({t.status})")
    
    # 2. Обновление задачи
    print("\n2. Завершение задачи...")
    task2.status = "completed"
    task2.completed_at = datetime.now(tz)
    session.commit()
    
    completed = session.query(Task).filter_by(user_id=user.id, status="completed").count()
    pending = session.query(Task).filter_by(user_id=user.id, status="pending").count()
    print(f"   Завершено: {completed}, В работе: {pending}")
    
    # 3. Перенос задачи
    print("\n3. Перенос задачи...")
    old_time = task1.reminder_time
    task1.reminder_time = now + timedelta(days=1, hours=10)
    session.commit()
    print(f"   {task1.title}: {old_time.strftime('%d.%m %H:%M')} -> {task1.reminder_time.strftime('%d.%m %H:%M')}")
    
    # 4. Проверка качества задач
    print("\n4. Проверка качества...")
    generic_titles = ["заняться вопросом", "сделать это", "та задача"]
    issues = []
    
    for task in tasks:
        if any(g in task.title.lower() for g in generic_titles):
            issues.append(f"{task.title} - неконкретное название")
        if len(task.title) < 10:
            issues.append(f"{task.title} - слишком короткое")
        if not task.reminder_time:
            issues.append(f"{task.title} - нет времени")
    
    if issues:
        print(f"   Найдено проблем: {len(issues)}")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print(f"   [OK] Все задачи качественные")
    
    # 5. Удаление задачи
    print("\n5. Удаление задачи...")
    session.delete(task2)
    session.commit()
    remaining = session.query(Task).filter_by(user_id=user.id).count()
    print(f"   Осталось задач: {remaining}")
    
    return len(issues) == 0

def test_data_integrity(session, user):
    """Тест целостности данных"""
    print("\n=== ТЕСТ ЦЕЛОСТНОСТИ ДАННЫХ ===\n")
    
    # Проверка связей
    print("1. Проверка связей...")
    user_from_db = session.query(User).filter_by(id=user.id).first()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    
    print(f"   Пользователь: {user_from_db.username}")
    print(f"   Профиль: {profile.city if profile else 'НЕТ'}")
    print(f"   Задачи: {len(tasks)}")
    
    # Проверка временных зон
    print("\n2. Проверка временных зон...")
    for task in tasks:
        if task.reminder_time:
            has_tz = task.reminder_time.tzinfo is not None
            print(f"   {task.title}: {'UTC' if has_tz else 'БЕЗ ЗОНЫ'}")
            if not has_tz:
                return False
    
    print("   [OK] Все времена с timezone")
    
    return True

def cleanup(session):
    """Очистка"""
    session.execute(text("DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM users WHERE telegram_id = :tid"), {"tid": TEST_USER_ID})
    session.commit()
    print("\n[+] Тестовые данные очищены")

def main():
    print("="*60)
    print("ТЕСТ РАБОТЫ С БАЗОЙ ДАННЫХ")
    print("="*60)
    
    session = Session()
    
    try:
        user = setup_test_user(session)
        
        # Запуск тестов
        test1 = test_task_operations(session, user)
        test2 = test_data_integrity(session, user)
        
        # Итог
        print("\n" + "="*60)
        print("РЕЗУЛЬТАТЫ")
        print("="*60)
        print(f"Качество задач: {'[OK]' if test1 else '[FAIL]'}")
        print(f"Целостность данных: {'[OK]' if test2 else '[FAIL]'}")
        
        if test1 and test2:
            print("\n[SUCCESS] Все тесты пройдены!")
        else:
            print("\n[WARNING] Есть проблемы")
        
    finally:
        cleanup(session)
        session.close()

if __name__ == "__main__":
    main()
