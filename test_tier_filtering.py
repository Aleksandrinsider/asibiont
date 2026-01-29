"""Полный тест фильтрации по тарифам"""
from models import Session, User, Subscription, UserProfile, SubscriptionTier
from ai_integration.handlers import get_partners_list

session = Session()

print("="*70)
print("ТЕСТ ФИЛЬТРАЦИИ РЕКОМЕНДАЦИЙ ПО ТАРИФАМ")
print("="*70)

# Тест 1: LIGHT видят LIGHT и STANDARD (не видят PREMIUM)
print("\n1. LIGHT ПОЛЬЗОВАТЕЛИ (должны видеть LIGHT + STANDARD):")
print("-"*70)
test1 = session.query(User).filter_by(username='test1').first()
partners = get_partners_list(test1.id, session)
tiers = {}
for p in partners:
    pu = session.query(User).filter_by(id=p.user_id).first()
    sub = session.query(Subscription).filter_by(user_id=p.user_id, status='active').first()
    tier = sub.tier.value if sub else 'LIGHT'
    tiers[tier] = tiers.get(tier, 0) + 1

print(f"@test1 (LIGHT) видит: {dict(tiers)}")
if 'PREMIUM' in tiers:
    print("❌ ОШИБКА: видит PREMIUM пользователей!")
else:
    print("✅ OK: не видит PREMIUM")

# Тест 2: STANDARD видят LIGHT и STANDARD (не видят PREMIUM)
print("\n2. STANDARD ПОЛЬЗОВАТЕЛИ (должны видеть LIGHT + STANDARD):")
print("-"*70)
test2 = session.query(User).filter_by(username='test2').first()
partners = get_partners_list(test2.id, session)
tiers = {}
for p in partners:
    pu = session.query(User).filter_by(id=p.user_id).first()
    sub = session.query(Subscription).filter_by(user_id=p.user_id, status='active').first()
    tier = sub.tier.value if sub else 'LIGHT'
    tiers[tier] = tiers.get(tier, 0) + 1

print(f"@test2 (STANDARD) видит: {dict(tiers)}")
if 'PREMIUM' in tiers:
    print("❌ ОШИБКА: видит PREMIUM пользователей!")
else:
    print("✅ OK: не видит PREMIUM")

if 'STANDARD' in tiers:
    print("✅ OK: видит других STANDARD пользователей")
else:
    print("⚠️  Не видит других STANDARD (возможно нет совпадений)")

# Тест 3: PREMIUM видят всех
print("\n3. PREMIUM ПОЛЬЗОВАТЕЛИ (должны видеть LIGHT + STANDARD + PREMIUM):")
print("-"*70)
test3 = session.query(User).filter_by(username='test3').first()
partners = get_partners_list(test3.id, session)
tiers = {}
for p in partners:
    pu = session.query(User).filter_by(id=p.user_id).first()
    sub = session.query(Subscription).filter_by(user_id=p.user_id, status='active').first()
    tier = sub.tier.value if sub else 'LIGHT'
    tiers[tier] = tiers.get(tier, 0) + 1

print(f"@test3 (PREMIUM) видит: {dict(tiers)}")
if 'LIGHT' in tiers and 'STANDARD' in tiers and 'PREMIUM' in tiers:
    print("✅ OK: видит все тарифы")
else:
    print("❌ ОШИБКА: не видит все тарифы!")

print("\n" + "="*70)
print("РЕЗЮМЕ:")
print("="*70)
print("LIGHT → LIGHT + STANDARD (блок PREMIUM)")
print("STANDARD → LIGHT + STANDARD (блок PREMIUM)")
print("PREMIUM → LIGHT + STANDARD + PREMIUM (все)")

session.close()
