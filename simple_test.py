import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.autonomous_agent import chat_with_ai
from ai_integration.prompts import get_extended_system_prompt
from ai_integration.memory import LongTermMemory
from models import User, SessionLocal

async def test_scenario(scenario, tier):
    print(f"\n=== Testing {scenario['name']} on {tier} tier ===")

    # Create test user
    user = User(
        id=1,
        telegram_id=123456789,
        username="test_user",
        subscription_tier=tier,
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
        print(f"✓ System prompt generated successfully ({len(system_prompt)} chars)")
    except Exception as e:
        print(f"✗ System prompt error: {e}")
        return []

    results = []

    for i, exchange in enumerate(scenario['exchanges'], 1):
        print(f"\n--- Exchange {i}: {exchange['query'][:50]}... ---")

        try:
            # Create database session
            session = SessionLocal()

            # Process user query using chat_with_ai
            result = await chat_with_ai(
                message=exchange['query'],
                user_id=user.id,
                db_session=session
            )

            response = result['response']
            tool_calls = result.get('tool_calls', [])

            print(f"✓ Response received ({len(response)} chars)")
            if tool_calls:
                print(f"✓ Used {len(tool_calls)} tools: {[t['function']['name'] for t in tool_calls]}")

            # Check if response contains expected elements
            success = True
            checks = []

            for check in exchange.get('checks', []):
                if check == 'uses_tools' and not tool_calls:
                    success = False
                    checks.append("No tool usage detected")
                elif check == 'personalized' and 'test_user' not in response:
                    success = False
                    checks.append("No personalization detected")
                elif check == 'comprehensive' and len(response) < 100:
                    success = False
                    checks.append("Response too short")

            if success:
                print("✓ All checks passed")
            else:
                print(f"⚠ Some checks failed: {', '.join(checks)}")

            results.append({
                'query': exchange['query'],
                'response': response[:200] + "..." if len(response) > 200 else response,
                'tools_used': len(tool_calls),
                'success': success,
                'checks': checks
            })

            session.close()

        except Exception as e:
            print(f"✗ Exchange {i} failed: {e}")
            results.append({
                'query': exchange['query'],
                'error': str(e),
                'success': False
            })

    return results

async def run_test():
    # Test scenarios for universality
    scenarios = [
        {
            'name': 'Daily Tasks',
            'exchanges': [
                {
                    'query': 'Как приготовить пасту карбонара?',
                    'checks': ['comprehensive']
                },
                {
                    'query': 'Помоги составить список покупок для ужина на двоих',
                    'checks': ['comprehensive']
                },
                {
                    'query': 'Как убраться в квартире за 30 минут?',
                    'checks': ['comprehensive']
                }
            ]
        },
        {
            'name': 'Social Connections',
            'exchanges': [
                {
                    'query': 'Как познакомиться с людьми в новом городе?',
                    'checks': ['comprehensive']
                },
                {
                    'query': 'Идеи для романтического свидания',
                    'checks': ['comprehensive']
                }
            ]
        },
        {
            'name': 'Entertainment',
            'exchanges': [
                {
                    'query': 'Какие фильмы посмотреть на выходных?',
                    'checks': ['comprehensive', 'uses_tools']
                },
                {
                    'query': 'Рекомендуй музыку для пробежки',
                    'checks': ['comprehensive']
                }
            ]
        }
    ]

    tiers = ['LIGHT', 'STANDARD']

    all_results = {}

    for tier in tiers:
        print(f"\n{'='*50}")
        print(f"TESTING {tier} TIER")
        print(f"{'='*50}")

        tier_results = {}
        for scenario in scenarios:
            results = await test_scenario(scenario, tier)
            tier_results[scenario['name']] = results

            # Summary for scenario
            successful = sum(1 for r in results if r.get('success', False))
            total = len(results)
            print(f"\nCompleted {scenario['name']} with {successful}/{total} successful exchanges")

        all_results[tier] = tier_results

    # Final summary
    print(f"\n{'='*60}")
    print("FINAL TEST SUMMARY")
    print(f"{'='*60}")

    for tier, scenarios_results in all_results.items():
        print(f"\n{tier} Tier:")
        for scenario_name, results in scenarios_results.items():
            successful = sum(1 for r in results if r.get('success', False))
            total = len(results)
            print(f"  {scenario_name}: {successful}/{total} successful")

if __name__ == "__main__":
    asyncio.run(run_test())