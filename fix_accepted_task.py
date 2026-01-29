"""
Исправление задачи с status='accepted' обратно на 'pending'
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
    # Находим задачу с status='accepted'
    task = session.query(Task).filter_by(
        title="Подготовить план питания перед марафоном",
        status="accepted"
    ).first()
    
    if task:
        print(f"✅ Найдена задача: {task.title}")
        print(f"   Текущий status: {task.status}")
        print(f"   Текущий delegation_status: {task.delegation_status}")
        
        # Возвращаем status в pending
        task.status = "pending"
        session.commit()
        
        print(f"\n✅ Исправлено:")
        print(f"   Новый status: {task.status}")
        print(f"   delegation_status остался: {task.delegation_status}")
    else:
        print("❌ Задача не найдена")

finally:
    session.close()
