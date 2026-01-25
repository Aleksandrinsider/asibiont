#!/usr/bin/env python3
"""
Script to sync subscription_tier in User table with Subscription table.
Creates Subscription records for users with GOLD/SILVER subscription_tier but no active subscription.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Subscription, SubscriptionTier
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sync_subscriptions():
    """Sync subscription_tier with Subscription table"""
    session = Session()
    try:
        # Find users with non-BRONZE subscription_tier
        premium_users = session.query(User).filter(
            User.subscription_tier.in_([SubscriptionTier.SILVER, SubscriptionTier.GOLD])
        ).all()

        logger.info(f"Found {len(premium_users)} users with premium subscription_tier")

        synced_count = 0
        for user in premium_users:
            # Check if user already has an active subscription
            existing_sub = session.query(Subscription).filter_by(user_id=user.id).first()

            if not existing_sub:
                # Create subscription record
                logger.info(f"Creating subscription for user {user.username} (tier: {user.subscription_tier.value})")

                # Set end date far in the future for manual subscriptions
                end_date = datetime.now() + timedelta(days=365*10)  # 10 years

                new_sub = Subscription(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    telegram_username=user.username,
                    username=user.username,
                    status='active',
                    plan='manual',
                    tier=user.subscription_tier,
                    start_date=datetime.now(),
                    end_date=end_date,
                    login_count=0
                )
                session.add(new_sub)
                synced_count += 1
            elif existing_sub.status != 'active':
                # Update existing inactive subscription
                logger.info(f"Activating subscription for user {user.username}")
                existing_sub.status = 'active'
                existing_sub.tier = user.subscription_tier
                existing_sub.end_date = datetime.now() + timedelta(days=365*10)
                synced_count += 1
            else:
                logger.info(f"User {user.username} already has active subscription")

        session.commit()
        logger.info(f"Successfully synced {synced_count} subscriptions")

    except Exception as e:
        logger.error(f"Error syncing subscriptions: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    sync_subscriptions()