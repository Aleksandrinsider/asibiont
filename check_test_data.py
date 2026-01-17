#!/usr/bin/env python3
import sys
import os
import sqlite3
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import DATABASE_URL

def check_test_data_sqlite():
    print("Проверка тест-данных в SQLite базе данных...")

    # Подключаемся напрямую к SQLite
    db_path = DATABASE_URL.replace('sqlite:///', '')
    print(f"Database path: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Проверяем тест-юзеров
        cursor.execute("SELECT id, telegram_id, first_name FROM users WHERE telegram_id IN (1001, 1002, 1003, 1004, 1005)")
        users = cursor.fetchall()
        print(f"\nНайдено тест-юзеров: {len(users)}")

        for user in users:
            user_id, telegram_id, name = user
            print(f"User ID: {user_id}, Telegram ID: {telegram_id}, Name: {name}")

            # Проверяем профиль
            cursor.execute("SELECT interests, city FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = cursor.fetchone()
            if profile:
                interests, city = profile
                print(f"  Profile: interests='{interests}', city='{city}'")

            # Проверяем подписку
            cursor.execute("SELECT tier, status FROM subscriptions WHERE user_id = ?", (user_id,))
            subscription = cursor.fetchone()
            if subscription:
                tier, status = subscription
                print(f"  Subscription: tier='{tier}', status='{status}'")

        # Проверяем промокод
        cursor.execute("SELECT code, discount_percent, max_uses, used_count FROM promo_codes WHERE code = 'BRONZEFREE26'")
        promo = cursor.fetchone()
        if promo:
            code, discount, max_uses, used_count = promo
            print(f"\nPromo Code {code}: discount={discount}%, max_uses={max_uses}, used_count={used_count}")

    finally:
        conn.close()

if __name__ == "__main__":
    check_test_data_sqlite()