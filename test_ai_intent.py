import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from improved_prompts_final import ai_classify_intent
from config import DEEPSEEK_API_KEY

async def test_ai_intent():
    test_messages = [
        "Напомни мне позвонить маме завтра в 10 утра",
        "Я сделал уборку в квартире",
        "Покажи мои задачи",
        "Привет, как дела?",
        "Мой город Москва, я разработчик",
        "Поручи @john проверить отчет"
    ]

    for message in test_messages:
        print(f"\nСообщение: {message}")
        intent = await ai_classify_intent(message, api_key=DEEPSEEK_API_KEY)
        print(f"Намерение: {intent}")

if __name__ == "__main__":
    asyncio.run(test_ai_intent())