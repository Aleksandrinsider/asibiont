import asyncio
import sys
import os
sys.path.append('.')

from ai_integration.chat import chat_with_ai
from models import Session, User, init_db
from datetime import datetime
import pytz

async def test_ai_completion():
    """Тест доведения AI до полного выполнения команд без keyword matching"""

    # Инициализируем базу данных
    init_db()

    # Создадим тестового пользователя
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(telegram_id=123456789, username='test_user')
        session.add(test_user)
        session.commit()

    print("=== ТЕСТИРОВАНИЕ ДОВЕДЕНИЯ AI ДО ВЫПОЛНЕНИЯ ===\n")

    # Тест 1: Делегирование с уточнениями
    print("1. Тест делегирования с уточнениями:")
    conversation = [
        "передай задачу по проверке кода",
        "нужно проверить код нового модуля, срок - завтра к 12:00",
        "@test_user"
    ]

    for i, msg in enumerate(conversation, 1):
        print(f"   {i}. Пользователь: '{msg}'")
        try:
            result = await chat_with_ai(
                message=msg,
                user_id=test_user.telegram_id,
                db_session=session,
                message_type='user'
            )
            print(f"      AI: {result[:200]}..." if len(result) > 200 else f"      AI: {result}")
        except Exception as e:
            print(f"      Ошибка: {e}")
        print()

    # Тест 2: Создание задачи с уточнениями
    print("2. Тест создания задачи с уточнениями:")
    conversation2 = [
        "нужно подготовить презентацию",
        "презентация по проекту анализа данных, сделать к пятнице",
        "завтра в 10 утра напомни"
    ]

    for i, msg in enumerate(conversation2, 1):
        print(f"   {i}. Пользователь: '{msg}'")
        try:
            result = await chat_with_ai(
                message=msg,
                user_id=test_user.telegram_id,
                db_session=session,
                message_type='user'
            )
            print(f"      AI: {result[:200]}..." if len(result) > 200 else f"      AI: {result}")
        except Exception as e:
            print(f"      Ошибка: {e}")
        print()

    # Тест 3: Завершение задачи с уточнениями
    print("3. Тест завершения задачи с уточнениями:")
    # Сначала создадим задачу
    await chat_with_ai(message="создай задачу: написать отчет по продажам", user_id=test_user.telegram_id, db_session=session, message_type='user')

    conversation3 = [
        "я закончил отчет",
        "отчет по продажам за этот месяц"
    ]

    for i, msg in enumerate(conversation3, 1):
        print(f"   {i}. Пользователь: '{msg}'")
        try:
            result = await chat_with_ai(
                message=msg,
                user_id=test_user.telegram_id,
                db_session=session,
                message_type='user'
            )
            print(f"      AI: {result[:200]}..." if len(result) > 200 else f"      AI: {result}")
        except Exception as e:
            print(f"      Ошибка: {e}")
        print()

    print("Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_ai_completion())