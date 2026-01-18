#!/usr/bin/env python3
"""
Проверка пользователей с интересом спорт
"""

import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Используем DATABASE_PUBLIC_URL для подключения к Railway
db_url = os.getenv('DATABASE_PUBLIC_URL')
if not db_url:
    print("❌ DATABASE_PUBLIC_URL не найден в .env файле")
    exit(1)

# Преобразуем URL для SQLAlchemy
if db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

engine = create_engine(db_url)

with engine.connect() as conn:
    print('🏃 Пользователи с интересом "спорт":')
    result = conn.execute(text('''
        SELECT u.id, u.username, u.first_name, u.subscription_tier, p.interests
        FROM users u
        JOIN user_profiles p ON u.id = p.user_id
        WHERE p.interests = 'спорт'
        ORDER BY u.id
    '''))
    sport_users = result.fetchall()
    for user in sport_users:
        print(f'  ID {user[0]}: {user[1]} ({user[2]}) - {user[3]} - {user[4]}')