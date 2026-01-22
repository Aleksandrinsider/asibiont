# -*- coding: utf-8 -*-
"""Создание промокодов для каждого тарифа"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import SessionLocal, PromoCode, SubscriptionTier

def create_promo_codes():
    """Создать промокоды на каждый тариф"""
    db = SessionLocal()
    try:
        print("=" * 60)
        print("СОЗДАНИЕ ПРОМОКОДОВ")
        print("=" * 60)
        
        expires_at = datetime.datetime(2026, 2, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        
        promo_codes_data = [
            {
                'code': 'BRONZE2026',
                'tier': SubscriptionTier.BRONZE,
                'discount_percent': 0,
                'max_uses': None,  # Без лимита
                'duration_days': 30,  # 1 месяц
                'expires_at': expires_at,
                'is_used': False,
                'used_count': 0,
                'used_by_users': '[]'
            },
            {
                'code': 'SILVER2026',
                'tier': SubscriptionTier.SILVER,
                'discount_percent': 0,
                'max_uses': None,  # Без лимита
                'duration_days': 30,  # 1 месяц
                'expires_at': expires_at,
                'is_used': False,
                'used_count': 0,
                'used_by_users': '[]'
            },
            {
                'code': 'GOLD2026',
                'tier': SubscriptionTier.GOLD,
                'discount_percent': 0,
                'max_uses': None,  # Без лимита
                'duration_days': 30,  # 1 месяц
                'expires_at': expires_at,
                'is_used': False,
                'used_count': 0,
                'used_by_users': '[]'
            }
        ]
        
        for promo_data in promo_codes_data:
            # Проверяем, не существует ли уже такой промокод
            existing = db.query(PromoCode).filter_by(code=promo_data['code']).first()
            if existing:
                print(f"⚠ Промокод {promo_data['code']} уже существует, пропускаем")
                continue
            
            promo = PromoCode(**promo_data)
            db.add(promo)
            print(f"✓ Создан промокод: {promo_data['code']} ({promo_data['tier'].value})")
        
        db.commit()
        
        print("\n" + "=" * 60)
        print("✅ ПРОМОКОДЫ СОЗДАНЫ")
        print("=" * 60)
        
        # Показываем все активные промокоды
        all_promos = db.query(PromoCode).filter(PromoCode.expires_at > datetime.datetime.now(datetime.timezone.utc)).all()
        print("\nАктивные промокоды:")
        for promo in all_promos:
            uses_info = f"Без лимита" if promo.max_uses is None else f"{promo.used_count}/{promo.max_uses}"
            print(f"  {promo.code} - {promo.tier.value} - {promo.duration_days} дней - {uses_info}")
            print(f"    Срок действия до: {promo.expires_at.strftime('%d.%m.%Y')}")
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ Ошибка: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    create_promo_codes()
