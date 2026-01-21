"""
Исправление статуса делегированных задач
"""
import os
os.environ['LOCAL'] = '1'  # Устанавливаем локальный режим
from config import DATABASE_URL
from models import Base, User, Task, Session

def fix_delegation_statuses():
    session = Session()
    try:
        # Найти все задачи, которые делегированы и имеют status='pending' и delegation_status='pending'
        pending_delegated = session.query(Task).filter(
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'pending',
            Task.status == 'pending'
        ).all()
        
        print(f"\nНайдено {len(pending_delegated)} задач с делегированием в ожидании подтверждения\n")
        
        for task in pending_delegated:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            delegator_name = delegator.username if delegator and delegator.username else 'Unknown'
            
            print(f"Задача #{task.id}: '{task.title}'")
            print(f"  От: {delegator_name}")
            print(f"  Кому: @{task.delegated_to_username}")
            print(f"  Текущий status: {task.status}")
            print(f"  Текущий delegation_status: {task.delegation_status}")
            print()
        
        # Задачи, которые были делегированы но не приняты, должны иметь status='waiting_acceptance'
        # а не 'pending', чтобы отличать их от обычных задач
        # Но согласно бизнес-логике - они должны быть "в ожидании"
        
        # Проверяем текущую логику: задачи с delegation_status='pending' 
        # уже находятся в ожидании подтверждения
        
        print("\n=== АНАЛИЗ ===")
        print("Задачи с delegation_status='pending' уже находятся в ожидании подтверждения.")
        print("Однако их status='pending', что означает 'в работе'.")
        print("\nВарианты решения:")
        print("1. Изменить status на 'waiting' для задач, ожидающих подтверждения")
        print("2. Фильтровать задачи по delegation_status при отображении")
        print("\nРекомендация: оставить status='pending', но в UI показывать как 'Ожидает подтверждения'")
        print("на основе delegation_status='pending'")
        
    finally:
        session.close()

if __name__ == '__main__':
    print("Проверка статусов делегированных задач...")
    fix_delegation_statuses()
