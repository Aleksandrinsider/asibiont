#!/usr/bin/env python3
"""
Script to migrate subscription_tier enum values in the database.
Updates BRONZE -> LIGHT, SILVER -> STANDARD, GOLD -> PREMIUM
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from config import DATABASE_URL

def migrate_subscription_tiers():
    """Migrate subscription_tier values in the database"""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()

        try:
            print("Starting subscription tier migration...")

            # Update users table
            print("Updating users table...")
            conn.execute(text("""
                UPDATE users
                SET subscription_tier = CASE
                    WHEN subscription_tier::text = 'BRONZE' THEN 'LIGHT'::subscriptiontier
                    WHEN subscription_tier::text = 'SILVER' THEN 'STANDARD'::subscriptiontier
                    WHEN subscription_tier::text = 'GOLD' THEN 'PREMIUM'::subscriptiontier
                    ELSE subscription_tier
                END
            """))

            # Update subscriptions table
            print("Updating subscriptions table...")
            conn.execute(text("""
                UPDATE subscriptions
                SET tier = CASE
                    WHEN tier::text = 'BRONZE' THEN 'LIGHT'::subscriptiontier
                    WHEN tier::text = 'SILVER' THEN 'STANDARD'::subscriptiontier
                    WHEN tier::text = 'GOLD' THEN 'PREMIUM'::subscriptiontier
                    ELSE tier
                END
            """))

            # Update promo_codes table
            print("Updating promo_codes table...")
            conn.execute(text("""
                UPDATE promo_codes
                SET tier = CASE
                    WHEN tier::text = 'BRONZE' THEN 'LIGHT'::subscriptiontier
                    WHEN tier::text = 'SILVER' THEN 'STANDARD'::subscriptiontier
                    WHEN tier::text = 'GOLD' THEN 'PREMIUM'::subscriptiontier
                    ELSE tier
                END
            """))

            # Commit transaction
            trans.commit()
            print("Migration completed successfully!")

        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate_subscription_tiers()