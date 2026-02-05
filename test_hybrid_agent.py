"""
Тест улучшенного гибридного автономного агента
"""

import asyncio
import sys
import os
from datetime import datetime, timezone, timedelta

# Настройка путей
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Task, Base, engine
from ai_integration.chat import chat_with_ai

# Инициализируем БД
Base.metadata.create_all(engine)

async def test_hybrid_agent():
    """Тестирование гибридного автономного агента"""
    
    # Тестовый пользователь
    TEST_USER_ID = 123456789
    
    # Создаем или получаем пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
    if not user:
        user = User(telegram_id=TEST_USER_ID, username="test_user")
        session.add(user)
        session.commit()
    
    user_db_id = user.id  # Сохраняем ID для дальнейшего использования
    
    # Очищаем старые задачи
    session.query(Task).filter_by(user_id=user_db_id).delete()
    session.commit()
    session.close()
    
    print("=" * 80)
    print("🧪 ТЕСТ УЛУЧШЕННОГО ГИБРИДНОГО АВТОНОМНОГО АГЕНТА")
    print("=" * 80)
    
    # Тест 1: Создание задачи
    print("\n📝 ТЕСТ 1: Создание задачи")
    print("-" * 80)
    message = "создай задачу купить молоко завтра в 9 утра"
    print(f"Пользователь: {message}")
    
    response = await chat_with_ai(
        message=message,
        user_id=TEST_USER_ID,
        context=[]
    )
    
    print(f"\n🤖 AI: {response['response']}")
    
    # Проверяем, что задача создана
    session = Session()
    tasks = session.query(Task).filter_by(user_id=user_db_id).all()
    print(f"\n✅ Создано задач: {len(tasks)}")
    for task in tasks:
        print(f"   - {task.title} ({task.due_date})")
    session.close()
    
    # Тест 2: Список задач
    print("\n\n📋 ТЕСТ 2: Список задач")
    print("-" * 80)
    message = "покажи мои задачи"
    print(f"Пользователь: {message}")
    
    response = await chat_with_ai(
        message=message,
        user_id=TEST_USER_ID,
        context=[]
    )
    
    print(f"\n🤖 AI: {response['response']}")
    
    # Тест 3: Завершение задачи
    print("\n\n✅ ТЕСТ 3: Завершение задачи")
    print("-" * 80)
    message = "готово, купил молоко"
    print(f"Пользователь: {message}")
    
    response = await chat_with_ai(
        message=message,
        user_id=TEST_USER_ID,
        context=[]
    )
    
    print(f"\n🤖 AI: {response['response']}")
    
    # Проверяем, что задача завершена
    session = Session()
    completed_tasks = session.query(Task).filter_by(user_id=user_db_id, status='completed').all()
    print(f"\n✅ Завершено задач: {len(completed_tasks)}")
    session.close()
    
    # Тест 4: Создание еще задач
    print("\n\n📝 ТЕСТ 4: Создание нескольких задач")
    print("-" * 80)
    
    test_messages = [
        "создай задачу позвонить маме послезавтра в 18:00",
        "создай задачу встреча с командой через 2 часа",
        "напомни мне про спорт каждый день в 7 утра"
    ]
    
    for msg in test_messages:
        print(f"\nПользователь: {msg}")
        response = await chat_with_ai(
            message=msg,
            user_id=TEST_USER_ID,
            context=[]
        )
        print(f"🤖 AI: {response['response'][:150]}...")
        await asyncio.sleep(0.5)
    
    # Итоговая статистика
    session = Session()
    all_tasks = session.query(Task).filter_by(user_id=user_db_id).all()
    active_tasks = session.query(Task).filter(Task.user_id == user_db_id, Task.status != 'completed').all()
    completed_tasks = session.query(Task).filter_by(user_id=user_db_id, status='completed').all()
    
    print("\n\n" + "=" * 80)
    print("📊 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    print(f"Всего задач: {len(all_tasks)}")
    print(f"Активных: {len(active_tasks)}")
    print(f"Завершенных: {len(completed_tasks)}")
    print("\nАктивные задачи:")
    for task in active_tasks:
        recurring = " (повторяющаяся)" if task.is_recurring else ""
        print(f"  - {task.title} → {task.due_date}{recurring}")
    
    session.close()
    
    print("\n✨ Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_hybrid_agent())
