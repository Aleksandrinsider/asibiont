#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Тест гибкости всех команд"""

import asyncio
from datetime import datetime
import pytz
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

async def test_flexible_commands():
    telegram_id = 888999
    session = Session()
    
    # Очистка
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    print("=" * 80)
    print("ТЕСТ ГИБКОСТИ ВСЕХ КОМАНД")
    print("=" * 80)
    
    # ТЕСТ 1: Создание задачи с разными форматами времени
    print("\n1️⃣  СОЗДАНИЕ ЗАДАЧ С РАЗНЫМИ ФОРМАТАМИ ВРЕМЕНИ")
    print("-" * 80)
    
    formats = [
        ("Купить молоко завтра в 9 утра", "завтра в 9"),
        ("Позвонить маме через 2 часа", "через 2 часа"),
        ("Встреча с боссом послезавтра в 14:30", "послезавтра в 14:30"),
        ("Отправить отчет в 17:00", "17:00"),
    ]
    
    for task_text, time_format in formats:
        response = await chat_with_ai(task_text, user_id=telegram_id)
        created = "✅" if "добавлен" in response.lower() or "напоминание" in response.lower() else "❌"
        print(f"{created} {time_format:20} | {task_text}")
    
    # Проверяем созданные задачи
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\n📊 Создано задач: {len(tasks)}/4")
    
    # ТЕСТ 2: Поиск задач с разными окончаниями
    print("\n\n2️⃣  ПОИСК ЗАДАЧ С РУССКИМИ ОКОНЧАНИЯМИ")
    print("-" * 80)
    
    if len(tasks) >= 2:
        # Берем первую задачу и пробуем найти её разными способами
        target_task = tasks[0]
        print(f"Ищем задачу: '{target_task.title}'")
        
        # Пробуем разные формулировки
        searches = [
            "завершить молоко",  # молоко vs молоку
            "закрой звонок",     # звонок vs позвонить
            "отчет готов",       # отчет vs отчёт
        ]
        
        for search in searches:
            response = await chat_with_ai(search, user_id=telegram_id)
            found = "✅" if "выполнен" in response.lower() or "завершен" in response.lower() else "❓"
            print(f"{found} '{search}'")
    
    # ТЕСТ 3: Перенос времени
    print("\n\n3️⃣  ПЕРЕНОС ЗАДАЧ С AI-ПАРСИНГОМ")
    print("-" * 80)
    
    if len(tasks) >= 1:
        task = tasks[-1]
        old_time = task.reminder_time
        
        reschedule_formats = [
            f"перенеси {task.title.split()[0]} на завтра в 11:00",
            f"сдвинь {task.title.split()[0]} на 18:30",
        ]
        
        for cmd in reschedule_formats:
            response = await chat_with_ai(cmd, user_id=telegram_id)
            session.expire_all()
            task_updated = session.query(Task).filter_by(id=task.id).first()
            changed = "✅" if task_updated and task_updated.reminder_time != old_time else "❌"
            print(f"{changed} {cmd}")
            if task_updated:
                old_time = task_updated.reminder_time
    
    # ТЕСТ 4: Редактирование с AI-парсингом
    print("\n\n4️⃣  РЕДАКТИРОВАНИЕ ВРЕМЕНИ")
    print("-" * 80)
    
    if len(tasks) >= 2:
        task = tasks[1]
        old_time = task.reminder_time
        
        response = await chat_with_ai(
            f"измени время {task.title.split()[0]} на через 3 часа",
            user_id=telegram_id
        )
        
        session.expire_all()
        task_updated = session.query(Task).filter_by(id=task.id).first()
        changed = "✅" if task_updated and task_updated.reminder_time != old_time else "❌"
        print(f"{changed} Время изменено с AI парсингом")
    
    # ТЕСТ 5: Удаление с гибким поиском
    print("\n\n5️⃣  УДАЛЕНИЕ С ГИБКИМ ПОИСКОМ")
    print("-" * 80)
    
    if len(tasks) >= 1:
        task = tasks[0]
        # Берем только первое слово и пробуем в другом падеже
        keyword = task.title.split()[0].lower()
        
        response = await chat_with_ai(f"удали {keyword}", user_id=telegram_id)
        deleted = "✅" if "удален" in response.lower() else "❌"
        print(f"{deleted} Удаление по ключевому слову '{keyword}'")
    
    # Финальная статистика
    print("\n\n" + "=" * 80)
    print("📈 ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 80)
    
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    remaining_tasks = session.query(Task).filter_by(user_id=user.id).all()
    
    print(f"✅ Создано задач: {len(tasks)}")
    print(f"🔄 Осталось задач: {len(remaining_tasks)}")
    print(f"🗑️  Удалено задач: {len(tasks) - len(remaining_tasks)}")
    
    print("\n🎉 Тест завершен!\n")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_flexible_commands())
