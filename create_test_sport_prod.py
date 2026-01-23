#!/usr/bin/env python3
"""Create test sport users and posts in PRODUCTION database"""

# DON'T set LOCAL=1 - use production database
from models import Session, User, UserProfile, Post, SubscriptionTier
import datetime

def create_users_and_posts():
    """Create test_sport_5 and test_sport_7 users with posts in PRODUCTION"""
    session = Session()
    try:
        # Check if users already exist
        user5 = session.query(User).filter_by(username='test_sport_5').first()
        user7 = session.query(User).filter_by(username='test_sport_7').first()
        
        # Create user5 if not exists
        if not user5:
            user5 = User(
                telegram_id=10005,
                username='test_sport_5',
                first_name='Алексей',
                subscription_tier=SubscriptionTier.SILVER
            )
            session.add(user5)
            session.flush()  # Get ID
            
            profile5 = UserProfile(
                user_id=user5.id,
                city='Москва',
                interests='бег, походы, марафоны, фитнес',
                skills='длинные дистанции, выносливость',
                goals='пробежать марафон, улучшить время',
                bio='Увлекаюсь бегом и активным отдыхом'
            )
            session.add(profile5)
            print(f"✅ Created user: {user5.username}")
        else:
            print(f"ℹ️  User {user5.username} already exists (ID: {user5.id})")
        
        # Create user7 if not exists
        if not user7:
            user7 = User(
                telegram_id=10007,
                username='test_sport_7',
                first_name='Дмитрий',
                subscription_tier=SubscriptionTier.SILVER
            )
            session.add(user7)
            session.flush()  # Get ID
            
            profile7 = UserProfile(
                user_id=user7.id,
                city='Москва',
                interests='велоспорт, скалолазание, теннис, активный отдых',
                skills='велоспорт, экстрим',
                goals='участвовать в соревнованиях, найти партнеров',
                bio='Люблю активный спорт и новые вызовы'
            )
            session.add(profile7)
            print(f"✅ Created user: {user7.username}")
        else:
            print(f"ℹ️  User {user7.username} already exists (ID: {user7.id})")
        
        session.commit()
        
        # Check existing posts
        existing_posts_5 = session.query(Post).filter_by(user_id=user5.id).count()
        existing_posts_7 = session.query(Post).filter_by(user_id=user7.id).count()
        
        print(f"\n📊 Existing posts: {user5.username}={existing_posts_5}, {user7.username}={existing_posts_7}")
        
        # Now add posts
        print("\n📝 Adding posts...")
        
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
        print(f"\nПосты от {user5.username}:")
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
            print(f"  {i}. {content[:60]}...")
        
        # Add posts from user7
        print(f"\nПосты от {user7.username}:")
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
            print(f"  {i}. {content[:60]}...")
        
        session.commit()
        
        print(f"\n✅ Добавлено {len(posts_user5)} постов от {user5.username}")
        print(f"✅ Добавлено {len(posts_user7)} постов от {user7.username}")
        print(f"\n💡 Всего новых постов: {len(posts_user5) + len(posts_user7)}")
        print("\n🎉 Готово! Посты созданы в ПРОДАКШЕН БД")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    print("🚀 Creating users and posts in PRODUCTION database...")
    create_users_and_posts()
