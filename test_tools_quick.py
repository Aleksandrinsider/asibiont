#!/usr/bin/env python3
"""Quick tool test - основные инструменты"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, UserProfile, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

async def quick_test():
    """Quick test of key tools"""
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    
    session = SessionLocal()
    
    # Setup test user
    existing = session.query(User).filter_by(telegram_id=999999).first()
    if existing:
        session.query(Task).filter_by(user_id=existing.id).delete()
        session.query(UserProfile).filter_by(user_id=existing.id).delete()
        session.delete(existing)
    session.commit()
    
    user = User(telegram_id=999999, username="test_quick", first_name="Test")
    session.add(user)
    session.commit()
    
    profile = UserProfile(
        user_id=user.id,
        city="Moscow",
        interests="AI, startups",
        goals="launch MVP",
        skills="Python"
    )
    session.add(profile)
    session.commit()
    
    print("=" * 70)
    print("QUICK TOOL TEST")
    print("=" * 70)
    
    tests = [
        ("add_task", "создай задачу: встреча завтра в 10:00", "add_task"),
        ("list_tasks", "покажи мои задачи", "list_tasks"),
        ("complete_task", "завершил встречу", "complete_task"),
        ("find_partners", "найди партнеров", "find_partners"),
        ("update_profile", "обнови профиль: компания - TechCo", "update_profile"),
    ]
    
    results = []
    
    for name, message, expected_tool in tests:
        print(f"\n{'='*70}")
        print(f"TEST: {name}")
        print(f"Message: {message}")
        
        try:
            result = await asyncio.wait_for(
                chat_with_ai(message=message, user_id=999999),
                timeout=30.0
            )
            
            tool_calls = result.get('tool_calls', [])
            called = [tc['function']['name'] for tc in tool_calls] if tool_calls else []
            
            success = expected_tool in called
            status = "✅" if success else "❌"
            
            print(f"{status} Expected: {expected_tool}, Called: {', '.join(called) if called else 'none'}")
            results.append((name, success, called))
            
        except asyncio.TimeoutError:
            print(f"❌ TIMEOUT after 30s")
            results.append((name, False, ['TIMEOUT']))
        except Exception as e:
            print(f"❌ ERROR: {e}")
            results.append((name, False, [f'ERROR: {str(e)[:50]}']))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, success, _ in results if success)
    total = len(results)
    
    print(f"Passed: {passed}/{total} ({passed*100//total}%)")
    
    for name, success, called in results:
        status = "✅" if success else "❌"
        print(f"{status} {name}: {', '.join(called)}")
    
    # Cleanup
    session.query(Task).filter_by(user_id=user.id).delete()
    session.query(UserProfile).filter_by(user_id=user.id).delete()
    session.delete(user)
    session.commit()
    session.close()
    
    print("\n✅ Cleanup complete")

if __name__ == "__main__":
    asyncio.run(quick_test())
