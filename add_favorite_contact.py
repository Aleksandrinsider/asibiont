"""Add test_user_1 to favorites of aleksandrinsider"""
from dotenv import load_dotenv
from models import User, UserProfile, Session
import json

load_dotenv()

def add_to_favorites():
    session = Session()
    try:
        # Find aleksandrinsider
        main_user = session.query(User).filter(
            User.username.ilike('aleksandrinsider')
        ).first()
        
        if not main_user:
            print("❌ User @aleksandrinsider not found")
            return
        
        print(f"✅ Found main user: @{main_user.username} (ID: {main_user.id})")
        
        # Find marathon_runner
        test_user = session.query(User).filter(
            User.username.ilike('marathon_runner')
        ).first()
        
        if not test_user:
            print("❌ User @marathon_runner not found")
            return
        
        print(f"✅ Found test user: @{test_user.username} (ID: {test_user.id})")
        
        # Get or create profile for aleksandrinsider
        profile = session.query(UserProfile).filter_by(user_id=main_user.id).first()
        if not profile:
            profile = UserProfile(user_id=main_user.id)
            session.add(profile)
            session.flush()
            print(f"✅ Created profile for @{main_user.username}")
        
        # Add test_user_1 to favorites
        favorites = []
        if profile.favorite_contacts:
            try:
                favorites = json.loads(profile.favorite_contacts)
            except:
                favorites = []
        
        if test_user.id not in favorites:
            favorites.append(test_user.id)
            profile.favorite_contacts = json.dumps(favorites)
            session.commit()
            print(f"\n✅ Added @{test_user.username} to favorites of @{main_user.username}")
            print(f"   Total favorites: {len(favorites)}")
        else:
            print(f"\n⚠️ @{test_user.username} already in favorites of @{main_user.username}")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    print("Adding @marathon_runner to favorites in Railway DB...\n")
    add_to_favorites()
