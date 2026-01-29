"""Тестирование рекомендаций для LIGHT пользователей"""
from models import Session, User, Subscription, UserProfile, SubscriptionTier
from ai_integration.handlers import get_partners_list

# Открываем сессию
session = Session()

# Находим LIGHT пользователей с интересами
light_users = session.query(User).join(Subscription).join(UserProfile).filter(
    Subscription.tier == SubscriptionTier.LIGHT,
    UserProfile.interests.isnot(None),
    UserProfile.interests != ''
).all()

print(f"Найдено {len(light_users)} пользователей с тарифом LIGHT и интересами\n\n")

for user in light_users[:5]:  # Первые 5
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
        print("❌ ОШИБКА: LIGHT пользователи не должны видеть рекомендации!")
        print("Партнеры:")
        for partner in partners[:3]:
            partner_user = session.query(User).filter_by(id=partner.user_id).first()
            print(f"  - @{partner_user.username if partner_user else 'unknown'}")
        print()
    else:
        print("✅ OK: Партнеров не найдено (как и должно быть для LIGHT)")

session.close()
