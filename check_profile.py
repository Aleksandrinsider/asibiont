from models import Session, User, UserProfile

s = Session()
u = s.query(User).filter_by(telegram_id=999000).first()
print(f'User found: {u is not None}')
if u:
    p = s.query(UserProfile).filter_by(user_id=u.id).first()
    print(f'Profile found: {p is not None}')
    if p:
        print(f'Company: {p.company}')
        print(f'Position: {p.position}')
    else:
        print('Profile not found')
s.close()
