#!/usr/bin/env python3
"""
Script to add test delegation tasks for a specific user in Railway database.
Usage: python add_test_delegation_tasks.py
"""

import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Task, UserProfile
from config import DATABASE_URL

def add_test_delegation_tasks():
    """Add test delegation tasks for user with telegram_id 146333757"""

    # Connect to database
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Find the user
        user = session.query(User).filter_by(telegram_id=146333757).first()
        if not user:
            print(f"User with telegram_id 146333757 not found")
            return

        print(f"Found user: {user.username or user.first_name} (ID: {user.id})")

        # Find some other users to delegate to
        # Get a few random users (excluding the target user)
        other_users = session.query(User).filter(User.id != user.id).limit(5).all()

        if not other_users:
            print("No other users found to delegate to")
            return

        print(f"Found {len(other_users)} users to delegate to")

        # Create test delegation tasks
        test_tasks = [
            {
                "title": "Подготовить презентацию о AI технологиях",
                "description": "Создать слайды и материалы для презентации на конференции",
                "delegated_to_username": other_users[0].username or f"user_{other_users[0].id}",
                "reminder_time": datetime.now() + timedelta(hours=2)
            },
            {
                "title": "Провести анализ рынка для нового проекта",
                "description": "Исследовать конкурентов и подготовить отчет",
                "delegated_to_username": other_users[1].username or f"user_{other_users[1].id}",
                "reminder_time": datetime.now() + timedelta(days=1)
            },
            {
                "title": "Организовать встречу с командой разработки",
                "description": "Согласовать время и подготовить agenda",
                "delegated_to_username": other_users[2].username or f"user_{other_users[2].id}",
                "reminder_time": datetime.now() + timedelta(hours=4)
            },
            {
                "title": "Протестировать новую функциональность",
                "description": "Провести тестирование и подготовить баг-репорт",
                "delegated_to_username": other_users[3].username or f"user_{other_users[3].id}",
                "reminder_time": datetime.now() + timedelta(days=2)
            },
            {
                "title": "Подготовить документацию по API",
                "description": "Написать подробную документацию для разработчиков",
                "delegated_to_username": other_users[4].username or f"user_{other_users[4].id}",
                "reminder_time": datetime.now() + timedelta(hours=6)
            }
        ]

        # Add tasks to database
        for task_data in test_tasks:
            task = Task(
                user_id=user.id,
                title=task_data["title"],
                description=task_data["description"],
                delegated_to_username=task_data["delegated_to_username"],
                delegation_status="pending",
                reminder_time=task_data["reminder_time"],
                status="pending"
            )
            session.add(task)
            print(f"Added task: '{task.title}' delegated to {task.delegated_to_username}")

        # Commit changes
        session.commit()
        print(f"\nSuccessfully added {len(test_tasks)} test delegation tasks for user {user.telegram_id}")

    except Exception as e:
        print(f"Error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    add_test_delegation_tasks()