#!/usr/bin/env python3
"""
Экстренное обновление тарифов в production после миграции
"""
import os
import sys

# Ensure production mode
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

# Set DATABASE_URL
os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

from models import Session, User, SubscriptionTier

def fix_tiers():
    session = Session()
    try:
        # Update tiers
        updates = [
            (111111, SubscriptionTier.BRONZE, 'sportfan1'),
            (222222, SubscriptionTier.SILVER, 'sportfan2'),
            (333333, SubscriptionTier.GOLD, 'sportfan3'),
            (444444, SubscriptionTier.BRONZE, 'sportfan4'),
            (555555, SubscriptionTier.SILVER, 'sportfan5'),
            (146333757, SubscriptionTier.BRONZE, 'aleksandrinsider'),
        ]
        
        print("Обновление тарифов в production БД...")
        for telegram_id, tier, username in updates:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                user.subscription_tier = tier
                print(f"✅ {username}: {tier.value}")
            else:
                print(f"❌ {username} not found")
        
        session.commit()
        print("\n✅ Все тарифы обновлены")
        
        # Verify
        print("\nПроверка:")
        for telegram_id, tier, username in updates:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                current = user.subscription_tier.value if user.subscription_tier else 'NONE'
                status = "✅" if current == tier.value else "❌"
                print(f"{status} {username}: {current}")
    finally:
        session.close()

if __name__ == '__main__':
    fix_tiers()
