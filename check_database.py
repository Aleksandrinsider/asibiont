#!/usr/bin/env python3
"""
Script to check database structure and verify migrations.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text, inspect
from config import DATABASE_URL

def check_database():
    """Check database structure and verify migrations"""
    engine = create_engine(DATABASE_URL)

    try:
        print("🔍 Checking database connection...")
        with engine.connect() as conn:
            print("✅ Database connection successful")

            # Check if average_rating column exists in users table
            print("\n📋 Checking users table structure...")
            result = conn.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'average_rating'
            """))

            row = result.fetchone()
            if row:
                print(f"✅ average_rating column found: {row}")
            else:
                print("❌ average_rating column NOT found in users table")

            # Check subscription tiers
            print("\n📋 Checking subscription tier enum...")
            result = conn.execute(text("""
                SELECT enumtypid, enumlabel
                FROM pg_enum
                WHERE enumtypid = (
                    SELECT oid FROM pg_type WHERE typname = 'subscription_tier_enum'
                )
                ORDER BY enumsortorder
            """))

            tiers = result.fetchall()
            print(f"✅ Subscription tiers: {[tier[1] for tier in tiers]}")

            # Check if old enum values still exist
            print("\n📋 Checking for old subscription tier values...")
            result = conn.execute(text("""
                SELECT COUNT(*) as old_count FROM users
                WHERE subscription_tier::text IN ('BRONZE', 'SILVER', 'GOLD')
            """))

            old_count = result.scalar()
            if old_count > 0:
                print(f"⚠️  Found {old_count} users with old subscription tiers")
            else:
                print("✅ No old subscription tiers found")

            # Check recent users
            print("\n📋 Checking recent users...")
            result = conn.execute(text("""
                SELECT id, username, subscription_tier, average_rating, rating_count
                FROM users
                ORDER BY created_at DESC
                LIMIT 5
            """))

            users = result.fetchall()
            print("✅ Recent users:")
            for user in users:
                print(f"  - ID {user[0]}: @{user[1]}, tier: {user[2]}, rating: {user[3]}/{user[4]}")

    except Exception as e:
        print(f"❌ Database check failed: {e}")

if __name__ == "__main__":
    check_database()