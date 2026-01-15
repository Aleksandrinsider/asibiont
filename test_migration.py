#!/usr/bin/env python3
"""
Test script to verify subscription_tier migration works correctly.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from config import DATABASE_URL
from models import Base, SubscriptionTier

def test_subscription_tier_migration():
    """Test that subscription_tier column exists and works correctly."""
    try:
        # Create engine
        engine = create_engine(DATABASE_URL)

        # Test 1: Check if column exists
        with engine.connect() as conn:
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'subscription_tier'"))
            if not result.fetchone():
                print("❌ FAIL: subscription_tier column does not exist")
                return False
            print("✅ PASS: subscription_tier column exists")

        # Test 2: Check if enum type exists
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 FROM pg_type WHERE typname = 'subscription_tier_enum'"))
            if not result.fetchone():
                print("❌ FAIL: subscription_tier_enum type does not exist")
                return False
            print("✅ PASS: subscription_tier_enum type exists")

        # Test 3: Test inserting a user with subscription_tier
        with engine.connect() as conn:
            # Insert test user
            conn.execute(text("INSERT INTO users (telegram_id, username, subscription_tier) VALUES (999999, 'test_user', 'bronze') ON CONFLICT (telegram_id) DO NOTHING"))
            conn.commit()

            # Check the value
            result = conn.execute(text("SELECT subscription_tier FROM users WHERE telegram_id = 999999"))
            row = result.fetchone()
            if row and row[0] == 'bronze':
                print("✅ PASS: subscription_tier correctly set to 'bronze'")
            else:
                print(f"❌ FAIL: subscription_tier not set correctly, got: {row[0] if row else 'None'}")
                return False

            # Clean up
            conn.execute(text("DELETE FROM users WHERE telegram_id = 999999"))
            conn.commit()

        print("🎉 All tests passed! Migration is working correctly.")
        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

if __name__ == "__main__":
    success = test_subscription_tier_migration()
    sys.exit(0 if success else 1)