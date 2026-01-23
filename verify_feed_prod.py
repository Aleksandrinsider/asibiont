"""
Финальная проверка ленты в продакшн
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, UserProfile, Post
import json

session = Session()
print(f"Database: {session.bind.url}\n")

# Находим пользователя aleksandrinsider
user = session.query(User).filter_by(username='aleksandrinsider').first()
if not user:
    print("❌ User aleksandrinsider not found")
    session.close()
    exit(1)

print(f"=== User: {user.username} (telegram_id={user.telegram_id}) ===")

# Проверяем профиль
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
if not profile or not profile.favorite_contacts:
    print("❌ No favorite contacts")
    session.close()
    exit(1)

print(f"favorite_contacts (raw): {profile.favorite_contacts}")
favorite_data = json.loads(profile.favorite_contacts)
print(f"favorite_contacts (parsed): {favorite_data}")

# Преобразуем в ID
favorite_user_ids = []
for item in favorite_data:
    if isinstance(item, int):
        favorite_user_ids.append(item)
    elif isinstance(item, str):
        fav_user = session.query(User).filter(
            (User.username == item) | (User.username == item.replace('@', ''))
        ).first()
        if fav_user:
            favorite_user_ids.append(fav_user.id)
            print(f"  {item} -> ID {fav_user.id}")
        else:
            print(f"  ⚠️ {item} -> NOT FOUND")

print(f"\nFavorite user IDs: {favorite_user_ids}")

# Список для ленты
all_user_ids = favorite_user_ids + [user.id]
print(f"Feed will show posts from IDs: {all_user_ids}")

# Получаем посты
posts = session.query(Post).filter(
    Post.user_id.in_(all_user_ids)
).order_by(Post.created_at.desc()).all()

print(f"\n=== Posts in feed: {len(posts)} ===")
for p in posts:
    post_user = session.query(User).filter_by(id=p.user_id).first()
    print(f"  Post {p.id} by {post_user.username}: {p.content[:50]}...")

session.close()

print("\n✓ Проверка завершена")
print("\nЧтобы увидеть посты в браузере:")
print("1. Обновите страницу (Ctrl+F5)")
print("2. Откройте консоль (F12)")
print("3. Перейдите на вкладку 'Лента новостей'")
print("4. В консоли должно появиться: 'Feed data received: {...}'")
