#!/usr/bin/env python3
"""
Тест для проверки активного сбора данных AI-агентом
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import init_db, Session
from datetime import datetime

async def test_data_collection():
    """Тестируем сбор данных агентом"""

    # Инициализируем БД
    init_db()

    # Создаем тестового пользователя с минимальным профилем
    session = Session()
    from models import User, UserProfile

    # Удаляем старого тестового пользователя если есть
    existing_user = session.query(User).filter_by(telegram_id=999000333999).first()
    if existing_user:
        existing_profile = session.query(UserProfile).filter_by(user_id=existing_user.id).first()
        if existing_profile:
            session.delete(existing_profile)
        session.delete(existing_user)
        session.commit()

    # Создаем нового пользователя с пустым профилем
    user = User(telegram_id=999000333999, username="test_user", timezone="Europe/Moscow")
    session.add(user)
    session.commit()

    # Создаем пустой профиль (только если его нет)
    existing_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not existing_profile:
        profile = UserProfile(
            user_id=user.id,
            city=None,
            company=None,
            position=None,
            skills=None,
            interests=None,
            goals=None
        )
        session.add(profile)
        session.commit()

    session.close()

    print("🧪 ТЕСТИРОВАНИЕ СБОРА ДАННЫХ AI-АГЕНТОМ")
    print("=" * 50)

    # Тест 1: Приветствие - должен спросить о профиле
    print("\n🔹 ТЕСТ 1: Приветствие с пустым профилем")
    result = await chat_with_ai("Привет!", user_id=999000333999)
    response = result.get('response', '')
    print(f"Ответ: {response[:200]}...")

    has_question = any(phrase in response.lower() for phrase in [
        'город', 'интересы', 'навыки', 'цели', 'компания', 'должность',
        'чем занимаешься', 'расскажи о себе', 'где живешь'
    ])
    print(f"✅ Задает вопросы: {'ДА' if has_question else 'НЕТ'}")

    # Тест 2: Общий разговор - должен спросить о целях
    print("\n🔹 ТЕСТ 2: Общий разговор")
    result = await chat_with_ai("Что ты умеешь?", user_id=999000333999)
    response = result.get('response', '')
    print(f"Ответ: {response[:200]}...")

    has_goals_question = any(phrase in response.lower() for phrase in [
        'цели', 'приоритеты', 'хочешь достичь', 'планируешь', 'работаешь над'
    ])
    print(f"✅ Спрашивает о целях: {'ДА' if has_goals_question else 'НЕТ'}")

    print("\n" + "=" * 50)
    print("🎯 РЕЗУЛЬТАТ: AI-агент активно собирает данные!" if (has_question or has_goals_question) else "⚠️  AI-агент не задает вопросы")

if __name__ == "__main__":
    asyncio.run(test_data_collection())