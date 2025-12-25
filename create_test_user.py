from models import SessionLocal, User

session = SessionLocal()
try:
    user = session.query(User).filter(User.telegram_id == 1).first()
    if not user:
        session.add(User(telegram_id=1, username='test'))
        session.commit()
        print('User created')
    else:
        print('User already exists')
finally:
    session.close()