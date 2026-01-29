import os
os.environ['LOCAL'] = '1'

from models import User, Subscription, SessionLocal

session = SessionLocal()

user2 = session.query(User).filter_by(id=2).first()
user3 = session.query(User).filter_by(id=3).first()

print(f"User 2 (@{user2.username}):")
print(f"  subscription_tier: {user2.subscription_tier}")
print(f"  type: {type(user2.subscription_tier)}")
if user2.subscription_tier:
    print(f"  value: {user2.subscription_tier.value}")

subscription2 = session.query(Subscription).filter_by(user_id=2).first()
if subscription2:
    print(f"  Subscription.tier: {subscription2.tier}")
    print(f"  Subscription.tier.value: {subscription2.tier.value}")

print(f"\nUser 3 (@{user3.username}):")
print(f"  subscription_tier: {user3.subscription_tier}")
print(f"  type: {type(user3.subscription_tier)}")
if user3.subscription_tier:
    print(f"  value: {user3.subscription_tier.value}")

subscription3 = session.query(Subscription).filter_by(user_id=3).first()
if subscription3:
    print(f"  Subscription.tier: {subscription3.tier}")
    print(f"  Subscription.tier.value: {subscription3.tier.value}")

session.close()
