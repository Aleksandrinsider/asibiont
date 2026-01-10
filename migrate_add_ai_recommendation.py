"""Migration: Add ai_recommendation column to tasks table"""
import os
from sqlalchemy import create_engine, text
from config import DATABASE_URL

def migrate():
    """Add ai_recommendation column to tasks table"""
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        # Check if column already exists
        result = conn.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='tasks' AND column_name='ai_recommendation'
        """))
        
        if result.fetchone() is None:
            print("Adding ai_recommendation column to tasks table...")
            conn.execute(text("""
                ALTER TABLE tasks 
                ADD COLUMN ai_recommendation TEXT
            """))
            conn.commit()
            print("✅ Migration completed successfully!")
        else:
            print("ℹ️  Column ai_recommendation already exists, skipping migration.")

if __name__ == "__main__":
    migrate()
