"""
Очистка старых тестовых задач из БД
"""
from models import Session, Task, User
from datetime import datetime
import pytz

USER_ID = 146333757

session = Session()
try:
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    if user:
        # Удалить все задачи старше 7 дней
        old_date = datetime.now(pytz.UTC).replace(day=25, month=12, year=2025)
        old_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.reminder_time < old_date
        ).all()
        
        print(f"Найдено старых задач: {len(old_tasks)}")
        for task in old_tasks:
            print(f"  Удаляю: {task.title} ({task.reminder_time})")
            session.delete(task)
        
        session.commit()
        print(f"\n✅ Удалено {len(old_tasks)} старых задач")
        
        # Показать оставшиеся задачи
        remaining = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\nОсталось задач: {len(remaining)}")
        for task in remaining:
            print(f"  - {task.title} ({task.reminder_time})")
    else:
        print("❌ Пользователь не найден")
finally:
    session.close()
