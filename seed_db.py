# -*- coding: utf-8 -*-
"""Добавление тестовых данных в базу данных"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# НЕ устанавливаем LOCAL=1, чтобы использовать Railway DB
# os.environ['LOCAL'] = '1'

from models import SessionLocal, User, Post, Comment, UserProfile, SubscriptionTier

def seed_database():
    """Добавление тестовых пользователей, постов и комментариев"""
    db = SessionLocal()
    try:
        # Создаем тестовых пользователей
        users_data = [
            {
                'telegram_id': 111111111,
                'username': 'alex_dev',
                'first_name': 'Алексей Разработчик',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 222222222,
                'username': 'maria_design',
                'first_name': 'Мария Дизайнер',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 333333333,
                'username': 'igor_manager',
                'first_name': 'Игорь Менеджер',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 444444444,
                'username': 'olga_analyst',
                'first_name': 'Ольга Аналитик',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 555555555,
                'username': 'dmitry_qa',
                'first_name': 'Дмитрий QA',
                'timezone': 'Europe/Moscow'
            }
        ]

        users = []
        for user_data in users_data:
            user = User(**user_data)
            db.add(user)
            users.append(user)
            db.commit()  # Сохраняем каждого пользователя отдельно

        # Создаем профили для пользователей
        profiles_data = [
            {
                'user_id': users[0].id,
                'bio': 'Python разработчик с опытом в AI и Telegram ботах. Люблю создавать полезные инструменты.',
                'skills': 'Python, SQL, AI, Telegram API, FastAPI',
                'interests': 'Программирование, ИИ, автоматизация процессов'
            },
            {
                'user_id': users[1].id,
                'bio': 'UX/UI дизайнер с фокусом на пользовательский опыт. Создаю интерфейсы, которые нравятся людям.',
                'skills': 'Figma, Adobe XD, UI/UX Design, Prototyping',
                'interests': 'Дизайн, психология, технологии, искусство'
            },
            {
                'user_id': users[2].id,
                'bio': 'Менеджер проектов с опытом в IT. Помогаю командам достигать целей эффективно.',
                'skills': 'Project Management, Agile, Scrum, Team Leadership',
                'interests': 'Менеджмент, спорт, чтение, путешествия'
            },
            {
                'user_id': users[3].id,
                'bio': 'Бизнес-аналитик с техническим бэкграундом. Анализирую данные и оптимизирую процессы.',
                'skills': 'SQL, Python, Data Analysis, Business Intelligence',
                'interests': 'Аналитика, машинное обучение, финансы'
            },
            {
                'user_id': users[4].id,
                'bio': 'QA инженер с вниманием к деталям. Обеспечиваю качество программного обеспечения.',
                'skills': 'Testing, QA, Automation, Bug Tracking',
                'interests': 'Качество, автоматизация, гейминг, спорт'
            }
        ]

        for profile_data in profiles_data:
            profile = UserProfile(**profile_data)
            db.add(profile)

        # Создаем посты
        posts_data = [
            {
                'user_id': users[0].id,
                'content': 'Привет всем! Сегодня начал новый проект с использованием AI для управления задачами. Очень интересно работать с DeepSeek!'
            },
            {
                'user_id': users[1].id,
                'content': 'Кто-нибудь пробовал интегрировать Telegram бота с PostgreSQL? Поделитесь опытом! У меня есть вопросы по оптимизации запросов.'
            },
            {
                'user_id': users[2].id,
                'content': 'Завершил важную задачу сегодня. Чувствую себя продуктивным! #продуктивность #менеджмент'
            },
            {
                'user_id': users[3].id,
                'content': 'Ищу единомышленников для совместного проекта. Интересует разработка AI-ассистентов для бизнеса.'
            },
            {
                'user_id': users[4].id,
                'content': 'Сегодня узнал много нового о машинном обучении. Рекомендую всем изучать эту тему! Особенно полезны курсы на Coursera.'
            },
            {
                'user_id': users[0].id,
                'content': 'Тестирую новый функционал в нашем боте. Добавил возможность создания задач через естественный язык. Работает отлично!'
            },
            {
                'user_id': users[1].id,
                'content': 'Создаю дизайн для дашборда. Какие цвета предпочитаете для интерфейса управления задачами?'
            },
            {
                'user_id': users[2].id,
                'content': 'Организовал встречу команды. Обсудили новые фичи и приоритеты на следующий спринт.'
            }
        ]

        posts = []
        for i, post_data in enumerate(posts_data):
            # Найти пользователя для получения username
            user_index = next((idx for idx, u in enumerate(users) if u.id == post_data['user_id']), None)
            if user_index is not None:
                post_data['username'] = users[user_index].username
            post = Post(**post_data)
            db.add(post)
            posts.append(post)

        db.commit()  # Сохраняем посты

        # Создаем комментарии
        comments_data = [
            {
                'post_id': posts[0].id,
                'user_id': users[1].id,
                'content': 'Звучит интересно! Расскажи подробнее о проекте. Какие технологии используешь?'
            },
            {
                'post_id': posts[0].id,
                'user_id': users[2].id,
                'content': 'AI для управления задачами - это будущее! Поддерживаю инициативу!'
            },
            {
                'post_id': posts[1].id,
                'user_id': users[0].id,
                'content': 'Да, у меня есть опыт. PostgreSQL отлично работает с aiogram. Могу поделиться кодом.'
            },
            {
                'post_id': posts[1].id,
                'user_id': users[3].id,
                'content': 'Я тоже работаю с этой связкой. Главное - правильно настроить connection pooling.'
            },
            {
                'post_id': posts[2].id,
                'user_id': users[0].id,
                'content': 'Поздравляю! Что за задача была? Может, поделишься опытом?'
            },
            {
                'post_id': posts[3].id,
                'user_id': users[1].id,
                'content': 'Я заинтересован! Какие технологии планируешь использовать? Python + AI?'
            },
            {
                'post_id': posts[3].id,
                'user_id': users[4].id,
                'content': 'Считай, что я в деле! Могу помочь с тестированием.'
            },
            {
                'post_id': posts[4].id,
                'user_id': users[2].id,
                'content': 'Полностью согласен! ML открывает огромные возможности в нашей сфере.'
            },
            {
                'post_id': posts[4].id,
                'user_id': users[0].id,
                'content': 'Какие ресурсы рекомендуешь для изучения? Книги, курсы?'
            },
            {
                'post_id': posts[5].id,
                'user_id': users[3].id,
                'content': 'Круто! А как обрабатываешь естественный язык? Используешь NLP модели?'
            },
            {
                'post_id': posts[6].id,
                'user_id': users[4].id,
                'content': 'Мне нравится минималистичный дизайн. Синий и белый цвета подойдут.'
            },
            {
                'post_id': posts[7].id,
                'user_id': users[1].id,
                'content': 'Хорошая работа! Какие фичи планируете добавить в следующем спринте?'
            }
        ]

        for comment_data in comments_data:
            # Найти пользователя для получения username
            user_index = next((idx for idx, u in enumerate(users) if u.id == comment_data['user_id']), None)
            if user_index is not None:
                comment_data['username'] = users[user_index].username
            comment = Comment(**comment_data)
            db.add(comment)

        db.commit()
        print("Тестовые данные успешно добавлены!")
        print(f"Добавлено пользователей: {len(users)}")
        print(f"Добавлено постов: {len(posts)}")
        print(f"Добавлено комментариев: {len(comments_data)}")
        print("\nПользователи:")
        for user in users:
            print(f"- {user.username} ({user.first_name})")

    except Exception as e:
        db.rollback()
        print(f"Ошибка при добавлении данных: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()