"""
Проверка текущего состояния production PostgreSQL
"""
import os
import sys

# Убираем LOCAL для подключения к production
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, Task, User, UserProfile, Subscription
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757

session = Session()
try:
    # Проверяем пользователя
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    
    if not user:
        print("❌ ПОЛЬЗОВАТЕЛЬ НЕ НАЙДЕН В PRODUCTION БД")
        print(f"Telegram ID: {USER_ID}")
        print("\nВозможно вы не логинились на production dashboard")
        print("Откройте: https://task-production-31b6.up.railway.app/login")
    else:
        print(f"✅ Пользователь найден: {user.username} (ID: {user.id})")
        print(f"First name: {user.first_name}")
        print(f"Timezone: {user.timezone}")
        
        # Задачи
        tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.reminder_time).all()
        print(f"\n📋 ЗАДАЧИ ({len(tasks)}):")
        if tasks:
            for task in tasks:
                emoji = "✅" if task.status == "completed" else "⏳"
                reminder = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "нет"
                print(f"  {emoji} {task.title}")
                print(f"     Статус: {task.status}, Время: {reminder}")
        else:
            print("  (нет задач)")
        
        # Профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"\n👤 ПРОФИЛЬ:")
        if profile:
            print(f"  Город: {profile.city or 'не указан'}")
            print(f"  Интересы: {profile.interests or 'не указаны'}")
            print(f"  Навыки: {profile.skills or 'не указаны'}")
            print(f"  Цели: {profile.goals or 'не указаны'}")
        else:
            print("  (профиль не заполнен)")
        
        # Подписка
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        print(f"\n💳 ПОДПИСКА:")
        if subscription:
            print(f"  Статус: {subscription.status}")
            print(f"  До: {subscription.end_date}")
        else:
            print("  (нет подписки)")
            
finally:
    session.close()

print("\n" + "="*60)
print("Проверьте dashboard: https://task-production-31b6.up.railway.app/dashboard")
print("="*60)
