import os
os.environ['LOCAL'] = '1'

from models import User, UserProfile, Subscription, SessionLocal

session = SessionLocal()

# Получаем всех пользователей
users = session.query(User).all()

print("Состояние базы данных:\n")

for user in users:
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    
    tier = subscription.tier.value if subscription and subscription.tier else 'LIGHT'
    interests = profile.interests if profile and profile.interests else 'нет'
    skills = profile.skills if profile and profile.skills else 'нет'
    
    print(f"User {user.id} (@{user.username}):")
    print(f"  Тариф: {tier}")
    print(f"  Интересы: {interests}")
    print(f"  Навыки: {skills}")
    print()

session.close()
