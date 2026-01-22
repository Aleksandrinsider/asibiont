# -*- coding: utf-8 -*-
"""Проверка всех пользователей в БД"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import SessionLocal, User, Subscription

def check_all_users():
    """Показать всех пользователей с подписками"""
    db = SessionLocal()
    try:
        users = db.query(User).all()
        print("=" * 60)
        print(f"ВСЕГО ПОЛЬЗОВАТЕЛЕЙ: {len(users)}")
        print("=" * 60)
        
        for user in users:
            sub = db.query(Subscription).filter_by(user_id=user.id).first()
            tier = sub.tier.value if sub else "Нет подписки"
            print(f"\n@{user.username or 'None'}")
            print(f"  Имя: {user.first_name or 'Не указано'}")
            print(f"  Telegram ID: {user.telegram_id}")
            print(f"  Тариф: {tier}")
            if sub:
                print(f"  Статус: {sub.status}")
            
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_all_users()
