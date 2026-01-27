import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
from models import Session, Task, User
import asyncio

async def minimal_test():
    """Минимальный тест для проверки понимания AI команд"""
    session = Session()
    user = session.query(User).first()

    if not user:
        print("❌ Пользователь не найден")
        return

    # Очистка
    old_tasks = session.query(Task).filter(Task.user_id == user.id).all()
    for task in old_tasks:
        session.delete(task)
    session.commit()

    print("Создаем тестовую задачу...")
    test_task = Task(
        user_id=user.id,
        title="тестовая задача для проверки",
        description="Тест",
        status="pending"
    )
    session.add(test_task)
    session.commit()

    print("Отправляем простую команду удаления...")
    result = await chat_with_ai(
        message="удали тестовую задачу для проверки",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"Ответ AI: {result[:100]}...")

    # Проверяем результат
    task_after = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title == "тестовая задача для проверки"
    ).first()

    if task_after:
        print("❌ ЗАДАЧА НЕ УДАЛЕНА - AI не вызвал delete_task")
    else:
        print("✅ ЗАДАЧА УДАЛЕНА - AI правильно вызвал delete_task")

    session.close()

asyncio.run(minimal_test())