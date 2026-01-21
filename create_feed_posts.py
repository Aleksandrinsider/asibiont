"""Create sample posts for @marathon_runner in Railway database"""
import os
import sys
from dotenv import load_dotenv
from sqlalchemy.orm import Session as DBSession

# Load environment variables
load_dotenv()

# Import models and config
from models import User, Post, Session

def create_posts():
    """Create sample posts for test user"""
    session = Session()
    try:
        # Find user by username - using marathon_runner from Railway DB
        user = session.query(User).filter(
            User.username.ilike('marathon_runner')
        ).first()
        
        if not user:
            print("❌ User @marathon_runner not found in database")
            print("\nSearching for similar usernames...")
            similar_users = session.query(User).filter(
                User.username.ilike('%marathon%')
            ).all()
            if similar_users:
                print(f"Found {len(similar_users)} users with 'marathon' in username:")
                for u in similar_users:
                    print(f"  - @{u.username} (ID: {u.id})")
            else:
                print("No users found with 'marathon' in username")
            
            # Show all users with non-null username
            all_users = session.query(User).filter(
                User.username.isnot(None),
                User.username != 'None'
            ).limit(20).all()
            print(f"\nShowing first 20 users with valid usernames:")
            for u in all_users:
                print(f"  - @{u.username} (ID: {u.id}, telegram_id: {u.telegram_id})")
            return
        
        print(f"✅ Found user: @{user.username} (ID: {user.id})")
        
        # Sample posts
        posts_data = [
            "Сегодня пробежал 15 км по набережной! Погода отличная, настроение супер 🏃‍♂️💪 Готовлюсь к марафону в марте.",
            
            "Кто-нибудь бегает по утрам в районе парка Победы? Хочу найти компанию для пробежек, одному скучновато 😅",
            
            "Вчера был на спортивной выставке - столько крутых кроссовок! Взял себе новые Nike Air Zoom Pegasus. Кто бегает в них? Какие впечатления?",
            
            "Начал следить за питанием перед марафоном. Кто-нибудь пользуется приложением MyFitnessPal? Посоветуйте хорошие рецепты для спортсменов 🥗",
            
            "Утренняя пробежка в 6:00 - лучшее начало дня! Город еще спит, дороги пустые, воздух свежий ❄️🌅 Присоединяйтесь!",
        ]
        
        created_count = 0
        for content in posts_data:
            post = Post(
                user_id=user.id,
                content=content
            )
            session.add(post)
            created_count += 1
        
        session.commit()
        print(f"\n✅ Successfully created {created_count} posts for @{user.username}")
        print("\nПосты:")
        for i, content in enumerate(posts_data, 1):
            preview = content[:70] + "..." if len(content) > 70 else content
            print(f"  {i}. {preview}")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    print("Creating sample posts for @marathon_runner in Railway DB...\n")
    create_posts()
