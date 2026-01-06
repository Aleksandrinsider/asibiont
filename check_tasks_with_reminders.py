from models import User, Task, SessionLocal

session = SessionLocal()

try:
    # Найти пользователя
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if user:
        print(f"Пользователь: {user.username} (ID: {user.telegram_id})")
        
        # Получить все задачи пользователя
        tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.id.desc()).limit(10).all()
        
        print(f"\nПоследние 10 задач:")
        for task in tasks:
            print(f"\nID: {task.id}")
            print(f"Название: {task.title}")
            print(f"Статус: {task.status}")
            print(f"reminder_time: {task.reminder_time}")
            print(f"reminder_time_original: {task.reminder_time_original if hasattr(task, 'reminder_time_original') else 'N/A'}")
            print(f"Создана: {task.created_at}")
    else:
        print("Пользователь не найден")
        
finally:
    session.close()
