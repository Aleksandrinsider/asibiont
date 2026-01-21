import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

os.environ["LOCAL"] = "1"

from ai_integration.chat import chat_with_ai

async def test_complete_task():
    user_id = 123456789

    # Создать задачу
    response1 = await chat_with_ai("Создай задачу позвонить маме завтра в 10:00", user_id=user_id)
    print(f"Создание задачи: {response1}")

    # Завершить задачу
    response2 = await chat_with_ai("Заверши задачу позвонить маме", user_id=user_id)
    print(f"Завершение задачи: {response2}")

    # Попытаться завершить снова
    response3 = await chat_with_ai("Заверши задачу позвонить маме", user_id=user_id)
    print(f"Повторное завершение: {response3}")

if __name__ == "__main__":
    asyncio.run(test_complete_task())