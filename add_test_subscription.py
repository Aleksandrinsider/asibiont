#!/usr/bin/env python3
"""
Скрипт для добавления тестовой подписки пользователю
"""
import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Subscription, User
from dotenv import load_dotenv

load_dotenv()

# Читаем DATABASE_URL из .env
def get_database_url_from_env():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('DATABASE_URL='):
                    return line.split('=', 1)[1].strip()
    return None

DATABASE_URL = get_database_url_from_env()
if not DATABASE_URL:
    print("❌ DATABASE_URL не найден в .env файле")
    sys.exit(1)

print(f"🔗 Подключаемся к БД: {DATABASE_URL[:50]}...")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # Найти пользователя aleksandrinsider
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if not user:
        print("❌ Пользователь aleksandrinsider не найден")
        sys.exit(1)
    
    print(f"✅ Пользователь найден: {user.username} (ID: {user.id}, telegram_id: {user.telegram_id})")
    
    # Проверить существующую подписку
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    
    if subscription:
        print(f"📋 Существующая подписка: status={subscription.status}, tier={subscription.tier}, end_date={subscription.end_date}")
        
        # Обновить подписку
        subscription.status = 'active'
        subscription.tier = 'SILVER'
        subscription.end_date = datetime.now() + timedelta(days=30)
        session.commit()
        print(f"✅ Подписка обновлена до SILVER до {subscription.end_date}")
    else:
        # Создать новую подписку
        subscription = Subscription(
            user_id=user.id,
            tier='SILVER',
            status='active',
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
            payment_id='test_payment_123',
            amount=9000.0,
            currency='RUB'
        )
        session.add(subscription)
        session.commit()
        print(f"✅ Создана новая подписка SILVER до {subscription.end_date}")
    
    print("\n🎉 Готово! Теперь вы можете войти в дашборд")

except Exception as e:
    print(f"❌ Ошибка: {e}")
    session.rollback()
finally:
    session.close()
