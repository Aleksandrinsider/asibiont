"""
Проверка логики делегирования задач через AI агента
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Task, User
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

print("=" * 80)
print("🐛 ПРОБЛЕМЫ В AI АГЕНТЕ С ДЕЛЕГИРОВАНИЕМ")
print("=" * 80)

print("\n📝 ФАЙЛ: ai_integration/handlers.py")
print("-" * 80)

print("\n1️⃣ ФУНКЦИЯ delegate_task() (строка 1079):")
print("   ❌ НЕПРАВИЛЬНО:")
print("      task = Task(")
print("          user_id=delegator.id,    # Создатель, а не получатель!")
print("          delegated_by=None,        # Не устанавливается!")
print("          delegated_to_username=recipient_username,")
print("      )")
print("\n   ✅ ПРАВИЛЬНО ДОЛЖНО БЫТЬ:")
print("      task = Task(")
print("          user_id=recipient.id,     # Получатель задачи")
print("          delegated_by=delegator.id, # Кто делегировал")
print("          delegated_to_username=recipient_username,")
print("      )")

print("\n2️⃣ ФУНКЦИЯ delegate_task_with_session() (строка 3294):")
print("   ❌ НЕПРАВИЛЬНО:")
print("      task = Task(")
print("          user_id=user.id,          # Создатель (делегирующий)")
print("          delegated_by=user.id,     # ✅ Правильно!")
print("          delegated_to_username=delegated_to_username,")
print("      )")
print("\n   ⚠️ ПРОБЛЕМА:")
print("      user_id должен быть delegated_user.id (получатель)")
print("\n   ✅ ПРАВИЛЬНО ДОЛЖНО БЫТЬ:")
print("      task = Task(")
print("          user_id=delegated_user.id, # Получатель задачи")
print("          delegated_by=user.id,      # Кто делегировал")
print("          delegated_to_username=delegated_to_username,")
print("      )")

print("\n" + "=" * 80)
print("🔍 ПОСЛЕДСТВИЯ ЭТИХ БАГОВ")
print("=" * 80)

print("\n❌ Что происходит с текущим кодом:")
print("   1. Задача создаётся с user_id=делегирующий (неправильно)")
print("   2. В разделе 'Поручили мне' у получателя НЕТ задачи")
print("   3. В разделе 'Поручил я' у делегирующего задача НЕ показывается")
print("   4. Задача 'зависает' у делегирующего как обычная задача")

print("\n✅ Что должно происходить:")
print("   1. Задача создаётся с user_id=получатель")
print("   2. В 'Поручили мне' у получателя появляется задача")
print("   3. В 'Поручил я' у делегирующего видна делегированная задача")
print("   4. Логика фильтров работает корректно")

print("\n" + "=" * 80)
print("📊 ТЕКУЩЕЕ СОСТОЯНИЕ БД")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    # Задачи созданные через AI агент (если есть)
    ai_delegated = session.query(Task).filter(
        Task.user_id == aleksandr.id,
        Task.delegated_to_username.isnot(None),
        Task.delegated_by.is_(None)  # delegated_by = NULL из-за бага
    ).all()
    
    if ai_delegated:
        print(f"\n⚠️ НАЙДЕНО {len(ai_delegated)} задач с БАГОМ (delegated_by=NULL):")
        for task in ai_delegated:
            print(f"   • {task.title}")
            print(f"     user_id: {task.user_id} (должен быть получатель!)")
            print(f"     delegated_by: {task.delegated_by} (NULL - БАГ!)")
            print(f"     delegated_to_username: {task.delegated_to_username}")
    else:
        print("\n✅ Задач с багом delegated_by=NULL не найдено")
    
    # Правильные задачи (созданные вручную или исправленные)
    correct_delegated = session.query(Task).filter(
        Task.delegated_by == aleksandr.id,
        Task.delegated_to_username.isnot(None)
    ).all()
    
    print(f"\n✅ Правильно созданных делегированных задач: {len(correct_delegated)}")
    for task in correct_delegated:
        recipient = session.query(User).filter_by(id=task.user_id).first()
        print(f"   • {task.title}")
        print(f"     user_id: {task.user_id} (@{recipient.username if recipient else 'N/A'})")
        print(f"     delegated_by: {task.delegated_by}")

finally:
    session.close()

print("\n" + "=" * 80)
print("✅ ЧТО НУЖНО ИСПРАВИТЬ:")
print("=" * 80)
print("\n1. ai_integration/handlers.py:1188")
print("   user_id=delegator.id → user_id=recipient.id")
print("   delegated_by=None → delegated_by=delegator.id")
print("\n2. ai_integration/handlers.py:3352")
print("   user_id=user.id → user_id=delegated_user.id")
print("\n3. Протестировать создание задачи через AI агента")
print("=" * 80)
