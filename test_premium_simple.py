"""Простой тест Premium - без эмодзи для Windows"""
import asyncio
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task, SubscriptionTier, Base, engine
from ai_integration.premium_simple import trigger_premium_automation_realtime, get_premium_recommendations_for_prompt
from datetime import datetime
import pytz
import json


async def test():
    print("="*70)
    print("TEST PREMIUM AUTOMATION")
    print("="*70)
    
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Clean old test data
        session.query(Task).filter(Task.user_id.in_(session.query(User.id).filter(User.telegram_id.in_([111111, 222222])))).delete(synchronize_session=False)
        session.query(UserProfile).filter(UserProfile.user_id.in_(session.query(User.id).filter(User.telegram_id.in_([111111, 222222])))).delete(synchronize_session=False)
        session.query(User).filter(User.telegram_id.in_([111111, 222222])).delete()
        session.commit()
        
        # Create Premium user
        premium = User(telegram_id=111111, username='premium', first_name='Premium', subscription_tier=SubscriptionTier.PREMIUM, timezone='Europe/Moscow')
        session.add(premium)
        session.commit()
        
        premium_profile = UserProfile(user_id=premium.id, interests='Python, AI', goals='Build AI platform', city='Moscow')
        session.add(premium_profile)
        session.commit()
        
        # Create partner
        partner = User(telegram_id=222222, username='partner', first_name='Partner', subscription_tier=SubscriptionTier.LIGHT, timezone='Europe/Moscow')
        session.add(partner)
        session.commit()
        
        partner_profile = UserProfile(user_id=partner.id, interests='AI, Python', skills='Python, ML', city='Moscow')
        session.add(partner_profile)
        session.commit()
        
        print("\n[OK] Created test users")
        print(f"  Premium: {premium.username}")
        print(f"  Partner: {partner.username}")
        
        # Premium creates task
        task = Task(user_id=premium.id, title='Find Python dev', description='Need Python developer for AI project', status='pending')
        session.add(task)
        session.commit()
        
        print(f"\n[OK] Premium created task: {task.title}")
        
        # Trigger automation
        print("\n[RUN] Triggering Premium automation...")
        try:
            result = await trigger_premium_automation_realtime(
                premium_user_id=premium.telegram_id,
                task_id=task.id,
                task_description=task.description
            )
            
            print(f"\n[RESULT] Automation completed:")
            print(f"  Items analyzed: {result.get('items_analyzed', 0)}")
            print(f"  Relevant users found: {result.get('relevant_users_found', 0)}")
            print(f"  Recommendations saved: {result.get('recommendations_saved', 0)}")
            
            if result.get('saved_details'):
                for det in result['saved_details']:
                    print(f"  -> {det['user']}: {det['match_reason']}")
        
        except Exception as e:
            print(f"\n[ERROR] Automation failed: {e}")
            result = {"error": str(e)}
        
        # Check partner's profile
        session.refresh(partner_profile)
        print("\n[CHECK] Partner's profile:")
        if partner_profile.pending_premium_recommendations:
            recs = json.loads(partner_profile.pending_premium_recommendations)
            print(f"  Found {len(recs)} recommendations")
            for i, rec in enumerate(recs, 1):
                print(f"\n  Recommendation #{i}:")
                print(f"    Opportunity: {rec.get('opportunity', 'N/A')[:60]}")
                print(f"    Reason: {rec.get('match_reason', 'N/A')}")
        else:
            print("  [EMPTY] No recommendations!")
        
        # Check what partner will see
        print("\n[CHECK] What partner will see in next chat:")
        prompt = get_premium_recommendations_for_prompt(partner.telegram_id, session)
        if prompt:
            print(prompt[:500])
        else:
            print("  [EMPTY] Nothing!")
        
        #==FINAL CONCLUSIONS==
        print("\n\n" + "="*70)
        print("CONCLUSIONS")
        print("="*70)
        
        print("\n[PROBLEM] Implementation != Description!")
        print("\nDESCRIPTION: 'AI on autopilot: finds partners, initiates collaborations'")
        print("\nREALITY:")
        print("  1. Premium creates task")
        print("  2. System finds relevant person")
        print("  3. Recommendation SAVED to person's profile")
        print("  4. When person messages bot (maybe in a week), AI mentions it")
        print("  5. Premium gets NO notifications about found partners")
        print("  6. No automatic messages/contact initiation")
        print("\nThis is NOT 'autopilot', it's PASSIVE matching via AI dialogue")
        
    finally:
        # Cleanup
        session.query(Task).delete()
        session.query(UserProfile).delete()
        session.query(User).delete()
        session.commit()
        session.close()
        print("\n[OK] Test data cleaned\n")


if __name__ == "__main__":
    asyncio.run(test())
