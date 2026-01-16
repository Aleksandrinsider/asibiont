#!/usr/bin/env python3
"""
Check test users.
"""
import os
os.environ['DATABASE_URL'] = 'sqlite:///test_local.db'

from models import Session, User, UserProfile

session = Session()
users = session.query(User).filter(User.telegram_id.in_([111111, 222222, 333333, 444444, 555555])).all()
for user in users:
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    print(f'{user.username}: Город - {profile.city if profile else "N/A"}, Тариф - {user.subscription_tier.value}')
session.close()