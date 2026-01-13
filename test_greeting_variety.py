#!/usr/bin/env python3
"""
Тест разнообразия ответов на приветствия
"""
import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Force local mode for testing
os.environ['LOCAL'] = '1'

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai, set_redis_client
from models import Base, engine, Session, User, UserProfile
from config import DATABASE_URL, REDIS_URL
import redis.asyncio as redis

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create database tables
try:
    Base.metadata.create_all(engine)
    logger.info("Database tables created successfully")
except Exception as e:
    logger.error(f"Failed to create database tables: {e}")

async def test_greeting_variety():
    """Тестируем разнообразие ответов на разные приветствия"""

    # Set up Redis client
    redis_client = redis.from_url(REDIS_URL)
    set_redis_client(redis_client)

    greetings = [
        "Привет",
        "Здравствуйте",
        "Добрый день",
        "Привет!",
        "Хай",
        "Hello",
        "Hi there"
    ]

    responses = []

    print("=== Тест разнообразия приветствий ===")

    for i, greeting in enumerate(greetings, 1):
        print(f"\n{i}. Тестируем: '{greeting}'")
        try:
            response = await chat_with_ai(
                message=greeting,
                user_id=123456789,
                context=[]
            )
            responses.append(response)
            print(f"   Ответ: {response}")

            # Проверяем, что ответ содержит приветствие
            if any(word in response.lower() for word in ['привет', 'здравствуй', 'добрый', 'хай', 'hello', 'hi']):
                print("   ✓ Содержит приветствие")
            else:
                print("   ⚠ Не содержит явного приветствия")

        except Exception as e:
            print(f"   ✗ Ошибка: {e}")

    print("\n=== Анализ разнообразия ===")
    unique_responses = set(responses)
    print(f"Всего ответов: {len(responses)}")
    print(f"Уникальных ответов: {len(unique_responses)}")

    if len(unique_responses) > 1:
        print("✓ Ответы разнообразны!")
    else:
        print("⚠ Все ответы одинаковые")

    print("\nВсе ответы:")
    for i, resp in enumerate(responses, 1):
        print(f"{i}. {resp}")

    # Clean up
    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(test_greeting_variety())