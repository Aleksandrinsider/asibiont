
import os
import sys
sys.path.append('.')

from models import User, Subscription, PaymentHistory, engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

Session = sessionmaker(bind=engine)
session = Session()

print('=== Проверка подписки @sportfan3 ===')

# Найдем пользователя
user = session.query(User).filter(User.username == 'sportfan3').first()
if user:
    print(f'User ID: {user.id}')
    print(f'Username: {user.username}')
    print(f'Current subscription_tier: {user.subscription_tier}')
    
    # Проверим активные подписки
    subscriptions = session.query(Subscription).filter(
        Subscription.user_id == user.id,
        Subscription.active == True
    ).all()
    print(f'\nActive subscriptions: {len(subscriptions)}')
    for sub in subscriptions:
        print(f'  ID: {sub.id}, tier: {sub.tier}, active: {sub.active}')
        print(f'  Start: {sub.start_date}, End: {sub.end_date}')
    
    # Проверим payment_history
    payments = session.query(PaymentHistory).filter(
        PaymentHistory.user_id == user.id
    ).order_by(PaymentHistory.created_at.desc()).all()
    print(f'\nPayment history records: {len(payments)}')
    for payment in payments:
        print(f'  ID: {payment.id}, tier: {payment.tier}, action: {payment.action}')
        print(f'  Start: {payment.start_date}, End: {payment.end_date}')
        print(f'  Created: {payment.created_at}')
        
    # Проверим нужно ли восстановление
    now = datetime.now(timezone.utc)
    has_active_gold = any(
        p.tier == 'gold' and p.end_date and p.end_date > now 
        for p in payments if p.action in ['subscription_activated', 'subscription_upgraded']
    )
    
    if has_active_gold and user.subscription_tier != 'gold':
        print(f'\n НАЙДЕНА ПРОБЛЕМА: Пользователь должен иметь GOLD, но имеет {user.subscription_tier}')
        print('Запускаем восстановление...')
        
        # Восстанавливаем подписку
        user.subscription_tier = 'gold'
        session.commit()
        print(' Подписка восстановлена!')
    elif user.subscription_tier == 'gold':
        print('\n Подписка корректна')
    else:
        print(f'\nℹ  Пользователь имеет {user.subscription_tier} тариф')
        
else:
    print('Пользователь sportfan3 не найден')

session.close()
