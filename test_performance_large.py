#!/usr/bin/env python3
"""
Тест производительности агента с большим количеством задач
"""

import asyncio
import sys
import os
import time
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from models import Session, Task, User
import datetime

async def test_large_dataset():
    """Тестируем производительность с большим количеством задач"""
    print('🧪 Тестируем производительность с большим объемом данных...')

    session = Session()

    # Создаем тестового пользователя с большим количеством задач
    test_user_id = 999999999
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(
            telegram_id=test_user_id,
            username="test_user",
            first_name="Test User"
        )
        session.add(user)
        session.commit()

    # Создаем 200 тестовых задач
    print('📝 Создаем 200 тестовых задач...')
    for i in range(200):
        task = Task(
            user_id=user.id,
            title=f"Тестовая задача #{i+1}",
            status="pending" if i < 150 else "completed",  # 150 активных, 50 выполненных
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )
        session.add(task)

    session.commit()
    print('✅ Создано 200 задач')

    # Тестируем время ответа
    print('\n⏱️  Тестируем время ответа...')

    test_queries = [
        "Покажи мои задачи",
        "Создай задачу: купить продукты завтра",
        "Удали задачу купить продукты",
        "Перенеси задачу на завтра",
        "Найди контакты для проекта"
    ]

    total_time = 0
    for query in test_queries:
        print(f'\n--- Тестируем: "{query}" ---')
        start_time = time.time()

        try:
            result = await chat_with_ai(query, user_id=test_user_id)
            end_time = time.time()

            response_time = end_time - start_time
            total_time += response_time

            print(f'Время ответа: {response_time:.2f} сек')

            if 'tool_calls' in result and result['tool_calls']:
                called_tools = [call.get('function', {}).get('name', '') for call in result['tool_calls']]
                print(f'Вызванные инструменты: {called_tools}')
            else:
                print('Инструменты не вызваны (общее общение)')

        except Exception as e:
            print(f'Ошибка: {e}')

    avg_time = total_time / len(test_queries)
    print(f'Среднее время ответа: {avg_time:.2f} сек')
    # Оценка производительности
    if avg_time < 1.0:
        print('✅ Отличная производительность!')
    elif avg_time < 3.0:
        print('⚠️  Приемлемая производительность')
    else:
        print('❌ Нужна оптимизация производительности')

    # Очищаем тестовые данные
    print('\n🧹 Очищаем тестовые данные...')
    session.query(Task).filter(Task.user_id == user.id).delete()
    session.delete(user)
    session.commit()

    session.close()
    print('✅ Тест завершен')

if __name__ == "__main__":
    asyncio.run(test_large_dataset())