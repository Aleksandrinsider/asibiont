#!/usr/bin/env python3
"""
Тест сохранения взаимодействий из Telegram
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import init_db, Session, User, Interaction
from ai_integration.chat import chat_with_ai
import asyncio

async def test_telegram_interaction():
    """Тестирует сохранение взаимодействия как в Telegram боте"""
    print("🧪 Тестирование сохранения взаимодействий из Telegram")

    # Инициализация БД
    init_db()

    # Создаем тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(
            telegram_id=123456789,
            username="test_user",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()
        print("✅ Создан тестовый пользователь")

    user_id = 123456789
    message = "Привет! Создай задачу на завтра в 10 утра: проверить почту"

    print(f"📨 Отправляем сообщение: '{message}'")

    # Получаем ответ от AI
    context = []  # Пустой контекст для теста
    response = await chat_with_ai(message, context, user_id)

    print(f"🤖 Ответ AI: {response[:100]}...")

    # Сохраняем взаимодействие как в Telegram боте
    if user:
        interaction_user = Interaction(user_id=user.id, message_type='user', content=message)
        session.add(interaction_user)

        if response and response.strip():
            interaction_ai = Interaction(user_id=user.id, message_type='ai', content=response.strip())
            session.add(interaction_ai)

        session.commit()
        print("✅ Взаимодействия сохранены в базу данных")

        # Проверяем, что сохранено
        interactions = session.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at.desc()).limit(2).all()
        print(f"📊 Сохранено {len(interactions)} взаимодействий:")
        for i in interactions:
            print(f"  {i.message_type}: {i.content[:50]}...")

    session.close()

if __name__ == "__main__":
    asyncio.run(test_telegram_interaction())