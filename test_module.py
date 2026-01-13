"""
Тест модульной структуры ai_integration
"""
import asyncio
from ai_integration import chat_with_ai, add_task, list_tasks

async def test_basic():
    print("=== Тест модульной структуры ===\n")
    
    # Тест 1: Импорты
    print("✅ Импорты работают")
    
    # Тест 2: Chat with AI
    print("\n[Тест] Отправка сообщения AI...")
    response = await chat_with_ai("Привет!", user_id=123456)
    print(f"AI ответ: {response[:100]}...")
    
    print("\n✅ Все тесты пройдены!")

if __name__ == "__main__":
    asyncio.run(test_basic())
