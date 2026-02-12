import asyncio
from ai_integration.autonomous_agent import chat_with_ai

async def test_improvements():
    print('🧪 ТЕСТИРОВАНИЕ УЛУЧШЕНИЙ АГЕНТА')
    print('=' * 50)

    # Тест 1: Проверка разнообразия инструментов
    print('\n1. ТЕСТ РАЗНООБРАЗИЯ ИНСТРУМЕНТОВ')
    result1 = await chat_with_ai('Что нового в AI?', user_id=77777)
    print('Запрос: Что нового в AI?')
    print('Инструменты:', result1.get('tools_used', []))
    print('Ответ:', result1['response'][:150] + '...')

    # Тест 2: Проверка персонализации
    print('\n2. ТЕСТ ПЕРСОНАЛИЗАЦИИ')
    result2 = await chat_with_ai('Привет, я ищу партнеров для стартапа', user_id=77777)
    print('Запрос: Привет, я ищу партнеров для стартапа')
    print('Инструменты:', result2.get('tools_used', []))
    print('Ответ:', result2['response'][:150] + '...')

    # Тест 3: Проверка actionable шагов
    print('\n3. ТЕСТ ACTIONABLE ШАГОВ')
    result3 = await chat_with_ai('Нашел партнеров, что делать дальше?', user_id=77777)
    print('Запрос: Нашел партнеров, что делать дальше?')
    print('Инструменты:', result3.get('tools_used', []))
    print('Ответ:', result3['response'][:150] + '...')

    print('\n✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО')

if __name__ == "__main__":
    asyncio.run(test_improvements())