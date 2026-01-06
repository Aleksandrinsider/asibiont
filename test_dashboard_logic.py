"""Прямая проверка API логики без HTTP сервера"""
from models import Session, User, Task
from sqlalchemy import or_
import pytz
from datetime import datetime

USER_ID = 146333757

print("Checking API logic for user", USER_ID)
print("="*50)

session = Session()
try:
    # Найти пользователя
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    if not user:
        print(f"ERROR: User {USER_ID} not found")
        exit(1)
    
    print(f"User: @{user.username} (DB id={user.id})")
    print()
    
    # Логика из api_tasks_handler в main.py
    tasks = session.query(Task).filter(
        or_(
            Task.user_id == user.id,
            Task.delegated_to_username == user.username
        )
    ).all()
    
    print(f"Tasks returned by API query: {len(tasks)}")
    print()
    
    user_tz = pytz.timezone(user.timezone if user.timezone else "Europe/Moscow")
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    
    tasks_data = []
    for task in tasks:
        # Format task title based on delegation
        title = task.title
        if task.delegated_to_username:
            # Remove leading @ if present
            delegated_username = task.delegated_to_username.lstrip('@')
            
            # Check if task is delegated TO me or BY me
            if task.delegated_to_username.lower() == user.username.lower() or task.delegated_to_username.lower() == f"@{user.username.lower()}":
                # Task delegated TO me
                creator = session.query(User).filter_by(id=task.user_id).first()
                if creator:
                    title = f"{task.title} (delegirovana ot @{creator.username})"
            elif task.user_id == user.id:
                # Task delegated BY me to someone else
                title = f"{task.title} (delegirovana dlia @{delegated_username})"
        
        task_data = {
            'id': task.id,
            'title': title,
            'status': task.status,
            'is_delegated': task.delegated_to_username is not None,
            'delegated_to_username': task.delegated_to_username,
        }
        
        if task.reminder_time:
            reminder_utc = task.reminder_time.replace(tzinfo=pytz.UTC) if task.reminder_time.tzinfo is None else task.reminder_time
            reminder_local = reminder_utc.astimezone(user_tz)
            task_data['reminder_time'] = reminder_local.strftime('%Y-%m-%d %H:%M')
            
            # Check overdue
            task_data['overdue'] = reminder_utc < base_now and task.status != 'completed'
        else:
            task_data['reminder_time'] = None
            task_data['overdue'] = False
        
        tasks_data.append(task_data)
    
    print("Formatted tasks for dashboard:")
    print()
    for t in tasks_data:
        print(f"ID: {t['id']}")
        print(f"  Title: {t['title']}")
        print(f"  Status: {t['status']}")
        print(f"  Is delegated: {t['is_delegated']}")
        print(f"  Delegated to: {t['delegated_to_username']}")
        print(f"  Reminder: {t['reminder_time']}")
        print(f"  Overdue: {t['overdue']}")
        print()
    
    # Проверка фильтров
    print("Filter checks:")
    print(f"  All tasks: {len([t for t in tasks_data if t['status'] != 'completed'])}")
    print(f"  My tasks (not delegated): {len([t for t in tasks_data if t['status'] != 'completed' and not t['is_delegated']])}")
    print(f"  Assigned to me: {len([t for t in tasks_data if t['status'] != 'completed' and t['is_delegated'] and 'ot @' in t['title']])}")
    print(f"  Assigned by me: {len([t for t in tasks_data if t['status'] != 'completed' and t['is_delegated'] and 'dlia @' in t['title']])}")
    print(f"  Completed: {len([t for t in tasks_data if t['status'] == 'completed'])}")
    
finally:
    session.close()
