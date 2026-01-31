import asyncio
import sys
sys.path.insert(0, '.')

from ai_integration.chat import chat_with_ai
from models import init_db

async def quick_test():
    print("Инициализация БД...")
    init_db()
    
    print("Вызов chat_with_ai...")
    try:
        result = await asyncio.wait_for(
            chat_with_ai("Привет", user_id=999),
            timeout=20.0
        )
        print(f"✅ Результат: {result}")
        return True
    except asyncio.TimeoutError:
        print("❌ TIMEOUT - chat_with_ai не ответил за 20 секунд")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(quick_test())
    sys.exit(0 if success else 1)
