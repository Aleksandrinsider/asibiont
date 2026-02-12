#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой тест триггеров
"""

import asyncio
import sys
sys.path.append('.')

from ai_integration.autonomous_agent import chat_with_ai
from models import User, SessionLocal

async def quick_test():
    user = User(id=1, telegram_id=123456789, username='test_user', subscription_tier='STANDARD', created_at='2024-01-01')
    session = SessionLocal()

    queries = [
        'Как приготовить пасту карбонара?',
        'Где найти новых друзей?',
    ]

    for i, query in enumerate(queries, 1):
        print(f'Тест {i}: {query}')
        result = await chat_with_ai(message=query, user_id=user.id, db_session=session)
        tools = [call.get('function', {}).get('name', '') for call in result.get('tool_calls', [])]
        print(f'Инструменты: {tools if tools else "нет"}')
        print('---')

    session.close()

if __name__ == "__main__":
    asyncio.run(quick_test())