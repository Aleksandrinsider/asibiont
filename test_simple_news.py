#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тест функции get_news_trends с отладкой
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
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_simple_news():
    """Простой тест с рабочими темами"""
    print("ТЕСТ: get_news_trends (Simple Debug)")
    print("=" * 60)
    
    # Working test cases based on debug results
    test_cases = [
        {
            "topic": "technology",  # We know this works from debug
            "period": "week",
            "focus": "trends",
            "description": "Technology trends"
        },
        {
            "topic": "AI",  # We know this works from debug
            "period": "today",
            "focus": "news",
            "description": "AI news today"
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
            print()
            
            try:
                result = await get_news_trends(
                    topic=case['topic'],
                    period=case['period'],
                    focus=case['focus'],
                    user_id=user.telegram_id,
                    session=session
                )
                
                print(f"✅ Result received ({len(result)} characters)")
                
                # Show full result for debugging
                print("FULL RESULT:")
                print("-" * 40)
                print(result)
                print("-" * 40)
                
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()
            
            print("=" * 60)
            print()
        
        print("SIMPLE TESTING COMPLETED!")
        
    except Exception as e:
        print(f"❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_simple_news())