#!/usr/bin/env python3
"""
Скрипт для обновления тарифов пользователей в production БД
"""
import sys
import os

# Remove LOCAL variable to force production mode
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, SubscriptionTier

def update_test_users_tiers():
    """Update existing test users with different subscription tiers."""
    try:
        # Check DATABASE_URL
        db_url = os.getenv('DATABASE_URL', '')
        if 'sqlite' in db_url.lower() or not db_url:
            logger.error("❌ ОШИБКА: Используйте DATABASE_URL для подключения к production БД")
            sys.exit(1)
        
        logger.info(f"✅ Подключение к production БД (PostgreSQL)")
        logger.info(f"Host: {db_url.split('@')[1].split('/')[0] if '@' in db_url else 'unknown'}")
        print("="*60)
        
        session = Session()

        # Define tier mapping for test users
        tier_mapping = {
            111111: SubscriptionTier.BRONZE,  # sportfan1
            222222: SubscriptionTier.SILVER,  # sportfan2
            333333: SubscriptionTier.GOLD,    # sportfan3
            444444: SubscriptionTier.BRONZE,  # sportfan4
            555555: SubscriptionTier.SILVER,  # sportfan5
            146333757: SubscriptionTier.BRONZE,  # aleksandrinsider
        }
        
        updated_count = 0
        for telegram_id, tier in tier_mapping.items():
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                old_tier = user.subscription_tier.value if user.subscription_tier else 'NONE'
                user.subscription_tier = tier
                print(f"✅ Updated {user.username} (ID: {telegram_id}): {old_tier} → {tier.value}")
                updated_count += 1
            else:
                print(f"❌ User with telegram_id {telegram_id} not found")

        session.commit()
        print("="*60)
        print(f"✅ Successfully updated {updated_count} users with subscription tiers")
        
        # Verify the updates
        print("\n✅ Verifying updates:")
        print("-"*60)
        session_verify = Session()
        for telegram_id in tier_mapping.keys():
            user = session_verify.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                print(f"✓ {user.username}: {user.subscription_tier.value if user.subscription_tier else 'None'}")
        session_verify.close()
        session.close()

    except Exception as e:
        print(f"❌ Error updating users: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    print("WARNING: This script will update PRODUCTION database on Railway!")
    print("Make sure you want to proceed.")
    confirm = input("Type 'yes' to continue: ")
    if confirm.lower() == 'yes':
        update_test_users_tiers()
    else:
        print("Operation cancelled")
