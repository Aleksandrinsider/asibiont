import os
os.environ['LOCAL'] = '1'

from models import Session, User

# Update existing test users with fake avatars
session_db = Session()
try:
    for telegram_id in range(1001, 1021):  # 1001 to 1020
        user = session_db.query(User).filter_by(telegram_id=telegram_id).first()
        if user and not user.photo_url:
            user.photo_url = f"https://api.telegram.org/file/botfake_token/photos/file_test_{telegram_id}.jpg?r={telegram_id}"
            print(f"Updated avatar for user {telegram_id}")
        elif user:
            print(f"User {telegram_id} already has avatar: {user.photo_url}")
    session_db.commit()
    print("All test users updated with avatars")
except Exception as e:
    print(f"Error: {e}")
    session_db.rollback()
finally:
    session_db.close()