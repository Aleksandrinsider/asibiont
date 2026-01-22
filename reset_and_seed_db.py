# -*- coding: utf-8 -*-
"""Полная очистка БД и создание тестовых данных"""

import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    SessionLocal, User, Post, Comment, UserProfile, Task, 
    Subscription, SubscriptionTier, Interaction
)
from sqlalchemy import text

def reset_and_seed():
    """Очистить БД и создать тестовые данные"""
    db = SessionLocal()
    try:
        print("=" * 60)
        print("ОЧИСТКА БАЗЫ ДАННЫХ")
        print("=" * 60)
        
        # Очищаем все таблицы (в правильном порядке из-за foreign keys)
        print("Удаление всех данных...")
        db.execute(text('TRUNCATE TABLE comments, posts, tasks, interactions, user_ratings, payment_history, promo_codes, subscriptions, user_profiles, users RESTART IDENTITY CASCADE'))
        db.commit()
        print("✓ Все данные удалены\n")
        
        print("=" * 60)
        print("СОЗДАНИЕ ТЕСТОВЫХ ДАННЫХ")
        print("=" * 60)
        
        # Создаем пользователей с разными тарифами
        users_data = [
            {
                'telegram_id': 146333757,  # Реальный ID
                'username': 'aleksandrinsider',
                'first_name': 'Александр',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 111111111,
                'username': 'alex_dev',
                'first_name': 'Алексей',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 222222222,
                'username': 'maria_design',
                'first_name': 'Мария',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 333333333,
                'username': 'igor_pm',
                'first_name': 'Игорь',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 444444444,
                'username': 'olga_analyst',
                'first_name': 'Ольга',
                'timezone': 'Europe/Moscow'
            },
            {
                'telegram_id': 555555555,
                'username': 'dmitry_qa',
                'first_name': 'Дмитрий',
                'timezone': 'Europe/Moscow'
            }
        ]
        
        users = []
        for user_data in users_data:
            user = User(**user_data)
            db.add(user)
            db.flush()  # Получаем ID сразу
            users.append(user)
        
        db.commit()
        print(f"✓ Создано пользователей: {len(users)}\n")
        
        # Создаем подписки с разными тарифами
        subscriptions_data = [
            {'user_id': users[0].id, 'tier': SubscriptionTier.BRONZE, 'telegram_id': users[0].telegram_id, 'username': users[0].username},  # aleksandrinsider
            {'user_id': users[1].id, 'tier': SubscriptionTier.SILVER, 'telegram_id': users[1].telegram_id, 'username': users[1].username},  # alex_dev
            {'user_id': users[2].id, 'tier': SubscriptionTier.GOLD, 'telegram_id': users[2].telegram_id, 'username': users[2].username},    # maria_design
            {'user_id': users[3].id, 'tier': SubscriptionTier.BRONZE, 'telegram_id': users[3].telegram_id, 'username': users[3].username},  # igor_pm
            {'user_id': users[4].id, 'tier': SubscriptionTier.SILVER, 'telegram_id': users[4].telegram_id, 'username': users[4].username},  # olga_analyst
            {'user_id': users[5].id, 'tier': SubscriptionTier.GOLD, 'telegram_id': users[5].telegram_id, 'username': users[5].username},    # dmitry_qa
        ]
        
        for sub_data in subscriptions_data:
            subscription = Subscription(
                user_id=sub_data['user_id'],
                telegram_id=sub_data['telegram_id'],
                telegram_username=sub_data['username'],
                username=sub_data['username'],
                tier=sub_data['tier'],
                status='active',
                end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
            )
            db.add(subscription)
            db.flush()  # Сохраняем по одному
            # Обновляем tier у пользователя
            user = db.query(User).filter_by(id=sub_data['user_id']).first()
            user.subscription_tier = sub_data['tier']
        
        db.commit()
        print(f"✓ Создано подписок: {len(subscriptions_data)}")
        for i, sub in enumerate(subscriptions_data):
            print(f"  - {users[i].username}: {sub['tier'].value}")
        print()
        
        # Создаем профили (у всех интерес "спорт")
        profiles_data = [
            {
                'user_id': users[0].id,
                'interests': 'спорт, технологии, чтение',
                'skills': 'Анализ, планирование',
                'city': 'Москва'
            },
            {
                'user_id': users[1].id,
                'interests': 'спорт, программирование, AI',
                'skills': 'Python, SQL, AI, Telegram API',
                'city': 'Санкт-Петербург',
                'company': 'TechCorp',
                'position': 'Senior Developer'
            },
            {
                'user_id': users[2].id,
                'interests': 'спорт, дизайн, психология',
                'skills': 'Figma, UI/UX Design',
                'city': 'Москва',
                'company': 'Design Studio',
                'position': 'Lead Designer'
            },
            {
                'user_id': users[3].id,
                'interests': 'спорт, менеджмент, путешествия',
                'skills': 'Project Management, Agile, Scrum',
                'city': 'Казань',
                'company': 'IT Solutions',
                'position': 'Project Manager'
            },
            {
                'user_id': users[4].id,
                'interests': 'спорт, аналитика, финансы',
                'skills': 'SQL, Python, Data Analysis',
                'city': 'Новосибирск',
                'company': 'Analytics Lab',
                'position': 'Business Analyst'
            },
            {
                'user_id': users[5].id,
                'interests': 'спорт, тестирование, автоматизация',
                'skills': 'Testing, QA, Automation',
                'city': 'Екатеринбург',
                'company': 'QA Team',
                'position': 'QA Lead'
            }
        ]
        
        for profile_data in profiles_data:
            profile = UserProfile(**profile_data)
            db.add(profile)
            db.flush()  # Flush после каждого профиля
        
        db.commit()
        print(f"✓ Создано профилей: {len(profiles_data)} (у всех интерес 'спорт')\n")
        
        # Создаем посты
        posts_data = [
            {
                'user_id': users[1].id,
                'username': users[1].username,
                'content': 'Утренняя пробежка 5км! Отличное начало дня 🏃‍♂️'
            },
            {
                'user_id': users[2].id,
                'username': users[2].username,
                'content': 'Кто-нибудь ходит на йогу? Хочу начать заниматься, посоветуйте студию!'
            },
            {
                'user_id': users[0].id,
                'username': users[0].username,
                'content': 'Сегодня пошел в тренажерку после долгого перерыва. Надо восстанавливать форму 💪'
            },
            {
                'user_id': users[3].id,
                'username': users[3].username,
                'content': 'Организовал футбольный матч в эти выходные. Присоединяйтесь!'
            },
            {
                'user_id': users[4].id,
                'username': users[4].username,
                'content': 'Закончила марафон за 4 часа! Новый личный рекорд 🎉'
            },
            {
                'user_id': users[5].id,
                'username': users[5].username,
                'content': 'Ищу партнера для игры в теннис. Уровень - средний. Кто в деле?'
            }
        ]
        
        posts = []
        for post_data in posts_data:
            post = Post(**post_data)
            db.add(post)
            db.flush()
            posts.append(post)
        
        db.commit()
        print(f"✓ Создано постов: {len(posts)}\n")
        
        # Создаем комментарии
        comments_data = [
            {'post_id': posts[0].id, 'user_id': users[0].id, 'username': users[0].username, 'content': 'Круто! Я тоже хочу начать бегать по утрам'},
            {'post_id': posts[0].id, 'user_id': users[2].id, 'username': users[2].username, 'content': 'Молодец! Какой у тебя темп?'},
            {'post_id': posts[1].id, 'user_id': users[0].id, 'username': users[0].username, 'content': 'Я хожу в студию на Тверской, очень нравится!'},
            {'post_id': posts[2].id, 'user_id': users[1].id, 'username': users[1].username, 'content': 'Отличное решение! Главное регулярность'},
            {'post_id': posts[3].id, 'user_id': users[5].id, 'username': users[5].username, 'content': 'Я в деле! Где и когда?'},
            {'post_id': posts[4].id, 'user_id': users[0].id, 'username': users[0].username, 'content': 'Поздравляю! Это отличный результат!'},
            {'post_id': posts[5].id, 'user_id': users[0].id, 'username': users[0].username, 'content': 'Я бы с удовольствием, но у меня нет ракетки'},
        ]
        
        for comment_data in comments_data:
            comment = Comment(**comment_data)
            db.add(comment)
        
        db.commit()
        print(f"✓ Создано комментариев: {len(comments_data)}\n")
        
        # Создаем задачи
        now = datetime.datetime.now(datetime.timezone.utc)
        
        tasks_data = [
            # Обычные задачи aleksandrinsider
            {
                'user_id': users[0].id,
                'title': 'Утренняя зарядка',
                'description': '20 минут упражнений',
                'due_date': now + datetime.timedelta(hours=12),
                'status': 'pending'
            },
            {
                'user_id': users[0].id,
                'title': 'Пробежка 3км',
                'description': 'Вечерняя пробежка в парке',
                'due_date': now + datetime.timedelta(hours=6),
                'status': 'pending'
            },
            {
                'user_id': users[0].id,
                'title': 'Купить абонемент в бассейн',
                'description': 'Посмотреть цены и купить на месяц',
                'due_date': now + datetime.timedelta(days=2),
                'status': 'pending'
            },
            # Задачи делегированные между другими пользователями
            {
                'user_id': users[1].id,
                'title': 'Проверить отчет по проекту',
                'description': 'Проверить аналитический отчет от команды',
                'due_date': now + datetime.timedelta(days=1),
                'status': 'pending',
                'delegated_by': users[3].id,
                'delegated_to_username': 'alex_dev',
                'delegation_status': 'pending',
                'delegation_details': 'Срочно нужна проверка отчета'
            },
            {
                'user_id': users[2].id,
                'title': 'Организовать встречу команды',
                'description': 'Забронировать переговорку и пригласить участников',
                'due_date': now + datetime.timedelta(days=3),
                'status': 'pending',
                'delegated_by': users[4].id,
                'delegated_to_username': 'maria_design',
                'delegation_status': 'accepted',
                'delegation_details': 'Встреча по новому проекту'
            },
            {
                'user_id': users[3].id,
                'title': 'Написать код для новой фичи',
                'description': 'Реализовать функционал экспорта данных',
                'due_date': now + datetime.timedelta(days=5),
                'status': 'pending',
                'delegated_by': users[1].id,
                'delegated_to_username': 'igor_pm',
                'delegation_status': 'accepted',
                'delegation_details': 'Нужен экспорт в CSV и JSON'
            },
            {
                'user_id': users[5].id,
                'title': 'Составить план спринта',
                'description': 'Подготовить backlog на следующие 2 недели',
                'due_date': now + datetime.timedelta(days=4),
                'status': 'pending',
                'delegated_by': users[2].id,
                'delegated_to_username': 'dmitry_qa',
                'delegation_status': 'pending',
                'delegation_details': 'План на следующий спринт'
            },
            # Разные задачи других пользователей
            {
                'user_id': users[1].id,
                'title': 'Рефакторинг кода',
                'description': 'Улучшить структуру модуля аналитики',
                'due_date': now + datetime.timedelta(days=7),
                'status': 'pending'
            },
            {
                'user_id': users[2].id,
                'title': 'Дизайн мобильной версии',
                'description': 'Создать макеты для iOS и Android',
                'due_date': now + datetime.timedelta(days=10),
                'status': 'pending'
            },
            {
                'user_id': users[4].id,
                'title': 'Анализ метрик',
                'description': 'Подготовить отчет по метрикам за месяц',
                'due_date': now + datetime.timedelta(days=2),
                'status': 'completed',
                'actual_completion_time': now - datetime.timedelta(hours=2)
            }
        ]
        
        for task_data in tasks_data:
            task = Task(**task_data)
            db.add(task)
        
        db.commit()
        print(f"✓ Создано задач: {len(tasks_data)}")
        print(f"  - Обычные задачи aleksandrinsider: 3")
        print(f"  - Делегированные задачи между другими: 4")
        print(f"  - Прочие задачи: 3")
        print()
        
        print("=" * 60)
        print("✅ ГОТОВО!")
        print("=" * 60)
        print("\nСоздано:")
        print(f"  Пользователей: {len(users)}")
        print(f"  Профилей: {len(profiles_data)}")
        print(f"  Подписок: {len(subscriptions_data)}")
        print(f"  Постов: {len(posts)}")
        print(f"  Комментариев: {len(comments_data)}")
        print(f"  Задач: {len(tasks_data)}")
        print("\nПользователи:")
        for i, user in enumerate(users):
            tier = subscriptions_data[i]['tier'].value
            print(f"  @{user.username} ({user.first_name}) - {tier}")
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ Ошибка: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    reset_and_seed()
