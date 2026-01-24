#!/usr/bin/env python3
"""
Интерактивный тест диалога с AI агентом для выявления ошибок в реальном времени
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import init_db, User, Task, Session
from config import DATABASE_URL
import logging

logging.basicConfig(level=logging.INFO)

async def interactive_dialog_test():
    """Интерактивный тест диалога с AI"""
    print("🚀 ИНТЕРАКТИВНЫЙ ТЕСТ ДИАЛОГА С AI АГЕНТОМ")
    print("=" * 60)
    print("Это тест для проверки работы AI в реальном времени.")
    print("Вы можете отправлять сообщения и видеть ответы AI.")
    print("Для выхода введите 'exit' или 'quit'.")
    print("=" * 60)

    # Инициализация БД
    print("📊 Инициализация базы данных...")
    init_db()

    # Создаем тестового пользователя
    user_id = 123456789
    session = Session()

    # Очищаем старые данные для чистого теста
    session.query(Task).filter_by(user_id=user_id).delete()
    session.commit()

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username="test_user")
        session.add(user)
        session.commit()
        print("👤 Создан тестовый пользователь")

    session.close()

    print("✅ Система готова к диалогу")
    print()

    # История сообщений для контекста
    message_history = []

    while True:
        try:
            # Получаем ввод пользователя
            user_input = input("👤 Вы: ").strip()

            if user_input.lower() in ['exit', 'quit', 'выход']:
                print("👋 Тест завершен!")
                break

            if not user_input:
                continue

            print("🤖 AI думает...")

            # Отправляем сообщение AI
            response = await chat_with_ai(user_input, context=message_history, user_id=user_id)

            # Сохраняем в истории
            message_history.append({"role": "user", "content": user_input})
            message_history.append({"role": "assistant", "content": response})

            # Ограничиваем историю последними 10 сообщениями
            if len(message_history) > 20:
                message_history = message_history[-20:]

            print(f"🤖 AI: {response}")
            print("-" * 60)

        except KeyboardInterrupt:
            print("\n👋 Тест прерван пользователем!")
            break
        except Exception as e:
            print(f"❌ Ошибка в тесте: {e}")
            print("Попробуйте еще раз или введите 'exit' для выхода.")
            continue

if __name__ == "__main__":
    try:
        asyncio.run(interactive_dialog_test())
    except KeyboardInterrupt:
        print("\n👋 Тест завершен!")