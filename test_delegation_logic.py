#!/usr/bin/env python3
"""
Тест для проверки логики делегирования задач
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import User, Task, Base, engine, Session, Subscription, SubscriptionTier

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

def init_db():
    """Инициализация БД"""
    print("Создание таблиц...")
    Base.metadata.create_all(engine)
    print("Готово!")

async def test_delegation():
    """Тест делегирования задач"""
    print("🧪 Тестирование логики делегирования задач")

    # Инициализация БД
    init_db()

    # Создание тестового пользователя
    session = Session()
    try:
        # Проверяем, существует ли тестовый пользователь
        test_user = session.query(User).filter_by(telegram_id=999999).first()
        if not test_user:
            test_user = User(
                telegram_id=999999,
                username="test_user",
                timezone="Europe/Moscow"
            )
        # Создаем активную подписку для тестового пользователя (Silver для делегирования)
        subscription = session.query(Subscription).filter_by(user_id=test_user.id, status="active").first()
        if not subscription:
            subscription = Subscription(
                user_id=test_user.id,
                telegram_id=999999,
                tier=SubscriptionTier.SILVER,  # Silver позволяет делегирование
                status="active",
                end_date=None  # Бессрочная для теста
            )
            session.add(subscription)
            session.commit()
            print("✅ Создана активная подписка Silver для тестового пользователя")

        # Создание тестового контакта для делегирования
        delegated_user = session.query(User).filter_by(telegram_id=888888).first()
        if not delegated_user:
            delegated_user = User(
                telegram_id=888888,
                username="test_sport_10",
                timezone="Europe/Moscow"
            )
            session.add(delegated_user)
            session.commit()
            print("✅ Создан пользователь для делегирования")

        # Тест 1: Сообщение с @username в начале
        print("\n📝 Тест 1: '@test_sport_10 сделать отчет'")
        result1 = await chat_with_ai(
            message="@test_sport_10 сделать отчет",
            user_id=999999
        )
        print(f"Результат: {result1[:200]}...")

        # Проверяем, создалась ли делегированная задача
        session.commit()  # Обновляем сессию
        tasks = session.query(Task).filter_by(
            user_id=999999,
            delegated_to_username="test_sport_10"
        ).all()
        if tasks:
            print(f"✅ Найдена делегированная задача: {tasks[-1].title}")
        else:
            print("❌ Делегированная задача не найдена")

        # Тест 2: Обычное сообщение без делегирования
        print("\n📝 Тест 2: 'сделать отчет самому'")
        result2 = await chat_with_ai(
            message="сделать отчет самому",
            user_id=999999
        )
        print(f"Результат: {result2[:200]}...")

        # Проверяем, создалась ли обычная задача
        session.commit()  # Обновляем сессию
        personal_tasks = session.query(Task).filter_by(
            user_id=999999,
            delegated_to_username=None
        ).all()
        if personal_tasks:
            print(f"✅ Найдена личная задача: {personal_tasks[-1].title}")
        else:
            print("❌ Личная задача не найдена")

        print("\n🎉 Тестирование завершено!")

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_delegation())