import asyncio
import os
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'

from ai_integration.chat import chat_with_ai

async def test_edit():
    result = await asyncio.wait_for(
        chat_with_ai("Отредактируй задачу 'купить продукты': добавь описание 'молоко и хлеб'", user_id=999000111),
        timeout=10.0
    )
    print('Result:', result)

asyncio.run(test_edit())