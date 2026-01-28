#!/usr/bin/env python3
"""
Тестовый скрипт для симуляции диалога AI с самим собой.
Запускает 20 итераций, где AI генерирует ответы как пользователь.
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.chat import chat_with_ai
from datetime import datetime, timezone
import pytz

async def simulate_dialog():
    """Симуляция диалога на 20 итераций"""

    # Начальные параметры
    user_id = 200012  # Тестовый пользователь
    username = "test_user"
    timezone_str = "Europe/Moscow"
    user_tz = pytz.timezone(timezone_str)

    # Начальный контекст разговора
    conversation_context = []

    # Начальное сообщение
    current_message = "Привет"

    print("=== СИМУЛЯЦИЯ ДИАЛОГА: 20 ИТЕРАЦИЙ ===\n")

    for i in range(20):
        print(f"Итерация {i+1}:")
        print(f"Пользователь: {current_message}")

        # Получить ответ AI
        try:
            result = await chat_with_ai(
                message=current_message,
                context=conversation_context,
                user_id=user_id
            )
            
            response = result['response']
            tool_calls = result['tool_calls']
            
            print(f"AI: {response}")
            
            # Показать tool calls если они есть
            if tool_calls:
                print(f"Tool calls executed: {len(tool_calls)}")
                for i, tc in enumerate(tool_calls, 1):
                    print(f"  {i}. {tc['function']}({tc['arguments']})")
            else:
                print("No tool calls executed")

            # Добавить в контекст разговора
            conversation_context.append({"role": "user", "content": current_message})
            conversation_context.append({"role": "assistant", "content": response})

            # Следующее сообщение пользователя - это ответ AI (симуляция)
            current_message = response

        except Exception as e:
            print(f"Ошибка: {e}")
            break

        print("-" * 50)

        # Небольшая пауза между итерациями
        await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(simulate_dialog())