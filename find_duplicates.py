"""
Поиск дубликатов пользователей
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, Task, UserProfile, Subscription
import sys

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757

session = Session()
try:
    users = session.query(User).filter_by(telegram_id=USER_ID).all()
    
    print(f"Найдено пользователей с telegram_id={USER_ID}: {len(users)}\n")
    
    for user in users:
        print(f"{'='*60}")
        print(f"User DB ID: {user.id}")
        print(f"Telegram ID: {user.telegram_id}")
        print(f"Username: {user.username}")
        print(f"Created: {user.created_at}")
        
        # Профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"Профиль: {profile.city}, {profile.interests}")
        
        # Задачи
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"Задач: {len(tasks)}")
        for task in tasks:
            print(f"  - {task.title} [{task.status}]")
        
        # Подписка
        sub = session.query(Subscription).filter_by(user_id=user.id).first()
        if sub:
            print(f"Подписка: {sub.status} до {sub.end_date}")
        print()
        
    if len(users) > 1:
        print("⚠️ НАЙДЕНЫ ДУБЛИКАТЫ!")
        print("Нужно удалить старые записи")
        
finally:
    session.close()
