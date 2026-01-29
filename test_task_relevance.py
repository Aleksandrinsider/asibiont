"""Тест релевантности задач"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

session = Session()

# Создаем тестовую задачу с пробежкой
test_user = session.query(User).filter_by(username='test_user').first()
if not test_user:
    print("test_user не найден")
    session.close()
    exit()

# Создаем задачу "пойти на пробежку"
task = Task(
    user_id=test_user.id,
    title="пойти на пробежку в парке",
    description="утренняя пробежка 5 км",
    reminder_time=datetime.now(pytz.UTC) + timedelta(days=1),
    status='active'
)
session.add(task)
session.commit()

print(f"✓ Создана задача: '{task.title}'")
print(f"ID задачи: {task.id}")

# Проверяем что извлечется из задачи
words = [w.lower().strip() for w in task.title.split() if len(w) > 3]
print(f"\nИзвлеченные ключевые слова: {words}")

# Проверяем рекомендации
from ai_integration.handlers import get_partners_list

print("\n" + "="*70)
print("ПРОВЕРКА РЕКОМЕНДАЦИЙ С УЧЕТОМ ЗАДАЧИ")
print("="*70)

partners = get_partners_list(user_id=test_user.id, session=session)
print(f"\nНайдено партнеров: {len(partners)}")

if partners:
    print("\nТОП-5 с учетом задачи о пробежке:")
    for i, partner in enumerate(partners[:5], 1):
        print(f"\n{i}. User ID: {partner.user_id}")
        print(f"   Интересы: {partner.interests or 'N/A'}")
        print(f"   Навыки: {partner.skills or 'N/A'}")
        if hasattr(partner, 'task_relevance') and partner.task_relevance:
            print(f"   ⭐ Релевантность: {partner.task_relevance}")
            print(f"   ⭐ Оценка: {partner.task_relevance_score}")
        if hasattr(partner, 'common_interests') and partner.common_interests:
            print(f"   Общие интересы: {partner.common_interests}")

# Удаляем тестовую задачу
session.delete(task)
session.commit()
print(f"\n✓ Тестовая задача удалена")

session.close()
