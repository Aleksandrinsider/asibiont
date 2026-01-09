from models import Session, Subscription
import datetime
from config import FREE_ACCESS_MODE
from payments import create_payment

def check_subscription(user_id):
    if FREE_ACCESS_MODE:
        return True
    session = Session()
    try:
        sub = session.query(Subscription).filter_by(user_id=user_id).first()
        if sub and sub.status == 'active' and (sub.end_date is None or sub.end_date > datetime.datetime.now(datetime.timezone.utc)):
            return True
        return False
    finally:
        session.close()

def create_subscription_payment(user_id):
    """Создает платеж для месячной подписки"""
    amount = "3000.00"  # Цена за месяц
    description = f"Подписка ASI Biont на месяц"
    return create_payment(amount, description, user_id)

def cancel_subscription(user_id):
    """Отменяет подписку пользователя"""
    session = Session()
    try:
        sub = session.query(Subscription).filter_by(user_id=user_id).first()
        if sub:
            sub.status = 'cancelled'
            session.commit()
            return True
        return False
    finally:
        session.close()

def get_subscription_status(user_id):
    """Возвращает детальную информацию о подписке"""
    session = Session()
    try:
        sub = session.query(Subscription).filter_by(user_id=user_id).first()
        if sub:
            return {
                'status': sub.status,
                'plan': sub.plan,
                'start_date': sub.start_date.isoformat() if sub.start_date else None,
                'end_date': sub.end_date.isoformat() if sub.end_date else None,
                'login_count': sub.login_count
            }
        return None
    finally:
        session.close()