"""Скрипт для заполнения базы тестовыми пользователями"""
import os
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
import random

load_dotenv()

from models import Session, User, Subscription, UserProfile, Task
from ai_integration import encrypt_data

def create_test_users():
    """Создаёт тестовых пользователей с профилями, задачами и подписками"""
    
    session = Session()
    
    # Тестовые пользователи
    test_users = [
        {
            'telegram_id': 111111111,
            'username': 'alexdev',
            'first_name': 'Александр',
            'city': 'Москва',
            'interests': 'стартапы, программирование, AI, спорт',
            'skills': 'Python, JavaScript, React, ML',
            'goals': 'запустить SaaS продукт, найти соосновательницу',
            'position': 'Backend Developer',
            'company': 'Стартап AI-продукт',
            'tasks': [
                'Запустить MVP стартапа',
                'Найти соосновательницу для проекта',
                'Пробежка 5 км утром'
            ]
        },
        {
            'telegram_id': 222222222,
            'username': 'marinapm',
            'first_name': 'Марина',
            'city': 'Москва',
            'interests': 'маркетинг, стартапы, йога, чтение',
            'skills': 'маркетинг, SMM, аналитика, контент',
            'goals': 'стать product manager, запустить свой продукт',
            'position': 'Marketing Manager',
            'company': 'E-commerce стартап',
            'tasks': [
                'Разработать маркетинговую стратегию',
                'Найти команду для MVP',
                'Йога-сессия вечером'
            ]
        },
        {
            'telegram_id': 333333333,
            'username': 'dmitrydesign',
            'first_name': 'Дмитрий',
            'city': 'Санкт-Петербург',
            'interests': 'дизайн, UI/UX, фотография, путешествия',
            'skills': 'Figma, Adobe XD, Photoshop, Illustrator',
            'goals': 'создать дизайн-студию, работать с международными клиентами',
            'position': 'UI/UX Designer',
            'company': 'Дизайн-агентство',
            'tasks': [
                'Редизайн мобильного приложения',
                'Встреча с клиентом по новому проекту',
                'Фотопрогулка по городу'
            ]
        },
        {
            'telegram_id': 444444444,
            'username': 'olgafit',
            'first_name': 'Ольга',
            'city': 'Москва',
            'interests': 'фитнес, здоровье, питание, бег',
            'skills': 'тренерство, нутрициология, мотивация',
            'goals': 'открыть фитнес-студию, пробежать марафон',
            'position': 'Фитнес-тренер',
            'company': 'Фитнес-клуб',
            'tasks': [
                'Подготовка к полумарафону',
                'Разработать программу тренировок',
                'Утренняя пробежка 10 км'
            ]
        },
        {
            'telegram_id': 555555555,
            'username': 'sergeydata',
            'first_name': 'Сергей',
            'city': 'Москва',
            'interests': 'data science, ML, математика, шахматы',
            'skills': 'Python, TensorFlow, PyTorch, SQL, статистика',
            'goals': 'стать lead data scientist, преподавать ML',
            'position': 'Data Scientist',
            'company': 'Tech компания',
            'tasks': [
                'Обучить модель для предсказания оттока',
                'Написать статью про ML алгоритмы',
                'Шахматный турнир онлайн'
            ]
        },
        {
            'telegram_id': 666666666,
            'username': 'annacontent',
            'first_name': 'Анна',
            'city': 'Санкт-Петербург',
            'interests': 'копирайтинг, блоггинг, литература, кофе',
            'skills': 'контент-маркетинг, SEO, редактура, сторителлинг',
            'goals': 'запустить свой блог, написать книгу',
            'position': 'Content Manager',
            'company': 'Медиа-проект',
            'tasks': [
                'Написать 5 статей для блога',
                'Встреча с издателем',
                'Кофе с друзьями в новой кофейне'
            ]
        },
        {
            'telegram_id': 777777777,
            'username': 'ivanfrontend',
            'first_name': 'Иван',
            'city': 'Москва',
            'interests': 'frontend, веб-разработка, музыка, гитара',
            'skills': 'React, Vue, TypeScript, CSS, Webpack',
            'goals': 'стать senior frontend, запустить open-source проект',
            'position': 'Frontend Developer',
            'company': 'IT-компания',
            'tasks': [
                'Рефакторинг компонентов на React',
                'Изучить Next.js 14',
                'Репетиция с группой вечером'
            ]
        },
        {
            'telegram_id': 888888888,
            'username': 'elenapsych',
            'first_name': 'Елена',
            'city': 'Екатеринбург',
            'interests': 'психология, медитация, йога, саморазвитие',
            'skills': 'психотерапия, коучинг, НЛП, майндфулнесс',
            'goals': 'открыть психологический центр, помочь 1000 человек',
            'position': 'Психолог',
            'company': 'Частная практика',
            'tasks': [
                'Консультация клиента в 10:00',
                'Прочитать книгу по когнитивной терапии',
                'Медитация 30 минут утром'
            ]
        }
    ]
    
    created_count = 0
    
    try:
        for user_data in test_users:
            # Проверяем, существует ли пользователь
            existing = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if existing:
                print(f"⚠️  Пользователь @{user_data['username']} уже существует")
                continue
            
            # Создаём пользователя
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name'],
                timezone='Europe/Moscow'
            )
            session.add(user)
            session.flush()  # Получаем user.id
            
            # Создаём профиль
            profile = UserProfile(
                user_id=user.id,
                contact_info=f"@{user_data['username']}",
                city=user_data['city'],
                interests=user_data['interests'],
                skills=user_data['skills'],
                goals=user_data['goals'],
                position=user_data['position'],
                company=user_data['company']
            )
            session.add(profile)
            
            # Создаём задачи
            for i, task_title in enumerate(user_data['tasks']):
                # Случайное время напоминания в течение дня
                reminder_time = datetime.now(pytz.UTC) + timedelta(hours=random.randint(1, 12))
                
                task = Task(
                    user_id=user.id,
                    title=task_title,
                    description=encrypt_data(f'Задача для {user_data["first_name"]}'),
                    status='pending',
                    reminder_time=reminder_time
                )
                session.add(task)
            
            # Активируем подписку на 30 дней
            now = datetime.now(pytz.UTC)
            subscription = Subscription(
                user_id=user.id,
                status='active',
                start_date=now,
                end_date=now + timedelta(days=30)
            )
            session.add(subscription)
            
            created_count += 1
            print(f"✅ Создан пользователь: @{user_data['username']} ({user_data['city']})")
        
        session.commit()
        print(f"\n🎉 Создано {created_count} пользователей с профилями и задачами!")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    create_test_users()
