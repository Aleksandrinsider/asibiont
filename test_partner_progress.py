"""Test Premium partner progress notifications"""
import asyncio
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task, SubscriptionTier, Base, engine
from ai_integration.premium_simple import trigger_premium_automation_realtime, get_premium_recommendations_for_prompt
from ai_integration.handlers import add_task, complete_task
from datetime import datetime
import pytz
import json


async def test():
    print("="*70)
    print("TEST PREMIUM PARTNER PROGRESS NOTIFICATIONS")
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
        
        print("\n[1] Created users")
        
        # Premium creates task -> triggers automation -> partner gets recommendation
        task = Task(user_id=premium.id, title='Find Python dev', description='Need Python developer for AI project', status='pending')
        session.add(task)
        session.commit()
        
        print(f"\n[2] Premium created task: {task.title}")
        
        result = await trigger_premium_automation_realtime(
            premium_user_id=premium.telegram_id,
            task_id=task.id,
            task_description=task.description
        )
        
        print(f"\n[3] Automation: found {result.get('relevant_users_found', 0)} partners, saved {result.get('recommendations_saved', 0)} recommendations")
        
        # Check Premium sees partner notification
        session.refresh(premium_profile)
        premium_prompt_1 = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[4] Premium sees (after finding partner):")
        if premium_prompt_1:
            lines = [line.strip() for line in premium_prompt_1.split('\n') if line.strip() and not line.startswith('=') and not line.startswith('ВАЖНО')]
            for line in lines[2:4]:  # Show first 2 insights
                print(f"  {line}")
        
        # Partner creates task -> Premium should be notified "started"
        print(f"\n[5] Partner creates task...")
        session.refresh(partner_profile)
        
        tz = pytz.timezone(partner.timezone)
        reminder = datetime.now(tz).replace(hour=15, minute=0, second=0, microsecond=0)
        
        partner_task_id = await add_task(
            title='Work on AI platform',
            description='Working on partnering with Premium user on AI platform',
            reminder_time=reminder,
            user_id=partner.telegram_id,
            session=session
        )
        
        print(f"  -> Partner task created: ID {partner_task_id}")
        
        # Check Premium sees "started" notification
        session.refresh(premium_profile)
        premium_prompt_2 = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[6] Premium sees (after partner started):")
        if premium_prompt_2:
            lines = [line.strip() for line in premium_prompt_2.split('\n') if line.strip() and not line.startswith('=') and not line.startswith('ВАЖНО')]
            for line in lines[2:5]:  # Show first 3 insights  
                print(f"  {line}")
        
        # Partner completes task -> Premium should be notified "completed"
        print(f"\n[7] Partner completes task...")
        
        await complete_task(
            task_id=partner_task_id,
            completion_note="Finished working on AI platform features",
            user_id=partner.telegram_id,
            session=session
        )
        
        print(f"  -> Partner task completed")
        
        # Check Premium sees "completed" notification
        session.refresh(premium_profile)
        premium_prompt_3 = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[8] Premium sees (after partner completed):")
        if premium_prompt_3:
            lines = [line.strip() for line in premium_prompt_3.split('\n') if line.strip() and not line.startswith('=') and not line.startswith('ВАЖНО')]
            for line in lines[2:6]:  # Show first 4 insights
                print(f"  {line}")
        
        # Check Premium profile notifications
        print(f"\n[9] Premium profile notifications:")
        if premium_profile.pending_premium_recommendations:
            recs = json.loads(premium_profile.pending_premium_recommendations)
            for rec in recs:
                rec_type = rec.get('type', 'unknown')
                if rec_type == 'partner_found':
                    print(f"  - partner_found: @{rec.get('partner_username')}")
                elif rec_type == 'partner_progress':
                    print(f"  - partner_progress: @{rec.get('partner_username')} {rec.get('action')} '{rec.get('task_title')}'")
        
        print("\n" + "="*70)
        print("RESULT")
        print("="*70)
        
        if premium_profile.pending_premium_recommendations:
            recs = json.loads(premium_profile.pending_premium_recommendations)
            progress_notifications = [r for r in recs if r.get('type') == 'partner_progress']
            
            if len(progress_notifications) >= 2:
                print("\n[SUCCESS] Premium receives full partner lifecycle notifications!")
                print("  1. Found partner notification")
                print("  2. Partner started work notification")
                print("  3. Partner completed work notification")
                print("\nPremium is fully informed about partner progress!")
            else:
                print(f"\n[PARTIAL] Only {len(progress_notifications)} progress notifications")
        else:
            print("\n[FAIL] No notifications found")
    
    finally:
        # Cleanup
        session.query(Task).delete()
        session.query(UserProfile).delete()
        session.query(User).delete()
        session.commit()
        session.close()
        print("\n[OK] Cleaned up\n")


if __name__ == "__main__":
    asyncio.run(test())
