"""
Скрипт для добавления эксклюзивного промокода Standard на 1 год
Ограничение: только 1 использование для 1 пользователя
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from models import PromoCode, SubscriptionTier
from datetime import datetime, timedelta
import json

# Railway PostgreSQL credentials
DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

def create_exclusive_standard_promo():
    """Создать эксклюзивный промокод Standard на год"""
    
    print("=" * 70)
    print("🎁 СОЗДАНИЕ ЭКСКЛЮЗИВНОГО ПРОМОКОДА STANDARD")
    print("=" * 70)
    
    engine = create_engine(DATABASE_URL)
    session = Session(engine)
    
    try:
        # Проверить существующие промокоды
        existing_codes = session.query(PromoCode.code).all()
        existing_codes = [code[0] for code in existing_codes]
        
        print(f"\n📋 Существующие промокоды: {len(existing_codes)}")
        
        # Эксклюзивный промокод
        exclusive_code = 'STANDARD1YEAR'
        
        if exclusive_code in existing_codes:
            print(f"\n⚠️  Промокод {exclusive_code} уже существует!")
            
            # Показать информацию о существующем
            existing_promo = session.query(PromoCode).filter_by(code=exclusive_code).first()
            if existing_promo:
                used_by_list = json.loads(existing_promo.used_by_users or '[]')
                uses = len(used_by_list)
                
                print(f"   Тариф: {existing_promo.tier.value}")
                print(f"   Длительность: {existing_promo.duration_days} дней")
                print(f"   Макс использований: {existing_promo.max_uses}")
                print(f"   Использований: {uses}/{existing_promo.max_uses}")
                print(f"   Статус: {'🔴 Исчерпан' if uses >= existing_promo.max_uses else '🟢 Доступен'}")
            
            session.close()
            return
        
        print(f"\n🔨 Создание эксклюзивного промокода...")
        
        # Создать промокод
        promo = PromoCode(
            code=exclusive_code,
            tier=SubscriptionTier.STANDARD,
            duration_days=365,  # 1 год
            is_used=False,
            used_count=0,
            used_by_users='[]',  # Пустой JSON массив
            max_uses=1,  # ТОЛЬКО ОДНО использование
            expires_at=datetime.utcnow() + timedelta(days=365*2)  # Действителен 2 года
        )
        
        session.add(promo)
        session.commit()
        
        print(f"\n✅ Создан: {exclusive_code}")
        print(f"   🥈 Тариф: STANDARD")
        print(f"   ⏰ Длительность: 365 дней (1 год)")
        print(f"   🎯 Макс использований: 1 (эксклюзивный)")
        print(f"   📅 Действителен до: {promo.expires_at.strftime('%Y-%m-%d')}")
        print(f"   💎 Ценность: 9000₽ × 12 = 108,000₽")
        
        # Показать все промокоды
        print(f"\n📋 Все промокоды в системе:")
        all_promos = session.query(PromoCode).order_by(PromoCode.tier, PromoCode.duration_days.desc()).all()
        
        for promo in all_promos:
            # Определить тип промокода
            if promo.max_uses is None:
                status = "🔓 Безлимит"
            elif promo.max_uses == 1:
                status = "💎 Эксклюзивный"
            else:
                status = f"🔢 {promo.max_uses} макс"
            
            # Парсим JSON для подсчета использований
            try:
                used_by_list = json.loads(promo.used_by_users)
                uses = len(used_by_list) if used_by_list else 0
            except:
                uses = promo.used_count
            
            # Определить доступность
            if promo.max_uses and uses >= promo.max_uses:
                availability = "🔴 Исчерпан"
            else:
                availability = "🟢 Доступен"
            
            print(f"\n   {promo.code}")
            print(f"      Тариф: {promo.tier.value}")
            print(f"      Длительность: {promo.duration_days} дней")
            print(f"      Тип: {status}")
            print(f"      Использований: {uses}")
            if promo.max_uses:
                print(f"      Статус: {availability}")
        
        print(f"\n" + "=" * 70)
        print("✨ ЭКСКЛЮЗИВНЫЙ ПРОМОКОД СОЗДАН!")
        print("=" * 70)
        print(f"🎁 Код: STANDARD1YEAR")
        print(f"🥈 Тариф: Standard (9000₽/мес)")
        print(f"⏰ Подписка: 1 год (365 дней)")
        print(f"💎 Ценность: 108,000₽")
        print(f"🎯 Ограничение: ТОЛЬКО 1 использование!")
        print(f"\nИспользование: /promo STANDARD1YEAR")
        print("=" * 70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    create_exclusive_standard_promo()