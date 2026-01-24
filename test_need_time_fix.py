"""
Тест исправления NEED_TIME_FOR_TASK обработки
"""
import asyncio
from ai_integration.chat import chat_with_ai
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test():
    user_id = 123456789

    print("=== ТЕСТ 1: Сообщение без времени ===")
    response1 = await chat_with_ai("надо проверить почту", context=None, user_id=user_id)
    print(f"Ответ: {response1}")

    print("\n=== ТЕСТ 2: Ответ на вопрос о времени ===")
    response2 = await chat_with_ai("напомни через 5 минут", context=None, user_id=user_id)
    print(f"Ответ: {response2}")

if __name__ == "__main__":
    asyncio.run(test())