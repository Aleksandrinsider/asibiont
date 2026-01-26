#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Создание тестовых делегированных задач"""

import os
import sys
from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from models import Task, User, engine
import pytz

# Устанавливаем переменные окружения для Railway
os.environ['LOCAL'] = '0'  # Использовать Railway БД
os.environ['DATABASE_PUBLIC_URL'] = 'postgresql://postgres:sANXAzJHOtUZkUeeiUUvdNqgxBuAVtdd@shinkansen.proxy.rlwy.net:27224/railway'

def create_test_delegations():
    """Создает тестовые делегированные задачи"""

    # Создаем сессию
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Находим пользователей
        test_user_4 = session.query(User).filter_by(username='test_user_4').first()
        aleksandrinsider = session.query(User).filter_by(username='aleksandrinsider').first()
        test_user_9 = session.query(User).filter_by(username='test_user_9').first()

        if not test_user_4:
            print("Пользователь @test_user_4 не найден")
            return
        if not aleksandrinsider:
            print("Пользователь @aleksandrinsider не найден")
            return
        if not test_user_9:
            print("Пользователь @test_user_9 не найден")
            return

        print(f"Найдены пользователи:")
        print(f"  @test_user_4: ID {test_user_4.id}")
        print(f"  @aleksandrinsider: ID {aleksandrinsider.id}")
        print(f"  @test_user_9: ID {test_user_9.id}")

        # Создаем задачи от @test_user_4 на @aleksandrinsider с разными статусами
        tasks_from_4_to_aleks = [
            {
                'title': 'Подготовить отчет по продажам',
                'description': 'Собрать данные за последний квартал и подготовить презентацию',
                'status': 'pending',
                'delegation_status': 'pending',
                'reminder_time': datetime.now(pytz.UTC) + timedelta(days=2)
            },
            {
                'title': 'Организовать встречу с клиентом',
                'description': 'Связаться с клиентом и согласовать время встречи',
                'status': 'pending',
                'delegation_status': 'accepted',
                'reminder_time': datetime.now(pytz.UTC) + timedelta(days=1)
            },
            {
                'title': 'Проверить код проекта',
                'description': 'Ревью кода и исправление багов',
                'status': 'completed',
                'delegation_status': 'accepted',
                'reminder_time': datetime.now(pytz.UTC) - timedelta(days=1),
                'actual_completion_time': datetime.now(pytz.UTC) - timedelta(hours=5)
            },
            {
                'title': 'Создать дизайн логотипа',
                'description': 'Разработать концепцию логотипа для нового проекта',
                'status': 'pending',
                'delegation_status': 'rejected',
                'reminder_time': datetime.now(pytz.UTC) + timedelta(days=3)
            }
        ]

        # Создаем задачи от @aleksandrinsider на @test_user_9 с разными статусами
        tasks_from_aleks_to_9 = [
            {
                'title': 'Настроить CI/CD pipeline',
                'description': 'Автоматизировать процесс развертывания',
                'status': 'in_progress',
                'delegation_status': 'pending',
                'reminder_time': datetime.now(pytz.UTC) + timedelta(days=5)
            },
            {
                'title': 'Написать документацию API',
                'description': 'Создать подробную документацию для REST API',
                'status': 'pending',
                'delegation_status': 'accepted',
                'reminder_time': datetime.now(pytz.UTC) + timedelta(days=7)
            },
            {
                'title': 'Провести тестирование системы',
                'description': 'Выполнить полный цикл тестирования',
                'status': 'completed',
                'delegation_status': 'accepted',
                'reminder_time': datetime.now(pytz.UTC) - timedelta(days=2),
                'actual_completion_time': datetime.now(pytz.UTC) - timedelta(hours=10)
            }
        ]

        # Создаем задачи от test_user_4 к aleksandrinsider
        print("\nСоздание задач от @test_user_4 к @aleksandrinsider:")
        for i, task_data in enumerate(tasks_from_4_to_aleks, 1):
            task = Task(
                user_id=test_user_4.id,
                title=task_data['title'],
                description=task_data['description'],
                status=task_data['status'],
                delegation_status=task_data['delegation_status'],
                delegated_to_username='@aleksandrinsider',
                reminder_time=task_data['reminder_time'],
                actual_completion_time=task_data.get('actual_completion_time')
            )
            session.add(task)
            print(f"  {i}. {task_data['title']} - статус: {task_data['delegation_status']}")

        # Создаем задачи от aleksandrinsider к test_user_9
        print("\nСоздание задач от @aleksandrinsider к @test_user_9:")
        for i, task_data in enumerate(tasks_from_aleks_to_9, 1):
            task = Task(
                user_id=aleksandrinsider.id,
                title=task_data['title'],
                description=task_data['description'],
                status=task_data['status'],
                delegation_status=task_data['delegation_status'],
                delegated_to_username='@test_user_9',
                reminder_time=task_data['reminder_time'],
                actual_completion_time=task_data.get('actual_completion_time')
            )
            session.add(task)
            print(f"  {i}. {task_data['title']} - статус: {task_data['delegation_status']}")

        # Коммитим изменения
        session.commit()
        print("\n✅ Все тестовые делегированные задачи созданы успешно!")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    create_test_delegations()