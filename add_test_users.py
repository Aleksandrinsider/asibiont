#!/usr/bin/env python3
"""
Скрипт для добавления 10 тестовых пользователей с разными тарифами в боевую БД
"""
import os
import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Subscription, UserProfile, SubscriptionTier
from dotenv import load_dotenv

load_dotenv()

# Получаем DATABASE_URL
db_url = os.getenv('DATABASE_URL')
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

engine = create_engine(
    db_url,
    pool_size=1,
    max_overflow=0,
    pool_timeout=60,
    pool_recycle=3600,
    pool_pre_ping=True
)
Session = sessionmaker(bind=engine)

print('=' * 60)
print('👥 ДОБАВЛЕНИЕ ТЕСТОВЫХ ПОЛЬЗОВАТЕЛЕЙ В БОЕВУЮ БД')
print('=' * 60)

# Данные для 10 пользователей
test_users = [
    # BRONZE (4 пользователей)
    {"telegram_id": 1001, "username": "test_user_1", "first_name": "Тестовый Пользователь 1", "tier": SubscriptionTier.BRONZE},
    {"telegram_id": 1002, "username": "test_user_2", "first_name": "Тестовый Пользователь 2", "tier": SubscriptionTier.BRONZE},
    {"telegram_id": 1003, "username": "test_user_3", "first_name": "Тестовый Пользователь 3", "tier": SubscriptionTier.BRONZE},
    {"telegram_id": 1004, "username": "test_user_4", "first_name": "Тестовый Пользователь 4", "tier": SubscriptionTier.BRONZE},

    # SILVER (3 пользователя)
    {"telegram_id": 1005, "username": "test_user_5", "first_name": "Тестовый Пользователь 5", "tier": SubscriptionTier.SILVER},
    {"telegram_id": 1006, "username": "test_user_6", "first_name": "Тестовый Пользователь 6", "tier": SubscriptionTier.SILVER},
    {"telegram_id": 1007, "username": "test_user_7", "first_name": "Тестовый Пользователь 7", "tier": SubscriptionTier.SILVER},

    # GOLD (3 пользователя)
    {"telegram_id": 1008, "username": "test_user_8", "first_name": "Тестовый Пользователь 8", "tier": SubscriptionTier.GOLD},
    {"telegram_id": 1009, "username": "test_user_9", "first_name": "Тестовый Пользователь 9", "tier": SubscriptionTier.GOLD},
    {"telegram_id": 1010, "username": "test_user_10", "first_name": "Тестовый Пользователь 10", "tier": SubscriptionTier.GOLD},
]

session = Session()
try:
    print('\n🔄 Добавление пользователей...')
    added_count = 0

    for user_data in test_users:
        # Проверяем, существует ли пользователь
        existing_user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
        if existing_user:
            print(f'  ⚠️  Пользователь {user_data["telegram_id"]} уже существует, пропускаем')
            continue

        # Создаем пользователя
        user = User(
            telegram_id=user_data["telegram_id"],
            username=user_data["username"],
            first_name=user_data["first_name"],
            subscription_tier=user_data["tier"]
        )
        session.add(user)
        session.flush()  # Получаем ID пользователя

        # Создаем подписку
        subscription = Subscription(
            user_id=user.id,
            telegram_username=user_data["username"],
            status='active',
            plan='monthly',
            tier=user_data["tier"],
            start_date=datetime.datetime.now(datetime.timezone.utc),
            end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30),
            subscriber_number=user.id  # Используем user.id как subscriber_number
        )
        session.add(subscription)

        # Создаем профиль пользователя
        profile = UserProfile(
            user_id=user.id,
            bio=f"Тестовый пользователь с тарифом {user_data['tier'].value}",
            skills="Тестирование, Разработка",
            interests="Технологии, ИИ"
        )
        session.add(profile)

        added_count += 1
        print(f'  ✅ Добавлен пользователь {user_data["telegram_id"]} ({user_data["tier"].value})')

    session.commit()

    print('\n' + '=' * 60)
    print('✅ ДОБАВЛЕНИЕ ЗАВЕРШЕНО УСПЕШНО')
    print('=' * 60)
    print(f'\n📊 Статистика:')
    print(f'   - Добавлено пользователей: {added_count}')
    print(f'   - BRONZE: 4')
    print(f'   - SILVER: 3')
    print(f'   - GOLD: 3')

except Exception as e:
    print(f'\n❌ Ошибка: {e}')
    session.rollback()
finally:
    session.close()