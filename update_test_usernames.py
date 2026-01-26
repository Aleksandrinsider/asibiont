import os
os.environ['LOCAL'] = '1'

from models import Session, User

# Update existing test users with new usernames
session_db = Session()
try:
    # Mapping of telegram_id to new username
    username_updates = {
        1001: 'test1', 1002: 'test2', 1003: 'test3', 1004: 'test4', 1005: 'test5',
        1006: 'test6', 1007: 'test7', 1008: 'test8', 1009: 'test9', 1010: 'test10',
        1011: 'test11', 1012: 'test12', 1013: 'test13', 1014: 'test14', 1015: 'test15',
        1016: 'test16', 1017: 'test17', 1018: 'test18', 1019: 'test19', 1020: 'test20'
    }

    for telegram_id, new_username in username_updates.items():
        user = session_db.query(User).filter_by(telegram_id=telegram_id).first()
        if user:
            old_username = user.username
            user.username = new_username
            user.first_name = f'Test User {telegram_id - 1000}'
            print(f"Updated user {telegram_id}: '{old_username}' -> '{new_username}'")
        else:
            print(f"User {telegram_id} not found")

    session_db.commit()
    print("All test users updated with new usernames")
except Exception as e:
    print(f"Error: {e}")
    session_db.rollback()
finally:
    session_db.close()