#!/usr/bin/env python3
"""Create test posts for feed indicator in PRODUCTION"""

# DON'T set LOCAL=1 - use production database

from models import Session, User, Post
from datetime import datetime, timedelta
import pytz

session = Session()

# Find test_sport_10 user
user = session.query(User).filter_by(username='test_sport_10').first()
if not user:
    print("❌ Пользователь test_sport_10 не найден")
    session.close()
    exit(1)

print(f"✅ Найден пользователь: {user.username} (ID: {user.id})")

now = datetime.now(pytz.UTC)

# Create 2 new posts
posts_data = [
    {
        'content': '🎯 Сегодня пробежал личный рекорд - 15 км за 1 час 12 минут! Чувствую себя на высоте, следующая цель - полумарафон 💪',
        'created_at': now - timedelta(minutes=5)
    },
    {
        'content': '☕️ Утренняя пробежка + кофе = идеальное начало дня! Кто еще любит активное утро? Делитесь своими ритуалами 🌅',
        'created_at': now - timedelta(minutes=2)
    }
]

print(f"\n📝 Создаю {len(posts_data)} постов...\n")

for i, post_data in enumerate(posts_data, 1):
    post = Post(
        user_id=user.id,
        content=post_data['content'],
        created_at=post_data['created_at']
    )
    session.add(post)
    print(f"  {i}. {post_data['content'][:60]}...")

session.commit()
print(f"\n✅ Создано {len(posts_data)} постов от {user.username}")

# Show total posts
total_posts = session.query(Post).filter_by(user_id=user.id).count()
print(f"📊 Всего постов у {user.username}: {total_posts}")

session.close()
