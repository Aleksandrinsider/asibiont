"""Create GOLD test users for local database."""
import os
os.environ['LOCAL'] = '1'

from main import app  # Ensure DB is initialized
from models import User, UserProfile, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_gold_users():
    """Create test GOLD users with profiles."""
    db_path = os.path.join(os.path.dirname(__file__), "local.db")
    DATABASE_URL = f"sqlite:///{db_path}"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        # Create GOLD users with telegram_ids 2001-2009
        gold_users_data = [
            {"telegram_id": 2001, "username": "gold_user1", "first_name": "Алексей", "city": "Москва", "company": "Tech Corp", "position": "CEO"},
            {"telegram_id": 2002, "username": "gold_user2", "first_name": "Мария", "city": "Москва", "company": "StartUp Inc", "position": "CTO"},
            {"telegram_id": 2003, "username": "gold_user3", "first_name": "Дмитрий", "city": "Санкт-Петербург", "company": "Digital Agency", "position": "Founder"},
            {"telegram_id": 2004, "username": "gold_user4", "first_name": "Анна", "city": "Москва", "company": "Consulting Group", "position": "Partner"},
            {"telegram_id": 2005, "username": "gold_user5", "first_name": "Иван", "city": "Екатеринбург", "company": "IT Solutions", "position": "Director"},
            {"telegram_id": 2006, "username": "gold_user6", "first_name": "Елена", "city": "Москва", "company": "Marketing Pro", "position": "CMO"},
            {"telegram_id": 2007, "username": "gold_user7", "first_name": "Сергей", "city": "Казань", "company": "Dev Studio", "position": "Lead Developer"},
            {"telegram_id": 2008, "username": "gold_user8", "first_name": "Ольга", "city": "Москва", "company": "Finance Corp", "position": "CFO"},
        ]
        
        created_count = 0
        for user_data in gold_users_data:
            try:
                # Check if user exists
                existing_user = session.query(User).filter_by(telegram_id=user_data["telegram_id"]).first()
                
                if existing_user:
                    print(f"⚠️ User {user_data['username']} already exists")
                    # Update to GOLD if not
                    if existing_user.subscription_tier != 'GOLD':
                        existing_user.subscription_tier = 'GOLD'
                        session.commit()
                        print(f"  ✅ Updated to GOLD")
                        
                    # Check profile
                    profile = session.query(UserProfile).filter_by(user_id=existing_user.id).first()
                    if not profile:
                        profile = UserProfile(
                            user_id=existing_user.id,
                            city=user_data['city'],
                            company=user_data.get('company'),
                            position=user_data.get('position'),
                            interests="Бизнес, технологии, инновации"
                        )
                        session.add(profile)
                        session.commit()
                        print(f"  ✅ Created profile")
                    else:
                        print(f"  ℹ️ Profile already exists")
                    continue
                
                # Create new user
                user = User(
                    telegram_id=user_data["telegram_id"],
                    username=user_data["username"],
                    first_name=user_data["first_name"],
                    subscription_tier='GOLD'
                )
                session.add(user)
                session.commit()  # Commit user first to get ID
                
                # Create profile
                profile = UserProfile(
                    user_id=user.id,
                    city=user_data["city"],
                    company=user_data.get("company"),
                    position=user_data.get("position"),
                    interests="Бизнес, технологии, инновации"
                )
                session.add(profile)
                session.commit()  # Commit profile
                
                created_count += 1
                print(f"✅ Created GOLD user: {user_data['username']} (ID: {user.telegram_id})")
            except Exception as e:
                print(f"❌ Error creating {user_data['username']}: {e}")
                session.rollback()
                continue
        
        print(f"\n📊 Created {created_count} new GOLD users")
        
        # Verify
        all_gold = session.query(User).filter_by(subscription_tier='GOLD').all()
        print(f"🎯 Total GOLD users in database: {len(all_gold)}")
        
        for user in all_gold:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            print(f"  - {user.username}: profile={'YES' if profile else 'NO'}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == '__main__':
    create_gold_users()
