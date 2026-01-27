"""Тест простой команды list_tasks для отладки"""
import asyncio
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

async def test_list():
    """Тест команды покажи задачи"""
    print("🧪 ТЕСТ КОМАНДЫ 'покажи мои задачи'\n")
    
    # Создаём тестовую сессию
    session = Session()
    user_id = 123456789
    
    try:
        # Создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, timezone='Europe/London')
            session.add(user)
            session.commit()
        
        # Создаём тестовую задачу
        test_task = Task(
            user_id=user.id,
            title='тестовая задача',
            description='для проверки list_tasks',
            status='pending',
            reminder_time=datetime.now(pytz.UTC) + timedelta(hours=1)
        )
        session.add(test_task)
        session.commit()
        print(f"✅ Создана тестовая задача: {test_task.title}")
        
        # Тестируем команду
        print("\n📝 Отправляем: 'покажи мои задачи'")
        response = await chat_with_ai(
            user_id=user_id,
            message='покажи мои задачи',
            db_session=session
        )
        
        print(f"\n💬 Ответ: {response[:200]}...")
        
        # Проверяем, был ли вызван list_tasks (задача должна быть упомянута в ответе)
        if 'тестовая задача' in response.lower():
            print("\n✅ УСПЕХ: задача упомянута в ответе")
        else:
            print("\n❌ ПРОВАЛ: задача не упомянута в ответе")
            print(f"Полный ответ: {response}")
        
    finally:
        session.close()

if __name__ == '__main__':
    asyncio.run(test_list())
