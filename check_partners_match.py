#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Check why certain partners are showing in recommendations
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
        # Main user
        main_user = session.query(User).filter_by(telegram_id=146333757).first()
        main_profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        
        print("=== Main User Profile ===")
        print(f"Interests: {main_profile.interests}")
        print(f"Skills: {main_profile.skills}")
        print(f"Goals: {main_profile.goals}")
        print(f"Company: {main_profile.company}")
        
        # Check test users
        test_users = [
            ('kate_business_analyst', 210010),
            ('ivan_data_scientist', 210003),
        ]
        
        print("\n=== Test Users ===")
        for username, telegram_id in test_users:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            
            print(f"\n@{username}:")
            print(f"  Interests: {profile.interests}")
            print(f"  Skills: {profile.skills}")
            print(f"  Goals: {profile.goals}")
            print(f"  Company: {profile.company}")
            
            # Check for matches
            print("\n  Checking matches:")
            
            # Skills match
            if main_profile.skills and profile.skills:
                main_skills = set(s.strip().lower() for s in main_profile.skills.split(','))
                partner_skills = set(s.strip().lower() for s in profile.skills.split(','))
                common_skills = main_skills & partner_skills
                if common_skills:
                    print(f"  ✓ Common skills: {common_skills}")
                else:
                    print(f"  ✗ No common skills")
            
            # Interests match
            if main_profile.interests and profile.interests:
                main_interests = set(i.strip().lower() for i in main_profile.interests.split(','))
                partner_interests = set(i.strip().lower() for i in profile.interests.split(','))
                common_interests = main_interests & partner_interests
                if common_interests:
                    print(f"  ✓ Common interests: {common_interests}")
                else:
                    print(f"  ✗ No common interests")
            
            # Goals match
            if main_profile.goals and profile.goals:
                main_goals = set(g.strip().lower() for g in main_profile.goals.split(','))
                partner_goals = set(g.strip().lower() for g in profile.goals.split(','))
                common_goals = main_goals & partner_goals
                if common_goals:
                    print(f"  ✓ Common goals: {common_goals}")
                else:
                    print(f"  ✗ No common goals")
            
            # Company match
            if main_profile.company and profile.company:
                if main_profile.company.lower() == profile.company.lower():
                    print(f"  ✓ Same company: {profile.company}")
                else:
                    print(f"  ✗ Different company")
    
    finally:
        session.close()

if __name__ == "__main__":
    main()
