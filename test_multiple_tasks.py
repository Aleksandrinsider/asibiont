#!/usr/bin/env python3
"""
Тест работы агента с множественными задачами
"""
import asyncio
import os
os.environ['LOCAL'] = '1'
from ai_integration.chat import chat_with_ai
from models import Session, Task, User, UserProfile
import random

async def test_multiple_tasks():
    """Тест правильности работы с несколькими задачами"""
    session = Session()
    
    # Создаем тестового пользователя
    user_id = random.randint(100000, 999999)
    test_user = User(telegram_id=user_id, username='test_user', timezone='Europe/Moscow')
    session.add(test_user)
    session.commit()
    
    # Создаем профиль
    profile = UserProfile(
        user_id=test_user.id,
        city='Москва',
        company='Test Corp',
        position='Manager',
        interests='работа',
        skills='управление',
        goals='продуктивность'
    )
    session.add(profile)
    session.commit()
    
    print(f'🧪 Тест работы с множественными задачами для user_id={user_id}')
    print('=' * 80)
    
    # ШАГ 1: Создаем несколько задач
    print('\n📝 ШАГ 1: Создание нескольких задач')
    tasks_to_create = [
        ('Напомни позвонить клиенту через 30 минут', 'позвонить клиенту'),
        ('Добавь задачу подготовить отчет через 1 час', 'подготовить отчет'),
        ('Создай задачу встреча с командой через 2 часа', 'встреча с командой'),
    ]
    
    for msg, expected_title in tasks_to_create:
        print(f'\n→ Создаем: "{msg}"')
        # ВАЖНО: Обновляем сессию перед каждым вызовом
        session.expire_all()  # Сбрасываем кэш SQLAlchemy
        response = await chat_with_ai(msg, user_id=user_id, db_session=session)
        print(f'  Ответ: {response[:150]}...')
        
        # Проверяем создание
        session.expire_all()  # Обновляем данные
        tasks = session.query(Task).filter_by(
            user_id=test_user.id,
            status='pending'
        ).all()
        print(f'  ✓ Задач в БД: {len(tasks)}')
    
    # Проверяем финальное количество задач
    all_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    print(f'\n✅ Всего создано {len(all_tasks)} задач:')
    for i, task in enumerate(all_tasks, 1):
        print(f'   {i}. "{task.title}"')
    
    if len(all_tasks) != 3:
        print(f'❌ ОШИБКА: Ожидалось 3 задачи, создано {len(all_tasks)}')
    
    # ШАГ 2: Завершение задачи
    print('\n📝 ШАГ 2: Завершение задачи "позвонить клиенту"')
    print('Ожидание: Задача завершается, НЕ создается новая')
    
    initial_count = len(all_tasks)
    response = await chat_with_ai('Готово позвонить клиенту', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:200]}...')
    
    # Проверяем
    pending_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    completed_tasks = session.query(Task).filter_by(user_id=test_user.id, status='completed').all()
    
    print(f'Активных: {len(pending_tasks)}, Завершенных: {len(completed_tasks)}')
    
    if len(completed_tasks) != 1:
        print(f'❌ ОШИБКА: Задача не завершена! Completed={len(completed_tasks)}')
    if len(pending_tasks) != 2:
        print(f'❌ ОШИБКА: Неправильное количество активных задач! Pending={len(pending_tasks)}')
    
    # Проверяем, не создалась ли новая задача с похожим названием
    new_call_tasks = [t for t in pending_tasks if 'позвонить' in t.title.lower()]
    if new_call_tasks:
        print(f'❌ КРИТИЧЕСКАЯ ОШИБКА: Создана новая задача вместо завершения! "{new_call_tasks[0].title}"')
    else:
        print('✅ УСПЕХ: Задача завершена правильно, новая не создана')
    
    # ШАГ 3: Удаление задачи
    print('\n📝 ШАГ 3: Удаление задачи "подготовить отчет"')
    print('Ожидание: Задача удаляется, НЕ завершается и НЕ создается новая')
    
    response = await chat_with_ai('Удали задачу подготовить отчет', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:200]}...')
    
    # Проверяем
    pending_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    deleted_tasks = session.query(Task).filter_by(user_id=test_user.id, status='deleted').all()
    
    print(f'Активных: {len(pending_tasks)}, Удаленных: {len(deleted_tasks)}')
    
    if len(deleted_tasks) != 1:
        print(f'❌ ОШИБКА: Задача не удалена! Deleted={len(deleted_tasks)}')
    if len(pending_tasks) != 1:
        print(f'❌ ОШИБКА: Неправильное количество активных задач! Pending={len(pending_tasks)}')
    else:
        print('✅ УСПЕХ: Задача удалена правильно')
    
    # ШАГ 4: Перенос задачи
    print('\n📝 ШАГ 4: Перенос задачи "встреча с командой" на другое время')
    print('Ожидание: Задача переносится, НЕ создается новая')
    
    old_task = pending_tasks[0] if pending_tasks else None
    old_time = old_task.reminder_time if old_task else None
    
    response = await chat_with_ai('Перенеси встречу с командой на завтра в 10:00', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:200]}...')
    
    # Проверяем
    pending_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    
    print(f'Активных задач: {len(pending_tasks)}')
    
    if len(pending_tasks) != 1:
        print(f'❌ КРИТИЧЕСКАЯ ОШИБКА: Создана новая задача вместо переноса! Pending={len(pending_tasks)}')
        for task in pending_tasks:
            print(f'  - "{task.title}" на {task.reminder_time}')
    else:
        new_time = pending_tasks[0].reminder_time
        if old_time and new_time != old_time:
            print(f'✅ УСПЕХ: Задача перенесена с {old_time} на {new_time}')
        else:
            print(f'⚠️ ПРЕДУПРЕЖДЕНИЕ: Время не изменилось')
    
    # ШАГ 5: Просмотр задач НЕ должен создавать новые
    print('\n📝 ШАГ 5: Просмотр списка задач')
    print('Ожидание: Показывается список, НЕ создаются/изменяются задачи')
    
    count_before = len(session.query(Task).filter_by(user_id=test_user.id, status='pending').all())
    
    response = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:300]}...')
    
    count_after = len(session.query(Task).filter_by(user_id=test_user.id, status='pending').all())
    
    if count_before != count_after:
        print(f'❌ КРИТИЧЕСКАЯ ОШИБКА: Просмотр изменил задачи! Было {count_before}, стало {count_after}')
    else:
        print('✅ УСПЕХ: Просмотр не изменил задачи')
    
    # ФИНАЛЬНАЯ СТАТИСТИКА
    print('\n' + '=' * 80)
    print('📊 ФИНАЛЬНАЯ СТАТИСТИКА:')
    all_tasks = session.query(Task).filter_by(user_id=test_user.id).all()
    print(f'Всего задач: {len(all_tasks)}')
    print(f'  Активных: {len([t for t in all_tasks if t.status == "pending"])}')
    print(f'  Завершенных: {len([t for t in all_tasks if t.status == "completed"])}')
    print(f'  Удаленных: {len([t for t in all_tasks if t.status == "deleted"])}')
    
    print('\n🎉 Тест завершен!')
    session.close()

if __name__ == "__main__":
    asyncio.run(test_multiple_tasks())
