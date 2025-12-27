from models import Session, Subscription
import datetime
from config import FREE_ACCESS_MODE

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