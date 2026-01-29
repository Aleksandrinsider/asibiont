"""
Проверка что видит пользователь в разделе "Поручил я"
"""
import os
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Task, User
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 ДИАГНОСТИКА 'ПОРУЧИЛ Я'")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username} (ID: {aleksandr.id}, TG: {aleksandr.telegram_id})")
    
    # 1. ЧТО В БД
    print("\n" + "=" * 80)
    print("1️⃣ ЧТО В БАЗЕ ДАННЫХ:")
    print("=" * 80)
    
    my_delegated = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    
    print(f"\n📊 SQL запрос нашёл {len(my_delegated)} задач:")
    for task in my_delegated:
        recipient = session.query(User).filter_by(id=task.user_id).first()
        print(f"   • {task.title}")
        print(f"     delegated_to: @{task.delegated_to_username}")
        print(f"     user_id: {task.user_id} (@{recipient.username if recipient else 'N/A'})")
        print(f"     delegated_by: {task.delegated_by}")
        print(f"     delegation_status: {task.delegation_status}")
    
    # 2. ЧТО ВОЗВРАЩАЕТ API
    print("\n" + "=" * 80)
    print("2️⃣ ЧТО ВОЗВРАЩАЕТ API:")
    print("=" * 80)
    
    # Попробуем получить данные через API
    api_url = os.getenv("RAILWAY_PUBLIC_DOMAIN") or "http://localhost:8080"
    if not api_url.startswith("http"):
        api_url = f"https://{api_url}"
    
    try:
        # Dashboard endpoint
        response = requests.get(
            f"{api_url}/dashboard",
            params={"user_id": aleksandr.telegram_id},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.text
            
            # Ищем секцию delegating_by_me в HTML
            if "delegating_by_me" in data or "delegatingByMe" in data:
                print(f"   ✅ API ответил (200 OK)")
                print(f"   📝 Размер ответа: {len(data)} байт")
                
                # Пробуем найти данные о делегировании
                if "Поручил я" in data:
                    print(f"   ✅ Секция 'Поручил я' найдена в HTML")
                else:
                    print(f"   ⚠️ Секция 'Поручил я' НЕ найдена в HTML")
                
                # Ищем контакты в секции
                import re
                contacts_match = re.search(r"delegating_by_me.*?\[(.*?)\]", data, re.DOTALL)
                if contacts_match:
                    print(f"   ✅ Найдены контакты в delegating_by_me")
                else:
                    print(f"   ⚠️ Контакты в delegating_by_me пустые или не найдены")
            else:
                print(f"   ⚠️ delegating_by_me не найден в ответе")
        else:
            print(f"   ❌ API вернул статус {response.status_code}")
    except Exception as e:
        print(f"   ⚠️ Не удалось проверить API: {e}")
        print(f"   💡 Это нормально для локального запуска")
    
    # 3. ПРОВЕРКА ЛОГИКИ В MAIN.PY
    print("\n" + "=" * 80)
    print("3️⃣ ПРОВЕРКА ЛОГИКИ В КОДЕ:")
    print("=" * 80)
    
    # Симулируем логику из main.py
    delegating_by_me = []
    
    my_delegated_tasks = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    
    print(f"\n📝 Запрос нашёл {len(my_delegated_tasks)} задач")
    
    delegatee_usernames = set()
    for task in my_delegated_tasks:
        if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
            delegatee_usernames.add(task.delegated_to_username)
            
            from sqlalchemy import or_
            delegatee = session.query(User).filter(
                or_(
                    User.username.ilike(task.delegated_to_username.replace('@', '')),
                    User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                )
            ).first()
            
            if delegatee and delegatee.id != aleksandr.id:
                delegatee_tasks = [
                    t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                task_count = len(delegatee_tasks)
                task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegatee_tasks[:3]]
                
                delegating_by_me.append({
                    'id': delegatee.id,
                    'username': delegatee.username,
                    'first_name': delegatee.first_name,
                    'reason': f'я делегировал {task_count} задач',
                    'tasks': task_titles,
                    'task_count': task_count
                })
                
                print(f"\n   ✅ Добавлен контакт: @{delegatee.username}")
                print(f"      Задач: {task_count}")
                print(f"      Задачи: {', '.join(task_titles)}")
    
    print(f"\n📊 ИТОГО в delegating_by_me: {len(delegating_by_me)} контактов")
    
    if not delegating_by_me:
        print("\n❌ ПРОБЛЕМА: Массив delegating_by_me пустой!")
        print("\n🔍 Возможные причины:")
        print("   1. Задачи есть, но delegatee не найден (проблема с username)")
        print("   2. Задачи есть, но delegatee.id == aleksandr.id (делегация себе)")
        print("   3. Логика фильтрации неправильная")
        
        # Детальная проверка
        for task in my_delegated_tasks:
            print(f"\n   🔍 Задача: {task.title}")
            print(f"      delegated_to_username: '{task.delegated_to_username}'")
            
            from sqlalchemy import or_
            delegatee = session.query(User).filter(
                or_(
                    User.username.ilike(task.delegated_to_username.replace('@', '')),
                    User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                )
            ).first()
            
            if not delegatee:
                print(f"      ❌ Пользователь @{task.delegated_to_username} НЕ НАЙДЕН в БД!")
            elif delegatee.id == aleksandr.id:
                print(f"      ❌ delegatee.id ({delegatee.id}) == aleksandr.id ({aleksandr.id}) - делегация себе!")
            else:
                print(f"      ✅ Пользователь найден: @{delegatee.username} (ID: {delegatee.id})")

finally:
    session.close()

print("\n" + "=" * 80)
