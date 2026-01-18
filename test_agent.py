import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session

async def test_agent():
    """Тестируем работу агента на соответствие требованиям"""

    print("=== ТЕСТИРОВАНИЕ АГЕНТА ===\n")

    # Создаем сессию БД
    db_session = Session()

    try:
        # Тест 1: Простой запрос на добавление задачи
        print("Тест 1: Добавление задачи")
        result1 = await chat_with_ai('Добавь задачу купить молоко завтра в 10 утра', context=[], user_id=1001, db_session=db_session)
        print(f"Ответ: {result1[:200]}...")
        print("✓ Задача должна быть добавлена\n")

        # Тест 2: Запрос списка задач
        print("Тест 2: Просмотр списка задач")
        result2 = await chat_with_ai('Покажи мои задачи', context=[{"user": "Добавь задачу купить молоко завтра в 10 утра", "agent": result1}], user_id=1001, db_session=db_session)
        print(f"Ответ: {result2[:200]}...")
        print("✓ Должен показать список задач\n")

        # Тест 3: Завершение задачи
        print("Тест 3: Завершение задачи")
        result3 = await chat_with_ai('Заверши задачу купить молоко', context=[
            {"user": "Добавь задачу купить молоко завтра в 10 утра", "agent": result1},
            {"user": "Покажи мои задачи", "agent": result2}
        ], user_id=1001, db_session=db_session)
        print(f"Ответ: {result3[:200]}...")
        print("✓ Задача должна быть завершена, и должен быть запланирован result check\n")

        # Тест 4: Запрос совета
        print("Тест 4: Запрос совета")
        result4 = await chat_with_ai('Как лучше организовать свой день?', context=[], user_id=1001, db_session=db_session)
        print(f"Ответ: {result4[:300]}...")
        print("✓ Должен дать конкретные рекомендации без списков\n")

        # Тест 5: Поиск партнеров
        print("Тест 5: Поиск партнеров")
        result5 = await chat_with_ai('Найди мне партнеров для проекта', context=[], user_id=1001, db_session=db_session)
        print(f"Ответ: {result5[:200]}...")
        print("✓ Должен предложить контакты из базы или запросить больше информации\n")

        print("=== ТЕСТИРОВАНИЕ ЗАВЕРШЕНО ===")

    except Exception as e:
        print(f"Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_agent())