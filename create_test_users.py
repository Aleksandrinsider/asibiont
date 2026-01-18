#!/usr/bin/env python3
"""
Создание 20 тестовых пользователей в базе данных Railway
- 10 пользователей с интересом "спорт"
- Разные тарифы: BRONZE, SILVER, GOLD
"""

import os
import random
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
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

print(f"🔗 Подключаемся к базе данных: {db_url[:50]}...")

# Создаем движок и сессию
engine = create_engine(db_url)
Session = sessionmaker(bind=engine)

def create_test_users():
    """Создание 20 тестовых пользователей"""

    # Списки для генерации данных
    first_names = [
        "Александр", "Мария", "Дмитрий", "Анна", "Сергей", "Елена", "Андрей", "Ольга",
        "Максим", "Татьяна", "Иван", "Наталья", "Алексей", "Юлия", "Владимир", "Екатерина",
        "Николай", "Ирина", "Михаил", "Светлана"
    ]

    cities = ["Москва", "Санкт-Петербург", "Екатеринбург", "Новосибирск", "Казань", "Нижний Новгород", "Челябинск", "Омск", "Самара", "Ростов-на-Дону"]

    companies = ["Яндекс", "Google", "Microsoft", "Apple", "Amazon", "Meta", "Tesla", "SpaceX", "Росатом", "Сбербанк"]

    positions = ["Разработчик", "Дизайнер", "Менеджер", "Аналитик", "Тестировщик", "DevOps", "Архитектор", "Продукт-менеджер", "Маркетолог", "HR"]

    # Интересы для пользователей без спорта
    other_interests = [
        "программирование", "дизайн", "музыка", "путешествия", "книги",
        "фотография", "кулинария", "искусство", "наука", "технологии"
    ]

    # Распределение тарифов (примерно равномерно)
    tiers = ["BRONZE"] * 7 + ["SILVER"] * 7 + ["GOLD"] * 6  # 7+7+6=20

    session = Session()

    try:
        print("🚀 Начинаем создание тестовых пользователей...")

        # Получаем максимальный ID пользователя для генерации telegram_id
        result = session.execute(text("SELECT COALESCE(MAX(id), 0) FROM users"))
        max_user_id = result.fetchone()[0]

        # Получаем максимальный subscriber_number
        result = session.execute(text("SELECT COALESCE(MAX(subscriber_number), 0) FROM subscriptions"))
        max_subscriber = result.fetchone()[0]

        created_users = []

        for i in range(20):
            user_id = max_user_id + i + 1
            telegram_id = 1000000 + user_id  # Генерируем уникальный telegram_id

            # Выбираем имя
            first_name = first_names[i % len(first_names)]

            # Генерируем username
            username = f"testuser{user_id}"

            # Выбираем тариф
            tier = tiers[i]

            # Определяем, будет ли у пользователя интерес "спорт" (первые 10)
            has_sport_interest = i < 10

            # Создаем пользователя
            user_data = {
                'id': user_id,
                'telegram_id': telegram_id,
                'username': username,
                'first_name': first_name,
                'subscription_tier': tier,
                'created_at': datetime.now(),
                'updated_at': datetime.now()
            }

            session.execute(text("""
                INSERT INTO users (id, telegram_id, username, first_name, subscription_tier, created_at, updated_at)
                VALUES (:id, :telegram_id, :username, :first_name, :subscription_tier, :created_at, :updated_at)
            """), user_data)

            # Создаем профиль пользователя
            profile_data = {
                'user_id': user_id,
                'city': random.choice(cities),
                'company': random.choice(companies),
                'position': random.choice(positions),
                'bio': f"Тестовый пользователь {user_id}",
                'languages': "Русский (родной), English (B2)",
                'total_tasks_created': random.randint(0, 50),
                'completed_tasks': random.randint(0, 40),
                'average_rating': random.randint(0, 10),
                'rating_count': random.randint(0, 20),
                'updated_at': datetime.now()
            }

            # Добавляем интересы
            if has_sport_interest:
                profile_data['interests'] = "спорт"
            else:
                profile_data['interests'] = random.choice(other_interests)

            session.execute(text("""
                INSERT INTO user_profiles (
                    user_id, city, company, position, bio, languages, interests,
                    total_tasks_created, completed_tasks, average_rating, rating_count, updated_at
                ) VALUES (
                    :user_id, :city, :company, :position, :bio, :languages, :interests,
                    :total_tasks_created, :completed_tasks, :average_rating, :rating_count, :updated_at
                )
            """), profile_data)

            # Создаем подписку
            subscriber_number = max_subscriber + i + 1
            start_date = datetime.now()
            end_date = start_date + timedelta(days=30)  # 30 дней подписки

            subscription_data = {
                'user_id': user_id,
                'telegram_id': telegram_id,
                'telegram_username': username,
                'username': username,
                'status': 'active',
                'plan': 'monthly',
                'tier': tier,
                'start_date': start_date,
                'end_date': end_date,
                'subscriber_number': subscriber_number,
                'created_at': start_date
            }

            session.execute(text("""
                INSERT INTO subscriptions (
                    user_id, telegram_id, telegram_username, username, status, plan, tier,
                    start_date, end_date, subscriber_number, created_at
                ) VALUES (
                    :user_id, :telegram_id, :telegram_username, :username, :status, :plan, :tier,
                    :start_date, :end_date, :subscriber_number, :created_at
                )
            """), subscription_data)

            created_users.append({
                'id': user_id,
                'username': username,
                'first_name': first_name,
                'tier': tier,
                'interests': profile_data['interests'],
                'city': profile_data['city']
            })

            print(f"✅ Создан пользователь {user_id}: {username} ({first_name}) - {tier} - {profile_data['interests']}")

        # Коммитим все изменения
        session.commit()

        print(f"\n🎉 Успешно создано {len(created_users)} тестовых пользователей!")
        print("\n📊 Статистика:")
        print(f"- BRONZE: {sum(1 for u in created_users if u['tier'] == 'BRONZE')}")
        print(f"- SILVER: {sum(1 for u in created_users if u['tier'] == 'SILVER')}")
        print(f"- GOLD: {sum(1 for u in created_users if u['tier'] == 'GOLD')}")
        print(f"- С интересом 'спорт': {sum(1 for u in created_users if u['interests'] == 'спорт')}")

    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка при создании пользователей: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    create_test_users()