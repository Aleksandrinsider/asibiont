#!/usr/bin/env python3
"""
Скрипт для обновления username тестового пользователя
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

    # Ищем тестового пользователя
    test_user = session.query(User).filter_by(telegram_id=1001).first()
    if not test_user:
        print("❌ Тестовый пользователь не найден")
        session.close()
        exit(1)

    print(f"Найден тестовый пользователь: ID={test_user.id}, username={test_user.username}")

    # Обновляем username
    test_user.username = "sportfan1"
    session.commit()

    print("✅ Username успешно обновлен!")
    print(f"   Пользователь: {test_user.username}")
    print(f"   Telegram ID: {test_user.telegram_id}")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    exit(1)