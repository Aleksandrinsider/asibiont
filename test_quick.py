"""
Быстрый тест ключевых функций агента
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime, timedelta

TEST_USER_ID = 999888777

async def cleanup():
    session = Session()
    try:
        session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
        session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
        session.commit()
        print("[OK] Cleanup done")
    finally:
        session.close()

async def test_message(msg, expectation=None):
    print(f"\n>>> User: {msg}")
    response = await chat_with_ai(msg, user_id=TEST_USER_ID)
    result = response.get('response', str(response)) if isinstance(response, dict) else str(response)
    print(f"<<< Agent: {result[:150]}")
    
    if expectation and expectation.lower() in result.lower():
        print(f"[OK] Expected '{expectation}' found")
        return True
    elif expectation:
        print(f"[FAIL] Expected '{expectation}' NOT found")
        return False
    return None

async def main():
    await cleanup()
    
    session = Session()
    user = User(telegram_id=TEST_USER_ID, username="test_user")
    session.add(user)
    session.commit()
    session.close()
    
    print("\n=== TEST 1: Create task ===")
    await test_message("Напомни позвонить клиенту завтра в 10:00")
    
    print("\n=== TEST 2: Context 'готово' ===")
    await test_message("Готово", "завершен")
    
    print("\n=== TEST 3: Profile update ===")
    await test_message("Я из Москвы", "москва")
    
    print("\n=== TEST 4: List tasks ===")
    await test_message("Покажи задачи")
    
    print("\n=== TEST 5: Delete ===")
    await test_message("Удали позвонить", "удал")
    
    await cleanup()
    print("\n[OK] All tests done")

if __name__ == "__main__":
    asyncio.run(main())
