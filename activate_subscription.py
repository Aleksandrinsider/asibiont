from models import Session, User, Subscription
import datetime
import pytz

user_id = 146333757

session = Session()

# Check if user exists
user = session.query(User).filter_by(telegram_id=user_id).first()
if not user:
    user = User(telegram_id=user_id, username="test_user")  # Add username if known
    session.add(user)
    session.commit()
    print(f"Created user {user_id}")

# Check subscription
subscription = session.query(Subscription).filter_by(user_id=user.id).first()
if not subscription:
    subscription = Subscription(user_id=user.id)
    session.add(subscription)

subscription.status = 'active'
subscription.start_date = datetime.datetime.now(pytz.UTC)
subscription.end_date = datetime.datetime.now(pytz.UTC) + datetime.timedelta(days=30)

session.commit()
print(f"Activated subscription for user {user_id}")

session.close()