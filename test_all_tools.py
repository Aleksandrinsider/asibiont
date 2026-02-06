#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprehensive test - все инструменты агента"""

import asyncio
import sys
import os

# Set UTF-8 encoding for console output on Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, UserProfile, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

class ComprehensiveToolTest:
    def __init__(self):
        self.engine = create_engine(DATABASE_URL)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.results = {}
        
    def setup(self):
        """Setup test users"""
        Base.metadata.create_all(bind=self.engine)
        session = self.SessionLocal()
        
        # Clean up
        for tid in [111111, 222222]:
            existing = session.query(User).filter_by(telegram_id=tid).first()
            if existing:
                session.query(Task).filter_by(user_id=existing.id).delete()
                session.query(UserProfile).filter_by(user_id=existing.id).delete()
                session.delete(existing)
        session.commit()
        
        # User 1
        user1 = User(telegram_id=111111, username="test_user1", first_name="Alex")
        session.add(user1)
        session.commit()
        
        profile1 = UserProfile(
            user_id=user1.id,
            city="Moscow",
            interests="AI, startups",
            goals="launch MVP",
            skills="Python, ML"
        )
        session.add(profile1)
        
        # User 2 (for delegation)
        user2 = User(telegram_id=222222, username="test_user2", first_name="Maria")
        session.add(user2)
        session.commit()
        
        profile2 = UserProfile(
            user_id=user2.id,
            city="Moscow",
            interests="AI, design",
            goals="launch MVP",
            skills="Design, UX"
        )
        session.add(profile2)
        
        session.commit()
        session.close()
        print("✅ Setup complete: 2 users created")
    
    async def test_tool(self, name, message, user_id=111111, expected_tool=None, check_db=None):
        """Test single tool"""
        print(f"\n{'='*70}")
        print(f"TEST: {name}")
        print(f"{'='*70}")
        print(f"Message: {message}")
        
        session = self.SessionLocal()
        
        # Get state before
        before_tasks = session.query(Task).filter_by(user_id=session.query(User).filter_by(telegram_id=user_id).first().id).count() if user_id else 0
        
        result = await chat_with_ai(message=message, user_id=user_id)
        
        # Get state after
        after_tasks = session.query(Task).filter_by(user_id=session.query(User).filter_by(telegram_id=user_id).first().id).count() if user_id else 0
        
        tool_calls = result.get('tool_calls', [])
        called_tools = [tc['function']['name'] for tc in tool_calls] if tool_calls else []
        
        print(f"\nResponse preview: {result['response'][:150]}...")
        print(f"Tool calls: {len(tool_calls)}")
        if called_tools:
            print(f"Called: {', '.join(called_tools)}")
        
        # Check expectations
        success = True
        if expected_tool:
            if expected_tool in called_tools:
                print(f"✅ Expected tool '{expected_tool}' was called")
            else:
                print(f"❌ Expected tool '{expected_tool}' NOT called")
                success = False
        
        if check_db:
            db_change = after_tasks - before_tasks
            if check_db == 'task_created' and db_change > 0:
                print(f"✅ Task created in DB (count: {before_tasks} → {after_tasks})")
            elif check_db == 'task_created' and db_change == 0:
                print(f"❌ Task NOT created in DB (count stayed: {after_tasks})")
                success = False
        
        session.close()
        
        self.results[name] = {
            'success': success and len(tool_calls) > 0,
            'tool_calls': len(tool_calls),
            'called_tools': called_tools,
            'expected': expected_tool
        }
        
        return result
    
    async def run_all_tests(self):
        """Run comprehensive test suite"""
        print("\n" + "="*70)
        print("COMPREHENSIVE TOOL TEST - ALL INSTRUMENTS")
        print("="*70)
        
        # 1. ADD_TASK
        await self.test_tool(
            "1. add_task",
            "создай задачу: подготовить презентацию завтра в 15:00",
            expected_tool='add_task',
            check_db='task_created'
        )
        
        # 2. LIST_TASKS
        await self.test_tool(
            "2. list_tasks",
            "покажи мои задачи",
            expected_tool='list_tasks'
        )
        
        # 3. COMPLETE_TASK
        await self.test_tool(
            "3. complete_task",
            "я завершил задачу подготовить презентацию",
            expected_tool='complete_task'
        )
        
        # 4. RESCHEDULE_TASK
        # Create task first
        await chat_with_ai("создай задачу: встреча завтра в 10:00", user_id=111111)
        await self.test_tool(
            "4. reschedule_task",
            "перенеси задачу встреча на послезавтра в 14:00",
            expected_tool='reschedule_task'
        )
        
        # 5. EDIT_TASK
        await self.test_tool(
            "5. edit_task",
            "измени название задачи встреча на 'важная встреча с инвестором'",
            expected_tool='edit_task'
        )
        
        # 6. DELETE_TASK
        await self.test_tool(
            "6. delete_task",
            "удали задачу встреча",
            expected_tool='delete_task'
        )
        
        # 7. FIND_PARTNERS
        await self.test_tool(
            "7. find_partners",
            "найди партнеров по интересам",
            expected_tool='find_partners'
        )
        
        # 8. FIND_RELEVANT_CONTACTS_FOR_TASK
        await self.test_tool(
            "8. find_relevant_contacts_for_task",
            "создай задачу: пойти на пробежку завтра в 19:00",
            # Может вызвать и add_task и find_relevant_contacts
        )
        
        # 9. UPDATE_PROFILE
        await self.test_tool(
            "9. update_profile",
            "обнови мой профиль: компания - Tech Corp, должность - CEO",
            expected_tool='update_profile'
        )
        
        # 10. UPDATE_USER_MEMORY
        await self.test_tool(
            "10. update_user_memory",
            "запомни: я планирую запустить стартап в марте 2026",
            expected_tool='update_user_memory'
        )
        
        # 11. ANALYZE_GOAL_PROGRESS
        await self.test_tool(
            "11. analyze_goal_progress",
            "проанализируй прогресс моих целей",
            expected_tool='analyze_goal_progress'
        )
        
        # 12. DELEGATE_TASK
        # Create task first
        await chat_with_ai("создай задачу: проверить документы завтра в 14:00", user_id=111111)
        await self.test_tool(
            "12. delegate_task",
            "делегируй задачу проверить документы пользователю test_user2",
            expected_tool='delegate_task'
        )
        
        # 13. GET_TASK_DETAILS
        await self.test_tool(
            "13. get_task_details",
            "расскажи подробнее о задаче дизайн лендинга",
            expected_tool='get_task_details'
        )
        
    def cleanup(self):
        """Cleanup test data"""
        session = self.SessionLocal()
        for tid in [111111, 222222]:
            existing = session.query(User).filter_by(telegram_id=tid).first()
            if existing:
                session.query(Task).filter_by(user_id=existing.id).delete()
                session.query(UserProfile).filter_by(user_id=existing.id).delete()
                session.delete(existing)
        session.commit()
        session.close()
        print("\n✅ Cleanup complete")
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        
        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r['success'])
        
        print(f"\nTotal tests: {total}")
        print(f"Passed: {passed} ({passed*100//total}%)")
        print(f"Failed: {total - passed}")
        
        print("\nDetailed results:")
        for name, result in self.results.items():
            status = "✅" if result['success'] else "❌"
            calls = result['tool_calls']
            tools = ', '.join(result['called_tools']) if result['called_tools'] else 'none'
            expected = result['expected'] or 'any'
            print(f"{status} {name}: {calls} tool calls ({tools}) [expected: {expected}]")
        
        if passed < total:
            print(f"\n⚠️  WARNING: {total - passed} tests failed!")
            print("Tool calling may not be working at 100%")
        else:
            print("\n🎉 ALL TESTS PASSED! Tool calling works at 100%")

async def main():
    tester = ComprehensiveToolTest()
    
    try:
        tester.setup()
        await tester.run_all_tests()
        tester.print_summary()
    finally:
        tester.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
