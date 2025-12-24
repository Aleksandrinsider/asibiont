from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from ai_integration import chat_with_ai
import os

router = Router()

if os.getenv("LOCAL") == "1":
    # Для локального тестирования использовать dict вместо Redis
    context_store = {}
else:
    import redis
    from config import REDIS_URL
    r = redis.from_url(REDIS_URL)

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.reply("Привет! Я ИИ-бот для управления задачами. Просто общайтесь со мной на естественном языке!")

@router.message()
async def chat_handler(message: Message):
    print(f"Received message from {message.from_user.id}: {message.text}")
    # Все сообщения обрабатываются через ИИ
    user_id = message.from_user.id
    if os.getenv("LOCAL") == "1":
        context = context_store.get(f"context:{user_id}")
    else:
        context = r.get(f"context:{user_id}")
        if context:
            context = context.decode('utf-8')
    response = chat_with_ai(message.text, context, user_id)
    print(f"Response: {response}")
    # Сохранить контекст для продолжения
    if os.getenv("LOCAL") == "1":
        context_store[f"context:{user_id}"] = response
    else:
        r.set(f"context:{user_id}", response)
    await message.reply(response)