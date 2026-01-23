#!/usr/bin/env python3
"""Test feed API"""

from models import Session, User, UserProfile, Post
import json

session = Session()
try:
    # Get user 1001
    user = session.query(User).filter_by(telegram_id=1001).first()
    print(f"User: {user.id if user else None}, telegram_id=1001")
    
    # Get profile with favorites
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    
    # Parse favorite contacts
    favorite_user_ids = []
    if user_profile and user_profile.favorite_contacts:
        try:
            favorite_data = json.loads(user_profile.favorite_contacts)
            for item in favorite_data:
                if isinstance(item, int):
                    favorite_user_ids.append(item)
                elif isinstance(item, str):
                    fav_user = session.query(User).filter(
                        (User.username == item) | (User.username == item.replace('@', ''))
                    ).first()
                    if fav_user:
                        favorite_user_ids.append(fav_user.id)
        except Exception as e:
            print(f"Error parsing favorites: {e}")
    
    print(f"Favorite user IDs: {favorite_user_ids}")
    
    # Include own posts too
    all_user_ids = favorite_user_ids + [user.id]
    print(f"All user IDs (favorites + self): {all_user_ids}")
    
    # Get posts
    if all_user_ids:
        posts = session.query(Post).filter(
            Post.user_id.in_(all_user_ids)
        ).order_by(Post.created_at.desc()).limit(50).all()
        print(f"\n✅ Found {len(posts)} posts:")
        for p in posts[:5]:
            print(f"  - ID {p.id}: {p.username} - {p.content[:50]}... (user_id={p.user_id})")
    else:
        print("\n❌ No user IDs to search")
        
finally:
    session.close()
