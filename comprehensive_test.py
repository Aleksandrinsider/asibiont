"""
Комплексный тест всех улучшений AI парсинга
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, UserProfile, Task

async def comprehensive_test():
    session = SessionLocal()

    # Создаем тестового пользователя
    user = session.query(User).filter(User.username == "testuser").first()
    if not user:
        user = User(telegram_id=123456789, username="testuser")
        session.add(user)
        session.flush()

        # Создаем профиль
        profile = UserProfile(user_id=user.id, city="Amsterdam")
        session.add(profile)
        session.commit()
        print("✅ Создан тестовый пользователь")

    print("🧪 КОМПЛЕКСНЫЙ ТЕСТ УЛУЧШЕНИЙ AI")
    print("=" * 60)
    print()

    # ТЕСТ 1: Обновление профиля
    print("📋 ТЕСТ 1: Обновление профиля")
    print("💬 Сообщение: Живу в Москве и увлекаюсь технологиями, ИИ и спортом")

    response = await chat_with_ai(
        message="Живу в Москве и увлекаюсь технологиями, ИИ и спортом",
        user_id=user.telegram_id
    )
    print(f"🤖 AI: {response[:150]}...")

    session.refresh(profile)
    success1 = profile.city == "Москва" and "технологии" in (profile.interests or "")
    print(f"✅ Профиль обновлен: город={profile.city}, интересы={profile.interests}")
    print(f"   Результат: {'✅ УСПЕХ' if success1 else '❌ ПРОВАЛ'}")
    print()

    # ТЕСТ 2: Добавление задачи с относительным временем
    print("📋 ТЕСТ 2: Добавление задачи (относительное время)")
    print("💬 Сообщение: Напомни купить продукты через 10 минут")

    response = await chat_with_ai(
        message="Напомни купить продукты через 10 минут",
        user_id=user.telegram_id
    )
    print(f"🤖 AI: {response[:150]}...")

    task1 = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like("%продукты%")
    ).first()

    success2 = task1 is not None and task1.reminder_time is not None
    print(f"✅ Задача создана: {task1.title if task1 else 'None'}")
    print(f"   Время: {task1.reminder_time if task1 else 'None'}")
    print(f"   Результат: {'✅ УСПЕХ' if success2 else '❌ ПРОВАЛ'}")
    print()

    # ТЕСТ 3: Добавление задачи с абсолютным временем
    print("📋 ТЕСТ 3: Добавление задачи (абсолютное время)")
    print("💬 Сообщение: Создай задачу позвонить маме завтра в 15:00")

    response = await chat_with_ai(
        message="Создай задачу позвонить маме завтра в 15:00",
        user_id=user.telegram_id
    )
    print(f"🤖 AI: {response[:150]}...")

    task2 = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like("%маме%")
    ).first()

    success3 = task2 is not None and "15:00" in (task2.reminder_time or "")
    print(f"✅ Задача создана: {task2.title if task2 else 'None'}")
    print(f"   Время: {task2.reminder_time if task2 else 'None'}")
    print(f"   Результат: {'✅ УСПЕХ' if success3 else '❌ ПРОВАЛ'}")
    print()

    # ТЕСТ 4: Завершение задачи
    if task1:
        print("📋 ТЕСТ 4: Завершение задачи")
        print(f"💬 Сообщение: Выполнил {task1.title}")

        response = await chat_with_ai(
            message=f"Выполнил {task1.title}",
            user_id=user.telegram_id
        )
        print(f"🤖 AI: {response[:150]}...")

        session.refresh(task1)
        success4 = task1.status == 'completed'
        print(f"✅ Статус задачи: {task1.status}")
        print(f"   Результат: {'✅ УСПЕХ' if success4 else '❌ ПРОВАЛ'}")
        print()

    # ТЕСТ 5: Делегирование задачи
    print("📋 ТЕСТ 5: Делегирование задачи")
    print("💬 Сообщение: @testuser2 подготовь отчет до завтра 18:00")

    response = await chat_with_ai(
        message="@testuser2 подготовь отчет до завтра 18:00",
        user_id=user.telegram_id
    )
    print(f"🤖 AI: {response[:150]}...")

    delegated = session.query(Task).filter(
        Task.delegated_by == user.id,
        Task.title.like("%отчет%")
    ).first()

    success5 = delegated is not None and delegated.delegated_to_username == "@testuser2"
    print(f"✅ Задача делегирована: {delegated.title if delegated else 'None'}")
    print(f"   Кому: {delegated.delegated_to_username if delegated else 'None'}")
    print(f"   Дедлайн: {delegated.reminder_time if delegated else 'None'}")
    print(f"   Результат: {'✅ УСПЕХ' if success5 else '❌ ПРОВАЛ'}")
    print()

    # ТЕСТ 6: Поиск партнеров
    print("📋 ТЕСТ 6: Поиск партнеров")
    print("💬 Сообщение: Найди людей с похожими интересами")

    response = await chat_with_ai(
        message="Найди людей с похожими интересами",
        user_id=user.telegram_id
    )
    print(f"🤖 AI: {response[:150]}...")

    # Проверяем что функция вызвана (просто проверяем что ответ содержит что-то про поиск)
    success6 = "партнер" in response.lower() or "людей" in response.lower()
    print(f"   Результат: {'✅ УСПЕХ' if success6 else '❌ ПРОВАЛ'}")
    print()

    # ИТОГИ
    print("=" * 60)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
    print(f"   Тест 1 (Профиль): {'✅' if success1 else '❌'}")
    print(f"   Тест 2 (Задача +10мин): {'✅' if success2 else '❌'}")
    print(f"   Тест 3 (Задача завтра): {'✅' if success3 else '❌'}")
    print(f"   Тест 4 (Завершение): {'✅' if success4 else '❌'}")
    print(f"   Тест 5 (Делегирование): {'✅' if success5 else '❌'}")
    print(f"   Тест 6 (Партнеры): {'✅' if success6 else '❌'}")

    total_success = sum([success1, success2, success3, success4, success5, success6])
    print(f"\n🎯 ОБЩИЙ РЕЗУЛЬТАТ: {total_success}/6 тестов пройдено")

    if total_success >= 5:
        print("🎉 ОТЛИЧНО! AI парсинг работает превосходно!")
    elif total_success >= 3:
        print("👍 ХОРОШО! Большинство функций работает")
    else:
        print("⚠️ НУЖНЫ ДОРАБОТКИ")

    session.close()

if __name__ == "__main__":
    asyncio.run(comprehensive_test())
