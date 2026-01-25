import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User

session = Session()

# Check users with id 22 and 37
for user_id in [11, 22, 37]:
    user = session.query(User).filter_by(id=user_id).first()
    if user:
        print(f'User {user_id}: username={user.username}, telegram_id={user.telegram_id}, subscription={user.subscription_tier}')
    else:
        print(f'User {user_id}: NOT FOUND')

session.close()