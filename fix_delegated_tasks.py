"""
Исправление делегированных задач - удаление старых и создание новых
"""
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, or_, and_
from sqlalchemy.orm import sessionmaker
from models import Task, User
from dotenv import load_dotenv

load_dotenv()

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
    
    if not all([aleksandr, maria, alex]):
        print("❌ Не все пользователи найдены")
        exit(1)
    
    print(f"✅ Найден @aleksandrinsider (ID: {aleksandr.telegram_id})")
    print(f"✅ Найден @fitness_maria (ID: {maria.telegram_id})")
    print(f"✅ Найден @sport_alex (ID: {alex.telegram_id})")
    
    # Удаляем все старые делегированные задачи
    print(f"\n🗑️ Удаляю старые делегированные задачи...")
    old_tasks = session.query(Task).filter(
        or_(
            and_(Task.delegated_by == aleksandr.id, Task.delegated_to_username == 'fitness_maria'),
            and_(Task.delegated_by == alex.id, Task.delegated_to_username == 'aleksandrinsider')
        )
    ).all()
    
    for task in old_tasks:
        session.delete(task)
    session.commit()
    print(f"   ✅ Удалено {len(old_tasks)} старых задач")
    
    # Задачи от aleksandrinsider для fitness_maria (показываются в "Поручил я" у aleksandrinsider)
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
        {
            "title": "Организовать групповую тренировку",
            "description": "Забронировать зал и подготовить программу для 10 участников",
            "due_date": datetime.now() + timedelta(days=4),
        },
        {
            "title": "Обновить сертификаты тренера",
            "description": "Пройти онлайн курс по новым методикам тренировок",
            "due_date": datetime.now() + timedelta(days=10),
        },
    ]
    
    # Задачи от sport_alex для aleksandrinsider (показываются в "Поручили мне" у aleksandrinsider)
    tasks_for_aleksandr = [
        {
            "title": "Проверить регистрацию на марафон",
            "description": "Подтвердить участие в беговом марафоне 15 февраля",
            "due_date": datetime.now() + timedelta(days=4),
        },
        {
            "title": "Купить спортивную экипировку",
            "description": "Новые кроссовки Nike Pegasus и форма для забега",
            "due_date": datetime.now() + timedelta(days=6),
        },
        {
            "title": "Записаться на тренировку",
            "description": "Бронь на групповое занятие в субботу утром 10:00",
            "due_date": datetime.now() + timedelta(days=2),
        },
        {
            "title": "Подготовить план питания перед марафоном",
            "description": "Разработать меню на неделю перед соревнованием",
            "due_date": datetime.now() + timedelta(days=8),
        },
        {
            "title": "Заказать медицинскую справку",
            "description": "Пройти медосмотр для допуска к марафону",
            "due_date": datetime.now() + timedelta(days=5),
        },
    ]
    
    # Создаем задачи для Марии (от Александра)
    print("\n📝 Создаю задачи от @aleksandrinsider для @fitness_maria:")
    for task_data in tasks_for_maria:
        task = Task(
            user_id=maria.id,  # ✅ Получатель - fitness_maria
            title=task_data["title"],
            description=task_data["description"],
            due_date=task_data["due_date"],
            status="pending",
            delegated_by=aleksandr.id,  # Кто поручил
            delegated_to_username="fitness_maria",  # Кому поручил
            delegation_status="pending"
        )
        session.add(task)
        print(f"   ✅ {task_data['title']}")
    
    # Создаем задачи для Александра (от Алекса)
    print("\n📝 Создаю задачи от @sport_alex для @aleksandrinsider:")
    for task_data in tasks_for_aleksandr:
        task = Task(
            user_id=aleksandr.id,  # ✅ Получатель - aleksandrinsider
            title=task_data["title"],
            description=task_data["description"],
            due_date=task_data["due_date"],
            status="pending",
            delegated_by=alex.id,  # Кто поручил
            delegated_to_username="aleksandrinsider",  # Кому поручил
            delegation_status="pending"
        )
        session.add(task)
        print(f"   ✅ {task_data['title']}")
    
    session.commit()
    
    print(f"\n✅ Успешно создано {len(tasks_for_maria) + len(tasks_for_aleksandr)} задач!")
    print(f"   • {len(tasks_for_maria)} задач от @aleksandrinsider для @fitness_maria (в разделе 'Поручил я')")
    print(f"   • {len(tasks_for_aleksandr)} задач от @sport_alex для @aleksandrinsider (в разделе 'Поручили мне')")
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
    session.rollback()
finally:
    session.close()
