"""
Тест улучшенного AI-ответа для создания задачи
"""
import asyncio
from ai_integration.chat import chat_with_ai
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test():
    message = "напомни заказать продукты на ужин через 5 минут"
    user_id = 123456789  # Тестовый пользователь
    
    print(f"\n{'='*60}")
    print(f"Тест улучшенного промпта: {message}")
    print(f"{'='*60}\n")
    
    response = await chat_with_ai(message, context=None, user_id=user_id)
    
    print(f"\n{'='*60}")
    print(f"Ответ AI (должен быть детализированным):")
    print(f"{'='*60}")
    print(response)
    print(f"\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test())