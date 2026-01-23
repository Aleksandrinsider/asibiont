#!/usr/bin/env python3
"""Add test_sport_10 to aleksandrinsider's favorites in PRODUCTION"""

from models import Session, User, UserProfile
import json

try:
    session = Session()

    # Find aleksandrinsider
    me = session.query(User).filter_by(username='aleksandrinsider').first()
    if not me:
        print("❌ aleksandrinsider не найден")
        session.close()
        exit(1)

    print(f"✅ Найден пользователь: {me.username} (ID: {me.id})")

    # Get or create profile
    profile = session.query(UserProfile).filter_by(user_id=me.id).first()
    if not profile:
        profile = UserProfile(user_id=me.id, favorite_contacts='[]')
        session.add(profile)
        session.flush()
        print("✅ Создан новый профиль")

    # Get current favorites
    favorites = []
    if profile.favorite_contacts:
        try:
            favorites = json.loads(profile.favorite_contacts)
        except:
            favorites = []

    print(f"📋 Текущие избранные: {favorites}")

    # Add test_sport_10 if not already there
    if 'test_sport_10' not in favorites:
        favorites.append('test_sport_10')
        profile.favorite_contacts = json.dumps(favorites)
        session.commit()
        print(f"✅ Добавлен test_sport_10 в избранные")
        print(f"📋 Новый список избранных: {favorites}")
    else:
        print("ℹ️  test_sport_10 уже в избранных")

    session.close()
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
