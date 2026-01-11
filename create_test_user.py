#!/usr/bin/env python3
"""
Создание тестовых пользователей для локального тестирования
"""
from models import Base, engine, Session, User, UserProfile, Task
from datetime import datetime
import pytz

def create_test_users():
    """Создает тестовых пользователей"""
    session = Session()

    try:
        # Создаем несколько тестовых пользователей
        test_users_data = [
            {
                'telegram_id': 123456789,
                'username': "test_user",
                'first_name': "Test",
                'profile': {
                    'skills': "Python, AI, Machine Learning",
                    'interests': "Программирование, Искусственный интеллект, Наука о данных",
                    'goals': "Изучить глубокое обучение, Создать полезный AI продукт",
                    'city': "Москва",
                    'company': "Test Company",
                    'position': "AI Developer",
                    'bio': "Я разработчик AI с опытом в Python и машинном обучении. Интересуюсь новыми технологиями и их применением в реальной жизни.",
                    'languages': "Русский (родной), English (C1)",
                    'current_plans': "Работаю над проектом по автоматизации задач"
                }
            },
            {
                'telegram_id': 123456790,
                'username': "dev_partner",
                'first_name': "Dev",
                'profile': {
                    'skills': "Python, JavaScript, React",
                    'interests': "Веб-разработка, AI, Стартапы",
                    'goals': "Создать успешный стартап, Изучить машинное обучение",
                    'city': "Москва",
                    'company': "Tech Startup",
                    'position': "Full Stack Developer",
                    'bio': "Full-stack разработчик с опытом в стартапах. Люблю создавать полезные продукты и работать с AI.",
                    'languages': "Русский (родной), English (B2)",
                    'current_plans': "Ищу команду для нового проекта"
                }
            },
            {
                'telegram_id': 123456791,
                'username': "ai_researcher",
                'first_name': "AI",
                'profile': {
                    'skills': "Machine Learning, Deep Learning, Python",
                    'interests': "Искусственный интеллект, Нейросети, Исследования",
                    'goals': "Защитить PhD, Опубликовать статьи в топ журналах",
                    'city': "Санкт-Петербург",
                    'company': "University",
                    'position': "PhD Researcher",
                    'bio': "Исследователь в области AI, специализируюсь на компьютерном зрении и обработке естественного языка.",
                    'languages': "Русский (родной), English (C1), German (A2)",
                    'current_plans': "Работаю над диссертацией"
                }
            },
            {
                'telegram_id': 123456792,
                'username': "product_manager",
                'first_name': "Product",
                'profile': {
                    'skills': "Product Management, Analytics, Agile",
                    'interests': "Продуктовая разработка, Стартапы, Технологии",
                    'goals': "Вывести продукт на рынок, Создать команду",
                    'city': "Москва",
                    'company': "Product Company",
                    'position': "Product Manager",
                    'bio': "Product manager с опытом в tech компаниях. Помогаю командам создавать отличные продукты.",
                    'languages': "Русский (родной), English (C1)",
                    'current_plans': "Планирую запуск нового продукта"
                }
            }
        ]

        created_users = []

        for user_data in test_users_data:
            # Проверяем, есть ли уже пользователь
            existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if existing_user:
                print(f"✅ Пользователь уже существует: {existing_user.username}")
                created_users.append(existing_user.id)
                continue

            # Создаем пользователя
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name'],
                timezone="Europe/Moscow",
                created_at=datetime.now(pytz.UTC)
            )

            session.add(user)
            session.flush()  # Получаем ID

            # Создаем профиль
            profile_data = user_data['profile']
            profile = UserProfile(
                user_id=user.id,
                skills=profile_data['skills'],
                interests=profile_data['interests'],
                goals=profile_data['goals'],
                city=profile_data['city'],
                company=profile_data['company'],
                position=profile_data['position'],
                bio=profile_data['bio'],
                languages=profile_data['languages'],
                current_plans=profile_data['current_plans']
            )

            session.add(profile)

            # Создаем тестовые задачи
            test_tasks = [
                Task(
                    user_id=user.id,
                    title=f"Разработать архитектуру новой фичи - {user_data['username']}",
                    description="Спроектировать архитектуру для новой функциональности",
                    status="active",
                    created_at=datetime.now(pytz.UTC)
                ),
                Task(
                    user_id=user.id,
                    title=f"Ответить на письма - {user_data['username']}",
                    description="Обработать входящую корреспонденцию",
                    status="active",
                    created_at=datetime.now(pytz.UTC)
                )
            ]
            session.add_all(test_tasks)

            created_users.append(user.id)
            print(f"✅ Создан пользователь: {user.username} (ID: {user.id})")

        session.commit()
        return created_users

    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка создания пользователей: {e}")
        return []
    finally:
        session.close()

if __name__ == "__main__":
    # Создаем таблицы
    Base.metadata.create_all(engine)
    print("✅ База данных инициализирована")

    # Создаем пользователей
    user_ids = create_test_users()
    if user_ids:
        print(f"🎯 Создано пользователей: {len(user_ids)}")
        print(f"IDs: {user_ids}")
    else:
        print("❌ Не удалось создать пользователей")