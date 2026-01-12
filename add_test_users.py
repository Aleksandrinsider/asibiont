"""
Скрипт для добавления тестовых пользователей с интересами "спорт"
"""
import os
from dotenv import load_dotenv

load_dotenv()

from models import Session, User, UserProfile
from datetime import datetime

def add_test_users():
    session = Session()
    
    test_users = [
        {
            'telegram_id': 111111,
            'username': 'sportfan1',
            'first_name': 'Алексей',
            'interests': 'спорт, бег, фитнес',
            'city': 'Москва',
            'company': 'Фитнес-клуб',
            'position': 'Тренер'
        },
        {
            'telegram_id': 222222,
            'username': 'sportfan2',
            'first_name': 'Дмитрий',
            'interests': 'спорт, футбол, плавание',
            'city': 'Санкт-Петербург',
            'company': 'Спортивная школа',
            'position': 'Инструктор'
        },
        {
            'telegram_id': 333333,
            'username': 'sportfan3',
            'first_name': 'Михаил',
            'interests': 'спорт, теннис, йога',
            'city': 'Москва',
            'company': 'Теннисный центр',
            'position': 'Спортсмен'
        },
        {
            'telegram_id': 444444,
            'username': 'sportfan4',
            'first_name': 'Елена',
            'interests': 'спорт, волейбол, танцы',
            'city': 'Казань',
            'company': 'Спортивный комплекс',
            'position': 'Администратор'
        },
        {
            'telegram_id': 555555,
            'username': 'sportfan5',
            'first_name': 'Анна',
            'interests': 'спорт, гимнастика, пилатес',
            'city': 'Москва',
            'company': 'Студия пилатес',
            'position': 'Инструктор'
        }
    ]
    
    for user_data in test_users:
        # Проверяем, существует ли пользователь
        existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
        
        if existing_user:
            print(f"Пользователь {user_data['username']} уже существует, пропускаем")
            continue
        
        # Создаем пользователя
        user = User(
            telegram_id=user_data['telegram_id'],
            username=user_data['username'],
            first_name=user_data['first_name'],
            created_at=datetime.utcnow()
        )
        session.add(user)
        session.flush()  # Получаем ID пользователя
        
        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            interests=user_data['interests'],
            city=user_data['city'],
            company=user_data['company'],
            position=user_data['position'],
            skills='коммуникация, лидерство, тренировки',
            goals='развитие в спорте, здоровый образ жизни',
            updated_at=datetime.utcnow()
        )
        session.add(profile)
        
        print(f"✅ Добавлен пользователь: {user_data['first_name']} (@{user_data['username']})")
    
    session.commit()
    session.close()
    print("\n✅ Все тестовые пользователи успешно добавлены!")

if __name__ == '__main__':
    add_test_users()
