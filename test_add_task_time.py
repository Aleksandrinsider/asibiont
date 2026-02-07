"""Test add_task with and without time"""
import asyncio
import sys
import os
import logging

# Enable detailed logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine

async def test_add_task():
    user_id = 123456789
    Base.metadata.create_all(engine)
    session = Session()
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='test_user', first_name='Test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, interests='спорт', goals='здоровье', city='Moscow')
        session.add(profile)
        session.commit()
    
    # Clear tasks
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    print('\n' + '='*60)
    print('TEST: Creating task with time')
    print('='*60 + '\n')
    
    # Test 1: With explicit time
    print('Test 1: Request WITH explicit time')
    print('Query: "napomni mne zavtra v 10 utra pozvonit klientu"\n')
    
    response1 = await chat_with_ai(
        'напомни мне завтра в 10 утра позвонить клиенту',
        user_id=user_id,
        db_session=session
    )
    
    print('Agent response:')
    try:
        print(response1.get('response', 'No response').encode('utf-8', errors='ignore').decode('utf-8')[:300])
    except:
        print('[Response contains special characters]')
    print()
    
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f'Tasks created: {len(tasks)}')
    if tasks:
        for t in tasks:
            print(f'   - "{t.title}" at {t.reminder_time}')
    else:
        print('   ERROR: Task not created!')
    print()
    
    # Clear
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    # Test 2: Without time
    print('-'*60)
    print('Test 2: Request WITHOUT time')
    print('Query: "dobav zadachu kupit moloko"\n')
    
    response2 = await chat_with_ai(
        'добавь задачу купить молоко',
        user_id=user_id,
        db_session=session
    )
    
    print('Agent response:')
    try:
        print(response2.get('response', 'No response').encode('utf-8', errors='ignore').decode('utf-8')[:300])
    except:
        print('[Response contains special characters]')
    print()
    
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f'Tasks created: {len(tasks)}')
    if tasks:
        for t in tasks:
            print(f'   - "{t.title}" at {t.reminder_time}')
        print('   WARNING: Agent created task WITHOUT asking for time - WRONG')
    else:
        print('   OK: Task NOT created - agent should have asked for time')
    
    print('\n' + '='*60 + '\n')
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_add_task())
