"""
Тест обнаружения дубликатов с fuzzy matching
"""
import asyncio
import os
from datetime import datetime
import pytz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, Subscription
from ai_integration.chat import process_message

# Настройка
os.environ['FREE_ACCESS_MODE'] = '1'
os.environ['DEEPSEEK_API_KEY'] = os.environ.get('DEEPSEEK_API_KEY', '')

async def test_duplicate_detection():
    # Создаём тестовую БД
    engine = create_engine('sqlite:///test_duplicate.db')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Создаём тестового пользователя
    user = User(
        telegram_id=123456789,
        username="test_user",
        subscription_tier=Subscription.GOLD,
        timezone="Europe/Moscow"
    )
    session.add(user)
    session.commit()
    
    print("=" * 80)
    print("ТЕСТ ОБНАРУЖЕНИЯ ДУБЛИКАТОВ")
    print("=" * 80)
    
    # Тест 1: Создаём первую задачу
    print("\n🔸 Запрос 1: 'напомни заказать продукты через 5 минут'")
    response1 = await process_message(
        "напомни заказать продукты через 5 минут",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"Ответ: {response1[:200]}")
    
    # Проверяем задачи
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\n📋 Задач после 1-го запроса: {len(tasks)}")
    for task in tasks:
        print(f"  - {task.title}")
    
    # Тест 2: Пытаемся создать дубликат с опечаткой
    print("\n🔸 Запрос 2: 'напомни закзать продукты через 5 минут' (с опечаткой)")
    response2 = await process_message(
        "напомни закзать продукты через 5 минут",
        user_id=user.telegram_id,
        db_session=session
    )
    print(f"Ответ: {response2[:200]}")
    
    # Проверяем задачи
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\n📋 Задач после 2-го запроса: {len(tasks)}")
    for task in tasks:
        print(f"  - {task.title}")
    
    # Итог
    print("\n" + "=" * 80)
    if len(tasks) == 1:
        print("✅ ТЕСТ ПРОЙДЕН: Дубликат обнаружен и не создан")
    else:
        print(f"❌ ТЕСТ НЕ ПРОЙДЕН: Создано {len(tasks)} задач вместо 1")
    print("=" * 80)
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_duplicate_detection())
