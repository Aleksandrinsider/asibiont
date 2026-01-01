"""
Финальный комплексный тест с исправлениями
"""
import os
import sys
import time

if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, Task, User, UserProfile, Subscription, Interaction
from ai_integration import chat_with_ai
from datetime import datetime, timedelta
import pytz

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757

print("="*60)
print("ФИНАЛЬНЫЙ ТЕСТ PRODUCTION СИСТЕМЫ")
print("="*60)

# 1. Очистка
print("\n1️⃣ Очистка БД...")
session = Session()
try:
    session.query(Interaction).delete()
    session.query(Task).delete()
    session.query(UserProfile).delete()
    session.query(Subscription).delete()
    session.query(User).delete()
    session.commit()
    print("   ✅ БД очищена")
finally:
    session.close()

# 2. Создание пользователя
print("\n2️⃣ Создание пользователя...")
session = Session()
try:
    user = User(
        telegram_id=USER_ID,
        username='aleksandrinsider',
        first_name='Aleksandr',
        timezone='Europe/Moscow'
    )
    session.add(user)
    session.flush()
    
    profile = UserProfile(
        user_id=user.id,
        city='Москва',
        interests='AI, стартапы',
        skills='Python, менеджмент',
        goals='Запустить MVP',
        contact_info='aleksandrinsider'
    )
    session.add(profile)
    
    subscription = Subscription(
        user_id=user.id,
        status='active',
        start_date=datetime.now(pytz.UTC),
        end_date=datetime.now(pytz.UTC) + timedelta(days=30)
    )
    session.add(subscription)
    
    session.commit()
    print(f"   ✅ Пользователь создан (ID: {user.id})")
finally:
    session.close()

print("\n3️⃣ Тест AI функций...")
print("-"*60)

async def simple_test(prompt):
    print(f"\n▶️ {prompt}")
    try:
        response = await chat_with_ai(prompt, [], USER_ID)
        print(f"✅ {response[:100]}..." if len(response) > 100 else f"✅ {response}")
        time.sleep(2)
        return response
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

import asyncio

async def run_tests():
    await simple_test("Добавь задачу: Встреча с командой завтра в 10:00")
    await simple_test("Добавь еще: Позвонить клиенту через 2 часа")
    
    # Проверяем количество задач
    session = Session()
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    
    print(f"\n📊 В БД задач: {len(tasks)}")
    for t in tasks:
        print(f"   - {t.title} [{t.status}] {t.reminder_time.strftime('%d.%m %H:%M') if t.reminder_time else 'нет времени'}")
    
    if len(tasks) == 2:
        print("✅ Правильное количество задач")
    else:
        print(f"⚠️ Ожидалось 2 задачи, получено {len(tasks)}")
    
    await simple_test("Выполнил встречу с командой")
    
    # Проверяем статус
    session = Session()
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    completed_tasks = session.query(Task).filter_by(user_id=user.id, status='completed').all()
    session.close()
    
    print(f"\n📊 Выполнено задач: {len(completed_tasks)}")
    if len(completed_tasks) >= 1:
        print("✅ Задача отмечена как выполненная")
    else:
        print("❌ Задача НЕ отмечена как выполненная")
    
    await simple_test("Удали звонок клиенту")
    
    # Проверяем удаление
    session = Session()
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    session.close()
    
    print(f"\n📊 Осталось задач: {len(tasks)}")
    if len(tasks) == 1:
        print("✅ Задача успешно удалена")
    else:
        print(f"⚠️ Ожидалась 1 задача, получено {len(tasks)}")
    
    print("\n" + "="*60)
    print("ТЕСТ ЗАВЕРШЕН")
    print("="*60)
    print("\nОткройте dashboard:")
    print("https://task-production-31b6.up.railway.app/direct_login?user_id=146333757")

asyncio.run(run_tests())
