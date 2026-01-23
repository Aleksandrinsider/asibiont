#!/usr/bin/env python3
"""Add test_sport users to favorites for user 1001"""

import os
os.environ['LOCAL'] = '1'  # Force local mode

from models import Session, User, UserProfile
import json

def add_to_favorites():
    """Add test_sport_5 and test_sport_7 to user 1001's favorites"""
    session = Session()
    try:
        # Get main user (1001)
        main_user = session.query(User).filter_by(telegram_id=1001).first()
        if not main_user:
            print("❌ User 1001 not found!")
            return
        
        # Get test users
        user5 = session.query(User).filter_by(username='test_sport_5').first()
        user7 = session.query(User).filter_by(username='test_sport_7').first()
        
        if not user5 or not user7:
            print("❌ Test sport users not found!")
            return
        
        print(f"✅ Found users:")
        print(f"   Main: {main_user.username} (ID: {main_user.id})")
        print(f"   Sport 5: {user5.username} (ID: {user5.id})")
        print(f"   Sport 7: {user7.username} (ID: {user7.id})")
        
        # Get or create profile
        profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        if not profile:
            profile = UserProfile(user_id=main_user.id)
            session.add(profile)
            session.flush()
        
        # Parse existing favorites
        favorites = []
        if profile.favorite_contacts:
            try:
                favorites = json.loads(profile.favorite_contacts)
            except:
                favorites = []
        
        print(f"\n📋 Current favorites: {favorites}")
        
        # Add test users to favorites if not already there
        added = []
        if user5.id not in favorites:
            favorites.append(user5.id)
            added.append(user5.username)
        if user7.id not in favorites:
            favorites.append(user7.id)
            added.append(user7.username)
        
        if added:
            profile.favorite_contacts = json.dumps(favorites)
            session.commit()
            print(f"\n✅ Added to favorites: {', '.join(added)}")
            print(f"📋 Updated favorites: {favorites}")
            print("\n🎉 Теперь посты от этих пользователей будут видны в ленте!")
        else:
            print("\nℹ️  Пользователи уже в избранном")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    add_to_favorites()
