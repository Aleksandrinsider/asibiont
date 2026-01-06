"""Удаление дублирующихся задач"""
from models import Session, Task

session = Session()
try:
    # Удалить дубли - оставить только первые 4 задачи
    duplicates = session.query(Task).filter(Task.id.in_([130, 131, 132, 133])).all()
    
    print(f"Deleting {len(duplicates)} duplicate tasks:")
    for task in duplicates:
        print(f"  - ID {task.id}: {task.title}")
        session.delete(task)
    
    session.commit()
    print("\nOK Duplicates deleted")
    
    # Проверка
    remaining = session.query(Task).all()
    print(f"\nRemaining tasks: {len(remaining)}")
    for t in remaining:
        print(f"  {t.id}. {t.title}")
        
finally:
    session.close()
