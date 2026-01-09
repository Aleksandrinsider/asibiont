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
from timezonefinder import TimezoneFinder

PREMIUM_DESCRIPTION = """👥 ASI Biont — находите людей для проектов, управляйте задачами через AI-агента

Ищете соавтора для стартапа? Партнёра для спортзала? Коллегу для совместного проекта? ASI Biont мгновенно находит единомышленников в вашем городе по интересам, навыкам и целям. Плюс — интеллектуальный ассистент возьмёт на себя планирование и напоминания.

За 3000₽/месяц вы получаете:

🔍 Умный поиск единомышленников
Укажите свои интересы — программирование, спорт, бизнес, творчество — и агент найдёт людей с похожими целями. Можно искать партнёров для проектов, компаньонов для хобби, коллег для коллабораций. Всё автоматически, с учётом вашего города и профиля.

🤝 Совместная работа над задачами
Делегируйте задачи партнёрам, принимайте задания от коллег, отслеживайте прогресс общих проектов. Идеально для команд, фрилансеров и любых совместных начинаний.

💬 Управление задачами через диалог
Просто скажите "напомни через час про встречу" или "добавь задачу позвонить клиенту завтра в 10:00" — агент всё сделает сам. Создаёт задачи, ставит напоминания, определяет приоритеты. Никаких сложных форм и меню.

⏰ Контекстные напоминания
Агент не просто пингует "напоминание о задаче", а объясняет полный контекст: что нужно сделать, почему это важно, что было сделано раньше. Работает с вашим часовым поясом и рабочим временем.

🎯 Автоматическое планирование
"Через 2 дня в 15:00 созвон с инвестором" — агент создаст задачу на точное время. Планируйте на дни и недели вперёд естественным языком, без календарей и форм.

🧠 Проактивный ассистент
Анализирует ваши задачи и цели, предлагает следующие шаги, находит связи между проектами, помогает расставить приоритеты. Работает с файлами — прикрепите документ, и агент проанализирует его.

📊 Веб-панель для контроля
Полная картина всех задач и контактов: фильтры, приоритеты, дедлайны, статистика выполнения. История всех взаимодействий, поиск людей по параметрам — всё визуально и удобно.

🔒 Полное шифрование
End-to-end шифрование всех данных. Ни мы, ни кто-либо ещё не видит содержимое ваших задач, переписки и контактов. Полная конфиденциальность.

Найдите своих людей и решайте задачи эффективнее — оформите подписку /subscribe

Поддержка: @aleksandrinsider"""

logger = logging.getLogger(__name__)

# Global Redis client
redis_client = None

async def init_redis(client):
    global redis_client
    redis_client = client

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    user_id = message.from_user.id
    
    # Check for duplicate message processing
    message_key = f"processed_message:{message.message_id}"
    if redis_client:
        if await redis_client.exists(message_key):
            logger.info(f"Duplicate /start message {message.message_id} ignored")
            return
        await redis_client.set(message_key, "1", ex=60)
    
    # Create user if doesn't exist
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
        logger.info(f"Created new user {user_id}")
    session.close()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
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
    user_id = message.from_user.id
    message_id = message.message_id
    logger.info(f"[HANDLER START] Received message {message_id} from user {user_id}: {message.text[:50] if message.text else 'None'}")
    
    try:
        # Check for duplicate message processing
        message_key = f"processed_message:{message_id}:{user_id}"
        if redis_client:
            logger.debug(f"[REDIS] Checking duplicate for key: {message_key}")
            is_duplicate = await redis_client.exists(message_key)
            if is_duplicate:
                logger.warning(f"[DUPLICATE] Message {message_id} from user {user_id} IGNORED (already processed)")
                return
            # Set key with short expiration
            await redis_client.set(message_key, "1", ex=60)
            logger.info(f"[REDIS] Marked message {message_id} as processed")
        else:
            logger.warning(f"[NO REDIS] Cannot prevent duplicates for message {message_id}")
        
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
                    logger.error(f"Error saving context to Redis: {e}")
            await message.bot.send_message(message.chat.id, "История очищена.")
            return
        
        # Handle delegation commands
        if message.text.lower().startswith("принять задачу "):
            task_id = message.text.split()[-1]
            try:
                from ai_integration import accept_delegated_task
                result = accept_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                await message.bot.send_message(message.chat.id, f"Ошибка: {str(e)}")
            return
        
        if message.text.lower().startswith("отклонить задачу "):
            task_id = message.text.split()[-1]
            try:
                from ai_integration import reject_delegated_task
                result = reject_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                await message.bot.send_message(message.chat.id, f"Ошибка: {str(e)}")
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
