import os
os.environ['LOCAL'] = '1'

from models import Session, UserProfile

# Add 'спорт' interest to all user profiles
session = Session()
try:
    profiles = session.query(UserProfile).all()
    updated = 0
    for profile in profiles:
        if profile.interests:
            interests_lower = profile.interests.lower()
            if 'спорт' not in interests_lower:
                profile.interests = profile.interests + ', спорт'
                updated += 1
        else:
            profile.interests = 'спорт'
            updated += 1

    if updated > 0:
        session.commit()
        print(f"Added 'спорт' interest to {updated} user profiles")
    else:
        print("All profiles already have 'спорт' interest")

except Exception as e:
    print(f"Error: {e}")
    session.rollback()
finally:
    session.close()