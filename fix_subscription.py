"""
Скрипт для проверки и создания подписки пользователю
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Subscription, SubscriptionTier
from datetime import datetime, timedelta

def check_and_fix_subscription(telegram_id: int):
    session = Session()
    
    try:
        # Находим пользователя
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            print(f"❌ Пользователь с telegram_id={telegram_id} не найден")
            return
        
        print(f"✅ Найден пользователь: ID={user.id}, username={user.username}")
        
        # Проверяем подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        
        if not subscription:
            print("❌ Подписка не найдена. Создаю...")
            subscription = Subscription(
                user_id=user.id,
                tier=SubscriptionTier.PREMIUM,
                status='active',
                expires_at=datetime.utcnow() + timedelta(days=365),
                created_at=datetime.utcnow()
            )
            session.add(subscription)
            session.commit()
            print("✅ Подписка PREMIUM создана на 1 год")
        else:
            print(f"📋 Подписка найдена:")
            print(f"   - Тариф: {subscription.tier}")
            print(f"   - Статус: {subscription.status}")
            print(f"   - Истекает: {subscription.expires_at}")
            
            if subscription.status != 'active':
                print("⚠️ Подписка неактивна. Активирую...")
                subscription.status = 'active'
                subscription.expires_at = datetime.utcnow() + timedelta(days=365)
                session.commit()
                print("✅ Подписка активирована")
                
    finally:
        session.close()

if __name__ == '__main__':
    # Введите ваш telegram_id
    telegram_id = int(input("Введите telegram_id: "))
    check_and_fix_subscription(telegram_id)
