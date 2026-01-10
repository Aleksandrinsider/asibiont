"""
ЧИСТЫЙ ТЕСТ AI-FIRST: без триггеров, только system prompt
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, UserProfile, Task

async def pure_ai_test():
    session = SessionLocal()

    # Создаем тестового пользователя
    user = session.query(User).filter(User.username == "pure_test").first()
    if not user:
        user = User(telegram_id=999999999, username="pure_test")
        session.add(user)
        session.flush()

        # Профиль
        profile = UserProfile(user_id=user.id, city="TestCity")
        session.add(profile)
        session.commit()
        print("✅ Создан чистый тестовый пользователь")

    print("🧪 ЧИСТЫЙ AI-ТЕСТ: только system prompt, без триггеров")
    print("=" * 60)

    # Тест 1: Профиль
    print("📋 Тест 1: Живу в Санкт-Петербурге и увлекаюсь программированием")
    response1 = await chat_with_ai(
        message="Живу в Санкт-Петербурге и увлекаюсь программированием",
        user_id=user.telegram_id
    )
    print(f"🤖 {response1[:150]}...")

    # Проверяем профиль
    session.refresh(profile)
    print(f"Результат: город='{profile.city}', интересы='{profile.interests}'")
    print()

    # Тест 2: Задача
    print("📋 Тест 2: Напомни мне позвонить маме через 10 минут")
    response2 = await chat_with_ai(
        message="Напомни мне позвонить маме через 10 минут",
        user_id=user.telegram_id
    )
    print(f"🤖 {response2[:150]}...")

    # Проверяем задачу
    task = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like("%маме%")
    ).first()
    if task:
        print(f"Результат: задача '{task.title}' создана, время '{task.reminder_time}'")
    else:
        print("Результат: задача НЕ создана")
    print()

    # Тест 3: Делегирование
    print("📋 Тест 3: @testuser2 проверь отчет до завтра 12:00")
    response3 = await chat_with_ai(
        message="@testuser2 проверь отчет до завтра 12:00",
        user_id=user.telegram_id
    )
    print(f"🤖 {response3[:150]}...")

    # Проверяем делегирование
    delegated = session.query(Task).filter(
        Task.delegated_by == user.id,
        Task.title.like("%отчет%")
    ).first()
    if delegated:
        print(f"Результат: делегировано '{delegated.title}' → {delegated.delegated_to_username}")
    else:
        print("Результат: НЕ делегировано")
    print()

    print("=" * 60)
    print("🎯 ВЕРДИКТ: AI справился без триггеров?")

    success_count = 0
    if profile.city == "Санкт-Петербург": success_count += 1
    if profile.interests and "программирование" in profile.interests: success_count += 1
    if task: success_count += 1
    if delegated: success_count += 1

    print(f"✅ Успешно: {success_count}/4 действий")
    if success_count >= 3:
        print("🎉 AI справился! Можно убрать все триггеры.")
    else:
        print("⚠️ AI не справился полностью. Нужны минимальные триггеры.")

    session.close()

if __name__ == "__main__":
    asyncio.run(pure_ai_test())
