#!/usr/bin/env python3
"""
Проверка созданных тестовых пользователей
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
    # Проверяем количество пользователей
    result = conn.execute(text('SELECT COUNT(*) FROM users'))
    total_users = result.fetchone()[0]

    # Проверяем пользователей с интересом спорт
    result = conn.execute(text("SELECT COUNT(*) FROM user_profiles WHERE interests = 'спорт'"))
    sport_users = result.fetchone()[0]

    # Проверяем распределение по тарифам
    result = conn.execute(text('SELECT subscription_tier, COUNT(*) FROM users GROUP BY subscription_tier'))
    tier_stats = result.fetchall()

    print(f'📊 Всего пользователей в БД: {total_users}')
    print(f'🏃 Пользователей с интересом "спорт": {sport_users}')
    print('📈 Распределение по тарифам:')
    for tier, count in tier_stats:
        print(f'  {tier}: {count}')

    # Показываем последние 5 созданных пользователей
    print('\n👥 Последние созданные пользователи:')
    result = conn.execute(text('''
        SELECT u.id, u.username, u.first_name, u.subscription_tier, p.interests, p.city
        FROM users u
        JOIN user_profiles p ON u.id = p.user_id
        ORDER BY u.id DESC
        LIMIT 5
    '''))
    recent_users = result.fetchall()
    for user in recent_users:
        print(f'  ID {user[0]}: {user[1]} ({user[2]}) - {user[3]} - {user[4]} - {user[5]}')