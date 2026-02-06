"""
Тест реальных handler функций
Проверяет add_task, complete_task, reschedule_task через API
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import asyncio
from models import Session, User, Task, UserProfile
from ai_integration.handlers import add_task, complete_task, delete_task, reschedule_task, list_tasks
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
        goals="Продвигать агента на рынке"
    )
    session.add(profile)
    session.commit()
    
    print(f"[+] Создан пользователь ID={user.id}, timezone={user.timezone}")
    return user

async def test_handlers(session, user):
    """Тест через handlers API"""
    print("\n=== ТЕСТ HANDLERS API ===\n")
    issues = []
    
    # 1. Создание задач через add_task
    print("1. Создание задач через add_task...")
    
    result1 = await add_task(
        title="Позвонить клиенту",
        description="Обсудить условия",
        reminder_time="завтра в 15:00",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result1}")
    
    result2 = await add_task(
        title="Купить молоко",
        reminder_time="через 2 часа",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result2}")
    
    result3 = await add_task(
        title="Работа над реферальной программой",
        description="Найти партнеров",
        reminder_time="через 30 минут",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result3}")
    
    # Проверка созданных задач
    session.expire_all()  # Обновить данные из БД
    user_obj = session.query(User).filter(User.telegram_id == TEST_USER_ID).first()
    tasks = session.query(Task).filter(Task.user_id == user_obj.id).all()
    print(f"\n   Создано всего: {len(tasks)} задач")
    
    # Проверка timezone
    print("\n2. Проверка timezone в задачах...")
    for task in tasks:
        if task.reminder_time:
            has_tz = task.reminder_time.tzinfo is not None
            status = "[OK]" if has_tz else "[FAIL]"
            print(f"   {status} {task.title}: {task.reminder_time} (tz: {task.reminder_time.tzinfo})")
            if not has_tz:
                issues.append(f"Задача '{task.title}' без timezone")
    
    # 3. Проверка конкретности названий
    print("\n3. Проверка качества названий...")
    generic_titles = ["заняться вопросом", "сделать это", "та задача", "вопрос"]
    
    for task in tasks:
        is_generic = any(g in task.title.lower() for g in generic_titles)
        is_short = len(task.title) < 10
        
        if is_generic:
            print(f"   [FAIL] {task.title} - неконкретное")
            issues.append(f"Неконкретное название: {task.title}")
        elif is_short:
            print(f"   [WARN] {task.title} - короткое")
        else:
            print(f"   [OK] {task.title}")
    
    # 4. Завершение задачи
    print("\n4. Завершение задачи...")
    result = await complete_task(
        task_title="Купить молоко",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result}")
    
    session.expire_all()
    user_obj = session.query(User).filter(User.telegram_id == TEST_USER_ID).first()
    completed_task = session.query(Task).filter(
        Task.user_id == user_obj.id,
        Task.title.like("%молоко%")
    ).first()
    
    if completed_task and completed_task.status == "completed":
        print(f"   [OK] Задача завершена")
        if hasattr(completed_task, 'actual_completion_time') and completed_task.actual_completion_time:
            print(f"   [OK] actual_completion_time: {completed_task.actual_completion_time}")
        else:
            print(f"   [WARN] actual_completion_time не установлено")
    else:
        print(f"   [FAIL] Задача не завершена")
        issues.append("Не удалось завершить задачу")
    
    # 5. Перенос задачи
    print("\n5. Перенос задачи...")
    result = await reschedule_task(
        task_title="Позвонить клиенту",
        new_time="послезавтра в 10:00",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result}")
    
    session.expire_all()
    user_obj = session.query(User).filter(User.telegram_id == TEST_USER_ID).first()
    rescheduled_task = session.query(Task).filter(
        Task.user_id == user_obj.id,
        Task.title.like("%клиенту%")
    ).first()
    
    if rescheduled_task and rescheduled_task.reminder_time:
        print(f"   [OK] Новое время: {rescheduled_task.reminder_time}")
    else:
        print(f"   [FAIL] Не удалось перенести")
        issues.append("Не удалось перенести задачу")
    
    # 6. Список задач
    print("\n6. Получение списка задач...")
    result = list_tasks(user_id=TEST_USER_ID, session=session)
    print(f"   {result[:200]}...")  # Первые 200 символов
    
    # 7. Удаление задачи
    print("\n7. Удаление задачи...")
    result = await delete_task(
        task_title="Работа над реферальной",
        user_id=TEST_USER_ID,
        session=session
    )
    print(f"   {result}")
    
    session.expire_all()
    user_obj = session.query(User).filter(User.telegram_id == TEST_USER_ID).first()
    remaining = session.query(Task).filter(Task.user_id == user_obj.id).count()
    print(f"   Осталось задач: {remaining}")
    
    return len(issues) == 0, issues

def cleanup(session):
    """Очистка"""
    session.execute(text("DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM user_profiles WHERE user_id IN (SELECT id FROM users WHERE telegram_id = :tid)"), {"tid": TEST_USER_ID})
    session.execute(text("DELETE FROM users WHERE telegram_id = :tid"), {"tid": TEST_USER_ID})
    session.commit()
    print("\n[+] Тестовые данные очищены")

async def main():
    print("="*60)
    print("ТЕСТ HANDLERS API")
    print("="*60)
    
    session = Session()
    
    try:
        user = setup_test_user(session)
        success, issues = await test_handlers(session, user)
        
        # Итог
        print("\n" + "="*60)
        print("РЕЗУЛЬТАТЫ")
        print("="*60)
        
        if success:
            print("[SUCCESS] Все тесты пройдены!")
        else:
            print(f"[WARNING] Найдено проблем: {len(issues)}")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. {issue}")
        
    finally:
        cleanup(session)
        session.close()

if __name__ == "__main__":
    asyncio.run(main())
