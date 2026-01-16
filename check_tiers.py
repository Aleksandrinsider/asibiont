#!/usr/bin/env python3
"""
Скрипт для проверки тарифов пользователей в базе данных
"""
import os
import sys

# Remove LOCAL variable to force production mode
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

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
            print(f"{user.username} (ID: {user.telegram_id}): {tier}")

        print()
        print("Распределение по тарифам:")
        for tier, count in tier_counts.items():
            print(f"  {tier}: {count} пользователей")

    finally:
        session.close()

if __name__ == "__main__":
    # Определяем тип БД
    db_url = os.getenv('DATABASE_URL', '')
    if 'sqlite' in db_url.lower() or not db_url:
        print("❌ ОШИБКА: Используйте DATABASE_URL для подключения к production БД")
        sys.exit(1)
    else:
        print(f"✅ Проверка production БД (PostgreSQL)")
        print(f"Host: {db_url.split('@')[1].split('/')[0] if '@' in db_url else 'unknown'}")
        print("="*60)

    check_user_tiers()