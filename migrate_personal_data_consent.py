#!/usr/bin/env python3
"""
Migration script to add personal data consent fields to User table
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from config import DATABASE_URL

load_dotenv()

def migrate_personal_data_consent():
    """Add personal_data_consent and consent_given_at columns to users table"""
    try:
        url = DATABASE_URL
        if not url:
            print("❌ DATABASE_URL not found")
            return False

        print("Adding personal data consent fields to users table...")
        print(f"Database: {url.split('@')[1] if '@' in url else 'local'}")

        # Create engine
        engine = create_engine(url)

        # Add new columns
        with engine.connect() as conn:
            # For SQLite, try to add columns and catch errors if they exist
            try:
                print("Adding personal_data_consent column...")
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN personal_data_consent BOOLEAN DEFAULT 0
                """))
                conn.commit()
                print("✅ personal_data_consent column added")
            except Exception as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    print("ℹ️ personal_data_consent column already exists")
                else:
                    raise

            try:
                print("Adding consent_given_at column...")
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN consent_given_at TIMESTAMP
                """))
                conn.commit()
                print("✅ consent_given_at column added")
            except Exception as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    print("ℹ️ consent_given_at column already exists")
                else:
                    raise

        print("✅ Migration completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = migrate_personal_data_consent()
    if success:
        print("\n🎉 Personal data consent fields added successfully!")
    else:
        print("\n❌ Failed to add personal data consent fields.")