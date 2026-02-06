"""Отладка fuzzy search для связки 'звонок' -> 'Позвонить Петрову'"""
import os
os.environ["LOCAL"] = "1"

import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

from ai_integration.task_search import find_task_flexible, apply_stemming, levenshtein_distance, similarity_ratio
from models import Session, User, Task
from datetime import datetime, timedelta
import pytz

# Создадим тестовые данные
session = Session()
user_id = 999999999

user = session.query(User).filter_by(telegram_id=user_id).first()
if not user:
    user = User(telegram_id=user_id, username="test_fuzzy", timezone="Europe/Moscow")
    session.add(user)
    session.commit()

# Очистим старые задачи
old_tasks = session.query(Task).filter_by(user_id=user.id).all()
for t in old_tasks:
    session.delete(t)
session.commit()

# Создадим задачу "Позвонить Петрову"
task = Task(
    title="Позвонить Петрову",
    description="Тестовая задача",user_id=user.id,
    status="pending",
    created_at=datetime.now(pytz.UTC),
    reminder_time=datetime.now(pytz.UTC) + timedelta(hours=24)
)
session.add(task)
session.commit()

print("🔍 ТЕСТ FUZZY SEARCH")
print("=" * 60)
print(f"Задача в БД: '{task.title}'")
print(f"Поисковый запрос: 'звонок'\n")

# Тест 1: Stemming
print("1️⃣ Проверка stemming:")
stem_task = apply_stemming(task.title)
stem_query = apply_stemming("звонок")
print(f"   Задача после stemming: '{stem_task}'")
print(f"   Запрос после stemming: '{stem_query}'")
print(f"   Совпадение: {stem_query in stem_task}\n")

# Тест 2: Levenshtein distance
print("2️⃣ Levenshtein distance:")
dist = levenshtein_distance("звонок", "позвонить")
print(f"   Расстояние между 'звонок' и 'позвонить': {dist}")
ratio = similarity_ratio("звонок", "позвонить")
print(f"   Similarity ratio: {ratio:.2%}\n")

# Тест 3: find_task_flexible
print("3️⃣ Поиск через find_task_flexible:")
result = find_task_flexible(session, user, task_id=None, task_title="звонок")
if result:
    print(f"   ✅ НАЙДЕНО: '{result.title}'")
else:
    print("   ❌ НЕ НАЙДЕНО")
    
    # Дополнительная диагностика
    print("\n   🔍 Диагностика:")
    all_tasks = session.query(Task).filter_by(user_id=user.id).all()
    print(f"   Всего задач в БД: {len(all_tasks)}")
    for t in all_tasks:
        print(f"   - ID={t.id}, title='{t.title}', status={t.status}")

session.close()
