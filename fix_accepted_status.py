"""
Исправление уже принятой задачи - меняем status на in_progress
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Task
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # Находим принятую задачу
    task = session.query(Task).filter(
        Task.delegation_status == 'accepted',
        Task.status == 'pending'
    ).first()
    
    if task:
        print(f"✅ Найдена принятая задача с неправильным статусом:")
        print(f"   {task.title}")
        print(f"   Текущий status: {task.status}")
        print(f"   delegation_status: {task.delegation_status}")
        
        # Исправляем status
        task.status = 'in_progress'
        session.commit()
        
        print(f"\n✅ ИСПРАВЛЕНО:")
        print(f"   status: pending → in_progress")
        print(f"   delegation_status: {task.delegation_status} (остался)")
        print(f"\n💡 Теперь задача:")
        print(f"   ✅ Видна как 'в работе'")
        print(f"   ✅ Показывается в фильтрах (status != 'completed' && status != 'rejected')")
        print(f"   ✅ Не пропадает после принятия")
    else:
        print("ℹ️ Задач с status='pending' и delegation_status='accepted' не найдено")
        
        # Проверяем все принятые задачи
        accepted_tasks = session.query(Task).filter(
            Task.delegation_status == 'accepted'
        ).all()
        
        if accepted_tasks:
            print(f"\n📋 Найдено {len(accepted_tasks)} принятых задач:")
            for t in accepted_tasks:
                print(f"   • {t.title}")
                print(f"     status: {t.status}, delegation_status: {t.delegation_status}")
        else:
            print("\nℹ️ Принятых задач вообще нет")

finally:
    session.close()
