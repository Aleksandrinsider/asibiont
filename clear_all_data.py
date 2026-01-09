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
        # Удаляем данные в правильном порядке (сначала дочерние таблицы)
        session.query(UserRating).delete()
        session.query(Interaction).delete()
        session.query(Task).delete()
        session.query(UserProfile).delete()
        session.query(Subscription).delete()
        session.query(User).delete()

        session.commit()
        print("✅ База данных очищена")

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