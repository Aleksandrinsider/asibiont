"""
Детальная проверка что попадает в delegating_by_me
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker
from models import User, Task
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 ДЕТАЛЬНАЯ ПРОВЕРКА delegating_by_me")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    print(f"\n👤 Пользователь: @{aleksandr.username} (ID: {aleksandr.id})")
    
    # Шаг 1: Запрос задач (как в api_partners_handler строка 3117)
    print(f"\n" + "=" * 80)
    print("1️⃣ ЗАПРОС ЗАДАЧ")
    print("=" * 80)
    
    my_delegated_tasks = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted'
    ).all()
    
    print(f"\n   Найдено {len(my_delegated_tasks)} задач")
    
    if not my_delegated_tasks:
        print(f"\n   ❌ НЕТ ЗАДАЧ!")
        print(f"\n   Проверим без фильтров:")
        all_tasks = session.query(Task).filter(
            Task.delegated_by == aleksandr.id
        ).all()
        print(f"   Всего задач с delegated_by={aleksandr.id}: {len(all_tasks)}")
        for task in all_tasks:
            print(f"      • {task.title}")
            print(f"        delegated_to_username: {task.delegated_to_username}")
            print(f"        delegation_status: {task.delegation_status}")
            print(f"        status: {task.status}")
    else:
        print(f"\n   ✅ Задачи найдены:")
        for task in my_delegated_tasks:
            print(f"      • {task.title}")
            print(f"        delegated_to_username: {task.delegated_to_username}")
            print(f"        delegation_status: {task.delegation_status}")
            print(f"        status: {task.status}")
    
    # Шаг 2: Формирование delegating_by_me (как в коде строка 3126)
    print(f"\n" + "=" * 80)
    print("2️⃣ ФОРМИРОВАНИЕ delegating_by_me")
    print("=" * 80)
    
    delegating_by_me = []
    delegatee_usernames = set()
    
    for task in my_delegated_tasks:
        if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
            delegatee_usernames.add(task.delegated_to_username)
            
            print(f"\n   🔍 Обработка: {task.delegated_to_username}")
            
            # Поиск пользователя (как в коде строка 3131-3135)
            delegatee = session.query(User).filter(
                or_(
                    User.username.ilike(task.delegated_to_username.replace('@', '')),
                    User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                )
            ).first()
            
            if not delegatee:
                print(f"      ❌ Пользователь НЕ НАЙДЕН в БД")
            elif delegatee.id == aleksandr.id:
                print(f"      ⚠️ Это сам пользователь (delegatee.id == user.id)")
            else:
                print(f"      ✅ Пользователь найден: @{delegatee.username} (ID: {delegatee.id})")
                
                task_titles = [
                    t.title for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username
                ]
                
                contact_dict = {
                    'id': delegatee.id,
                    'username': delegatee.username,
                    'first_name': delegatee.first_name,
                    'task_count': len(task_titles),
                    'reason': f'я делегировал {len(task_titles)} задач'
                }
                
                delegating_by_me.append(contact_dict)
                print(f"      ✅ Добавлен в delegating_by_me")
                print(f"         username: {contact_dict['username']}")
                print(f"         first_name: {contact_dict['first_name']}")
                print(f"         task_count: {contact_dict['task_count']}")
    
    print(f"\n" + "=" * 80)
    print("3️⃣ ИТОГО")
    print("=" * 80)
    print(f"\n   delegating_by_me: {len(delegating_by_me)} контактов")
    
    for i, contact in enumerate(delegating_by_me, 1):
        print(f"\n   {i}. @{contact.get('username', 'N/A')}")
        print(f"      ID: {contact.get('id')}")
        print(f"      first_name: {contact.get('first_name', 'N/A')}")
        print(f"      task_count: {contact.get('task_count', 0)}")
        print(f"      reason: {contact.get('reason', 'N/A')}")
    
    # Шаг 4: Проверка что контакт не пропустится на строке 3504-3507
    print(f"\n" + "=" * 80)
    print("4️⃣ ПРОВЕРКА ФИЛЬТРОВ")
    print("=" * 80)
    
    for contact in delegating_by_me:
        print(f"\n   Контакт: @{contact.get('username')}")
        
        # Проверка 1: есть ли username
        if not contact.get('username'):
            print(f"      ❌ НЕТ USERNAME - будет пропущен (строка 3505)")
        else:
            print(f"      ✅ username есть")
        
        # Проверка 2: tier access
        delegatee = session.query(User).filter_by(id=contact['id']).first()
        if delegatee:
            print(f"      Тариф делегата: {delegatee.subscription_tier.value if delegatee.subscription_tier else 'None'}")
            print(f"      Тариф пользователя: {aleksandr.subscription_tier.value if aleksandr.subscription_tier else 'None'}")
            
            # Логика can_access (строка 3614-3630)
            user_tier_str = aleksandr.subscription_tier.value.lower() if aleksandr.subscription_tier else 'light'
            delegatee_tier_str = delegatee.subscription_tier.value.lower() if delegatee.subscription_tier else 'light'
            
            can_access = False
            if user_tier_str == 'light':
                can_access = (delegatee_tier_str in ['light', 'standard'])
            elif user_tier_str == 'standard':
                can_access = (delegatee_tier_str in ['light', 'standard'])
            elif user_tier_str == 'premium':
                can_access = True
            
            if can_access:
                print(f"      ✅ can_access = True - контакт БУДЕТ добавлен")
            else:
                print(f"      ❌ can_access = False - контакт НЕ БУДЕТ добавлен")

finally:
    session.close()

print("\n" + "=" * 80)
