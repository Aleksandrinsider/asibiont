"""Simple AI test to check task creation"""
import asyncio
import json
from datetime import datetime
import pytz
from models import Session, User, Task
from ai_integration.chat import chat_with_ai

async def test_simple_task():
    """Test: 'напомни через 5 минут проверить почту'"""
    print("\n" + "="*60)
    print("TEST: напомни через 5 минут проверить почту")
    print("="*60)
    
    # Setup test user
    user_id = 123456789  
    session = Session()
    
    # Clean up old test data
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        # Don't delete user, just reuse
        session.commit()
    else:
        # Create fresh test user (will auto-create subscription via model)
        user = User(telegram_id=user_id, username="test_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()  # Get user.id before accessing subscription
        session.commit()
    
    # Test message
    message = "напомни через 5 минут проверить почту"
    
    print(f"\n📝 User message: {message}")
    print(f"⏰ Current time: {datetime.now(pytz.timezone('Europe/Moscow'))}")
    
    # Call AI
    response = await chat_with_ai(
        message=message,
        user_id=user_id,
        db_session=session,
        context=""
    )
    
    print(f"\n🤖 AI Response:\n{response}")
    
    # Check if task was created
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    if tasks:
        print(f"\n✅ Task created:")
        for task in tasks:
            print(f"   Title: {task.title}")
            print(f"   Reminder: {task.reminder_time}")
    else:
        print(f"\n❌ NO TASK CREATED!")
    
    # Cleanup
    session.query(Task).filter_by(user_id=user.id).delete()
    # Don't delete user to avoid subscription issues
    session.commit()
    session.close()
    
    print("\n" + "="*60)

if __name__ == "__main__":
    asyncio.run(test_simple_task())
