"""
Скрипт для обновления пользователей в БД:
- 5 пользователей с разными тарифами
- 3 пользователя с интересом "спорт"
- 2 пользователя с интересом "бизнес"
"""
import os
os.environ['LOCAL'] = '1'

from models import User, UserProfile, Subscription, SubscriptionTier, SessionLocal
from datetime import datetime, timedelta

def update_users():
    session = SessionLocal()
    try:
        # Получаем всех пользователей
        users = session.query(User).order_by(User.id).all()
        
        if len(users) < 5:
            print(f"⚠️ В БД только {len(users)} пользователей, нужно минимум 5")
            return
        
        # Берем первых 5 пользователей
        users = users[:5]
        
        print(f"📊 Обновляем {len(users)} пользователей\n")
        
        # Распределение тарифов: LIGHT, STANDARD, PREMIUM, LIGHT, STANDARD
        tiers = [
            SubscriptionTier.LIGHT,
            SubscriptionTier.STANDARD,
            SubscriptionTier.PREMIUM,
            SubscriptionTier.LIGHT,
            SubscriptionTier.STANDARD
        ]
        
        # Интересы: 3 пользователя - спорт, 2 - бизнес
        interests = ["спорт", "спорт", "спорт", "бизнес", "бизнес"]
        
        for i, user in enumerate(users):
            tier = tiers[i]
            interest = interests[i]
            
            # Обновляем или создаем подписку
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            if subscription:
                subscription.tier = tier
                subscription.is_active = True
                subscription.expires_at = datetime.now() + timedelta(days=365)
            else:
                subscription = Subscription(
                    user_id=user.id,
                    tier=tier,
                    is_active=True,
                    started_at=datetime.now(),
                    expires_at=datetime.now() + timedelta(days=365)
                )
                session.add(subscription)
            
            # Обновляем или создаем профиль
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                # Просто заменяем интересы
                profile.interests = interest
            else:
                profile = UserProfile(
                    user_id=user.id,
                    interests=interest,
                    city="Москва"
                )
                session.add(profile)
            
            print(f"👤 User {user.id} ({user.username or user.first_name}):")
            print(f"   Тариф: {tier.value}")
            print(f"   Интересы: {interest}")
            print()
        
        session.commit()
        print("✅ Все пользователи обновлены успешно!")
        
        # Показываем итоговое состояние
        print("\n" + "="*50)
        print("ИТОГОВОЕ СОСТОЯНИЕ БД:")
        print("="*50)
        for user in users:
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            print(f"\n👤 {user.username or user.first_name} (ID: {user.id})")
            print(f"   📱 Telegram: @{user.username or 'no_username'}")
            print(f"   💳 Тариф: {subscription.tier.value if subscription else 'LIGHT'}")
            print(f"   🎯 Интересы: {profile.interests if profile and profile.interests else 'нет'}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    update_users()
