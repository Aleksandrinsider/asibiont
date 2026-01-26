#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Создание тестовых пользователей"""

import os
from sqlalchemy.orm import sessionmaker
from models import User, engine

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

def create_test_users():
    """Создает тестовых пользователей"""

    # Создаем сессию
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Создаем пользователей, если они не существуют
        users_data = [
            {'username': 'test_user_4', 'telegram_id': 1000004},
            {'username': 'aleksandrinsider', 'telegram_id': 1000005},
            {'username': 'test_user_9', 'telegram_id': 1000009}
        ]

        for user_data in users_data:
            user = session.query(User).filter_by(username=user_data['username']).first()
            if not user:
                user = User(
                    username=user_data['username'],
                    telegram_id=user_data['telegram_id'],
                    first_name=f"Test {user_data['username']}"
                )
                session.add(user)
                print(f"Создан пользователь: @{user_data['username']}")
            else:
                print(f"Пользователь @{user_data['username']} уже существует")

        session.commit()
        print("✅ Пользователи созданы/проверены")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    create_test_users()