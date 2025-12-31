from models import Session, UserProfile
import sys

# Заполнить профиль для пользователя
user_id = 146333757

session = Session()
try:
    profile = session.query(UserProfile).filter_by(user_id=user_id).first()
    if not profile:
        # Найти user по telegram_id
        from models import User
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            profile = UserProfile(
                user_id=user.id,
                skills='Python, AI, разработка',
                interests='Искусственный интеллект, программирование, стартапы',
                goals='Создать полезный AI-продукт',
                city='Москва',
                current_plans='Работа над проектом TaskChat'
            )
            session.add(profile)
            session.commit()
            print(f"Профиль для пользователя {user.username} создан")
        else:
            print(f"Пользователь с ID {user_id} не найден")
    else:
        print(f"Профиль уже существует для пользователя")
        print(f"Навыки: {profile.skills}")
        print(f"Интересы: {profile.interests}")
        print(f"Город: {profile.city}")
except Exception as e:
    print(f"Ошибка: {e}")
finally:
    session.close()