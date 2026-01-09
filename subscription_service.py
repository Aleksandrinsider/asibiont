from models import Session, Subscription, User
import datetime
from config import FREE_ACCESS_MODE
from payments import create_payment

def check_subscription(user_id):
    if FREE_ACCESS_MODE:
        return True
    session = Session()
    try:
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        
        # Then find subscription by user.id
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub and sub.status == 'active':
            if sub.end_date is None:
                return True
            # Handle both offset-naive and offset-aware datetimes
            now = datetime.datetime.now(datetime.timezone.utc)
            if sub.end_date.tzinfo is None:
                # end_date is offset-naive, make now offset-naive too
                now_naive = now.replace(tzinfo=None)
                return sub.end_date > now_naive
            else:
                # end_date is offset-aware
                return sub.end_date > now
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
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        # Then find subscription by user.id
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
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