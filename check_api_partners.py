"""
Проверка реального ответа /api/partners
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User
from dotenv import load_dotenv
import requests
import json

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

RAILWAY_URL = "https://asibiont.ru"

print("=" * 80)
print("🔍 ПРОВЕРКА /api/partners")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username}")
    print(f"   Telegram ID: {aleksandr.telegram_id}")
    
    # Создаём cookie для авторизации
    # Railway использует session_id в cookies
    # Попробуем через telegram_id напрямую
    
    print(f"\n" + "=" * 80)
    print(f"🌐 GET {RAILWAY_URL}/api/partners")
    print("=" * 80)
    
    # Авторизация через telegram_id как параметр (если поддерживается)
    # Или создадим тестовую сессию
    
    # Попробуем без авторизации - должен вернуть 401
    try:
        response = requests.get(
            f"{RAILWAY_URL}/api/partners",
            timeout=15
        )
        
        print(f"\n   Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n   ✅ Получен ответ!")
            print(f"\n📊 СТРУКТУРА:")
            print(f"   Ключи: {list(data.keys())}")
            
            if 'partners' in data:
                partners = data['partners']
                print(f"\n👥 PARTNERS: {len(partners)} контактов")
                
                # Ищем delegating_by_me
                delegating_by_me = [p for p in partners if p.get('type') == 'delegating_by_me']
                print(f"\n   📤 DELEGATING_BY_ME:")
                print(f"      Контактов: {len(delegating_by_me)}")
                
                if delegating_by_me:
                    print(f"\n      ✅ Найдены:")
                    for p in delegating_by_me:
                        print(f"         • @{p.get('contact_info', 'N/A')}")
                        print(f"           first_name: {p.get('first_name', 'N/A')}")
                        print(f"           task_count: {p.get('task_count', 0)}")
                else:
                    print(f"      ❌ НЕТ!")
                    print(f"\n      🔍 Все partners:")
                    for i, p in enumerate(partners[:10], 1):
                        print(f"         {i}. @{p.get('contact_info', 'N/A')}")
                        print(f"            type: {p.get('type', 'N/A')}")
                        print(f"            task_count: {p.get('task_count', 0)}")
                
                # Ищем delegating_to_me
                delegating_to_me = [p for p in partners if p.get('type') == 'delegating_to_me']
                print(f"\n   📥 DELEGATING_TO_ME:")
                print(f"      Контактов: {len(delegating_to_me)}")
                if delegating_to_me:
                    for p in delegating_to_me:
                        print(f"         • @{p.get('contact_info', 'N/A')} ({p.get('task_count', 0)} задач)")
            
            # Полный JSON (урезанный)
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            print(f"\n📄 JSON (первые 1000 символов):")
            print(json_str[:1000])
            
        elif response.status_code == 401:
            print(f"\n   ⚠️ 401 Unauthorized")
            print(f"\n   💡 Нужна авторизация через сессию")
            print(f"\n   📝 Response: {response.text[:200]}")
        elif response.status_code == 404:
            print(f"\n   ❌ 404 - Endpoint не найден")
        elif response.status_code == 302:
            print(f"\n   🔀 302 Redirect")
            print(f"   Location: {response.headers.get('Location', 'N/A')}")
        else:
            print(f"\n   ⚠️ Статус: {response.status_code}")
            print(f"   Response: {response.text[:500]}")
    
    except requests.exceptions.Timeout:
        print(f"\n   ❌ Timeout")
    except Exception as e:
        print(f"\n   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n" + "=" * 80)
    print("💡 ВЫВОДЫ:")
    print("=" * 80)
    print("""
Если вернул 401:
  → Endpoint требует авторизацию
  → Нужно проверить через браузер с F12
  → Откройте dashboard → F12 → Network → найдите /api/partners
  → Проверьте Response

Если вернул 200 но нет delegating_by_me:
  → Backend не формирует эти данные
  → Проверьте логику в main.py api_partners_handler

Если всё на месте в ответе:
  → Проблема в frontend JavaScript
  → renderContacts() не находит элементы
  → updatePeople() не создаёт HTML
""")

finally:
    session.close()

print("=" * 80)
