import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Subscription, User

# Set environment
os.environ['LOCAL'] = '0'  # Production mode

# Import config after setting LOCAL
from config import DATABASE_URL

print(f"DATABASE_URL: {DATABASE_URL}")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def check_subscription(user_id):
    session = Session()
    try:
        # Find user
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            print(f"User {user_id} not found")
            return

        # Check subscription
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription:
            print(f"Subscription for user {user_id}: status={subscription.status}, plan={subscription.plan}, end_date={subscription.end_date}")
        else:
            print(f"No subscription found for user {user_id}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    user_id = 146333757
    check_subscription(user_id)