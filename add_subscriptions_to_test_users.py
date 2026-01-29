"""Добавление подписок для тестовых пользователей"""

import os
os.environ['LOCAL'] = '0'  # Используем Railway БД

from models import User, Subscription, SubscriptionTier
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL
import datetime

# Подключение к Railway БД
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Telegram ID тестовых пользователей
test_telegram_ids = [111111001, 111111002, 111111003, 111111004, 111111005]

print("🚀 Добавление подписок для тестовых пользователей...\n")

for telegram_id in test_telegram_ids:
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    
    if not user:
        print(f"⚠️  Пользователь с telegram_id {telegram_id} не найден")
        continue
    
    # Проверяем, есть ли уже подписка
    existing_sub = session.query(Subscription).filter_by(user_id=user.id).first()
    
    if existing_sub:
        print(f"✅ @{user.username} - подписка уже существует (статус: {existing_sub.status})")
        continue
    
    # Создаем активную подписку
    subscription = Subscription(
        user_id=user.id,
        telegram_id=telegram_id,
        telegram_username=user.username,
        username=user.username,
        status='active',
        plan='monthly',
        tier=SubscriptionTier.STANDARD,
        start_date=datetime.datetime.now(datetime.timezone.utc),
        end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    )
    session.add(subscription)
    session.commit()
    
    print(f"✅ @{user.username} - добавлена подписка STANDARD (активна до {subscription.end_date.strftime('%d.%m.%Y')})")

print("\n✨ Готово!")

# Статистика
total_subs = session.query(Subscription).filter(Subscription.status == 'active').count()
print(f"\n📊 Всего активных подписок: {total_subs}")

session.close()
