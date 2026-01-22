#!/usr/bin/env python3
"""Check production profile"""
import os
os.environ["LOCAL"] = "0"

from models import User, UserProfile, Session

session = Session()
user = session.query(User).filter_by(username="aleksandrinsider").first()
if user:
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    print(f"Username: {user.username}")
    print(f"Telegram ID: {user.telegram_id}")
    if profile:
        print(f"Interests: {profile.interests}")
        print(f"Skills: {profile.skills}")
        print(f"Goals: {profile.goals}")
        print(f"City: {profile.city}")
    else:
        print("No profile found")
else:
    print("User not found")
session.close()
