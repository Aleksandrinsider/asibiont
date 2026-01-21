"""
Скрипт для добавления делегированных задач для aleksandrinsider
"""
import os
import sys
from datetime import datetime, timedelta
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, Task
from ai_integration.memory import encrypt_data

def add_delegation_tasks():
    """Добавить делегированные задачи от тестовых пользователей"""
    session = Session()
    try:
        print("Начинаю создание задач...")
        
        # Найти главного пользователя aleksandrinsider
        main_user = session.query(User).filter_by(telegram_id=146333757).first()
        if not main_user:
            print("❌ Пользователь @aleksandrinsider не найден")
            print("Проверяю всех пользователей:")
            all_users = session.query(User).limit(5).all()
            for u in all_users:
                print(f"  - ID: {u.id}, username: {u.username}, telegram_id: {u.telegram_id}")
            return
        
        print(f"✅ Найден пользователь: @{main_user.username} (ID: {main_user.id})")
        
        # Найти тестовых пользователей (БЕЗ @ в username!)
        test_users = session.query(User).filter(
            User.username.in_(['basketball_star', 'marathon_runner', 'badminton_ace', 
                              'fitness_guru', 'yoga_master', 'tennis_player'])
        ).all()
        
        if not test_users:
            print("❌ Тестовые пользователи не найдены")
            return
        
        print(f"✅ Найдено {len(test_users)} тестовых пользователей")
        
        # Делегированные задачи для aleksandrinsider
        delegation_tasks = [
            {
                'delegator_username': 'basketball_star',
                'title': 'Организовать спортивное мероприятие',
                'description': 'Нужно собрать команду для баскетбольного турнира и найти площадку',
                'deadline_hours': 48
            },
            {
                'delegator_username': 'marathon_runner',
                'title': 'Найти зал для совместных тренировок',
                'description': 'Подобрать спортзал с удобным расположением и хорошим оборудованием',
                'deadline_hours': 24
            },
            {
                'delegator_username': 'badminton_ace',
                'title': 'Создать чат для спортсменов района',
                'description': 'Создать Telegram-группу и пригласить активных спортсменов',
                'deadline_hours': 12
            },
            {
                'delegator_username': 'fitness_guru',
                'title': 'Подготовить план питания на неделю',
                'description': 'Составить сбалансированное меню с учетом тренировок',
                'deadline_hours': 36
            },
            {
                'delegator_username': 'yoga_master',
                'title': 'Найти место для йога-сессии на природе',
                'description': 'Подобрать парк или площадку для групповой практики',
                'deadline_hours': 72
            },
            {
                'delegator_username': 'tennis_player',
                'title': 'Организовать турнир по теннису',
                'description': 'Забронировать корты и собрать участников',
                'deadline_hours': 96
            }
        ]
        
        added = 0
        now = datetime.now(pytz.UTC)
        
        # Создать словарь пользователей по username
        users_by_name = {u.username: u for u in test_users}
        
        for task_data in delegation_tasks:
            delegator = users_by_name.get(task_data['delegator_username'])
            if not delegator:
                print(f"⚠️  Пользователь {task_data['delegator_username']} не найден")
                continue
            
            # Проверить существование задачи
            existing = session.query(Task).filter(
                Task.title == task_data['title'],
                Task.delegated_by == delegator.id,
                Task.delegated_to_username == main_user.username
            ).first()
            
            if existing:
                print(f"⚠️  Задача '{task_data['title']}' уже существует")
                continue
            
            # Создать задачу
            deadline = now + timedelta(hours=task_data['deadline_hours'])
            
            # Задача создается ОТ ИМЕНИ ДЕЛЕГАТОРА, но делегирована главному пользователю
            task = Task(
                user_id=delegator.id,  # Владелец задачи - делегатор
                title=task_data['title'],
                description=encrypt_data(task_data.get('description', '')),
                delegated_by=delegator.id,
                delegated_to_username=main_user.username,
                delegation_status='pending',
                delegation_details=f"Задача делегирована пользователю {main_user.username}",
                reminder_time=deadline,
                due_date=deadline,
                status='pending',
                created_at=now
            )
            
            session.add(task)
            added += 1
            print(f"✅ Добавлена задача: '{task_data['title']}' от {delegator.username} для {main_user.username}")
        
        session.commit()
        print(f"\n✅ Всего добавлено {added} делегированных задач")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == '__main__':
    add_delegation_tasks()
