#!/usr/bin/env python3
"""
Script to create profiles for GOLD users in Railway database
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_profiles_for_gold_users():
    """Create basic profiles for GOLD users without profiles"""
    # Force production mode to use Railway DB
    os.environ['LOCAL'] = '0'

    # Reload config to use Railway DB
    import importlib
    import config
    importlib.reload(config)

    session = Session()
    try:
        # Get GOLD users
        gold_users = session.query(User).filter(User.subscription_tier == SubscriptionTier.GOLD).all()
        logger.info(f"Found {len(gold_users)} total GOLD users")

        # Check which ones don't have profiles
        gold_users_without_profiles = []
        for user in gold_users:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if not profile:
                gold_users_without_profiles.append(user)
                logger.info(f"GOLD user without profile: {user.username} (ID: {user.telegram_id})")
            else:
                logger.info(f"GOLD user with profile: {user.username} (ID: {user.telegram_id})")

        logger.info(f"Found {len(gold_users_without_profiles)} GOLD users without profiles")

        created_count = 0
        for user in gold_users_without_profiles:
            # Skip the main user (aleksandrinsider) as they already have a profile
            if user.username == 'aleksandrinsider':
                logger.info(f"Skipping main user: {user.username}")
                continue

            logger.info(f"Creating profile for GOLD user: {user.username} (ID: {user.telegram_id})")

            # Create a basic profile
            new_profile = UserProfile(
                user_id=user.id,
                skills='Бизнес, управление проектами',
                interests='Технологии, инновации, развитие',
                goals='Развитие бизнеса, новые партнерства',
                company=f'Компания {user.username}',
                position='Руководитель',
                bio=f'Профессионал в сфере бизнеса. Ищу интересные проекты и партнерства.',
                languages='Русский',
                total_tasks_created=0,
                completed_tasks=0,
                skipped_tasks=0,
                average_completion_time=0,
                last_activity=user.last_interaction_at,
                average_rating=0,
                rating_count=0,
                interaction_count=0
            )

            session.add(new_profile)
            created_count += 1
            logger.info(f"Added profile for {user.username}, total created: {created_count}")

        session.commit()
        logger.info(f"Successfully created {created_count} profiles for GOLD users")

    except Exception as e:
        logger.error(f"Error creating profiles: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    create_profiles_for_gold_users()