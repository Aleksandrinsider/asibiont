#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Улучшенный тест после доработок"""

import asyncio
from datetime import datetime
import pytz
from models import Session, Task, User
from ai_integration.chat import chat_with_ai

async def test_improvements():
    telegram_id = 999111
    session = Session()
    
    # Очистка
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()
    
    print("=" * 80)
    print("ТЕСТ ПОСЛЕ ДОРАБОТОК")
    print("=" * 80)
    
    # 1. Создание задач
    print("\n1️⃣  ADD_TASK - создание с улучшенными описаниями")
    print("-" * 80)
    
    tasks_to_create = [
        "Напомни купить молоко завтра в 9:00",
        "Встреча с клиентом послезавтра в 14:30",
        "Позвонить маме через 2 часа",
    ]
    
    for task_text in tasks_to_create:
        response = await chat_with_ai(task_text, user_id=telegram_id)
        print(f"  {task_text[:40]} -> {response[:60]}")
    
    session.expire_all()
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"\n✅ Создано: {len(tasks)} задач")
    for i, t in enumerate(tasks, 1):
        print(f"   {i}. {t.title} -> {t.reminder_time}")
    
    # 2. COMPLETE_TASK с tool_choice="required"
    print("\n2️⃣  COMPLETE_TASK - завершение с форсированием")
    print("-" * 80)
    
    if len(tasks) > 0:
        task = tasks[0]
        print(f"До: {task.title} -> статус: {task.status}")
        
        # Используем разные формулировки
        formulations = [
            f"Сделал {task.title.split()[0]}",
            f"Выполнил {task.title.split()[0]}",
            f"Завершил {task.title.split()[0]}",
        ]
        
        for formulation in formulations[:1]:  # Пробуем первую
            response = await chat_with_ai(formulation, user_id=telegram_id)
            print(f"  Запрос: {formulation}")
            print(f"  Ответ: {response[:60]}")
            
            session.expire_all()
            task_updated = session.query(Task).filter_by(id=task.id).first()
            if task_updated:
                print(f"После: {task_updated.title} -> статус: {task_updated.status}")
                if task_updated.status == "completed":
                    print("✅ УСПЕХ: Задача завершена!")
                else:
                    print(f"❌ ОШИБКА: Статус не изменился ({task_updated.status})")
            break
    
    # 3. RESCHEDULE без edit_task
    print("\n3️⃣  RESCHEDULE_TASK - перенос без конфликта с edit_task")
    print("-" * 80)
    
    session.expire_all()
    active_tasks = session.query(Task).filter_by(user_id=user.id, status="pending").all()
    
    if len(active_tasks) > 0:
        task = active_tasks[0]
        old_time = task.reminder_time
        print(f"До: {task.title} -> {old_time}")
        
        response = await chat_with_ai(f"Перенеси {task.title.split()[0]} на 18:00", user_id=telegram_id)
        print(f"Ответ: {response[:60]}")
        
        session.expire_all()
        task_updated = session.query(Task).filter_by(id=task.id).first()
        if task_updated:
            print(f"После: {task_updated.title} -> {task_updated.reminder_time}")
            if task_updated.reminder_time != old_time:
                print("✅ УСПЕХ: Время изменено!")
            else:
                print("❌ ОШИБКА: Время не изменилось")
    
    # 4. DELETE с гибким поиском
    print("\n4️⃣  DELETE_TASK - удаление")
    print("-" * 80)
    
    session.expire_all()
    tasks_before = session.query(Task).filter_by(user_id=user.id).count()
    
    if tasks_before > 0:
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        task = tasks[-1]
        keyword = task.title.split()[0]
        
        print(f"До: {tasks_before} задач")
        response = await chat_with_ai(f"Удали {keyword}", user_id=telegram_id)
        print(f"Ответ: {response[:60]}")
        
        session.expire_all()
        tasks_after = session.query(Task).filter_by(user_id=user.id).count()
        print(f"После: {tasks_after} задач")
        
        if tasks_after < tasks_before:
            print("✅ УСПЕХ: Задача удалена!")
        else:
            print("❌ ОШИБКА: Количество не изменилось")
    
    # ИТОГИ
    print("\n" + "=" * 80)
    print("📊 ИТОГИ")
    print("=" * 80)
    
    session.expire_all()
    final_tasks = session.query(Task).filter_by(user_id=user.id).all()
    completed = [t for t in final_tasks if t.status == "completed"]
    pending = [t for t in final_tasks if t.status == "pending"]
    
    print(f"✅ Завершено: {len(completed)}")
    print(f"📋 Активных: {len(pending)}")
    print(f"🗑️  Удалено: {len(tasks) - len(final_tasks)}")
    
    print("\n🎉 Тест завершен!\n")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(test_improvements())
