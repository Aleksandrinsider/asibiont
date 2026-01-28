#!/usr/bin/env python3
"""
Create tables in Railway PostgreSQL database
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from models import Base

load_dotenv()

def create_railway_tables():
    """Create all tables in Railway database"""
    try:
        # Use DATABASE_PUBLIC_URL for external access
        url = os.getenv('DATABASE_PUBLIC_URL')
        if not url:
            print("❌ DATABASE_PUBLIC_URL not found in .env")
            return False

        print("Creating tables in Railway PostgreSQL database...")
        print(f"URL: {url.replace(os.getenv('PGPASSWORD', ''), '***')}")

        # Create engine
        engine = create_engine(url, connect_args={'connect_timeout': 10})

        # Create all tables
        print("Creating tables...")
        Base.metadata.create_all(engine)
        print("✅ All tables created successfully!")

        # Verify tables were created
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        print(f"Created tables: {tables}")

        return True

    except Exception as e:
        print(f"❌ Failed to create tables: {e}")
        return False

if __name__ == "__main__":
    success = create_railway_tables()
    if success:
        print("\n🎉 Railway database tables created successfully!")
    else:
        print("\n❌ Failed to create Railway database tables.")