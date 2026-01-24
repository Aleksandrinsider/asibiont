"""
Тест создания задачи с напоминанием для проверки логов
"""
import asyncio
from ai_integration.chat import chat_with_ai
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test():
    message = "нужно проверить почту через 5 минут"
    user_id = 123456789  # Тестовый пользователь
    
    print(f"\n{'='*60}")
    print(f"Тест: {message}")
    print(f"{'='*60}\n")
    
    response = await chat_with_ai(message, context=None, user_id=user_id)
    
    print(f"\n{'='*60}")
    print(f"Ответ AI:")
    print(f"{'='*60}")
    print(response)
    print(f"\n{'='*60}\n")

if __name__ == "__main__":
    asyncio.run(test())
