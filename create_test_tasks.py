#!/usr/bin/env python3
"""
Скрипт для создания дополнительных тестовых задач для проверки фильтров
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task
from datetime import datetime, timedelta

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

    # Ищем пользователя @aleksandrinsider
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    if not user:
        print("❌ Пользователь @aleksandrinsider не найден")
        session.close()
        exit(1)

    print(f"Найден пользователь: {user.username} (ID: {user.id})")

    # Создаем выполненную задачу
    completed_task = Task(
        user_id=user.id,
        title="Выполненная задача",
        description="Эта задача уже выполнена",
        status="completed"
    )
    session.add(completed_task)

    # Создаем задачу с отставанием (reminder_time в прошлом)
    overdue_task = Task(
        user_id=user.id,
        title="Задача с отставанием",
        description="Эта задача просрочена",
        status="pending",
        reminder_time=datetime.now() - timedelta(hours=2)  # 2 часа назад
    )
    session.add(overdue_task)

    # Создаем личную задачу (не делегированную)
    personal_task = Task(
        user_id=user.id,
        title="Личная задача",
        description="Это личная задача пользователя",
        status="pending"
    )
    session.add(personal_task)

    session.commit()

    print("✅ Тестовые задачи созданы!")
    print(f"   Выполненная задача: ID {completed_task.id}")
    print(f"   Задача с отставанием: ID {overdue_task.id}")
    print(f"   Личная задача: ID {personal_task.id}")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    exit(1)