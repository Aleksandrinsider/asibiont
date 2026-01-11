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

def activate_subscription(user_id, plan='monthly'):
    """Активирует подписку для пользователя"""
    session = Session()
    try:
        # First find the user by telegram_id
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False, "Пользователь не найден"
        
        # Check if subscription already exists
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        
        if sub:
            # Update existing subscription
            sub.status = 'active'
            sub.plan = plan
            sub.start_date = datetime.datetime.now(datetime.timezone.utc)
            if plan == 'monthly':
                sub.end_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
            elif plan == 'yearly':
                sub.end_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
            session.commit()
            return True, f"Подписка обновлена до {sub.end_date.strftime('%d.%m.%Y')}"
        else:
            # Create new subscription
            end_date = None
            if plan == 'monthly':
                end_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
            elif plan == 'yearly':
                end_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
            
            new_sub = Subscription(
                user_id=user.id,
                status='active',
                plan=plan,
                start_date=datetime.datetime.now(datetime.timezone.utc),
                end_date=end_date,
                login_count=0
            )
            session.add(new_sub)
            session.commit()
            return True, f"Подписка активирована до {end_date.strftime('%d.%m.%Y') if end_date else 'бессрочно'}"
            
    except Exception as e:
        session.rollback()
        return False, f"Ошибка активации подписки: {str(e)}"
    finally:
        session.close()