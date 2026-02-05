#!/usr/bin/env python3
"""
Тест всех типов запросов для проверки работы агента
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai

async def test_all_tools():
    print('🧪 Тестируем распознавание всех типов запросов...')

    test_cases = [
        ('Создай задачу: купить молоко завтра в 10 утра', 'add_task'),
        ('Готово, купил молоко', 'complete_task'),
        ('Удали задачу купить молоко', 'delete_task'),
        ('Покажи мои задачи', 'list_tasks'),
        ('Перенеси задачу на завтра', 'reschedule_task'),
        ('Делегируй задачу коллеге', 'delegate_task'),
        ('Найди контакты для проекта', 'find_relevant_contacts_for_task'),
        ('Найди единомышленников по Python', 'find_partners'),
        ('Обнови мой профиль', 'update_profile'),
        ('Запомни что я люблю чай', 'update_user_memory')
    ]

    results = []

    for i, (message, expected_tool) in enumerate(test_cases, 1):
        print(f'\n{i}. "{message}" → ожидается {expected_tool}')
        try:
            result = await chat_with_ai(message, user_id=123456789)
            response = result.get('response', '')[:150] + '...'
            print(f'   ✅ Ответ: {response}')

            # Проверяем, есть ли tool_calls
            tool_calls = result.get('tool_calls', [])
            if tool_calls:
                called_tools = [call.get('function', {}).get('name', '') for call in tool_calls]
                print(f'   🔧 Вызванные инструменты: {called_tools}')
                results.append({
                    'message': message,
                    'expected': expected_tool,
                    'called': called_tools,
                    'success': expected_tool in called_tools
                })
            else:
                print('   📝 Только текстовый ответ')
                results.append({
                    'message': message,
                    'expected': expected_tool,
                    'called': [],
                    'success': False
                })

        except Exception as e:
            print(f'   ❌ Ошибка: {e}')
            results.append({
                'message': message,
                'expected': expected_tool,
                'error': str(e),
                'success': False
            })

    # Анализ результатов
    print('\n📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:')
    print('=' * 50)

    successful = sum(1 for r in results if r.get('success', False))
    total = len(results)

    print(f'✅ Правильных распознаваний: {successful}/{total} ({successful/total*100:.1f}%)')

    for result in results:
        status = '✅' if result.get('success', False) else '❌'
        msg = result['message'][:30] + '...'
        expected = result['expected']
        called = result.get('called', [])
        print(f'{status} {msg} | Ожидалось: {expected} | Вызвано: {called}')

    if successful == total:
        print('\n🎉 АГЕНТ ОТРАБАТЫВАЕТ ВСЕ ЗАПРОСЫ ПРАВИЛЬНО!')
    else:
        print('\n⚠️ Есть проблемы с распознаванием некоторых запросов')

if __name__ == "__main__":
    asyncio.run(test_all_tools())