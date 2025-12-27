from models import Session, User, Subscription

def check_subscription(user_id):
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        return subscription and subscription.status == 'active'
    finally:
        session.close()