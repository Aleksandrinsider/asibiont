import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, PaymentHistory, SubscriptionTier
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL
from datetime import datetime, timedelta
import pytz

print('Проверяем, кто должен иметь GOLD подписки...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Список пользователей, которые должны иметь GOLD
gold_users = [
    'sportfan3',  # Из кода check_sportfan3_handler
    'aleksandrinsider',  # Основной разработчик
]

fixed_count = 0

for username in gold_users:
    user = session.query(User).filter_by(username=username).first()
    if user:
        current_tier = user.subscription_tier.value if user.subscription_tier else None
        if current_tier != 'GOLD':
            print(f'Пользователь {username}: тариф {current_tier} -> GOLD')

            # Обновляем тариф
            user.subscription_tier = SubscriptionTier.GOLD

            # Также обновим подписку, если она есть
            sub = session.query(Subscription).filter_by(user_id=user.id).first()
            if sub:
                sub.tier = SubscriptionTier.GOLD
                sub.status = 'active'
                # Установим end_date на год вперед, если не установлено
                now = datetime.now(pytz.UTC)
                if not sub.end_date or (sub.end_date.replace(tzinfo=pytz.UTC) if sub.end_date.tzinfo is None else sub.end_date) < now:
                    sub.end_date = now + timedelta(days=365)
                print(f'  Обновлена подписка: статус=active, тариф=GOLD, end_date={sub.end_date}')
            else:
                # Создадим подписку
                new_sub = Subscription(
                    user_id=user.id,
                    telegram_username=user.username,
                    tier=SubscriptionTier.GOLD,
                    status='active',
                    start_date=datetime.now(pytz.UTC),
                    end_date=datetime.now(pytz.UTC) + timedelta(days=365)
                )
                session.add(new_sub)
                print(f'  Создана новая подписка GOLD')

            fixed_count += 1
        else:
            print(f'Пользователь {username}: уже имеет GOLD')
    else:
        print(f'Пользователь {username}: не найден')

# Сохраняем изменения
session.commit()
print(f'\nОбновлено пользователей: {fixed_count}')

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