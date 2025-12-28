import json
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from ai_integration import chat_with_ai
from models import Session, User, UserProfile, Subscription
import os

PREMIUM_DESCRIPTION = "🚀 Лаборатория искусственного интеллекта EREBUS AI — ваш путь к успеху через умное управление задачами и мощное сообщество единомышленников!\n\nПредставьте: вы не просто планируете дела, а достигаете целей быстрее, чем когда-либо! С премиум-подпиской всего за 3000 рублей в месяц откройте доступ к эксклюзивным возможностям:\n\n🔹 **Интеллектуальный ИИ-ассистент**: Автоматическое планирование задач, умные напоминания и персональная мотивация — чтобы каждый день был продуктивным!\n🔹 **Проактивные советы**: Агент анализирует вашу ситуацию, делится инсайтами и предлагает шаги на основе ваших задач и интересов. Забудьте о хаосе — вперед к результатам!\n🔹 **Сообщество лидеров**: Найдите единомышленников по интересам — от программирования и дизайна до спорта и бизнеса. Общайтесь, коллаборируйте и меняйте жизнь вместе!\n🔹 **Совместные возможности**: Рекомендации по событиям, проектам и коллаборациям, которые откроют новые горизонты.\n🔹 **Персонализированные инструменты**: Управляйте задачами с приоритетами, дедлайнами и аналитикой прогресса — все в одном месте.\n🔹 **Безопасность на первом месте**: 🔒 Ваши данные шифруются, и даже мы не имеем к ним доступа. Полная конфиденциальность!\n\nНе ждите — присоединяйтесь к сообществу амбициозных профессионалов, где успех — это норма! Оформите подписку прямо сейчас командой /subscribe и начните трансформировать свою жизнь уже сегодня! 💪✨\n\nПо вопросам поддержки обращайтесь: @aleksandrinsider"

def check_subscription(user_id):
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        return subscription and subscription.status == 'active'
    finally:
        session.close()

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
    await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)

@router.message(Command("update_profile"))
async def update_profile_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription or subscription.status != 'active':
        await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
        session.close()
        return
    session.close()
    # Отправить запрос в ИИ
    text = message.text.replace("/update_profile", "").strip()
    if text:
        prompt = f"Обнови мой профиль: {text}"
    else:
        prompt = "Помоги обновить профиль"
    context = []
    if os.getenv("LOCAL") == "1":
        context = context_store.get(f"context:{user_id}", [])
    else:
        try:
            context_data = r.get(f"context:{user_id}")
            if context_data:
                context = json.loads(context_data)
        except Exception as e:
            context = []
    response = chat_with_ai(prompt, context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": prompt, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    if os.getenv("LOCAL") == "1":
        context_store[f"context:{user_id}"] = context
    else:
        try:
            r.set(f"context:{user_id}", json.dumps(context))
        except Exception as e:
            pass

@router.message(Command("find_partners"))
async def find_partners_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription or subscription.status != 'active':
        await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
        session.close()
        return
    session.close()
    # Отправить запрос в ИИ
    context = []
    if os.getenv("LOCAL") == "1":
        context = context_store.get(f"context:{user_id}", [])
    else:
        try:
            context_data = r.get(f"context:{user_id}")
            if context_data:
                context = json.loads(context_data)
        except Exception as e:
            context = []
    response = chat_with_ai("Найди партнеров", context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": "Найди партнеров", "agent": response})
    if len(context) > 10:
        context = context[-10:]
    if os.getenv("LOCAL") == "1":
        context_store[f"context:{user_id}"] = context
    else:
        try:
            r.set(f"context:{user_id}", json.dumps(context))
        except Exception as e:
            pass

@router.message(Command("subscribe"))
async def subscribe_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if subscription and subscription.status == 'active':
        await message.bot.send_message(message.chat.id, "У вас уже активная подписка!")
        session.close()
        return
    # Создать платеж на 3000 RUB
    from payments import create_payment
    payment_url = create_payment(3000, "Подписка на премиум-функции бота (месяц)", user_id)
    await message.bot.send_message(message.chat.id, f"Оплатить с помощью ЮКАССА, СБЕР или БАНКОВСКОЙ КАРТЫ: {payment_url}\n\nПосле оплаты подписка активируется мгновенно! Добро пожаловать в мир эффективного управления задачами с ИИ! 💪")
    session.close()

@router.message()
async def chat_handler(message: Message):
    print(f"Received message from {message.from_user.id}: {message.text}")
    try:
        user_id = message.from_user.id
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, username=message.from_user.username)
            session.add(user)
            session.commit()
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        session.close()
        if not subscription or subscription.status != 'active':
            await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
            return
        # Все сообщения обрабатываются через ИИ
        if message.text.lower() == "очистить историю":
            context = []
            if os.getenv("LOCAL") == "1":
                context_store[f"context:{user_id}"] = context
            else:
                try:
                    r.set(f"context:{user_id}", json.dumps(context))
                except Exception as e:
                    print(f"Error saving context to Redis: {e}")
            await message.bot.send_message(message.chat.id, "История очищена.")
            return
        if os.getenv("LOCAL") == "1":
            context = context_store.get(f"context:{user_id}", [])
        else:
            try:
                context_data = r.get(f"context:{user_id}")
                if context_data:
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = []
            except Exception as e:
                print(f"Error loading context from Redis: {e}")
                context = []
        response = chat_with_ai(message.text, context, user_id)
        print(f"Response: {response}")
        # Сохранить контекст для продолжения
        context.append({"user": message.text, "agent": response})
        if os.getenv("LOCAL") == "1":
            context_store[f"context:{user_id}"] = context
        else:
            try:
                r.set(f"context:{user_id}", json.dumps(context))
            except Exception as e:
                print(f"Error saving context to Redis: {e}")
        await message.bot.send_message(message.chat.id, response)
    except Exception as e:
        print(f"Error in chat_handler: {e}")
        await message.bot.send_message(message.chat.id, "Извините, произошла ошибка. Попробуйте позже.")

@router.message(Command("dashboard"))
async def dashboard_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        await message.bot.send_message(message.chat.id, "Сначала зарегистрируйтесь, отправив /start")
        session.close()
        return
    session.close()
    
    # Generate dashboard URL
    base_url = os.getenv("WEBHOOK_URL", "http://localhost:8000").replace("/webhook", "")
    dashboard_url = f"{base_url}/dashboard?telegram_id={user_id}"
    await message.bot.send_message(message.chat.id, f"Ваш личный дашборд: {dashboard_url}")
