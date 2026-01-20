from models import Session, User, Subscription, SubscriptionTier
from datetime import datetime, timedelta

telegram_id = 146333757
tier = SubscriptionTier.SILVER

db = Session()
try:
    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        print(f"User {telegram_id} not found")
        exit(1)
    
    # Update user tier
    user.subscription_tier = tier
    
    # Update or create subscription
    subscription = db.query(Subscription).filter_by(user_id=user.id).first()
    if subscription:
        subscription.status = 'active'
        subscription.tier = tier
        subscription.start_date = datetime.now()
        subscription.end_date = datetime.now() + timedelta(days=30)
        print(f"Updated subscription for user {telegram_id}")
    else:
        subscription = Subscription(
            user_id=user.id,
            telegram_id=telegram_id,
            telegram_username=user.username,
            username=user.first_name,
            status='active',
            tier=tier,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30)
        )
        db.add(subscription)
        print(f"Created subscription for user {telegram_id}")
    
    db.commit()
    print(f"✅ Silver subscription activated for user {telegram_id}")
    print(f"Valid until: {subscription.end_date}")
finally:
    db.close()
