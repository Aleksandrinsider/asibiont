#!/usr/bin/env python3
"""
Add pending_delegator_report field to tasks table
"""
import os
import sys
import psycopg2
from urllib.parse import urlparse

from config import DATABASE_URL

def add_pending_delegator_field():
    """Add pending_delegator_report column to tasks table"""
    print(f"Connecting to database...")
    
    # Parse DATABASE_URL
    result = urlparse(DATABASE_URL)
    username = result.username
    password = result.password
    database = result.path[1:]
    hostname = result.hostname
    port = result.port
    
    # Connect using psycopg2
    conn = psycopg2.connect(
        database=database,
        user=username,
        password=password,
        host=hostname,
        port=port
    )
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Check if column already exists
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'tasks' 
        AND column_name = 'pending_delegator_report'
    """)
    
    exists = cursor.fetchone()
    
    if exists:
        print("✅ Column 'pending_delegator_report' already exists in tasks table")
        cursor.close()
        conn.close()
        return
    
    # Add the column
    cursor.execute("""
        ALTER TABLE tasks 
        ADD COLUMN pending_delegator_report BIGINT
    """)
    
    print("✅ Successfully added 'pending_delegator_report' column to tasks table")
    cursor.close()
    conn.close()

if __name__ == "__main__":
    try:
        add_pending_delegator_field()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
