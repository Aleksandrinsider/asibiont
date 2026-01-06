from models import Session, Task, User, UserProfile, Interaction

session = Session()
user = session.query(User).filter_by(telegram_id=146333757).first()

if user:
    # Удаляем задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    # Удаляем взаимодействия
    session.query(Interaction).filter_by(user_id=user.id).delete()
    # Удаляем профиль
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        session.delete(profile)
    
    session.commit()
    print(f"✅ Cleared all data for user {user.telegram_id}")
    print(f"   - Deleted tasks")
    print(f"   - Deleted interactions")
    print(f"   - Deleted profile")
else:
    print("User not found")

session.close()
