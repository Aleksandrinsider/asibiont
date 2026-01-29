"""
Проверка /api/partners с авторизацией
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

BASE_URL = "https://asibiont.ru"

print("=" * 80)
print("🔍 ПРОВЕРКА /api/partners С АВТОРИЗАЦИЕЙ")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username}")
    print(f"   Telegram ID: {aleksandr.telegram_id}")
    
    # Шаг 1: Авторизация через /direct_login
    print(f"\n" + "=" * 80)
    print("🔐 АВТОРИЗАЦИЯ")
    print("=" * 80)
    
    # Создаём сессию для сохранения cookies
    s = requests.Session()
    
    # Авторизуемся
    login_url = f"{BASE_URL}/direct_login?telegram_id={aleksandr.telegram_id}"
    print(f"\n   GET {login_url}")
    
    login_response = s.get(login_url, timeout=15, allow_redirects=False)
    print(f"   Статус: {login_response.status_code}")
    
    if login_response.status_code == 302:
        print(f"   ✅ Редирект на: {login_response.headers.get('Location', 'N/A')}")
        print(f"   Cookies: {dict(s.cookies)}")
    else:
        print(f"   ⚠️ Неожиданный статус: {login_response.status_code}")
    
    # Шаг 2: Запрос /api/partners с сессией
    print(f"\n" + "=" * 80)
    print("🌐 ЗАПРОС /api/partners")
    print("=" * 80)
    
    partners_url = f"{BASE_URL}/api/partners"
    print(f"\n   GET {partners_url}")
    
    response = s.get(
        partners_url,
        timeout=15,
        headers={
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    )
    
    print(f"   Статус: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        
        print(f"\n   ✅ ДАННЫЕ ПОЛУЧЕНЫ!")
        print(f"\n📊 СТРУКТУРА ОТВЕТА:")
        print(f"   Ключи: {list(data.keys())}")
        
        if 'partners' in data:
            partners = data['partners']
            print(f"\n👥 PARTNERS:")
            print(f"   Всего контактов: {len(partners)}")
            
            # Группируем по типам
            by_type = {}
            for p in partners:
                ptype = p.get('type', 'unknown')
                if ptype not in by_type:
                    by_type[ptype] = []
                by_type[ptype].append(p)
            
            print(f"\n   📊 Разбивка по типам:")
            for ptype, items in sorted(by_type.items()):
                print(f"      {ptype}: {len(items)} контактов")
            
            # Ищем delegating_by_me
            delegating_by_me = by_type.get('delegating_by_me', [])
            print(f"\n   📤 DELEGATING_BY_ME (Поручил я):")
            print(f"      Контактов: {len(delegating_by_me)}")
            
            if delegating_by_me:
                print(f"\n      ✅ НАЙДЕНЫ:")
                for p in delegating_by_me:
                    print(f"         • @{p.get('contact_info', 'N/A')}")
                    print(f"           first_name: {p.get('first_name', 'N/A')}")
                    print(f"           task_count: {p.get('task_count', 0)}")
                    print(f"           photo_url: {'Да' if p.get('photo_url') else 'Нет'}")
                    if p.get('tasks'):
                        print(f"           tasks: {p['tasks'][:3]}")
            else:
                print(f"      ❌ НЕТ КОНТАКТОВ 'delegating_by_me'!")
                print(f"\n      🔍 Первые 5 контактов из всех:")
                for i, p in enumerate(partners[:5], 1):
                    print(f"         {i}. @{p.get('contact_info', 'N/A')}")
                    print(f"            type: {p.get('type', 'N/A')}")
                    print(f"            task_count: {p.get('task_count', 0)}")
            
            # Ищем delegating_to_me
            delegating_to_me = by_type.get('delegating_to_me', [])
            print(f"\n   📥 DELEGATING_TO_ME (Поручили мне):")
            print(f"      Контактов: {len(delegating_to_me)}")
            if delegating_to_me:
                for p in delegating_to_me:
                    print(f"         • @{p.get('contact_info', 'N/A')} ({p.get('task_count', 0)} задач)")
            
            # Полный JSON первого delegating_by_me (если есть)
            if delegating_by_me:
                print(f"\n📄 ПОЛНЫЙ JSON первого контакта 'delegating_by_me':")
                print(json.dumps(delegating_by_me[0], ensure_ascii=False, indent=2))
        
        else:
            print(f"   ⚠️ Нет ключа 'partners' в ответе")
            print(f"   Ответ: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")
    
    elif response.status_code == 401:
        print(f"\n   ❌ 401 Unauthorized")
        print(f"   Авторизация не сработала")
        print(f"   Response: {response.text[:200]}")
    else:
        print(f"\n   ⚠️ Неожиданный статус")
        print(f"   Response: {response.text[:500]}")
    
    # ИТОГ
    print(f"\n" + "=" * 80)
    print("🎯 ИТОГОВАЯ ДИАГНОСТИКА:")
    print("=" * 80)
    
    if response.status_code == 200 and 'partners' in data:
        if delegating_by_me:
            print(f"""
✅ ВСЁ РАБОТАЕТ ПРАВИЛЬНО!
   • API возвращает delegating_by_me
   • Контактов: {len(delegating_by_me)}
   • Данные корректны

🔍 ЕСЛИ НЕ ВИДНО В БРАУЗЕРЕ:
   1. Очистите кэш браузера (Ctrl+Shift+Delete)
   2. Обновите страницу (Ctrl+F5)
   3. Откройте в режиме инкогнито
   4. Проверьте Console (F12) на ошибки JS
   5. Проверьте что кнопка "Поручаю я" активна (не серая)
""")
        else:
            print(f"""
⚠️ API РАБОТАЕТ, НО delegating_by_me ПУСТОЙ
   • Всего контактов: {len(partners)}
   • Но нет типа 'delegating_by_me'

🔍 ПРОБЛЕМА:
   Backend не формирует delegating_by_me контакты
   Проверьте логику в main.py api_partners_handler
   Строки примерно ~3100-3150
""")
    else:
        print(f"""
❌ ПРОБЛЕМА С API
   Статус: {response.status_code}
   
   Проверьте логи Railway
""")

finally:
    session.close()

print("=" * 80)
