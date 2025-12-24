from yookassa import Configuration, Payment
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, YOOKASSA_WEBHOOK_URL

Configuration.configure(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)

def create_payment(amount, description, user_id):
    payment = Payment.create({
        "amount": {
            "value": str(amount),
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": "https://your-return-url.com"  # Заменить
        },
        "capture": True,
        "description": description,
        "metadata": {
            "user_id": user_id
        }
    })
    return payment.confirmation.confirmation_url