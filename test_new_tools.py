import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, Task
from datetime import datetime, timedelta

def test_new_tools():
    # Create a test user and task
    session = SessionLocal()
    try:
        # Add a test task
        test_task = Task(
            user_id=1,  # Assuming user 1 exists
            title="Test Task",
            description="Test description",
            reminder_time=datetime.now() + timedelta(hours=1),
            priority="medium"
        )
        session.add(test_task)
        session.commit()
        task_id = test_task.id
        print(f"Created test task with ID: {task_id}")

        # Test get_task_details
        response = chat_with_ai(f"Расскажи подробнее о задаче {task_id}", context=[], user_id=1)
        print(f"AI Response for get_task_details: {response}")

        # Test edit_task
        response = chat_with_ai(f"Изменить название задачи {task_id} на 'Updated Test Task' и описание на 'Updated description'", context=[], user_id=1)
        print(f"AI Response for edit_task: {response}")

        # Test set_priority
        response = chat_with_ai(f"Установить высокий приоритет для задачи {task_id}", context=[], user_id=1)
        print(f"AI Response for set_priority: {response}")

        # Test delete_task
        response = chat_with_ai(f"Удалить задачу {task_id}", context=[], user_id=1)
        print(f"AI Response for delete_task: {response}")

        # Verify task was deleted
        task = session.query(Task).filter(Task.id == task_id).first()
        if task:
            print("Task still exists")
        else:
            print("Task successfully deleted")

    finally:
        session.close()

if __name__ == "__main__":
    test_new_tools()