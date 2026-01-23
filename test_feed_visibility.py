"""
Тест ленты - проверяем почему не видны посты других пользователей
"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User, UserProfile, Post
import json

session = Session()

# Проверяем пользователя 1001
user = session.query(User).filter_by(telegram_id=1001).first()
print(f"\n=== User 1001 ===")
print(f"ID: {user.id}, username: {user.username}")

# Проверяем профиль
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
print(f"\n=== Profile ===")
print(f"favorite_contacts: {profile.favorite_contacts}")

# Парсим избранные
fav_list = json.loads(profile.favorite_contacts) if profile.favorite_contacts else []
print(f"Parsed favorites: {fav_list}")

# Список ID для ленты (избранные + сам пользователь)
all_user_ids = fav_list + [user.id]
print(f"\n=== Feed should show posts from IDs: {all_user_ids} ===")

# Проверяем посты
posts = session.query(Post).filter(Post.user_id.in_(all_user_ids)).order_by(Post.created_at.desc()).all()
print(f"\nTotal posts found: {len(posts)}")

# Группируем по пользователям
from collections import defaultdict
posts_by_user = defaultdict(list)
for post in posts:
    posts_by_user[post.user_id].append(post)

for user_id, user_posts in posts_by_user.items():
    post_user = session.query(User).filter_by(id=user_id).first()
    print(f"\nUser {user_id} ({post_user.username if post_user else '?'}): {len(user_posts)} posts")
    for p in user_posts[:2]:
        print(f"  - Post {p.id}: {p.content[:40]}...")

# Проверяем есть ли посты от самого пользователя 1001
my_posts = [p for p in posts if p.user_id == user.id]
print(f"\n=== My posts (user {user.id}): {len(my_posts)} ===")

# Проверяем посты от избранных
fav_posts = [p for p in posts if p.user_id in fav_list]
print(f"=== Posts from favorites {fav_list}: {len(fav_posts)} ===")

session.close()
