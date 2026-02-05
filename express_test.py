#!/usr/bin/env python3
"""
Экспресс-тест - быстрая проверка всех ключевых функций
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import UserProfile, init_db, get_session
from config import TEST_USER_ID
import logging

logging.getLogger().setLevel(logging.CRITICAL)

async def express_test():
    """Экспресс-тест всех функций"""
    print("🚀 ЭКСПРЕСС-ТЕСТ AI-АГЕНТА")
    print("=" * 30)

    await init_db()
    session = get_session()

    # Настройка профиля
    session.query(UserProfile).filter_by(user_id=TEST_USER_ID).delete()
    profile = UserProfile(user_id=TEST_USER_ID, interaction_count=0)
    session.add(profile)
    session.commit()
    session.close()

    # Тесты
    test_cases = [
        ("Привет!", "👋"),
        ("Что ты умеешь?", "🛠️"),
        ("Создай задачу на завтра", "📝"),
        ("Готово", "✅"),
    ]

    results = []
    for message, icon in test_cases:
        print(f"{icon} {message}", end=" → ")
        try:
            response = await asyncio.wait_for(
                chat_with_ai(TEST_USER_ID, message, use_cache=False),
                timeout=12
            )
            print(f"✅ ({len(response)} симв.)")
            results.append(True)
        except Exception as e:
            print(f"❌ {str(e)[:20]}...")
            results.append(False)

    # Итог
    success = sum(results)
    total = len(results)
    print(f"\n🎯 РЕЗУЛЬТАТ: {success}/{total}")

    if success == total:
        print("🎉 ВСЕ ФУНКЦИИ РАБОТАЮТ!")
    elif success >= total * 0.75:
        print("👍 БОЛЬШИНСТВО ФУНКЦИЙ РАБОТАЮТ")
    else:
        print("⚠️ НУЖНЫ ДОРАБОТКИ")

async def main():
    await express_test()

if __name__ == "__main__":
    asyncio.run(main())