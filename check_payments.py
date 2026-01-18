import os
os.environ['LOCAL'] = '0'
from models import User, Subscription, PaymentHistory
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from config import DATABASE_URL

print('Подключаемся к production БД...')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Проверим все платежи
all_payments = session.query(PaymentHistory).all()
print(f'Всего платежей в истории: {len(all_payments)}')

if all_payments:
    action_counts = {}
    for payment in all_payments:
        action = payment.action
        if action not in action_counts:
            action_counts[action] = 0
        action_counts[action] += 1

    print('Распределение платежей по действиям:')
    for action, count in action_counts.items():
        print(f'  {action}: {count}')

    # Посмотрим последние 5 платежей
    print('\nПоследние 5 платежей:')
    for payment in all_payments[-5:]:
        print(f'  ID: {payment.id}, User: {payment.telegram_username}, Action: {payment.action}, Tier: {payment.tier}, Created: {payment.created_at}')
else:
    print('Платежей в истории нет вообще')

# Проверим конкретных пользователей с GOLD подписками из кода
gold_users = ['sportfan3', 'aleksandrinsider']
for username in gold_users:
    user = session.query(User).filter_by(username=username).first()
    if user:
        print(f'\nПользователь {username}:')
        print(f'  Текущий тариф: {user.subscription_tier.value if user.subscription_tier else "None"}')

        # Проверим его подписки
        subs = session.query(Subscription).filter_by(user_id=user.id).all()
        print(f'  Всего подписок: {len(subs)}')
        for sub in subs:
            print(f'    Подписка ID {sub.id}: статус={sub.status}, тариф={sub.tier.value if sub.tier else "None"}, end_date={sub.end_date}')

        # Проверим платежи
        payments = session.query(PaymentHistory).filter_by(user_id=user.id).all()
        print(f'  Всего платежей: {len(payments)}')
        for payment in payments:
            print(f'    Платеж ID {payment.id}: action={payment.action}, tier={payment.tier}, end_date={payment.end_date}')

session.close()