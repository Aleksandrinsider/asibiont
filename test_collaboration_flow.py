"""Test full collaboration flow: Premium finds partners, partners see opportunities"""
import asyncio
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task, SubscriptionTier, Base, engine
from ai_integration.premium_simple import (
    trigger_premium_automation_realtime, 
    get_premium_recommendations_for_prompt,
    get_partner_recommendations_for_prompt
)
from ai_integration.handlers import add_task, complete_task
from datetime import datetime
import pytz
import json


async def test():
    print("="*70)
    print("TEST COLLABORATION FLOW: WIN-WIN")
    print("="*70)
    
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Clean
        session.query(Task).filter(Task.user_id.in_(session.query(User.id).filter(User.telegram_id.in_([111111, 222222])))).delete(synchronize_session=False)
        session.query(UserProfile).filter(UserProfile.user_id.in_(session.query(User.id).filter(User.telegram_id.in_([111111, 222222])))).delete(synchronize_session=False)
        session.query(User).filter(User.telegram_id.in_([111111, 222222])).delete()
        session.commit()
        
        # Create Premium user
        premium = User(telegram_id=111111, username='premium_ivan', first_name='Ivan', subscription_tier=SubscriptionTier.PREMIUM, timezone='Europe/Moscow')
        session.add(premium)
        session.commit()
        
        premium_profile = UserProfile(
            user_id=premium.id, 
            interests='AI, бизнес, стартапы', 
            skills='маркетинг, продажи, менеджмент',
            goals='Создать AI платформу для автоматизации задач',
            city='Moscow'
        )
        session.add(premium_profile)
        session.commit()
        
        # Create partner (Light user)  
        partner = User(telegram_id=222222, username='developer_maria', first_name='Maria', subscription_tier=SubscriptionTier.LIGHT, timezone='Europe/Moscow')
        session.add(partner)
        session.commit()
        
        partner_profile = UserProfile(
            user_id=partner.id, 
            interests='AI, Python, machine learning', 
            skills='Python, FastAPI, PostgreSQL, ML',
            goals='Развиваться в AI направлении',
            city='Moscow'
        )
        session.add(partner_profile)
        session.commit()
        
        print("\n[1] Created users:")
        print(f"  Premium: @{premium.username} (interests: {premium_profile.interests}, skills: {premium_profile.skills})")
        print(f"  Partner: @{partner.username} (interests: {partner_profile.interests}, skills: {partner_profile.skills})")
        
        # Premium creates task
        task = Task(
            user_id=premium.id, 
            title='Find Python developer', 
            description='Need Python developer for AI task automation platform', 
            status='pending'
        )
        session.add(task)
        session.commit()
        
        print(f"\n[2] Premium created task: '{task.title}'")
        
        # Trigger automation
        result = await trigger_premium_automation_realtime(
            premium_user_id=premium.telegram_id,
            task_id=task.id,
            task_description=task.description
        )
        
        print(f"\n[3] Automation result:")
        print(f"  Found partners: {result.get('relevant_users_found', 0)}")
        print(f"  Saved recommendations: {result.get('recommendations_saved', 0)}")
        
        # Check what Premium sees
        session.refresh(premium_profile)
        premium_prompt = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[4] What Premium sees in chat:")
        if premium_prompt:
            lines = [l.strip() for l in premium_prompt.split('\n') if l.strip() and not l.startswith('=') and not l.startswith('ВАЖНО') and not l.startswith('🔴') and not l.startswith('🟡')]
            for line in lines[:3]:
                print(f"  {line}")
        else:
            print("  ❌ Nothing")
        
        # Check what Partner sees (ВАЖНО!)
        session.refresh(partner_profile)
        partner_prompt = get_partner_recommendations_for_prompt(partner.telegram_id, session)
        print(f"\n[5] What Partner sees in chat (коллаборация!):")
        if partner_prompt:
            lines = [l.strip() for l in partner_prompt.split('\n') if l.strip() and not l.startswith('=')]
            for line in lines[:8]:  # Show more details
                print(f"  {line}")
        else:
            print("  ❌ Nothing")
        
        # Check partner's recommendations data
        print(f"\n[6] Partner's recommendation data:")
        if partner_profile.pending_premium_recommendations:
            recs = json.loads(partner_profile.pending_premium_recommendations)
            for rec in recs:
                if rec.get('type') == 'task_created':
                    print(f"  Goal: {rec.get('goal', 'N/A')}")
                    print(f"  Match: {rec.get('match_reason', 'N/A')}")
                    print(f"  Premium user: @{rec.get('premium_username', 'N/A')}")
                    print(f"  Premium interests: {rec.get('premium_interests', 'N/A')}")
                    print(f"  Premium skills: {rec.get('premium_skills', 'N/A')}")
        
        # Partner creates task
        print(f"\n[7] Partner creates task...")
        tz = pytz.timezone(partner.timezone)
        reminder = datetime.now(tz).replace(hour=15, minute=0, second=0, microsecond=0)
        
        partner_task_id = await add_task(
            title='Work on AI automation',
            description='Building AI automation platform',
            reminder_time=reminder,
            user_id=partner.telegram_id,
            session=session
        )
        print(f"  Partner task created")
        
        # Check Premium sees partner started
        session.refresh(premium_profile)
        premium_prompt_2 = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[8] Premium sees partner progress:")
        if premium_prompt_2:
            lines = [l.strip() for l in premium_prompt_2.split('\n') if l.strip() and '@developer_maria' in l]
            for line in lines:
                print(f"  {line}")
        
        # Partner completes task
        print(f"\n[9] Partner completes task...")
        await complete_task(
            task_id=partner_task_id,
            completion_note="Finished automation features",
            user_id=partner.telegram_id,
            session=session
        )
        print(f"  Task completed ✓")
        
        # Check Premium sees completion
        session.refresh(premium_profile)
        premium_prompt_3 = get_premium_recommendations_for_prompt(premium.telegram_id, session)
        print(f"\n[10] Premium sees completion:")
        if premium_prompt_3:
            lines = [l.strip() for l in premium_prompt_3.split('\n') if l.strip() and ('завершил' in l.lower() or 'completed' in l.lower())]
            for line in lines:
                print(f"  {line}")
        
        print("\n" + "="*70)
        print("РЕЗУЛЬТАТ")
        print("="*70)
        
        success_checks = []
        
        # Check 1: Premium sees partner found
        if premium_prompt and ('партнёр' in premium_prompt.lower() or 'partner' in premium_prompt.lower()):
            print("\n✅ Premium видит найденного партнёра")
            success_checks.append(True)
        else:
            print("\n❌ Premium НЕ видит партнёра")
            success_checks.append(False)
        
        # Check 2: Partner sees collaboration
        if partner_prompt and 'коллаборация' in partner_prompt.lower():
            print("✅ Partner видит приглашение к коллаборации")
            success_checks.append(True)
        else:
            print("❌ Partner НЕ видит приглашение")
            success_checks.append(False)
        
        # Check 3: Partner sees Premium context
        if partner_prompt and '@premium_ivan' in partner_prompt:
            print("✅ Partner видит контекст Premium (username, навыки)")
            success_checks.append(True)
        else:
            print("❌ Partner НЕ видит контекст Premium")
            success_checks.append(False)
        
        # Check 4: Premium sees partner progress
        if premium_prompt_2 and 'начал' in premium_prompt_2.lower():
            print("✅ Premium видит когда партнёр начал работу")
            success_checks.append(True)
        else:
            print("❌ Premium НЕ видит начало работы")
            success_checks.append(False)
        
        # Check 5: Premium sees completion
        if premium_prompt_3 and 'завершил' in premium_prompt_3.lower():
            print("✅ Premium видит когда партнёр завершил")
            success_checks.append(True)
        else:
            print("❌ Premium НЕ видит завершение")
            success_checks.append(False)
        
        if all(success_checks):
            print("\n🎉 ПОЛНЫЙ УСПЕХ! Коллаборации работают win-win!")
            print("   - Premium: видит найденных партнёров, отслеживает прогресс")
            print("   - Partner: видит приглашение, понимает контекст, может откликнуться")
        else:
            passed = sum(success_checks)
            total = len(success_checks)
            print(f"\n⚠️ Частичный успех: {passed}/{total} проверок пройдено")
    
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
