from models import Session, User, UserProfile

s = Session()
user = s.query(User).filter_by(username='aleksandrinsider').first()
if user:
    profile = s.query(UserProfile).filter_by(user_id=user.id).first()
    print(f'Username: {user.username}')
    print(f'Telegram ID: {user.telegram_id}')
    if profile:
        print(f'Interests: {profile.interests}')
        print(f'Skills: {profile.skills}')
        print(f'Goals: {profile.goals}')
        print(f'City: {profile.city}')
    else:
        print('No profile')
else:
    print('User not found')
s.close()
