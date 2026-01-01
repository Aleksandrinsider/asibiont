import os
os.environ['LOCAL'] = '1'

from ai_integration import chat_with_ai
from models import Session, User, Task
from datetime import datetime
import asyncio
import pytz

async def test_relative_time():
    # Создаем тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=9999).first()
    if not user:
        user = User(telegram_id=9999, timezone='Europe/Moscow')
        session.add(user)
        session.commit()
    
    # Очистить старые задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    print("Тест 1: 'напомни через 5 минут купить молоко'")
    print("Текущее время (Moscow):", datetime.now(pytz.timezone('Europe/Moscow')).strftime('%H:%M'))
    
    response = await chat_with_ai("напомни через 5 минут купить молоко", [], 9999)
    print("Ответ AI:", response)
    
    # Проверить созданную задачу
    task = session.query(Task).filter_by(user_id=user.id).first()
    if task and task.reminder_time:
        # Конвертировать UTC -> Moscow
        moscow_tz = pytz.timezone('Europe/Moscow')
        reminder_moscow = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(moscow_tz)
        print(f"Задача создана с reminder_time (UTC): {task.reminder_time}")
        print(f"Задача создана с reminder_time (Moscow): {reminder_moscow.strftime('%Y-%m-%d %H:%M')}")
        
        # Проверить разницу
        now_moscow = datetime.now(moscow_tz)
        delta = (reminder_moscow - now_moscow).total_seconds() / 60
        print(f"Разница с текущим временем: {delta:.1f} минут (должно быть ~5)")
        
        if 3 <= delta <= 7:
            print("✓ ТЕСТ ПРОЙДЕН (время расчитано правильно с погрешностью на выполнение теста)")
        else:
            print("✗ ТЕСТ НЕ ПРОЙДЕН")
    else:
        print("✗ Задача не создана")
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_relative_time())
