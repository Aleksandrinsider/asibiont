#!/usr/bin/env python3
"""
Скрипт для проверки и создания профиля тестового пользователя
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, UserProfile

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

    print(f"Найден тестовый пользователь: {test_user.username} (ID: {test_user.id})")

    # Проверяем профиль
    profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()

    if profile:
        print("✅ Профиль существует:")
        print(f"   Город: {profile.city}")
        print(f"   Компания: {profile.company}")
        print(f"   Интересы: {profile.interests}")
        print(f"   Навыки: {profile.skills}")
    else:
        print("❌ Профиль не найден, создаем...")
        profile = UserProfile(
            user_id=test_user.id,
            interests='спорт, фитнес, здоровый образ жизни',
            city='Москва',
            company='Фитнес-клуб',
            position='Тренер',
            contact_info=f"user{test_user.telegram_id}@test.com",
            average_rating=4.5,
            rating_count=10
        )
        session.add(profile)
        session.commit()
        print("✅ Профиль создан!")

    session.close()

except Exception as e:
    print(f"❌ Ошибка: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    exit(1)