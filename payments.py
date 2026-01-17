from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_WEBHOOK_URL, WEB_APP_URL

print(f"Yookassa config: SHOP_ID={YOOKASSA_SHOP_ID}, SECRET_KEY starts with {YOOKASSA_SECRET_KEY[:10] if YOOKASSA_SECRET_KEY else 'None'}...")
Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)

# Pricing for subscription tiers (RUB/month)
TIER_PRICES = {
    'bronze': 3000,
    'silver': 9000,
    'gold': 27000
}

TIER_NAMES = {
    'bronze': '🥉 Бронза',
    'silver': '🥈 Серебро',
    'gold': '🥇 Золото'
}

def create_payment(amount, description, user_id, tier='bronze'):
    """Create payment for subscription
    
    Args:
        amount: Payment amount in RUB
        description: Payment description
        user_id: User ID for metadata
        tier: Subscription tier (bronze, silver, gold)
    """
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
        print(f"Yookassa error: {e}")
        raise

def get_tier_price(tier):
    """Get price for subscription tier"""
    return TIER_PRICES.get(tier, 3000)

def get_tier_name(tier):
    """Get display name for subscription tier"""
    return TIER_NAMES.get(tier, '🥉 Бронза')