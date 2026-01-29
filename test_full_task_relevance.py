"""Полный тест релевантности задач с настройкой пользователей"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Task, Subscription, SubscriptionTier
from datetime import datetime, timedelta
import pytz

session = Session()

print("="*70)
print("НАСТРОЙКА ТЕСТОВЫХ ПОЛЬЗОВАТЕЛЕЙ")
print("="*70)

# User 1: test_user с задачей про пробежку
test_user = session.query(User).filter_by(username='test_user').first()
if not test_user:
    print("❌ test_user не найден")
    session.close()
    exit()

test_profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
if not test_profile:
    test_profile = UserProfile(user_id=test_user.id)
    session.add(test_profile)

test_profile.interests = "программирование, спорт"
test_profile.city = "Москва"

# Добавляем подписку если нет
test_sub = session.query(Subscription).filter_by(user_id=test_user.id).first()
if not test_sub:
    test_sub = Subscription(
        user_id=test_user.id,
        telegram_id=test_user.telegram_id,
        username=test_user.username,
        tier=SubscriptionTier.STANDARD,
        status='active',
        start_date=datetime.now(pytz.UTC),
        end_date=datetime.now(pytz.UTC) + timedelta(days=365)
    )
    session.add(test_sub)

session.commit()
print(f"✓ test_user: интересы='{test_profile.interests}', подписка={test_sub.tier.value}")

# User 2: бегун
sport_user = session.query(User).filter_by(telegram_id=1000002).first()
if sport_user:
    sport_profile = session.query(UserProfile).filter_by(user_id=sport_user.id).first()
    if sport_profile:
        print(f"✓ {sport_user.username}: интересы='{sport_profile.interests}'")
        
        # Проверяем подписку
        sport_sub = session.query(Subscription).filter_by(user_id=sport_user.id).first()
        print(f"  Подписка: {sport_sub.tier.value if sport_sub else 'НЕТ'}")

# Создаем задачу про пробежку
print("\n" + "="*70)
print("СОЗДАНИЕ ЗАДАЧИ ПРО ПРОБЕЖКУ")
print("="*70)

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
words = [w.lower().strip() for w in task.title.split() if len(w) > 3]
print(f"  Ключевые слова: {words}")

# Проверяем рекомендации
print("\n" + "="*70)
print("ПРОВЕРКА РЕКОМЕНДАЦИЙ")
print("="*70)

from ai_integration.handlers import get_partners_list
partners = get_partners_list(user_id=test_user.id, session=session)

print(f"\nНайдено партнеров: {len(partners)}")

if partners:
    print("\nТОП-5 рекомендаций:")
    for i, partner in enumerate(partners[:5], 1):
        partner_user = session.query(User).filter_by(id=partner.user_id).first()
        print(f"\n{i}. @{partner_user.username if partner_user else 'N/A'}")
        print(f"   Интересы: {partner.interests or 'не указаны'}")
        print(f"   Навыки: {partner.skills or 'не указаны'}")
        
        if hasattr(partner, 'task_relevance') and partner.task_relevance:
            print(f"   ⭐ РЕЛЕВАНТНОСТЬ ДЛЯ ЗАДАЧИ: {partner.task_relevance}")
            print(f"   ⭐ Оценка: {partner.task_relevance_score}")
        
        if hasattr(partner, 'common_interests') and partner.common_interests:
            print(f"   Общие интересы: {partner.common_interests}")
else:
    print("\n❌ Партнеров не найдено")
    print("\nВозможные причины:")
    print("1. У test_user нет интересов")
    print("2. Нет пользователей с совпадающими интересами")
    print("3. Фильтр по подпискам блокирует показ")

# Удаляем задачу
session.delete(task)
session.commit()
print(f"\n✓ Тестовая задача удалена")

session.close()
