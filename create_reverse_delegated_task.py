#!/usr/bin/env python3
"""
Скрипт для создания задачи, делегированной ОТ текущего пользователя К @sportfan1
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task

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
    delegator_user = session.query(User).filter_by(username='aleksandrinsider').first()
    if not delegator_user:
        print("❌ Пользователь @aleksandrinsider не найден")
        session.close()
        exit(1)

    print(f"Найден пользователь-делегатор: {delegator_user.username} (ID: {delegator_user.id})")

    # Создаем задачу, делегированную ОТ delegator_user К @sportfan1
    new_task = Task(
        user_id=delegator_user.id,
        title="Задача делегированная мной",
        description="Это тестовая задача, которую я делегировал @sportfan1",
        status="pending",
        delegated_to_username="sportfan1",
        delegation_status="pending"
    )

    session.add(new_task)
    session.commit()

    print("✅ Задача успешно создана!")
    print(f"   ID задачи: {new_task.id}")
    print(f"   От пользователя: {delegator_user.username}")
    print(f"   Делегирована на: @sportfan1")
    print(f"   Статус делегирования: pending")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    exit(1)