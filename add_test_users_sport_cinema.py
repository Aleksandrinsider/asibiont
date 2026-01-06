from models import User, UserProfile, Subscription, SessionLocal
from datetime import datetime, timedelta

session = SessionLocal()

try:
    print("\n" + "="*50)
    print("Создание тестовых пользователей с интересами 'спорт, кино' и целью 'знакомства'...")
    print("="*50 + "\n")
    
    # Удаляем старых тестовых пользователей (210011-210020)
    test_users = session.query(User).filter(User.telegram_id >= 210011, User.telegram_id <= 210020).all()
    for user in test_users:
        # Удаляем подписки
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription:
            session.delete(subscription)
        # Удаляем профили
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.delete(user)
    session.commit()
    print(f"Удалено {len(test_users)} старых тестовых пользователей\n")
    
    # Тестовые пользователи с интересами спорт, кино и целью знакомства
    test_users_data = [
        {
            "telegram_id": 210011,
            "username": "sergey_sports_fan",
            "profile": {
                "city": "Moscow",
                "company": "Adidas",
                "position": "Маркетинг-менеджер",
                "interests": "спорт, кино, бег, футбол, кинематограф",
                "skills": "Маркетинг, спортивный менеджмент, SMM",
                "goals": "знакомства, общение с единомышленниками, посещение спортивных мероприятий"
            }
        },
        {
            "telegram_id": 210012,
            "username": "elena_cinema_lover",
            "profile": {
                "city": "Saint Petersburg",
                "company": "Каро Фильм",
                "position": "Киновед",
                "interests": "кино, спорт, йога, классическое кино, современные фильмы",
                "skills": "Анализ кино, критика, монтаж",
                "goals": "знакомства, обсуждение фильмов, совместные просмотры"
            }
        },
        {
            "telegram_id": 210013,
            "username": "mikhail_athlete",
            "profile": {
                "city": "Moscow",
                "company": "World Class",
                "position": "Фитнес-тренер",
                "interests": "спорт, кино, тренировки, боевые искусства, документальное кино",
                "skills": "Персональные тренировки, питание, мотивация",
                "goals": "знакомства, поиск партнеров для тренировок, общение"
            }
        },
        {
            "telegram_id": 210014,
            "username": "olga_film_producer",
            "profile": {
                "city": "Moscow",
                "company": "START",
                "position": "Продюсер",
                "interests": "кино, спорт, производство фильмов, теннис, драматургия",
                "skills": "Кинопроизводство, сценарное мастерство, режиссура",
                "goals": "знакомства, нетворкинг, совместные проекты"
            }
        },
        {
            "telegram_id": 210015,
            "username": "andrey_marathon_runner",
            "profile": {
                "city": "Saint Petersburg",
                "company": "Nike",
                "position": "Спортивный консультант",
                "interests": "спорт, кино, марафоны, триатлон, спортивные фильмы",
                "skills": "Беговые тренировки, спортивное питание, реабилитация",
                "goals": "знакомства, поиск беговых партнеров, общение"
            }
        },
        {
            "telegram_id": 210016,
            "username": "natasha_actress",
            "profile": {
                "city": "Moscow",
                "company": "Театр Вахтангова",
                "position": "Актриса",
                "interests": "кино, спорт, театр, пилатес, артхаус",
                "skills": "Актерское мастерство, танцы, вокал",
                "goals": "знакомства, творческое общение, новые роли"
            }
        },
        {
            "telegram_id": 210017,
            "username": "pavel_sports_journalist",
            "profile": {
                "city": "Moscow",
                "company": "Матч ТВ",
                "position": "Спортивный журналист",
                "interests": "спорт, кино, футбол, хоккей, спортивные документалки",
                "skills": "Журналистика, репортажи, интервью, видеомонтаж",
                "goals": "знакомства, профессиональное общение, поиск экспертов"
            }
        },
        {
            "telegram_id": 210018,
            "username": "ksenia_yoga_instructor",
            "profile": {
                "city": "Saint Petersburg",
                "company": "YogaSpace",
                "position": "Инструктор йоги",
                "interests": "спорт, кино, йога, медитация, индийское кино",
                "skills": "Йога, растяжка, медитация, wellness",
                "goals": "знакомства, создание йога-комьюнити, обмен опытом"
            }
        },
        {
            "telegram_id": 210019,
            "username": "denis_film_critic",
            "profile": {
                "city": "Moscow",
                "company": "Кинопоиск",
                "position": "Кинокритик",
                "interests": "кино, спорт, кинотеория, плавание, международные фестивали",
                "skills": "Киноанализ, критические статьи, публичные выступления",
                "goals": "знакомства, обсуждение кино, посещение кинофестивалей"
            }
        },
        {
            "telegram_id": 210020,
            "username": "victoria_sports_coach",
            "profile": {
                "city": "Moscow",
                "company": "ЦСКА",
                "position": "Спортивный психолог",
                "interests": "спорт, кино, психология, баскетбол, биографические фильмы",
                "skills": "Спортивная психология, коучинг, тренинги",
                "goals": "знакомства, помощь спортсменам, обмен опытом"
            }
        }
    ]
    
    # Создаём пользователей
    created_count = 0
    for user_data in test_users_data:
        # Проверяем, существует ли уже пользователь
        existing_user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
        
        if not existing_user:
            # Создаём пользователя
            user = User(
                telegram_id=user_data["telegram_id"],
                username=user_data["username"],
                first_name=user_data["username"].split('_')[0].capitalize()
            )
            session.add(user)
            session.flush()
            
            # Создаём подписку
            subscription = Subscription(
                user_id=user.id,
                status='active',
                plan='yearly',
                start_date=datetime.now(),
                end_date=datetime.now() + timedelta(days=365)
            )
            session.add(subscription)
            
            # Создаём профиль
            profile = UserProfile(
                user_id=user.id,
                city=user_data["profile"]["city"],
                company=user_data["profile"]["company"],
                position=user_data["profile"]["position"],
                interests=user_data["profile"]["interests"],
                skills=user_data["profile"]["skills"],
                goals=user_data["profile"]["goals"],
                contact_info=user_data["username"]
            )
            session.add(profile)
            
            created_count += 1
            print(f"✅ Создан: @{user_data['username']} (ID: {user_data['telegram_id']})")
            print(f"   Город: {user_data['profile']['city']}")
            print(f"   Компания: {user_data['profile']['company']}")
            print(f"   Интересы: {user_data['profile']['interests']}")
            print(f"   Цели: {user_data['profile']['goals']}")
            print()
    
    session.commit()
    print(f"\n{'='*50}")
    print(f"Успешно создано {created_count} тестовых пользователей!")
    print(f"{'='*50}\n")

except Exception as e:
    print(f"❌ Ошибка: {str(e)}")
    session.rollback()
    raise
finally:
    session.close()
