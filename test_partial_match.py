#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест частичного совпадения интересов"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile
import config

# Connect to database
database_url = config.DATABASE_URL if hasattr(config, 'DATABASE_URL') else config.SQLALCHEMY_DATABASE_URI
engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=3600)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # Найти пользователя aleksandrinsider
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    if not user:
        print("❌ Пользователь aleksandrinsider не найден")
        exit(1)
    
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        print("❌ Профиль пользователя не найден")
        exit(1)
    
    print(f"👤 Пользователь: {user.username} (ID: {user.id})")
    print(f"📝 Интересы: {profile.interests}")
    print()
    
    # Получить его интересы
    user_interests = set(i.strip().lower() for i in profile.interests.split(',')) if profile.interests else set()
    print(f"🎯 Интересы пользователя: {user_interests}")
    print()
    
    # Найти всех пользователей с интересами, содержащими "спорт"
    all_profiles = session.query(UserProfile).filter(UserProfile.interests.isnot(None)).all()
    
    print("🔍 Поиск совпадений (частичное совпадение):")
    print()
    
    matches = []
    for p in all_profiles:
        if p.user_id == user.id:
            continue
        
        partner_interests = set(i.strip().lower() for i in p.interests.split(','))
        common = set()
        
        # Частичное совпадение
        for ui in user_interests:
            for pi in partner_interests:
                if ui in pi or pi in ui:
                    common.add(pi)
        
        if common:
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            matches.append({
                'username': partner_user.username if partner_user else 'Unknown',
                'interests': p.interests,
                'common': common
            })
    
    print(f"✅ Найдено контактов с общими интересами: {len(matches)}")
    print()
    
    # Показать первые 5
    for i, m in enumerate(matches[:10], 1):
        print(f"{i}. @{m['username']}")
        print(f"   Интересы: {m['interests']}")
        print(f"   Общее: {', '.join(m['common'])}")
        print()
    
    if len(matches) > 10:
        print(f"... и еще {len(matches) - 10} контактов")

finally:
    session.close()
