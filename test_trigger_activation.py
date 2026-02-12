#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тест активации триггеров инструментов
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def test_tool_triggers():
    """Тестируем активацию триггеров инструментов"""

    # Create test user
    user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')

    test_cases = [
        "Как приготовить пасту карбонара?",
        "Какие фильмы посмотреть сегодня?",
        "Где найти новых друзей?",
        "Как улучшить здоровье?"
    ]

    print("=== ТЕСТИРОВАНИЕ АКТИВАЦИИ ТРИГГЕРОВ ===\n")

    for i, query in enumerate(test_cases, 1):
        print(f"Тест {i}: {query}")
        print("-" * 50)

        try:
            session = SessionLocal()
            # Вызываем чат с AI
            result = await chat_with_ai(
                message=query,
                user_id=user.id,
                db_session=session
            )

            response = result['response']
            tool_calls = result.get('tool_calls', [])
            used_tools = [call.get('function', {}).get('name', '') for call in tool_calls]

            print(f"📝 Ответ: {len(response)} символов")
            print(f"🔧 Инструменты: {used_tools if used_tools else 'нет'}")

            # Проверяем, есть ли упоминание инструментов в ответе
            tool_mentions = []
            if "research_topic" in str(response).lower():
                tool_mentions.append("research_topic")
            if "find_partners" in str(response).lower():
                tool_mentions.append("find_partners")
            if "list_tasks" in str(response).lower():
                tool_mentions.append("list_tasks")
            if "add_task" in str(response).lower():
                tool_mentions.append("add_task")

            if tool_mentions:
                print(f"✅ ОБНАРУЖЕНЫ УПОМИНАНИЯ ИНСТРУМЕНТОВ: {', '.join(tool_mentions)}")
            else:
                print("❌ ИНСТРУМЕНТЫ НЕ ОБНАРУЖЕНЫ В ОТВЕТЕ")

            if used_tools:
                print(f"✅ РЕАЛЬНЫЕ ВЫЗОВЫ ИНСТРУМЕНТОВ: {', '.join(used_tools)}")
            else:
                print("❌ РЕАЛЬНЫЕ ВЫЗОВЫ ИНСТРУМЕНТОВ НЕ ОБНАРУЖЕНЫ")

            print(f"Ответ: {response[:200]}...")
            print()

            session.close()

        except Exception as e:
            print(f"❌ ОШИБКА: {e}")
            print()

if __name__ == "__main__":
    asyncio.run(test_tool_triggers())