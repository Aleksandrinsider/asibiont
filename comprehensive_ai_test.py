#!/usr/bin/env python3
"""
Comprehensive AI-first test for all command types.
Tests pure AI approach without any force_tool_calls triggers.
"""

import os
import sys
import asyncio
from datetime import datetime

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from models import Session, User, UserProfile, Task
from config import FREE_ACCESS_MODE

# Global variable to track initial task count
initial_task_count = 0

async def test_ai_commands():
    """Test all AI commands without force_tool_calls triggers"""

    print("[TEST] Starting comprehensive AI-first test...")

    # Create test user
    session = Session()
    try:
        # Clean up test users first
        session.query(User).filter(User.telegram_id.in_([999999, 999998])).delete()
        session.commit()
        print("[CLEANUP] Cleared test users")
        
        # Find or create test user
        test_user = session.query(User).filter_by(telegram_id=999999).first()
        if not test_user:
            test_user = User(
                telegram_id=999999,
                username="main_test_user",
                first_name="Test User",
                timezone="Europe/Moscow"
            )
            session.add(test_user)
            session.commit()

        # Create user profile
        profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=test_user.id,
                city="Москва",
                interests="программирование, спорт"
            )
            session.add(profile)
            session.commit()

        # Create another user for delegation (@test_user)
        delegated_user = session.query(User).filter_by(username="test_user").first()
        if not delegated_user:
            delegated_user = User(
                telegram_id=999998,
                username="test_user",
                first_name="Delegated User",
                timezone="Europe/Moscow"
            )
            session.add(delegated_user)
            session.commit()

        user_id = test_user.telegram_id
        print(f"[OK] Test user created: ID {user_id}")

        # Get initial task count
        global initial_task_count
        initial_task_count = get_task_count(user_id)
        print(f"[INFO] Initial task count: {initial_task_count}")

    finally:
        session.close()

    # Enable free access mode for testing
    os.environ['FREE_ACCESS_MODE'] = '1'

    test_cases = [
        # Profile updates
        {
            "name": "Profile Update - City and Interests",
            "message": "Живу в Санкт-Петербурге и увлекаюсь дизайном и фотографией",
            "expected_tools": ["update_profile"],
            "check_db": lambda: check_profile_updated(user_id, "Санкт-Петербург", "дизайном, фотографией")
        },

        # Task management
        {
            "name": "Add Task with Time",
            "message": "Напомни позвонить маме завтра в 15:00",
            "expected_tools": ["add_task"],
            "check_db": lambda response=None: check_response_contains_success(response or "", ["добавил", "добавлена", "напомнил", "создал"])
        },
        {
            "name": "Complete Task",
            "message": "Сделал позвонить маме",
            "expected_tools": ["complete_task"],
            "setup": lambda: create_task_for_test(user_id, "Позвонить маме", "2026-01-11 15:00"),
            "check_db": lambda: check_task_completed(user_id, "Позвонить маме")
        },

        # Delegation
        {
            "name": "Delegate Task",
            "message": "@test_user сделай отчет до завтра 10:00",
            "expected_tools": ["delegate_task"],
            "check_db": lambda: check_task_delegated(user_id, "@test_user", "сделай отчет")
        },

        # Find partners
        {
            "name": "Find Partners",
            "message": "Найди людей с похожими интересами",
            "expected_tools": ["find_partners"],
            "check_db": None
        },

        # Delete all tasks
        {
            "name": "Delete All Tasks",
            "message": "Удали все мои задачи",
            "expected_tools": ["delete_all_tasks"],
            "check_db": lambda: check_all_tasks_deleted(user_id)
        }
    ]

    results = []

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📋 Test {i}/{len(test_cases)}: {test_case['name']}")
        print(f"   Message: '{test_case['message']}'")

        # Clear all tasks before each test to ensure independence
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                session.query(Task).filter_by(user_id=user.id).delete()
                session.commit()
                print(f"   [CLEANUP] Cleared all tasks for user")
        finally:
            session.close()

        # Setup if needed
        if 'setup' in test_case and test_case['setup']:
            test_case['setup']()
            print(f"   [SETUP] Ran setup for test")

        try:
            # Call AI
            response = await chat_with_ai(
                message=test_case['message'],
                user_id=user_id
            )

            print(f"   Response: {response[:100]}...")

            # Check if expected tools were called (we'll need to check logs or response)
            # For now, just check if response indicates success
            success = len(response) > 10 and "ошибка" not in response.lower()

            if test_case['check_db']:
                import time
                time.sleep(0.5)  # Wait for DB to update
                db_check = test_case['check_db'](response) if 'response' in test_case['check_db'].__code__.co_varnames else test_case['check_db']()
                success = success and db_check

            results.append({
                "name": test_case['name'],
                "success": success,
                "response": response[:200]
            })

            print(f"   ✅ {'PASS' if success else 'FAIL'}")

        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            results.append({
                "name": test_case['name'],
                "success": False,
                "error": str(e)
            })

    # Summary
    print("\n🎯 Test Results Summary:")
    passed = sum(1 for r in results if r['success'])
    total = len(results)

    for result in results:
        status = "✅ PASS" if result['success'] else "❌ FAIL"
        print(f"   {status}: {result['name']}")
        if not result['success'] and 'error' in result:
            print(f"      Error: {result['error']}")

    print(f"\n🏆 Overall: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL TESTS PASSED! Pure AI-first approach works perfectly!")
    else:
        print("⚠️  Some tests failed. May need to adjust system prompt or investigate.")

    return passed == total

def check_profile_updated(user_id, expected_city, expected_interests):
    """Check if profile was updated correctly"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                city_match = expected_city.lower() in profile.city.lower() if profile.city else False
                interests_match = expected_interests.lower() in (profile.interests or "").lower()
                return city_match and interests_match
        return False
    finally:
        session.close()

def get_task_count(user_id):
    """Get current task count for user"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            return session.query(Task).filter_by(user_id=user.id).count()
        return 0
    finally:
        session.close()

def check_response_contains_success(response, keywords):
    """Check if response contains success keywords"""
    if not response:
        return False
    response_lower = response.lower()
    return any(keyword in response_lower for keyword in keywords)

def check_task_completed(user_id, expected_title):
    """Check if task was completed"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id, status='completed').all()
            print(f"      [DEBUG] Found {len(tasks)} completed tasks")
            for task in tasks:
                print(f"      [DEBUG] Task: '{task.title}' status={task.status}")
            result = any(expected_title.lower() in task.title.lower() for task in tasks)
            if not result:
                print(f"      [DEBUG] Expected '{expected_title}' not found in completed tasks")
            return result
        return False
    finally:
        session.close()

def check_task_delegated(user_id, expected_username, expected_title):
    """Check if task was delegated"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            print(f"      [DEBUG] Found {len(tasks)} total tasks for user")
            for task in tasks:
                print(f"      [DEBUG] Task: '{task.title}' delegated_to='{task.delegated_to_username}'")
            result = any(
                expected_username.replace('@', '') in (task.delegated_to_username or "") and
                expected_title.lower() in task.title.lower()
                for task in tasks
            )
            if not result:
                print(f"      [DEBUG] Expected delegation to '{expected_username}' with '{expected_title}' not found")
            return result
        return False
    finally:
        session.close()

def create_task_for_test(user_id, title, reminder_time):
    """Create a test task"""
    from ai_integration import add_task
    add_task(title=title, reminder_time=reminder_time, user_id=user_id)

def check_all_tasks_deleted(user_id):
    """Check if all tasks were deleted"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            return len(tasks) == 0
        return False
    finally:
        session.close()

if __name__ == "__main__":
    success = asyncio.run(test_ai_commands())
    sys.exit(0 if success else 1)