#!/usr/bin/env python3
"""
Тест обновления профиля пользователя
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

# Тестируем обновление профиля для пользователя с ID 161 (первый тестовый пользователь)
user_id = 161

with engine.connect() as conn:
    print(f"🔍 Проверяем профиль пользователя ID {user_id} до обновления...")

    # Получаем текущий профиль
    result = conn.execute(text('''
        SELECT interests, skills, goals, city
        FROM user_profiles
        WHERE user_id = :user_id
    '''), {'user_id': user_id})

    profile = result.fetchone()
    if profile:
        print(f"📋 Текущий профиль:")
        print(f"  Интересы: {profile[0] or 'не указаны'}")
        print(f"  Навыки: {profile[1] or 'не указаны'}")
        print(f"  Цели: {profile[2] or 'не указаны'}")
        print(f"  Город: {profile[3] or 'не указан'}")
    else:
        print("❌ Профиль не найден")
        exit(1)

    print(f"\n⚡ Симулируем обновление интересов (добавляем 'бег')...")

    # Имитируем логику update_profile из handlers.py
    current_interests = profile[0] or ""
    interests_list = [i.strip() for i in current_interests.split(", ") if i.strip()]

    # Добавляем новый интерес
    new_interest = "бег"
    if new_interest not in interests_list:
        interests_list.append(new_interest)

    updated_interests = ", ".join(interests_list)

    # Обновляем в базе данных
    conn.execute(text('''
        UPDATE user_profiles
        SET interests = :interests, updated_at = CURRENT_TIMESTAMP
        WHERE user_id = :user_id
    '''), {'interests': updated_interests, 'user_id': user_id})

    conn.commit()

    print(f"✅ Интересы обновлены: '{current_interests}' → '{updated_interests}'")

    # Проверяем результат
    result = conn.execute(text('''
        SELECT interests FROM user_profiles WHERE user_id = :user_id
    '''), {'user_id': user_id})

    updated_profile = result.fetchone()
    print(f"🔍 Проверка после обновления: {updated_profile[0] if updated_profile else 'ошибка'}")

    print("\n🎉 Тест завершен успешно!")