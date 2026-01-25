#!/usr/bin/env python3
"""
Script to test premium contacts API for a specific user
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Subscription, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_premium_contacts(user_id):
    """Test premium contacts logic for a specific user"""
    # Force production mode to use Railway DB
    os.environ['LOCAL'] = '0'

    # Reload config to use Railway DB
    import importlib
    import config
    importlib.reload(config)

    logger.info(f"Testing premium contacts for user_id: {user_id}")

    session = Session()
    try:
        # Find the user
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.error(f"User not found: {user_id}")
            return

        logger.info(f"Testing premium contacts for user: {user.username} (ID: {user.telegram_id})")
        logger.info(f"User tier: {user.subscription_tier.value}")

        # Check if user has Gold tier
        if user.subscription_tier.value.lower() != 'gold':
            logger.info("User does not have Gold tier - no premium contacts")
            return

        # Get user profile
        user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not user_profile:
            logger.info("User has no profile - no premium contacts")
            return

        logger.info("User has profile and Gold tier - checking other Gold users")

        # Get hidden contacts
        hidden_contacts = set()
        if user.memory and len(user.memory.strip()) > 0:
            try:
                import re
                hide_matches = re.findall(r'hide_contact:@?(\w+):(\d+)', user.memory, re.IGNORECASE)
                import datetime
                from datetime import timezone as dt_timezone_local
                current_time = int(datetime.datetime.now(dt_timezone_local.utc).timestamp())
                for username, expiration_ts in hide_matches:
                    exp_ts = int(expiration_ts)
                    if exp_ts > current_time:
                        hidden_contacts.add(username.lower())
            except Exception as e:
                logger.error(f"Error parsing hidden contacts: {e}")

        logger.info(f"Hidden contacts: {hidden_contacts}")

        # Get blocked contacts
        blocked_by_me = set()
        if user_profile.blocked_contacts:
            try:
                import json
                blocked_by_me = set(json.loads(user_profile.blocked_contacts))
            except json.JSONDecodeError:
                pass

        logger.info(f"Blocked contacts: {blocked_by_me}")

        # Get all Gold users (except self)
        gold_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.GOLD,
            User.id != user.id
        ).all()

        logger.info(f"Found {len(gold_users)} other Gold users")

        visible_contacts = 0
        for gold_user in gold_users:
            username_clean = gold_user.username.replace('@', '').lower() if gold_user.username else ''
            is_hidden = username_clean in hidden_contacts
            is_blocked = gold_user.username in blocked_by_me

            if is_hidden or is_blocked:
                logger.info(f"Gold user {gold_user.username} is hidden or blocked")
            else:
                logger.info(f"Gold user {gold_user.username} should be visible")
                visible_contacts += 1

        logger.info(f"Total visible premium contacts: {visible_contacts}")

    except Exception as e:
        logger.error(f"Error testing premium contacts: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    # Test for aleksandrinsider
    test_premium_contacts(146333757)