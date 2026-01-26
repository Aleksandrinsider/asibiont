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

            # Use SQLAlchemy inspector for cross-database compatibility
            inspector = inspect(engine)

            # Check if users table exists
            if 'users' in inspector.get_table_names():
                print("\n📋 Checking users table structure...")

                # Check columns
                columns = inspector.get_columns('users')
                column_names = [col['name'] for col in columns]
                print(f"✅ Users table columns: {column_names}")

                # Check for average_rating column
                if 'average_rating' in column_names:
                    print("✅ average_rating column found")
                else:
                    print("❌ average_rating column NOT found in users table")

                # Check subscription tiers in data
                result = conn.execute(text("SELECT DISTINCT subscription_tier FROM users"))
                tiers = result.fetchall()
                print(f"🏷️  Subscription tiers in use: {[t[0] for t in tiers if t[0]]}")

                # Check for old enum values
                old_tiers = ['BRONZE', 'SILVER', 'GOLD']
                found_old = any(tier[0] in old_tiers for tier in tiers if tier[0])
                if found_old:
                    print("❌ Old subscription tiers still exist in data!")
                else:
                    print("✅ No old subscription tiers found in data")

                # Show recent users
                result = conn.execute(text("SELECT id, username, subscription_tier FROM users ORDER BY id DESC LIMIT 5"))
                users = result.fetchall()
                print("\n👥 Recent users:")
                for user in users:
                    print(f"  ID: {user[0]}, Username: {user[1] or 'None'}, Tier: {user[2]}")

            else:
                print("❌ Users table does not exist")

            # Check other tables
            tables = inspector.get_table_names()
            print(f"\n📋 All tables: {tables}")

    except Exception as e:
        print(f"❌ Database check failed: {e}")
        return False

    return True

if __name__ == "__main__":
    success = check_database()
    if success:
        print("\n🎯 Database check completed successfully")
    else:
        print("\n❌ Database check failed")
        sys.exit(1)