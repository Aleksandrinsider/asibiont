import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import Session, Task, User
import json
import asyncio

async def test_improved_delete():
    # Создадим тестовую задачу
    session = Session()
    user = session.query(User).first()
    if user:
        # Очистим старые тестовые задачи
        test_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.like('%тестовая%')
        ).all()
        for task in test_tasks:
            session.delete(task)
        session.commit()

        # Создадим новую уникальную тестовую задачу
        test_task = Task(
            user_id=user.id,
            title="специальная тестовая задача 2026",
            description="Тестовая задача для проверки улучшенных prompts",
            status="pending"
        )
        session.add(test_task)
        session.commit()
        print(f"Создана тестовая задача: '{test_task.title}'")

        # Теперь протестируем AI команду удаления
        user_message = "удали специальную тестовую задачу 2026"

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

        # Проверим, была ли удалена нужная задача
        deleted_task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title == "специальная тестовая задача 2026"
        ).first()

        if deleted_task:
            print("❌ ОШИБКА: Задача не была удалена!")
        else:
            print("✅ УСПЕХ: Задача была правильно удалена!")

    session.close()

# Запустим тест
asyncio.run(test_improved_delete())