import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import Session, Task, User
import json
import asyncio

async def test_ai_delete():
    # Создадим тестовую задачу
    session = Session()
    user = session.query(User).first()
    if user:
        # Создадим задачу для теста
        test_task = Task(
            user_id=user.id,
            title="тестовая задача для удаления",
            description="Тестовая задача",
            status="pending"
        )
        session.add(test_task)
        session.commit()
        print(f"Создана тестовая задача: {test_task.title}")

        # Теперь протестируем AI команду удаления
        user_message = "удали тестовую задачу для удаления"

        print(f"\nОтправляем AI команду: '{user_message}'")

        # Перехватим tool calls
        try:
            result = await chat_with_ai(
                message=user_message,
                user_id=user.telegram_id,
                db_session=session
            )
            print(f"Ответ AI: {result}")
        except Exception as e:
            print(f"Ошибка: {e}")

        # Проверим, что осталось в БД
        tasks_after = session.query(Task).filter(Task.user_id == user.id).all()
        print(f"\nЗадач после AI команды: {len(tasks_after)}")
        for task in tasks_after:
            print(f"  ID: {task.id}, Title: '{task.title}'")

    session.close()

# Запустим тест
asyncio.run(test_ai_delete())