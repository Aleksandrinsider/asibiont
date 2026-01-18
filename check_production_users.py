#!/usr/bin/env python3
"""
Скрипт для проверки пользователей в боевой БД
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Subscription, SubscriptionTier
from dotenv import load_dotenv

load_dotenv()

# Получаем DATABASE_URL
db_url = os.getenv('DATABASE_URL')
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

engine = create_engine(db_url)
Session = sessionmaker(bind=engine)

print('=' * 60)
print('👥 ПРОВЕРКА ПОЛЬЗОВАТЕЛЕЙ В БОЕВОЙ БД')
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

    # Проверяем тестовых пользователей
    test_telegram_ids = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010]
    test_users = session.query(User).filter(User.telegram_id.in_(test_telegram_ids)).all()

    print(f'\n🎯 Тестовых пользователей: {len(test_users)}/10')

    if len(test_users) < 10:
        missing = set(test_telegram_ids) - set(u.telegram_id for u in test_users)
        print(f'  Отсутствуют: {sorted(missing)}')

    # Проверяем лишних пользователей
    all_telegram_ids = [u.telegram_id for u in users]
    extra_users = [tid for tid in all_telegram_ids if tid not in test_telegram_ids]

    if extra_users:
        print(f'\n⚠️  Лишние пользователи (не тестовые): {len(extra_users)}')
        for tid in sorted(extra_users):
            user = next(u for u in users if u.telegram_id == tid)
            print(f'  Telegram ID: {tid}, Username: {user.username}')
    else:
        print('\n✅ Лишних пользователей нет')

except Exception as e:
    print(f'\n❌ Ошибка: {e}')
finally:
    session.close()