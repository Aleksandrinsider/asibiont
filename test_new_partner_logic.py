#!/usr/bin/env python3
"""
Тест новой логики поиска партнеров с приоритетом релевантности
"""

import os
import sys
sys.path.append('.')

from ai_integration.handlers import find_partners
from models import User, UserProfile, Task, init_db
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def test_new_partner_logic():
    """Тестируем новую логику поиска партнеров"""

    # Инициализируем базу данных
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Найдем тестового пользователя
        test_user = session.query(User).filter_by(username='test_user').first()
        if not test_user:
            print("Тестовый пользователь не найден")
            return

        print(f"Тестируем для пользователя: @{test_user.username}")

        # Получим профиль пользователя
        user_profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
        if not user_profile:
            print("Профиль пользователя не найден")
            return

        print(f"Город: {user_profile.city}")
        print(f"Навыки: {user_profile.skills}")
        print(f"Интересы: {user_profile.interests}")
        print(f"Цели: {user_profile.goals}")

        # Вызовем новую логику поиска партнеров
        result = find_partners(user_id=test_user.telegram_id, session=session)

        print(f"\nРезультат поиска партнеров:")
        print(result)

        print("\nТест завершен успешно!")

    except Exception as e:
        print(f"Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()

    finally:
        session.close()

if __name__ == "__main__":
    test_new_partner_logic()