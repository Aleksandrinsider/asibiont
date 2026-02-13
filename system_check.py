#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comprehensive system check
"""

import sys
import asyncio

def test_imports():
    """Test all critical imports"""
    print("\n🔍 Testing imports...")
    
    try:
        from models import User, UserProfile, Task, Session
        print("  ✅ Models")
        
        from auto_marketing_service import AutoMarketingService, start_marketing_service
        print("  ✅ Auto Marketing Service")
        
        from contact_alerts_service import ContactAlertsService, start_contact_alerts_service
        print("  ✅ Contact Alerts Service")
        
        from ai_integration import (
            set_auto_post_time, get_weather_info, get_news_trends,
            chat_with_ai, add_task, list_tasks, find_partners
        )
        print("  ✅ AI Integration Functions")
        
        from ai_integration.tools import TOOLS, get_available_tools
        print("  ✅ AI Tools")
        
        from ai_integration.marketing_agent import generate_marketing_content, research_topic
        print("  ✅ Marketing Agent")
        
        return True
    except Exception as e:
        print(f"  ❌ Import error: {e}")
        return False


def test_tools():
    """Test tools registration"""
    print("\n🔧 Testing tools registration...")
    
    try:
        from ai_integration.tools import TOOLS
        
        tools_names = [t['function']['name'] for t in TOOLS]
        
        required_tools = [
            'add_task', 'list_tasks', 'complete_task',
            'find_partners', 'delegate_task',
            'set_auto_post_time', 'get_weather_info', 'get_news_trends',
            'research_topic', 'generate_marketing_content'
        ]
        
        missing = []
        for tool in required_tools:
            if tool not in tools_names:
                missing.append(tool)
        
        if missing:
            print(f"  ❌ Missing tools: {', '.join(missing)}")
            return False
        else:
            print(f"  ✅ All {len(required_tools)} required tools registered")
            print(f"  ℹ️  Total tools: {len(tools_names)}")
            return True
            
    except Exception as e:
        print(f"  ❌ Tools error: {e}")
        return False


def test_db_model():
    """Test database model"""
    print("\n💾 Testing database model...")
    
    try:
        from models import UserProfile
        
        # Check that auto_post_time field exists
        if hasattr(UserProfile, 'auto_post_time'):
            print("  ✅ UserProfile.auto_post_time field exists")
        else:
            print("  ❌ UserProfile.auto_post_time field missing")
            return False
        
        return True
    except Exception as e:
        print(f"  ❌ Model error: {e}")
        return False


async def test_services():
    """Test services initialization"""
    print("\n🚀 Testing services...")
    
    try:
        from auto_marketing_service import AutoMarketingService
        service = AutoMarketingService(bot=None, check_interval_minutes=30)
        print("  ✅ Auto Marketing Service initialized")
        
        from contact_alerts_service import ContactAlertsService
        alerts_service = ContactAlertsService(bot=None, check_interval_minutes=30)
        print("  ✅ Contact Alerts Service initialized")
        
        return True
    except Exception as e:
        print(f"  ❌ Service error: {e}")
        return False


def main():
    print("="*60)
    print("🔍 SYSTEM HEALTH CHECK")
    print("="*60)
    
    results = []
    
    # Test imports
    results.append(("Imports", test_imports()))
    
    # Test tools
    results.append(("Tools", test_tools()))
    
    # Test DB model
    results.append(("Database Model", test_db_model()))
    
    # Test services
    try:
        results.append(("Services", asyncio.run(test_services())))
    except Exception as e:
        print(f"  ❌ Async error: {e}")
        results.append(("Services", False))
    
    # Summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    
    all_passed = True
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {name}")
        if not result:
            all_passed = False
    
    print("="*60)
    
    if all_passed:
        print("\n🎉 ALL CHECKS PASSED! System is ready.\n")
        return 0
    else:
        print("\n⚠️  SOME CHECKS FAILED. Please review errors above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
