from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, WEB_APP_URL, LOCAL
import logging
import datetime
import json
import time
import uuid
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

# Token packages (primary monetization)
TOKEN_PACK_PRICES = {
    'tokens_small':  {'price': 1500,  'tokens': 1500},
    'tokens_medium': {'price': 5000,  'tokens': 5500},
    'tokens_large':  {'price': 15000, 'tokens': 18000},
}

def create_payment(amount, description, user_id, tier='tokens_small', promo_code=None):
    """Create payment for token pack
    
    Args:
        amount: Payment amount in RUB
        description: Payment description
        user_id: User ID for metadata
        tier: Token pack tier (tokens_small, tokens_medium, tokens_large)
        promo_code: Unused, kept for backward compat
    """
    logger.info(f"Creating payment: amount={amount}, tier={tier}, user_id={user_id}")
    
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("Cannot create payment: Yookassa credentials not configured")
        raise ValueError("Payment system not configured")
    
    logger.info(f"Yookassa configured: SHOP_ID={YOOKASSA_SHOP_ID}, SECRET_KEY=***")
    
    final_amount = amount
    
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
                "tier": tier
            }
        }
        
        # Idempotence key prevents duplicate payments on retry
        idempotence_key = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{tier}_{int(time.time() // 60)}"))
        logger.info(f"Creating Yookassa payment with data: {json.dumps(payment_data, ensure_ascii=False)}")
        payment = Payment.create(payment_data, idempotence_key)
        logger.info(f"Payment created successfully: ID={payment.id}, URL={payment.confirmation.confirmation_url[:50]}...")
        
        return payment.confirmation.confirmation_url
    except Exception as e:
        logger.error(f"Yookassa API error: {type(e).__name__}: {e}")
        logger.error(f"Detailed error info: {traceback.format_exc()}")
        raise

