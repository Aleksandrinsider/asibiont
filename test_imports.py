"""Проверка импортов и основных функций"""
import sys
import os

os.environ['LOCAL'] = '1'

print("🔍 ПРОВЕРКА ИМПОРТОВ И МОДУЛЕЙ\n" + "="*60)

# 1. Базовые модули
print("\n[1] Базовые модули...")
try:
    from models import Session, User, UserProfile, Task, Base, engine
    print("  ✅ models")
except Exception as e:
    print(f"  ❌ models: {e}")

try:
    from config import DATABASE_URL, DEEPSEEK_API_KEY
    print("  ✅ config")
except Exception as e:
    print(f"  ❌ config: {e}")

# 2. AI модули
print("\n[2] AI интеграция...")
try:
    from ai_integration.chat import chat_with_ai
    print("  ✅ ai_integration.chat")
except Exception as e:
    print(f"  ❌ ai_integration.chat: {e}")

try:
    from ai_integration.autonomous_agent import HybridAutonomousAgent
    print("  ✅ ai_integration.autonomous_agent")
except Exception as e:
    print(f"  ❌ ai_integration.autonomous_agent: {e}")

try:
    from ai_integration.tools import get_all_tools
    print("  ✅ ai_integration.tools")
except Exception as e:
    print(f"  ❌ ai_integration.tools: {e}")

try:
    from ai_integration.dynamic_tools import get_tools_for_context
    print("  ✅ ai_integration.dynamic_tools")
except Exception as e:
    print(f"  ❌ ai_integration.dynamic_tools: {e}")

try:
    from ai_integration.prompts import get_extended_system_prompt
    print("  ✅ ai_integration.prompts")
except Exception as e:
    print(f"  ❌ ai_integration.prompts: {e}")

try:
    from ai_integration.utils import clean_technical_details, encrypt_data, decrypt_data
    print("  ✅ ai_integration.utils")
except Exception as e:
    print(f"  ❌ ai_integration.utils: {e}")

try:
    from ai_integration.time_parser import parse_datetime_with_ai
    print("  ✅ ai_integration.time_parser")
except Exception as e:
    print(f"  ❌ ai_integration.time_parser: {e}")

try:
    from ai_integration.memory import update_user_memory
    print("  ✅ ai_integration.memory")
except Exception as e:
    print(f"  ❌ ai_integration.memory: {e}")

# 3. Handlers
print("\n[3] Хендлеры...")
try:
    from ai_integration.handlers import (
        add_task_handler,
        list_tasks_handler,
        complete_task_handler,
        edit_task_handler,
        delete_task_handler,
        reschedule_task_handler
    )
    print("  ✅ ai_integration.handlers (основные)")
except Exception as e:
    print(f"  ❌ ai_integration.handlers: {e}")

# 4. Сервисы
print("\n[4] Сервисы...")
try:
    from reminder_service import ReminderService
    print("  ✅ reminder_service")
except Exception as e:
    print(f"  ❌ reminder_service: {e}")

try:
    from subscription_service import SubscriptionService
    print("  ✅ subscription_service")
except Exception as e:
    print(f"  ❌ subscription_service: {e}")

# 5. Проверка функциональности
print("\n[5] Быстрая проверка функциональности...")
try:
    Base.metadata.create_all(engine)
    session = Session()
    user_count = session.query(User).count()
    task_count = session.query(Task).count()
    session.close()
    print(f"  ✅ БД работает: {user_count} пользователей, {task_count} задач")
except Exception as e:
    print(f"  ❌ БД: {e}")

try:
    tools = get_all_tools()
    print(f"  ✅ Инструменты: {len(tools)} доступно")
except Exception as e:
    print(f"  ❌ Инструменты: {e}")

try:
    test_text = "Привет! <｜DSML｜>technical data<｜end｜>"
    cleaned = clean_technical_details(test_text)
    if "<｜DSML｜>" not in cleaned:
        print("  ✅ Очистка DSML работает")
    else:
        print("  ❌ Очистка DSML НЕ работает")
except Exception as e:
    print(f"  ❌ Очистка DSML: {e}")

print("\n" + "="*60)
print("✅ ПРОВЕРКА ЗАВЕРШЕНА\n")
