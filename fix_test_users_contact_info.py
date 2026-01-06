#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fix contact_info for test users
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def main():
    session = Session()
    try:
        # Get all test users (210001-210010)
        test_users = session.query(User).filter(
            User.telegram_id.between(210001, 210010)
        ).all()
        
        print(f"Found {len(test_users)} test users")
        
        for user in test_users:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                # Set contact_info to username (without @)
                profile.contact_info = user.username
                print(f"Updated contact_info for @{user.username}: {profile.contact_info}")
        
        session.commit()
        print("\nSuccess! All test users now have contact_info set.")
        
        # Verify
        print("\n=== Verification ===")
        for user in test_users:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                print(f"@{user.username} -> contact_info: {profile.contact_info}")
    
    finally:
        session.close()

if __name__ == "__main__":
    main()
