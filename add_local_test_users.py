"""Добавление тестовых пользователей в локальную БД"""
import os
os.environ['LOCAL'] = '1'  # Форсируем локальный режим

from datetime import datetime, timezone, timedelta
from models import Session, User, UserProfile, Subscription, SubscriptionTier

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

print(f"📝 Добавляем {len(all_users)} пользователей в локальную БД...\n")

added = 0
skipped = 0

for user_data in all_users:
    existing = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
    if existing:
        print(f"⚠️  @{user_data['username']} уже существует")
        skipped += 1
        continue
    
    user = User(
        telegram_id=user_data['telegram_id'],
        username=user_data['username'],
        subscription_tier=user_data['tier'],
        created_at=datetime.now(timezone.utc)
    )
    session.add(user)
    session.flush()
    
    profile = UserProfile(
        user_id=user.id,
        interests=user_data['interests'],
        skills='',
        goals=''
    )
    session.add(profile)
    
    end_date = datetime.now(timezone.utc) + timedelta(days=365)
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
    added += 1

session.commit()

print(f"\n{'='*60}")
print(f"Добавлено: {added}")
print(f"Пропущено: {skipped}")
print(f"Всего в БД: {session.query(User).count()} пользователей")
print(f"Всего subscriptions: {session.query(Subscription).count()}")

session.close()
