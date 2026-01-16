#!/usr/bin/env python3
"""
Скрипт для проверки тарифов пользователей в базе данных
"""
import os
import sys
sys.path.append('.')

from models import Session, User, SubscriptionTier

def check_user_tiers():
    """Проверяет тарифы всех пользователей"""
    session = Session()
    try:
        users = session.query(User).all()
        print(f"Всего пользователей: {len(users)}")
        print()

        tier_counts = {}
        for user in users:
            tier = user.subscription_tier.value if user.subscription_tier else 'NONE'
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            print(f"{user.username}: {tier}")

        print()
        print("Распределение по тарифам:")
        for tier, count in tier_counts.items():
            print(f"  {tier}: {count} пользователей")

    finally:
        session.close()

if __name__ == "__main__":
    # Определяем тип БД
    if os.getenv('LOCAL') == '1':
        print("Проверка локальной БД (SQLite)")
    else:
        print("Проверка production БД (PostgreSQL)")

    check_user_tiers()