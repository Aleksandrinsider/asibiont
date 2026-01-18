import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, PaymentHistory, SubscriptionTier
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL
from datetime import datetime
import pytz

print('Восстанавливаем тарифы пользователей на основе активных подписок...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Получаем всех пользователей
users = session.query(User).all()
print(f'Всего пользователей: {len(users)}')

fixed_count = 0
now = datetime.now(pytz.UTC)

for user in users:
    # Получаем активные подписки пользователя
    active_sub = session.query(Subscription).filter(
        Subscription.user_id == user.id,
        Subscription.status == 'active',
        Subscription.end_date > now
    ).first()

    if active_sub and active_sub.tier:
        current_tier = user.subscription_tier.value if user.subscription_tier else None
        sub_tier = active_sub.tier.value if hasattr(active_sub.tier, 'value') else str(active_sub.tier).upper()

        if current_tier != sub_tier:
            print(f'Пользователь {user.username}: тариф {current_tier} -> {sub_tier}')
            # Обновляем тариф пользователя
            if sub_tier == 'BRONZE':
                user.subscription_tier = SubscriptionTier.BRONZE
            elif sub_tier == 'SILVER':
                user.subscription_tier = SubscriptionTier.SILVER
            elif sub_tier == 'GOLD':
                user.subscription_tier = SubscriptionTier.GOLD

            fixed_count += 1
        else:
            print(f'Пользователь {user.username}: тариф уже корректный ({current_tier})')
    else:
        print(f'Пользователь {user.username}: нет активных подписок или подписка истекла')

# Сохраняем изменения
session.commit()
print(f'\nВосстановлено тарифов: {fixed_count}')

# Проверяем результат
users = session.query(User).all()
tier_counts = {'BRONZE': 0, 'SILVER': 0, 'GOLD': 0, 'None': 0}
for user in users:
    tier = user.subscription_tier.value if user.subscription_tier else 'None'
    tier_counts[tier] += 1

print('\nИтоговое распределение по тарифам:')
for tier, count in tier_counts.items():
    print(f'  {tier}: {count} пользователей')

session.close()
print('Готово!')