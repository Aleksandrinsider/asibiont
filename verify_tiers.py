import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, SubscriptionTier
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL

print('Проверяем работу тарифов после исправлений...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Получаем всех пользователей
users = session.query(User).all()
print(f'Всего пользователей: {len(users)}')

# Проверяем синхронизацию тарифов
sync_issues = 0
for user in users:
    user_tier = user.subscription_tier.value if user.subscription_tier else None

    # Получаем активную подписку
    active_sub = session.query(Subscription).filter(
        Subscription.user_id == user.id,
        Subscription.status == 'active'
    ).first()

    if active_sub and active_sub.tier:
        sub_tier = active_sub.tier.value if hasattr(active_sub.tier, 'value') else str(active_sub.tier).upper()

        if user_tier != sub_tier:
            print(f'❌ Несинхронизирован: {user.username} - user_tier={user_tier}, sub_tier={sub_tier}')
            sync_issues += 1
        else:
            print(f'✅ Синхронизирован: {user.username} - tier={user_tier}')
    else:
        print(f'ℹ️  Нет активной подписки: {user.username} - user_tier={user_tier}')

print(f'\nПроблем с синхронизацией: {sync_issues}')

# Проверяем распределение тарифов
tier_counts = {'BRONZE': 0, 'SILVER': 0, 'GOLD': 0, 'None': 0}
for user in users:
    tier = user.subscription_tier.value if user.subscription_tier else 'None'
    tier_counts[tier] += 1

print('\nРаспределение по тарифам:')
for tier, count in tier_counts.items():
    print(f'  {tier}: {count} пользователей')

# Проверяем активные подписки
active_subs = session.query(Subscription).filter(Subscription.status == 'active').all()
print(f'\nАктивных подписок: {len(active_subs)}')

sub_tier_counts = {'BRONZE': 0, 'SILVER': 0, 'GOLD': 0, 'None': 0}
for sub in active_subs:
    tier = sub.tier.value if sub.tier else 'None'
    sub_tier_counts[tier] += 1

print('Распределение активных подписок по тарифам:')
for tier, count in sub_tier_counts.items():
    print(f'  {tier}: {count} подписок')

session.close()
print('\nПроверка завершена!')