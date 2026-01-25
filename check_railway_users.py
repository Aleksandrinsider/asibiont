#!/usr/bin/env python3
"""
Script to check premium users in Railway database
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Subscription, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_railway_users():
    """Check premium users in Railway database"""
    # Force production mode to use Railway DB
    os.environ['LOCAL'] = '0'

    # Reload config to use Railway DB
    import importlib
    import config
    importlib.reload(config)

    session = Session()
    try:
        # Get all users
        all_users = session.query(User).all()
        logger.info(f"Total users in database: {len(all_users)}")

        # Get premium users (GOLD and SILVER)
        premium_users = session.query(User).filter(
            User.subscription_tier.in_([SubscriptionTier.SILVER, SubscriptionTier.GOLD])
        ).all()

        logger.info(f"Premium users (GOLD/SILVER): {len(premium_users)}")

        for user in premium_users:
            logger.info(f"User: {user.username} (ID: {user.telegram_id}), Tier: {user.subscription_tier.value}")

            # Check if user has profile
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                logger.info(f"  Has profile: {profile.company or 'No company'}, Position: {profile.position or 'No position'}")
            else:
                logger.info("  No profile found")

            # Check subscription record
            sub = session.query(Subscription).filter_by(user_id=user.id).first()
            if sub:
                logger.info(f"  Subscription: {sub.status}, Plan: {sub.plan}, End: {sub.end_date}")
            else:
                logger.info("  No subscription record")

        # Check all users
        logger.info("\nAll users:")
        for user in all_users:
            logger.info(f"User: {user.username} (ID: {user.telegram_id}), Tier: {user.subscription_tier.value}")

            # Check if user has profile
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                logger.info(f"  Has profile: {profile.company or 'No company'}, Position: {profile.position or 'No position'}")
            else:
                logger.info("  No profile found")

            # Check subscription record
            sub = session.query(Subscription).filter_by(user_id=user.id).first()
            if sub:
                logger.info(f"  Subscription: {sub.status}, Plan: {sub.plan}, End: {sub.end_date}")
            else:
                logger.info("  No subscription record")

        # Check GOLD users specifically
        gold_users = session.query(User).filter(User.subscription_tier == SubscriptionTier.GOLD).all()
        logger.info(f"\nGOLD users: {len(gold_users)}")

        gold_with_profiles = 0
        for user in gold_users:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                gold_with_profiles += 1
                logger.info(f"GOLD user with profile: {user.username}")
            else:
                logger.info(f"GOLD user without profile: {user.username}")

        logger.info(f"GOLD users with profiles: {gold_with_profiles}/{len(gold_users)}")

        if gold_with_profiles >= 2:
            logger.info("Multiple GOLD users with profiles found - premium contacts should be visible")
        else:
            logger.info("Less than 2 GOLD users with profiles - premium contacts won't show")

    except Exception as e:
        logger.error(f"Error checking users: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    check_railway_users()