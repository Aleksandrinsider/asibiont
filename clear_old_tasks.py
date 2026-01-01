"""Script to clear old test tasks from database"""
from models import Session, Task
from datetime import datetime
import pytz

def clear_old_tasks():
    session = Session()
    try:
        # Delete all tasks with dates before 2026
        cutoff_date = datetime(2026, 1, 1, tzinfo=pytz.UTC)
        old_tasks = session.query(Task).filter(Task.reminder_time < cutoff_date).all()
        
        print(f"Found {len(old_tasks)} old tasks to delete")
        for task in old_tasks:
            print(f"Deleting task: {task.title} - {task.reminder_time}")
            session.delete(task)
        
        session.commit()
        print("Old tasks cleared successfully!")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    clear_old_tasks()
