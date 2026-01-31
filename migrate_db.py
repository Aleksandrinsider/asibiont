#!/usr/bin/env python3
"""
Database migration script to add missing columns to existing tables.
Run this script to update the database schema.
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from sqlalchemy import create_engine, text
from config import DATABASE_URL

def migrate_database():
    """Add missing columns to existing tables"""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        # Check database type
        is_sqlite = 'sqlite' in DATABASE_URL.lower()

        if is_sqlite:
            # SQLite: use PRAGMA table_info
            def column_exists(table, column):
                result = conn.execute(text(f"PRAGMA table_info({table})"))
                columns = [row[1] for row in result.fetchall()]
                return column in columns
        else:
            # PostgreSQL: use information_schema
            def column_exists(table, column):
                result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = :table AND column_name = :column
                """), {"table": table, "column": column})
                return result.fetchone() is not None

        # Check if referral_balance column exists
        if not column_exists('users', 'referral_balance'):
            print("Adding referral_balance column to users table...")
            if is_sqlite:
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN referral_balance INTEGER DEFAULT 0
                """))
            else:
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN referral_balance INTEGER DEFAULT 0
                """))
            conn.commit()
            print("✓ Added referral_balance column")
        else:
            print("✓ referral_balance column already exists")

        # Check if referrer_id column exists
        if not column_exists('users', 'referrer_id'):
            print("Adding referrer_id column to users table...")
            if is_sqlite:
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id)
                """))
            else:
                conn.execute(text("""
                    ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id)
                """))
            conn.commit()
            print("✓ Added referrer_id column")
        else:
            print("✓ referrer_id column already exists")

        print("Migration completed successfully!")

if __name__ == "__main__":
    migrate_database()