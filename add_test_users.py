"""
Добавляет тестовых пользователей с разными интересами для проверки find_partners
"""
from models import Session, User, UserProfile
from datetime import datetime, timedelta

def add_test_users():
    session = Session()
    
    test_users_data = [
        {
            "telegram_id": 111111111,
            "username": "alex_dev",
            "city": "Москва",
            "interests": "AI, машинное обучение, Python",
            "skills": "Python, TensorFlow, Data Science",
            "goals": "Создать AI-стартап, изучить GPT"
        },
        {
            "telegram_id": 222222222,
            "username": "maria_designer",
            "city": "Москва",
            "interests": "UI/UX дизайн, стартапы, веб-разработка",
            "skills": "Figma, Sketch, Adobe XD",
            "goals": "Запустить свой SaaS продукт"
        },
        {
            "telegram_id": 333333333,
            "username": "ivan_backend",
            "city": "Санкт-Петербург",
            "interests": "Backend разработка, микросервисы, AI",
            "skills": "Python, Go, Docker, Kubernetes",
            "goals": "Построить высоконагруженную систему"
        },
        {
            "telegram_id": 444444444,
            "username": "olga_product",
            "city": "Москва",
            "interests": "Product management, стартапы, AI продукты",
            "skills": "Roadmap planning, User research, Analytics",
            "goals": "Запустить AI-powered продукт"
        },
        {
            "telegram_id": 555555555,
            "username": "dmitry_ml",
            "city": "Москва",
            "interests": "Deep Learning, NLP, Computer Vision",
            "skills": "PyTorch, OpenCV, Transformers",
            "goals": "Исследования в области AI"
        }
    ]
    
    for user_data in test_users_data:
        # Проверяем, существует ли пользователь
        existing_user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
        if existing_user:
            print(f"✓ Пользователь @{user_data['username']} уже существует")
            continue
        
        # Создаем пользователя
        user = User(
            telegram_id=user_data["telegram_id"],
            username=user_data["username"]
        )
        session.add(user)
        session.flush()  # Чтобы получить user.id
        
        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            city=user_data["city"],
            interests=user_data["interests"],
            skills=user_data["skills"],
            goals=user_data["goals"]
        )
        session.add(profile)
        
        print(f"✓ Добавлен @{user_data['username']} - {user_data['interests']}")
    
    session.commit()
    session.close()
    print("\n✅ Тестовые пользователи добавлены!")
    print("Теперь find_partners сможет находить потенциальных партнеров.")

if __name__ == "__main__":
    add_test_users()
