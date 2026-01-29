"""Добавление тестовых пользователей в Railway БД"""
import os
import sys
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile, Subscription, SubscriptionTier, Base

# Получаем DATABASE_URL для Railway (через railway CLI она автоматически подставляется)
database_url = os.getenv('DATABASE_URL')
if not database_url:
    print("❌ ОШИБКА: Переменная DATABASE_URL не найдена!")
    print("Запусти: railway run python add_railway_users.py")
    sys.exit(1)

# Создаем подключение к Railway БД
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

print(f"🔗 Подключаемся к Railway БД...")
engine = create_engine(database_url)
Session = sessionmaker(bind=engine)
session = Session()

# Данные пользователей
sport_users = [
    {'username': 'sport_alex', 'telegram_id': 1000001, 'interests': 'футбол, баскетбол, волейбол', 'tier': SubscriptionTier.LIGHT},
    {'username': 'sport_maria', 'telegram_id': 1000002, 'interests': 'бег, йога, пилатес', 'tier': SubscriptionTier.STANDARD},
    {'username': 'sport_ivan', 'telegram_id': 1000003, 'interests': 'теннис, плавание, велоспорт', 'tier': SubscriptionTier.PREMIUM},
    {'username': 'sport_olga', 'telegram_id': 1000004, 'interests': 'фитнес, кроссфит, бодибилдинг', 'tier': SubscriptionTier.LIGHT},
    {'username': 'sport_dmitry', 'telegram_id': 1000005, 'interests': 'хоккей, биатлон, лыжи', 'tier': SubscriptionTier.STANDARD},
]

business_users = [
    {'username': 'biz_anna', 'telegram_id': 2000001, 'interests': 'стартапы, маркетинг, продажи', 'tier': SubscriptionTier.PREMIUM},
    {'username': 'biz_sergey', 'telegram_id': 2000002, 'interests': 'инвестиции, финансы, криптовалюта', 'tier': SubscriptionTier.LIGHT},
    {'username': 'biz_elena', 'telegram_id': 2000003, 'interests': 'управление проектами, agile, scrum', 'tier': SubscriptionTier.STANDARD},
    {'username': 'biz_maxim', 'telegram_id': 2000004, 'interests': 'e-commerce, онлайн-торговля, логистика', 'tier': SubscriptionTier.PREMIUM},
    {'username': 'biz_victoria', 'telegram_id': 2000005, 'interests': 'HR, рекрутинг, обучение персонала', 'tier': SubscriptionTier.LIGHT},
]

all_users = sport_users + business_users

print(f"\n📝 Добавляем {len(all_users)} пользователей...")

for user_data in all_users:
    # Проверяем существует ли пользователь
    existing = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
    if existing:
        print(f"⚠️  @{user_data['username']} уже существует, пропускаем")
        continue
    
    # Создаем пользователя
    user = User(
        telegram_id=user_data['telegram_id'],
        username=user_data['username'],
        subscription_tier=user_data['tier'],
        created_at=datetime.now(timezone.utc)
    )
    session.add(user)
    session.flush()
    
    # Создаем профиль
    profile = UserProfile(
        user_id=user.id,
        interests=user_data['interests'],
        skills='',
        goals='',
        created_at=datetime.now(timezone.utc)
    )
    session.add(profile)
    
    # Создаем подписку
    end_date = datetime.now(timezone.utc) + timedelta(days=365)  # На год
    subscription = Subscription(
        user_id=user.id,
        telegram_id=user_data['telegram_id'],
        telegram_username=user_data['username'],
        username=user_data['username'],
        status='active',
        plan='yearly',
        tier=user_data['tier'],
        start_date=datetime.now(timezone.utc),
        end_date=end_date,
        login_count=1,
        created_at=datetime.now(timezone.utc)
    )
    session.add(subscription)
    
    print(f"✅ @{user_data['username']} ({user_data['tier'].value}): {user_data['interests']}")

session.commit()
print(f"\n✅ Готово! Добавлено пользователей в Railway БД")

# Проверяем
total = session.query(User).count()
print(f"\n📊 Всего пользователей в БД: {total}")

session.close()
