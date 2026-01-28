#!/usr/bin/env python3
"""
Clear Railway PostgreSQL database via public URL
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, MetaData

load_dotenv()

def clear_railway_database():
    """Clear all data from Railway database via public URL"""
    try:
        # Use DATABASE_PUBLIC_URL for external access
        url = os.getenv('DATABASE_PUBLIC_URL')
        if not url:
            print("❌ DATABASE_PUBLIC_URL not found in .env")
            return False

        print("Clearing Railway PostgreSQL database via public URL...")
        print(f"URL: {url.replace(os.getenv('PGPASSWORD', ''), '***')}")  # Hide password

        # Create engine
        engine = create_engine(url, connect_args={'connect_timeout': 10})

        # Get all table names
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE'
                ORDER BY table_name;
            """))
            tables = [row[0] for row in result.fetchall()]

        print(f"Found tables: {tables}")

        if not tables:
            print("✅ Database already empty!")
            return True

        # Drop all tables
        with engine.connect() as conn:
            # Disable foreign key checks temporarily
            conn.execute(text("SET session_replication_role = 'replica';"))

            for table in tables:
                print(f"Dropping table: {table}")
                conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE;"))

            # Re-enable foreign key checks
            conn.execute(text("SET session_replication_role = 'origin';"))

            conn.commit()

        print("✅ All tables dropped successfully!")

        # Verify tables are gone
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE';
            """))
            remaining_tables = [row[0] for row in result.fetchall()]

        if remaining_tables:
            print(f"⚠️  Some tables still exist: {remaining_tables}")
        else:
            print("✅ Database is now completely empty!")

        return True

    except Exception as e:
        print(f"❌ Failed to clear database: {e}")
        return False

if __name__ == "__main__":
    success = clear_railway_database()
    if success:
        print("\n🎉 Railway database cleared successfully!")
        print("Tables will be recreated when the application starts.")
    else:
        print("\n❌ Failed to clear Railway database.")