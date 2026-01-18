import os
os.environ['LOCAL'] = '1'
print("Starting script...")

try:
    from models import *
    from config import *
    print("Imports successful")
except Exception as e:
    print(f"Import error: {e}")
    exit(1)

from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
import json

print("Creating database connection...")
# Создаем подключение к базе данных
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    print("Creating test users...")
    # Создаем тестовых пользователей
    user1 = User(
        telegram_id=123456789,
        username='testuser1',
        subscription_tier=SubscriptionTier.BRONZE
    )
    session.add(user1)

    user2 = User(
        telegram_id=987654321,
        username='testuser2',
        subscription_tier=SubscriptionTier.SILVER
    )
    session.add(user2)

    user3 = User(
        telegram_id=555666777,
        username='testuser3',
        subscription_tier=SubscriptionTier.GOLD
    )
    session.add(user3)

    session.commit()
    print("Users created")

    # Создаем профили пользователей
    profile1 = UserProfile(
        user_id=user1.id,
        skills='Python, SQL',
        interests='Programming, AI',
        goals='Learn new technologies',
        contact_info='testuser1',
        city='Moscow',
        company='Tech Corp',
        position='Developer',
        favorite_contacts=json.dumps(['testuser2'])  # testuser2 в избранных у testuser1
    )
    session.add(profile1)

    profile2 = UserProfile(
        user_id=user2.id,
        skills='JavaScript, React',
        interests='Web development',
        goals='Build web apps',
        contact_info='testuser2',
        city='Moscow',
        company='Web Inc',
        position='Frontend Developer'
    )
    session.add(profile2)

    profile3 = UserProfile(
        user_id=user3.id,
        skills='Management, Strategy',
        interests='Business',
        goals='Grow company',
        contact_info='testuser3',
        city='St. Petersburg',
        company='Biz Ltd',
        position='CEO'
    )
    session.add(profile3)

    session.commit()
    print("Profiles created")

    # Создаем задачи с делегированием
    task1 = Task(
        user_id=user1.id,
        title='Разработать API для проекта',
        description='Нужно создать REST API',
        status='active',
        delegated_to_username='testuser2',
        delegation_status='pending'
    )
    session.add(task1)

    task2 = Task(
        user_id=user1.id,
        title='Создать дизайн интерфейса',
        description='UI/UX дизайн для приложения',
        status='active',
        delegated_to_username='testuser3',
        delegation_status='accepted'
    )
    session.add(task2)

    task3 = Task(
        user_id=user2.id,
        title='Написать документацию',
        description='Техническая документация',
        status='active',
        delegated_to_username='testuser1',
        delegation_status='pending'
    )
    session.add(task3)

    session.commit()
    print("Tasks created")

    print('Тестовые данные созданы успешно!')
    print(f'Пользователи: {session.query(User).count()}')
    print(f'Профили: {session.query(UserProfile).count()}')
    print(f'Задачи: {session.query(Task).count()}')
    print(f'Задачи с делегированием: {session.query(Task).filter(Task.delegated_to_username.isnot(None)).count()}')

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    session.close()