#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Быстрый тест ключевых функций
"""

import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, Task, SessionLocal

async def quick_function_test():
    print("⚡ БЫСТРЫЙ ТЕСТ КЛЮЧЕВЫХ ФУНКЦИЙ\n")

    session = SessionLocal()
    try:
        # Очищаем старые тестовые данные
        session.query(Task).filter_by(user_id=1).delete()
        session.commit()

        user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')

        # Тест 1: research_topic
        print("1️⃣ ТЕСТ research_topic:")
        result1 = await chat_with_ai(message="Как приготовить пасту карбонара?", user_id=user.id, db_session=session)
        tools1 = [call.get('function', {}).get('name', '') for call in result1.get('tool_calls', [])]
        print(f"   Инструменты: {tools1}")
        print("   ✅" if 'research_topic' in tools1 else "   ❌")

        # Тест 2: find_partners
        print("\n2️⃣ ТЕСТ find_partners:")
        result2 = await chat_with_ai(message="Где найти единомышленников по AI?", user_id=user.id, db_session=session)
        tools2 = [call.get('function', {}).get('name', '') for call in result2.get('tool_calls', [])]
        print(f"   Инструменты: {tools2}")
        print("   ✅" if 'find_partners' in tools2 else "   ❌")

        # Тест 3: add_task
        print("\n3️⃣ ТЕСТ add_task:")
        tasks_before = session.query(Task).filter_by(user_id=user.id).count()
        result3 = await chat_with_ai(message="Создай задачу: изучить Python асинхронность", user_id=user.id, db_session=session)
        tools3 = [call.get('function', {}).get('name', '') for call in result3.get('tool_calls', [])]
        tasks_after = session.query(Task).filter_by(user_id=user.id).count()
        print(f"   Инструменты: {tools3}")
        print(f"   Задач до: {tasks_before}, после: {tasks_after}")
        db_changed = tasks_after > tasks_before
        print("   ✅" if 'add_task' in tools3 and db_changed else "   ❌")

        # Тест 4: complete_task (если задача создана)
        if tasks_after > tasks_before:
            print("\n4️⃣ ТЕСТ complete_task:")
            result4 = await chat_with_ai(message="Сделал задачу про Python", user_id=user.id, db_session=session)
            tools4 = [call.get('function', {}).get('name', '') for call in result4.get('tool_calls', [])]
            completed_tasks = session.query(Task).filter_by(user_id=user.id, status='completed').count()
            print(f"   Инструменты: {tools4}")
            print(f"   Завершенных задач: {completed_tasks}")
            print("   ✅" if 'complete_task' in tools4 and completed_tasks > 0 else "   ❌")

        print("\n🎯 РЕЗУЛЬТАТ: Функции выполняются реально!" if all([
            'research_topic' in tools1,
            'find_partners' in tools2,
            'add_task' in tools3 and db_changed
        ]) else "\n⚠️ Некоторые функции нужно доработать.")

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(quick_function_test())