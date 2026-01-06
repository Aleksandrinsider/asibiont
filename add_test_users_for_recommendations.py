from models import User, UserProfile, SessionLocal
from datetime import datetime, timedelta
import random

session = SessionLocal()

try:
    # Проверяем профиль основного пользователя
    main_user = session.query(User).filter_by(username='aleksandrinsider').first()
    
    if main_user:
        print(f"Основной пользователь: @{main_user.username}")
        profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        
        if profile:
            print(f"Текущий профиль:")
            print(f"  Город: {profile.city}")
            print(f"  Компания: {profile.company}")
            print(f"  Должность: {profile.position}")
            print(f"  Интересы: {profile.interests}")
            print(f"  Навыки: {profile.skills}")
            print(f"  Цели: {profile.goals}")
        else:
            print("  Профиль не заполнен")
    
    print("\n" + "="*50)
    print("Создание тестовых пользователей для рекомендаций...")
    print("="*50 + "\n")
    
    # Удаляем старых тестовых пользователей (210001-210020)
    test_users = session.query(User).filter(User.telegram_id >= 210001, User.telegram_id <= 210020).all()
    for user in test_users:
        # Удаляем профили
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
        session.delete(user)
    session.commit()
    print(f"Удалено {len(test_users)} старых тестовых пользователей\n")
    
    # Тестовые пользователи с разными профилями
    test_users_data = [
        {
            "telegram_id": 210001,
            "username": "alex_python_dev",
            "profile": {
                "city": "Moscow",
                "company": "Yandex",
                "position": "Senior Python Developer",
                "interests": "Python, AI, машинное обучение, backend разработка",
                "skills": "Python, Django, FastAPI, PostgreSQL, Redis, Docker",
                "goals": "Развитие в ML/AI, создание масштабируемых систем"
            }
        },
        {
            "telegram_id": 210002,
            "username": "maria_product_manager",
            "profile": {
                "city": "Moscow",
                "company": "VK",
                "position": "Product Manager",
                "interests": "Product management, UX, аналитика, менеджмент",
                "skills": "Product strategy, User research, Analytics, Agile, Jira",
                "goals": "Запустить успешный продукт с 1M+ пользователей"
            }
        },
        {
            "telegram_id": 210003,
            "username": "ivan_data_scientist",
            "profile": {
                "city": "Saint Petersburg",
                "company": "Sber",
                "position": "Data Scientist",
                "interests": "Machine Learning, Deep Learning, NLP, Computer Vision",
                "skills": "Python, TensorFlow, PyTorch, Pandas, Scikit-learn, SQL",
                "goals": "Стать экспертом в NLP и компьютерном зрении"
            }
        },
        {
            "telegram_id": 210004,
            "username": "anna_frontend_dev",
            "profile": {
                "city": "Moscow",
                "company": "Ozon",
                "position": "Frontend Developer",
                "interests": "React, TypeScript, UI/UX, веб-разработка",
                "skills": "React, TypeScript, JavaScript, HTML/CSS, Redux, Next.js",
                "goals": "Создавать красивые и быстрые интерфейсы"
            }
        },
        {
            "telegram_id": 210005,
            "username": "dmitry_startup_founder",
            "profile": {
                "city": "Moscow",
                "company": "MyStartup",
                "position": "CEO & Founder",
                "interests": "Стартапы, предпринимательство, инвестиции, growth hacking",
                "skills": "Business development, Marketing, Sales, Product management",
                "goals": "Построить единорога, привлечь венчурное финансирование"
            }
        },
        {
            "telegram_id": 210006,
            "username": "elena_designer",
            "profile": {
                "city": "Moscow",
                "company": "Tinkoff",
                "position": "UX/UI Designer",
                "interests": "Design, UX research, психология пользователей, Figma",
                "skills": "Figma, Adobe XD, User research, Prototyping, Design systems",
                "goals": "Создавать дизайн который любят пользователи"
            }
        },
        {
            "telegram_id": 210007,
            "username": "sergey_devops",
            "profile": {
                "city": "Moscow",
                "company": "Mail.ru",
                "position": "DevOps Engineer",
                "interests": "DevOps, CI/CD, Kubernetes, мониторинг, автоматизация",
                "skills": "Docker, Kubernetes, Terraform, Ansible, AWS, GitLab CI",
                "goals": "Построить идеальный CI/CD pipeline"
            }
        },
        {
            "telegram_id": 210008,
            "username": "olga_marketing_lead",
            "profile": {
                "city": "Moscow",
                "company": "Avito",
                "position": "Marketing Lead",
                "interests": "Digital marketing, SMM, контент-маркетинг, брендинг",
                "skills": "Google Analytics, Facebook Ads, SEO, Content strategy",
                "goals": "Увеличить узнаваемость бренда и привлечь 100K новых клиентов"
            }
        },
        {
            "telegram_id": 210009,
            "username": "pavel_ml_engineer",
            "profile": {
                "city": "Saint Petersburg",
                "company": "JetBrains",
                "position": "ML Engineer",
                "interests": "Machine Learning, Python, AI системы, оптимизация моделей",
                "skills": "Python, ML, Deep Learning, MLOps, TensorFlow, PyTorch",
                "goals": "Развертывать ML модели в production"
            }
        },
        {
            "telegram_id": 210010,
            "username": "kate_business_analyst",
            "profile": {
                "city": "Moscow",
                "company": "Kaspersky",
                "position": "Business Analyst",
                "interests": "Бизнес-анализ, процессы, SQL, визуализация данных",
                "skills": "SQL, Excel, Power BI, Business process modeling, Requirements",
                "goals": "Оптимизировать бизнес-процессы компании"
            }
        }
    ]
    
    # Создаем пользователей
    for user_data in test_users_data:
        # Создать пользователя
        user = User(
            telegram_id=user_data["telegram_id"],
            username=user_data["username"],
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.flush()  # Чтобы получить user.id
        
        # Создать профиль
        profile_data = user_data["profile"]
        profile = UserProfile(
            user_id=user.id,
            city=profile_data["city"],
            company=profile_data["company"],
            position=profile_data["position"],
            interests=profile_data["interests"],
            skills=profile_data["skills"],
            goals=profile_data["goals"]
        )
        session.add(profile)
        
        print(f"✅ Создан: @{user.username} - {profile_data['position']} в {profile_data['company']}")
    
    session.commit()
    
    print(f"\n{'='*50}")
    print(f"✅ Успешно создано {len(test_users_data)} тестовых пользователей!")
    print(f"{'='*50}")
    print("\nТеперь они будут отображаться в фильтре 'Рекомендуемые' на основе совпадения интересов, навыков и целей")
    
except Exception as e:
    session.rollback()
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
finally:
    session.close()
