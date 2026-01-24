#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile

session = Session()
try:
    users = session.query(User).all()
    print(f"\nВсего пользователей: {len(users)}\n")
    
    for user in users:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"ID: {user.id}, TG ID: {user.telegram_id}, Username: @{user.username}")
        if profile:
            print(f"  Город: {profile.city}")
            print(f"  Интересы: {profile.interests}")
        else:
            print("  Профиль не создан")
        print()
finally:
    session.close()
