#!/usr/bin/env python3
"""
Script to add average_rating column to users table.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text, Column, Integer
from config import DATABASE_URL

def add_average_rating_column():
    """Add average_rating column to users table"""
    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        try:
            print("Adding average_rating column to users table...")

            # Add the column
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS average_rating INTEGER DEFAULT 0"))
            conn.commit()

            print("✅ Successfully added average_rating column to users table")

        except Exception as e:
            print(f"❌ Error adding column: {e}")
            conn.rollback()

if __name__ == "__main__":
    add_average_rating_column()