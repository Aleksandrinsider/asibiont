#!/usr/bin/env python3
import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from models import Session, User, Task, UserProfile, Interaction
from ai_integration.handlers import complete_task
from datetime import datetime
import pytz

async def test_complete_task():
    # Test complete_task function
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=1001).first()
        if not user:
            print("Test user not found")
            return

        print(f"Testing with user: {user.username} (telegram_id: {user.telegram_id})")

        # Create a test task
        task = Task(
            user_id=user.id,
            title="Test Task",
            description="Test Description",
            status="pending"
        )
        session_db.add(task)
        session_db.commit()

        print(f"Created test task: {task.id}")

        # Test complete_task function
        try:
            result = await complete_task(task_id=task.id, user_id=user.telegram_id, session=session_db)
            print(f"complete_task result: {result}")
        except Exception as e:
            print(f"complete_task failed: {e}")
            import traceback
            traceback.print_exc()

        # Check if task was completed
        updated_task = session_db.query(Task).filter_by(id=task.id).first()
        print(f"Task status after completion: {updated_task.status}")

    finally:
        session_db.close()

async def test_database_queries():
    # Test database queries directly
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=1001).first()
        if not user:
            print("Test user not found")
            return

        print(f"Testing database queries for user: {user.username}")

        # Test tasks query
        from sqlalchemy import or_, and_
        tasks = session_db.query(Task).filter(
            or_(Task.user_id == user.id, 
                and_(Task.delegated_to_username.ilike(user.username), 
                     Task.delegation_status == "accepted"))
        ).all()
        print(f"Found {len(tasks)} tasks")

        # Test profile query
        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"Profile exists: {profile is not None}")

        # Test interactions query
        interactions = session_db.query(Interaction).filter_by(user_id=user.id).all()
        print(f"Found {len(interactions)} interactions")

    except Exception as e:
        print(f"Database query failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session_db.close()

if __name__ == "__main__":
    print("Testing complete_task function...")
    asyncio.run(test_complete_task())
    
    print("\nTesting database queries...")
    asyncio.run(test_database_queries())