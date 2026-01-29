"""
Проверка логики завершения задачи
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Task
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🔍 ПРОВЕРКА ЛОГИКИ ЗАВЕРШЕНИЯ ЗАДАЧ")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    # Проверяем обе завершённые задачи
    task_37 = session.query(Task).filter_by(id=37).first()
    task_33 = session.query(Task).filter_by(id=33).first()
    
    print(f"\n📋 Задача ID=37:")
    print(f"   Название: {task_37.title if task_37 else 'N/A'}")
    print(f"   Status: {task_37.status if task_37 else 'N/A'}")
    print(f"   Created: {task_37.created_at if task_37 else 'N/A'}")
    
    print(f"\n📋 Задача ID=33:")
    print(f"   Название: {task_33.title if task_33 else 'N/A'}")
    print(f"   Status: {task_33.status if task_33 else 'N/A'}")
    print(f"   Created: {task_33.created_at if task_33 else 'N/A'}")
    print(f"   Delegated from: {task_33.delegated_by if task_33 else 'N/A'}")
    
    print(f"\n" + "=" * 80)
    print("🔍 ВОЗМОЖНЫЕ СЦЕНАРИИ:")
    print("=" * 80)
    
    print(f"""
СЦЕНАРИЙ 1: Пользователь нажал на кнопку завершения для задачи ID=33
   → Frontend: completeTask(33)
   → Backend: complete_task(task_id=33, user_id=146333757)
   → AI: find_task_flexible находит задачу ID=33
   → Обновляет только task.id=33
   → Задача ID=37 НЕ ДОЛЖНА завершиться
   
СЦЕНАРИЙ 2: Поиск по названию вместо ID
   Если find_task_flexible искал по названию "Купить спортивную экипировку":
   → Может найти похожую задачу по stemming
   → НО названия разные: "Купить..." vs "Завершить тестирование..."
   → Не должны совпасть
   
СЦЕНАРИЙ 3: Frontend отправил два запроса
   → Пользователь дважды кликнул
   → Или баг в JS
   → Нужно проверить логи браузера (F12 → Network)
   
СЦЕНАРИЙ 4: updateTasks() показал обе как completed
   → После завершения task_id=33 вызывается updateTasks()
   → Получает список всех задач с сервера
   → Обе задачи показаны как completed
   → Обе исчезают из списка "pending"
   
🎯 ВЕРОЯТНАЯ ПРИЧИНА:
   Скорее всего СЦЕНАРИЙ 4:
   1. Пользователь завершил task_id=33 (делегированная)
   2. Задача успешно обновлена в БД
   3. Frontend вызвал updateTasks()
   4. Сервер вернул обе задачи со status='completed'
   5. Обе исчезли из списка
   
   НО: Почему task_id=37 ТОЖЕ completed?
   
🔍 НУЖНО ПРОВЕРИТЬ:
   1. Время создания обеих задач (может task_id=37 была завершена раньше?)
   2. Логи Railway - поиск "COMPLETE_TASK" для обеих ID
   3. Был ли второй запрос на /complete_task?
""")
    
    # Проверка времени завершения
    if task_37 and hasattr(task_37, 'actual_completion_time') and task_37.actual_completion_time:
        print(f"\n⏰ Task ID=37 завершена: {task_37.actual_completion_time}")
    else:
        print(f"\n⏰ Task ID=37 actual_completion_time: N/A")
    
    if task_33 and hasattr(task_33, 'actual_completion_time') and task_33.actual_completion_time:
        print(f"⏰ Task ID=33 завершена: {task_33.actual_completion_time}")
    else:
        print(f"⏰ Task ID=33 actual_completion_time: N/A")
    
    print(f"\n" + "=" * 80)
    print("💡 РЕКОМЕНДАЦИЯ:")
    print("=" * 80)
    print(f"""
Добавить подробное логирование в complete_task_handler:

logger.info(f"[COMPLETE_TASK] User {{user_id}} completing task {{task_id}}")
logger.info(f"[COMPLETE_TASK] Found task: ID={{task.id}}, Title='{{task.title}}'")
logger.info(f"[COMPLETE_TASK] Task {{task.id}} status changed to completed")

Это покажет какая задача была передана и завершена.
""")

finally:
    session.close()

print("=" * 80)
