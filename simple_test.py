"""Простой тест времени задач"""
import sys
sys.path.insert(0, 'c:\\Users\\Insider\\Desktop\\Task')

from datetime import datetime, timedelta
import pytz
from models import Session, User, Task

def test_task_time():
    print("=== Тест отображения времени задач ===\n")
    
    session = Session()
    try:
        # Получаем пользователя
        user = session.query(User).first()
        if not user:
            print("❌ Пользователь не найден в БД")
            return
        
        print(f"✅ Пользователь: {user.username}")
        print(f"   Telegram ID: {user.telegram_id}")
        print(f"   Timezone: {user.timezone}\n")
        
        # Получаем часовой пояс
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        
        # Текущее время
        now_utc = datetime.now(pytz.UTC)
        now_local = now_utc.astimezone(user_tz)
        
        print(f"⏰ Текущее время:")
        print(f"   UTC: {now_utc.strftime('%H:%M:%S')}")
        print(f"   Local ({user.timezone}): {now_local.strftime('%H:%M:%S')}\n")
        
        # Проверяем задачи
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"📋 Найдено задач: {len(tasks)}\n")
        
        if not tasks:
            print("   (нет задач для проверки)")
            return
        
        for i, task in enumerate(tasks, 1):
            print(f"{i}. {task.title}")
            print(f"   Статус: {task.status}")
            
            if task.reminder_time:
                # Конвертируем в UTC если нужно
                if task.reminder_time.tzinfo is None:
                    reminder_utc = pytz.UTC.localize(task.reminder_time)
                else:
                    reminder_utc = task.reminder_time
                
                # Локальное время напоминания
                reminder_local = reminder_utc.astimezone(user_tz)
                
                print(f"   Напоминание (UTC): {reminder_utc.strftime('%d.%m.%Y %H:%M')}")
                print(f"   Напоминание (Local): {reminder_local.strftime('%d.%m.%Y %H:%M')}")
                
                # Рассчитываем разницу
                delta = now_local - reminder_local
                delta_minutes = int(delta.total_seconds() / 60)
                
                if reminder_local < now_local and task.status == 'pending':
                    hours = abs(delta_minutes) // 60
                    minutes = abs(delta_minutes) % 60
                    if hours > 0:
                        print(f"   ⚠️  ПРОСРОЧЕНА на {hours}ч {minutes}мин")
                    else:
                        print(f"   ⚠️  ПРОСРОЧЕНА на {minutes}мин")
                elif reminder_local > now_local and task.status == 'pending':
                    hours = abs(delta_minutes) // 60
                    minutes = abs(delta_minutes) % 60
                    if hours > 0:
                        print(f"   ✅ Через {hours}ч {minutes}мин")
                    else:
                        print(f"   ✅ Через {minutes}мин")
                elif task.status == 'completed':
                    print(f"   ✔️  Выполнено")
            else:
                print(f"   ⏰ Без напоминания")
            
            print()
        
    finally:
        session.close()
    
    print("✅ Тест завершён")

if __name__ == "__main__":
    test_task_time()
