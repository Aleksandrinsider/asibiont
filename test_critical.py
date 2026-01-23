#!/usr/bin/env python3
"""Критический тест агента - проверка основных проблем"""
import os
import sys
import asyncio
import logging
from datetime import datetime

os.environ['LOCAL'] = '1'
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from models import Session, User, Task
from ai_integration.chat import chat_with_ai

async def test_critical():
    print("\n" + "="*80)
    print("КРИТИЧЕСКИЙ ТЕСТ АГЕНТА")
    print("="*80)
    
    test_user_id = 12345
    errors = []
    
    # Подготовка
    session = Session()
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(telegram_id=test_user_id, username='test_critical', first_name='Test')
        session.add(user)
        session.commit()
    
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()
    
    # ТЕСТ 1: Создание задачи БЕЗ времени
    print("\n" + "-"*80)
    print("ТЕСТ 1: 'напомни купить продукты'")
    print("-"*80)
    
    response1 = await chat_with_ai("напомни купить продукты", user_id=test_user_id)
    print(f"Ответ AI: {response1}")
    
    session = Session()
    tasks_count = session.query(Task).filter_by(user_id=user.id).count()
    
    if tasks_count > 0:
        errors.append("❌ ОШИБКА: Создал задачу БЕЗ уточнения времени!")
        task = session.query(Task).filter_by(user_id=user.id).first()
        print(f"❌ Создана задача: '{task.title}' на {task.reminder_time}")
    else:
        print("✅ Задача не создана")
    
    if "когда" in response1.lower() or "во сколько" in response1.lower():
        print("✅ Агент спросил время")
    else:
        errors.append("❌ ОШИБКА: Агент НЕ спросил время!")
    
    session.close()
    
    # ТЕСТ 2: Указание времени
    print("\n" + "-"*80)
    print("ТЕСТ 2: 'в 18:00'")
    print("-"*80)
    
    response2 = await chat_with_ai("в 18:00", user_id=test_user_id, context=[
        {"role": "user", "content": "напомни купить продукты"},
        {"role": "assistant", "content": response1}
    ])
    print(f"Ответ AI: {response2}")
    
    session = Session()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    
    if len(tasks) == 0:
        errors.append("❌ ОШИБКА: НЕ создал задачу после указания времени!")
    elif len(tasks) > 1:
        errors.append(f"❌ ОШИБКА: Создано {len(tasks)} дубликатов!")
        print(f"❌ Дубликаты:")
        for task in tasks:
            print(f"  - '{task.title}' на {task.reminder_time}")
    else:
        task = tasks[0]
        print(f"✅ Создана: '{task.title}'")
        if task.reminder_time:
            task_time = task.reminder_time.strftime("%H:%M")
            if task_time == "18:00":
                print(f"✅ Время правильное: {task_time}")
            else:
                errors.append(f"❌ ОШИБКА: Неправильное время {task_time}")
    
    session.close()
    
    # ТЕСТ 3: С временем сразу
    print("\n" + "-"*80)
    print("ТЕСТ 3: 'напомни позвонить маме завтра в 10:00'")
    print("-"*80)
    
    session = Session()
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()
    
    response3 = await chat_with_ai("напомни позвонить маме завтра в 10:00", user_id=test_user_id)
    print(f"Ответ AI: {response3}")
    
    session = Session()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    
    if len(tasks) == 0:
        errors.append("❌ ОШИБКА: НЕ создал задачу с явным временем!")
    elif len(tasks) > 1:
        errors.append(f"❌ ОШИБКА: Дубликаты ({len(tasks)})")
    else:
        task = tasks[0]
        print(f"✅ Создана: '{task.title}'")
        if task.reminder_time:
            print(f"✅ Время: {task.reminder_time.strftime('%H:%M')}")
    
    session.close()
    
    # Результаты
    print("\n" + "="*80)
    print("РЕЗУЛЬТАТЫ")
    print("="*80)
    
    if errors:
        print(f"\n❌ ОШИБОК: {len(errors)}\n")
        for i, error in enumerate(errors, 1):
            print(f"{i}. {error}")
    else:
        print("\n✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
    
    print("\n" + "="*80)
    return len(errors) == 0

if __name__ == "__main__":
    success = asyncio.run(test_critical())
    sys.exit(0 if success else 1)
