from models import User, Task
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker
import os

# Подключаемся к базе данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Найти пользователя aleksandrinsider
user = session.query(User).filter_by(username='aleksandrinsider').first()

if user:
    print(f"\n=== Проверка для пользователя @{user.username} (ID: {user.id}) ===\n")
    
    # Проверяем делегированные задачи через delegated_to_username
    username_clean = user.username.replace('@', '') if user.username else ''
    delegated_tasks = session.query(Task).filter(
        or_(
            Task.delegated_to_username.ilike(username_clean),
            Task.delegated_to_username.ilike(f'@{username_clean}')
        ),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted'
    ).all()
    
    print(f"Найдено {len(delegated_tasks)} делегированных задач:")
    print("-" * 80)
    
    delegator_ids = set()
    for task in delegated_tasks:
        delegator = session.query(User).filter_by(id=task.user_id).first()
        delegator_name = delegator.username if delegator else "unknown"
        print(f"Task ID: {task.id:3} | Title: {task.title[:40]:40} | From: @{delegator_name:15} | Status: {task.delegation_status}")
        if task.user_id:
            delegator_ids.add(task.user_id)
    
    print(f"\n=== Уникальные делегаторы (должны быть в 'Делегирует мне') ===")
    print("-" * 80)
    
    for delegator_id in delegator_ids:
        delegator = session.query(User).filter_by(id=delegator_id).first()
        if delegator and delegator.id != user.id:
            task_count = len([t for t in delegated_tasks if t.user_id == delegator.id])
            print(f"User ID: {delegator.id:3} | Username: @{delegator.username:15} | Tasks: {task_count}")
else:
    print("Пользователь @aleksandrinsider не найден")

session.close()
