"""Создание тестовых задач для пользователя"""
import asyncio
from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

USER_ID = 146333757

async def create_test_tasks():
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER_ID).first()
        if not user:
            print(f"User {USER_ID} not found")
            return
        
        print(f"Creating tasks for @{user.username}...")
        
        # Задача 1: Обычная задача с напоминанием на завтра
        task1 = Task(
            title="Позвонить клиенту Иванову",
            description="Обсудить условия контракта",
            user_id=user.id,
            status="pending",
            reminder_time=datetime.now(pytz.UTC) + timedelta(days=1),
            created_at=datetime.now(pytz.UTC)
        )
        session.add(task1)
        
        # Задача 2: Обычная задача с напоминанием через 2 часа
        task2 = Task(
            title="Подготовить презентацию",
            description="Слайды для встречи с инвесторами",
            user_id=user.id,
            status="pending",
            reminder_time=datetime.now(pytz.UTC) + timedelta(hours=2),
            created_at=datetime.now(pytz.UTC)
        )
        session.add(task2)
        
        # Задача 3: Делегированная задача (создана мной для testuser)
        task3 = Task(
            title="Проверить документы",
            description="Проверить все документы на корректность",
            user_id=user.id,
            delegated_to_username="@testuser",
            delegation_status="pending",
            status="pending",
            reminder_time=datetime.now(pytz.UTC) + timedelta(days=2),
            created_at=datetime.now(pytz.UTC)
        )
        session.add(task3)
        
        # Задача 4: Завершенная задача
        task4 = Task(
            title="Отправить отчет",
            description="Месячный отчет по продажам",
            user_id=user.id,
            status="completed",
            created_at=datetime.now(pytz.UTC) - timedelta(days=1)
        )
        session.add(task4)
        
        session.commit()
        
        print("OK Created 4 test tasks:")
        print(f"  1. {task1.title} (reminder in 1 day)")
        print(f"  2. {task2.title} (reminder in 2 hours)")
        print(f"  3. {task3.title} -> @testuser (delegated)")
        print(f"  4. {task4.title} (completed)")
        
        # Проверка
        all_tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\nTotal tasks for user: {len(all_tasks)}")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(create_test_tasks())
