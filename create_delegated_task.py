#!/usr/bin/env python3
"""
Скрипт для создания делегированной задачи в базе данных Railway
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, UserProfile

# Загружаем переменные окружения
load_dotenv()

# Получаем DATABASE_URL (используем публичный для внешнего доступа)
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ DATABASE_PUBLIC_URL или DATABASE_URL не найдены в .env")
    exit(1)

print(f"Подключение к базе данных: {DATABASE_URL[:50]}...")

# Создаем подключение
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

try:
    session = Session()
    print("✅ Подключение к базе данных успешно")

    # Ищем тестового пользователя (отправителя)
    test_user = session.query(User).filter_by(telegram_id=1001).first()  # @sportfan1
    if not test_user:
        print("❌ Тестовый пользователь не найден")
        session.close()
        exit(1)

    print(f"Найден тестовый пользователь: {test_user.username} (ID: {test_user.id})")

    # Создаем задачу, делегированную на @aleksandrinsider
    new_task = Task(
        user_id=test_user.id,
        title="Тестовая делегированная задача",
        description="Это тестовая задача, делегированная от @sportfan1 на @aleksandrinsider",
        status="pending",
        delegated_to_username="aleksandrinsider",
        delegation_status="pending"
    )

    session.add(new_task)
    session.commit()

    print("✅ Задача успешно создана!")
    print(f"   ID задачи: {new_task.id}")
    print(f"   От пользователя: {test_user.username}")
    print(f"   Делегирована на: @aleksandrinsider")
    print(f"   Статус делегирования: pending")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    exit(1)