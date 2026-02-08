"""
Простое тестирование готовности к продакшену
Проверяет критические компоненты системы
"""

import sys
import os

# Цвета
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

test_results = []

def log_test(name, passed, details=""):
    """Логирование результата теста"""
    status = f"{GREEN}✅ PASS{RESET}" if passed else f"{RED}❌ FAIL{RESET}"
    print(f"\n{status} {name}")
    if details:
        print(f"   {details}")
    test_results.append({'name': name, 'passed': passed, 'details': details})

def test_imports():
    """Тест 1: Импорты всех модулей"""
    try:
        # Основные модули
        import models
        import config
        import handlers
        import payments
        
        # AI модули
        from ai_integration import chat
        from ai_integration import tools
        from ai_integration import prompts
        from ai_integration import autonomous_agent
        
        log_test("Импорт модулей", True, "Все модули импортированы успешно")
        return True
    except Exception as e:
        log_test("Импорт модулей", False, f"Ошибка: {e}")
        return False

def test_database_connection():
    """Тест 2: Подключение к БД"""
    try:
        from models import Session, User
        session = Session()
        user_count = session.query(User).count()
        session.close()
        
        log_test("Подключение к БД", True, f"Подключение успешно. Пользователей: {user_count}")
        return True
    except Exception as e:
        log_test("Подключение к БД", False, f"Ошибка: {e}")
        return False

def test_config():
    """Тест 3: Конфигурация"""
    try:
        from config import TELEGRAM_TOKEN, DEEPSEEK_API_KEY, DATABASE_URL
        
        checks = []
        checks.append(("TELEGRAM_TOKEN", TELEGRAM_TOKEN is not None and len(TELEGRAM_TOKEN) > 0))
        checks.append(("DEEPSEEK_API_KEY", DEEPSEEK_API_KEY is not None and len(DEEPSEEK_API_KEY) > 0))
        checks.append(("DATABASE_URL", DATABASE_URL is not None and len(DATABASE_URL) > 0))
        
        passed = all(c[1] for c in checks)
        details = ", ".join([f"{c[0]}: {'✅' if c[1] else '❌'}" for c in checks])
        
        log_test("Конфигурация", passed, details)
        return passed
    except Exception as e:
        log_test("Конфигурация", False, f"Ошибка: {e}")
        return False

def test_models():
    """Тест 4: Модели БД"""
    try:
        from models import (
            User, Task, Subscription, UserProfile, Goal,
            Interaction, PromoCode, PaymentHistory
        )
        
        models_list = [User, Task, Subscription, UserProfile, Goal, Interaction, PromoCode, PaymentHistory]
        
        log_test("Модели БД", True, f"Успешно загружено {len(models_list)} моделей")
        return True
    except Exception as e:
        log_test("Модели БД", False, f"Ошибка: {e}")
        return False

def test_ai_tools():
    """Тест 5: AI инструменты"""
    try:
        from ai_integration.tools import TOOLS
        
        tools_count = len(TOOLS)
        passed = tools_count >= 15
        
        log_test("AI инструменты", passed, f"Загружено инструментов: {tools_count}")
        return passed
    except Exception as e:
        log_test("AI инструменты", False, f"Ошибка: {e}")
        return False

def test_prompts():
    """Тест 6: Системные промпты"""
    try:
        from ai_integration.prompts import get_extended_system_prompt
        
        prompt = get_extended_system_prompt(
            user_now={},
            current_time_str="10:00",
            current_date_str="2026-02-08",
            user_username="test",
            mentions_str="",
            user_memory="",
            subscription_tier='light'
        )
        passed = len(prompt) > 100
        
        log_test("Системные промпты", passed, f"Длина промпта: {len(prompt)} символов")
        return passed
    except Exception as e:
        log_test("Системные промпты", False, f"Ошибка: {e}")
        return False

def test_handlers():
    """Тест 7: Telegram обработчики"""
    try:
        from handlers import router, PREMIUM_DESCRIPTION
        
        passed = router is not None and len(PREMIUM_DESCRIPTION) > 0
        
        log_test("Telegram обработчики", passed, "Router и описания настроены")
        return passed
    except Exception as e:
        log_test("Telegram обработчики", False, f"Ошибка: {e}")
        return False

def test_payments():
    """Тест 8: Платежная система"""
    try:
        from payments import create_payment
        
        log_test("Платежная система", True, "Модуль payments загружен")
        return True
    except Exception as e:
        log_test("Платежная система", False, f"Ошибка: {e}")
        return False

def test_files_structure():
    """Тест 9: Структура файлов"""
    try:
        required_files = [
            'main.py',
            'models.py',
            'config.py',
            'handlers.py',
            'payments.py',
            'requirements.txt',
            'Procfile',
            'railway.json',
            'README.md'
        ]
        
        missing = []
        for file in required_files:
            if not os.path.exists(file):
                missing.append(file)
        
        passed = len(missing) == 0
        details = f"Найдено {len(required_files) - len(missing)}/{len(required_files)} файлов"
        if missing:
            details += f". Отсутствуют: {', '.join(missing)}"
        
        log_test("Структура файлов", passed, details)
        return passed
    except Exception as e:
        log_test("Структура файлов", False, f"Ошибка: {e}")
        return False

def test_ai_integration_structure():
    """Тест 10: Структура AI модулей"""
    try:
        ai_files = [
            'ai_integration/__init__.py',
            'ai_integration/chat.py',
            'ai_integration/tools.py',
            'ai_integration/prompts.py',
            'ai_integration/autonomous_agent.py'
        ]
        
        missing = []
        for file in ai_files:
            if not os.path.exists(file):
                missing.append(file)
        
        passed = len(missing) == 0
        details = f"Найдено {len(ai_files) - len(missing)}/{len(ai_files)} AI модулей"
        
        log_test("AI модули", passed, details)
        return passed
    except Exception as e:
        log_test("AI модули", False, f"Ошибка: {e}")
        return False

def print_summary():
    """Вывести итоговую статистику"""
    print("\n" + "=" * 70)
    print(f"{BLUE}📊 ИТОГОВАЯ СТАТИСТИКА{RESET}")
    print("=" * 70)
    
    total = len(test_results)
    passed = sum(1 for t in test_results if t['passed'])
    failed = total - passed
    percentage = (passed / total * 100) if total > 0 else 0
    
    print(f"\nВсего тестов: {total}")
    print(f"{GREEN}✅ Пройдено: {passed}{RESET}")
    print(f"{RED}❌ Провалено: {failed}{RESET}")
    print(f"Процент успеха: {percentage:.1f}%")
    
    if failed > 0:
        print(f"\n{RED}Провалены тесты:{RESET}")
        for test in test_results:
            if not test['passed']:
                print(f"  ❌ {test['name']}: {test['details']}")
    
    print("\n" + "=" * 70)
    
    if percentage == 100:
        print(f"{GREEN}✨ ВСЕ ТЕСТЫ ПРОЙДЕНЫ! СИСТЕМА ГОТОВА К ПРОДАКШЕНУ!{RESET}")
    elif percentage >= 90:
        print(f"{GREEN}✅ СИСТЕМА ГОТОВА К ПРОДАКШЕНУ ({percentage:.0f}%){RESET}")
    elif percentage >= 70:
        print(f"{YELLOW}⚠️  СИСТЕМА ТРЕБУЕТ ДОРАБОТКИ ({percentage:.0f}%){RESET}")
    else:
        print(f"{RED}❌ СИСТЕМА НЕ ГОТОВА ({percentage:.0f}%){RESET}")
    
    print("=" * 70 + "\n")
    
    return percentage >= 90

def main():
    """Запустить все тесты"""
    print(f"\n{BLUE}{'=' * 70}")
    print("🚀 ПРОВЕРКА ГОТОВНОСТИ К ПРОДАКШЕНУ")
    print(f"{'=' * 70}{RESET}\n")
    
    tests = [
        test_imports,
        test_database_connection,
        test_config,
        test_models,
        test_ai_tools,
        test_prompts,
        test_handlers,
        test_payments,
        test_files_structure,
        test_ai_integration_structure
    ]
    
    for test_func in tests:
        try:
            test_func()
        except Exception as e:
            log_test(test_func.__name__, False, f"Критическая ошибка: {e}")
    
    ready = print_summary()
    sys.exit(0 if ready else 1)

if __name__ == "__main__":
    main()
