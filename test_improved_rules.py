#!/usr/bin/env python3
"""
Простой тест улучшенных правил распознавания
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai

async def test_improved():
    print('🧪 Тестируем улучшенные правила...')

    test_cases = [
        ('Удали задачу купить молоко', 'delete_task'),
        ('Покажи мои задачи', 'list_tasks'),
        ('Перенеси задачу на завтра', 'reschedule_task'),
        ('Найди контакты для проекта', 'find_relevant_contacts_for_task')
    ]

    for msg, expected in test_cases:
        print(f'\n--- {msg} ---')
        try:
            result = await chat_with_ai(msg, user_id=123456789)
            called = [call.get('function', {}).get('name', '') for call in result.get('tool_calls', [])]
            print(f'Ожидалось: {expected}')
            print(f'Вызвано: {called}')
            success = expected in called
            print(f'Результат: {"✅" if success else "❌"}')
        except Exception as e:
            print(f'Ошибка: {e}')

if __name__ == "__main__":
    asyncio.run(test_improved())