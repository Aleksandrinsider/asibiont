"""
Финальный тест всех статусов делегированных задач
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
    alex = session.query(User).filter_by(username="sport_alex").first()
    
    print("=" * 80)
    print("✅ ФИНАЛЬНАЯ ПРОВЕРКА СТАТУСОВ ДЕЛЕГИРОВАННЫХ ЗАДАЧ")
    print("=" * 80)
    
    # Получаем все задачи делегированные aleksandrinsider
    delegated_to_aleksandr = session.query(Task).filter(
        Task.delegated_to_username.ilike('aleksandrinsider')
    ).all()
    
    print(f"\n📋 ЗАДАЧИ ДЕЛЕГИРОВАННЫЕ @aleksandrinsider (от @sport_alex):")
    print("-" * 80)
    
    if not delegated_to_aleksandr:
        print("   ❌ Нет задач")
    else:
        pending_count = 0
        in_progress_count = 0
        completed_count = 0
        rejected_count = 0
        
        for task in delegated_to_aleksandr:
            delegator = session.query(User).filter_by(id=task.delegated_by).first()
            
            status_emoji = {
                'pending': '⏳',
                'in_progress': '🔄',
                'completed': '✅',
                'rejected': '❌'
            }.get(task.status, '❓')
            
            delegation_emoji = {
                'pending': '⏳ Ожидает принятия',
                'accepted': '✅ Принята',
                'rejected': '❌ Отклонена'
            }.get(task.delegation_status, '❓')
            
            print(f"\n   {status_emoji} {task.title}")
            print(f"      От: @{delegator.username if delegator else 'N/A'}")
            print(f"      status: {task.status}")
            print(f"      delegation_status: {task.delegation_status} ({delegation_emoji})")
            
            # Проверяем видимость
            visible_in_filters = []
            
            # Фильтр "Поручили мне" (delegated_to_me)
            if (task.delegation_status in ['pending', 'accepted'] and 
                task.status not in ['deleted', 'rejected']):
                visible_in_filters.append("'Поручили мне' (контакты)")
            
            # Фильтр "Назначенные мне" (assigned-to-me tasks)
            if (task.status not in ['completed', 'rejected'] and
                task.delegated_to_username):
                visible_in_filters.append("'Назначенные мне' (задачи)")
            
            # Фильтр "Все задачи"
            if task.status not in ['completed', 'rejected']:
                visible_in_filters.append("'Все задачи'")
            
            # Фильтр "Завершённые"
            if task.status == 'completed':
                visible_in_filters.append("'Завершённые'")
            
            if visible_in_filters:
                print(f"      ✅ Видна в: {', '.join(visible_in_filters)}")
            else:
                print(f"      ❌ НЕ ВИДНА (status={task.status}, delegation_status={task.delegation_status})")
            
            # Статистика
            if task.status == 'pending':
                pending_count += 1
            elif task.status == 'in_progress':
                in_progress_count += 1
            elif task.status == 'completed':
                completed_count += 1
            elif task.status == 'rejected':
                rejected_count += 1
        
        print("\n" + "-" * 80)
        print(f"📊 СТАТИСТИКА:")
        print(f"   ⏳ Ожидают: {pending_count}")
        print(f"   🔄 В работе: {in_progress_count}")
        print(f"   ✅ Завершены: {completed_count}")
        print(f"   ❌ Отклонены: {rejected_count}")
        print(f"   📝 ВСЕГО: {len(delegated_to_aleksandr)}")
    
    print("\n" + "=" * 80)
    print("✅ ПРАВИЛЬНОЕ ПОВЕДЕНИЕ:")
    print("=" * 80)
    print("\n1️⃣ Создание: status='pending', delegation_status='pending'")
    print("   → Видна в 'Поручили мне' и 'Назначенные мне'")
    
    print("\n2️⃣ Принятие: status='in_progress', delegation_status='accepted'")
    print("   → Видна в 'Поручили мне' и 'Назначенные мне'")
    print("   → Показывает что задача принята и в работе")
    
    print("\n3️⃣ Завершение: status='completed', delegation_status='accepted'")
    print("   → Видна только в 'Завершённые'")
    
    print("\n4️⃣ Отклонение: status='rejected', delegation_status='rejected'")
    print("   → НЕ видна в активных фильтрах")
    
    print("\n" + "=" * 80)

finally:
    session.close()
