"""Тест проверки инициализации REMINDER_SERVICE"""

import sys
import os

# Настройки для локального тестирования
os.environ["LOCAL"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///./local.db"

# Проверка инициализации REMINDER_SERVICE
def test_reminder_service_init():
    """Проверка что REMINDER_SERVICE инициализируется правильно"""
    
    # Импортируем main чтобы инициализировать сервис
    import main
    from reminder_service import REMINDER_SERVICE
    
    print(f"\n📋 Проверка инициализации REMINDER_SERVICE:")
    print(f"   REMINDER_SERVICE = {REMINDER_SERVICE}")
    print(f"   Type: {type(REMINDER_SERVICE)}")
    
    if REMINDER_SERVICE:
        print(f"   ✅ REMINDER_SERVICE инициализирован")
        print(f"   Scheduler: {REMINDER_SERVICE.scheduler}")
        print(f"   Scheduler running: {REMINDER_SERVICE.scheduler.running if REMINDER_SERVICE.scheduler else 'N/A'}")
    else:
        print(f"   ❌ REMINDER_SERVICE не инициализирован!")
        return False
    
    return True

if __name__ == "__main__":
    success = test_reminder_service_init()
    sys.exit(0 if success else 1)
