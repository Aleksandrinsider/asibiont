#!/usr/bin/env python3
"""
Simple test for reschedule_task
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, UserProfile, Task, User
from datetime import datetime, timezone
import pytz

# Test user data
TEST_USER_ID = 22

async def test_reschedule():
    """Test reschedule_task function"""
    print("Testing reschedule_task...")

    # First create a task
    response1 = await chat_with_ai("создай задачу тестовая задача на 10:00", user_id=TEST_USER_ID)
    print(f"Create response: {response1[:100]}...")

    # Then try to reschedule
    response2 = await chat_with_ai("перенеси задачу тестовая задача на 11:00", user_id=TEST_USER_ID)
    print(f"Reschedule response: {response2[:100]}...")

    # Check if task was rescheduled
    session = Session()
    user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
    if user:
        tasks = session.query(Task).filter(Task.user_id == user.id, Task.status == "pending").all()
        for task in tasks:
            print(f"Task: {task.title} at {task.reminder_time}")
    session.close()

if __name__ == "__main__":
    asyncio.run(test_reschedule())