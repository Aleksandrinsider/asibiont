"""
Финальная проверка делегирования после исправлений
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
    print("✅ ФИНАЛЬНАЯ ПРОВЕРКА ДЕЛЕГИРОВАНИЯ")
    print("=" * 80)
    
    print("\n📋 РАЗДЕЛ 'ПОРУЧИЛИ МНЕ' (@aleksandrinsider):")
    print("    Показывает контакты, которые делегировали задачи МНЕ")
    print("-" * 80)
    
    delegated_to_me_tasks = session.query(Task).filter(
        Task.delegated_to_username.ilike('aleksandrinsider'),
        Task.delegation_status.in_(['pending', 'accepted']),
        Task.status != 'deleted',
        Task.status != 'rejected'
    ).all()
    
    if delegated_to_me_tasks:
        # Группируем по delegated_by
        delegators = {}
        for task in delegated_to_me_tasks:
            if task.delegated_by not in delegators:
                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                delegators[task.delegated_by] = {
                    'user': delegator,
                    'tasks': []
                }
            delegators[task.delegated_by]['tasks'].append(task)
        
        for delegator_id, data in delegators.items():
            delegator = data['user']
            tasks = data['tasks']
            print(f"\n   👤 @{delegator.username} (ID: {delegator.id})")
            print(f"      Делегировал {len(tasks)} задач:")
            for task in tasks:
                status_emoji = "✅" if task.delegation_status == "accepted" else "⏳"
                print(f"         {status_emoji} {task.title}")
                print(f"            delegation_status: {task.delegation_status}, status: {task.status}")
    else:
        print("   ❌ Нет задач")
    
    print("\n" + "=" * 80)
    print("📋 РАЗДЕЛ 'ПОРУЧИЛ Я' (@aleksandrinsider):")
    print("    Показывает контакты, КОТОРЫМ я делегировал задачи")
    print("-" * 80)
    
    delegated_by_me_tasks = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegation_status.in_(['pending', 'accepted'])
    ).all()
    
    if delegated_by_me_tasks:
        # Группируем по user_id (получатель)
        recipients = {}
        for task in delegated_by_me_tasks:
            if task.user_id not in recipients:
                recipient = session.query(User).filter_by(id=task.user_id).first()
                recipients[task.user_id] = {
                    'user': recipient,
                    'tasks': []
                }
            recipients[task.user_id]['tasks'].append(task)
        
        for recipient_id, data in recipients.items():
            recipient = data['user']
            tasks = data['tasks']
            print(f"\n   👤 @{recipient.username} (ID: {recipient.id})")
            print(f"      Я делегировал {len(tasks)} задач:")
            for task in tasks:
                status_emoji = "✅" if task.delegation_status == "accepted" else "⏳"
                print(f"         {status_emoji} {task.title}")
                print(f"            delegation_status: {task.delegation_status}, status: {task.status}")
    else:
        print("   ❌ Нет задач")
    
    print("\n" + "=" * 80)
    print("✅ ОЖИДАЕМЫЙ РЕЗУЛЬТАТ:")
    print("   • 'Поручили мне': @sport_alex с 5 задачами")
    print("   • 'Поручил я': @fitness_maria с 5 задачами")
    print("   • При принятии задачи: delegation_status='accepted', status='pending'")
    print("   • Задача остаётся видимой после принятия")
    print("=" * 80)

finally:
    session.close()
