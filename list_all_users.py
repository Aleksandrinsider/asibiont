import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

from models import Session, User

session = Session()

users = session.query(User).all()

print(f'\n=== Все пользователи в базе ({len(users)}) ===\n')

for u in users:
    print(f'ID: {u.id}')
    print(f'Username: @{u.username or "нет"}')
    print(f'Имя: {u.first_name or "нет"}')
    print(f'Telegram ID: {u.telegram_id}')
    print(f'Тариф: {u.subscription_tier}')
    print('-' * 40)

session.close()
