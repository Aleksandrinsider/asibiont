"""
Проверка что реально возвращает Railway API для dashboard
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

# Подключаемся к БД
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

RAILWAY_URL = "https://web-production-f6d2.up.railway.app"

print("=" * 80)
print("🔍 ПРОВЕРКА RAILWAY DASHBOARD API")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username}")
    print(f"   ID: {aleksandr.id}")
    print(f"   Telegram ID: {aleksandr.telegram_id}")
    
    # Проверяем /api/dashboard напрямую
    print(f"\n" + "=" * 80)
    print(f"🌐 GET {RAILWAY_URL}/api/dashboard/{aleksandr.telegram_id}")
    print("=" * 80)
    
    try:
        response = requests.get(
            f"{RAILWAY_URL}/api/dashboard/{aleksandr.telegram_id}",
            timeout=15
        )
        
        print(f"\n   Статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            print(f"\n   ✅ API вернул данные!")
            print(f"\n📊 СТРУКТУРА ОТВЕТА:")
            print(f"   Ключи верхнего уровня: {list(data.keys())}")
            
            # Проверяем contacts
            if 'contacts' in data:
                contacts = data['contacts']
                print(f"\n👥 CONTACTS:")
                print(f"   Всего: {len(contacts)} контактов")
                
                # Проверяем delegating_by_me
                delegating_by_me = [c for c in contacts if c.get('reason', '').startswith('я делегировал')]
                print(f"\n   📤 DELEGATING_BY_ME (Поручил я):")
                print(f"      Контактов: {len(delegating_by_me)}")
                
                if delegating_by_me:
                    print(f"\n      ✅ Найдены контакты:")
                    for contact in delegating_by_me:
                        print(f"         • @{contact.get('username', 'N/A')}")
                        print(f"           Имя: {contact.get('first_name', 'N/A')}")
                        print(f"           Причина: {contact.get('reason', 'N/A')}")
                        print(f"           Задач: {contact.get('task_count', 0)}")
                        if contact.get('tasks'):
                            print(f"           Задачи: {contact['tasks'][:3]}")
                else:
                    print(f"      ❌ НЕТ КОНТАКТОВ 'Поручил я'")
                    print(f"\n      🔍 Все contacts:")
                    for i, contact in enumerate(contacts[:5], 1):
                        print(f"         {i}. @{contact.get('username', 'N/A')}")
                        print(f"            reason: {contact.get('reason', 'N/A')}")
                
                # Проверяем delegating_to_me
                delegating_to_me = [c for c in contacts if 'делегировал мне' in c.get('reason', '')]
                print(f"\n   📥 DELEGATING_TO_ME (Поручили мне):")
                print(f"      Контактов: {len(delegating_to_me)}")
                if delegating_to_me:
                    for contact in delegating_to_me:
                        print(f"         • @{contact.get('username', 'N/A')} ({contact.get('task_count', 0)} задач)")
            
            # Проверяем subscription
            if 'subscription_tier' in data:
                print(f"\n💳 SUBSCRIPTION:")
                print(f"   Тариф: {data['subscription_tier']}")
                print(f"   FREE_ACCESS_MODE: {data.get('FREE_ACCESS_MODE', 'N/A')}")
            
            # Полный JSON (первые 500 символов)
            print(f"\n📄 ПОЛНЫЙ JSON (первые 500 символов):")
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            print(f"   {json_str[:500]}...")
            
        elif response.status_code == 404:
            print(f"\n   ❌ 404 - Endpoint не найден")
            print(f"   💡 Проверьте что main.py правильно роутит /api/dashboard/<telegram_id>")
        elif response.status_code == 502:
            print(f"\n   ❌ 502 - Service перезапускается")
            print(f"   💡 Подождите 1-2 минуты")
        else:
            print(f"\n   ⚠️ Неожиданный статус")
            print(f"   Response text: {response.text[:200]}")
    
    except requests.exceptions.Timeout:
        print(f"\n   ❌ Timeout - API не отвечает")
    except requests.exceptions.ConnectionError as e:
        print(f"\n   ❌ Connection Error: {e}")
    except Exception as e:
        print(f"\n   ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    # ИТОГОВАЯ ДИАГНОСТИКА
    print(f"\n" + "=" * 80)
    print("🎯 ЧТО ДЕЛАТЬ ДАЛЬШЕ:")
    print("=" * 80)
    print("""
Если API вернул 200 и delegating_by_me пустой:
  → Backend не формирует contacts правильно
  → Проверьте логику в main.py строка ~1900

Если API вернул 200 и delegating_by_me НЕ пустой:
  → Проблема в frontend JavaScript
  → Откройте F12 → Console в браузере
  → Проверьте есть ли ошибки при рендере
  → Очистите кэш (Ctrl+Shift+Delete)

Если API вернул 404/502:
  → Проблема в Railway deployment
  → Проверьте Railway логи
""")

finally:
    session.close()

print("=" * 80)
