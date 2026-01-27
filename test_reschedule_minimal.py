#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Минимальный тест reschedule_task"""

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
    
    # Создаём задачу
    print("1. Создание...")
    await chat_with_ai("Напомни проверить почту через 30 минут", user_id=user_id)
    
    # Показываем текущее время
    session.expire_all()
    task = session.query(Task).filter_by(user_id=user.id).first()
    if task:
        print(f"   Задача создана: {task.title}, время {task.reminder_time}")
    
    # Переносим
    print("\n2. Перенос...")
    response = await chat_with_ai("Перенеси почту на 15:00", user_id=user_id)
    print(f"   Ответ: {response[:100]}")
    
    # Проверяем
    session.expire_all()
    task = session.query(Task).filter_by(user_id=user.id).first()
    if task:
        from datetime import datetime
        import pytz
        moscow = pytz.timezone('Europe/Moscow')
        time_msk = task.reminder_time.astimezone(moscow)
        print(f"   Время после переноса: {time_msk}")
        
        if time_msk.hour == 15:
            print("\n✅ УСПЕХ! reschedule_task работает!")
        else:
            print(f"\n❌ ОШИБКА: Время не изменилось (ожидалось 15:00, получено {time_msk.hour}:00)")
    else:
        print("\n❌ Задача не найдена")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(main())
