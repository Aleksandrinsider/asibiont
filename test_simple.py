#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Простой тест основных функций проекта"""

import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

from models import Session, User, Task, UserProfile, Interaction
from datetime import datetime, timezone
from subscription_service import check_subscription
from ai_integration import chat_with_ai

def test_database_operations():
    """Тестируем операции с базой данных"""
    print("Тестируем операции с базой данных...")

    session = Session()

    try:
        # Создаем тестового пользователя
        user = session.query(User).filter_by(telegram_id=123456789).first()
        if not user:
            user = User(
                telegram_id=123456789,
                username="test_user",
                first_name="Test",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
        print("✅ Пользователь создан")

        # Создаем задачу
        task = Task(
            user_id=user.id,
            title="Тестовая задача",
            description="Описание тестовой задачи",
            due_date=datetime.now(timezone.utc)
        )
        session.add(task)
        session.commit()
        print("✅ Задача создана")

        # Создаем профиль пользователя
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                skills="Python, SQL",
                interests="AI, Programming"
            )
            session.add(profile)
            session.commit()
        print("✅ Профиль пользователя создан")

        # Создаем взаимодействие
        interaction = Interaction(
            user_id=user.id,
            message_type="user",
            content="Тестовое сообщение"
        )
        session.add(interaction)
        session.commit()
        print("✅ Взаимодействие создано")

        # Проверяем запросы
        users_count = session.query(User).count()
        tasks_count = session.query(Task).count()
        print(f"✅ В базе {users_count} пользователей, {tasks_count} задач")

        # Очищаем тестовые данные
        session.query(Interaction).filter_by(user_id=user.id).delete()
        session.query(Task).filter_by(user_id=user.id).delete()
        session.query(UserProfile).filter_by(user_id=user.id).delete()
        session.query(User).filter_by(telegram_id=123456789).delete()
        session.commit()
        print("✅ Тестовые данные очищены")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

def test_config():
    """Тестируем конфигурацию"""
    print("Тестируем конфигурацию...")
    try:
        from config import LOCAL, DATABASE_URL
        print(f"✅ LOCAL: {LOCAL}")
        print(f"✅ DATABASE_URL: {DATABASE_URL}")
    except Exception as e:
        print(f"❌ Ошибка конфигурации: {e}")

def test_subscription():
    """Тестируем сервис подписки"""
    print("Тестируем сервис подписки...")
    try:
        # Проверяем подписку для несуществующего пользователя
        result = check_subscription(999999)
        print(f"✅ Подписка для несуществующего пользователя: {result}")

        # Проверяем подписку для тестового пользователя (без подписки)
        result = check_subscription(123456789)
        print(f"✅ Подписка для тестового пользователя: {result}")

    except Exception as e:
        print(f"❌ Ошибка в тесте подписки: {e}")

import asyncio

def test_ai_agent():
    """Тестируем AI агента на реальных запросах"""
    print("Тестируем AI агента...")
    async def run_test():
        try:
            # Создаем тестового пользователя в БД
            session = Session()
            user = session.query(User).filter_by(telegram_id=123456789).first()
            if not user:
                user = User(telegram_id=123456789, username="test_user", first_name="Test")
                session.add(user)
                session.commit()
            telegram_id = user.telegram_id
            session.close()

            # Тестовый запрос
            test_message = "Создай задачу: купить молоко"
            print(f"Отправляем запрос: {test_message}")

            # Вызываем AI
            response = await chat_with_ai(test_message, user_id=telegram_id)
            print(f"Ответ AI: {response[:200]}...")

            print("✅ AI агент работает")

        except Exception as e:
            print(f"❌ Ошибка в тесте AI: {e}")
            import traceback
            traceback.print_exc()
    
    asyncio.run(run_test())

async def test_ai_conversation():
    """Тестируем AI агента в многошаговом диалоге"""
    print("Тестируем AI агента в диалоге...")
    
    # Создаем тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=123456789).first()
    if not user:
        user = User(telegram_id=123456789, username="test_user", first_name="Test", timezone="Europe/Moscow")
        session.add(user)
        session.commit()
    else:
        # Обновляем timezone если не установлен
        if not user.timezone:
            user.timezone = "Europe/Moscow"
            session.commit()
    telegram_id = user.telegram_id
    session.close()
    
    # Сценарии диалогов
    scenarios = [
        {
            "name": "Создание задачи без времени",
            "messages": [
                "Создай задачу: купить молоко",
                "Завтра в 10 утра",  # Ответ на вопрос о времени
            ]
        },
        {
            "name": "Просмотр задач",
            "messages": [
                "Покажи мои задачи",
            ]
        },
        {
            "name": "Завершение задачи",
            "messages": [
                "Заверши задачу купить молоко",
            ]
        },
        {
            "name": "Обновление профиля",
            "messages": [
                "Мои навыки: Python, SQL",
                "Мои интересы: AI, программирование",
            ]
        },
        {
            "name": "Делегирование задачи",
            "messages": [
                "Создай задачу: подготовить отчет",
                "Завтра в 15:00",
                "Делегируй эту задачу пользователю @testuser",
            ]
        }
    ]
    
    for scenario in scenarios:
        print(f"\n--- Сценарий: {scenario['name']} ---")
        try:
            for i, message in enumerate(scenario['messages']):
                print(f"Пользователь: {message}")
                response = await chat_with_ai(message, user_id=telegram_id)
                print(f"AI: {response[:150]}...")
                if i < len(scenario['messages']) - 1:
                    await asyncio.sleep(0.5)  # Небольшая пауза между сообщениями
        except Exception as e:
            print(f"❌ Ошибка в сценарии {scenario['name']}: {e}")
            import traceback
            traceback.print_exc()
    
    print("✅ Тестирование диалогов завершено")

if __name__ == "__main__":
    print("Запуск простого тестирования...")
    try:
        test_config()
        test_database_operations()
        test_subscription()
        test_ai_agent()
        asyncio.run(test_ai_conversation())
        print("Тестирование завершено.")
    except Exception as e:
        print(f"Ошибка в тестировании: {e}")
        import traceback
        traceback.print_exc()