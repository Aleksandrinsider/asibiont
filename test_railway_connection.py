#!/usr/bin/env python3
"""
Test connection to Railway PostgreSQL database
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

def test_railway_connection():
    """Test connection to Railway database using public URL"""
    try:
        # Use DATABASE_PUBLIC_URL for external access
        url = os.getenv('DATABASE_PUBLIC_URL')
        if not url:
            print("❌ DATABASE_PUBLIC_URL not found in .env")
            return False

        print("Testing Railway PostgreSQL connection via public URL...")
        print(f"URL: {url.replace(os.getenv('PGPASSWORD', ''), '***')}")  # Hide password in logs

        # Create engine
        engine = create_engine(url, connect_args={'connect_timeout': 10})

        # Test connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            print("✅ Connection successful!")
            print(f"PostgreSQL version: {version}")

            # Check if tables exist
            result = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
            """))
            tables = [row[0] for row in result.fetchall()]
            print(f"Tables in database: {tables}")

        return True

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    test_railway_connection()