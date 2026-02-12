import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.prompts import get_extended_system_prompt
from ai_integration.memory import LongTermMemory
from models import User, SessionLocal

async def test_brief_responses():
    print("=== ТЕСТИРОВАНИЕ КРАТКИХ ОТВЕТОВ ===\n")

    # Create test user
    user = User(
        id=1,
        telegram_id=123456789,
        username="test_user",
        subscription_tier="STANDARD",
        created_at="2024-01-01"
    )

    # Initialize memory
    memory = LongTermMemory(user.id)

    # Get system prompt
    try:
        from datetime import datetime
        current_time = datetime.now()
        system_prompt = get_extended_system_prompt(
            user_now=current_time,
            current_time_str=current_time.strftime("%H:%M"),
            current_date_str=current_time.strftime("%Y-%m-%d"),
            user_username=user.username,
            mentions_str="",
            user_memory=memory,
            user_id_param=user.id
        )
        print(f"✓ System prompt generated ({len(system_prompt)} chars)")
    except Exception as e:
        print(f"✗ System prompt error: {e}")
        return

    # Test queries that should be brief
    test_queries = [
        "Расскажи подробно о приготовлении пасты карбонара с пошаговым рецептом",  # Этот должен быть длинным
        "Какие фильмы посмотреть на выходных?",
        "Как убраться в квартире быстро?"
    ]

    for i, query in enumerate(test_queries, 1):
        print(f"\n--- Тест {i}: {query} ---")

        try:
            session = SessionLocal()
            result = await chat_with_ai(
                message=query,
                user_id=user.id,
                db_session=session
            )

            response = result['response']
            response_length = len(response)

            print(f"Ответ ({response_length} символов):")
            print(f'"{response[:200]}..."' if len(response) > 200 else f'"{response}"')

            # Check length
            if response_length <= 300:
                print("✅ КРАТКИЙ ОТВЕТ - ОТЛИЧНО!")
            elif response_length <= 500:
                print("⚠️ СРЕДНИЙ - можно короче")
            else:
                print("❌ СЛИШКОМ ДЛИННЫЙ - нужно сокращать")

            session.close()

        except Exception as e:
            print(f"✗ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_brief_responses())