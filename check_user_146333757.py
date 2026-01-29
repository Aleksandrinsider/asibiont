"""Проверка пользователя 146333757"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Subscription
from ai_integration.handlers import get_partners_list

session = Session()

user_id = 146333757
print(f"Проверка пользователя с telegram_id: {user_id}")
print("="*70)

# Найти пользователя
user = session.query(User).filter_by(telegram_id=user_id).first()
if not user:
    print(f"❌ Пользователь с telegram_id={user_id} НЕ НАЙДЕН в БД!")
    session.close()
    exit()

print(f"✓ Пользователь найден: ID={user.id}, Username={user.username or 'N/A'}")

# Проверить подписку
subscription = session.query(Subscription).filter_by(user_id=user.id, status='active').first()
if subscription:
    print(f"✓ Подписка: {subscription.tier} | Статус: {subscription.status}")
    print(f"  Начало: {subscription.start_date}")
    print(f"  Окончание: {subscription.end_date}")
else:
    print("❌ ПОДПИСКА НЕ НАЙДЕНА или не active!")
    all_subs = session.query(Subscription).filter_by(user_id=user.id).all()
    if all_subs:
        print(f"  Найдено подписок (любых статусов): {len(all_subs)}")
        for sub in all_subs:
            print(f"    - {sub.tier} | {sub.status} | {sub.start_date} - {sub.end_date}")

# Проверить профиль
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
if profile:
    print(f"\n✓ Профиль найден:")
    print(f"  Интересы: {profile.interests or '❌ НЕ УКАЗАНЫ'}")
    print(f"  Навыки: {profile.skills or 'не указаны'}")
    print(f"  Город: {profile.city or 'не указан'}")
    print(f"  О себе: {profile.about or 'не указано'}")
else:
    print("\n❌ ПРОФИЛЬ НЕ НАЙДЕН!")

# Проверить рекомендации
print("\n" + "="*70)
print("ПРОВЕРКА РЕКОМЕНДАЦИЙ")
print("="*70)

try:
    partners = get_partners_list(user_id=user.id, session=session)
    print(f"\nНайдено рекомендаций: {len(partners)}")
    
    if partners:
        print("\nПервые 5 рекомендаций:")
        for i, partner in enumerate(partners[:5], 1):
            partner_profile = session.query(UserProfile).filter_by(user_id=partner['user_id']).first()
            print(f"{i}. {partner.get('username', 'N/A')}")
            print(f"   Интересы: {partner_profile.interests if partner_profile else 'N/A'}")
            print(f"   Причина: {partner.get('match_reason', 'N/A')}")
    else:
        print("\n❌ РЕКОМЕНДАЦИЙ НЕТ")
        print("\nВозможные причины:")
        if not profile or not profile.interests:
            print("  1. ❌ У вас не указаны интересы в профиле")
        if not subscription:
            print("  2. ❌ Нет активной подписки")
        
        # Проверим сколько всего пользователей с интересами
        users_with_interests = session.query(UserProfile).filter(
            UserProfile.interests.isnot(None),
            UserProfile.interests != ''
        ).count()
        print(f"\n  Всего пользователей с интересами в БД: {users_with_interests}")
        
except Exception as e:
    print(f"❌ ОШИБКА при получении рекомендаций: {e}")
    import traceback
    traceback.print_exc()

session.close()
