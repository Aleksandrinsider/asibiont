"""
Тест создания задач через AI
"""
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from ai_integration import chat_with_ai

async def test_task_creation():
    print("=== Тест создания задач через AI ===\n")

    # Тестовое сообщение
    message = "напомни купить молоко через 10 минут"
    user_id = 123456

    print(f"Отправка сообщения: '{message}'")
    print(f"User ID: {user_id}")

    try:
        response = await chat_with_ai(message, user_id=user_id)
        print(f"\nAI ответ: {response}")

        # Проверяем intent classification
        from improved_prompts_final import improved_classify_intent
        intent = improved_classify_intent(message)
        print(f"\nIntent classification: {intent}")

        # Проверяем, создалась ли задача в базе данных
        from models import Session, Task, User
        session = Session()
        try:
            # Находим пользователя по telegram_id
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                tasks = session.query(Task).filter_by(user_id=user.id).all()
                print(f"\nНайдено задач в БД для пользователя {user_id} (user.id={user.id}): {len(tasks)}")
                for task in tasks:
                    print(f"- ID: {task.id}, Title: '{task.title}', Reminder: {task.reminder_time}, Status: {task.status}")
            else:
                print(f"\nПользователь с telegram_id={user_id} не найден в БД")
        finally:
            session.close()

    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_task_creation())