"""
Полное тестирование всей функциональности
"""
import asyncio
import os
import sys

# Отключить emoji в выводе
def print_safe(text):
    """Печать без emoji для Windows console"""
    try:
        print(text)
    except UnicodeEncodeError:
        # Удалить все не-ASCII символы
        ascii_text = ''.join(c if ord(c) < 128 else '?' for c in text)
        print(ascii_text)

from models import Session, User, Task
from ai_integration import chat_with_ai

USER1_ID = 146333757  # Основной пользователь
USER2_USERNAME = "testuser"

def create_test_users():
    """Создать тестовых пользователей"""
    session = Session()
    try:
        user1 = session.query(User).filter_by(telegram_id=USER1_ID).first()
        if not user1:
            user1 = User(
                telegram_id=USER1_ID,
                username="aleksandrinsider",
                first_name="Aleksandr",
                timezone="Europe/Moscow"
            )
            session.add(user1)
        
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
        return user1, user2
    finally:
        session.close()

def check_tasks():
    """Вывести все задачи"""
    session = Session()
    try:
        tasks = session.query(Task).all()
        print(f"\nZadach v BD: {len(tasks)}")
        for task in tasks:
            creator = session.query(User).filter_by(id=task.user_id).first()
            print_safe(f"  * {task.title}")
            print_safe(f"    Sozdatel: {creator.username if creator else 'Unknown'}")
            if task.delegated_to_username:
                print_safe(f"    -> Delegirovana: @{task.delegated_to_username}")
            print_safe(f"    Status: {task.status}")
    finally:
        session.close()

async def test_all():
    print("="*70)
    print("POLNOE TESTIROVANIE")
    print("="*70)
    
    create_test_users()
    
    # Тест 1: Добавить обычную задачу
    print("\n" + "="*70)
    print("ТЕСТ 1: Добавление обычной задачи")
    print("="*70)
    msg = "Добавь задачу: Позвонить клиенту Иванову завтра в 14:00"
    print_safe(f"USER: {msg}")
    resp = await chat_with_ai(msg, user_id=USER1_ID)
    print_safe(f"BOT: {resp}")
    check_tasks()
    
    # Тест 2: Делегировать задачу
    print("\n" + "="*70)
    print("ТЕСТ 2: Делегирование задачи")
    print("="*70)
    msg = f"Делегируй @{USER2_USERNAME} задачу: Проверить документы до послезавтра 16:00"
    print_safe(f"USER: {msg}")
    resp = await chat_with_ai(msg, user_id=USER1_ID)
    print_safe(f"BOT: {resp}")
    check_tasks()
    
    # Тест 3: Просмотр всех задач
    print("\n" + "="*70)
    print("ТЕСТ 3: Просмотр всех задач")
    print("="*70)
    msg = "Покажи все мои задачи"
    print_safe(f"USER: {msg}")
    resp = await chat_with_ai(msg, user_id=USER1_ID)
    print_safe(f"BOT: {resp}")
    
    # Тест 4: Завершить первую задачу
    print("\n" + "="*70)
    print("ТЕСТ 4: Завершение первой задачи")
    print("="*70)
    msg = "Отметь первую задачу как выполненную"
    print_safe(f"USER: {msg}")
    resp = await chat_with_ai(msg, user_id=USER1_ID)
    print_safe(f"BOT: {resp}")
    check_tasks()
    
    # Тест 5: Проверка фильтра "Назначенные мной"
    print("\n" + "="*70)
    print("ТЕСТ 5: Проверка делегированных задач")
    print("="*70)
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER1_ID).first()
        delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None)
        ).all()
        print(f"Делегированных задач: {len(delegated)}")
        for task in delegated:
            print(f"  • {task.title} → @{task.delegated_to_username}")
    finally:
        session.close()
    
    print("\n" + "="*70)
    print("OK TESTIROVANIE ZAVERSHENO")
    print("="*70)

if __name__ == "__main__":
    asyncio.run(test_all())
