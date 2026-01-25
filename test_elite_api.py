"""Test elite partners API endpoint."""
import os
os.environ['LOCAL'] = '1'

from main import app
from models import User, UserProfile, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import asyncio


async def test_elite_api():
    """Test /api/elite_partners endpoint logic."""
    # Create session
    db_path = os.path.join(os.path.dirname(__file__), "local.db")
    DATABASE_URL = f"sqlite:///{db_path}"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    try:
        # Get test user (should be GOLD)
        user = session.query(User).filter_by(telegram_id=1003).first()
        if not user:
            print("❌ User 1003 not found")
            return
        
        print(f"✅ User: {user.username}, Tier: {user.subscription_tier}")
        
        # Check user profile
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            print("❌ User has no profile")
            return
        
        print(f"✅ Profile exists: city={profile.city}, company={profile.company}")
        
        # Get all GOLD users except current user
        gold_users = session.query(User).filter(
            User.subscription_tier == 'GOLD',
            User.id != user.id
        ).all()
        
        print(f"\n📊 Total GOLD users (excluding current): {len(gold_users)}")
        
        # Filter by those with profiles
        gold_with_profiles = []
        for gold_user in gold_users:
            gold_profile = session.query(UserProfile).filter_by(user_id=gold_user.id).first()
            if gold_profile:
                gold_with_profiles.append(gold_user)
                print(f"  ✅ {gold_user.username}: has profile (city={gold_profile.city})")
            else:
                print(f"  ❌ {gold_user.username}: NO profile")
        
        print(f"\n📋 GOLD users with profiles: {len(gold_with_profiles)}")
        
        # Check blocked contacts
        blocked = profile.blocked_contacts if profile.blocked_contacts else []
        print(f"🚫 Blocked contacts: {blocked}")
        
        # Check hidden contacts (users who blocked current user)
        hidden = []
        for gold_user in gold_with_profiles[:]:
            gold_profile = session.query(UserProfile).filter_by(user_id=gold_user.id).first()
            if gold_profile and gold_profile.blocked_contacts:
                if user.username in gold_profile.blocked_contacts:
                    hidden.append(gold_user.username)
                    gold_with_profiles.remove(gold_user)
        
        print(f"🙈 Hidden contacts (blocked current user): {hidden}")
        
        # Final visible count
        visible_count = len([u for u in gold_with_profiles 
                           if u.username not in blocked and u.username not in hidden])
        
        print(f"\n🎯 Final visible GOLD contacts: {visible_count}")
        
    finally:
        session.close()


if __name__ == '__main__':
    asyncio.run(test_elite_api())
