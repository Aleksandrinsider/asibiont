"""
Скрипт для добавления безлимитных промокодов (по 1 на пользователя)
Создает 3 промокода - по одному на каждый тариф
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from models import PromoCode, SubscriptionTier
from datetime import datetime, timedelta

# Railway PostgreSQL credentials
DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

def create_unlimited_promo_codes():
    """Создать безлимитные промокоды"""
    
    print("=" * 70)
    print("🎟️  СОЗДАНИЕ БЕЗЛИМИТНЫХ ПРОМОКОДОВ")
    print("=" * 70)
    
    engine = create_engine(DATABASE_URL)
    session = Session(engine)
    
    try:
        # Проверить существующие промокоды
        existing_codes = session.query(PromoCode.code).all()
        existing_codes = [code[0] for code in existing_codes]
        
        print(f"\n📋 Существующие промокоды: {len(existing_codes)}")
        for code in existing_codes:
            print(f"   - {code}")
        
        # Промокоды для создания
        promo_codes_data = [
            {
                'code': 'LIGHT1MONTH',
                'tier': SubscriptionTier.LIGHT,
                'duration_days': 30
            },
            {
                'code': 'STANDARD1MONTH',
                'tier': SubscriptionTier.STANDARD,
                'duration_days': 30
            },
            {
                'code': 'PREMIUM1MONTH',
                'tier': SubscriptionTier.PREMIUM,
                'duration_days': 30
            }
        ]
        
        created = []
        skipped = []
        
        print("\n🔨 Создание промокодов...")
        
        for promo_data in promo_codes_data:
            code = promo_data['code']
            
            # Проверить, существует ли уже
            if code in existing_codes:
                print(f"\n⚠️  Промокод {code} уже существует - пропускаем")
                skipped.append(code)
                continue
            
            # Создать промокод
            promo = PromoCode(
                code=code,
                tier=promo_data['tier'],
                duration_days=promo_data['duration_days'],
                is_used=False,
                used_count=0,
                used_by_users='[]',  # Пустой JSON массив
                max_uses=None,  # None = безлимитное количество использований
                expires_at=datetime.utcnow() + timedelta(days=365*10)  # 10 лет = почти навсегда
            )
            
            session.add(promo)
            created.append(code)
            
            print(f"\n✅ Создан: {code}")
            print(f"   Тариф: {promo_data['tier'].value}")
            print(f"   Длительность: {promo_data['duration_days']} дней")
            print(f"   Макс использований: Безлимит")
            print(f"   Использований на пользователя: 1")
        
        # Сохранить изменения
        if created:
            session.commit()
            print(f"\n💾 Сохранено в базу данных")
        
        # Итоговая статистика
        print("\n" + "=" * 70)
        print("📊 ИТОГИ")
        print("=" * 70)
        print(f"✅ Создано промокодов: {len(created)}")
        if created:
            for code in created:
                print(f"   - {code}")
        
        print(f"⚠️  Пропущено (уже существуют): {len(skipped)}")
        if skipped:
            for code in skipped:
                print(f"   - {code}")
        
        # Показать все промокоды в БД
        print("\n📋 Все промокоды в системе:")
        all_promos = session.query(PromoCode).all()
        
        for promo in all_promos:
            status = "🔓 Безлимит" if promo.max_uses is None else f"🔢 {promo.max_uses} макс"
            
            # Парсим JSON для подсчета использований
            import json
            try:
                used_by_list = json.loads(promo.used_by_users)
                uses = len(used_by_list) if used_by_list else 0
            except:
                uses = promo.used_count
            
            print(f"\n   {promo.code}")
            print(f"      Тариф: {promo.tier.value}")
            print(f"      Длительность: {promo.duration_days} дней")
            print(f"      Статус: {status}")
            print(f"      Использований: {uses} пользователей")
            print(f"      Действителен до: {promo.expires_at.strftime('%Y-%m-%d')}")
        
        print("\n" + "=" * 70)
        print("✨ Готово! Промокоды можно использовать в боте:")
        print("   /promo LIGHT1MONTH")
        print("   /promo STANDARD1MONTH")
        print("   /promo PREMIUM1MONTH")
        print("=" * 70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    create_unlimited_promo_codes()
