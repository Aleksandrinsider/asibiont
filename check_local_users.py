#!/usr/bin/env python3
"""
Скрипт для проверки пользователей в локальной SQLite БД
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Subscription, SubscriptionTier

# Локальная SQLite база
db_url = "sqlite:///local.db"

engine = create_engine(db_url)
Session = sessionmaker(bind=engine)

print('=' * 60)
print('👥 ПРОВЕРКА ПОЛЬЗОВАТЕЛЕЙ В ЛОКАЛЬНОЙ БД')
print('=' * 60)

session = Session()
try:
    # Получаем всех пользователей
    users = session.query(User).all()

    print(f'\n📊 Найдено пользователей: {len(users)}')

    if users:
        print('\nСписок пользователей:')
        for user in users:
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            tier = subscription.tier.value if subscription else 'Нет подписки'
            status = subscription.status if subscription else 'Нет подписки'
            print(f'  ID: {user.id}, Telegram ID: {user.telegram_id}, Username: {user.username}, Tier: {tier}, Status: {status}')
    else:
        print('  Пользователей не найдено')

except Exception as e:
    print(f'\n❌ Ошибка: {e}')
finally:
    session.close()