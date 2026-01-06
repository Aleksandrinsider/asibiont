from models import User, UserProfile, SessionLocal

session = SessionLocal()

try:
    user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if user:
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        if profile:
            # Обновляем профиль
            profile.company = "ASI Biont"
            profile.goals = "Создать успешный AI продукт для управления задачами, привлечь 10K+ пользователей"
            
            session.commit()
            
            print("✅ Профиль обновлен:")
            print(f"  Город: {profile.city}")
            print(f"  Компания: {profile.company}")
            print(f"  Должность: {profile.position}")
            print(f"  Интересы: {profile.interests}")
            print(f"  Навыки: {profile.skills}")
            print(f"  Цели: {profile.goals}")
        else:
            print("Профиль не найден")
    else:
        print("Пользователь не найден")
        
except Exception as e:
    session.rollback()
    print(f"Ошибка: {e}")
finally:
    session.close()
