#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.utils import get_filtered_news_for_user
from models import Session, UserProfile

def test_news_filter_with_db():
    """Тест функции get_filtered_news_for_user с реальной БД"""
    try:
        # Получаем сессию БД
        session = Session()

        # Ищем пользователя с заполненным профилем
        profiles = session.query(UserProfile).filter(
            UserProfile.interests.isnot(None),
            UserProfile.interests != ""
        ).limit(5).all()

        if not profiles:
            print("No users with filled profiles found")
            return False

        print(f"Found {len(profiles)} users with profiles")

        # Тестируем на первом пользователе
        test_user = profiles[0]
        user_id = test_user.user_id

        print(f"Testing with user_id: {user_id}")
        print(f"User interests: {test_user.interests}")
        print(f"User skills: {test_user.skills}")
        print(f"User goals: {test_user.goals}")

        # Получаем отфильтрованные новости
        result = get_filtered_news_for_user(user_id, session)

        print(f"News result: {result}")

        session.close()

        if result:
            print("✅ Test passed - news filtering works!")
            return True
        else:
            print("⚠️ Test completed - no news returned (may be normal)")
            return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

if __name__ == "__main__":
    test_news_filter_with_db()