import os
# Используем публичный URL для подключения извне
os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

from models import Session, User, Subscription, SubscriptionTier

session = Session()

# Получаем пользователей с тарифом GOLD
users_gold = session.query(User).filter(User.subscription_tier == SubscriptionTier.GOLD).all()

print('\n=== Пользователи со статусом GOLD ===\n')

if users_gold:
    for u in users_gold:
        print(f'ID: {u.id}')
        print(f'Username: @{u.username or "нет"}')
        print(f'Имя: {u.first_name or "нет"}')
        print(f'Telegram ID: {u.telegram_id}')
        print('-' * 40)
else:
    print('Нет пользователей с тарифом GOLD')

print(f'\nВсего пользователей GOLD: {len(users_gold)}')

# Также проверим таблицу subscriptions
subs_gold = session.query(Subscription).filter(Subscription.tier == SubscriptionTier.GOLD).all()
print(f'\nВсего подписок GOLD в таблице subscriptions: {len(subs_gold)}')

if subs_gold:
    print('\n=== Подписки GOLD ===\n')
    for sub in subs_gold:
        user = session.query(User).filter_by(id=sub.user_id).first()
        print(f'User ID: {sub.user_id}')
        print(f'Username: @{user.username if user else "нет"}')
        print(f'Status: {sub.status}')
        print(f'End date: {sub.end_date}')
        print('-' * 40)

session.close()
