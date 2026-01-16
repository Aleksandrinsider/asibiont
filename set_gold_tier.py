import os
# Используем публичный URL для подключения извне
os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

from models import Session, User, Subscription, SubscriptionTier
from datetime import datetime, timedelta

session = Session()

# Найти пользователя @sportfan3
user = session.query(User).filter(User.username.ilike('sportfan3')).first()

if user:
    print(f'Найден пользователь: @{user.username}, ID: {user.id}')
    print(f'Текущий тариф: {user.subscription_tier}')
    
    # Обновить тариф на GOLD
    user.subscription_tier = SubscriptionTier.GOLD
    
    # Также обновить или создать подписку
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if subscription:
        subscription.tier = SubscriptionTier.GOLD
        subscription.status = 'active'
        subscription.end_date = datetime.now() + timedelta(days=365)
        print('Обновлена существующая подписка')
    else:
        subscription = Subscription(
            user_id=user.id,
            telegram_username=user.username,
            tier=SubscriptionTier.GOLD,
            status='active',
            plan='yearly',
            end_date=datetime.now() + timedelta(days=365)
        )
        session.add(subscription)
        print('Создана новая подписка')
    
    session.commit()
    print(f'\n✓ Тариф обновлен на GOLD для пользователя @{user.username}')
else:
    print('Пользователь @sportfan3 не найден в базе данных')

session.close()
