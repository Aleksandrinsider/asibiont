"""Добавление 5 тестовых пользователей с интересом "спорт" в Railway БД"""

import os
os.environ['LOCAL'] = '0'  # Используем Railway БД

from models import User, UserProfile, Subscription, SubscriptionTier
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL
import datetime

# Подключение к Railway БД
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

# Данные тестовых пользователей
test_users = [
    {
        "telegram_id": 111111001,
        "username": "sport_alex",
        "first_name": "Александр",
        "profile": {
            "city": "Москва",
            "skills": "бег на длинные дистанции, плавание, велоспорт",
            "interests": "спорт, триатлон, марафоны, здоровый образ жизни",
            "goals": "Пробежать марафон под 3 часа, участвовать в Ironman",
            "bio": "Увлекаюсь триатлоном уже 5 лет. Ищу партнеров для совместных тренировок и участия в соревнованиях.",
            "company": "Nike",
            "position": "Спортивный менеджер"
        }
    },
    {
        "telegram_id": 111111002,
        "username": "fitness_maria",
        "first_name": "Мария",
        "profile": {
            "city": "Санкт-Петербург",
            "skills": "фитнес, йога, пилатес, растяжка",
            "interests": "спорт, фитнес, йога, правильное питание, wellness",
            "goals": "Стать сертифицированным тренером по йоге, открыть свою студию",
            "bio": "Практикую йогу 8 лет, преподаю фитнес. Веду здоровый образ жизни и вдохновляю других.",
            "company": "World Class",
            "position": "Фитнес-тренер"
        }
    },
    {
        "telegram_id": 111111003,
        "username": "football_dmitry",
        "first_name": "Дмитрий",
        "profile": {
            "city": "Казань",
            "skills": "футбол, командная игра, тактика",
            "interests": "спорт, футбол, тренировки, футбольная аналитика",
            "goals": "Играть в любительской лиге, создать сильную команду",
            "bio": "Играю в футбол с детства. Организую любительские матчи по выходным.",
            "company": "Рубин",
            "position": "Спортивный аналитик"
        }
    },
    {
        "telegram_id": 111111004,
        "username": "gym_victor",
        "first_name": "Виктор",
        "profile": {
            "city": "Екатеринбург",
            "skills": "силовые тренировки, бодибилдинг, пауэрлифтинг",
            "interests": "спорт, тренажерный зал, набор массы, силовой спорт",
            "goals": "Жим 200 кг, становая 250 кг, участие в соревнованиях по пауэрлифтингу",
            "bio": "Профессионально занимаюсь пауэрлифтингом. Ищу партнера для совместных тренировок и страховки.",
            "company": "GymZone",
            "position": "Персональный тренер"
        }
    },
    {
        "telegram_id": 111111005,
        "username": "tennis_elena",
        "first_name": "Елена",
        "profile": {
            "city": "Сочи",
            "skills": "большой теннис, ракеточные виды спорта",
            "interests": "спорт, теннис, корты, спортивные турниры, активный отдых",
            "goals": "Выйти в финал городского турнира, повысить рейтинг до уровня B",
            "bio": "Играю в теннис 10 лет. Ищу партнеров для игры и участия в парных турнирах.",
            "company": "Теннисный клуб Олимп",
            "position": "Тренер по теннису"
        }
    }
]

print("🚀 Добавление тестовых пользователей в Railway БД...\n")

for user_data in test_users:
    # Проверяем, существует ли уже пользователь
    existing_user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
    
    if existing_user:
        print(f"⚠️  Пользователь @{user_data['username']} уже существует (ID: {existing_user.id})")
        continue
    
    # Создаем пользователя
    user = User(
        telegram_id=user_data["telegram_id"],
        username=user_data["username"],
        first_name=user_data["first_name"],
        subscription_tier=SubscriptionTier.STANDARD,  # Даем стандартную подписку
        created_at=datetime.datetime.now(datetime.timezone.utc)
    )
    session.add(user)
    session.flush()  # Получаем ID пользователя
    
    # Создаем профиль
    profile_data = user_data["profile"]
    profile = UserProfile(
        user_id=user.id,
        city=profile_data["city"],
        skills=profile_data["skills"],
        interests=profile_data["interests"],
        goals=profile_data["goals"],
        bio=profile_data["bio"],
        company=profile_data["company"],
        position=profile_data["position"],
        contact_info=user_data["username"]
    )
    session.add(profile)
    
    # Создаем активную подписку
    subscription = Subscription(
        user_id=user.id,
        telegram_id=user_data["telegram_id"],
        telegram_username=user_data["username"],
        username=user_data["username"],
        status='active',
        plan='monthly',
        tier=SubscriptionTier.STANDARD,
        start_date=datetime.datetime.now(datetime.timezone.utc),
        end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    )
    session.add(subscription)
    
    print(f"✅ Добавлен: @{user_data['username']} - {user_data['first_name']} ({profile_data['city']})")
    print(f"   Интересы: {profile_data['interests']}")
    print(f"   Подписка: STANDARD (активна)")
    print(f"   Цели: {profile_data['goals']}\n")

# Сохраняем изменения
session.commit()
print("✨ Готово! Все пользователи добавлены в Railway БД")

# Проверяем
total_users = session.query(User).count()
sport_users = session.query(UserProfile).filter(UserProfile.interests.like('%спорт%')).count()
print(f"\n📊 Статистика:")
print(f"   Всего пользователей: {total_users}")
print(f"   Пользователей с интересом 'спорт': {sport_users}")

session.close()
