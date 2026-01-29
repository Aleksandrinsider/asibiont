"""
Полная диагностика делегированных задач
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Task, User
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    maria = session.query(User).filter_by(username="fitness_maria").first()
    alex = session.query(User).filter_by(username="sport_alex").first()
    
    print("=" * 80)
    print("👥 ПОЛЬЗОВАТЕЛИ:")
    print(f"   @aleksandrinsider - DB ID: {aleksandr.id}, TG ID: {aleksandr.telegram_id}")
    print(f"   @fitness_maria - DB ID: {maria.id}, TG ID: {maria.telegram_id}")
    print(f"   @sport_alex - DB ID: {alex.id}, TG ID: {alex.telegram_id}")
    
    print("\n" + "=" * 80)
    print("📋 ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ALEKSANDR (delegated_by=1):")
    print("    Должны показываться в разделе 'Поручил я' у aleksandrinsider")
    print("-" * 80)
    
    tasks_by_aleksandr = session.query(Task).filter_by(delegated_by=aleksandr.id).all()
    if not tasks_by_aleksandr:
        print("   ❌ НЕТ ЗАДАЧ!")
    else:
        for task in tasks_by_aleksandr:
            recipient = session.query(User).filter_by(id=task.user_id).first()
            print(f"\n   📝 {task.title}")
            print(f"      user_id (получатель): {task.user_id} (@{recipient.username if recipient else 'N/A'})")
            print(f"      delegated_by: {task.delegated_by} (кто делегировал)")
            print(f"      delegated_to_username: '{task.delegated_to_username}'")
            print(f"      delegation_status: {task.delegation_status}")
            print(f"      status: {task.status}")
    
    print("\n" + "=" * 80)
    print("📋 ЗАДАЧИ ДЛЯ ALEKSANDR (user_id=1):")
    print("    Это задачи, которые aleksandr должен выполнить")
    print("-" * 80)
    
    tasks_for_aleksandr = session.query(Task).filter_by(user_id=aleksandr.id).all()
    if not tasks_for_aleksandr:
        print("   ❌ НЕТ ЗАДАЧ!")
    else:
        for task in tasks_for_aleksandr:
            delegator = session.query(User).filter_by(id=task.delegated_by).first() if task.delegated_by else None
            print(f"\n   📝 {task.title}")
            print(f"      user_id: {task.user_id} (получатель)")
            print(f"      delegated_by: {task.delegated_by} (@{delegator.username if delegator else 'НЕТ - своя задача'})")
            print(f"      delegated_to_username: '{task.delegated_to_username}'")
            print(f"      delegation_status: {task.delegation_status}")
            print(f"      status: {task.status}")
    
    print("\n" + "=" * 80)
    print("🔍 ЛОГИКА РАЗДЕЛА 'ПОРУЧИЛИ МНЕ' для @aleksandrinsider:")
    print("    Должны показаться задачи где:")
    print("    - delegated_to_username = 'aleksandrinsider'")
    print("    - delegation_status IN ('pending', 'accepted')")
    print("    - status != 'deleted' AND status != 'rejected'")
    print("-" * 80)
    
    delegated_to_me = session.query(Task).filter(
        Task.delegated_to_username.ilike('aleksandrinsider'),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted',
        Task.status != 'rejected'
    ).all()
    
    if not delegated_to_me:
        print("   ✅ Нет задач (правильно!)")
    else:
        print(f"   ⚠️ НАЙДЕНО {len(delegated_to_me)} задач:")
        for task in delegated_to_me:
            delegator = session.query(User).filter_by(id=task.delegated_by).first() if task.delegated_by else None
            print(f"      • {task.title}")
            print(f"        delegated_by: {task.delegated_by} (@{delegator.username if delegator else 'N/A'})")
            print(f"        delegation_status: {task.delegation_status}, status: {task.status}")
    
    print("\n" + "=" * 80)
    print("🔍 ЛОГИКА РАЗДЕЛА 'ПОРУЧИЛ Я' для @aleksandrinsider:")
    print("    Должны показаться задачи где:")
    print("    - delegated_by = 1 (aleksandr.id)")
    print("    - delegated_to_username IS NOT NULL")
    print("    - delegation_status IN ('pending', 'accepted')")
    print("-" * 80)
    
    delegated_by_me = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    
    if not delegated_by_me:
        print("   ❌ Нет задач!")
    else:
        print(f"   ✅ НАЙДЕНО {len(delegated_by_me)} задач:")
        for task in delegated_by_me:
            recipient = session.query(User).filter_by(id=task.user_id).first()
            print(f"      • {task.title}")
            print(f"        user_id: {task.user_id} (@{recipient.username if recipient else 'N/A'})")
            print(f"        delegated_to_username: '{task.delegated_to_username}'")
            print(f"        delegation_status: {task.delegation_status}, status: {task.status}")
    
    print("\n" + "=" * 80)

finally:
    session.close()
