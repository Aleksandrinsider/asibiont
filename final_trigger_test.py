#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Финальный тест триггеров инструментов
"""

import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def final_trigger_test():
    print("🎯 ФИНАЛЬНЫЙ ТЕСТ ТРИГГЕРОВ ИНСТРУМЕНТОВ\n")

    # Create test user
    user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')

    # Test queries that should trigger tools
    test_scenarios = [
        {
            'query': 'Как приготовить пасту карбонара?',
            'expected_tools': ['research_topic'],
            'description': 'Кулинария - должен использовать research_topic'
        },
        {
            'query': 'Какие фильмы посмотреть сегодня?',
            'expected_tools': ['research_topic'],
            'description': 'Фильмы - должен использовать research_topic'
        },
        {
            'query': 'Где найти новых друзей?',
            'expected_tools': ['find_partners'],
            'description': 'Знакомства - должен использовать find_partners'
        },
        {
            'query': 'Как улучшить здоровье?',
            'expected_tools': ['research_topic'],
            'description': 'Здоровье - должен использовать research_topic'
        }
    ]

    total_tests = len(test_scenarios)
    successful_tests = 0

    for i, scenario in enumerate(test_scenarios, 1):
        print(f"🧪 Тест {i}/{total_tests}: {scenario['description']}")
        print(f"   ❓ {scenario['query']}")

        try:
            session = SessionLocal()
            result = await chat_with_ai(
                message=scenario['query'],
                user_id=user.id,
                db_session=session
            )

            response = result['response']
            tool_calls = result.get('tool_calls', [])
            used_tools = [call.get('function', {}).get('name', '') for call in tool_calls]

            print(f"   📝 Ответ: {len(response)} символов")
            print(f"   🔧 Инструменты: {used_tools if used_tools else 'нет'}")

            # Check tool usage
            tools_used = any(tool in used_tools for tool in scenario['expected_tools'])

            success = tools_used

            if success:
                print("   ✅ ТРИГГЕР РАБОТАЕТ!")
                successful_tests += 1
            else:
                print("   ❌ ТРИГГЕР НЕ СРАБОТАЛ")

            session.close()

        except Exception as e:
            print(f"   ✗ Ошибка: {e}")

        print()

    # Final results
    success_rate = (successful_tests / total_tests) * 100
    print(f"🎯 РЕЗУЛЬТАТ: {successful_tests}/{total_tests} ({success_rate:.1f}%)")

    if success_rate >= 75:
        print("🏆 ОТЛИЧНО! Триггеры инструментов работают!")
    elif success_rate >= 50:
        print("👍 ХОРОШО! Большинство триггеров работает.")
    else:
        print("🔧 НУЖНО ДОРАБОТАТЬ триггеры.")

if __name__ == "__main__":
    asyncio.run(final_trigger_test())