"""
Скрипт для создания промокодов для каждого тарифа.
Промокоды активируют подписку на месяц. Один пользователь может использовать только раз.
"""
import datetime
from models import Session, PromoCode, SubscriptionTier
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_promo_codes():
    """Создать промокоды для каждого тарифа"""
    session = Session()
    
    try:
        # Определяем промокоды
        promo_codes_data = [
            {
                'code': 'LIGHT1',
                'tier': SubscriptionTier.LIGHT,
                'discount_percent': 100,  # 100% скидка = бесплатно
                'duration_days': 30,
                'expires_at': datetime.datetime(2026, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc),
                'max_uses': None  # Неограничено
            },
            {
                'code': 'STD2026XPRO',
                'tier': SubscriptionTier.STANDARD,
                'discount_percent': 100,  # 100% скидка = бесплатно
                'duration_days': 30,
                'expires_at': datetime.datetime(2026, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc),
                'max_uses': None  # Неограничено
            },
            {
                'code': 'PREM2026ELITE',
                'tier': SubscriptionTier.PREMIUM,
                'discount_percent': 100,  # 100% скидка = бесплатно
                'duration_days': 30,
                'expires_at': datetime.datetime(2026, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc),
                'max_uses': None  # Неограничено
            },
            {
                'code': 'VIPACCESS2026',
                'tier': SubscriptionTier.PREMIUM,
                'discount_percent': 100,  # 100% скидка = бесплатно
                'duration_days': 365,  # 1 год
                'expires_at': datetime.datetime(2026, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc),
                'max_uses': 1  # Только один раз для одного пользователя
            }
        ]
        
        for promo_data in promo_codes_data:
            # Проверяем, существует ли промокод
            existing = session.query(PromoCode).filter_by(code=promo_data['code']).first()
            
            if existing:
                logger.info(f"Промокод {promo_data['code']} уже существует")
                continue
            
            # Создаем новый промокод
            promo_code = PromoCode(
                code=promo_data['code'],
                tier=promo_data['tier'],
                discount_percent=promo_data['discount_percent'],
                duration_days=promo_data['duration_days'],
                expires_at=promo_data['expires_at'],
                max_uses=promo_data['max_uses'],
                used_count=0,
                used_by_users='[]'
            )
            
            session.add(promo_code)
            logger.info(f"✓ Создан промокод: {promo_data['code']} для тарифа {promo_data['tier'].value}")
        
        session.commit()
        logger.info("\n✅ Все промокоды созданы успешно!")
        
        # Выводим информацию о созданных промокодах
        print("\n" + "="*60)
        print("СОЗДАННЫЕ ПРОМОКОДЫ")
        print("="*60)
        
        all_promos = session.query(PromoCode).all()
        for promo in all_promos:
            print(f"\nКод: {promo.code}")
            print(f"Тариф: {promo.tier.value}")
            print(f"Скидка: {promo.discount_percent}%")
            print(f"Длительность: {promo.duration_days} дней")
            print(f"Истекает: {promo.expires_at.strftime('%d.%m.%Y')}")
            print(f"Использований: {promo.used_count}")
            print("-"*60)
        
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при создании промокодов: {e}")
        raise
    finally:
        session.close()


if __name__ == '__main__':
    create_promo_codes()
