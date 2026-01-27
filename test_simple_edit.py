#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Простой тест переноса одной задачи"""

import asyncio
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

async def main():
    user_id = 888999
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    # Создаём
    await chat_with_ai("Напомни купить молоко через 1 час", user_id=user_id)
    
    # Переносим
    response = await chat_with_ai("Перенеси молоко на завтра в 15:00", user_id=user_id)
    print(f"\nОтвет: {response[:200]}")
    
    # Проверяем
    session.expire_all()
    task = session.query(Task).filter_by(user_id=user.id).filter(Task.title.ilike("%молоко%")).first()
    if task:
        from datetime import datetime
        import pytz
        moscow = pytz.timezone('Europe/Moscow')
        time_msk = task.reminder_time.astimezone(moscow)
        print(f"Время задачи: {time_msk}")
        if time_msk.hour == 15:
            print("✅ УСПЕХ")
        else:
            print(f"❌ ОШИБКА: Ожидалось 15:00, получено {time_msk.hour}:00")
    else:
        print("❌ Задача не найдена")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(main())
