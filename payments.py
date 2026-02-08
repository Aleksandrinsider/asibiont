from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, WEB_APP_URL, LOCAL
import logging
import datetime
import json
import traceback

logger = logging.getLogger(__name__)

# Validate Yookassa configuration
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    logger.info(f"Yookassa config: SHOP_ID={YOOKASSA_SHOP_ID}, SECRET_KEY configured")
    Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
else:
    if not LOCAL:
        logger.error("Yookassa credentials not configured in production mode")
    else:
        logger.warning("Yookassa credentials not configured (local mode)")

# Pricing for subscription tiers (RUB/month)
TIER_PRICES = {
    'light': 3000,
    'standard': 9000,
    'premium': 27000
}

TIER_NAMES = {
    'light': '🥉 Бронза',
    'standard': '🥈 Серебро',
    'premium': '🥇 Золото'
}

def create_payment(amount, description, user_id, tier='light', promo_code=None):
    """Create payment for subscription
    
    Args:
        amount: Payment amount in RUB
        description: Payment description
        user_id: User ID for metadata
        tier: Subscription tier (light, standard, premium)
        promo_code: Optional promo code to apply discount
    """
    logger.info(f"Creating payment: amount={amount}, tier={tier}, user_id={user_id}, promo_code={promo_code}")
    
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("Cannot create payment: Yookassa credentials not configured")
        raise ValueError("Payment system not configured")
    
    logger.info(f"Yookassa configured: SHOP_ID={YOOKASSA_SHOP_ID}, SECRET_KEY=***{YOOKASSA_SECRET_KEY[-4:] if YOOKASSA_SECRET_KEY else 'None'}")
    
    # Apply promo code discount if provided
    final_amount = amount
    discount_applied = 0
    if promo_code:
        logger.info(f"Processing promo code: {promo_code}")
        from models import Session, PromoCode, SubscriptionTier
        session = Session()
        try:
            promo = session.query(PromoCode).filter_by(code=promo_code.upper()).first()
            if promo and promo.tier.value.lower() == tier:
                # Check if user already used this promo code
                used_by_users = json.loads(promo.used_by_users or '[]')
                if user_id in used_by_users:
                    raise ValueError("Вы уже использовали этот промокод")
                
                # Check max uses
                if promo.max_uses and promo.used_count >= promo.max_uses:
                    raise ValueError("Промокод уже исчерпан")
                
                # Check expiration
                if promo.expires_at and promo.expires_at < datetime.datetime.now(datetime.timezone.utc):
                    raise ValueError("Промокод истек")
                
                # Apply discount
                discount_applied = int(amount * promo.discount_percent / 100)
                final_amount = max(1, amount - discount_applied)  # Minimum 1 RUB
                
                logger.info(f"Applied promo code {promo_code}: {discount_applied} RUB discount, final amount: {final_amount} RUB")
            else:
                raise ValueError("Неверный промокод для этого тарифа")
        finally:
            session.close()
    else:
        logger.info("No promo code provided, using full amount")
    
    logger.info(f"Final payment amount: {final_amount} RUB")
    
    try:
        payment_data = {
            "amount": {
                "value": str(final_amount),
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": f"{WEB_APP_URL}/dashboard"
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": user_id,
                "tier": tier,
                "promo_code": promo_code,
                "original_amount": amount,
                "discount_applied": discount_applied
            }
        }
        
        logger.info(f"Creating Yookassa payment with data: {json.dumps(payment_data, ensure_ascii=False)}")
        payment = Payment.create(payment_data)
        logger.info(f"Payment created successfully: ID={payment.id}, URL={payment.confirmation.confirmation_url[:50]}...")
        
        return payment.confirmation.confirmation_url
    except Exception as e:
        logger.error(f"Yookassa API error: {type(e).__name__}: {e}")
        logger.error(f"Detailed error info: {traceback.format_exc()}")
        raise

def get_tier_price(tier):
    """Get price for subscription tier"""
    return TIER_PRICES.get(tier, 3000)

def get_tier_name(tier):
    """Get display name for subscription tier"""
    return TIER_NAMES.get(tier, '🥉 Бронза')