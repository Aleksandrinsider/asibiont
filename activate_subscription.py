import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Subscription, User
from datetime import datetime, timedelta

# Set environment
os.environ['LOCAL'] = '0'  # Production mode

# Import config after setting LOCAL
from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def activate_subscription(user_id):
    session = Session()
    try:
        # Find user
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            print(f"User {user_id} not found")
            return

        # Check if subscription exists
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription:
            # Update existing
            subscription.status = 'active'
            subscription.end_date = datetime.now() + timedelta(days=30)  # 30 days
            print(f"Updated subscription for user {user_id}")
        else:
            # Create new
            end_date = datetime.now() + timedelta(days=30)
            subscription = Subscription(user_id=user.id, status='active', plan='monthly', end_date=end_date)
            session.add(subscription)
            print(f"Created subscription for user {user_id}")

        session.commit()
    except Exception as e:
        print(f"Error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    user_id = 146333757
    activate_subscription(user_id)