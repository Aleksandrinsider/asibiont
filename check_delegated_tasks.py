"""
Проверка делегированных задач в БД
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
    # Получаем пользователей
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    maria = session.query(User).filter_by(username="fitness_maria").first()
    alex = session.query(User).filter_by(username="sport_alex").first()
    
    print("👥 Пользователи:")
    print(f"   @aleksandrinsider - DB ID: {aleksandr.id}, TG ID: {aleksandr.telegram_id}")
    print(f"   @fitness_maria - DB ID: {maria.id}, TG ID: {maria.telegram_id}")
    print(f"   @sport_alex - DB ID: {alex.id}, TG ID: {alex.telegram_id}")
    
    # Проверяем задачи для aleksandrinsider
    print(f"\n📋 Задачи пользователя @aleksandrinsider (user_id={aleksandr.id}):")
    tasks = session.query(Task).filter_by(user_id=aleksandr.id).all()
    for task in tasks:
        delegator = session.query(User).filter_by(id=task.delegated_by).first() if task.delegated_by else None
        delegator_name = f"@{delegator.username}" if delegator else "нет"
        print(f"   • {task.title}")
        print(f"     - user_id: {task.user_id}")
        print(f"     - delegated_by: {task.delegated_by} ({delegator_name})")
        print(f"     - delegated_to_username: {task.delegated_to_username}")
        print()

finally:
    session.close()
