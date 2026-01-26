#!/usr/bin/env python3
"""
Fixed script to migrate subscription_tier enum values in the database.
Uses simple UPDATE statements instead of CASE/WHEN to avoid type coercion issues.
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
        # Use AUTOCOMMIT for enum operations
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")

        try:
            print("Starting subscription tier migration...")

            # Update users table with simple UPDATE statements
            print("Updating users table...")
            conn.execute(text("UPDATE users SET subscription_tier = 'LIGHT' WHERE subscription_tier::text = 'BRONZE'"))
            conn.execute(text("UPDATE users SET subscription_tier = 'STANDARD' WHERE subscription_tier::text = 'SILVER'"))
            conn.execute(text("UPDATE users SET subscription_tier = 'PREMIUM' WHERE subscription_tier::text = 'GOLD'"))

            # Update subscriptions table
            print("Updating subscriptions table...")
            conn.execute(text("UPDATE subscriptions SET tier = 'LIGHT' WHERE tier::text = 'BRONZE'"))
            conn.execute(text("UPDATE subscriptions SET tier = 'STANDARD' WHERE tier::text = 'SILVER'"))
            conn.execute(text("UPDATE subscriptions SET tier = 'PREMIUM' WHERE tier::text = 'GOLD'"))

            # Update promo_codes table
            print("Updating promo_codes table...")
            conn.execute(text("UPDATE promo_codes SET tier = 'LIGHT' WHERE tier::text = 'BRONZE'"))
            conn.execute(text("UPDATE promo_codes SET tier = 'STANDARD' WHERE tier::text = 'SILVER'"))
            conn.execute(text("UPDATE promo_codes SET tier = 'PREMIUM' WHERE tier::text = 'GOLD'"))

            print("✅ Migration completed successfully!")

        except Exception as e:
            print(f"❌ Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate_subscription_tiers()