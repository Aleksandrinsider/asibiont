"""
ПОЛНАЯ ДИАГНОСТИКА СИСТЕМЫ ЗАДАЧ
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Task
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 АУДИТ СИСТЕМЫ ЗАДАЧ")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    fitness_maria = session.query(User).filter_by(username="fitness_maria").first()
    sport_alex = session.query(User).filter_by(username="sport_alex").first()
    
    print(f"\n👤 Пользователи:")
    print(f"   @aleksandrinsider: ID={aleksandr.id}, TG={aleksandr.telegram_id}")
    print(f"   @fitness_maria: ID={fitness_maria.id if fitness_maria else 'N/A'}")
    print(f"   @sport_alex: ID={sport_alex.id if sport_alex else 'N/A'}")
    
    # ========================================================================
    # ПРОБЛЕМА 1: Делегированные задачи не видны
    # ========================================================================
    print(f"\n" + "=" * 80)
    print("🔍 ПРОБЛЕМА 1: ЗАДАЧИ ДЕЛЕГИРОВАННЫЕ @fitness_maria")
    print("=" * 80)
    
    # Задачи где aleksandr делегатор
    delegated_by_aleksandr = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None)
    ).all()
    
    print(f"\n1️⃣ Задачи где delegated_by={aleksandr.id}:")
    print(f"   Всего: {len(delegated_by_aleksandr)}")
    
    for task in delegated_by_aleksandr:
        print(f"\n   • ID={task.id}: {task.title}")
        print(f"     user_id: {task.user_id} (получатель)")
        print(f"     delegated_by: {task.delegated_by} (делегатор)")
        print(f"     delegated_to_username: {task.delegated_to_username}")
        print(f"     status: {task.status}")
        print(f"     delegation_status: {task.delegation_status}")
    
    # Проверка что API должен вернуть (новая логика)
    print(f"\n2️⃣ Что должен вернуть api_tasks_handler:")
    
    # Task.user_id == aleksandr.id
    tasks_as_owner = session.query(Task).filter(Task.user_id == aleksandr.id).all()
    print(f"   Task.user_id == {aleksandr.id}: {len(tasks_as_owner)} задач")
    
    # Task.delegated_to_username == aleksandrinsider
    tasks_delegated_to = session.query(Task).filter(
        Task.delegated_to_username.in_(['aleksandrinsider', '@aleksandrinsider'])
    ).all()
    print(f"   Task.delegated_to_username = aleksandrinsider: {len(tasks_delegated_to)} задач")
    
    # Task.delegated_by == aleksandr.id (НОВОЕ)
    tasks_delegated_by = session.query(Task).filter(Task.delegated_by == aleksandr.id).all()
    print(f"   Task.delegated_by == {aleksandr.id}: {len(tasks_delegated_by)} задач")
    
    # Объединение (с дедупликацией)
    all_task_ids = set()
    for t in tasks_as_owner:
        all_task_ids.add(t.id)
    for t in tasks_delegated_to:
        all_task_ids.add(t.id)
    for t in tasks_delegated_by:
        all_task_ids.add(t.id)
    
    print(f"\n   ✅ ИТОГО уникальных задач: {len(all_task_ids)}")
    
    # Фильтрация rejected
    all_tasks = session.query(Task).filter(Task.id.in_(all_task_ids)).all()
    non_rejected = [t for t in all_tasks if t.status != 'rejected' and t.delegation_status != 'rejected']
    print(f"   После фильтрации rejected: {len(non_rejected)} задач")
    
    # Группировка по типу
    my_tasks = []
    delegated_to_me = []
    delegated_by_me = []
    
    for task in non_rejected:
        if task.delegated_to_username and aleksandr.username:
            username_clean = aleksandr.username.replace('@', '')
            delegated_username = task.delegated_to_username.replace('@', '').lower()
            
            if delegated_username == username_clean.lower():
                # Делегирована МНЕ
                delegated_to_me.append(task)
            elif task.delegated_by == aleksandr.id:
                # Делегирована МНОЙ
                delegated_by_me.append(task)
            else:
                my_tasks.append(task)
        elif task.delegated_by == aleksandr.id:
            # Делегирована МНОЙ
            delegated_by_me.append(task)
        else:
            # Моя задача
            my_tasks.append(task)
    
    print(f"\n3️⃣ Разбивка по категориям:")
    print(f"   Мои задачи: {len(my_tasks)}")
    print(f"   Делегированы мне: {len(delegated_to_me)}")
    print(f"   Делегированы мной: {len(delegated_by_me)}")
    
    if delegated_by_me:
        print(f"\n   📤 Задачи делегированные МНОЙ:")
        for task in delegated_by_me:
            print(f"      • {task.title}")
            print(f"        → @{task.delegated_to_username}")
            print(f"        status: {task.status}, delegation_status: {task.delegation_status}")
    
    # ========================================================================
    # ПРОБЛЕМА 2: Выполнение одной задачи закрывает другие
    # ========================================================================
    print(f"\n" + "=" * 80)
    print("🔍 ПРОБЛЕМА 2: ВЫПОЛНЕНИЕ ЗАДАЧИ ЗАКРЫВАЕТ ДРУГИЕ")
    print("=" * 80)
    
    # Ищем completed задачи
    completed_tasks = session.query(Task).filter(
        Task.user_id == aleksandr.id,
        Task.status == 'completed'
    ).all()
    
    print(f"\n1️⃣ Завершённые задачи пользователя:")
    print(f"   Всего: {len(completed_tasks)}")
    
    for task in completed_tasks[:10]:  # Первые 10
        print(f"\n   • ID={task.id}: {task.title}")
        print(f"     user_id: {task.user_id}")
        print(f"     delegated_by: {task.delegated_by}")
        print(f"     delegated_to_username: {task.delegated_to_username}")
        print(f"     status: {task.status}")
        print(f"     created_at: {task.created_at}")
    
    # Проверка на дубликаты ID
    all_tasks = session.query(Task).all()
    task_ids = [t.id for t in all_tasks]
    duplicates = [id for id in task_ids if task_ids.count(id) > 1]
    
    if duplicates:
        print(f"\n   ❌ НАЙДЕНЫ ДУБЛИКАТЫ ID: {set(duplicates)}")
    else:
        print(f"\n   ✅ Дубликатов ID нет")
    
    # Проверка логики complete_task
    print(f"\n2️⃣ Проверка логики обновления статуса:")
    print(f"\n   В main.py complete_task_handler должен:")
    print(f"   1. Получить task_id из запроса")
    print(f"   2. Найти Task.query.filter_by(id=task_id, user_id=user.id)")
    print(f"   3. Обновить ТОЛЬКО эту задачу")
    print(f"\n   ⚠️ ВАЖНО: WHERE условие должно включать:")
    print(f"      - Task.id == task_id")
    print(f"      - Task.user_id == user.id (или delegated_to)")
    
    # ========================================================================
    # РЕКОМЕНДАЦИИ
    # ========================================================================
    print(f"\n" + "=" * 80)
    print("💡 РЕКОМЕНДАЦИИ ПО ИСПРАВЛЕНИЮ")
    print("=" * 80)
    
    print(f"""
1️⃣ ПРОБЛЕМА С ОТОБРАЖЕНИЕМ ДЕЛЕГИРОВАННЫХ ЗАДАЧ:
   
   ✅ FIX УЖЕ ПРИМЕНЁН: Task.delegated_by == user.id добавлен в запрос
   
   🔍 Нужно проверить:
   - Frontend фильтрацию (показывает ли делегированные задачи)
   - Метку задачи ("Делегирована на @username")
   - Фильтр по статусу (pending задачи могут скрываться)

2️⃣ ПРОБЛЕМА С ЗАКРЫТИЕМ НЕСКОЛЬКИХ ЗАДАЧ:
   
   ❌ КРИТИЧЕСКИЙ БАГ - нужно проверить:
   
   a) main.py complete_task_handler:
      - WHERE условие должно быть строгим (id + user_id)
      - Не должно быть bulk update без фильтра
   
   b) ai_integration/handlers.py complete_task():
      - Проверить что передаётся правильный task_id
      - Не должно быть логики "завершить все задачи с таким названием"
   
   c) Frontend:
      - Кнопка "Завершить" должна отправлять уникальный task.id
      - Не должно быть group operations без подтверждения

3️⃣ СЛЕДУЮЩИЕ ШАГИ:
   
   1. Проверить complete_task_handler в main.py (строка ~4900)
   2. Проверить AI complete_task в ai_integration/handlers.py
   3. Проверить frontend кнопку завершения задачи
   4. Добавить логирование task_id при завершении
""")

finally:
    session.close()

print("=" * 80)
