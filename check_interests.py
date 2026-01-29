import os
os.environ['LOCAL'] = '1'

from models import User, UserProfile, SessionLocal

session = SessionLocal()
profiles = session.query(UserProfile).join(User).all()[:5]

print("Текущее состояние interests в БД:\n")
for profile in profiles:
    user = session.query(User).filter_by(id=profile.user_id).first()
    print(f"User {user.id} (@{user.username}):")
    print(f"  interests = '{profile.interests}'")
    print(f"  skills = '{profile.skills}'")
    print(f"  goals = '{profile.goals}'")
    
    # Проверим как будет работать split
    if profile.interests:
        interests_set = set(i.strip().lower() for i in profile.interests.split(','))
        print(f"  после split: {interests_set}")
    print()

session.close()
