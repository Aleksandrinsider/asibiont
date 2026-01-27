"""
Тест продвинутых операций: делегирование, переносы, описания
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from models import Session, User, Task, UserProfile
from ai_integration.chat import chat_with_ai
from datetime import datetime
import pytz


async def test_advanced_operations():
    """Тестируем делегирование, переносы и описания"""
    
    user_id = 888999
    session = Session()
    
    # Создаем пользователя
    test_user = session.query(User).filter_by(telegram_id=user_id).first()
    if not test_user:
        test_user = User(
            telegram_id=user_id,
            username='test_advanced',
            first_name='TestUser',
            timezone='Europe/Moscow'
        )
        session.add(test_user)
        session.commit()
    
    # Профиль
    if not session.query(UserProfile).filter_by(user_id=test_user.id).first():
        session.add(UserProfile(user_id=test_user.id))
        session.commit()
    
    # Очистка
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    
    print(f'🧪 Тест продвинутых операций для user_id={user_id}')
    print('=' * 80)
    
    # ТЕСТ 1: Создание задачи с описанием
    print('\n📝 ТЕСТ 1: Создание задачи с описанием')
    print('-' * 80)
    
    test_cases_create = [
        ('Напомни позвонить клиенту через 1 час, обсудить новый контракт', 'обсудить новый контракт'),
        ('Создай задачу встреча с инвестором завтра в 10:00. Нужно подготовить презентацию', 'Нужно подготовить презентацию'),
        ('Добавь задачу купить продукты через 2 часа - молоко, хлеб, яйца', 'молоко, хлеб, яйца'),
    ]
    
    for i, (command, expected_desc) in enumerate(test_cases_create, 1):
        print(f'\n{i}. "{command[:60]}..."')
        response = await chat_with_ai(command, user_id=user_id, db_session=session)
        print(f'   Ответ: {response[:100]}...')
        
        session.expire_all()
        tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
        
        if tasks:
            latest = tasks[-1]
            has_desc = latest.description and len(latest.description.strip()) > 0
            desc_matches = expected_desc.lower() in (latest.description or '').lower()
            
            print(f'   Задача: "{latest.title}"')
            print(f'   Описание: "{latest.description or "НЕТ"}"')
            
            if has_desc and desc_matches:
                print(f'   ✅ УСПЕХ: Описание сохранено')
            elif has_desc:
                print(f'   ⚠️ ЧАСТИЧНО: Описание есть, но не соответствует')
            else:
                print(f'   ❌ ОШИБКА: Описание отсутствует')
        else:
            print(f'   ❌ ОШИБКА: Задача не создана')
    
    # ТЕСТ 2: Перенос задач на другое время
    print('\n\n📅 ТЕСТ 2: Перенос задач')
    print('-' * 80)
    
    # Очистка и создание тестовых задач
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    
    await chat_with_ai('Напомни проверить почту через 30 минут', user_id=user_id, db_session=session)
    await chat_with_ai('Напомни встреча с командой через 2 часа', user_id=user_id, db_session=session)
    
    session.expire_all()
    tasks_before = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    print(f'\nСоздано {len(tasks_before)} задач для переноса:')
    for t in tasks_before:
        print(f'  - "{t.title}" на {t.reminder_time}')
    
    test_cases_reschedule = [
        ('Перенеси проверить почту на завтра в 10:00', 'проверить почту'),
        ('Измени время встречи с командой на послезавтра в 14:00', 'встреча с командой'),
        ('Перенеси проверить почту на 15:30', 'проверить почту'),
    ]
    
    for i, (command, task_keyword) in enumerate(test_cases_reschedule, 1):
        print(f'\n{i}. "{command}"')
        
        # Находим задачу до переноса
        session.expire_all()
        task_before = None
        for t in session.query(Task).filter_by(user_id=test_user.id, status='pending').all():
            if task_keyword.lower() in t.title.lower():
                task_before = t
                break
        
        if not task_before:
            print(f'   ⚠️ Задача "{task_keyword}" не найдена для переноса')
            continue
        
        old_time = task_before.reminder_time
        old_id = task_before.id
        
        response = await chat_with_ai(command, user_id=user_id, db_session=session)
        print(f'   Ответ: {response[:100]}...')
        
        # Проверяем изменение
        session.expire_all()
        task_after = session.query(Task).filter_by(id=old_id).first()
        
        if task_after and task_after.reminder_time != old_time:
            print(f'   Было: {old_time}')
            print(f'   Стало: {task_after.reminder_time}')
            print(f'   ✅ УСПЕХ: Задача перенесена')
        elif not task_after:
            # Проверяем не создалась ли новая задача
            new_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
            if len(new_tasks) > len(tasks_before):
                print(f'   ❌ ОШИБКА: Создана новая задача вместо переноса')
            else:
                print(f'   ❌ ОШИБКА: Задача исчезла')
        else:
            print(f'   ❌ ОШИБКА: Время не изменилось')
    
    # ТЕСТ 3: Делегирование задач
    print('\n\n👥 ТЕСТ 3: Делегирование задач')
    print('-' * 80)
    
    # Очистка
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    
    test_cases_delegate = [
        ('Делегируй Ивану задачу проверить документы через 2 часа', 'Иван', 'проверить документы'),
        ('Поручи @maria подготовить отчет завтра в 10:00', 'maria', 'подготовить отчет'),
        ('Передай Петрову задачу позвонить клиенту через 30 минут', 'Петров', 'позвонить клиенту'),
    ]
    
    for i, (command, delegate_to, task_title) in enumerate(test_cases_delegate, 1):
        print(f'\n{i}. "{command}"')
        response = await chat_with_ai(command, user_id=user_id, db_session=session)
        print(f'   Ответ: {response[:100]}...')
        
        session.expire_all()
        tasks = session.query(Task).filter_by(user_id=test_user.id).all()
        
        delegated_task = None
        for t in tasks:
            if t.delegated_to_username:
                delegated_task = t
                break
        
        if delegated_task:
            delegate_name = delegated_task.delegated_to_username.lower().replace('@', '')
            expected_name = delegate_to.lower().replace('@', '')
            
            print(f'   Задача: "{delegated_task.title}"')
            print(f'   Делегирована: {delegated_task.delegated_to_username}')
            print(f'   Статус: {delegated_task.delegation_status}')
            
            if expected_name in delegate_name or delegate_name in expected_name:
                print(f'   ✅ УСПЕХ: Задача делегирована правильно')
            else:
                print(f'   ⚠️ ЧАСТИЧНО: Делегирована, но другому человеку')
        else:
            print(f'   ❌ ОШИБКА: Задача не делегирована')
    
    # Итоги
    print('\n' + '=' * 80)
    print('📊 ИТОГИ ТЕСТИРОВАНИЯ')
    print('=' * 80)
    
    session.expire_all()
    total_tasks = session.query(Task).filter_by(user_id=test_user.id).count()
    pending = session.query(Task).filter_by(user_id=test_user.id, status='pending').count()
    delegated = session.query(Task).filter(
        Task.user_id == test_user.id,
        Task.delegated_to_username.isnot(None)
    ).count()
    
    print(f'\nВсего задач: {total_tasks}')
    print(f'Активных: {pending}')
    print(f'Делегированных: {delegated}')
    
    # Очистка
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    session.close()
    
    print('\n🎉 Тест завершен!')


if __name__ == '__main__':
    asyncio.run(test_advanced_operations())
