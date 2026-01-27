#!/usr/bin/env python3
"""
Comprehensive test script for AI chat functionality and error detection
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from config import LOCAL

async def test_comprehensive_chat():
    """Test various AI functions to detect errors"""
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

        # Test various messages to trigger different functions
        test_messages = [
            "привет",
            "создай задачу: купить продукты в магазине",
            "покажи мои задачи",
            "отметь задачу 'купить продукты' как выполненную",
            "удали все задачи",
            "хочу найти партнеров по покеру",
            "обнови мой профиль: город Москва, компания Yandex, должность разработчик",
            "какие у меня навыки",
            "порекомендуй идеи для развития",
            "что trending сейчас"
        ]

        for i, message in enumerate(test_messages):
            print(f"\n--- Test {i+1}: {message} ---")

            try:
                # Call chat_with_ai
                response = await chat_with_ai(
                    message=message,
                    user_id=user.telegram_id,
                    db_session=session
                )

                print(f"AI Response: {response}")

            except Exception as e:
                print(f"ERROR in test {i+1}: {e}")
                import traceback
                traceback.print_exc()

        # Check final state
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\nFinal state - User has {len(tasks)} tasks:")
        for task in tasks:
            print(f"  - {task.title}: {task.status}")

    except Exception as e:
        print(f"Test setup error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_comprehensive_chat())