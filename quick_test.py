import asyncio
import sys
import os
import logging

# Настройка logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Task, UserProfile, Base, engine
from ai_integration.autonomous_agent import chat_with_ai

async def quick_test():
    """Быстрый тест агента с текущей задачей"""
    
    # Создаем таблицы если нужно
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Создаем тестового пользователя
        user_id = 999888777
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            print("Creating test user...")
            user = User(
                telegram_id=user_id,
                username='test_user',
                first_name='Test',
                timezone='Europe/Moscow'
            )
            session.add(user)
            session.commit()
            
            # Профиль
            profile = UserProfile(
                user_id=user.id,
                interests='тестирование',
                goals='закрывать задачи',
                city='Moscow'
            )
            session.add(profile)
            session.commit()
            print(f"Created user with ID: {user.id}")
        
        # Создаем тестовую задачу
        task = Task(
            user_id=user.id,
            title='Проверить почту',
            status='active'
        )
        session.add(task)
        session.commit()
        print(f"Created task: {task.title} (ID: {task.id})")
        
        # Устанавливаем как текующую задачу
        user.current_task_id = task.id
        session.commit()
        print(f"Set current_task_id = {task.id}")
        
        # Тест 1: Подтверждение выполнения
        print("\n" + "="*60)
        print("TEST 1: 'я уже проверил почту'")
        print("="*60)
        
        response = await chat_with_ai(
            message='я уже проверил почту',
            user_id=user_id,
            db_session=session
        )
        
        print(f"\nResponse: {response.get('response', 'No response')}")
        print(f"Tool calls: {response.get('tool_calls', [])}")
        
        # Проверяем статус задачи
        session.refresh(task)
        print(f"\nTask status after: {task.status}")
        
        if task.status == 'completed':
            print("✅ SUCCESS: Task was completed!")
        else:
            print(f"❌ FAIL: Task status is still '{task.status}'")
            print("\nДетали:")
            print(f"- user.current_task_id = {user.current_task_id}")
            print(f"- task.id = {task.id}")
            print(f"- task.title = {task.title}")
        
    finally:
        session.close()

if __name__ == '__main__':
    asyncio.run(quick_test())
