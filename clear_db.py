from models import engine, Base
from sqlalchemy import text

# Clear all data from tables
with engine.connect() as conn:
    try:
        # Disable foreign key checks for PostgreSQL
        conn.execute(text("SET session_replication_role = 'replica';"))
        # Truncate tables
        conn.execute(text("TRUNCATE TABLE tasks RESTART IDENTITY CASCADE;"))
        conn.execute(text("TRUNCATE TABLE users RESTART IDENTITY CASCADE;"))
        # Re-enable
        conn.execute(text("SET session_replication_role = 'origin';"))
        conn.commit()
        print("Database cleared successfully")
    except Exception as e:
        print(f"Error clearing database: {e}")