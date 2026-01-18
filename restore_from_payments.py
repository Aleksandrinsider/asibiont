import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, PaymentHistory, SubscriptionTier
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL
from datetime import datetime, timedelta
import pytz

print('Восстанавливаем тарифы на основе платежной истории...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Получаем всех пользователей
users = session.query(User).all()
print(f'Всего пользователей: {len(users)}')

fixed_count = 0
now = datetime.now(pytz.UTC)

for user in users:
    # Проверяем платежную историю на наличие активных GOLD подписок
    payments = session.query(PaymentHistory).filter(
        PaymentHistory.user_id == user.id,
        PaymentHistory.action.in_(['subscription_activated', 'subscription_upgraded'])
    ).all()

    has_active_gold = False
    latest_gold_payment = None

    for payment in payments:
        payment_tier = payment.tier
        if hasattr(payment_tier, 'value'):
            payment_tier = payment_tier.value
        payment_tier = str(payment_tier).upper()

        if payment_tier == 'GOLD':
            # Проверяем, активна ли подписка
            if payment.end_date:
                payment_end = payment.end_date
                if payment_end.tzinfo is None:
                    payment_end = payment_end.replace(tzinfo=pytz.UTC)

                if payment_end > now:
                    has_active_gold = True
                    if not latest_gold_payment or payment.end_date > latest_gold_payment.end_date:
                        latest_gold_payment = payment
                    break

    current_tier = user.subscription_tier.value if user.subscription_tier else None

    if has_active_gold and current_tier != 'GOLD':
        print(f'Пользователь {user.username}: тариф {current_tier} -> GOLD (на основе платежа)')

        # Обновляем тариф пользователя
        user.subscription_tier = SubscriptionTier.GOLD

        # Обновляем или создаем подписку
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub:
            sub.tier = SubscriptionTier.GOLD
            sub.status = 'active'
            if latest_gold_payment and latest_gold_payment.end_date:
                sub.end_date = latest_gold_payment.end_date
        else:
            # Создаем подписку на основе платежа
            new_sub = Subscription(
                user_id=user.id,
                telegram_username=user.username,
                tier=SubscriptionTier.GOLD,
                status='active',
                start_date=latest_gold_payment.start_date if latest_gold_payment else now,
                end_date=latest_gold_payment.end_date if latest_gold_payment else (now + timedelta(days=30))
            )
            session.add(new_sub)

        fixed_count += 1
    elif has_active_gold and current_tier == 'GOLD':
        print(f'Пользователь {user.username}: уже имеет GOLD (корректно)')
    else:
        print(f'Пользователь {user.username}: остается {current_tier} (нет активных GOLD платежей)')

# Сохраняем изменения
session.commit()
print(f'\nВосстановлено тарифов на основе платежей: {fixed_count}')

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