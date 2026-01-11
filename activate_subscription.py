#!/usr/bin/env python3
"""
Скрипт для активации подписки пользователю
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from subscription_service import activate_subscription

def main():
    if len(sys.argv) != 2:
        print("Использование: python activate_subscription.py <telegram_user_id>")
        sys.exit(1)

    user_id = int(sys.argv[1])

    print(f"Активация подписки для пользователя {user_id}...")

    success, message = activate_subscription(user_id, plan='monthly')

    if success:
        print(f"✅ {message}")
    else:
        print(f"❌ {message}")

if __name__ == "__main__":
    main()