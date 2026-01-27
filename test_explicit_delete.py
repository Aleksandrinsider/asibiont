import sys
sys.path.append('.')
from ai_integration.chat import chat_with_ai
import asyncio

async def test_explicit_delete():
    print('Тестируем явную команду удаления...')

    # Создаем задачу
    result1 = await chat_with_ai('создай задачу "явная тестовая задача" на завтра в 16:00', user_id=123456789)
    print('Создана задача:', 'успешно' if 'создал' in result1.lower() else 'не успешно')

    # Проверяем список
    result2 = await chat_with_ai('покажи задачи', user_id=123456789)
    print('Задачи в списке:', 'есть' if 'явная тестовая задача' in result2 else 'нет')

    # Удаляем с явной командой
    result3 = await chat_with_ai('удалить задачу "явная тестовая задача"', user_id=123456789)
    print('Результат удаления:', result3[:200])

    # Проверяем после удаления
    result4 = await chat_with_ai('покажи задачи', user_id=123456789)
    print('Задачи после удаления:', 'удалена' if 'явная тестовая задача' not in result4 else 'осталась')

asyncio.run(test_explicit_delete())