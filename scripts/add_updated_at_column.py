#!/usr/bin/env python3
"""
Migration script to add updated_at column to users table
"""
import psycopg2
from urllib.parse import urlparse
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not found")
    exit(1)

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Check if updated_at column exists
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'updated_at'
    """)

    if cur.fetchone():
        print("updated_at column already exists")
    else:
        # Add updated_at column
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        """)

        # Set updated_at = created_at for existing records
        cur.execute("""
            UPDATE users
            SET updated_at = created_at
            WHERE updated_at IS NULL
        """)

        conn.commit()
        print("ALTER TABLE executed: updated_at column added")

    conn.close()

except Exception as e:
    print(f"Error: {e}")
    exit(1)