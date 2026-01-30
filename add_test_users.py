#!/usr/bin/env python3
"""
Скрипт для добавления тестовых пользователей в локальную БД
"""
import os
from datetime import datetime, timedelta
import sys

# Установим LOCAL=1 для использования SQLite
os.environ['LOCAL'] = '1'

# Добавим путь для импорта models
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Subscription, SubscriptionTier

def create_test_users():
    """Создать 5 тестовых пользователей с разными тарифами"""
    session = Session()
    
    try:
        # Данные тестовых пользователей
        test_users = [
            {
                'telegram_id': 100001, 
                'username': 'sports_alex', 
                'first_name': 'Александр',
                'tier': SubscriptionTier.LIGHT,
                'city': 'Москва',
                'company': 'ИТ Консалтинг',
                'position': 'Разработчик',
                'skills': 'Python, JavaScript, спорт'
            },
            {
                'telegram_id': 100002, 
                'username': 'maria_fitness', 
                'first_name': 'Мария',
                'tier': SubscriptionTier.STANDARD,
                'city': 'Санкт-Петербург',
                'company': 'Фитнес центр',
                'position': 'Тренер',
                'skills': 'фитнес, йога, плавание'
            },
            {
                'telegram_id': 100003, 
                'username': 'dmitry_runner', 
                'first_name': 'Дмитрий',
                'tier': SubscriptionTier.PREMIUM,
                'city': 'Екатеринбург',
                'company': 'Спорт Маркет',
                'position': 'Менеджер',
                'skills': 'бег, велосипед, маркетинг'
            },
            {
                'telegram_id': 100004, 
                'username': 'anna_gym', 
                'first_name': 'Анна',
                'tier': SubscriptionTier.LIGHT,
                'city': 'Новосибирск',
                'company': 'Спортзал',
                'position': 'Администратор',
                'skills': 'тренажеры, бодибилдинг, управление'
            },
            {
                'telegram_id': 100005, 
                'username': 'sergey_soccer', 
                'first_name': 'Сергей',
                'tier': SubscriptionTier.STANDARD,
                'city': 'Казань',
                'company': 'Футбольный клуб',
                'position': 'Тренер',
                'skills': 'футбол, тактика, командная работа'
            }
        ]
        
        created_users = []
        
        for idx, user_data in enumerate(test_users):
            # Проверяем существует ли пользователь
            existing = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if existing:
                print(f"⚠️  Пользователь {user_data['username']} уже существует, пропускаем")
                continue
            
            # Создаем пользователя
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name'],
                subscription_tier=user_data['tier']
            )
            session.add(user)
            session.flush()  # Получить ID для профиля
            
            # Создаем профиль
            profile = UserProfile(
                user_id=user.id,
                city=user_data['city'],
                company=user_data['company'],
                position=user_data['position'],
                interests='спорт, здоровье, активный образ жизни',  # Общий интерес спорт
                skills=user_data['skills'],
                goals='поддерживать форму, найти спортивных партнеров'
            )
            session.add(profile)
            
            # Создаем подписку
            now = datetime.utcnow()
            end_date = now + timedelta(days=30)  # Месяц подписки
            
            subscription = Subscription(
                user_id=user.id,
                telegram_id=user_data['telegram_id'],
                telegram_username=user_data['username'],
                username=user_data['username'],
                tier=user_data['tier'],
                status='active',
                start_date=now,
                end_date=end_date,
                plan='monthly',
                subscriber_number=1000 + idx  # Уникальный номер подписчика
            )
            session.add(subscription)
            
            created_users.append({
                'username': user_data['username'],
                'first_name': user_data['first_name'], 
                'tier': user_data['tier'].value,
                'telegram_id': user_data['telegram_id']
            })
        
        session.commit()
        
        print("\n" + "="*60)
        print("🎉 ТЕСТОВЫЕ ПОЛЬЗОВАТЕЛИ СОЗДАНЫ")
        print("="*60)
        
        for user in created_users:
            print(f"\n👤 {user['first_name']} (@{user['username']})")
            print(f"   Тариф: {user['tier']}")
            print(f"   Telegram ID: {user['telegram_id']}")
            print(f"   Интересы: спорт, здоровье, активный образ жизни")
        
        print(f"\n✅ Всего создано: {len(created_users)} пользователей")
        print("\n📱 ССЫЛКИ ДЛЯ ВХОДА В ПАНЕЛЬ:")
        print("-"*60)
        
        for user in created_users:
            print(f"🔗 {user['first_name']}: http://localhost:8080/direct_login?user_id={user['telegram_id']}")
        
        print("\n🚀 Для запуска локального сервера:")
        print("   python main.py")
        print("\n🌐 Основная панель: http://localhost:8080/dashboard")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка: {e}")
        raise
    finally:
        session.close()

if __name__ == "__main__":
    print("🔄 Создание тестовых пользователей...")
    create_test_users()