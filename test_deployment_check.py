"""
Comprehensive deployment check for ASI Biont
Tests all critical functionality before deployment
"""
import sys
import os
from datetime import datetime

# Color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def print_test(name, passed, message=""):
    """Print test result with color"""
    status = f"{GREEN}✓ PASS{RESET}" if passed else f"{RED}✗ FAIL{RESET}"
    print(f"{status} | {name}")
    if message:
        print(f"      {message}")

def test_imports():
    """Test that all critical imports work"""
    print("\n=== Testing Imports ===")
    
    try:
        import config
        print_test("Config import", True)
    except Exception as e:
        print_test("Config import", False, str(e))
        return False
    
    try:
        import models
        print_test("Models import", True)
    except Exception as e:
        print_test("Models import", False, str(e))
        return False
    
    try:
        from ai_integration import chat, handlers, prompts, utils
        print_test("AI integration imports", True)
    except Exception as e:
        print_test("AI integration imports", False, str(e))
        return False
    
    try:
        import payments
        print_test("Payments import", True)
    except Exception as e:
        print_test("Payments import", False, str(e))
        return False
    
    return True

def test_config():
    """Test configuration variables"""
    print("\n=== Testing Configuration ===")
    import config
    
    critical_vars = [
        'TELEGRAM_TOKEN',
        'DEEPSEEK_API_KEY',
        'DATABASE_URL',
        'OPENWEATHERMAP_API_KEY',
        'NEWSAPI_API_KEY',
    ]
    
    all_ok = True
    for var in critical_vars:
        value = getattr(config, var, None)
        if value:
            print_test(f"{var} set", True)
        else:
            print_test(f"{var} set", False, "Variable not set or empty")
            all_ok = False
    
    return all_ok

def test_database_connection():
    """Test database connectivity"""
    print("\n=== Testing Database ===")
    
    try:
        from models import Session, User
        session = Session()
        
        # Try a simple query
        count = session.query(User).count()
        session.close()
        print_test("Database connection", True, f"Found {count} users")
        return True
    except Exception as e:
        print_test("Database connection", False, str(e))
        return False

def test_api_endpoints():
    """Test external API availability"""
    print("\n=== Testing External APIs ===")
    import requests
    
    # Test OpenWeatherMap
    try:
        from config import OPENWEATHERMAP_API_KEY
        url = f"http://api.openweathermap.org/data/2.5/weather?q=Moscow&appid={OPENWEATHERMAP_API_KEY}&units=metric"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            print_test("OpenWeatherMap API", True)
        else:
            print_test("OpenWeatherMap API", False, f"Status: {response.status_code}")
    except Exception as e:
        print_test("OpenWeatherMap API", False, str(e))
    
    # Test NewsAPI
    try:
        from config import NEWSAPI_API_KEY
        url = f"https://newsapi.org/v2/everything?q=Россия&language=ru&apiKey={NEWSAPI_API_KEY}&pageSize=1"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            print_test("NewsAPI", True)
        else:
            print_test("NewsAPI", False, f"Status: {response.status_code}")
    except Exception as e:
        print_test("NewsAPI", False, str(e))

def test_tier_descriptions():
    """Test consistency of tier descriptions"""
    print("\n=== Testing Tier Descriptions ===")
    
    # Load handlers.py
    with open('handlers.py', 'r', encoding='utf-8') as f:
        handlers_content = f.read()
    
    # Load subscription_tiers.html
    with open('templates/subscription_tiers.html', 'r', encoding='utf-8') as f:
        tiers_content = f.read()
    
    all_ok = True
    
    # Check Light description (key phrase)
    if "Для нетворкинга" in handlers_content and "Для нетворкинга" in tiers_content:
        if "инициирует знакомства" in handlers_content and "инициирует знакомства" in tiers_content:
            print_test("Light tier description consistency", True)
        else:
            print_test("Light tier description consistency", False, "Key phrases missing")
            all_ok = False
    else:
        print_test("Light tier description consistency", False, "Descriptions don't match")
        all_ok = False
    
    # Check Standard description (key phrase)
    if "для делегирования" in handlers_content and "для делегирования" in tiers_content:
        if "координирует команду" in handlers_content and "координирует команду" in tiers_content:
            print_test("Standard tier description consistency", True)
        else:
            print_test("Standard tier description consistency", False, "Key phrases missing")
            all_ok = False
    else:
        print_test("Standard tier description consistency", False, "Descriptions don't match")
        all_ok = False
    
    # Check Premium description (key phrase with flexible quotes)
    premium_in_handlers = "AI на автопилоте" in handlers_content and "инициирует выполнение" in handlers_content
    premium_in_tiers = "AI на автопилоте" in tiers_content and "инициирует выполнение" in tiers_content
    
    if premium_in_handlers and premium_in_tiers:
        print_test("Premium tier description consistency", True)
    else:
        print_test("Premium tier description consistency", False, "Descriptions don't match")
        all_ok = False
    
    return all_ok

def test_ai_integration():
    """Test AI chat integration"""
    print("\n=== Testing AI Integration ===")
    
    try:
        from ai_integration.chat import chat_with_ai
        from models import Session, User, UserProfile
        
        # Create test session
        session = Session()
        
        # Check if test user exists
        test_user = session.query(User).filter_by(telegram_id=999999999).first()
        
        if test_user:
            print_test("AI chat function available", True, "Test user found")
        else:
            print_test("AI chat function available", True, "Function imported successfully")
        
        session.close()
        return True
    except Exception as e:
        print_test("AI chat function available", False, str(e))
        return False

def test_premium_automation():
    """Test premium automation imports"""
    print("\n=== Testing Premium Automation ===")
    
    try:
        from ai_integration.premium_simple import (
            trigger_premium_automation_realtime,
            get_premium_recommendations_for_prompt,
            get_partner_recommendations_for_prompt
        )
        print_test("Premium automation functions", True)
        return True
    except Exception as e:
        print_test("Premium automation functions", False, str(e))
        return False

def test_file_consistency():
    """Test file consistency and required files"""
    print("\n=== Testing File Consistency ===")
    
    required_files = [
        'main.py',
        'handlers.py',
        'config.py',
        'models.py',
        'payments.py',
        'requirements.txt',
        'Procfile',
        'railway.json',
        '.env',
        'templates/index.html',
        'templates/subscription_tiers.html',
        'ai_integration/chat.py',
        'ai_integration/handlers.py',
        'ai_integration/prompts.py',
        'ai_integration/utils.py',
        'ai_integration/premium_simple.py',
    ]
    
    all_ok = True
    for file in required_files:
        if os.path.exists(file):
            print_test(f"File exists: {file}", True)
        else:
            print_test(f"File exists: {file}", False)
            all_ok = False
    
    return all_ok

def main():
    """Run all tests"""
    print(f"\n{'='*60}")
    print(f"  ASI Biont Deployment Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    results = {
        "Imports": test_imports(),
        "Configuration": test_config(),
        "Database": test_database_connection(),
        "File Consistency": test_file_consistency(),
        "Tier Descriptions": test_tier_descriptions(),
        "AI Integration": test_ai_integration(),
        "Premium Automation": test_premium_automation(),
    }
    
    # Test external APIs (non-blocking)
    test_api_endpoints()
    
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"{status} | {name}")
    
    print(f"\n{passed}/{total} test groups passed")
    
    if passed == total:
        print(f"\n{GREEN}✓ ALL TESTS PASSED - READY FOR DEPLOYMENT{RESET}")
        return 0
    else:
        print(f"\n{RED}✗ SOME TESTS FAILED - FIX BEFORE DEPLOYMENT{RESET}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
