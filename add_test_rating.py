from config import DATABASE_URL
from models import Session, User, UserProfile, UserRating
from datetime import datetime

session = Session()

try:
    # Найти главного пользователя
    main_user = session.query(User).filter_by(telegram_id=146333757).first()
    if not main_user:
        print("Main user not found")
        exit()
    
    # Найти тестового пользователя для оценки
    test_user = session.query(User).filter_by(telegram_id=210001).first()
    if not test_user:
        print("Test user not found")
        exit()
    
    # Добавить 3 оценки от разных пользователей для главного пользователя
    ratings_data = [
        (210001, 9),  # От первого тестового
        (210002, 8),  # От второго тестового
        (210003, 10), # От третьего тестового
    ]
    
    for rater_tid, rating_value in ratings_data:
        rater = session.query(User).filter_by(telegram_id=rater_tid).first()
        if rater:
            # Проверить, есть ли уже оценка
            existing = session.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=main_user.id
            ).first()
            
            if not existing:
                new_rating = UserRating(
                    rater_user_id=rater.id,
                    rated_user_id=main_user.id,
                    rating=rating_value,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                session.add(new_rating)
                print(f"Added rating {rating_value} from user {rater_tid}")
    
    session.commit()
    
    # Пересчитать средний рейтинг
    all_ratings = session.query(UserRating).filter_by(rated_user_id=main_user.id).all()
    if all_ratings:
        avg_rating = sum(r.rating for r in all_ratings) / len(all_ratings)
        main_profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        if main_profile:
            main_profile.average_rating = int(round(avg_rating))
            main_profile.rating_count = len(all_ratings)
            session.commit()
            print(f"\nUpdated profile: average_rating={main_profile.average_rating}, rating_count={main_profile.rating_count}")
    
    print("Test ratings added successfully")
    
except Exception as e:
    print(f"Error: {e}")
    session.rollback()
finally:
    session.close()
