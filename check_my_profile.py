# -*- coding: utf-8 -*-
"""Проверка telegram_id для aleksandrinsider"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import SessionLocal, User

def check_user():
    """Найти telegram_id для aleksandrinsider"""
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username='aleksandrinsider').first()
        if user:
            print(f"Найден пользователь @{user.username}")
            print(f"Telegram ID: {user.telegram_id}")
            print(f"Имя: {user.first_name}")
        else:
            print("Пользователь @aleksandrinsider не найден")
            print("\nВсе пользователи:")
            all_users = db.query(User).all()
            for u in all_users:
                print(f"  @{u.username} - telegram_id: {u.telegram_id}")
            
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_user()
