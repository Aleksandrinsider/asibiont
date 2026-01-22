# -*- coding: utf-8 -*-
"""Проверка профилей пользователей в базе данных"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import SessionLocal, User, UserProfile

def check_profiles():
    """Проверяем профили с заполненным bio"""
    db = SessionLocal()
    try:
        # Получаем все профили с bio
        profiles = db.query(UserProfile, User).join(
            User, UserProfile.user_id == User.id
        ).filter(UserProfile.bio.isnot(None)).all()
        
        print(f"Найдено профилей с bio: {len(profiles)}\n")
        
        for profile, user in profiles:
            print("=" * 60)
            print(f"Username: @{user.username}")
            print(f"Имя: {user.first_name}")
            print(f"Bio: {profile.bio}")
            print(f"Interests: {profile.interests}")
            print(f"Skills: {profile.skills}")
            print()
            
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_profiles()
