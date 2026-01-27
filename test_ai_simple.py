#!/usr/bin/env python3
"""
Простой тест AI с упрощенными промптами
"""

import asyncio
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai

async def test_ai():
    """Тестируем AI с различными естественными запросами"""

    test_cases = [
        "напомни мне позвонить маме завтра в 10 утра",
        "что у меня запланировано на сегодня",
        "я только что закончил отчет, готово",
        "перенеси встречу с клиентом на послезавтра",
        "удали задачу про уборку",
        "@test_user помоги мне с презентацией",
        "расскажи о моих интересах",
        "что нового в технологиях",
    ]

    print("🧪 Тестирование AI с упрощенными промптами")
    print("=" * 50)

    for i, message in enumerate(test_cases, 1):
        print(f"\n📝 Тест {i}: '{message}'")
        print("-" * 30)

        try:
            # Тестируем с user_id=12345 (фейковый пользователь)
            response = await chat_with_ai(
                message=message,
                user_id=12345,
                message_type='normal'
            )

            print(f"🤖 Ответ: {response[:200]}{'...' if len(response) > 200 else ''}")

        except Exception as e:
            print(f"❌ Ошибка: {e}")

        print()

if __name__ == "__main__":
    asyncio.run(test_ai())