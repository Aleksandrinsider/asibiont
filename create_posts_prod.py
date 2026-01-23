"""
Создать тестовые посты от test_sport пользователей в продакшн БД
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, Post
from datetime import datetime, timedelta

session = Session()
print(f"Database: {session.bind.url}")

# Посты для test_sport_5
test_sport_5 = session.query(User).filter_by(username='test_sport_5').first()
# Посты для test_sport_7  
test_sport_7 = session.query(User).filter_by(username='test_sport_7').first()

if not test_sport_5 or not test_sport_7:
    print("❌ Test users not found in production!")
    session.close()
    exit(1)

print(f"✓ Found test_sport_5: ID={test_sport_5.id}")
print(f"✓ Found test_sport_7: ID={test_sport_7.id}")

# Проверяем существующие посты
existing_5 = session.query(Post).filter_by(user_id=test_sport_5.id).count()
existing_7 = session.query(Post).filter_by(user_id=test_sport_7.id).count()

print(f"\nExisting posts:")
print(f"  test_sport_5: {existing_5}")
print(f"  test_sport_7: {existing_7}")

if existing_5 > 0 or existing_7 > 0:
    confirm = input("\nПосты уже существуют. Создать еще? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Отменено")
        session.close()
        exit(0)

# Посты для test_sport_5
posts_sport_5 = [
    "Отличная утренняя пробежка! 10 км за 55 минут. Кто еще любит бегать по утрам?",
    "Планирую завтра поход в горы. Есть желающие присоединиться? Маршрут средней сложности.",
    "Записался на марафон через 2 месяца. Нужна компания для тренировок - кто со мной?",
]

# Посты для test_sport_7
posts_sport_7 = [
    "Кто-нибудь знает хорошие велосипедные маршруты в Москве? Хочу покататься в выходные.",
    "Сегодня попробовал скалолазание в первый раз - невероятные ощущения! Рекомендую всем.",
    "Ищу партнера для игры в теннис. Уровень - любитель. Москва, район Сокольники.",
]

base_time = datetime.now() - timedelta(days=2)

print("\n=== Создание постов для test_sport_5 ===")
for i, content in enumerate(posts_sport_5):
    post = Post(
        user_id=test_sport_5.id,
        username=test_sport_5.username,
        content=content,
        created_at=base_time + timedelta(hours=i*8)
    )
    session.add(post)
    print(f"  + {content[:50]}...")

print("\n=== Создание постов для test_sport_7 ===")
for i, content in enumerate(posts_sport_7):
    post = Post(
        user_id=test_sport_7.id,
        username=test_sport_7.username,
        content=content,
        created_at=base_time + timedelta(hours=i*8, minutes=30)
    )
    session.add(post)
    print(f"  + {content[:50]}...")

session.commit()
print("\n✓ Посты созданы успешно!")

# Проверяем
total_5 = session.query(Post).filter_by(user_id=test_sport_5.id).count()
total_7 = session.query(Post).filter_by(user_id=test_sport_7.id).count()
print(f"\nИтого постов:")
print(f"  test_sport_5: {total_5}")
print(f"  test_sport_7: {total_7}")

session.close()
