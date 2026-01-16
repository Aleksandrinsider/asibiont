#!/usr/bin/env python3
import os
import psycopg2
from urllib.parse import urlparse

# Get DATABASE_URL from environment
database_url = os.getenv('DATABASE_URL')

if not database_url:
    print("❌ DATABASE_URL не установлен. Используйте: railway run python clear_db_railway.py")
    exit(1)

# Parse the URL
result = urlparse(database_url)

try:
    # Connect directly using psycopg2
    conn = psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )
    
    conn.autocommit = True
    cursor = conn.cursor()
    
    print("✅ Подключено к базе данных")
    
    # Disable foreign key constraints
    cursor.execute("SET session_replication_role = 'replica';")
    
    # Delete all data
    tables = ['user_ratings', 'interactions', 'tasks', 'user_profiles', 'subscriptions', 'users']
    
    for table in tables:
        print(f"🗑️  Очистка таблицы {table}...")
        cursor.execute(f"DELETE FROM {table};")
        print(f"✅ Таблица {table} очищена")
    
    # Re-enable foreign key constraints
    cursor.execute("SET session_replication_role = 'origin';")
    
    cursor.close()
    conn.close()
    
    print("✅ Все данные успешно удалены из базы!")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    exit(1)
