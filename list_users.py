#!/usr/bin/env python3
"""
Скрипт для просмотра пользователей в базе данных
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User

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

    # Получаем всех пользователей
    users = session.query(User).all()

    print(f"Найдено {len(users)} пользователей:")
    for user in users:
        print(f"  ID: {user.id}, Telegram ID: {user.telegram_id}, Username: {user.username}, First Name: {user.first_name}")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.close()
    exit(1)