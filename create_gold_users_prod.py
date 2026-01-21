"""
Создает тестовых Gold пользователей на продакшен базе
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile, SubscriptionTier

def create_gold_users():
    # Продакшен база
    db_url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
    print(f"Используется продакшен база данных")
    
    if not db_url:
        print("❌ Не указан DATABASE_URL")
        return
    
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Данные тестовых Gold пользователей
    gold_users_data = [
        {
            'telegram_id': 111111111,
            'username': 'golduser1_test',
            'first_name': 'Иван',
            'city': 'Москва',
            'company': 'TechCorp',
            'position': 'CEO',
            'interests': 'AI, стартапы',
            'skills': 'Python, лидерство'
        },
        {
            'telegram_id': 222222222,
            'username': 'golduser2_test',
            'first_name': 'Мария',
            'city': 'Санкт-Петербург',
            'company': 'InnovateLab',
            'position': 'CTO',
            'interests': 'инвестиции, технологии',
            'skills': 'менеджмент, аналитика'
        },
        {
            'telegram_id': 333333333,
            'username': 'golduser3_test',
            'first_name': 'Петр',
            'city': 'Москва',
            'company': 'StartupHub',
            'position': 'Founder',
            'interests': 'стартапы, нетворкинг',
            'skills': 'Python, продажи'
        }
    ]
    
    try:
        created_count = 0
        for data in gold_users_data:
            # Проверяем, существует ли пользователь
            existing = session.query(User).filter_by(telegram_id=data['telegram_id']).first()
            if existing:
                print(f"⏭️  Пользователь @{data['username']} уже существует (ID: {existing.id})")
                # Обновляем тариф
                existing.subscription_tier = SubscriptionTier.GOLD
                existing.username = data['username']
                existing.first_name = data['first_name']
                session.commit()
                print(f"   Обновлен тариф на GOLD")
            else:
                # Создаем нового пользователя
                user = User(
                    telegram_id=data['telegram_id'],
                    username=data['username'],
                    first_name=data['first_name'],
                    subscription_tier=SubscriptionTier.GOLD,
                    timezone='Europe/Moscow'
                )
                session.add(user)
                session.flush()
                
                # Создаем профиль
                profile = UserProfile(
                    user_id=user.id,
                    city=data['city'],
                    company=data['company'],
                    position=data['position'],
                    interests=data['interests'],
                    skills=data['skills'],
                    goals='Рост бизнеса, партнерства'
                )
                session.add(profile)
                session.commit()
                
                created_count += 1
                print(f"✅ Создан Gold пользователь: @{data['username']} (ID: {user.id})")
        
        if created_count > 0:
            print(f"\n✅ Создано {created_count} новых Gold пользователей")
        else:
            print(f"\n✅ Все тестовые пользователи уже существуют")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    create_gold_users()
