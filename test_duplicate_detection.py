"""
Тестирование обнаружения дубликатов и запроса времени
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

async def test_duplicate_and_time_detection():
    print("=== ТЕСТ: ОБНАРУЖЕНИЕ ДУБЛИКАТОВ И ЗАПРОС ВРЕМЕНИ ===\n")
    
    session = Session()
    try:
        # Используем тестового пользователя
        user = session.query(User).filter_by(telegram_id=999999).first()
        if not user:
            print("❌ Тестовый пользователь не найден")
            return
        
        print(f"✅ Тестовый пользователь: {user.username or user.first_name} (ID: {user.telegram_id})\n")
        
        # Очищаем старые тестовые задачи
        session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.in_(['Позвонить маме', 'Встреча с программистом'])
        ).delete()
        session.commit()
        print("🧹 Очищены старые тестовые задачи\n")
        
        # ТЕСТ 1: Создание задачи БЕЗ времени - должен спросить время
        print("=" * 60)
        print("ТЕСТ 1: Создание задачи БЕЗ указания времени")
        print("=" * 60)
        print("Сообщение: 'нужно позвонить маме'\n")
        
        response1 = await chat_with_ai(
            user_id=999999,
            message='нужно позвонить маме'
        )
        print(f"🤖 Ответ агента:\n{response1}\n")
        
        # Проверяем, что агент спросил время
        time_keywords = ['какое время', 'когда', 'во сколько', 'на какое время']
        asked_time = any(keyword in response1.lower() for keyword in time_keywords)
        
        if asked_time:
            print("✅ ТЕСТ 1 ПРОЙДЕН: Агент спросил время\n")
        else:
            print("❌ ТЕСТ 1 ПРОВАЛЕН: Агент НЕ спросил время\n")
        
        # ТЕСТ 2: Создание задачи С временем - должна создаться
        print("=" * 60)
        print("ТЕСТ 2: Создание задачи С указанием времени")
        print("=" * 60)
        print("Сообщение: 'нужно позвонить маме через 5 минут'\n")
        
        response2 = await chat_with_ai(
            user_id=999999,
            message='нужно позвонить маме через 5 минут'
        )
        print(f"🤖 Ответ агента:\n{response2}\n")
        
        # Проверяем, что задача создалась
        task_created = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.like('%Позвонить маме%')
        ).first()
        
        if task_created:
            print(f"✅ ТЕСТ 2 ПРОЙДЕН: Задача создана - {task_created.title} на {task_created.reminder_time}\n")
        else:
            print("❌ ТЕСТ 2 ПРОВАЛЕН: Задача НЕ создана\n")
        
        # ТЕСТ 3: Попытка создать дубликат - должен спросить
        print("=" * 60)
        print("ТЕСТ 3: Попытка создать дубликат задачи")
        print("=" * 60)
        print("Сообщение: 'позвонить маме в 15:00'\n")
        
        response3 = await chat_with_ai(
            user_id=999999,
            message='позвонить маме в 15:00'
        )
        print(f"🤖 Ответ агента:\n{response3}\n")
        
        # Проверяем, что агент спросил о дубликате
        duplicate_keywords = ['уже есть', 'похожая задача', 'изменить', 'отдельная']
        asked_duplicate = any(keyword in response3.lower() for keyword in duplicate_keywords)
        
        if asked_duplicate:
            print("✅ ТЕСТ 3 ПРОЙДЕН: Агент обнаружил дубликат и спросил\n")
        else:
            print("❌ ТЕСТ 3 ПРОВАЛЕН: Агент НЕ обнаружил дубликат\n")
        
        # ТЕСТ 4: Ответ на вопрос о дубликате - изменить существующую
        print("=" * 60)
        print("ТЕСТ 4: Изменение существующей задачи")
        print("=" * 60)
        print("Сообщение: 'изменить на 15:00'\n")
        
        response4 = await chat_with_ai(
            user_id=999999,
            message='изменить на 15:00'
        )
        print(f"🤖 Ответ агента:\n{response4}\n")
        
        # Проверяем количество задач (должна быть одна)
        tasks_count = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.like('%Позвонить маме%'),
            Task.status == 'pending'
        ).count()
        
        if tasks_count == 1:
            print("✅ ТЕСТ 4 ПРОЙДЕН: Задача изменена, дубликат не создан\n")
        else:
            print(f"❌ ТЕСТ 4 ПРОВАЛЕН: Найдено {tasks_count} задач вместо 1\n")
        
        # ТЕСТ 5: Создание другой задачи БЕЗ времени
        print("=" * 60)
        print("ТЕСТ 5: Другая задача без времени")
        print("=" * 60)
        print("Сообщение: 'завтра с утра встреча с программистом'\n")
        
        response5 = await chat_with_ai(
            user_id=999999,
            message='завтра с утра встреча с программистом'
        )
        print(f"🤖 Ответ агента:\n{response5}\n")
        
        # Проверяем, что агент спросил время И не упомянул задачу "Позвонить маме"
        asked_time_new = any(keyword in response5.lower() for keyword in time_keywords)
        mentioned_old_task = 'маме' in response5.lower() or 'позвонить' in response5.lower()
        
        if asked_time_new and not mentioned_old_task:
            print("✅ ТЕСТ 5 ПРОЙДЕН: Агент спросил время и не отвлёкся на другую задачу\n")
        else:
            if not asked_time_new:
                print("❌ ТЕСТ 5 ПРОВАЛЕН: Агент НЕ спросил время\n")
            if mentioned_old_task:
                print("❌ ТЕСТ 5 ПРОВАЛЕН: Агент отвлёкся на другую задачу\n")
        
        # ИТОГИ
        print("\n" + "=" * 60)
        print("ИТОГИ ТЕСТИРОВАНИЯ")
        print("=" * 60)
        
        all_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'pending'
        ).all()
        
        print(f"\nВсего активных задач: {len(all_tasks)}")
        for task in all_tasks:
            print(f"  - {task.title} на {task.reminder_time}")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_duplicate_and_time_detection())
