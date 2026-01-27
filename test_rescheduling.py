#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест переноса задач"""

import asyncio
from datetime import datetime, timedelta
import pytz
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

async def main():
    telegram_id = 888999
    session = Session()
    
    # Удалим старые задачи
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    # Создаём 2 задачи
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(moscow_tz)
    
    response1 = await chat_with_ai(
        "Напомни проверить почту через 30 минут",
        user_id=telegram_id
    )
    print(f"Создание 1: {response1[:100]}")
    
    response2 = await chat_with_ai(
        "Напомни встреча с командой через 1 час",
        user_id=telegram_id
    )
    print(f"Создание 2: {response2[:100]}")
    
    # Покажем задачи
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\nСоздано задач: {len(tasks)}")
    for task in tasks:
        print(f"  - {task.title} на {task.reminder_time}")
    
    # ТЕСТ 1: Перенос с "на завтра в 10:00"
    print("\n" + "="*80)
    print("ТЕСТ 1: Перенеси проверить почту на завтра в 10:00")
    print("="*80)
    
    task1_before = session.query(Task).filter_by(user_id=user.id, title="Проверить почту").first()
    time_before = task1_before.reminder_time if task1_before else None
    print(f"До: {time_before}")
    
    response = await chat_with_ai(
        "Перенеси проверить почту на завтра в 10:00",
        user_id=telegram_id
    )
    print(f"Ответ: {response[:200]}")
    
    session.expire_all()
    task1_after = session.query(Task).filter_by(user_id=user.id, title="Проверить почту").first()
    time_after = task1_after.reminder_time if task1_after else None
    print(f"После: {time_after}")
    
    if time_after and time_after != time_before:
        print("✅ УСПЕХ: Время изменилось")
    else:
        print("❌ ОШИБКА: Время не изменилось")
    
    # ТЕСТ 2: Перенос с "на 15:30"
    print("\n" + "="*80)
    print("ТЕСТ 2: Перенеси встречу на 15:30")
    print("="*80)
    
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    # SQLite ilike doesn't work well with Russian, use Python search
    all_tasks = session.query(Task).filter_by(user_id=user.id).all()
    task2_before = next((t for t in all_tasks if 'встреча' in t.title.lower()), None)
    time_before = task2_before.reminder_time if task2_before else None
    print(f"До: {time_before}")
    
    response = await chat_with_ai(
        "Перенеси встречу на 15:30",
        user_id=telegram_id
    )
    print(f"Ответ: {response[:200]}")
    
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    all_tasks = session.query(Task).filter_by(user_id=user.id).all()
    task2_after = next((t for t in all_tasks if 'встреча' in t.title.lower()), None)
    time_after = task2_after.reminder_time if task2_after else None
    print(f"После: {time_after}")
    
    if time_after and time_after != time_before:
        print("✅ УСПЕХ: Время изменилось")
    else:
        print("❌ ОШИБКА: Время не изменилось")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(main())
