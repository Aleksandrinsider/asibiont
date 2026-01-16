#!/usr/bin/env python3
"""
Script to update existing test users with different subscription tiers in PRODUCTION database.
Run this without LOCAL=1 environment variable to update Railway PostgreSQL database.
"""
import sys
import os

# Force production mode
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, SubscriptionTier

def update_test_users_tiers():
    """Update existing test users with different subscription tiers."""
    try:
        session = Session()

        # Define tier mapping for test users
        tier_mapping = {
            111111: SubscriptionTier.BRONZE,  # sportfan1
            222222: SubscriptionTier.SILVER,  # sportfan2
            333333: SubscriptionTier.GOLD,    # sportfan3
            444444: SubscriptionTier.BRONZE,  # sportfan4
            555555: SubscriptionTier.SILVER,  # sportfan5
        }

        print("Connecting to PRODUCTION database...")
        print("="*60)
        
        updated_count = 0
        for telegram_id, tier in tier_mapping.items():
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                user.subscription_tier = tier
                print(f"✓ Updated {user.username} (ID: {telegram_id}) to tier: {tier.value}")
                updated_count += 1
            else:
                print(f"✗ User with telegram_id {telegram_id} not found")

        session.commit()
        print("="*60)
        print(f"✅ Successfully updated {updated_count} test users with subscription tiers")
        
        # Verify the updates
        print("\nVerifying updates in PRODUCTION database:")
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
