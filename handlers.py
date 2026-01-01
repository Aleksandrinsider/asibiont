import json
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from ai_integration import chat_with_ai
from models import Session, User, UserProfile, Subscription
from config import WEBHOOK_URL
from config import WEB_APP_URL
from redis.asyncio import Redis
from config import REDIS_URL, FREE_ACCESS_MODE

PREMIUM_DESCRIPTION = """🌟 Добро пожаловать в ASI Biont!

Для доступа ко всем функциям необходима подписка:
• Умное управление задачами с ИИ
• Поиск партнеров и единомышленников
• Персональные напоминания и отчеты
• Интеграция с веб-панелью

Оформите подписку командой /subscribe
"""

logger = logging.getLogger(__name__)

# Global Redis client
redis_client = None

async def init_redis(client):
    global redis_client
    redis_client = client

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/telegram_auth?id={message.from_user.id}&first_name={message.from_user.first_name or ''}&last_name={message.from_user.last_name or ''}&username={message.from_user.username or ''}&auth_date={int(message.date.timestamp())}&hash=placeholder"))]  # Placeholder hash, actual auth should be done properly
    ])
    await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION, reply_markup=keyboard)

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
    try:
        if redis_client:
            context_data = redis_client.get(f"context:{user_id}")
            if context_data:
                if isinstance(context_data, bytes):
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = json.loads(context_data)
        else:
            context = []
    except Exception as e:
            context = []
    response = await chat_with_ai(prompt, context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": prompt, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    try:
        if redis_client:
            redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
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
    try:
        if redis_client:
            context_data = redis_client.get(f"context:{user_id}")
            if context_data:
                if isinstance(context_data, bytes):
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = json.loads(context_data)
        else:
            context = []
    except Exception as e:
        context = []
    response = await chat_with_ai("Найди партнеров", context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": "Найди партнеров", "agent": response})
    if len(context) > 10:
        context = context[-10:]
    try:
        if redis_client:
            redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
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
    await message.bot.send_message(message.chat.id, f"Оплатите подписку удобным способом:\n\nСсылка на оплату (ЮКАССА, СБЕР или банковская карта): {payment_url}\n\nПосле оплаты подписка активируется мгновенно — никаких задержек!")
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
        if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
            await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
            return
        
        # Обработка геолокации
        if message.location:
            lat = message.location.latitude
            lon = message.location.longitude
            tf = TimezoneFinder()
            timezone_str = tf.timezone_at(lng=lon, lat=lat)
            if timezone_str:
                session = Session()
                user = session.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    user.timezone = timezone_str
                    session.commit()
                    await message.bot.send_message(message.chat.id, f"Ваш часовой пояс установлен на {timezone_str}. Теперь время будет рассчитываться автоматически!")
                session.close()
            else:
                await message.bot.send_message(message.chat.id, "Не удалось определить часовой пояс по вашим координатам. Попробуйте указать его вручную через диалог.")
            return
        
        # Все сообщения обрабатываются через ИИ
        if message.text.lower() == "очистить историю":
            context = []
            if redis_client:
                try:
                    await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                except Exception as e:
                    print(f"Error saving context to Redis: {e}")
            await message.bot.send_message(message.chat.id, "История очищена.")
            return
        context = []
        if redis_client:
            try:
                context_data = await redis_client.get(f"context:{user_id}")
                logger.debug(f"Redis get for user {user_id}: {context_data}")
                if context_data:
                    context = json.loads(context_data.decode('utf-8'))
                    logger.info(f"Loaded context for user {user_id}: {len(context)} messages")
                else:
                    context = []
                    logger.debug(f"No context found for user {user_id}")
            except Exception as e:
                logger.error(f"Error loading context from Redis: {e}", exc_info=True)
                context = []
        else:
            logger.warning("Redis client not initialized, context will not persist")
        response = await chat_with_ai(message.text, context, user_id)
        logger.debug(f"AI response generated for user {user_id}")
        # Сохранить контекст для продолжения
        context.append({"user": message.text, "agent": response})
        if len(context) > 10:
            context = context[-10:]  # Keep last 10 exchanges
        if redis_client:
            try:
                context_json = json.dumps(context).encode('utf-8')
                await redis_client.set(f"context:{user_id}", context_json)
                logger.info(f"Saved context for user {user_id}: {len(context)} messages")
            except Exception as e:
                logger.error(f"Error saving context to Redis: {e}", exc_info=True)
        else:
            logger.warning("Redis client not initialized, context not saved")
        
        try:
            await message.bot.send_message(message.chat.id, response)
            logger.info(f"Response sent successfully to user {user_id}")
        except Exception as e:
            logger.error(f"Error sending message to {message.chat.id}: {e}")
            await message.bot.send_message(message.chat.id, "Извините, произошла ошибка при отправке ответа.")
        # Записать взаимодействие для проактивных проверок
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            from models import Interaction
            interaction = Interaction(user_id=user.id, message_type='user', content=message.text)
            session.add(interaction)
            interaction = Interaction(user_id=user.id, message_type='ai', content=response)
            session.add(interaction)
            session.commit()
        session.close()
    except Exception as e:
        logger.error(f"Error in chat_handler for user {user_id}: {e}", exc_info=True)
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
    base_url = WEBHOOK_URL.replace("/webhook", "")
    dashboard_url = f"{base_url}/dashboard?telegram_id={user_id}"
    await message.bot.send_message(message.chat.id, f"Ваш личный дашборд: {dashboard_url}")
