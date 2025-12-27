from models import Session, User, Task, UserProfile, Subscription

session = Session()

print("Database check:")
print(f"Users: {session.query(User).count()}")
print(f"Tasks: {session.query(Task).count()}")
print(f"UserProfiles: {session.query(UserProfile).count()}")
print(f"Subscriptions: {session.query(Subscription).count()}")

# List users
users = session.query(User).all()
print("\nUsers:")
for user in users:
    print(f"  ID: {user.id}, Telegram ID: {user.telegram_id}, Username: {user.username}")

# List subscriptions
subscriptions = session.query(Subscription).all()
print("\nSubscriptions:")
for sub in subscriptions:
    print(f"  User ID: {sub.user_id}, Status: {sub.status}, Start: {sub.start_date}, End: {sub.end_date}")

session.close()