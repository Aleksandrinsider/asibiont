"""
Тест для production сервера на Railway
Открывайте dashboard в браузере: https://task-production-31b6.up.railway.app/dashboard
User: aleksandrinsider

ВАЖНО: Запускать БЕЗ LOCAL=1 чтобы использовать production PostgreSQL!
"""
import asyncio
import sys
import os

# Убираем LOCAL чтобы использовать production БД
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from ai_integration import chat_with_ai
from models import Session, Task, User, UserProfile
from datetime import datetime
import pytz

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757  # aleksandrinsider

def show_state():
    """Показать текущее состояние БД"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=USER_ID).first()
        if not user:
            print("❌ Пользователь не найден")
            return
        
        tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.reminder_time).all()
        print(f"\n📋 ЗАДАЧИ В БД ({len(tasks)}):")
        for task in tasks:
            emoji = "✅" if task.status == "completed" else "⏳"
            reminder = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "нет"
            print(f"  {emoji} {task.title} - {reminder} ({task.status})")
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n👤 ПРОФИЛЬ:")
            print(f"  Город: {profile.city or 'не указан'}")
            print(f"  Интересы: {profile.interests or 'не указаны'}")
    finally:
        session.close()

async def test_step(step_num, description, message):
    """Выполнить один шаг теста"""
    print(f"\n{'='*70}")
    print(f"ШАГ {step_num}: {description}")
    print(f"{'='*70}")
    print(f"💬 Отправляю: {message}")
    
    try:
        context = []
        response = await chat_with_ai(message, context, USER_ID)
        print(f"🤖 Ответ: {response[:200]}..." if len(response) > 200 else f"🤖 Ответ: {response}")
        show_state()
        print(f"\n⏳ Проверьте dashboard...")
        await asyncio.sleep(3)
        return True
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        return False

async def main():
    print("""
╔════════════════════════════════════════════════════════════════════╗
║                 ТЕСТ PRODUCTION СЕРВЕРА (20 ИТЕРАЦИЙ)              ║
║            https://task-production-31b6.up.railway.app             ║
╚════════════════════════════════════════════════════════════════════╝

📱 User: aleksandrinsider (ID: 146333757)
🌐 Dashboard: https://task-production-31b6.up.railway.app/dashboard

СЦЕНАРИИ ТЕСТА (по 20 итераций каждый):
1. Просмотр текущих задач
2. Добавление новой задачи с конкретным временем
3. Добавление задачи с относительным временем
4. Обновление профиля
5. Завершение задачи
6. Удаление задачи

Все изменения будут видны на dashboard в реальном времени!
    """)
    
    errors_count = 0
    success_count = 0
    
    for iteration in range(1, 21):
        print(f"\n{'#'*70}")
        print(f"ИТЕРАЦИЯ {iteration}/20")
        print(f"{'#'*70}")
        
        # Шаг 1: Просмотр задач
        result = await test_step(1, "Просмотр текущих задач", "Покажи мои задачи на сегодня")
        if result:
            success_count += 1
        else:
            errors_count += 1
        
        # Шаг 2: Добавление задачи
        result = await test_step(2, "Добавление задачи на конкретное время", 
                       f"Добавь задачу: Задача {iteration} завтра в 09:00")
        if result:
            success_count += 1
        else:
            errors_count += 1
        
        # Шаг 3: Относительное время
        result = await test_step(3, "Задача через 30 минут",
                       f"Напомни выполнить задачу {iteration} через 30 минут")
        if result:
            success_count += 1
        else:
            errors_count += 1
        
        # Шаг 4: Обновление профиля (каждые 5 итераций)
        if iteration % 5 == 0:
            result = await test_step(4, "Добавление интереса в профиль",
                       f"Также интересуюсь технологией {iteration}")
            if result:
                success_count += 1
            else:
                errors_count += 1
        
        # Шаг 5: Завершение задачи
        result = await test_step(5, "Завершение задачи",
                       f"Я выполнил задачу {iteration}, отметь как выполненную")
        if result:
            success_count += 1
        else:
            errors_count += 1
        
        # Шаг 6: Удаление задачи
        result = await test_step(6, "Удаление задачи",
                       f"Удали задачу {iteration}")
        if result:
            success_count += 1
        else:
            errors_count += 1
        
        print(f"\n📊 Статистика итерации {iteration}: Успешно: {success_count}, Ошибок: {errors_count}")
        await asyncio.sleep(1)
    
    print(f"\n{'='*70}")
    print("✅ ВСЕ 20 ИТЕРАЦИЙ ЗАВЕРШЕНЫ!")
    print(f"{'='*70}")
    print(f"\n📊 ИТОГОВАЯ СТАТИСТИКА:")
    print(f"  ✅ Успешно: {success_count}")
    print(f"  ❌ Ошибок: {errors_count}")
    print(f"  📈 Процент успеха: {(success_count/(success_count+errors_count)*100):.1f}%")
    print("\nПроверьте финальное состояние на dashboard:")
    print("https://task-production-31b6.up.railway.app/dashboard")
    show_state()

if __name__ == "__main__":
    asyncio.run(main())
