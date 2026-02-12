#!/usr/bin/env python3
"""
Простой тест вызова инструментов
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import HybridAutonomousAgent

async def test_simple_tool_call():
    agent = HybridAutonomousAgent()

    # Простое сообщение, которое должно вызвать list_tasks
    user_message = "Привет!"

    try:
        # Получаем план
        plan = await agent.plan_strategy(user_message, 99999)  # user_id = 99999 (существующий пользователь)

        print(f"План: {plan}")
        print(f"Intent: {plan.get('intent')}")
        print(f"Actions: {plan.get('actions', [])}")

        if plan.get('actions'):
            print("✅ ИНСТРУМЕНТЫ ВЫЗВАНЫ!")
            for action in plan['actions']:
                print(f"  - {action['tool']} с параметрами {action['params']}")
        else:
            print("❌ ИНСТРУМЕНТЫ НЕ ВЫЗВАНЫ")

    except Exception as e:
        print(f"Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_simple_tool_call())