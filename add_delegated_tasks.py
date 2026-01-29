"""
Скрипт для добавления делегированных задач в Railway БД
"""
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Task, User
from dotenv import load_dotenv

load_dotenv()

# Подключение к Railway БД
DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_PUBLIC_URL или DATABASE_URL не установлен")

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    # Получаем пользователей
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    maria = session.query(User).filter_by(username="fitness_maria").first()
    alex = session.query(User).filter_by(username="sport_alex").first()
    
    if not aleksandr:
        print("❌ Пользователь @aleksandrinsider не найден")
        exit(1)
    if not maria:
        print("❌ Пользователь @fitness_maria не найден")
        exit(1)
    if not alex:
        print("❌ Пользователь @sport_alex не найден")
        exit(1)
    
    print(f"✅ Найден @aleksandrinsider (ID: {aleksandr.telegram_id})")
    print(f"✅ Найден @fitness_maria (ID: {maria.telegram_id})")
    print(f"✅ Найден @sport_alex (ID: {alex.telegram_id})")
    
    # Задачи от aleksandrinsider для fitness_maria
    tasks_for_maria = [
        {
            "title": "Разработать план тренировок на февраль",
            "description": "Составить индивидуальные программы для группы начинающих",
            "due_date": datetime.now() + timedelta(days=5),
        },
        {
            "title": "Провести консультацию по питанию",
            "description": "Онлайн встреча с клиентом для обсуждения рациона",
            "due_date": datetime.now() + timedelta(days=3),
        },
        {
            "title": "Подготовить отчет по занятиям",
            "description": "Статистика посещаемости и результаты за январь",
            "due_date": datetime.now() + timedelta(days=7),
        },
    ]
    
    # Задачи от sport_alex для aleksandrinsider
    tasks_for_aleksandr = [
        {
            "title": "Проверить регистрацию на марафон",
            "description": "Подтвердить участие в беговом марафоне 15 февраля",
            "due_date": datetime.now() + timedelta(days=4),
        },
        {
            "title": "Купить спортивную экипировку",
            "description": "Новые кроссовки и форма для забега",
            "due_date": datetime.now() + timedelta(days=6),
        },
        {
            "title": "Записаться на тренировку",
            "description": "Бронь на групповое занятие в субботу утром",
            "due_date": datetime.now() + timedelta(days=2),
        },
    ]
    
    # Создаем задачи для Марии (от Александра)
    print("\n📝 Создаю задачи от @aleksandrinsider для @fitness_maria:")
    for task_data in tasks_for_maria:
        task = Task(
            user_id=maria.id,
            title=task_data["title"],
            description=task_data["description"],
            due_date=task_data["due_date"],
            status="pending",
            delegated_by=aleksandr.id,
            delegated_to_username="fitness_maria"
        )
        session.add(task)
        print(f"   ✅ {task_data['title']}")
    
    # Создаем задачи для Александра (от Алекса)
    print("\n📝 Создаю задачи от @sport_alex для @aleksandrinsider:")
    for task_data in tasks_for_aleksandr:
        task = Task(
            user_id=aleksandr.id,
            title=task_data["title"],
            description=task_data["description"],
            due_date=task_data["due_date"],
            status="pending",
            delegated_by=alex.id,
            delegated_to_username="aleksandrinsider"
        )
        session.add(task)
        print(f"   ✅ {task_data['title']}")
    
    session.commit()
    print(f"\n✅ Успешно создано {len(tasks_for_maria) + len(tasks_for_aleksandr)} задач!")
    print(f"   • {len(tasks_for_maria)} задач от @aleksandrinsider для @fitness_maria")
    print(f"   • {len(tasks_for_aleksandr)} задач от @sport_alex для @aleksandrinsider")
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    session.rollback()
finally:
    session.close()
