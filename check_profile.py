import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile

s = Session()
u = s.query(User).first()
if u:
    p = s.query(UserProfile).filter_by(user_id=u.id).first()
    print(f'User: {u.username}')
    print(f'Interests: {p.interests if p and p.interests else "None"}')
    print(f'Skills: {p.skills if p and p.skills else "None"}')
    print(f'Goals: {p.goals if p and p.goals else "None"}')
    print(f'City: {p.city if p and p.city else "None"}')
s.close()

# Проверим тестовых пользователей
print("\nTest users:")
test_users = s = Session()
for tid in [111111, 222222, 333333, 444444, 555555]:
    u = s.query(User).filter_by(telegram_id=tid).first()
    if u:
        p = s.query(UserProfile).filter_by(user_id=u.id).first()
        print(f'{u.username}: {p.interests if p else "No profile"}')
    else:
        print(f'{tid}: NOT FOUND')
s.close()
