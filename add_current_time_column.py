from models import engine, Base
from sqlalchemy import text

# Add current_time column to user_profiles table if it doesn't exist
with engine.connect() as conn:
    try:
        conn.execute(text('ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS "current_time" VARCHAR(10)'))
        conn.commit()
        print("current_time column added successfully")
    except Exception as e:
        print(f"Error adding column: {e}")