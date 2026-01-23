#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Migration: Add birthdate and zodiac_sign fields to user_profiles table
"""

import os
os.environ['LOCAL'] = '1'

from sqlalchemy import text
from models import Session, engine

def migrate():
    """Add birthdate and zodiac_sign columns"""
    session = Session()
    
    try:
        # Check if columns already exist
        result = session.execute(text("PRAGMA table_info(user_profiles)"))
        columns = [row[1] for row in result.fetchall()]
        
        if 'birthdate' not in columns:
            print("Adding birthdate column...")
            session.execute(text("ALTER TABLE user_profiles ADD COLUMN birthdate VARCHAR(10)"))
            session.commit()
            print("✅ birthdate column added")
        else:
            print("⚠️ birthdate column already exists")
        
        if 'zodiac_sign' not in columns:
            print("Adding zodiac_sign column...")
            session.execute(text("ALTER TABLE user_profiles ADD COLUMN zodiac_sign VARCHAR(20)"))
            session.commit()
            print("✅ zodiac_sign column added")
        else:
            print("⚠️ zodiac_sign column already exists")
        
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    migrate()
