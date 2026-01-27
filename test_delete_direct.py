import sys
sys.path.append('.')
from models import Session, Task, User
from ai_integration.handlers import delete_task_sync

# Проверим, какие задачи есть в БД
session = Session()
user = session.query(User).first()
if user:
    tasks = session.query(Task).filter(Task.user_id == user.id).all()
    print(f'Найдено задач: {len(tasks)}')
    for task in tasks:
        print(f'  ID: {task.id}, Title: \"{task.title}\"')

    # Попробуем удалить задачу по названию
    if tasks:
        result = delete_task_sync(
            task_title='тестовая задача',
            user_id=user.telegram_id,
            session=session,
            confirmed=True
        )
        print(f'Результат удаления: {result}')

        # Проверим, что осталось
        tasks_after = session.query(Task).filter(Task.user_id == user.id).all()
        print(f'Задач после удаления: {len(tasks_after)}')
        for task in tasks_after:
            print(f'  ID: {task.id}, Title: \"{task.title}\"')

session.close()