#!/usr/bin/env python3
"""Test proactive context generation"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from ai_integration.prompts import generate_proactive_context
from models import User, Task, UserProfile, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

async def test_proactivity():
    """Test proactive context"""
    
    # Setup
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    session = SessionLocal()
    
    # Clean existing test user
    existing = session.query(User).filter_by(telegram_id=777777).first()
    if existing:
        # Delete tasks first
        session.query(Task).filter_by(user_id=existing.id).delete()
        session.query(UserProfile).filter_by(user_id=existing.id).delete()
        session.delete(existing)
        session.commit()
    
    # Create test user with profile and tasks
    test_user = User(
        telegram_id=777777,
        username="proactive_test",
        first_name="Test",
        memory="IT entrepreneur, developing AI startup"
    )
    session.add(test_user)
    session.commit()
    
    # Create profile
    profile = UserProfile(
        user_id=test_user.id,
        city="Perm",
        interests="AI, startups, running",
        goals="attract investment, launch MVP",
        skills="Python, ML"
    )
    session.add(profile)
    session.commit()
    
    # Create tasks
    now = datetime.now()
    
    # Overdue task
    overdue_task = Task(
        user_id=test_user.id,
        title="Prepare investor deck",
        status="pending",
        reminder_time=now - timedelta(hours=2)
    )
    session.add(overdue_task)
    
    # Today task
    today_task = Task(
        user_id=test_user.id,
        title="Call the bank",
        status="pending",
        reminder_time=now + timedelta(hours=3)
    )
    session.add(today_task)
    
    # Tomorrow task
    tomorrow_task = Task(
        user_id=test_user.id,
        title="Morning run",
        status="pending",
        reminder_time=now + timedelta(days=1, hours=7)
    )
    session.add(tomorrow_task)
    
    session.commit()
    
    print("=" * 70)
    print("TESTING PROACTIVE CONTEXT GENERATION")
    print("=" * 70)
    
    # Test 1: Generate proactive context directly
    print("\n1. DIRECT PROACTIVE CONTEXT GENERATION:")
    proactive_ctx = generate_proactive_context(777777, session)
    print(proactive_ctx)
    print(f"\nLength: {len(proactive_ctx)} chars")
    
    # Test 2: Full chat with proactive context
    print("\n" + "=" * 70)
    print("2. CHAT WITH PROACTIVE CONTEXT:")
    print("=" * 70)
    
    result = await chat_with_ai(
        message="привет, как дела?",
        user_id=777777
    )
    
    print(f"\nResponse preview:\n{result['response'][:500]}...")
    print(f"\nFull response length: {len(result['response'])} chars")
    print(f"Tool calls: {len(result.get('tool_calls', []))}")
    
    # Check if proactive hints are in response
    has_time_hint = any(word in result['response'].lower() for word in ['утро', 'день', 'вечер'])
    has_tasks_hint = any(word in result['response'].lower() for word in ['просрочен', 'задач'])
    has_interests_hint = any(word in result['response'].lower() for word in ['интерес', 'ai', 'стартап'])
    
    print(f"\nProactive elements detected:")
    print(f"  - Time context: {'✅' if has_time_hint else '❌'}")
    print(f"  - Tasks mention: {'✅' if has_tasks_hint else '❌'}")
    print(f"  - Interests mention: {'✅' if has_interests_hint else '❌'}")
    
    # Cleanup
    session.query(Task).filter_by(user_id=test_user.id).delete()
    session.query(UserProfile).filter_by(user_id=test_user.id).delete()
    session.delete(test_user)
    session.commit()
    session.close()
    
    print("\n" + "=" * 70)
    print("SUMMARY:")
    if proactive_ctx and (has_time_hint or has_tasks_hint or has_interests_hint):
        print("✅ SUCCESS: Proactive context is working!")
    else:
        print("❌ FAILED: Proactive context not detected")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(test_proactivity())
