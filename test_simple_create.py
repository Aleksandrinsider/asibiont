"""Простой тест - только создание задачи"""
import os
os.environ['FREE_ACCESS_MODE'] = '1'

from models import init_db, Session, User, Task
import asyncio
from ai_integration.chat import chat_with_ai

async def test_create():
    init_db()
    s = Session()
    u = s.query(User).filter_by(telegram_id=999999).first()
    if not u:
        u = User(telegram_id=999999, username='test')
        s.add(u)
        s.commit()
    
    print(f"[USER] ID={u.id}, telegram_id={u.telegram_id}")
    
    print("[CREATE]")
    r = await chat_with_ai(message='напомни купить молоко завтра', user_id=999999, db_session=s)
    print(f"Response: {r['response'][:80]}")
    print(f"Tool calls: {r.get('tool_calls', [])}")
    
    # Check если были ошибки
    if 'tool_calls' in r:
        for tc in r['tool_calls']:
            print(f"  Tool: {tc['function']}, Args: {tc.get('arguments', '')[:50]}")
    
    s.expire_all()  # Force reload from DB
    tasks = s.query(Task).filter_by(user_id=u.id).filter(Task.status.in_(['active', 'pending'])).all()
    print(f"\nTasks in DB (user_id={u.id}): {len(tasks)}")
    for t in tasks:
        print(f"  - {t.title}: {t.reminder_time} ({t.status})")
    s.close()

asyncio.run(test_create())
