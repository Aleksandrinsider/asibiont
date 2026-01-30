"""Тест полного флоу переноса задачи с напоминанием"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
import pytz

# Настройки для локального тестирования
os.environ["LOCAL"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///./local.db"

# Импортируем main чтобы инициализировать сервис
import main
from models import Session, User, Task
from reminder_service import REMINDER_SERVICE
from ai_integration.handlers import reschedule_task

async def test_reschedule_with_running_service():
    """Тест переноса задачи с запущенным планировщиком"""
    
    print(f"\n🧪 ТЕСТ: Перенос задачи с запущенным планировщиком\n")
    
    # 1. Проверяем что REMINDER_SERVICE инициализирован
    print(f"📋 Проверка REMINDER_SERVICE:")
    if not REMINDER_SERVICE:
        print(f"   ❌ REMINDER_SERVICE не инициализирован!")
        return False
    print(f"   ✅ REMINDER_SERVICE инициализирован")
    
    # 2. Запускаем планировщик
    print(f"\n🚀 Запуск планировщика...")
    await REMINDER_SERVICE.start()
    
    if not REMINDER_SERVICE.scheduler.running:
        print(f"   ❌ Планировщик не запустился!")
        return False
    print(f"   ✅ Планировщик запущен")
    
    # 3. Создаем тестового пользователя и задачу
    db = Session()
    try:
        user = db.query(User).filter(User.telegram_id == 777777).first()
        if not user:
            user = User(
                telegram_id=777777,
                username="test_reschedule_user",
                first_name="Test",
                timezone="Europe/Moscow"
            )
            db.add(user)
            db.commit()
        
        # Создаем задачу с напоминанием через 5 минут
        now = datetime.now(pytz.UTC)
        reminder_time = now + timedelta(minutes=5)
        
        task = Task(
            user_id=user.id,
            title="Проверить перенос с запущенным планировщиком",
            description="Тестовая задача",
            status="pending",
            reminder_time=reminder_time,
            created_at=now
        )
        db.add(task)
        db.commit()
        
        task_id = task.id
        print(f"\n✅ Создана задача ID {task_id} с напоминанием на {reminder_time}")
        
        # 4. Планируем напоминание
        REMINDER_SERVICE.schedule_reminder(
            task_id=task_id,
            reminder_time=reminder_time,
            user_id=user.telegram_id,
            task_title=task.title
        )
        
        # Проверяем что джоб создан
        job_id = f"reminder_{task_id}"
        job = REMINDER_SERVICE.scheduler.get_job(job_id)
        if not job:
            print(f"   ❌ Напоминание не создано!")
            return False
        print(f"   ✅ Напоминание создано, срабатывание: {job.next_run_time}")
        
        # 5. Переносим задачу на +10 минут
        print(f"\n🔄 Переносим задачу на +10 минут...")
        result = await reschedule_task(
            task_title="Проверить перенос с запущенным планировщиком",
            new_time="через 15 минут",
            user_id=777777
        )
        
        print(f"   📝 Результат переноса: {result}")
        
        # 6. Проверяем что джоб обновлен
        new_job = REMINDER_SERVICE.scheduler.get_job(job_id)
        if not new_job:
            print(f"   ❌ Напоминание не найдено после переноса!")
            return False
        
        db.refresh(task)
        expected_time = task.reminder_time
        
        print(f"\n📅 Проверка результатов:")
        print(f"   Время в БД: {expected_time}")
        print(f"   Время в планировщике: {new_job.next_run_time}")
        
        # Проверяем что время совпадает (с точностью до 1 секунды)
        time_diff = abs((new_job.next_run_time.replace(tzinfo=None) - expected_time.replace(tzinfo=None)).total_seconds())
        if time_diff > 1:
            print(f"   ❌ Время не совпадает! Разница: {time_diff} секунд")
            return False
        
        print(f"   ✅ Время напоминания обновлено правильно!")
        print(f"   ✅ ТЕСТ ПРОЙДЕН!")
        
        return True
        
    finally:
        db.close()
        # Останавливаем планировщик
        REMINDER_SERVICE.scheduler.shutdown(wait=False)

if __name__ == "__main__":
    success = asyncio.run(test_reschedule_with_running_service())
    sys.exit(0 if success else 1)
