import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, init_db
import logging

logging.basicConfig(level=logging.INFO)

async def test_agent():
    """Тестируем новый подход с REQUIRED tool calls для всех действий"""

    # Инициализируем базу данных
    init_db()

    # Создаем тестовую сессию БД
    session = Session()

    # Создаем тестового пользователя
    test_user_id = 123456789
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(telegram_id=test_user_id, conversation_state='normal', timezone='Europe/Moscow')
        session.add(user)
        session.commit()

    test_cases = [
        # Создание задачи
        ("Создай задачу: купить молоко в 10:00", "Создание задачи"),
        ("Заверши задачу о молоке", "Завершение задачи"),
        ("Отредактируй задачу о молоке на: купить молоко и хлеб", "Редактирование задачи"),
        ("Удалить задачу о молоке", "Удаление задачи"),
        ("Делегируй задачу о хлебе пользователю @testuser", "Делегирование задачи"),
        ("Привет, как дела?", "Чистый разговор"),
        ("Обнови мой профиль: timezone Europe/London", "Обновление профиля"),
    ]

    for message, description in test_cases:
        print(f"\n=== ТЕСТИРУЕМ: {description} ===")
        print(f"Сообщение: {message}")

        try:
            response = await chat_with_ai(message, user_id=test_user_id, db_session=session)
            print(f"Ответ: {response[:200]}...")
        except Exception as e:
            print(f"Ошибка: {e}")

    # Закрываем сессию
    session.close()

if __name__ == "__main__":
    asyncio.run(test_agent())