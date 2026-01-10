"""
Быстрый тест основных улучшений
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, UserProfile, Task

async def quick_test():
    session = SessionLocal()

    # Создаем тестового пользователя
    user = session.query(User).filter(User.username == "testuser").first()
    if not user:
        user = User(telegram_id=123456789, username="testuser")
        session.add(user)
        session.flush()

        profile = UserProfile(user_id=user.id, city="Amsterdam")
        session.add(profile)
        session.commit()
    else:
        profile = session.query(UserProfile).filter(UserProfile.user_id == user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, city="Amsterdam")
            session.add(profile)
            session.commit()

    print(f"User ID: {user.id}, Telegram ID: {user.telegram_id}")

    # Тест 1: Профиль
    print("1. Обновление профиля...")
    await chat_with_ai("Живу в Москве", user_id=user.telegram_id)
    session.refresh(profile)
    print(f"   Город: {profile.city} {'✅' if profile.city == 'Москва' else '❌'}")

    # Тест 2: Задача
    print("2. Добавление задачи...")
    await chat_with_ai("Создай задачу: позвонить другу через 5 минут", user_id=user.telegram_id)
    task = session.query(Task).filter(Task.user_id == user.id, Task.title.like("%звонить%")).first()
    print(f"   Задача: {task.title if task else 'None'} {'✅' if task else '❌'}")

    # Тест 3: Завершение
    if task:
        print("3. Завершение задачи...")
        await chat_with_ai(f"Завершил задачу {task.title}", user_id=user.telegram_id)
        session.refresh(task)
        print(f"   Статус: {task.status} {'✅' if task.status == 'completed' else '❌'}")

    print("=" * 40)
    print("🏁 Быстрый тест завершен!")

    session.close()

if __name__ == "__main__":
    asyncio.run(quick_test())
