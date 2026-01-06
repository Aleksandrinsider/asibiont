"""Проверка задач пользователя в production БД"""
from models import Session, User, Task
from datetime import datetime
import pytz

USER_ID = 146333757

session = Session()
try:
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    if not user:
        print(f"User {USER_ID} not found in DB")
        exit(1)
    
    print(f"User: @{user.username} (id={user.id})")
    print()
    
    # Задачи созданные пользователем
    my_tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"Tasks created by user: {len(my_tasks)}")
    for task in my_tasks:
        print(f"  {task.id}. {task.title}")
        print(f"     Status: {task.status}")
        print(f"     Delegated to: {task.delegated_to_username or 'None'}")
        print(f"     Reminder: {task.reminder_time}")
        print()
    
    # Задачи делегированные пользователю
    delegated_to_me = session.query(Task).filter_by(delegated_to_username=f"@{user.username}").all()
    print(f"Tasks delegated TO user: {len(delegated_to_me)}")
    for task in delegated_to_me:
        creator = session.query(User).filter_by(id=task.user_id).first()
        print(f"  {task.id}. {task.title}")
        print(f"     From: @{creator.username if creator else 'Unknown'}")
        print(f"     Status: {task.status}")
        print()
    
    # Всего задач
    all_tasks = session.query(Task).all()
    print(f"Total tasks in DB: {len(all_tasks)}")
    
finally:
    session.close()
