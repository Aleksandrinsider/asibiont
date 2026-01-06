"""Создаёт демо-контакты для всех трёх фильтров"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile, Task
from datetime import datetime, timedelta

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///bot.db')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
session = SessionLocal()

try:
    # Находим основного пользователя
    main_user = session.query(User).filter_by(telegram_id=146333757).first()
    if not main_user:
        print("❌ Основной пользователь не найден")
        exit(1)
    
    print(f"✅ Основной пользователь: {main_user.username}")
    
    # Обновляем профиль основного пользователя для рекомендаций
    main_profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
    if not main_profile:
        main_profile = UserProfile(
            user_id=main_user.id,
            interests='Python, менеджмент, проектирование',
            skills='Project Management, Python, SQL',
            position='Product Manager'
        )
        session.add(main_profile)
    else:
        main_profile.interests = 'Python, менеджмент, проектирование'
        main_profile.skills = 'Project Management, Python, SQL'
    
    # 1. Создаём рекомендуемых контактов (со схожими интересами)
    recommended_users = [
        {
            'id': 200001,
            'telegram_id': 200001,
            'username': 'anna_marketing',
            'first_name': 'Анна',
            'position': 'Маркетолог',
            'city': 'Москва',
            'interests': 'SMM, контент-маркетинг, менеджмент'  # Общий интерес: менеджмент
        },
        {
            'id': 200002,
            'telegram_id': 200002,
            'username': 'sergey_analyst',
            'first_name': 'Сергей',
            'position': 'Аналитик данных',
            'city': 'Санкт-Петербург',
            'interests': 'Python, SQL, визуализация'  # Общие интересы: Python, SQL
        },
        {
            'id': 200003,
            'telegram_id': 200003,
            'username': 'elena_designer',
            'first_name': 'Елена',
            'position': 'UI/UX дизайнер',
            'city': 'Казань',
            'interests': 'Figma, проектирование, UX'  # Общий интерес: проектирование
        }
    ]
    
    for user_data in recommended_users:
        user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
        if not user:
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name']
            )
            session.add(user)
            session.flush()
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                position=user_data['position'],
                city=user_data['city'],
                interests=user_data['interests']
            )
            session.add(profile)
    
    print("✅ Создано 3 рекомендуемых контакта")
    
    # 2. Создаём пользователей, которые делегируют МНЕ
    delegating_to_me_users = [
        {
            'telegram_id': 200004,
            'username': 'boss_ivan',
            'first_name': 'Иван',
            'position': 'CEO',
            'interests': 'Стратегия, рост бизнеса',
            'tasks': ['Подготовить презентацию для инвесторов', 'Проанализировать конкурентов']
        },
        {
            'telegram_id': 200005,
            'username': 'manager_olga',
            'first_name': 'Ольга',
            'position': 'Проект-менеджер',
            'interests': 'Agile, Scrum',
            'tasks': ['Обновить дорожную карту проекта', 'Провести ретроспективу спринта', 'Составить отчет по метрикам']
        }
    ]
    
    for user_data in delegating_to_me_users:
        user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
        if not user:
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name']
            )
            session.add(user)
            session.flush()
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                position=user_data['position'],
                interests=user_data['interests']
            )
            session.add(profile)
        
        # Создаём задачи, делегированные мне
        for i, task_title in enumerate(user_data['tasks']):
            existing_task = session.query(Task).filter_by(
                user_id=user.id,
                title=f"{task_title} - делегирована от @{user.username}"
            ).first()
            if not existing_task:
                task = Task(
                    user_id=user.id,
                    title=f"{task_title} - делегирована от @{user.username}",
                    description=f"Задача от {user.first_name}",
                    status='pending',
                    priority='high' if i == 0 else 'medium',
                    created_at=datetime.utcnow() - timedelta(days=i),
                    delegated_to_username=main_user.username,
                    delegation_status='accepted'
                )
                session.add(task)
    
    print("✅ Создано 2 пользователя, делегирующих мне (5 задач)")
    
    # 3. Создаём пользователей, КОТОРЫМ Я делегирую
    delegating_by_me_users = [
        {
            'telegram_id': 200006,
            'username': 'junior_dmitry',
            'first_name': 'Дмитрий',
            'position': 'Junior Developer',
            'interests': 'JavaScript, React',
            'tasks': ['Исправить баг в форме регистрации', 'Написать unit-тесты']
        },
        {
            'telegram_id': 200007,
            'username': 'intern_maria',
            'first_name': 'Мария',
            'position': 'Стажёр',
            'interests': 'Обучение, дизайн',
            'tasks': ['Сделать макет новой страницы']
        }
    ]
    
    for user_data in delegating_by_me_users:
        user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
        if not user:
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['first_name']
            )
            session.add(user)
            session.flush()
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                position=user_data['position'],
                interests=user_data['interests']
            )
            session.add(profile)
        
        # Создаём задачи, которые Я делегирую им
        for i, task_title in enumerate(user_data['tasks']):
            existing_task = session.query(Task).filter_by(
                user_id=main_user.id,
                title=f"{task_title} - делегирована на @{user.username}"
            ).first()
            if not existing_task:
                task = Task(
                    user_id=main_user.id,
                    title=f"{task_title} - делегирована на @{user.username}",
                    description=f"Задача для {user.first_name}",
                    status='pending',
                    priority='medium',
                    created_at=datetime.utcnow() - timedelta(days=i),
                    delegated_to_username=user.username,
                    delegation_status='pending'
                )
                session.add(task)
    
    print("✅ Создано 2 пользователя, которым я делегирую (3 задачи)")
    
    session.commit()
    
    print("\n📊 Итоговая статистика:")
    print(f"- Рекомендуемые контакты: 3 (со схожими интересами)")
    print(f"- Делегируют мне: 2 (5 задач)")
    print(f"- Делегирую я: 2 (3 задачи)")
    print(f"\n✅ Всего создано 7 демо-пользователей и 8 задач делегирования")
    
    # Проверяем фильтры
    print("\n🔍 Проверка фильтров:")
    
    # Делегируют мне
    delegated_to_me = session.query(Task).filter(
        Task.delegated_to_username == main_user.username,
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    unique_delegators = set([t.user_id for t in delegated_to_me])
    print(f"Делегируют мне: {len(unique_delegators)} пользователей ({len(delegated_to_me)} задач)")
    
    # Делегирую я
    delegated_by_me = session.query(Task).filter(
        Task.user_id == main_user.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    unique_delegatees = set([t.delegated_to_username for t in delegated_by_me])
    print(f"Делегирую я: {len(unique_delegatees)} пользователей ({len(delegated_by_me)} задач)")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
    session.rollback()
finally:
    session.close()
