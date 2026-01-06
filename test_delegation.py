"""
Тестирование функциональности делегирования задач
"""
import asyncio
import sys
from models import Session, User, Task
from ai_integration import chat_with_ai

# Тестовые пользователи
USER1_ID = 146333757  # Основной пользователь
USER2_USERNAME = "testuser"  # Пользователь для делегирования

def create_test_users():
    """Создать тестовых пользователей"""
    session = Session()
    try:
        # Проверить/создать основного пользователя
        user1 = session.query(User).filter_by(telegram_id=USER1_ID).first()
        if not user1:
            user1 = User(
                telegram_id=USER1_ID,
                username="mainuser",
                first_name="Main",
                timezone="Europe/Moscow"
            )
            session.add(user1)
        
        # Проверить/создать второго пользователя
        user2 = session.query(User).filter(User.username.ilike(USER2_USERNAME)).first()
        if not user2:
            user2 = User(
                telegram_id=999999999,
                username=USER2_USERNAME,
                first_name="Test User",
                timezone="Europe/Moscow"
            )
            session.add(user2)
        
        session.commit()
        print(f"✅ Пользователи созданы: {user1.username}, {user2.username}")
        return user1, user2
    finally:
        session.close()

def check_tasks():
    """Проверить задачи в БД"""
    session = Session()
    try:
        tasks = session.query(Task).all()
        print(f"\n📋 Всего задач в БД: {len(tasks)}")
        for task in tasks:
            creator = session.query(User).filter_by(id=task.user_id).first()
            print(f"  - ID: {task.id}")
            print(f"    Название: {task.title}")
            print(f"    Создатель (user_id): {creator.username if creator else 'Unknown'} (ID: {task.user_id})")
            print(f"    Делегирована кому: {task.delegated_to_username or 'Никому'}")
            print(f"    Статус делегирования: {task.delegation_status or 'N/A'}")
            print(f"    Статус задачи: {task.status}")
            print()
    finally:
        session.close()

async def test_delegation():
    """Тестировать делегирование"""
    print("="*70)
    print("🧪 ТЕСТИРОВАНИЕ ДЕЛЕГИРОВАНИЯ ЗАДАЧ")
    print("="*70)
    
    # Создать пользователей
    user1, user2 = create_test_users()
    
    # Тест 1: Делегировать задачу
    print("\n" + "="*70)
    print("ТЕСТ 1: Делегирование задачи @testuser")
    print("="*70)
    message = f"Делегируй @{USER2_USERNAME} задачу: Проверить отчёт до завтра 15:00"
    print(f"[USER] {message}")
    
    response = await chat_with_ai(message, user_id=USER1_ID)
    print(f"[AI] {response}")
    
    check_tasks()
    
    # Тест 2: Показать задачи
    print("\n" + "="*70)
    print("ТЕСТ 2: Просмотр всех задач")
    print("="*70)
    message = "Покажи все мои задачи"
    print(f"[USER] {message}")
    
    response = await chat_with_ai(message, user_id=USER1_ID)
    print(f"[AI] {response}")
    
    # Тест 3: Проверить фильтр "Назначенные мной"
    print("\n" + "="*70)
    print("ТЕСТ 3: Проверка данных для фильтра 'Назначенные мной'")
    print("="*70)
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER1_ID).first()
        
        # Задачи делегированные МНОЙ
        my_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None)
        ).all()
        
        print(f"Задачи делегированные мной (user_id={user.id}, delegated_to_username не NULL):")
        print(f"Найдено: {len(my_delegated)}")
        for task in my_delegated:
            print(f"  - {task.title} → @{task.delegated_to_username}")
        
        if len(my_delegated) == 0:
            print("❌ ОШИБКА: Делегированные задачи не найдены!")
        else:
            print("✅ Делегированные задачи найдены")
    finally:
        session.close()
    
    print("\n" + "="*70)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(test_delegation())
