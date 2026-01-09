#!/usr/bin/env python3
"""
Скрипт для полной очистки базы данных и Redis.
Использовать только для тестирования!
"""

import os
import sys
from datetime import datetime
import pytz

# Добавляем текущую директорию в путь
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from dotenv import load_dotenv
load_dotenv()

from models import Session, User, UserProfile, Task, Interaction, Subscription, UserRating, Base
from config import DATABASE_URL
import redis

def clear_database():
    """Полная очистка базы данных"""
    print("Очистка базы данных...")

    session = Session()

    try:
        # Сначала пробуем удалить через ORM
        print("  Удаляем рейтинги...")
        session.query(UserRating).delete()

        print("  Удаляем взаимодействия...")
        session.query(Interaction).delete()

        print("  Удаляем задачи...")
        session.query(Task).delete()

        print("  Удаляем профили...")
        session.query(UserProfile).delete()

        print("  Удаляем подписки...")
        session.query(Subscription).delete()

        print("  Удаляем пользователей...")
        session.query(User).delete()

        session.commit()
        print("✅ База данных очищена через ORM")

        # Дополнительная проверка
        users_count = session.query(User).count()
        subscriptions_count = session.query(Subscription).count()
        print(f"  Проверка после ORM: пользователей - {users_count}, подписок - {subscriptions_count}")

        # Если остались данные, используем raw SQL
        if users_count > 0 or subscriptions_count > 0:
            print("  ⚠️ Остались данные, используем raw SQL...")

            # Отключаем foreign key checks для SQLite
            session.execute("PRAGMA foreign_keys = OFF")

            # Удаляем все данные через raw SQL
            session.execute("DELETE FROM user_ratings")
            session.execute("DELETE FROM interactions")
            session.execute("DELETE FROM tasks")
            session.execute("DELETE FROM user_profiles")
            session.execute("DELETE FROM subscriptions")
            session.execute("DELETE FROM users")

            # Включаем foreign key checks обратно
            session.execute("PRAGMA foreign_keys = ON")

            session.commit()
            print("✅ База данных очищена через raw SQL")

            # Финальная проверка
            users_count = session.query(User).count()
            subscriptions_count = session.query(Subscription).count()
            print(f"  Финальная проверка: пользователей - {users_count}, подписок - {subscriptions_count}")

    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка при очистке БД: {e}")
        import traceback
        traceback.print_exc()

    finally:
        session.close()

def clear_redis():
    """Очистка Redis"""
    print("Очистка Redis...")

    try:
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            if redis_url.startswith("redis://"):
                # Удаляем протокол для redis-py
                redis_url = redis_url.replace("redis://", "")

            # Разбираем URL
            if "@" in redis_url:
                # redis://:password@host:port/db
                parts = redis_url.split("@")
                auth_part = parts[0]
                host_part = parts[1]

                password = auth_part.split(":")[-1] if ":" in auth_part else None
                host_port_db = host_part.split("/")
                host_port = host_port_db[0].split(":")
                host = host_port[0]
                port = int(host_port[1]) if len(host_port) > 1 else 6379
                db = int(host_port_db[1]) if len(host_port_db) > 1 else 0

                r = redis.Redis(host=host, port=port, password=password, db=db)
            else:
                # localhost
                r = redis.Redis(host='localhost', port=6379, db=0)

            r.flushall()
            print("✅ Redis очищен")
        else:
            print("⚠️ REDIS_URL не найден, Redis не очищен")

    except Exception as e:
        print(f"❌ Ошибка при очистке Redis: {e}")
        import traceback
        traceback.print_exc()

def reset_database():
    """Пересоздание таблиц"""
    print("Пересоздание таблиц...")

    try:
        from sqlalchemy import create_engine
        engine = create_engine(DATABASE_URL)
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        print("✅ Таблицы пересозданы")

    except Exception as e:
        print(f"❌ Ошибка при пересоздании таблиц: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    print("🚨 ВНИМАНИЕ: Это удалит ВСЕ данные!")
    confirm = input("Вы уверены? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Отмена")
        sys.exit(0)

    clear_redis()
    clear_database()
    reset_database()

    print("🎉 Очистка завершена!")