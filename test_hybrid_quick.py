"""Быстрый тест гибридного подхода"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task

async def quick_test():
    user_id = 777888999
    session = Session()
    
    # Создаем пользователя если нет
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        from models import UserProfile
        user = User(telegram_id=user_id, username='test_hybrid', first_name='TestHybrid', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, city='Москва', goals='Тестирование')
        session.add(profile)
        session.commit()
    
    # Очистка задач
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()
    
    # Тест 1: Создание
    print('[TEST 1] Создание задачи...')
    session = Session()
    r = await chat_with_ai('Создай задачу проверить почту завтра в 10:00', user_id=user_id, db_session=session)
    session.close()
    print(f"Ответ: {r.get('response', '')[:150]}")
    
    # Тест 2: Редактирование через AI (без keywords)
    print('\n[TEST 2] Редактирование задачи через AI...')
    session = Session()
    r = await chat_with_ai('Измени название задачи "проверить почту" на "Важная почта от клиента"', user_id=user_id, db_session=session)
    session.close()
    print(f"Ответ: {r.get('response', '')[:150]}")
    
    # Тест 3: Перенос через AI
    print('\n[TEST 3] Перенос задачи через AI...')
    session = Session()
    r = await chat_with_ai('Перенеси задачу про почту на послезавтра в 14:00', user_id=user_id, db_session=session)
    session.close()
    print(f"Ответ: {r.get('response', '')[:150]}")
    
    # Тест 4: Удаление через AI
    print('\n[TEST 4] Удаление задачи через AI...')
    session = Session()
    r = await chat_with_ai('Удали задачу про почту от клиента', user_id=user_id, db_session=session)
    session.close()
    print(f"Ответ: {r.get('response', '')[:150]}")
    
    # Проверка в БД
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        tasks = session.query(Task).filter_by(user_id=user.id, status='pending').all()
        print(f'\n[DB CHECK] Задач в БД: {len(tasks)}')
        for t in tasks:
            print(f'  - Title: "{t.title}"')
            print(f'    Status: {t.status}')
    session.close()
    
    print('\n[SUCCESS] Тест завершен!')

if __name__ == '__main__':
    asyncio.run(quick_test())
