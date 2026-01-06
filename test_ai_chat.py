"""
Тест AI чата с сообщением "я увлекаюсь спортом"
"""
import asyncio
import os

# Устанавливаем LOCAL=1 для использования SQLite
os.environ['LOCAL'] = '1'

from ai_integration import chat_with_ai

async def test_chat():
    """Тестируем чат с проблемным сообщением"""
    print("Тестируем сообщение: 'я увлекаюсь спортом'")
    print("-" * 50)
    
    # Тестовые данные
    message = "я увлекаюсь спортом"
    mentions = "нет"
    context = []
    user_id = 146333757
    
    try:
        # Вызываем функцию чата
        response = await chat_with_ai(
            message=message,
            context=context,
            user_id=user_id,
            file_content=None
        )
        
        print("\n✅ Ответ получен:")
        print(response)
        
        # Проверяем на наличие ошибок
        if "Ошибка" in response or "error" in response.lower():
            print("\n❌ ОШИБКА в ответе!")
            return False
        else:
            print("\n✅ Тест успешен - нет ошибок")
            return True
            
    except Exception as e:
        print(f"\n❌ Исключение: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_chat())
    exit(0 if result else 1)
