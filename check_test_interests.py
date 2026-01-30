#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile

def check_test_user_interests():
    session = Session()
    try:
        print("=== Проверка интересов тестовых пользователей ===")
        for i in [1, 2, 3, 4, 5]:
            user = session.query(User).filter_by(telegram_id=1000+i).first()
            if user:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                interests = profile.interests if profile and profile.interests else "NO PROFILE"
                print(f"test{i}: {interests}")
    finally:
        session.close()

if __name__ == "__main__":
    check_test_user_interests()