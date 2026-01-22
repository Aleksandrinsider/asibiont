from models import Session, User, UserProfile

s = Session()

# Найти всех пользователей с интересом "спорт"
profiles = s.query(UserProfile).filter(UserProfile.interests.isnot(None)).all()

print("Пользователи с интересом 'спорт':\n")
for profile in profiles:
    if profile.interests and 'спорт' in profile.interests.lower():
        user = s.query(User).filter_by(id=profile.user_id).first()
        if user:
            print(f"@{user.username or user.first_name}")
            print(f"  Интересы: {profile.interests}")
            print()

s.close()
