"""
Test proactive agent behavior with realistic scenario
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import Session, User, UserProfile, Task, Base, engine
from ai_integration.chat import chat_with_ai

async def test_proactive():
    """Test agent's proactive behavior with overdue and upcoming tasks"""
    
    print("\n" + "="*60)
    print("TEST: Proactive Agent Behavior")
    print("="*60 + "\n")
    
    # Setup
    user_id = 999888777
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Clean up
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            session.query(Task).filter_by(user_id=user.id).delete()
            session.query(UserProfile).filter_by(user_id=user.id).delete()
            session.delete(user)
            session.commit()
        
        # Create test user with interests
        user = User(
            telegram_id=user_id,
            username='proactive_test',
            first_name='ПроактивныйТест',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        profile = UserProfile(
            user_id=user.id,
            interests='спорт, программирование, чтение, здоровье',
            goals='улучшить физическую форму, изучить новые технологии, развить навыки коммуникации',
            city='Москва'
        )
        session.add(profile)
        session.commit()
        
        # Create tasks
        tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(tz)
        
        # 1. Overdue task (should trigger reschedule suggestion)
        overdue_task = Task(
            user_id=user.id,
            title='Проверить электронную почту',
            status='pending',
            reminder_time=now - timedelta(hours=1)
        )
        session.add(overdue_task)
        
        # 2. Social activity task (should trigger find_relevant_contacts_for_task)
        social_task = Task(
            user_id=user.id,
            title='Пробежка в парке',
            status='pending',
            reminder_time=now + timedelta(hours=2)
        )
        session.add(social_task)
        
        # 3. Learning task
        learning_task = Task(
            user_id=user.id,
            title='Изучить новый фреймворк',
            status='pending',
            reminder_time=now + timedelta(hours=4)
        )
        session.add(learning_task)
        
        session.commit()
        print("OK: Created test user with 3 tasks:")
        print(f"   - Overdue: '{overdue_task.title}' (-1 hour)")
        print(f"   - Social: '{social_task.title}' (+2 hours)")
        print(f"   - Learning: '{learning_task.title}' (+4 hours)")
        print()
        
        # Test proactive response
        print("Request: 'pokazi moi zadachi'\n")
        
        response = await chat_with_ai(
            'покажи мои задачи',
            user_id=user_id,
            db_session=session
        )
        
        response_text = response.get('response', '')
        print("AGENT RESPONSE:")
        print("-" * 60)
        print(response_text)
        print("-" * 60)
        print()
        
        # Analyze proactive indicators
        print("\n" + "="*60)
        print("PROACTIVE ANALYSIS")
        print("="*60 + "\n")
        
        proactive_indicators = []
        passive_indicators = []
        
        # Check for overdue task mention (should suggest, not auto-reschedule)
        has_overdue_mention = 'просроч' in response_text.lower() or 'перенес' in response_text.lower()
        if has_overdue_mention:
            proactive_indicators.append("OK: Overdue task mentioned")
        else:
            passive_indicators.append("NO: Overdue task not mentioned")
        
        # Check for partner actions (automatic find_relevant_contacts_for_task)
        has_partner_found = '@' in response_text or 'нашел' in response_text.lower() or 'нашла' in response_text.lower() or 'партнер' in response_text.lower()
        if has_partner_found:
            proactive_indicators.append("OK: Partners found/mentioned")
        else:
            passive_indicators.append("NO: No partners found")
        
        # Check for action-oriented language
        action_words = ['давай', 'сразу', 'предлагаю', 'рекомендую', 'можем']
        has_action = any(word in response_text.lower() for word in action_words)
        if has_action:
            proactive_indicators.append("OK: Action-oriented language")
        
        # Check for passive questions
        passive_questions = ['хочешь?', 'может быть?', 'интересно?', 'хотел бы?']
        has_passive = any(q in response_text.lower() for q in passive_questions)
        if has_passive:
            passive_indicators.append("⚠️ Пассивные вопросы обнаружены")
        
        # Results
        print("PROACTIVE INDICATORS:")
        if proactive_indicators:
            for indicator in proactive_indicators:
                print(f"   {indicator}")
        else:
            print("   NOT FOUND")
        
        print("\nPASSIVE INDICATORS:")
        if passive_indicators:
            for indicator in passive_indicators:
                print(f"   {indicator}")
        else:
            print("   NONE")
        
        print()
        
        # Verdict
        proactive_score = len(proactive_indicators)
        passive_score = len(passive_indicators)
        
        print("="*60)
        print("RESULT:")
        print(f"   Proactive: {proactive_score}/2")
        print(f"   Passive: {passive_score}")
        
        if proactive_score >= 2 and passive_score <= 1:
            print("\n   PASS: Agent is proactive")
            success = True
        elif proactive_score >= 1:
            print("\n   PARTIAL: Agent moderately proactive")
            success = False
        else:
            print("\n   FAIL: Agent passive, only asks questions")
            success = False
        
        print("="*60 + "\n")
        
        return success
        
    finally:
        session.close()

if __name__ == "__main__":
    result = asyncio.run(test_proactive())
    sys.exit(0 if result else 1)
