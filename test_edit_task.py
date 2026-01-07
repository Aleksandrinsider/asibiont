import asyncio
import sys
import os

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set LOCAL environment variable
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'

from models import Session, User, Task, Base, engine
from ai_integration import chat_with_ai, set_redis_client
import json

# Mock Redis client for testing
class MockRedis:
    def __init__(self):
        self.storage = {}
    
    async def get(self, key):
        value = self.storage.get(key)
        if value:
            return value.encode('utf-8')
        return None
    
    async def setex(self, key, ttl, value):
        self.storage[key] = value.decode('utf-8') if isinstance(value, bytes) else value
        print(f"[MOCK REDIS] Saved {key}: {self.storage[key]}")

async def test_edit_task():
    # Initialize database
    Base.metadata.create_all(engine)
    print("Database tables created successfully")
    
    # Mock redis client
    mock_redis = MockRedis()
    set_redis_client(mock_redis)
    print("Mock Redis initialized")
    
    # Create test user
    session = Session()
    test_user_id = 888777666
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(telegram_id=test_user_id, username="testuser")
        session.add(user)
        session.commit()
    print(f"Test user created: {user.username}")
    
    # Test 1: Create task
    print("\n" + "="*50)
    print("ТЕСТ 1: Создание задачи")
    print("="*50)
    message1 = "Давай запланируй пробежку завтра утром в 10:00"
    print(f"Сообщение: '{message1}'")
    print("-"*50)
    
    try:
        response1 = await chat_with_ai(message1, [], test_user_id, None)
        print(f"\n✅ Ответ получен:\n{response1}")
        
        # Check if task was saved to redis
        last_task_data = await mock_redis.get(f"last_task_id:{test_user_id}")
        if last_task_data:
            task_info = json.loads(last_task_data.decode('utf-8') if isinstance(last_task_data, bytes) else last_task_data)
            print(f"\n📦 Задача сохранена в Redis: {task_info}")
        else:
            print("\n⚠️ Задача НЕ сохранена в Redis")
    except Exception as e:
        print(f"\n❌ ОШИБКА: {str(e)}")
        import traceback
        traceback.print_exc()
        session.close()
        return
    
    # Test 2: Edit task
    print("\n" + "="*50)
    print("ТЕСТ 2: Изменение задачи")
    print("="*50)
    message2 = "я ошибся не завтра я сегодня в 10:00"
    print(f"Сообщение: '{message2}'")
    print("-"*50)
    
    try:
        response2 = await chat_with_ai(message2, [], test_user_id, None)
        print(f"\n✅ Ответ получен:\n{response2}")
        
        # Check if task was edited
        if last_task_data:
            task_info = json.loads(last_task_data.decode('utf-8') if isinstance(last_task_data, bytes) else last_task_data)
            task_id = task_info['id']
            task = session.query(Task).filter_by(id=int(task_id)).first()
            if task:
                print(f"\n📋 Задача в базе:")
                print(f"  - ID: {task.id}")
                print(f"  - Название: {task.title}")
                print(f"  - Время: {task.reminder_time}")
                
                if "сегодня" in task.title.lower() or (task.reminder_time and "2026-01-07" in str(task.reminder_time)):
                    print("\n✅ УСПЕХ: Задача обновлена!")
                else:
                    print("\n⚠️ Задача НЕ обновлена")
            else:
                print(f"\n❌ Задача с ID {task_id} не найдена в базе")
    except Exception as e:
        print(f"\n❌ ОШИБКА: {str(e)}")
        import traceback
        traceback.print_exc()
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_edit_task())
