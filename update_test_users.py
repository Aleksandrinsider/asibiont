#!/usr/bin/env python3
"""
Script to update existing test users with different subscription tiers.
"""
import sys
import os
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

        updated_count = 0
        for telegram_id, tier in tier_mapping.items():
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                user.subscription_tier = tier
                print(f"Updated {user.username} (ID: {telegram_id}) to tier: {tier.value}")
                updated_count += 1
            else:
                print(f"User with telegram_id {telegram_id} not found")

        session.commit()
        print(f"Successfully updated {updated_count} test users with subscription tiers")
        session.close()

        # Verify the updates
        print("\nVerifying updates:")
        for telegram_id in tier_mapping.keys():
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                print(f"✓ {user.username}: {user.subscription_tier.value}")

    except Exception as e:
        print(f"Error updating users: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    update_test_users_tiers()