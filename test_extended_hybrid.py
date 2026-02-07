"""
Тест расширенного гибридного подхода
Проверяем: analyze_tasks, find_partners, find_relevant_contacts, delegate_task, update_profile
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from config import DATABASE_URL
from datetime import datetime, timedelta

async def test_extended_commands():
    user_id = 123456789
    Base.metadata.create_all(engine)
    session = Session()
    
    # Setup user
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='test', first_name='Test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, interests='Python, AI, стартапы', goals='Запустить AI-продукт', city='Moscow')
        session.add(profile)
        session.commit()
    
    # Add test tasks
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    task1 = Task(user_id=user.id, title='Написать код', status='pending', 
                 reminder_time=datetime.now() + timedelta(hours=1))
    task2 = Task(user_id=user.id, title='Позвонить клиенту', status='pending',
                 reminder_time=datetime.now() + timedelta(days=1))
    task3 = Task(user_id=user.id, title='Проверить баги', status='pending',
                 reminder_time=datetime.now() + timedelta(hours=2))
    session.add_all([task1, task2, task3])
    session.commit()
    
    print("="*60)
    print("ТЕСТ 1: analyze_tasks")
    print("="*60)
    response = await chat_with_ai('Проанализируй мои задачи, что делать в первую очередь?', 
                                   user_id=user_id, db_session=session)
    print(f"Ответ: {response.get('response', 'No response')[:200]}")
    print(f"Actions: {response.get('actions', [])}")
    print()
    
    print("="*60)
    print("ТЕСТ 2: find_partners")
    print("="*60)
    response = await chat_with_ai('Найди мне партнеров которые занимаются AI стартапами', 
                                   user_id=user_id, db_session=session)
    print(f"Ответ: {response.get('response', 'No response')[:200]}")
    print(f"Actions: {response.get('actions', [])}")
    print()
    
    print("="*60)
    print("ТЕСТ 3: find_relevant_contacts_for_task")
    print("="*60)
    response = await chat_with_ai('Кто может помочь с написанием кода на Python?', 
                                   user_id=user_id, db_session=session)
    print(f"Ответ: {response.get('response', 'No response')[:200]}")
    print(f"Actions: {response.get('actions', [])}")
    print()
    
    print("="*60)
    print("ТЕСТ 4: delegate_task")
    print("="*60)
    response = await chat_with_ai('Делегируй задачу "Написать код" кому-нибудь', 
                                   user_id=user_id, db_session=session)
    print(f"Ответ: {response.get('response', 'No response')[:200]}")
    print(f"Actions: {response.get('actions', [])}")
    print()
    
    print("="*60)
    print("ТЕСТ 5: update_profile")
    print("="*60)
    response = await chat_with_ai('Обнови мой профиль: добавь навык TypeScript', 
                                   user_id=user_id, db_session=session)
    print(f"Ответ: {response.get('response', 'No response')[:200]}")
    print(f"Actions: {response.get('actions', [])}")
    print()
    
    # Check tool calls
    print("="*60)
    print("ПРОВЕРКА: Были ли вызваны инструменты?")
    print("="*60)
    all_actions = []
    for test_name in ['analyze_tasks', 'find_partners', 'find_relevant_contacts', 'delegate_task', 'update_profile']:
        # Смотрим в лог были ли вызовы
        print(f"{test_name}: проверь логи выше")
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_extended_commands())
