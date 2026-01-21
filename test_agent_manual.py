import asyncio
import os
import sys
from datetime import datetime, timezone

# Устанавливаем LOCAL=1 для тестирования
os.environ["LOCAL"] = "1"

print("Starting test script...")

# Импортируем модули
from models import init_db, Session, User, Task
from ai_integration import chat_with_ai, set_redis_client
from config import LOCAL, DATABASE_URL

print("Modules imported successfully")

async def test_agent_responses():
    """Тестируем ответы агента и работу с БД/Redis"""

    print("=== ИНИЦИАЛИЗАЦИЯ ТЕСТА ===")
    print(f"LOCAL: {LOCAL}")
    print(f"DATABASE_URL: {DATABASE_URL}")

    # Инициализируем БД
    init_db()
    print("База данных инициализирована")

    # Создаем тестового пользователя
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(telegram_id=123456789, username="testuser")
        session.add(test_user)
        session.commit()
        print("Создан тестовый пользователь")
    else:
        print("Тестовый пользователь уже существует")

    user_id = test_user.telegram_id
    session.close()

    # Тестовые сообщения
    test_messages = [
        "Привет! Как дела?",
        "Создай задачу: позвонить маме завтра в 10 утра",
        "Покажи мои задачи",
        "Заверши задачу позвонить маме",
    ]

    context = []

    for i, message in enumerate(test_messages, 1):
        print(f"\n=== ТЕСТ {i}: {message} ===")

        try:
            # Вызываем агента
            response = await chat_with_ai(message, context, user_id)

            print(f"Ответ агента: {response}")

            # Сохраняем в контекст
            context.append({"user": message, "agent": response})

            # Проверяем изменения в БД
            session = Session()
            tasks = session.query(Task).filter_by(user_id=test_user.id).all()
            print(f"Текущие задачи в БД: {len(tasks)}")
            for task in tasks:
                print(f"  - {task.title} (статус: {task.status}, время: {task.reminder_time})")

            # Проверяем профиль
            from models import UserProfile
            profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
            if profile:
                print(f"Профиль: город={profile.city}, компания={profile.company}")

            session.close()

            # Для сообщений, требующих уточнения, симулируем ИИ ответ
            if "Когда" in response or "время" in response.lower() or "уточни" in response.lower():
                print("Обнаружено требование уточнения. Симулируем ИИ ответ...")
                clarification = "завтра в 15:00"
                print(f"ИИ уточнение: {clarification}")

                # Повторный вызов с уточнением
                follow_up_response = await chat_with_ai(clarification, context, user_id)
                print(f"Ответ после уточнения: {follow_up_response}")
                context.append({"user": clarification, "agent": follow_up_response})

        except Exception as e:
            print(f"Ошибка в тесте {i}: {e}")
            import traceback
            traceback.print_exc()

    print("\n=== ТЕСТ ЗАВЕРШЕН ===")

if __name__ == "__main__":
    asyncio.run(test_agent_responses())