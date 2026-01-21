"""
Создание тестовых данных для проверки обеих проблем
"""
import os
os.environ['LOCAL'] = '1'
from models import Base, User, Task, Session, SubscriptionTier, UserProfile
from datetime import datetime, timedelta, timezone
import random

def create_test_data():
    session = Session()
    try:
        # Создаем или обновляем главного пользователя
        main_user = session.query(User).filter(User.username.ilike('aleksandrinsider')).first()
        if not main_user:
            main_user = User(
                telegram_id=567890123,
                username='aleksandrinsider',
                first_name='Aleksandr',
                subscription_tier=SubscriptionTier.GOLD,
                timezone='Europe/Moscow'
            )
            session.add(main_user)
            session.commit()
            print(f"✅ Создан пользователь aleksandrinsider (ID: {main_user.id})")
        else:
            main_user.subscription_tier = SubscriptionTier.GOLD
            session.commit()
            print(f"✅ Обновлен пользователь aleksandrinsider -> Gold tier")
        
        # Создаем профиль для главного пользователя
        main_profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        if not main_profile:
            main_profile = UserProfile(
                user_id=main_user.id,
                city='Москва',
                interests='AI, стартапы, инвестиции',
                skills='Python, менеджмент, аналитика',
                goals='Развитие бизнеса, нетворкинг'
            )
            session.add(main_profile)
            session.commit()
        
        # Создаем 3 тестовых Gold пользователя
        gold_users_data = [
            {
                'telegram_id': 111111111,
                'username': 'golduser1',
                'first_name': 'Иван',
                'city': 'Москва',
                'company': 'TechCorp',
                'position': 'CEO',
                'interests': 'AI, стартапы',
                'skills': 'Python, лидерство'
            },
            {
                'telegram_id': 222222222,
                'username': 'golduser2',
                'first_name': 'Мария',
                'city': 'Санкт-Петербург',
                'company': 'InnovateLab',
                'position': 'CTO',
                'interests': 'инвестиции, технологии',
                'skills': 'менеджмент, аналитика'
            },
            {
                'telegram_id': 333333333,
                'username': 'golduser3',
                'first_name': 'Петр',
                'city': 'Москва',
                'company': 'StartupHub',
                'position': 'Founder',
                'interests': 'стартапы, нетворкинг',
                'skills': 'Python, продажи'
            }
        ]
        
        created_gold_users = []
        for data in gold_users_data:
            user = session.query(User).filter_by(telegram_id=data['telegram_id']).first()
            if not user:
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
                created_gold_users.append(user)
                print(f"✅ Создан Gold пользователь: @{data['username']}")
            else:
                user.subscription_tier = SubscriptionTier.GOLD
                created_gold_users.append(user)
                print(f"✅ Обновлен пользователь @{data['username']} -> Gold tier")
        
        session.commit()
        
        # Создаем делегированные задачи от Gold пользователей к aleksandrinsider
        delegation_tasks = [
            {
                'title': 'Подготовить презентацию для инвесторов',
                'description': 'Нужна презентация на 10 слайдов о нашем продукте',
                'delegation_details': 'Презентация должна включать метрики, прогнозы и конкурентный анализ'
            },
            {
                'title': 'Провести код-ревью нового модуля',
                'description': 'Проверить качество кода в ветке feature/new-module',
                'delegation_details': 'Обратить внимание на архитектуру и тесты'
            },
            {
                'title': 'Организовать встречу с потенциальным партнером',
                'description': 'Договориться о встрече с представителем TechStart',
                'delegation_details': 'Встреча должна состояться до конца недели'
            }
        ]
        
        # Удаляем старые делегированные задачи
        session.query(Task).filter(
            Task.delegated_to_username.ilike('aleksandrinsider')
        ).delete()
        session.commit()
        
        for i, task_data in enumerate(delegation_tasks):
            delegator = created_gold_users[i % len(created_gold_users)]
            reminder_time = datetime.now(timezone.utc) + timedelta(days=i+1, hours=10)
            
            task = Task(
                user_id=delegator.id,
                title=task_data['title'],
                description=task_data['description'],
                delegated_to_username='aleksandrinsider',
                delegation_status='pending',
                delegation_details=task_data['delegation_details'],
                status='pending',
                reminder_time=reminder_time
            )
            session.add(task)
            print(f"✅ Создана делегированная задача от @{delegator.username}: '{task_data['title']}'")
        
        session.commit()
        
        print("\n" + "="*60)
        print("✅ ГОТОВО! Тестовые данные созданы:")
        print(f"   - 1 главный пользователь (aleksandrinsider) с Gold тарифом")
        print(f"   - {len(created_gold_users)} Gold пользователей для 'Премиум статус'")
        print(f"   - {len(delegation_tasks)} делегированных задач в статусе 'pending'")
        print("\nТеперь вы можете:")
        print("1. Войти в веб-интерфейс под aleksandrinsider")
        print("2. Увидеть делегированные задачи со статусом 'Ожидает подтверждения'")
        print("3. Открыть 'Премиум статус' и увидеть Gold пользователей")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    create_test_data()
