"""Комплексное тестирование всех функций handlers"""
import os
os.environ['LOCAL'] = '1'

import asyncio
from datetime import datetime, timezone, timedelta
from models import Session, User, UserProfile, Task
from ai_integration.handlers import (
    add_task, complete_task, list_tasks, 
    reschedule_task, delete_task_sync, update_user_memory_async,
    get_partners_list
)

async def test_all_functions():
    """Тестируем все функции последовательно"""
    session = Session()
    
    # Создаем тестового пользователя
    test_user = session.query(User).filter_by(username='test_user').first()
    if not test_user:
        print("❌ Тестовый пользователь не найден. Создайте пользователя с username='test_user'")
        return
    
    user_id = test_user.telegram_id
    print(f"OK Используем пользователя: {test_user.username} (ID: {user_id})")
    print("="*70)
    
    # 1. TEST: add_task
    print("\n1. ТЕСТ: add_task (создание задачи)")
    print("-"*70)
    try:
        result = add_task(
            title="Тестовая задача",
            description="Описание тестовой задачи",
            reminder_time="через 1 час",
            user_id=user_id,
            session=session
        )
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 2. TEST: list_tasks
    print("\n2. ТЕСТ: list_tasks (просмотр задач)")
    print("-"*70)
    try:
        result = list_tasks(user_id=user_id, include_completed=False, session=session)
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. TEST: reschedule_task
    print("\n3. ТЕСТ: reschedule_task (перенос задачи)")
    print("-"*70)
    try:
        result = await reschedule_task(
            task_title="тестовая",
            new_time="через 2 часа",
            user_id=user_id,
            session=session
        )
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 4. TEST: complete_task
    print("\n4. ТЕСТ: complete_task (завершение задачи)")
    print("-"*70)
    try:
        result = await complete_task(
            task_title="тестовая",
            completion_note="Выполнено успешно",
            user_id=user_id,
            session=session
        )
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 5. TEST: update_user_memory (интересы)
    print("\n5. ТЕСТ: update_user_memory (добавление интереса)")
    print("-"*70)
    try:
        result = await update_user_memory_async(
            memory_type="interest",
            content="программирование",
            user_id=user_id,
            session=session
        )
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 6. TEST: get_partners_list
    print("\n6. ТЕСТ: get_partners_list (поиск партнеров)")
    print("-"*70)
    try:
        partners = get_partners_list(user_id=test_user.id, session=session)
        print(f"✅ Найдено партнеров: {len(partners)}")
        if partners:
            for p in partners[:3]:
                u = session.query(User).filter_by(id=p.user_id).first()
                print(f"  - @{u.username if u else 'unknown'}: {p.interests or 'нет интересов'}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # 7. TEST: delete_task
    print("\n7. ТЕСТ: delete_task (удаление задачи)")
    print("-"*70)
    try:
        # Сначала создаем задачу для удаления
        add_result = add_task(
            title="Задача для удаления",
            reminder_time="завтра в 10:00",
            user_id=user_id,
            session=session
        )
        print(f"Создали: {add_result}")
        
        result = delete_task_sync(
            task_title="удаления",
            confirmed=True,
            user_id=user_id,
            session=session
        )
        print(f"✅ {result}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    # Проверяем финальное состояние профиля
    print("\n"+"="*70)
    print("📊 ФИНАЛЬНОЕ СОСТОЯНИЕ ПРОФИЛЯ")
    print("="*70)
    profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
    if profile:
        print(f"Интересы: {profile.interests or 'не указаны'}")
        print(f"Навыки: {profile.skills or 'не указаны'}")
        print(f"Город: {profile.city or 'не указан'}")
    
    tasks = session.query(Task).filter_by(user_id=test_user.id).all()
    print(f"\nВсего задач: {len(tasks)}")
    active = [t for t in tasks if t.status in ['active', 'pending', 'in_progress']]
    completed = [t for t in tasks if t.status == 'completed']
    print(f"  Активных: {len(active)}")
    print(f"  Завершенных: {len(completed)}")
    
    session.close()
    print("\n✅ Тестирование завершено")

if __name__ == '__main__':
    asyncio.run(test_all_functions())
