"""
Тест API /api/partners для проверки common_interests в delegation контактах
"""
import os
os.environ['LOCAL'] = '1'

import asyncio
from aiohttp import web
from main import api_partners_handler
from models import SessionLocal, User
import json

async def test_api():
    # Создаем фейковый request
    class FakeSession:
        def __init__(self, user_id):
            self._data = {'user_id': user_id}
        
        def get(self, key):
            return self._data.get(key)
    
    class FakeRequest:
        def __init__(self, user_id):
            self.session = FakeSession(user_id)
            self.app = {}
    
    # Тестируем для user 1
    session_db = SessionLocal()
    user = session_db.query(User).filter_by(id=1).first()
    print(f"Testing API for user: @{user.username if user else 'unknown'}")
    session_db.close()
    
    request = FakeRequest(user.telegram_id if user else None)
    
    try:
        response = await api_partners_handler(request)
        if response.status == 200:
            data = json.loads(response.body.decode())
            partners = data.get('partners', [])
            print(f"\nAPI вернул {len(partners)} партнеров\n")
            
            # Проверяем delegation контакты
            delegation_contacts = [p for p in partners if p.get('type') in ['delegating_to_me', 'delegating_by_me']]
            print(f"Delegation контактов: {len(delegation_contacts)}")
            
            for p in delegation_contacts:
                print(f"\n--- {p.get('type')} ---")
                print(f"  Username: @{p.get('contact_info')}")
                print(f"  common_interests: {p.get('common_interests')}")
                print(f"  common_skills: {p.get('common_skills')}")
                print(f"  common_goals: {p.get('common_goals')}")
                print(f"  common_tasks: {p.get('common_tasks')}")
            
            # Проверяем recommended контакты
            recommended = [p for p in partners if p.get('type') == 'recommended']
            print(f"\nRecommended контактов: {len(recommended)}")
            
            for p in recommended[:3]:  # Показываем первых 3
                print(f"\n--- recommended ---")
                print(f"  Username: @{p.get('contact_info')}")
                print(f"  common_interests: {p.get('common_interests')}")
                print(f"  common_skills: {p.get('common_skills')}")
                print(f"  common_goals: {p.get('common_goals')}")
        else:
            print(f"API вернул ошибку: {response.status}")
            print(response.body.decode())
    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_api())
