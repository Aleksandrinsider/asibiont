"""Тест для проверки правильности отображения времени в AI контексте"""
import asyncio
from datetime import datetime, timedelta
import pytz
from ai_integration import list_tasks, replace_placeholders
from models import Session, User, Task

async def test_list_tasks():
    """Тестирует функцию list_tasks с разными задачами"""
    print("=== Тест list_tasks ===")
    
    session = Session()
    try:
        # Получаем пользователя
        user = session.query(User).first()
        if not user:
            print("❌ Пользователь не найден")
            return
        
        print(f"✅ Пользователь найден: {user.username}")
        print(f"   Timezone: {user.timezone}")
        
        # Проверяем задачи
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"✅ Найдено задач: {len(tasks)}")
        
        if tasks:
            for task in tasks:
                print(f"\n   Задача: {task.title}")
                print(f"   Статус: {task.status}")
                if task.reminder_time:
                    print(f"   Reminder UTC: {task.reminder_time}")
                    if task.reminder_time.tzinfo is None:
                        reminder_utc = pytz.UTC.localize(task.reminder_time)
                    else:
                        reminder_utc = task.reminder_time
                    
                    # Локальное время
                    user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                    reminder_local = reminder_utc.astimezone(user_tz)
                    print(f"   Reminder Local: {reminder_local}")
                    
                    # Текущее время
                    now_utc = datetime.now(pytz.UTC)
                    now_local = now_utc.astimezone(user_tz)
                    print(f"   Now Local: {now_local}")
                    
                    # Просрочена ли
                    if reminder_local < now_local:
                        delta = now_local - reminder_local
                        minutes = int(delta.total_seconds() / 60)
                        print(f"   ⚠️ ПРОСРОЧЕНА на {minutes} мин")
                    else:
                        delta = reminder_local - now_local
                        minutes = int(delta.total_seconds() / 60)
                        print(f"   ✅ Через {minutes} мин")
        
        # Вызываем list_tasks
        print("\n=== Результат list_tasks() ===")
        result = list_tasks(user_id=user.telegram_id, session=session)
        print(result)
        
    finally:
        session.close()

def test_replace_placeholders():
    """Тестирует замену плейсхолдеров"""
    print("\n=== Тест replace_placeholders ===")
    
    # Тестовый контент с плейсхолдерами
    test_content = """
    Привет! Сейчас {{current_time}} ({{current_date}}).
    Напоминание: "Заказать продукты" в 02:06 — через 5 минут.
    """
    
    print("До замены:")
    print(test_content)
    
    # Заменяем
    user_tz = pytz.timezone('Europe/Moscow')
    now_utc = datetime.now(pytz.UTC)
    now_local = now_utc.astimezone(user_tz)
    current_time_str = now_local.strftime('%H:%M')
    
    result = replace_placeholders(test_content, now_local, current_time_str)
    
    print("\nПосле замены:")
    print(result)
    print(f"\nТекущее время: {current_time_str}")

async def main():
    await test_list_tasks()
    test_replace_placeholders()
    print("\n✅ Все тесты завершены")

if __name__ == "__main__":
    asyncio.run(main())
