#!/usr/bin/env python3
"""
Тест для проверки синхронизации взаимодействий между TG и веб-интерфейсом
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import init_db, User, Interaction, Session
from config import DATABASE_URL
import logging

logging.basicConfig(level=logging.INFO)

async def test_interaction_sync():
    """Тест синхронизации взаимодействий"""
    print("=== ТЕСТ: Синхронизация взаимодействий ===")

    # Инициализация БД
    init_db()

    # Создаем тестового пользователя
    user_id = 123456789

    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username="test_user")
        session.add(user)
        session.commit()

    # Создаем тестовые взаимодействия
    interactions = [
        Interaction(user_id=user.id, message_type='user', content='Привет'),
        Interaction(user_id=user.id, message_type='ai', content='Привет! Чем могу помочь?'),
        Interaction(user_id=user.id, message_type='user', content='Создай задачу купить продукты'),
        Interaction(user_id=user.id, message_type='ai', content='На какое время поставить задачу "купить продукты"?'),
    ]

    for interaction in interactions:
        session.add(interaction)
    session.commit()

    # Проверяем, что взаимодействия сохранены
    saved_interactions = session.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at.asc()).all()

    print(f"Сохранено взаимодействий: {len(saved_interactions)}")
    for i, interaction in enumerate(saved_interactions):
        print(f"{i+1}. [{interaction.message_type}] {interaction.content}")

    # Проверяем фильтрацию по времени очистки истории
    user.history_cleared_at = None  # Нет очистки истории
    session.commit()

    filtered = [
        i for i in saved_interactions
        if i.created_at.replace(tzinfo=session.query(User).filter_by(id=user.id).first().timezone or 'UTC') > 0
    ]

    print(f"После фильтрации: {len(filtered)} взаимодействий")

    session.close()
    print("✅ Тест завершен")

if __name__ == "__main__":
    asyncio.run(test_interaction_sync())