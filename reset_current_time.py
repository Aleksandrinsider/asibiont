"""
Миграция: сброс current_time в профилях пользователей
Запустить один раз для очистки старых значений current_time
"""
from models import Session, UserProfile

def reset_current_time():
    session = Session()
    try:
        # Обнуляем current_time для всех профилей
        profiles = session.query(UserProfile).all()
        count = 0
        for profile in profiles:
            if hasattr(profile, 'current_time') and profile.current_time:
                print(f"Resetting current_time for user_id={profile.user_id}: {profile.current_time} -> NULL")
                profile.current_time = None
                count += 1
        
        session.commit()
        print(f"✅ Successfully reset current_time for {count} profiles")
    except Exception as e:
        session.rollback()
        print(f"❌ Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    reset_current_time()
