"""
Очистка production БД напрямую + запуск теста
"""
import os
import sys
import time

# Убираем LOCAL для подключения к production
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, Task, User, UserProfile, Subscription, Interaction
from ai_integration import chat_with_ai

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757

print("="*60)
print("СБРОС PRODUCTION БД И ТЕСТИРОВАНИЕ")
print("="*60)

# 1. Очистка БД напрямую
print("\n1️⃣ Очистка PostgreSQL напрямую...")
session = Session()
try:
    session.query(Interaction).delete()
    session.query(Task).delete()
    session.query(UserProfile).delete()
    session.query(Subscription).delete()
    session.query(User).delete()
    session.commit()
    print("   ✅ БД полностью очищена")
except Exception as e:
    session.rollback()
    print(f"   ❌ Ошибка очистки: {e}")
    sys.exit(1)
finally:
    session.close()

# 2. Проверка что БД пуста
print("\n2️⃣ Проверка состояния БД...")
session = Session()
try:
    user_count = session.query(User).count()
    task_count = session.query(Task).count()
    print(f"   Пользователей: {user_count}")
    print(f"   Задач: {task_count}")
    
    if user_count == 0 and task_count == 0:
        print("   ✅ БД пуста")
    else:
        print("   ⚠️ БД не пуста!")
finally:
    session.close()

# 3. Создание тестового пользователя с подпиской
print("\n3️⃣ Создание тестового пользователя...")
session = Session()
try:
    from datetime import datetime, timedelta
    import pytz
    
    user = User(
        telegram_id=USER_ID,
        username='aleksandrinsider',
        first_name='Aleksandr',
        timezone='Europe/Moscow'
    )
    session.add(user)
    session.flush()
    
    # Профиль
    profile = UserProfile(
        user_id=user.id,
        city='Москва',
        interests='программирование, машинное обучение',
        skills='Python, AI, разработка',
        goals='Создать полезный AI-продукт',
        contact_info='aleksandrinsider'
    )
    session.add(profile)
    
    # Подписка
    subscription = Subscription(
        user_id=user.id,
        status='active',
        start_date=datetime.now(pytz.UTC),
        end_date=datetime.now(pytz.UTC) + timedelta(days=30)
    )
    session.add(subscription)
    
    session.commit()
    print(f"   ✅ Пользователь создан: {user.username} (ID: {user.id})")
    print(f"   ✅ Профиль: {profile.city}, {profile.interests}")
    print(f"   ✅ Подписка: {subscription.status} до {subscription.end_date.strftime('%d.%m.%Y')}")
finally:
    session.close()

print("\n4️⃣ Запуск теста AI агента...")
print("-"*60)

async def test_step(step_num, prompt):
    print(f"\n▶️ Шаг {step_num}: {prompt}")
    print("   Отправка запроса AI...")
    
    start_time = time.time()
    try:
        response = await chat_with_ai(prompt, [], USER_ID)
        elapsed = time.time() - start_time
        
        print(f"   ✅ Ответ получен за {elapsed:.1f}с:")
        print(f"   {response[:150]}..." if len(response) > 150 else f"   {response}")
        
        # Показываем состояние БД
        session = Session()
        user = session.query(User).filter_by(telegram_id=USER_ID).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            
            print(f"\n   📊 Состояние БД:")
            print(f"      Задач: {len(tasks)}")
            if tasks:
                for t in tasks[:3]:
                    reminder = t.reminder_time.strftime('%d.%m %H:%M') if t.reminder_time else 'нет'
                    print(f"      - {t.title} [{t.status}] {reminder}")
            if profile:
                print(f"      Интересы: {profile.interests}")
        session.close()
        
        print("   ⏳ Ожидание 3 секунды...\n")
        time.sleep(3)
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"   ❌ Ошибка за {elapsed:.1f}с: {e}")

# Запуск тестов
import asyncio

async def run_tests():
    await test_step(1, "Привет! Покажи мои задачи на сегодня")
    await test_step(2, "Добавь задачу: Проверить почту завтра в 09:00")
    await test_step(3, "Напомни сделать перерыв через 30 минут")
    await test_step(4, "Обнови мой профиль: также интересуюсь криптовалютами и DeFi")
    await test_step(5, "Выполнил задачу 'Сделать перерыв'")
    await test_step(6, "Удали задачу 'Проверить почту'")
    
    print("\n" + "="*60)
    print("ТЕСТ ЗАВЕРШЕН")
    print("="*60)
    print("\nПроверьте dashboard: https://task-production-31b6.up.railway.app/dashboard")

asyncio.run(run_tests())
