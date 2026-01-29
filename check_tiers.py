"""
Проверка тарифов пользователей
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, SubscriptionTier
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 ПРОВЕРКА ТАРИФОВ")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    fitness_maria = session.query(User).filter_by(username="fitness_maria").first()
    
    print(f"\n👤 @aleksandrinsider:")
    print(f"   Тариф: {aleksandr.subscription_tier}")
    print(f"   Значение: {aleksandr.subscription_tier.value if aleksandr.subscription_tier else 'None'}")
    
    print(f"\n👤 @fitness_maria:")
    if fitness_maria:
        print(f"   Тариф: {fitness_maria.subscription_tier}")
        print(f"   Значение: {fitness_maria.subscription_tier.value if fitness_maria.subscription_tier else 'None'}")
    else:
        print(f"   ❌ Пользователь не найден!")
    
    print(f"\n" + "=" * 80)
    print("🎯 ПРОБЛЕМА:")
    print("=" * 80)
    
    if aleksandr.subscription_tier == SubscriptionTier.STANDARD:
        print(f"\n   @aleksandrinsider имеет тариф STANDARD")
        print(f"\n   По логике в api_partners_handler (строка 3619):")
        print(f"   can_access = (delegatee_tier_str.lower() in ['light', 'standard'])")
        print(f"\n   То есть STANDARD видит только:")
        print(f"   ✅ LIGHT контакты")
        print(f"   ✅ STANDARD контакты")
        print(f"   ❌ PREMIUM контакты")
        
        if fitness_maria and fitness_maria.subscription_tier == SubscriptionTier.PREMIUM:
            print(f"\n   ❌ @fitness_maria имеет тариф PREMIUM!")
            print(f"   ❌ can_access = False")
            print(f"   ❌ Контакт НЕ добавляется в delegating_by_me!")
            print(f"\n💡 РЕШЕНИЕ:")
            print(f"   1. Изменить логику can_access для delegating_by_me")
            print(f"   2. Делегированные контакты должны ВСЕГДА отображаться")
            print(f"   3. Или обновить тариф @fitness_maria на STANDARD")
        elif fitness_maria and fitness_maria.subscription_tier in [SubscriptionTier.LIGHT, SubscriptionTier.STANDARD]:
            print(f"\n   ✅ @fitness_maria имеет тариф {fitness_maria.subscription_tier.value}")
            print(f"   ✅ can_access = True")
            print(f"   ✅ Контакт ДОЛЖЕН добавляться!")
            print(f"\n   ⚠️ Значит проблема в другом месте")

finally:
    session.close()

print("=" * 80)
