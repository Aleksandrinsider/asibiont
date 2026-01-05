import asyncio
import sys
import os
import traceback
sys.path.append(os.path.dirname(__file__))

from ai_integration import chat_with_ai

async def test():
    messages = [
        "добавь задачу купить продукты",
        "покажи задачи",
        "заверши купить продукты",
        "удали купить продукты",
        "помоги найти партнера по программированию",
        "напомни через 5 минут о встрече"
    ]
    
    for msg in messages:
        print(f"\nТест: {msg}")
        try:
            result = await chat_with_ai(msg, user_id=1)
            print(f"Ответ: {result}")
        except Exception as e:
            print(f"Ошибка: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())