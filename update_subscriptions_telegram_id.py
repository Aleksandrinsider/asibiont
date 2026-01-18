#!/usr/bin/env python3
"""
Script to update existing subscriptions with missing telegram_id
"""
import os
import sys
import logging

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, Subscription, User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_subscriptions_telegram_id():
    """Update subscriptions that are missing telegram_id"""
    session = Session()

    try:
        # Find subscriptions without telegram_id
        subs_without_telegram_id = session.query(Subscription).filter(
            Subscription.telegram_id.is_(None)
        ).all()

        logger.info(f"Found {len(subs_without_telegram_id)} subscriptions without telegram_id")

        updated_count = 0
        for sub in subs_without_telegram_id:
            # Get user to get telegram_id
            user = session.query(User).filter(User.id == sub.user_id).first()
            if user and user.telegram_id:
                sub.telegram_id = user.telegram_id
                sub.telegram_username = user.username
                sub.username = user.username
                updated_count += 1
                logger.info(f"Updated subscription id {sub.id} with telegram_id {user.telegram_id}")
            else:
                logger.warning(f"User not found or no telegram_id for subscription id {sub.id}, user_id {sub.user_id}")

        if updated_count > 0:
            session.commit()
            logger.info(f"Successfully updated {updated_count} subscriptions")
        else:
            logger.info("No subscriptions needed updating")

    except Exception as e:
        logger.error(f"Error updating subscriptions: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    update_subscriptions_telegram_id()