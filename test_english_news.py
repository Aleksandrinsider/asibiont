#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест функции get_news_trends с английскими темами
"""

import asyncio
import sys
import os
import logging

# Add path
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.handlers import get_news_trends
from models import Session, User

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_english_news():
    """Тестирует новости на английском языке"""
    print("ТЕСТ: get_news_trends (English)")
    print("=" * 50)
    
    # English test cases
    test_cases = [
        {
            "topic": "artificial intelligence startups",
            "period": "week",
            "focus": "opportunities",
            "description": "AI startup opportunities"
        },
        {
            "topic": "fintech trends",
            "period": "today", 
            "focus": "trends",
            "description": "Fintech trends today"
        }
    ]
    
    session = Session()
    
    try:
        # Find user with STANDARD/PREMIUM subscription
        user = session.query(User).filter(
            User.subscription_tier.in_(['STANDARD', 'PREMIUM'])
        ).first()
        
        if not user:
            print("❌ No users with STANDARD/PREMIUM subscription found")
            return
        
        print(f"User: {user.telegram_id} (subscription: {user.subscription_tier})")
        print()
        
        # Test each case
        for i, case in enumerate(test_cases, 1):
            print(f"Test {i}/{len(test_cases)}: {case['description']}")
            print(f"   Topic: {case['topic']}")
            print(f"   Period: {case['period']}")
            print(f"   Focus: {case['focus']}")
            
            try:
                result = await get_news_trends(
                    topic=case['topic'],
                    period=case['period'],
                    focus=case['focus'],
                    user_id=user.telegram_id,
                    session=session
                )
                
                print(f"✅ Result received ({len(result)} characters)")
                
                # Show preview
                preview = result[:400] 
                if len(result) > 400:
                    preview += "..."
                print(f"Preview: {preview}")
                
            except Exception as e:
                print(f"❌ Error: {e}")
            
            print("-" * 40)
            print()
        
        print("ENGLISH TESTING COMPLETED!")
        
    except Exception as e:
        print(f"❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_english_news())