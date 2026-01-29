"""Тестирование рекомендаций для PREMIUM пользователей"""
from models import Session, User, Subscription, UserProfile, SubscriptionTier
from ai_integration.handlers import get_partners_list

# Открываем сессию
session = Session()

# Находим PREMIUM пользователей
premium_users = session.query(User).join(Subscription).filter(Subscription.tier == SubscriptionTier.PREMIUM).all()

print(f"Найдено {len(premium_users)} пользователей с тарифом PREMIUM\n\n")

for user in premium_users:
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    subscription = session.query(Subscription).filter_by(user_id=user.id, status='active').first()
    
    print("="*60)
    print(f"Пользователь: @{user.username} (ID: {user.id})")
    print(f"Интересы: {profile.interests if profile and profile.interests else 'не указаны'}")
    print(f"Навыки: {profile.skills if profile and profile.skills else 'не указаны'}")
    print()
    
    # Получаем партнеров
    partners = get_partners_list(user.id, session)
    
    print(f"Рекомендовано партнеров: {len(partners)}")
    print()
    
    if partners:
        print("Партнеры:")
        for partner in partners:
            partner_user = session.query(User).filter_by(id=partner.user_id).first()
            partner_sub = session.query(Subscription).filter_by(user_id=partner.user_id, status='active').first()
            partner_tier = partner_sub.tier.value if partner_sub else 'LIGHT'
            username = partner_user.username if partner_user else 'unknown'
            print(f"  - @{username} ({partner_tier}): {partner.interests if partner.interests else 'не указаны'}")
            if partner.common_interests:
                print(f"    Общие интересы: {partner.common_interests}")
        print()
    else:
        print("Партнеров не найдено")

session.close()
