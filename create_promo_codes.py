"""
Скрипт для создания промокодов на каждый тариф
"""
import logging
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL
from models import PromoCode, SubscriptionTier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_promo_codes():
    """Создать промокоды на каждый тариф"""
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Дата истечения: 1 февраля 2026 года
        expires_at = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        
        promo_codes = [
            {
                'code': 'BIONT_STARTER_JAN26',
                'tier': SubscriptionTier.BRONZE,
                'duration_days': 30,
                'expires_at': expires_at,
                'max_uses': None,  # Без ограничений по количеству человек
                'discount_percent': 100  # 100% скидка = бесплатная подписка
            },
            {
                'code': 'BIONT_PREMIUM_JAN26',
                'tier': SubscriptionTier.SILVER,
                'duration_days': 30,
                'expires_at': expires_at,
                'max_uses': None,
                'discount_percent': 100
            },
            {
                'code': 'BIONT_ELITE_JAN26',
                'tier': SubscriptionTier.GOLD,
                'duration_days': 30,
                'expires_at': expires_at,
                'max_uses': None,
                'discount_percent': 100
            }
        ]
        
        logger.info("Создание промокодов...")
        
        for promo_data in promo_codes:
            # Проверяем, не существует ли уже такой промокод
            existing = session.query(PromoCode).filter_by(code=promo_data['code']).first()
            if existing:
                logger.info(f"⚠️  Промокод {promo_data['code']} уже существует, пропускаем")
                continue
            
            promo = PromoCode(**promo_data)
            session.add(promo)
            logger.info(f"✅ Создан промокод: {promo_data['code']} (тариф: {promo_data['tier'].value}, срок: 30 дней, истекает: 01.02.2026)")
        
        session.commit()
        logger.info("\n🎉 Все промокоды успешно созданы!")
        logger.info("\nДетали промокодов:")
        logger.info("- BIONT_STARTER_JAN26: Bronze тариф, 30 дней")
        logger.info("- BIONT_PREMIUM_JAN26: Silver тариф, 30 дней")
        logger.info("- BIONT_ELITE_JAN26: Gold тариф, 30 дней")
        logger.info("\nКаждый пользователь может использовать только 1 раз")
        logger.info("Промокоды без ограничений по количеству человек")
        logger.info("Срок действия: до 01.02.2026")
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Ошибка при создании промокодов: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    create_promo_codes()
