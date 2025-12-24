from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import Message
from models import Session, Task
from payments import create_payment

router = Router()
r = redis.from_url(REDIS_URL)

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.reply("Привет! Я ИИ-бот для управления задачами. Чем могу помочь?")

@router.message(Command("add_task"))
async def add_task_handler(message: Message):
    # Простая версия: ожидать формат "Название: Описание"
    text = message.text.replace("/add_task", "").strip()
    if ":" in text:
        title, desc = text.split(":", 1)
        session = Session()
        task = Task(user_id=message.from_user.id, title=title.strip(), description=desc.strip())
        session.add(task)
        session.commit()
        session.close()
        await message.reply(f"Задача '{title}' добавлена!")
    else:
        await message.reply("Формат: /add_task Название: Описание")

@router.message(Command("list_tasks"))
async def list_tasks_handler(message: Message):
    session = Session()
    tasks = session.query(Task).filter_by(user_id=message.from_user.id).all()
    session.close()
    if tasks:
        response = "\n".join([f"{t.id}: {t.title} - {t.status}" for t in tasks])
    else:
        response = "Нет задач."
    await message.reply(response)

@router.message(Command("pay"))
async def pay_handler(message: Message):
    url = create_payment(100, "Премиум подписка", message.from_user.id)
    await message.reply(f"Оплатите здесь: {url}")

@router.message()
async def chat_handler(message: Message):
    # Для всех остальных сообщений - чат с ИИ
    user_id = message.from_user.id
    context = r.get(f"context:{user_id}")
    if context:
        context = context.decode('utf-8')
    response = chat_with_ai(message.text, context)
    # Сохранить контекст для продолжения
    r.set(f"context:{user_id}", response)
    await message.reply(response)