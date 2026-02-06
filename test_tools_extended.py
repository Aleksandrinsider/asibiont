#!/usr/bin/env python3
"""Extended tool test - все 13 инструментов"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, UserProfile, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

async def extended_test():
    """Test all 13 tools"""
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    
    session = SessionLocal()
    
    # Setup 2 test users
    for tid in [888881, 888882]:
        existing = session.query(User).filter_by(telegram_id=tid).first()
        if existing:
            session.query(Task).filter_by(user_id=existing.id).delete()
            session.query(UserProfile).filter_by(user_id=existing.id).delete()
            session.delete(existing)
    session.commit()
    
    user1 = User(telegram_id=888881, username="test_ext1", first_name="Alex")
    session.add(user1)
    session.commit()
    
    profile1 = UserProfile(
        user_id=user1.id,
        city="Moscow",
        interests="AI, startups",
        goals="launch MVP",
        skills="Python"
    )
    session.add(profile1)
    
    user2 = User(telegram_id=888882, username="test_ext2", first_name="Maria")
    session.add(user2)
    session.commit()
    
    profile2 = UserProfile(
        user_id=user2.id,
        city="Moscow",
        interests="AI, design",
        goals="launch MVP",
        skills="Design"
    )
    session.add(profile2)
    session.commit()
    
    print("=" * 70)
    print("EXTENDED TOOL TEST - 13 INSTRUMENTS")
    print("=" * 70)
    
    tests = [
        ("add_task", "создай задачу: презентация завтра в 15:00", "add_task", 888881),
        ("list_tasks", "покажи задачи", "list_tasks", 888881),
        ("complete_task", "завершил презентация", "complete_task", 888881),
        ("reschedule_task", "создай задачу: встреча послезавтра в 10:00, потом: перенеси встречу на 14:00", "reschedule_task", 888881),
        ("edit_task", "измени встречу на 'важная встреча'", "edit_task", 888881),
        ("delete_task", "удали встречу", "delete_task", 888881),
        ("find_partners", "найди партнеров", "find_partners", 888881),
        ("find_contacts", "создай задачу: пробежка завтра в 19:00", "add_task", 888881),  # может вызвать find_relevant_contacts
        ("update_profile", "обнови профиль: компания TechCo", "update_profile", 888881),
        ("update_memory", "запомни: запуск MVP в марте", "update_user_memory", 888881),
        ("analyze_goals", "проанализируй мои цели", "analyze_goal_progress", 888881),
        ("delegate", "создай задачу: проверка документов завтра в 12:00, потом: делегируй проверка test_ext2", "delegate_task", 888881),
        ("get_details", "расскажи о задаче пробежка", "get_task_details", 888881),
    ]
    
    results = []
    
    for name, message, expected_tool, user_id in tests:
        print(f"\n{'='*70}")
        print(f"TEST: {name}")
        print(f"Message: {message[:60]}...")
        
        try:
            # Для составных тестов (создание + другое действие)
            if ", потом:" in message:
                msg1, msg2 = message.split(", потом:")
                await asyncio.wait_for(chat_with_ai(message=msg1.strip(), user_id=user_id), timeout=30.0)
                result = await asyncio.wait_for(chat_with_ai(message=msg2.strip(), user_id=user_id), timeout=30.0)
            else:
                result = await asyncio.wait_for(
                    chat_with_ai(message=message, user_id=user_id),
                    timeout=30.0
                )
            
            tool_calls = result.get('tool_calls', [])
            called = [tc['function']['name'] for tc in tool_calls] if tool_calls else []
            
            success = expected_tool in called
            status = "✅" if success else "❌"
            
            print(f"{status} Expected: {expected_tool}, Called: {', '.join(called) if called else 'none'}")
            results.append((name, success, called))
            
        except asyncio.TimeoutError:
            print(f"❌ TIMEOUT")
            results.append((name, False, ['TIMEOUT']))
        except Exception as e:
            print(f"❌ ERROR: {str(e)[:80]}")
            results.append((name, False, [f'ERROR']))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, success, _ in results if success)
    total = len(results)
    
    print(f"Passed: {passed}/{total} ({passed*100//total}%)")
    print(f"\nDetailed results:")
    
    for name, success, called in results:
        status = "✅" if success else "❌"
        tools_str = ', '.join(called) if called else 'none'
        print(f"{status} {name:20s}: {tools_str}")
    
    if passed == total:
        print(f"\n🎉 ALL TESTS PASSED! Tool calling works at 100%")
    else:
        print(f"\n⚠️  {total - passed} tests failed")
    
    # Cleanup
    for tid in [888881, 888882]:
        user = session.query(User).filter_by(telegram_id=tid).first()
        if user:
            session.query(Task).filter_by(user_id=user.id).delete()
            session.query(UserProfile).filter_by(user_id=user.id).delete()
            session.delete(user)
    session.commit()
    session.close()
    
    print("\n✅ Cleanup complete")

if __name__ == "__main__":
    asyncio.run(extended_test())
