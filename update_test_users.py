import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, SubscriptionTier
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL
from datetime import datetime, timedelta

print('Обновляем тарифы тестовых пользователей...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Маппинг telegram_id -> tier для тестовых пользователей
test_users_mapping = {
    1001: 'BRONZE',
    1002: 'SILVER',
    1003: 'GOLD',
    1004: 'BRONZE',
    1005: 'SILVER'
}

updated_users = 0
updated_subs = 0

for telegram_id, tier in test_users_mapping.items():
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        # Обновляем тариф пользователя
        current_tier = user.subscription_tier.value if user.subscription_tier else None
        if current_tier != tier:
            if tier == 'BRONZE':
                user.subscription_tier = SubscriptionTier.BRONZE
            elif tier == 'SILVER':
                user.subscription_tier = SubscriptionTier.SILVER
            elif tier == 'GOLD':
                user.subscription_tier = SubscriptionTier.GOLD

            print(f'Обновлен пользователь {telegram_id}: {current_tier} -> {tier}')
            updated_users += 1

        # Обновляем подписку
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub:
            sub_tier = sub.tier.value if hasattr(sub.tier, 'value') else str(sub.tier).upper()
            if sub_tier != tier:
                sub.tier = tier
                sub.status = 'active'
                # Продлеваем подписку на месяц
                now = datetime.now()
                if not sub.end_date or sub.end_date < now:
                    sub.end_date = now + timedelta(days=30)

                print(f'Обновлена подписка пользователя {telegram_id}: {sub_tier} -> {tier}')
                updated_subs += 1
        else:
            # Создаем подписку если не существует
            now = datetime.now()
            new_sub = Subscription(
                user_id=user.id,
                telegram_id=user.telegram_id,
                telegram_username=user.username,
                status='active',
                tier=tier,
                start_date=now,
                end_date=now + timedelta(days=30)
            )
            session.add(new_sub)
            print(f'Создана подписка для пользователя {telegram_id}: {tier}')
            updated_subs += 1

# Сохраняем изменения
session.commit()

print(f'\nОбновлено пользователей: {updated_users}')
print(f'Обновлено подписок: {updated_subs}')

# Проверяем результат
print('\nПроверяем тестовых пользователей:')
for telegram_id, expected_tier in test_users_mapping.items():
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        actual_tier = user.subscription_tier.value if user.subscription_tier else None
        status = '✅' if actual_tier == expected_tier else '❌'
        print(f'{status} Пользователь {telegram_id}: {actual_tier} (ожидалось {expected_tier})')

session.close()
print('Готово!')