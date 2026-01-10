"""
Тест AI парсинга задач через tool calling (без регексов)
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, UserProfile, Task

async def test_task_management():
    session = SessionLocal()
    
    # Используем тестового пользователя
    user = session.query(User).filter(User.username == "testuser").first()
    if not user:
        print("❌ Пользователь не найден, создайте его через test_ai_profile.py")
        return
    
    print(f"🧪 Тестирование AI парсинга задач")
    print(f"=" * 50)
    print()
    
    # Тест 1: Добавление задачи с относительным временем
    print("📋 Тест 1: Напомни купить хлеб через 5 минут")
    response1 = await chat_with_ai(
        message="Напомни купить хлеб через 5 минут",
        user_id=user.telegram_id
    )
    print(f"🤖 {response1[:200]}...")
    print()
    
    # Проверяем что задача создана
    task1 = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like("%хлеб%")
    ).first()
    
    if task1:
        print(f"✅ Задача создана: {task1.title}")
        print(f"   Время: {task1.reminder_time}")
    else:
        print("❌ Задача не создана")
    print()
    
    # Тест 2: Добавление задачи с абсолютным временем
    print("📋 Тест 2: Создай задачу позвонить маме завтра в 15:00")
    response2 = await chat_with_ai(
        message="Создай задачу позвонить маме завтра в 15:00",
        user_id=user.telegram_id
    )
    print(f"🤖 {response2[:200]}...")
    print()
    
    task2 = session.query(Task).filter(
        Task.user_id == user.id,
        Task.title.like("%маме%")
    ).first()
    
    if task2:
        print(f"✅ Задача создана: {task2.title}")
        print(f"   Время: {task2.reminder_time}")
    else:
        print("❌ Задача не создана")
    print()
    
    # Тест 3: Завершение задачи
    if task1:
        print(f"📋 Тест 3: Сделал {task1.title}")
        response3 = await chat_with_ai(
            message=f"Сделал {task1.title}",
            user_id=user.telegram_id
        )
        print(f"🤖 {response3[:200]}...")
        print()
        
        session.refresh(task1)
        if task1.status == 'completed':
            print(f"✅ Задача завершена")
        else:
            print(f"❌ Задача не завершена: {task1.status}")
        print()
    
    # Тест 4: Делегирование
    print("📋 Тест 4: @testuser2 сделай отчет до завтра 18:00")
    response4 = await chat_with_ai(
        message="@testuser2 сделай отчет до завтра 18:00",
        user_id=user.telegram_id
    )
    print(f"🤖 {response4[:200]}...")
    print()
    
    delegated = session.query(Task).filter(
        Task.delegated_by == user.id,
        Task.title.like("%отчет%")
    ).first()
    
    if delegated:
        print(f"✅ Задача делегирована: {delegated.title}")
        print(f"   Кому: {delegated.delegated_to_username}")
        print(f"   Дедлайн: {delegated.reminder_time}")
    else:
        print("❌ Задача не делегирована")
    print()
    
    print("=" * 50)
    print("🏁 Тесты завершены!")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_task_management())
