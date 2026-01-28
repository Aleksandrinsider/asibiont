from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, WEB_APP_URL, LOCAL
import logging

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

def create_payment(amount, description, user_id, tier='light'):
    """Create payment for subscription
    
    Args:
        amount: Payment amount in RUB
        description: Payment description
        user_id: User ID for metadata
        tier: Subscription tier (light, standard, premium)
    """
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("Cannot create payment: Yookassa credentials not configured")
        raise ValueError("Payment system not configured")
    
    try:
        payment = Payment.create({
            "amount": {
                "value": str(amount),
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
                "tier": tier
            }
        })
        return payment.confirmation.confirmation_url
    except Exception as e:
        logger.error(f"Yookassa error: {e}")
        raise

def get_tier_price(tier):
    """Get price for subscription tier"""
    return TIER_PRICES.get(tier, 3000)

def get_tier_name(tier):
    """Get display name for subscription tier"""
    return TIER_NAMES.get(tier, '🥉 Бронза')