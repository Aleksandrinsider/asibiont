"""Проверка рекомендаций пользователей"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Subscription
from sqlalchemy import func

session = Session()

print("="*70)
print("ПОЛЬЗОВАТЕЛИ В БД")
print("="*70)

users = session.query(User).all()
print(f"\nВсего пользователей: {len(users)}\n")

for user in users:
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    
    print(f"ID: {user.id} | TG: {user.telegram_id} | Username: {user.username or 'N/A'}")
    print(f"  Интересы: {profile.interests if profile and profile.interests else 'не указаны'}")
    print(f"  Навыки: {profile.skills if profile and profile.skills else 'не указаны'}")
    print(f"  Город: {profile.city if profile and profile.city else 'не указан'}")
    print(f"  Подписка: {subscription.tier if subscription else 'N/A'} | Статус: {subscription.status if subscription else 'N/A'}")
    print()

# Проверяем логику рекомендаций для конкретного пользователя
print("="*70)
print("ПРОВЕРКА РЕКОМЕНДАЦИЙ ДЛЯ test_user")
print("="*70)

test_user = session.query(User).filter_by(username='test_user').first()
if test_user:
    print(f"\nПользователь: {test_user.username} (ID: {test_user.telegram_id})")
    test_profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
    test_sub = session.query(Subscription).filter_by(user_id=test_user.id).first()
    
    print(f"Интересы: {test_profile.interests if test_profile and test_profile.interests else 'не указаны'}")
    print(f"Подписка: {test_sub.tier if test_sub else 'N/A'}")
    
    # Проверяем потенциальных партнеров
    print("\n--- Анализ потенциальных партнеров ---")
    
    other_users = session.query(User).filter(User.id != test_user.id).all()
    print(f"Других пользователей в БД: {len(other_users)}\n")
    
    for other in other_users:
        other_profile = session.query(UserProfile).filter_by(user_id=other.id).first()
        other_sub = session.query(Subscription).filter_by(user_id=other.id).first()
        
        print(f"{other.username or f'User_{other.id}'}")
        print(f"  Интересы: {other_profile.interests if other_profile and other_profile.interests else 'EMPTY'}")
        print(f"  Подписка: {other_sub.tier if other_sub else 'NO_SUB'}")
        
        # Проверка фильтрации
        if not other_sub:
            print(f"  ❌ Нет подписки - будет отфильтрован")
        elif test_sub and test_sub.tier in ['LIGHT', 'STANDARD']:
            if other_sub.tier == 'PREMIUM':
                print(f"  ❌ PREMIUM пользователь - будет отфильтрован (test_user tier: {test_sub.tier})")
            else:
                print(f"  ✅ Подписка подходит")
        elif test_sub and test_sub.tier == 'PREMIUM':
            print(f"  ✅ Подписка подходит (PREMIUM видит всех)")
        else:
            print(f"  ⚠️ test_user без подписки")
        
        # Проверка общих интересов
        if test_profile and test_profile.interests and other_profile and other_profile.interests:
            test_interests_set = set(i.strip().lower() for i in test_profile.interests.split(','))
            other_interests_set = set(i.strip().lower() for i in other_profile.interests.split(','))
            common = test_interests_set & other_interests_set
            if common:
                print(f"  ✅ Общие интересы: {common}")
            else:
                print(f"  ❌ Нет общих интересов")
        else:
            print(f"  ❌ Интересы не указаны у одного из пользователей")
        print()
else:
    print("test_user не найден в БД")

session.close()
