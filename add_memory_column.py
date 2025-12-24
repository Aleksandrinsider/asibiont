from models import engine, Base
from sqlalchemy import text

# Add memory column to users table if it doesn't exist
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS memory TEXT"))
        conn.commit()
        print("Memory column added successfully")
    except Exception as e:
        print(f"Error adding column: {e}")