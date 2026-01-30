"""Тест реального переноса задачи"""
import asyncio
import os

# Enable free access for testing
os.environ['FREE_ACCESS_MODE'] = '1'

from models import init_db, Session, User, Task
from ai_integration.chat import chat_with_ai

async def test_reschedule():
    init_db()
    session = Session()
    
    # Создаём пользователя если нет
    user = session.query(User).filter_by(telegram_id=999999).first()
    if not user:
        user = User(telegram_id=999999, username='test')
        session.add(user)
        session.commit()
    
    print("[1] CREATE TASK")
    r1 = await chat_with_ai(
        message='напомни проверить почту через 5 минут',
        user_id=999999,
        db_session=session
    )
    print(f"AI Response: {r1['response'][:100]}")
    
    print("\n[2] CHECK TASKS BEFORE RESCHEDULE")
    session.commit()  # Commit any pending changes
    tasks_before = session.query(Task).filter_by(user_id=user.id).filter(
        Task.status.in_(['active', 'pending'])
    ).all()
    print(f"Found {len(tasks_before)} tasks")
    for t in tasks_before:
        print(f"  - {t.title}: {t.reminder_time} ({t.status})")
    
    print("\n[3] RESCHEDULE TASK")
    r3 = await chat_with_ai(
        message='перенеси на 15 минут',
        user_id=999999,
        db_session=session
    )
    print(f"AI Response: {r3['response'][:100]}")
    print(f"Tool calls: {r3.get('tool_calls', [])}")
    
    # ВАЖНО: refresh from DB!
    session.expire_all()
    
    print("\n[4] CHECK TASKS AFTER RESCHEDULE")
    tasks_after = session.query(Task).filter_by(user_id=user.id).filter(
        Task.status.in_(['active', 'pending'])
    ).all()
    for t in tasks_after:
        print(f"  - {t.title}: {t.reminder_time} ({t.status})")
    
    # Compare
    if tasks_before and tasks_after:
        # Find the task that was just created (last one)
        created_task = tasks_before[-1]
        # Find same task after reschedule (by ID)
        rescheduled_task = next((t for t in tasks_after if t.id == created_task.id), None)
        
        if rescheduled_task:
            time_before = created_task.reminder_time
            time_after = rescheduled_task.reminder_time
            if time_before != time_after:
                print(f"\n✅ SUCCESS: Task {created_task.id} time changed from {time_before} to {time_after}")
            else:
                print(f"\n❌ FAIL: Task {created_task.id} time NOT changed, still {time_before}")
        else:
            print(f"\n❌ FAIL: Task {created_task.id} not found after reschedule")
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_reschedule())
