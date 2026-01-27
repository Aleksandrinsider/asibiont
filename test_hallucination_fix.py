#!/usr/bin/env python3
"""
Тест для проверки устранения галлюцинаций и неправильной интерпретации команд
"""
import asyncio
import os
os.environ['LOCAL'] = '1'
from ai_integration.chat import chat_with_ai
from models import Session, Task, User, UserProfile
import random

async def test_hallucination_fix():
    """Тест проблемного сценария из issue"""
    session = Session()
    
    # Создаем тестового пользователя
    user_id = random.randint(100000, 999999)
    test_user = User(telegram_id=user_id, username='test_user', timezone='Europe/Moscow')
    session.add(test_user)
    session.commit()
    
    # Создаем профиль пользователя (чтобы избежать проактивных вопросов)
    profile = UserProfile(
        user_id=test_user.id,
        city='Москва',
        company='ASI Biont',
        position='Директор',
        interests='покер, спорт',
        skills='управление',
        goals='развитие компании'
    )
    session.add(profile)
    session.commit()
    
    print(f'🧪 Тест устранения галлюцинаций для user_id={user_id}')
    print('=' * 80)
    
    # Тест 1: Простое приветствие не должно вызывать галлюцинации
    print('\n📝 ТЕСТ 1: Простое приветствие')
    print('Ожидание: Простой ответ-приветствие БЕЗ упоминания несуществующих задач')
    response = await chat_with_ai('Привет', user_id=user_id, db_session=session)
    print(f'Ответ: {response}')
    
    # Проверяем, что нет упоминаний о несуществующих событиях
    hallucination_keywords = ['покер', '19:00', 'вечер', 'планируешь']
    has_hallucination = any(kw in response.lower() for kw in hallucination_keywords)
    if has_hallucination:
        print('❌ ПРОВАЛ: Обнаружена галлюцинация о несуществующих планах!')
    else:
        print('✅ УСПЕХ: Нет галлюцинаций')
    
    # Тест 2: Создание одной задачи
    print('\n📝 ТЕСТ 2: Создание задачи "заказать продукты"')
    print('Ожидание: Создается ОДНА задача на 17:17 (через 5 минут)')
    response = await chat_with_ai('Напомни заказать продукты из магазина через 5 минут', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:200]}...')
    
    # Проверяем задачу в БД
    tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    print(f'Задач в БД: {len(tasks)}')
    if len(tasks) == 1:
        print(f'✅ Создана 1 задача: "{tasks[0].title}"')
    else:
        print(f'❌ ПРОВАЛ: Ожидалась 1 задача, создано {len(tasks)}')
    
    # Тест 3: Добавление второй задачи (не перенос!)
    print('\n📝 ТЕСТ 3: Добавление второй задачи "встретить сына"')
    print('Ожидание: Создается ВТОРАЯ задача через час, первая остается')
    response = await chat_with_ai('так и встретить сына через час', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:200]}...')
    
    # Проверяем задачи в БД
    tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    print(f'Задач в БД: {len(tasks)}')
    
    if len(tasks) == 2:
        print('✅ УСПЕХ: Создано 2 задачи')
        for i, task in enumerate(tasks, 1):
            print(f'   {i}. "{task.title}" - {task.reminder_time}')
    else:
        print(f'❌ ПРОВАЛ: Ожидалось 2 задачи, найдено {len(tasks)}')
        
    # Проверяем, что не было неправильной интерпретации (перенос вместо создания)
    if 'перен' in response.lower():
        print('❌ ПРОВАЛ: Агент решил, что нужно перенести задачу вместо создания новой!')
    else:
        print('✅ УСПЕХ: Агент правильно интерпретировал команду как создание новой задачи')
    
    # Тест 4: Простой разговор не должен создавать задачи
    print('\n📝 ТЕСТ 4: Обычный разговор без команд')
    print('Ожидание: Задачи НЕ создаются, только разговор')
    initial_count = len(tasks)
    response = await chat_with_ai('Как погода сегодня?', user_id=user_id, db_session=session)
    print(f'Ответ: {response[:150]}...')
    
    tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').all()
    if len(tasks) == initial_count:
        print(f'✅ УСПЕХ: Задачи не создавались ({len(tasks)} остается)')
    else:
        print(f'❌ ПРОВАЛ: Неожиданно создана задача! Было {initial_count}, стало {len(tasks)}')
    
    print('\n' + '=' * 80)
    print('🎉 Тесты завершены!')
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_hallucination_fix())
