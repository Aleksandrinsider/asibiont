"""
Простой тест API для проверки отображения постов в ленте
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Post
import json

session = Session()

# Проверяем пользователя 1001
user = session.query(User).filter_by(telegram_id=1001).first()
print(f"\n=== Пользователь 1001 ===")
print(f"ID: {user.id}")
print(f"Username: {user.username}")

# Проверяем профиль и избранные
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
print(f"\n=== Профиль ===")
print(f"favorite_contacts: {profile.favorite_contacts}")

# Парсим JSON
try:
    fav_list = json.loads(profile.favorite_contacts)
    print(f"Parsed favorites: {fav_list}")
    print(f"Type of elements: {[type(x) for x in fav_list]}")
except Exception as e:
    print(f"Error parsing: {e}")
    fav_list = []

# Проверяем всех пользователей которые есть в списке
all_user_ids = fav_list + [user.id]
print(f"\n=== Все ID для ленты (избранные + я) ===")
print(f"IDs: {all_user_ids}")

# Проверяем посты от этих пользователей
posts = session.query(Post).filter(Post.user_id.in_(all_user_ids)).order_by(Post.created_at.desc()).all()
print(f"\n=== Посты ===")
print(f"Всего постов: {len(posts)}")

for post in posts:
    post_user = session.query(User).filter_by(id=post.user_id).first()
    print(f"  - Post {post.id} от user_id={post.user_id} ({post_user.username if post_user else '?'}): {post.content[:50]}...")

# Проверяем всех пользователей test_sport
test_users = session.query(User).filter(User.username.like('%test_sport%')).all()
print(f"\n=== Test Sport Users ===")
for tu in test_users:
    posts_count = session.query(Post).filter_by(user_id=tu.id).count()
    print(f"  - {tu.username} (ID={tu.id}, telegram_id={tu.telegram_id}): {posts_count} постов")

session.close()

print("\n✓ Тест завершен")
