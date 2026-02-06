#!/usr/bin/env python3
"""Quick test - проверка tool calling"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, Base
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

async def quick_test():
    """Быстрая проверка tool calling"""
    
    # Setup
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    session = SessionLocal()
    
    # Создаем тестового пользователя
    existing = session.query(User).filter_by(telegram_id=999999).first()
    if existing:
        session.delete(existing)
        session.commit()
    
    test_user = User(
        telegram_id=999999,
        username="quicktest",
        first_name="Test"
    )
    session.add(test_user)
    session.commit()
    print(f"User created: {test_user.telegram_id}")
    
    # Тест 1: Простая задача
    print("\n=== Test 1: Create task ===")
    result1 = await chat_with_ai(
        message="создай задачу: пробежка завтра в 19:00",
        user_id=999999
    )
    print(f"Response: {result1['response'][:100]}...")
    print(f"Tool calls: {len(result1.get('tool_calls', []))}")
    
    # Проверка БД
    tasks = session.query(Task).filter_by(user_id=test_user.id).all()
    print(f"Tasks in DB: {len(tasks)}")
    if tasks:
        print(f"Task title: {tasks[0].title}")
    
    # Тест 2: Список задач
    print("\n=== Test 2: List tasks ===")
    result2 = await chat_with_ai(
        message="какие у меня задачи?",
        user_id=999999
    )
    print(f"Response: {result2['response'][:100]}...")
    print(f"Tool calls: {len(result2.get('tool_calls', []))}")
    
    # Cleanup
    session.delete(test_user)
    session.commit()
    session.close()
    
    print("\n=== SUMMARY ===")
    print(f"Test 1 - Tool calls: {len(result1.get('tool_calls', []))}, Tasks created: {len(tasks)}")
    print(f"Test 2 - Tool calls: {len(result2.get('tool_calls', []))}")
    
    if len(result1.get('tool_calls', [])) > 0 and len(tasks) > 0:
        print("SUCCESS: Tool calling works!")
    else:
        print("FAILED: Tool calling broken")

if __name__ == "__main__":
    asyncio.run(quick_test())
