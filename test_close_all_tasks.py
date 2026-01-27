"""
Тест команды закрытия всех задач с разными формулировками
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from models import Session, User, Task, UserProfile
from ai_integration.chat import chat_with_ai
from datetime import datetime, timedelta
import pytz


async def test_close_all_commands():
    """Тестируем разные формулировки команды закрытия всех задач"""
    
    # Используем тестового пользователя
    user_id = 999888  # Уникальный тестовый ID
    session = Session()
    
    # Создаем/получаем тестового пользователя
    test_user = session.query(User).filter_by(telegram_id=user_id).first()
    if not test_user:
        test_user = User(
            telegram_id=user_id,
            username='test_close_all',
            first_name='Test',
            timezone='Europe/Moscow'
        )
        session.add(test_user)
        session.commit()
    
    # Создаем профиль если нет
    profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
    if not profile:
        profile = UserProfile(user_id=test_user.id)
        session.add(profile)
        session.commit()
    
    # Удаляем старые задачи
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    
    print(f'🧪 Тест команды закрытия всех задач для user_id={user_id}')
    print('=' * 80)
    
    # Создаем 3 тестовые задачи
    print('\n📝 Создание 3 тестовых задач...')
    test_tasks = [
        ('проверить почту', 'через 10 минут'),
        ('позвонить менеджеру', 'через 1 час'),
        ('подготовить презентацию', 'завтра в 10:00'),
    ]
    
    for title, time in test_tasks:
        response = await chat_with_ai(f'Напомни {title} {time}', user_id=user_id, db_session=session)
        print(f'  ✓ Создана: "{title}"')
    
    session.expire_all()
    initial_tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    print(f'\n✅ Создано {len(initial_tasks)} активных задач')
    
    # Тестируем разные формулировки
    test_commands = [
        'Закрой все мои задачи',
        'Удали все задачи',
        'Очисти все задачи',
        'Убери все задачи',
        'Удали все мои задачи',
        'Закрыть все задачи',
        'Завершить все задачи',
        'Отметить все как выполненные',
        'Сделал все задачи',
        'Готово со всеми задачами',
    ]
    
    print('\n' + '=' * 80)
    print('🔍 ТЕСТИРОВАНИЕ РАЗНЫХ ФОРМУЛИРОВОК')
    print('=' * 80)
    
    for i, command in enumerate(test_commands, 1):
        # Пересоздаем задачи перед каждым тестом
        session.query(Task).filter_by(user_id=test_user.id).delete()
        session.commit()
        
        # Создаем 3 задачи
        for title, time in test_tasks:
            await chat_with_ai(f'Напомни {title} {time}', user_id=user_id, db_session=session)
        
        session.expire_all()
        before_count = session.query(Task).filter_by(user_id=test_user.id, status='pending').count()
        
        print(f'\n📋 Тест #{i}: "{command}"')
        print(f'   До: {before_count} активных задач')
        
        # Выполняем команду
        response = await chat_with_ai(command, user_id=user_id, db_session=session)
        print(f'   Ответ: {response[:150]}...')
        
        # Проверяем результат
        session.expire_all()
        after_pending = session.query(Task).filter_by(user_id=test_user.id, status='pending').count()
        after_completed = session.query(Task).filter_by(user_id=test_user.id, status='completed').count()
        after_total = session.query(Task).filter_by(user_id=test_user.id).count()
        
        print(f'   После: {after_pending} активных, {after_completed} завершенных (всего {after_total})')
        
        # Проверка результата
        if after_pending == 0 and (after_completed == 3 or after_total == 0):
            print(f'   ✅ УСПЕХ: Все задачи обработаны корректно')
        else:
            print(f'   ❌ ОШИБКА: Задачи не обработаны! Pending={after_pending}, Completed={after_completed}')
            # Показываем какие tools были вызваны (если есть в логах)
    
    print('\n' + '=' * 80)
    print('📊 ИТОГИ ТЕСТИРОВАНИЯ')
    print('=' * 80)
    
    # Финальная очистка
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.commit()
    session.close()
    
    print('\n🎉 Тест завершен!')


if __name__ == '__main__':
    asyncio.run(test_close_all_commands())
