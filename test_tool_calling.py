import os
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"

import logging
logging.basicConfig(level=logging.INFO)
from ai_integration.commands.conversation import ConversationCommand
from ai_integration.router import CommandRouter
from models import User, SessionLocal
import pytz
from datetime import datetime
import asyncio

async def test_tool_calling():
    # Создаем тестового пользователя
    user = User(
        telegram_id=12345,
        username='test_user',
        timezone='Europe/Moscow',
        created_at=datetime.now(pytz.UTC),
        memory='Ты увлекаешься технологиями, бизнесом и спортом. У тебя есть активные задачи по разработке приложений.'
    )

    # Создаем сессию БД
    db_session = SessionLocal()

    # Создаем роутер
    router = CommandRouter()

    message_time = datetime.now(pytz.UTC)

    # Тестовое сообщение для создания задачи
    message = "Создай задачу: позвонить клиенту завтра в 10:00"

    print(f'=== Тестирование tool calling: "{message}" ===')

    try:
        result = await router.route(message, user.telegram_id, message_time)
        response = await result.execute(user.telegram_id, db_session)
        print(f'Ответ: {response}')

    except Exception as e:
        print(f'❌ Ошибка: {e}')

    db_session.close()

if __name__ == "__main__":
    asyncio.run(test_tool_calling())