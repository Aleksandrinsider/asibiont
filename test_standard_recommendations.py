import os
os.environ['LOCAL'] = '1'

from ai_integration.handlers import get_partners_list
from models import User, UserProfile, Subscription, SessionLocal

session = SessionLocal()

# Проверяем для нескольких пользователей STANDARD
standard_users = session.query(User).join(Subscription).filter(
    Subscription.tier == 'STANDARD'
).all()

print(f"Найдено {len(standard_users)} пользователей с тарифом STANDARD\n")

for user in standard_users[:3]:  # Проверим первых 3
    print(f"\n{'='*60}")
    print(f"Пользователь: @{user.username} (ID: {user.id})")
    
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        print(f"Интересы: {profile.interests or 'не указаны'}")
        print(f"Навыки: {profile.skills or 'не указаны'}")
    else:
        print("Профиль не заполнен")
    
    # Получаем рекомендации
    partners = get_partners_list(user.id, session)
    print(f"\nРекомендовано партнеров: {len(partners)}")
    
    if partners:
        print("\nПартнеры:")
        for p in partners[:5]:
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            partner_subscription = session.query(Subscription).filter_by(user_id=p.user_id).first()
            tier = partner_subscription.tier.value if partner_subscription else 'LIGHT'
            print(f"  - @{partner_user.username} ({tier}): {p.interests or 'нет интересов'}")
            if p.common_interests:
                print(f"    Общие интересы: {p.common_interests}")
    else:
        print("Партнеров не найдено")

session.close()
