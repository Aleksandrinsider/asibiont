#!/usr/bin/env python3
"""
Проверка и исправление тарифов в production
"""
import os
import sys

# Ensure production mode
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

# Set DATABASE_URL
os.environ['DATABASE_URL'] = 'postgresql://postgres:hHmIDLimfDQMFAzkSZswCDKboRnZagYU@yamabiko.proxy.rlwy.net:12729/railway'

from models import Session, User, SubscriptionTier

def check_and_fix():
    session = Session()
    try:
        logger.info("=" * 60)
        logger.info("ПРОВЕРКА ТАРИФОВ В PRODUCTION БД")
        logger.info("=" * 60)
        
        # Все пользователи
        users = session.query(User).all()
        print(f"\nВсего пользователей: {len(users)}")
        
        print("\nТекущие тарифы:")
        for user in users:
            tier = user.subscription_tier.value if user.subscription_tier else 'NONE'
            logger.info(f"  {user.username} (ID: {user.telegram_id}): {tier}")
        
        # Исправляем тарифы
        logger.info("\n" + "=" * 60)
        logger.info("ИСПРАВЛЕНИЕ ТАРИФОВ")
        logger.info("=" * 60)
        
        updates = [
            (111111, SubscriptionTier.BRONZE, 'sportfan1'),
            (222222, SubscriptionTier.SILVER, 'sportfan2'),
            (333333, SubscriptionTier.GOLD, 'sportfan3'),
            (444444, SubscriptionTier.BRONZE, 'sportfan4'),
            (555555, SubscriptionTier.SILVER, 'sportfan5'),
            (146333757, SubscriptionTier.BRONZE, 'aleksandrinsider'),
        ]
        
        for telegram_id, tier, username in updates:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                old_tier = user.subscription_tier.value if user.subscription_tier else 'NONE'
                user.subscription_tier = tier
                logger.info(f"✅ {username}: {old_tier} → {tier.value}")
            else:
                logger.error(f"❌ {username} не найден")
        
        session.commit()
        logger.info("\n" + "=" * 60)
        logger.info("ПРОВЕРКА ПОСЛЕ ОБНОВЛЕНИЯ")
        logger.info("=" * 60)
        
        for telegram_id, tier, username in updates:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                current = user.subscription_tier.value if user.subscription_tier else 'NONE'
                status = "✅" if current == tier.value else "❌"
                logger.info(f"{status} {username}: {current} (ожидается {tier.value})")
        
        logger.info("\n" + "=" * 60)
        logger.info("ГОТОВО!")
        logger.info("=" * 60)
    finally:
        session.close()

if __name__ == '__main__':
    check_and_fix()
