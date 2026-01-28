#!/usr/bin/env python3
"""
Script to clear all data from Railway PostgreSQL database
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# Always use Railway database URL for this script
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_PUBLIC_URL or DATABASE_URL not found in .env")
    sys.exit(1)

def clear_database():
    """Clear all data from database by dropping and recreating tables"""
    print("Clearing Railway database...")

    # Create engine
    engine = create_engine(DATABASE_URL)

    try:
        with engine.connect() as conn:
            # Drop all tables
            print("Dropping all tables...")
            conn.execute(text("""
                DROP TABLE IF EXISTS post_views;
                DROP TABLE IF EXISTS comments;
                DROP TABLE IF EXISTS post_likes;
                DROP TABLE IF EXISTS posts;
                DROP TABLE IF EXISTS payment_history;
                DROP TABLE IF EXISTS promo_codes;
                DROP TABLE IF EXISTS subscriptions;
                DROP TABLE IF EXISTS user_ratings;
                DROP TABLE IF EXISTS user_profiles;
                DROP TABLE IF EXISTS interactions;
                DROP TABLE IF EXISTS tasks;
                DROP TABLE IF EXISTS users;
            """))
            conn.commit()
            print("All tables dropped successfully!")

            # Recreate tables
            print("Recreating tables...")
            from models import Base
            Base.metadata.create_all(engine)
            print("All tables recreated successfully!")

    except Exception as e:
        print(f"Error clearing database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Check if we're in production mode
    if os.getenv('LOCAL') == '1' and not os.getenv('FORCE_RAILWAY_CLEAR'):
        print("This script is for Railway database only. Set LOCAL=0 or FORCE_RAILWAY_CLEAR=1 to run.")
        sys.exit(1)

    clear_database()