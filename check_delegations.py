#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка пользователей и задач в БД"""

import os
from sqlalchemy.orm import sessionmaker
from models import User, Task, engine

# Устанавливаем переменные окружения для Railway
os.environ['LOCAL'] = '0'  # Использовать Railway БД
os.environ['DATABASE_PUBLIC_URL'] = 'postgresql://postgres:sANXAzJHOtUZkUeeiUUvdNqgxBuAVtdd@shinkansen.proxy.rlwy.net:27224/railway'

def check_users_and_tasks():
    """Проверяет пользователей и задачи"""

    # Создаем сессию
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        print("Подключение к БД:", engine.url)

        # Находим пользователей
        test_user_4 = session.query(User).filter_by(username='test_user_4').first()
        aleksandrinsider = session.query(User).filter_by(username='aleksandrinsider').first()
        test_user_9 = session.query(User).filter_by(username='test_user_9').first()

        print("\nПользователи:")
        if test_user_4:
            print(f"  @test_user_4: ID {test_user_4.id}")
        else:
            print("  @test_user_4: НЕ НАЙДЕН")

        if aleksandrinsider:
            print(f"  @aleksandrinsider: ID {aleksandrinsider.id}")
        else:
            print("  @aleksandrinsider: НЕ НАЙДЕН")

        if test_user_9:
            print(f"  @test_user_9: ID {test_user_9.id}")
        else:
            print("  @test_user_9: НЕ НАЙДЕН")

        # Проверяем делегированные задачи
        if test_user_4:
            delegated_tasks = session.query(Task).filter_by(user_id=test_user_4.id).filter(Task.delegated_to_username.isnot(None)).all()
            print(f"\nЗадачи делегированные от @test_user_4 ({len(delegated_tasks)}):")
            for task in delegated_tasks:
                print(f"  - {task.title} -> {task.delegated_to_username} (статус: {task.delegation_status})")

        if aleksandrinsider:
            delegated_tasks = session.query(Task).filter_by(user_id=aleksandrinsider.id).filter(Task.delegated_to_username.isnot(None)).all()
            print(f"\nЗадачи делегированные от @aleksandrinsider ({len(delegated_tasks)}):")
            for task in delegated_tasks:
                print(f"  - {task.title} -> {task.delegated_to_username} (статус: {task.delegation_status})")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    check_users_and_tasks()