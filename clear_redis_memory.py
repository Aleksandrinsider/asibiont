#!/usr/bin/env python3
"""
Очистка Redis context memory на Railway
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Устанавливаем переменные окружения для Railway
os.environ['REDIS_ENABLED'] = 'True'
os.environ['REDIS_HOST'] = 'containers-us-west-181.railway.app'
os.environ['REDIS_PORT'] = '6379'
os.environ['REDIS_PASSWORD'] = 'your_redis_password_here'  # Нужно заменить на реальный пароль

from ai_integration.autonomous_agent import get_autonomous_agent

def clear_railway_redis_memory():
    """Очищает context_memory в Railway Redis."""
    print("=== ОЧИСТКА RAILWAY REDIS CONTEXT MEMORY ===")

    try:
        agent = get_autonomous_agent()
        agent.clear_context_memory()
        print("✅ Context memory очищена в Railway Redis")
    except Exception as e:
        print(f"❌ Ошибка очистки: {e}")

if __name__ == "__main__":
    clear_railway_redis_memory()