#!/usr/bin/env python3
"""
Тест функциональности напоминаний
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Загружаем переменные окружения
load_dotenv()

# Импортируем необходимые модули
from models import Session, User, Task
from ai_integration.handlers import add_task
from ai_integration.chat import chat_with_ai

async def test_reminder_creation():
    """Тестируем создание напоминания"""
    print("🧪 Тестируем создание напоминания...")

    # Создаем сессию базы данных
    session = Session()

    try:
        # Находим тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        if not user:
            print("❌ Тестовый пользователь не найден")
            return

        print(f"👤 Найден пользователь: {user.username} (ID: {user.telegram_id})")

        # Очищаем существующие задачи для чистоты теста
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Тестируем создание задачи с напоминанием
        test_message = "напомни проверить почту через 5 минут"
        print(f"📝 Тестовое сообщение: '{test_message}'")

        # Вызываем функцию создания задачи
        result = add_task(
            title="проверить почту",
            description="",
            reminder_time="через 5 минут",
            user_id=user.telegram_id,
            session=session
        )

        print(f"✅ Результат add_task: {result}")

        # Проверяем, что задача создана
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"📋 Задачи пользователя после создания: {len(tasks)}")

        for task in tasks:
            print(f"  - Задача '{task.title}' (ID: {task.id})")
            if task.reminder_time:
                print(f"    Напоминание: {task.reminder_time}")
            else:
                print("    Напоминание: не установлено")

        # Тестируем AI чат (опционально, если есть API ключ)
        print("\n🤖 Проверяем наличие API ключа...")
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if api_key:
            print("✅ API ключ найден, тестируем AI чат...")
            try:
                ai_response = await chat_with_ai(
                    message=test_message,
                    user_id=user.telegram_id,
                    context=[]
                )

                print(f"💬 AI ответ: '{ai_response}'")

                if ai_response and len(ai_response.strip()) > 0:
                    print("✅ AI вернул ответ")
                else:
                    print("❌ AI вернул пустой ответ")

            except Exception as e:
                print(f"❌ Ошибка в AI чате: {e}")
        else:
            print("⚠️ API ключ не найден, пропускаем тест AI чата")

    finally:
        session.close()

async def main():
    """Главная функция теста"""
    print("🚀 Запуск тестов напоминаний...")
    await test_reminder_creation()
    print("🏁 Тесты завершены")

if __name__ == "__main__":
    asyncio.run(main())