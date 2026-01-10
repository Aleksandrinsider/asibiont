"""
Тест AI парсинга профиля через tool calling (без регексов)
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, UserProfile

async def test_profile_update():
    session = SessionLocal()
    
    # Создаем тестового пользователя
    user = session.query(User).filter(User.username == "testuser").first()
    if not user:
        from datetime import datetime, timedelta
        user = User(
            telegram_id=123456789,
            username="testuser"
        )
        session.add(user)
        session.flush()
        
        # Добавляем подписку
        from models import Subscription
        sub = Subscription(
            user_id=user.id,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=30),
            status='active'
        )
        session.add(sub)
        session.commit()
        print("✅ Создан тестовый пользователь с подпиской")
    
    # Проверяем профиль
    profile = session.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id, city="Amsterdam")
        session.add(profile)
        session.commit()
    
    print(f"📊 До обновления:")
    print(f"  Город: {profile.city or 'Не указан'}")
    print(f"  Интересы: {profile.interests or 'Не указаны'}")
    print()
    
    # Тестовое сообщение - то же, что отправлял пользователь
    test_message = "Живу в Москве и увлекаюсь технологиями, ии и спортом"
    print(f"💬 Сообщение: {test_message}")
    print()
    
    # Отправляем в AI (используем telegram_id, а не id!)
    response = await chat_with_ai(
        message=test_message,
        user_id=user.telegram_id
    )
    
    print(f"🤖 AI ответ:\n{response}")
    print()
    
    # Перезагружаем профиль из БД
    session.refresh(profile)
    
    print(f"📊 После обновления:")
    print(f"  Город: {profile.city or 'Не указан'}")
    print(f"  Интересы: {profile.interests or 'Не указаны'}")
    print()
    
    # Проверяем результат
    if profile.city == "Москва":
        print("✅ Город обновлен правильно!")
    else:
        print(f"❌ Город не обновлен: {profile.city}")
    
    if profile.interests and "технологии" in profile.interests.lower():
        print("✅ Интересы обновлены!")
    else:
        print(f"❌ Интересы не обновлены: {profile.interests}")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_profile_update())
