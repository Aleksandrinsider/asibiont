#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Минимальный тест для отладки edit_task"""

import asyncio
import logging
from ai_integration.chat import chat_with_ai
from models import Session, Task, User

# Включаем подробное логирование
logging.basicConfig(level=logging.INFO)

async def main():
    user_id = 888999
    
    # Очистим базу
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
        print("База очищена\n")
    session.close()
    
    # Создаём задачу
    print("Создание задачи...")
    response = await chat_with_ai("Напомни проверить почту через 30 минут", user_id=user_id)
    print(f"Ответ: {response[:100]}\n")
    
    # Переносим задачу
    print("="*80)
    print("Перенос задачи...")
    print("="*80)
    response = await chat_with_ai("Перенеси проверить почту на завтра в 10:00", user_id=user_id)
    print(f"Ответ: {response[:200]}")
    
    # Проверяем результат
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    task = session.query(Task).filter_by(user_id=user.id, title="Проверить почту").first()
    if task:
        print(f"\nЗадача найдена: {task.title}")
        print(f"Время: {task.reminder_time}")
    else:
        print("\nЗадача НЕ найдена!")
    session.close()

if __name__ == "__main__":
    asyncio.run(main())
