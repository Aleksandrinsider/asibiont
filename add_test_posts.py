#!/usr/bin/env python3
"""Add test posts from test_sport_5 and test_sport_7"""

import os
os.environ['LOCAL'] = '1'  # Force local mode

from models import Session, User, Post
import datetime

def add_test_posts():
    """Add several test posts from test_sport_5 and test_sport_7"""
    session = Session()
    try:
        # Find test users
        user5 = session.query(User).filter_by(username='test_sport_5').first()
        user7 = session.query(User).filter_by(username='test_sport_7').first()
        
        if not user5:
            print("❌ User test_sport_5 not found!")
            return
        if not user7:
            print("❌ User test_sport_7 not found!")
            return
        
        print(f"✅ Found users: {user5.username} (ID: {user5.id}), {user7.username} (ID: {user7.id})")
        
        # Posts from test_sport_5
        posts_user5 = [
            "Отличная утренняя пробежка! 10 км за 55 минут. Кто еще бегает по утрам? 🏃‍♂️",
            "Планирую завтра поход в горы. Есть желающие присоединиться? Маршрут средней сложности.",
            "Записался на марафон через 2 месяца. Нужна компания для подготовки и мотивации! 💪",
        ]
        
        # Posts from test_sport_7
        posts_user7 = [
            "Кто-нибудь знает хорошие велосипедные маршруты в Москве? Хочу покататься в выходные 🚴",
            "Сегодня попробовал скалолазание в первый раз - невероятные ощущения! Всем рекомендую 🧗",
            "Ищу партнера для игры в теннис. Уровень - любитель. Играю по вечерам в будни 🎾",
        ]
        
        now = datetime.datetime.now(datetime.timezone.utc)
        
        # Add posts from user5
        print(f"\n📝 Adding posts from {user5.username}:")
        for i, content in enumerate(posts_user5, 1):
            # Add slight time difference for realistic ordering
            post_time = now - datetime.timedelta(minutes=30*(len(posts_user5)-i))
            post = Post(
                user_id=user5.id,
                username=user5.username,
                content=content,
                created_at=post_time
            )
            session.add(post)
            print(f"  {i}. {content[:50]}...")
        
        # Add posts from user7
        print(f"\n📝 Adding posts from {user7.username}:")
        for i, content in enumerate(posts_user7, 1):
            # Add slight time difference for realistic ordering
            post_time = now - datetime.timedelta(minutes=20*(len(posts_user7)-i))
            post = Post(
                user_id=user7.id,
                username=user7.username,
                content=content,
                created_at=post_time
            )
            session.add(post)
            print(f"  {i}. {content[:50]}...")
        
        session.commit()
        print(f"\n✅ Successfully added {len(posts_user5)} posts from {user5.username}")
        print(f"✅ Successfully added {len(posts_user7)} posts from {user7.username}")
        print(f"\n💡 Total posts added: {len(posts_user5) + len(posts_user7)}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    add_test_posts()
