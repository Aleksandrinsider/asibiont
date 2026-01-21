"""
Скрипт для добавления делегированных задач и очистки descriptions
"""

import os
import sys
from datetime import datetime, timedelta
import pytz

# Добавить корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Task, UserProfile
from ai_integration.memory import encrypt_data

def clean_task_descriptions():
    """Убрать лишний текст из descriptions делегированных задач"""
    session = Session()
    try:
        # Найти все делегированные задачи
        delegated_tasks = session.query(Task).filter(
            Task.delegated_to_username.isnot(None)
        ).all()
        
        cleaned = 0
        for task in delegated_tasks:
            if task.description:
                try:
                    from ai_integration.memory import decrypt_data
                    desc = decrypt_data(task.description)
                    if desc:
                        # Убрать лишний текст
                        if "Задача делегирована пользователю" in desc:
                            desc = ""
                            task.description = encrypt_data(desc)
                            cleaned += 1
                        elif "делегирована вам" in desc:
                            desc = ""
                            task.description = encrypt_data(desc)
                            cleaned += 1
                except Exception as e:
                    print(f"Ошибка при обработке задачи {task.id}: {e}")
        
        session.commit()
        print(f"✅ Очищено {cleaned} descriptions задач")
    finally:
        session.close()


def add_delegation_tasks():
    """Добавить делегированные задачи на главного пользователя"""
    session = Session()
    try:
        # Найти главного пользователя
        main_user = session.query(User).filter_by(telegram_id=1001).first()
        if not main_user:
            print("❌ Главный пользователь не найден")
            return
        
        print(f"✅ Найден пользователь: @{main_user.username}")
        
        # Использовать существующих тестовых пользователей
        delegator1 = session.query(User).filter_by(telegram_id=999001).first()
        delegator2 = session.query(User).filter_by(telegram_id=999002).first()
        
        if not delegator1 or not delegator2:
            print("❌ Тестовые пользователи не найдены")
            return
        
        print(f"✅ Найдены делегаторы: {delegator1.username}, {delegator2.username}")
        
        # Задачи для делегирования на главного пользователя
        tasks_to_delegate = [
            {
                'title': 'Составить план тренировок на неделю',
                'delegator': delegator1,
                'deadline_hours': 48
            },
            {
                'title': 'Подготовить презентацию по фитнесу',
                'delegator': delegator2,
                'deadline_hours': 72
            },
            {
                'title': 'Найти партнёра для совместных пробежек',
                'delegator': delegator1,
                'deadline_hours': 24
            },
            {
                'title': 'Организовать групповую тренировку',
                'delegator': delegator2,
                'deadline_hours': 96
            }
        ]
        
        added = 0
        now = datetime.now(pytz.UTC)
        
        for task_data in tasks_to_delegate:
            # Получить делегатора из объекта task_data
            delegator = task_data['delegator']
            
            # Проверить, существует ли уже такая задача
            existing = session.query(Task).filter(
                Task.title == task_data['title'],
                Task.user_id == delegator.id,
                Task.delegated_to_username == str(main_user.telegram_id)
            ).first()
            
            if existing:
                print(f"⚠️  Задача '{task_data['title']}' уже существует")
                continue
            
            # Создать задачу
            deadline = now + timedelta(hours=task_data['deadline_hours'])
            
            task = Task(
                user_id=delegator.id,
                title=task_data['title'],
                description=encrypt_data(""),  # Пустое описание
                delegated_to_username=str(main_user.telegram_id),
                delegation_status='pending',
                reminder_time=deadline,
                status='pending'
            )
            
            session.add(task)
            added += 1
            print(f"✅ Добавлена задача: '{task_data['title']}' от @{delegator.username}")
        
        session.commit()
        print(f"\n✅ Всего добавлено {added} задач")
        
    finally:
        session.close()


def add_similar_tasks_to_users():
    """Добавить похожие задачи другим пользователям для тестирования рекомендаций"""
    session = Session()
    try:
        # Получить главного пользователя и его задачи
        main_user = session.query(User).filter_by(telegram_id=1001).first()
        if not main_user:
            return
        
        main_tasks = session.query(Task).filter_by(user_id=main_user.id).all()
        main_task_titles = set(t.title.lower() for t in main_tasks if t.title)
        
        # Найти тестовых пользователей
        test_users = session.query(User).filter(
            User.username.in_([
                'tennis_player',
                'basketball_star',
                'football_hero',
                'volleyball_ace',
                'hockey_champion'
            ])
        ).all()
        
        # Похожие задачи для добавления
        similar_tasks = [
            'Составить план тренировок на неделю',
            'Организовать групповую тренировку',
            'Найти партнёра для совместных пробежек',
            'Подготовить презентацию по фитнесу'
        ]
        
        added = 0
        now = datetime.now(pytz.UTC)
        
        for user in test_users:
            # Добавить 2 случайные похожие задачи каждому пользователю
            import random
            tasks_for_user = random.sample(similar_tasks, min(2, len(similar_tasks)))
            
            for task_title in tasks_for_user:
                # Проверить, есть ли уже такая задача
                existing = session.query(Task).filter(
                    Task.title == task_title,
                    Task.user_id == user.id
                ).first()
                
                if existing:
                    continue
                
                # Создать задачу
                deadline = now + timedelta(hours=random.randint(24, 120))
                
                task = Task(
                    user_id=user.id,
                    title=task_title,
                    description=encrypt_data(""),
                    reminder_time=deadline,
                    status='pending'
                )
                
                session.add(task)
                added += 1
                print(f"✅ Добавлена задача '{task_title}' пользователю @{user.username}")
        
        session.commit()
        print(f"\n✅ Всего добавлено {added} похожих задач другим пользователям")
        
    finally:
        session.close()


if __name__ == '__main__':
    print("🔧 Очистка старых descriptions...")
    clean_task_descriptions()
    
    print("\n📋 Добавление делегированных задач...")
    add_delegation_tasks()
    
    print("\n📋 Добавление похожих задач другим пользователям...")
    add_similar_tasks_to_users()
    
    print("\n✅ Готово!")
