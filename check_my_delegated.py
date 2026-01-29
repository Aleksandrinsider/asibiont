"""
Проверка задач, которые aleksandrinsider делегировал другим
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
    
    print(f"👤 Пользователь: @aleksandrinsider")
    print(f"   DB ID: {aleksandr.id}, TG ID: {aleksandr.telegram_id}")
    
    # Проверяем задачи, где aleksandr - это delegated_by
    print(f"\n📋 Задачи, которые @aleksandrinsider делегировал (delegated_by={aleksandr.id}):")
    delegated_by_me = session.query(Task).filter_by(delegated_by=aleksandr.id).all()
    
    if not delegated_by_me:
        print("   ❌ Нет задач!")
    else:
        for task in delegated_by_me:
            recipient = session.query(User).filter_by(id=task.user_id).first() if task.user_id else None
            print(f"\n   • {task.title}")
            print(f"     - user_id (получатель): {task.user_id} (@{recipient.username if recipient else 'не найден'})")
            print(f"     - delegated_by: {task.delegated_by}")
            print(f"     - delegated_to_username: {task.delegated_to_username}")
            print(f"     - delegation_status: {task.delegation_status}")
            print(f"     - status: {task.status}")
    
    # Также проверяем по user_id (старая логика - должны быть пустые)
    print(f"\n📋 Задачи с user_id={aleksandr.id} и delegated_to_username!=None:")
    my_tasks = session.query(Task).filter(
        Task.user_id == aleksandr.id,
        Task.delegated_to_username.isnot(None)
    ).all()
    
    if not my_tasks:
        print("   ❌ Нет задач!")
    else:
        for task in my_tasks:
            print(f"\n   • {task.title}")
            print(f"     - user_id: {task.user_id}")
            print(f"     - delegated_by: {task.delegated_by}")
            print(f"     - delegated_to_username: {task.delegated_to_username}")
            print(f"     - delegation_status: {task.delegation_status}")

finally:
    session.close()
