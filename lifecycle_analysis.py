"""
Анализ жизненного цикла делегированных задач
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
print("📖 ЖИЗНЕННЫЙ ЦИКЛ ДЕЛЕГИРОВАННОЙ ЗАДАЧИ")
print("=" * 80)

print("\n1️⃣ СОЗДАНИЕ И ДЕЛЕГИРОВАНИЕ")
print("   Александр создаёт задачу и делегирует Марии")
print("   ✅ status = 'pending'")
print("   ✅ delegation_status = 'pending'")
print("   ✅ user_id = maria.id (получатель)")
print("   ✅ delegated_by = aleksandr.id (кто делегировал)")
print("   ✅ delegated_to_username = 'fitness_maria'")

print("\n2️⃣ ПРИНЯТИЕ ЗАДАЧИ")
print("   Мария нажимает 'Принять задачу'")
print("   📝 Что ДОЛЖНО происходить:")
print("      ✅ delegation_status = 'accepted'")
print("      ✅ status = 'in_progress' (задача теперь в работе!)")
print("   📝 Где показывается:")
print("      ✅ У Марии: в разделе 'Мои задачи' или 'Назначенные мне'")
print("      ✅ У Александра: в разделе 'Поручил я' (видит что принята)")

print("\n3️⃣ ВЫПОЛНЕНИЕ ЗАДАЧИ")
print("   Мария отмечает задачу как выполненную")
print("   ✅ status = 'completed'")
print("   ✅ delegation_status = 'accepted' (остаётся)")
print("   📝 Где показывается:")
print("      ✅ У Марии: в разделе 'Завершённые'")
print("      ✅ У Александра: в разделе 'Поручил я' (видит что выполнена)")

print("\n4️⃣ ОТКЛОНЕНИЕ ЗАДАЧИ")
print("   Если Мария отклонит задачу:")
print("   ✅ status = 'rejected'")
print("   ✅ delegation_status = 'rejected'")
print("   📝 Где показывается:")
print("      ❌ Не показывается в активных задачах")
print("      ✅ Может быть в истории/архиве")

print("\n" + "=" * 80)
print("🔍 ПРОВЕРКА ФИЛЬТРОВ")
print("=" * 80)

print("\n📋 Фильтр 'Поручили мне':")
print("   status NOT IN ('completed', 'rejected') AND")
print("   delegation_status IN ('pending', 'accepted')")
print("   → Показывает pending (ожидает) и in_progress (принято)")

print("\n📋 Фильтр 'Поручил я':")
print("   status NOT IN ('deleted') AND")
print("   delegation_status IN ('pending', 'accepted')")
print("   → Показывает pending (ожидает), in_progress (принято), completed (выполнено)")

print("\n📋 Фильтр 'Мои задачи':")
print("   status NOT IN ('completed', 'rejected') AND")
print("   !is_delegated")
print("   → Только свои задачи, не делегированные")

print("\n📋 Фильтр 'Назначенные мне':")
print("   status NOT IN ('completed', 'rejected') AND")
print("   is_delegated AND title.includes('Делегирована от')")
print("   → Делегированные задачи в статусах pending и in_progress")

print("\n" + "=" * 80)
print("🐛 ТЕКУЩАЯ ПРОБЛЕМА")
print("=" * 80)

try:
    aleksandr = session.query(User).filter_by(username="aleksandrinsider").first()
    
    # Проверяем принятую задачу
    accepted_task = session.query(Task).filter(
        Task.delegated_to_username.ilike('aleksandrinsider'),
        Task.delegation_status == 'accepted'
    ).first()
    
    if accepted_task:
        print(f"\n❌ НАЙДЕНА ПРИНЯТАЯ ЗАДАЧА С НЕПРАВИЛЬНЫМ СТАТУСОМ:")
        print(f"   Задача: {accepted_task.title}")
        print(f"   status: {accepted_task.status}")
        print(f"   delegation_status: {accepted_task.delegation_status}")
        print(f"\n   ⚠️ ПРОБЛЕМА: status='{accepted_task.status}' вместо 'in_progress'")
        print(f"   💡 РЕШЕНИЕ: При принятии задачи status должен стать 'in_progress'")
    else:
        print("\n✅ Принятых задач с неправильным статусом не найдено")
    
    print("\n" + "=" * 80)
    print("✅ ЧТО НУЖНО ИСПРАВИТЬ:")
    print("=" * 80)
    print("\n1. В функции accept_task (main.py ~4910):")
    print("   task.delegation_status = 'accepted'")
    print("   task.status = 'in_progress'  # ← ДОБАВИТЬ ЭТО!")
    print("\n2. Frontend фильтры уже правильные:")
    print("   status !== 'completed' && status !== 'rejected'")
    print("   → Будут показывать 'pending' и 'in_progress'")
    print("\n3. Задача будет видна пока не completed/rejected")

finally:
    session.close()
