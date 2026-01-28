import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task

TEST_USER_ID = 99888

async def test():
    # Cleanup
    s = Session()
    s.query(Task).filter_by(user_id=TEST_USER_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
    s.commit()
    
    # Create user
    u = User(telegram_id=TEST_USER_ID, username="test")
    s.add(u)
    s.commit()
    s.close()
    
    # Test 1
    print("\n1. Create task")
    r = await chat_with_ai("Напомни позвонить завтра в 10:00", user_id=TEST_USER_ID)
    print("OK - task created")
    
    # Test 2
    print("\n2. Complete by context")
    r = await chat_with_ai("Готово", user_id=TEST_USER_ID)
    tools = r.get('tool_calls', []) if isinstance(r, dict) else []
    if any('complete_task' in str(t) for t in tools):
        print("OK - complete_task called")
    else:
        print("FAIL - complete_task NOT called")
        print(f"Tools: {tools}")
    
    # Test 3
    print("\n3. Update profile")
    r = await chat_with_ai("Я из Москвы", user_id=TEST_USER_ID)
    tools = r.get('tool_calls', []) if isinstance(r, dict) else []
    if any('update_profile' in str(t) for t in tools):
        print("OK - update_profile called")
    else:
        print("FAIL - update_profile NOT called")
        print(f"Tools: {tools}")
    
    # Cleanup
    s = Session()
    s.query(Task).filter_by(user_id=TEST_USER_ID).delete()
    s.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
    s.commit()
    s.close()
    
    print("\nDone")

asyncio.run(test())
