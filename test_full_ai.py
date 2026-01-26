#!/usr/bin/env python3
"""
Тестовый скрипт для проверки всех функций бота
"""
import sys
import os
import asyncio
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, Task, User, SubscriptionTier
from datetime import datetime

async def test_ai_functions():
    """Тестируем все функции AI"""

    # Используем существующего пользователя и обновляем его тариф на PREMIUM для тестирования delegation
    user_id = 999999999
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            user.subscription_tier = SubscriptionTier.PREMIUM
            session.commit()
            print(f"Пользователь {user_id} обновлен до PREMIUM тарифа для тестирования delegation\n")
        else:
            print(f"Пользователь {user_id} не найден, создаем...")
            # Создать пользователя если не существует
            new_user = User(
                telegram_id=user_id,
                username="testuser",
                first_name="Test User",
                subscription_tier=SubscriptionTier.PREMIUM
            )
            session.add(new_user)
            session.commit()
            print(f"Создан новый пользователь {user_id} с PREMIUM тарифом\n")
    finally:
        session.close()

    print("=== ТЕСТИРОВАНИЕ ФУНКЦИЙ AI ===\n")

    # Тест 1: Создание задачи
    print("1. Тест создания задачи:")
    message1 = "Создай задачу: Позвонить другу завтра в 15:00"
    print(f"Сообщение: {message1}")
    response1 = await chat_with_ai(message1, user_id=user_id)
    print(f"Ответ AI: {response1}\n")

    # Тест 2: Просмотр списка задач
    print("2. Тест просмотра списка задач:")
    message2 = "Покажи мои задачи"
    print(f"Сообщение: {message2}")
    response2 = await chat_with_ai(message2, user_id=user_id)
    print(f"Ответ AI: {response2}\n")

    # Тест 3: Завершение задачи
    print("3. Тест завершения задачи:")
    message3 = "Я выполнил задачу 'Купить молоко'"
    print(f"Сообщение: {message3}")
    response3 = await chat_with_ai(message3, user_id=user_id)
    print(f"Ответ AI: {response3}\n")

    # Тест 3.1: Завершение задачи естественной фразой
    print("3.1. Тест завершения задачи естественной фразой:")
    # Сначала создадим задачу "Пробежка с подкастом"
    message3_1_create = "Создай задачу: Пробежка с подкастом через 30 минут"
    print(f"Создание задачи: {message3_1_create}")
    response3_1_create = await chat_with_ai(message3_1_create, user_id=user_id)
    print(f"Ответ AI: {response3_1_create}")
    
    message3_1_complete = "я только с пробежки"
    print(f"Завершение: {message3_1_complete}")
    response3_1_complete = await chat_with_ai(message3_1_complete, user_id=user_id)
    print(f"Ответ AI: {response3_1_complete}\n")

    # Тест 4: Редактирование задачи
    print("4. Тест редактирования задачи:")
    message4 = "Измени время задачи 'Позвонить другу' на завтра в 16:00"
    print(f"Сообщение: {message4}")
    response4 = await chat_with_ai(message4, user_id=user_id)
    print(f"Ответ AI: {response4}\n")

    # Тест 5: Делегирование задачи
    print("5. Тест делегирования задачи:")
    message5 = "Делегируй задачу 'Позвонить другу' пользователю @test1"
    print(f"Сообщение: {message5}")
    response5 = await chat_with_ai(message5, user_id=user_id)
    print(f"Ответ AI: {response5}\n")

    # Тест 5.1: Делегирование через keywords
    print("5.1. Тест делегирования через keywords:")
    message5_1 = "Поручи @test1 сделать отчет завтра в 10:00"
    print(f"Сообщение: {message5_1}")
    response5_1 = await chat_with_ai(message5_1, user_id=user_id)
    print(f"Ответ AI: {response5_1}\n")

    print("=== ПРОВЕРКА РЕЗУЛЬТАТОВ В БАЗЕ ДАННЫХ ===\n")

    # Проверяем изменения в базе данных
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            print(f"Задачи пользователя {user_id}:")
            for task in tasks:
                status_emoji = "✅" if task.status == "completed" else "⏳"
                print(f"  {status_emoji} ID {task.id}: '{task.title}' - {task.status} - {task.reminder_time}")
        else:
            print(f"Пользователь {user_id} не найден")
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_functions())