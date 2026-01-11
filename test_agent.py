#!/usr/bin/env python3
"""Test agent functionality directly"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration import AIIntegration, chat_with_ai, set_redis_client
from config import LOCAL

async def test_agent():
    """Test agent functionality"""

    # Initialize Redis client
    if LOCAL:
        redis_client = None
        print("Using local mode without Redis")
    else:
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url("redis://localhost:6379", decode_responses=False)
            print("Redis client initialized")
        except Exception as e:
            print(f"Failed to initialize Redis: {e}")
            redis_client = None

    set_redis_client(redis_client)

    # Initialize AI
    ai = AIIntegration()
    print("AI Integration initialized")

    # Test different types of requests
    test_requests = [
        "Привет! Как дела?",
        "Добавь задачу: купить продукты в магазине",
        "Напомни мне позвонить маме через 2 часа",
        "Покажи мои задачи",
        "Я сделал задачу купить продукты",
        "Найди людей для совместной работы над проектом",
        "Обнови мой профиль: я работаю в IT компании",
        "Какие у меня цели на сегодня?"
    ]

    user_id = 123456789

    for i, request in enumerate(test_requests, 1):
        print(f"\n{'='*50}")
        print(f"Test {i}: {request}")
        print('='*50)

        try:
            response = await chat_with_ai(request, user_id=user_id)
            print(f"Agent response: {response}")

            # Check if response contains function calls or tool usage
            if "add_task" in response.lower() or "list_tasks" in response.lower() or "complete_task" in response.lower():
                print("✓ Agent attempted to use functions")
            else:
                print("ℹ Agent responded with text only")

        except Exception as e:
            print(f"Error in test {i}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_agent())