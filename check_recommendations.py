#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify profile update and test user visibility for recommendations
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

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
        # Check main user profile
        print("=== Main User Profile ===")
        result = session.execute(
            text("""
                SELECT u.telegram_id, u.username, 
                       up.city, up.company, up.position, 
                       up.interests, up.skills, up.goals
                FROM users u
                LEFT JOIN user_profiles up ON u.id = up.user_id
                WHERE u.telegram_id = 146333757
            """)
        ).first()
        
        if result:
            print(f"Telegram ID: {result[0]}")
            print(f"Username: @{result[1]}")
            print(f"City: {result[2]}")
            print(f"Company: {result[3]}")
            print(f"Position: {result[4]}")
            print(f"Interests: {result[5]}")
            print(f"Skills: {result[6]}")
            print(f"Goals: {result[7]}")
        else:
            print("Main user not found")
        
        print("\n=== Test Users Created ===")
        test_users = session.execute(
            text("""
                SELECT u.telegram_id, u.username, 
                       up.city, up.company, up.position,
                       up.interests, up.skills
                FROM users u
                LEFT JOIN user_profiles up ON u.id = up.user_id
                WHERE u.telegram_id BETWEEN 210001 AND 210010
                ORDER BY u.telegram_id
            """)
        ).fetchall()
        
        print(f"Total test users: {len(test_users)}\n")
        for user in test_users:
            print(f"@{user[1]} - {user[4]} at {user[3]} ({user[2]})")
            print(f"  Interests: {user[5][:60]}...")
            print()
        
        print("=== Recommendation System Test ===")
        print("Test users can now be evaluated in 'Рекомендуемые' filter")
        print("They should match based on shared interests/skills:")
        print("- Python developers will match with main user's Python skills")
        print("- Product managers will match with management interests")
        print("- Moscow-based professionals will have location relevance")
        
    finally:
        session.close()

if __name__ == "__main__":
    main()
