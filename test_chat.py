#!/usr/bin/env python3
"""
Test script for AI chat functionality
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from config import LOCAL

async def test_chat():
    # Create test user
    session = Session()
    try:
        # Find or create test user
        user = session.query(User).filter_by(telegram_id=146333757).first()
        if not user:
            user = User(telegram_id=146333757, username='aleksandrinsider', conversation_state='normal', timezone='Europe/Moscow')
            session.add(user)
            session.commit()
            print(f"Created test user {user.telegram_id}")

        # Test multiple messages to check conversation history
        messages = [
            "привет",
            "да напомни проверить почту",
            "я только что проверил почту, все ок",
            "хочу заняться покером"
        ]
        
        for i, message in enumerate(messages):
            print(f"\n--- Message {i+1}: {message} ---")
            
            # Call chat_with_ai
            response = await chat_with_ai(
                message=message,
                user_id=user.telegram_id,
                db_session=session
            )

            print(f"AI Response: {response}")

        # Check tasks after
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\nUser has {len(tasks)} tasks:")
        for task in tasks:
            print(f"  - {task.title}: {task.reminder_time}")

    finally:
        session.close()

if __name__ == "__main__":
    if not LOCAL:
        print("This test script is for local testing only. Set LOCAL=1")
        sys.exit(1)
    
    asyncio.run(test_chat())