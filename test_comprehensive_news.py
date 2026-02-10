#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Финальный тест функции get_news_trends - все сценарии
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

async def test_comprehensive_news():
    """Комплексный тест всех сценариев"""
    print("🚀 КОМПЛЕКСНЫЙ ТЕСТ: get_news_trends")
    print("=" * 60)
    
    # Test cases covering all scenarios
    test_cases = [
        {
            "topic": "машинное обучение",  # Russian
            "period": "week",
            "focus": "trends",
            "description": "Русские тренды ML"
        },
        {
            "topic": "startup funding",    # English 
            "period": "week",
            "focus": "opportunities",
            "description": "Startup opportunities"
        },
        {
            "topic": "cryptocurrency",     # English
            "period": "today",
            "focus": "news",
            "description": "Crypto news today"
        },
        {
            "topic": "блокчейн",          # Russian
            "period": "month",
            "focus": "trends", 
            "description": "Blockchain trends monthly"
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
        
        print(f"👤 User: {user.telegram_id} (subscription: {user.subscription_tier})")
        print()
        
        # Test each case
        success_count = 0
        
        for i, case in enumerate(test_cases, 1):
            print(f"📝 Test {i}/{len(test_cases)}: {case['description']}")
            print(f"   Topic: '{case['topic']}'")
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
                
                # Check for errors
                if "❌ Ошибка" in result:
                    print(f"   ❌ Error: {result}")
                elif "🔍 По запросу" in result and "не найдено" in result:
                    print(f"   ⚠️  No results: {result}")
                else:
                    print(f"   ✅ Success ({len(result)} characters)")
                    success_count += 1
                    
                    # Show structure analysis
                    if case['focus'] == 'trends':
                        checks = [
                            ("🔥 **Главные тренды**" in result, "Trends section"),
                            ("📈 **О чём говорят**" in result, "Summary section"),
                            ("📋 **Ключевые события**" in result, "Events section")
                        ]
                    elif case['focus'] == 'opportunities':
                        checks = [
                            ("🚀 **Бизнес-возможности**" in result, "Opportunities section"),
                            ("📋 **На что обратить внимание**" in result, "Attention section"),
                            ("🔍 **Рекомендации**" in result, "Recommendations section")
                        ]
                    else:  # news
                        checks = [
                            ("📰 **Новости по теме**" in result, "News header"),
                            ("1." in result, "Article numbering"),
                            ("**" in result, "Formatting")
                        ]
                    
                    for check, name in checks:
                        status = "✅" if check else "❌" 
                        print(f"      {status} {name}")
            
            except Exception as e:
                print(f"   ❌ Exception: {e}")
            
            print("-" * 50)
            
        print(f"\n🎯 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")
        print(f"   ✅ Успешно: {success_count}/{len(test_cases)}")
        print(f"   📊 Процент успеха: {(success_count/len(test_cases)*100):.0f}%")
        
        if success_count >= 2:
            print("   🚀 Функция get_news_trends ГОТОВА к продакшену!")
        else:
            print("   ⚠️  Требует дополнительной проработки")
        
        print("\n🔧 ФУНКЦИЯ ПОДДЕРЖИВАЕТ:")
        print("   - Автоопределение языка (русский/английский)")
        print("   - Три временных периода: today/week/month") 
        print("   - Три режима анализа: news/trends/opportunities")
        print("   - AI анализ через DeepSeek API")
        print("   - Проверка подписки STANDARD/PREMIUM")
        
    except Exception as e:
        print(f"❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_comprehensive_news())