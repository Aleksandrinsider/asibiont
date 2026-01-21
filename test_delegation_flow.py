"""
Скрипт для тестирования полного цикла делегирования задач
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

session = Session()

# Находим двух пользователей для теста
user1 = session.query(User).filter_by(telegram_id=999001).first()
user2 = session.query(User).filter_by(telegram_id=999002).first()

if not user1 or not user2:
    print("❌ Тестовые пользователи не найдены")
    session.close()
    exit(1)

print(f"👤 User 1: {user1.username} (ID: {user1.id})")
print(f"👤 User 2: {user2.username} (ID: {user2.id})")

# Удаляем старые тестовые задачи
old_tasks = session.query(Task).filter(Task.title.like('ТЕСТ:%')).all()
for task in old_tasks:
    session.delete(task)
session.commit()
print(f"🗑️  Удалено {len(old_tasks)} старых тестовых задач")

# Создаем задачи для разных сценариев
now = datetime.now(pytz.UTC)
tomorrow = now + timedelta(days=1)

# Сценарий 1: User1 делегирует User2, ожидает принятия
task1 = Task(
    title="ТЕСТ: Задача ожидает принятия",
    description="User1 делегировал User2, задача в статусе pending",
    user_id=user1.id,
    status='in_progress',
    reminder_time=tomorrow,
    delegated_to_username=user2.username,
    delegation_status='pending'
)
session.add(task1)

# Сценарий 2: User1 делегирует User2, уже принята
task2 = Task(
    title="ТЕСТ: Задача принята",
    description="User1 делегировал User2, задача принята",
    user_id=user1.id,
    status='in_progress',
    reminder_time=tomorrow,
    delegated_to_username=user2.username,
    delegation_status='accepted'
)
session.add(task2)

# Сценарий 3: User1 делегирует User2, отклонена
task3 = Task(
    title="ТЕСТ: Задача отклонена",
    description="User1 делегировал User2, задача отклонена",
    user_id=user1.id,
    status='in_progress',
    reminder_time=tomorrow,
    delegated_to_username=user2.username,
    delegation_status='rejected'
)
session.add(task3)

# Сценарий 4: User2 делегирует User1, ожидает принятия
task4 = Task(
    title="ТЕСТ: Входящая задача",
    description="User2 делегировал User1, нужно принять или отклонить",
    user_id=user2.id,
    status='in_progress',
    reminder_time=tomorrow,
    delegated_to_username=user1.username,
    delegation_status='pending'
)
session.add(task4)

# Сценарий 5: Обычная задача без делегирования
task5 = Task(
    title="ТЕСТ: Обычная задача",
    description="Задача без делегирования",
    user_id=user1.id,
    status='in_progress',
    reminder_time=tomorrow
)
session.add(task5)

# Сценарий 6: Завершенная делегированная задача
task6 = Task(
    title="ТЕСТ: Завершенная делегированная",
    description="Делегированная задача которая была завершена",
    user_id=user1.id,
    status='completed',
    reminder_time=tomorrow,
    delegated_to_username=user2.username,
    delegation_status='accepted'
)
session.add(task6)

session.commit()

print("\n✅ Созданы тестовые задачи:")
print(f"  1. Задача от User1 → User2 (pending) - должна показывать 'Ожидает подтверждения'")
print(f"  2. Задача от User1 → User2 (accepted) - должна показывать 'В работе'")
print(f"  3. Задача от User1 → User2 (rejected) - должна показывать 'Отклонена'")
print(f"  4. Задача от User2 → User1 (pending) - должна показывать 'Ожидает принятия'")
print(f"  5. Обычная задача User1 - должна показывать 'В работе'")
print(f"  6. Завершенная делегированная - должна показывать 'Завершена'")

# Проверяем как будут выглядеть задачи для User1
print(f"\n📋 Задачи для {user1.username}:")
user1_tasks = session.query(Task).filter(
    (Task.user_id == user1.id) | (Task.delegated_to_username == user1.username)
).all()

for task in user1_tasks:
    status_text = task.status
    if task.delegated_to_username:
        if task.delegated_to_username == user1.username:
            direction = f"← от @{session.query(User).filter_by(id=task.user_id).first().username}"
        else:
            direction = f"→ на @{task.delegated_to_username}"
        print(f"  {task.title} {direction} | delegation_status={task.delegation_status} | status={status_text}")
    else:
        print(f"  {task.title} | status={status_text}")

session.close()
print("\n✅ Тестовые данные готовы. Войдите как user 999001 и проверьте отображение задач.")
