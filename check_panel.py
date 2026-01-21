"""
Скрипт для проверки данных в панели
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task, UserProfile, Subscription
from datetime import datetime
import pytz

session = Session()

# Проверяем пользователя 999001
user = session.query(User).filter_by(telegram_id=999001).first()

if not user:
    print("❌ Пользователь 999001 не найден")
    session.close()
    exit(1)

print("=" * 60)
print("ПРОВЕРКА ДАННЫХ ПАНЕЛИ")
print("=" * 60)

# 1. ПРОФИЛЬ
print(f"\n👤 ПРОФИЛЬ:")
print(f"  Username: @{user.username}")
print(f"  Имя: {user.first_name}")
print(f"  Telegram ID: {user.telegram_id}")
print(f"  Фото: {user.photo_url or 'нет'}")

profile = session.query(UserProfile).filter_by(user_id=user.id).first()
if profile:
    print(f"  Город: {profile.city or 'не указан'}")
    print(f"  Компания: {profile.company or 'не указана'}")
    print(f"  Должность: {profile.position or 'не указана'}")
    print(f"  Рейтинг: {profile.average_rating or 0}/10 ({profile.rating_count or 0} отзывов)")
    print(f"  Избранные: {len(profile.favorite_contacts.split(',')) if profile.favorite_contacts else 0}")
    print(f"  Заблокированные: {len(profile.blocked_contacts.split(',')) if profile.blocked_contacts else 0}")
else:
    print("  ❌ Профиль не найден")

# 2. ПОДПИСКА
subscription = session.query(Subscription).filter_by(user_id=user.id).first()
if subscription:
    print(f"\n💎 ПОДПИСКА:")
    print(f"  Тариф: {subscription.tier.value if subscription.tier else 'BRONZE'}")
    print(f"  Активна до: {subscription.end_date.strftime('%d.%m.%Y') if subscription.end_date else 'не указана'}")
    print(f"  Статус: {'активна' if subscription.is_active else 'неактивна'}")
else:
    print(f"\n💎 ПОДПИСКА: Bronze (по умолчанию)")

# 3. ЗАДАЧИ
tasks = session.query(Task).filter(
    (Task.user_id == user.id) | (Task.delegated_to_username == user.username)
).all()

print(f"\n📋 ЗАДАЧИ ({len(tasks)} всего):")

# Группируем задачи
by_status = {}
by_delegation = {'created': 0, 'delegated_to_me': 0, 'delegated_by_me': 0}

for task in tasks:
    # По статусу
    status_key = task.status
    if task.delegation_status:
        status_key = f"{status_key}_{task.delegation_status}"
    by_status[status_key] = by_status.get(status_key, 0) + 1
    
    # По делегированию
    if task.delegated_to_username:
        if task.delegated_to_username.lower().strip('@') == user.username.lower():
            by_delegation['delegated_to_me'] += 1
        else:
            by_delegation['delegated_by_me'] += 1
    else:
        by_delegation['created'] += 1

print(f"  Статусы:")
for status, count in sorted(by_status.items()):
    print(f"    {status}: {count}")

print(f"\n  Делегирование:")
print(f"    Созданные мной: {by_delegation['created']}")
print(f"    Делегированные мной: {by_delegation['delegated_by_me']}")
print(f"    Делегированные мне: {by_delegation['delegated_to_me']}")

# Показываем тестовые задачи
test_tasks = [t for t in tasks if t.title.startswith('ТЕСТ:')]
if test_tasks:
    print(f"\n  🧪 Тестовые задачи ({len(test_tasks)}):")
    for task in test_tasks:
        status_display = task.status
        if task.delegation_status:
            status_display += f" ({task.delegation_status})"
        direction = ""
        if task.delegated_to_username:
            if task.delegated_to_username.lower().strip('@') == user.username.lower():
                direction = " ← входящая"
            else:
                direction = f" → @{task.delegated_to_username}"
        print(f"    {task.title[:40]}{direction} | {status_display}")

# 4. КОНТАКТЫ
all_users = session.query(User).filter(User.id != user.id).count()
gold_users = session.query(User).join(Subscription).filter(
    User.id != user.id,
    Subscription.tier == 'GOLD'
).count()

print(f"\n👥 КОНТАКТЫ:")
print(f"  Всего пользователей: {all_users}")
print(f"  Gold пользователей: {gold_users}")

if profile and profile.favorite_contacts:
    favorites = [u.strip() for u in profile.favorite_contacts.split(',') if u.strip()]
    print(f"  Избранные ({len(favorites)}): {', '.join(favorites[:5])}")

if profile and profile.blocked_contacts:
    blocked = [u.strip() for u in profile.blocked_contacts.split(',') if u.strip()]
    print(f"  Заблокированные ({len(blocked)}): {', '.join(blocked[:5])}")

# 5. ПРОВЕРКА API ENDPOINTS
print(f"\n🔗 API ENDPOINTS:")
print(f"  /api/tasks - возвращает {len(tasks)} задач")
print(f"  /api/partners - должен вернуть контакты")
print(f"  /api/elite_partners - должен вернуть {gold_users} Gold пользователей")
print(f"  /api/favorite_contacts - избранные")
print(f"  /api/blocked_contacts - заблокированные")

session.close()

print("\n" + "=" * 60)
print("✅ Проверка завершена")
print("=" * 60)
