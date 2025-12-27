from handlers import update_profile_handler, find_partners_handler
from aiogram.types import Message, User as TgUser
import asyncio

# Мок сообщение
class MockMessage:
    def __init__(self, text, user_id, username):
        self.text = text
        self.from_user = TgUser(id=user_id, username=username)
        self.chat = type('Chat', (), {'id': user_id})()
        self.bot = None

async def test_commands():
    # Тест update_profile
    msg = MockMessage("/update_profile программирование, технологии, создать приложение", 123456789, "testuser1")
    await update_profile_handler(msg)

    # Тест find_partners для другого пользователя
    msg2 = MockMessage("/find_partners", 987654321, "testuser2")
    await find_partners_handler(msg2)

if __name__ == "__main__":
    asyncio.run(test_commands())