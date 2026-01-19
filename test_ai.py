import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, Subscription, init_db
from datetime import datetime, timedelta
import pytz

async def test_ai_response():
    # Инициализируем базу данных
    init_db()
    
    # Создаем тестового пользователя с просроченной задачей
    session = Session()

    # Найдем существующего пользователя или создадим тестового
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username="testuser")
        session.add(user)
        session.commit()

    # Создадим подписку для тестового пользователя
    subscription = session.query(Subscription).filter_by(user_id=user.id, status="active").first()
    if not subscription:
        subscription = Subscription(
            user_id=user.id,
            telegram_id=user.telegram_id,
            telegram_username=user.username,
            status="active",
            tier="BRONZE",
            start_date=datetime.now(pytz.UTC),
            end_date=datetime.now(pytz.UTC) + timedelta(days=30)
        )
        session.add(subscription)
        session.commit()

    # Создадим просроченную задачу
    overdue_task = session.query(Task).filter_by(user_id=user.id, title="Позвонить маме").first()
    if not overdue_task:
        overdue_task = Task(
            user_id=user.id,
            title="Позвонить маме",
            description="Позвонить маме и поздравить с днем рождения",
            reminder_time=datetime.now(pytz.UTC) - timedelta(hours=5),  # Просрочена на 5 часов
            status="pending"
        )
        session.add(overdue_task)
        session.commit()

    # Создадим профиль пользователя
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            skills="программирование, дизайн",
            interests="спорт, чтение"
        )
        session.add(profile)
        session.commit()

    session.close()

    # Тестируем ответ на "привет"
    response = await chat_with_ai("привет", user_id=123456789)
    print("Тестовый ответ AI:")
    print(response)

    # Очистим тестовые данные
    session = Session()
    session.query(Task).filter_by(user_id=user.id, title="Позвонить маме").delete()
    session.query(Subscription).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_response())