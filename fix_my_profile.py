#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile

USER_TG_ID = 7072177138  # aleksandrinsider

session = Session()
try:
    user = session.query(User).filter_by(telegram_id=USER_TG_ID).first()
    if user:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"Текущие интересы: {profile.interests}")
            
            # Исправляем на нормальные интересы
            profile.interests = "программирование"
            session.commit()
            
            print(f"Обновлённые интересы: {profile.interests}")
            print("✅ Профиль исправлен!")
        else:
            print("Профиль не найден")
    else:
        print("Пользователь не найден")
finally:
    session.close()
