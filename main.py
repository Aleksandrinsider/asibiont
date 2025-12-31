import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import aiohttp_jinja2
import aiohttp_cors
import jinja2
from redis.asyncio import Redis
from aiohttp_session.redis_storage import RedisStorage
from aiohttp_session import get_session
from config import TELEGRAM_TOKEN, WEBHOOK_URL, TELEGRAM_BOT_USERNAME
from datetime import datetime
from handlers import router
from reminder_service import ReminderService
from ai_integration import AIIntegration, chat_with_ai, get_partners_list
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import os
import pytz
from datetime import timedelta
import hashlib
import hmac
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global Redis client
redis_client = None

def check_telegram_authentication(data):
    # Проверка авторизации от Telegram
    token = TELEGRAM_TOKEN
    if token.startswith('bot'):
        token = token[3:]  # Remove 'bot' prefix
    secret_key = hashlib.sha256(token.encode()).digest()
    data_check_string = '\n'.join(sorted([f'{k}={v}' for k, v in data.items() if k != 'hash']))
    hash_computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hash_computed == data.get('hash')


@aiohttp_jinja2.template('login.html')
async def login_handler(request):
    return web.HTTPFound('/dashboard')


# Temporary simple handler
async def simple_login_handler(request):
    return web.Response(text="Login page - Telegram auth available at /tg_auth")


async def auth_handler(request):
    data = request.query
    if check_telegram_authentication(data):
        user_id = int(data['id'])
        session_db = Session()
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, username=data.get('username'), first_name=data.get('first_name'))
            session_db.add(user)
            session_db.commit()
        
        # Increment login count if subscription exists
        subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
        if subscription:
            subscription.login_count += 1
            session_db.commit()
        
        session_db.close()
        
        session = await get_session(request)
        session['user_id'] = user_id
        return web.HTTPFound('/dashboard')
    else:
        return web.Response(text='Authentication failed', status=401)


async def logout_handler(request):
    session = await get_session(request)
    session.clear()
    return web.HTTPFound('/')


@aiohttp_jinja2.template('dashboard_new.html')
async def dashboard_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    
    logged_in = bool(user_id)
    
    if not logged_in:
        return {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'is_local': os.getenv('LOCAL') == '1'
        }
    
    # Получить задачи пользователя
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session_db.close()
        return {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None
        }
    
    logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")
    
    # Проверить подписку
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
    logger.info(f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}")
    
    if not subscription or subscription.status != 'active':
        logger.info("No active subscription, rendering no_subscription")
        session_db.close()
        return aiohttp_jinja2.render_template('no_subscription.html', request, {'bot_username': TELEGRAM_BOT_USERNAME})
    
    tasks = session_db.query(Task).filter_by(user_id=user.id).all()
    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
    interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at.desc()).limit(50).all() if user else []
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None
    partners = get_partners_list(user_id=user_id)
    # Add common interests
    if profile and profile.interests:
        user_interests = set(i.strip().lower() for i in profile.interests.split(','))
        for p in partners:
            if hasattr(p, 'interests') and p.interests:
                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                common = user_interests & partner_interests
                p.common_interests = ', '.join(common) if common else 'Нет общих интересов'
            else:
                p.common_interests = 'Интересы не указаны'
    
    # Set overdue flag and local time for tasks
    user_tz = pytz.UTC
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            user_tz = pytz.UTC
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    for task in tasks:
        if task.reminder_time:
            if task.reminder_time.tzinfo is None:
                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
            local_reminder = task.reminder_time.astimezone(user_tz)
            task.overdue = local_reminder < user_now and task.status == 'pending'
            task.reminder_time_local = local_reminder.strftime('%d.%m %H:%M')
        else:
            task.overdue = False
            task.reminder_time_local = None
    
    session_db.close()
    
    # Calculate metrics
    total_tasks = len(tasks)
    completed_tasks = len([t for t in tasks if t.status == 'completed'])
    pending_tasks = len([t for t in tasks if t.status == 'pending'])
    skipped_tasks = len([t for t in tasks if t.status == 'skipped'])
    
    # Format date and time in user's timezone
    base_now = datetime.now(pytz.UTC)
    user_now = base_now
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
            user_now = base_now.astimezone(user_tz)
        except pytz.exceptions.UnknownTimeZoneError:
            user_now = base_now
    months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    current_date = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
    current_time = user_now.strftime('%H:%M')
    
    # Format subscription end date
    formatted_end_date = None
    if subscription and subscription.end_date:
        end_dt = subscription.end_date
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=pytz.UTC)
        end_local = end_dt.astimezone(user_tz if user.timezone else pytz.UTC)
        formatted_end_date = f"{end_local.day} {months[end_local.month - 1]} {end_local.year}"
    
    # Calculate upcoming reminders
    upcoming_reminders = []
    if user:
        for task in tasks:
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                if task.reminder_time.astimezone(user_tz if user.timezone else pytz.UTC) > user_now and task.status == 'pending':
                    reminder_time_local = task.reminder_time.astimezone(user_tz if user.timezone else pytz.UTC).strftime("%H:%M")
                    upcoming_reminders.append(f"{task.title} в {reminder_time_local}")
    
    return {
        'logged_in': True,
        'tasks': tasks, 
        'user': user, 
        'profile': profile,
        'interactions': interactions,
        'partners': partners,
        'subscription': subscription,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'skipped_tasks': skipped_tasks,
        'current_date': current_date,
        'current_time': current_time,
        'formatted_end_date': formatted_end_date,
        'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
        'is_local': os.getenv('LOCAL') == '1'
    }


async def tasks_handler(request):
    return web.HTTPFound('/dashboard')


async def profile_handler(request):
    return web.HTTPFound('/dashboard')


async def chat_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"Chat handler called, session user_id: {user_id}")
    logger.info(f"Session keys: {list(session.keys())}")
    logger.info(f"Session data: {dict(session)}")
    
    if not user_id:
        logger.warning("No user_id in session for chat")
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    message = data.get('message', '')
    logger.info(f"Message received: {message}")

    # Load context from Redis
    context = []
    try:
        context_data = await redis_client.get(f"context:{user_id}")
        if context_data:
            context = json.loads(context_data.decode('utf-8'))
            logger.info(f"Loaded context with {len(context)} messages")
        else:
            logger.info("No context found in Redis")
    except Exception as e:
        logger.error(f"Error loading context: {e}")
        context = []

    # Save user message
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    logger.info(f"User found: {user is not None}")
    if user:
        interaction_user = Interaction(user_id=user.id, message_type='user', content=message)
        session_db.add(interaction_user)
        session_db.commit()

    # Get AI response
    try:
        logger.info(f"Calling chat_with_ai with user_id: {user_id}")
        response = await chat_with_ai(message, context, user_id)
        logger.info(f"AI response: {response[:100]}...")
    except Exception as e:
        logger.error(f"Error getting AI response: {e}")
        response = "Извините, произошла ошибка при обработке сообщения."

    # Save context back to Redis
    context.append({"user": message, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    try:
        await redis_client.set(f"context:{user_id}", json.dumps(context))
        logger.info("Context saved to Redis")
    except Exception as e:
        logger.error(f"Error saving context: {e}")

    # Save agent response
    if user:
        interaction_agent = Interaction(user_id=user.id, message_type='agent', content=response)
        session_db.add(interaction_agent)
        session_db.commit()

    session_db.close()

    return web.json_response({'response': response})


bot = Bot(token=TELEGRAM_TOKEN)


async def send_reminder(task_id, user_id):
    session = Session()
    task = session.query(Task).filter_by(id=task_id).first()
    session.close()
    if task and task.status == 'pending':
        await bot.send_message(user_id, f"Напоминание: {task.title}")


async def schedule_reminders(scheduler):
    try:
        session = Session()
        tasks = session.query(Task).filter(Task.reminder_time.isnot(None), Task.status == 'pending').all()
        session.close()
        for task in tasks:
            if task.reminder_time.tzinfo is None:
                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
            if task.reminder_time > datetime.now(pytz.UTC):
                scheduler.add_job(send_reminder, 'date', run_date=task.reminder_time, args=[task.id, task.user_id])
    except Exception as e:
        print(f"Error in schedule_reminders: {e}")


async def on_startup(app):
    global redis_client
    logger.info("Starting on_startup")
    try:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook set to: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
    # Initialize Redis
    from config import REDIS_URL
    redis_client = Redis.from_url(REDIS_URL)
    logger.info("Redis client initialized")
    # Initialize handlers Redis
    from handlers import init_redis
    await init_redis()
    logger.info("Handlers Redis initialized")
    # Инициализировать AI и ReminderService
    logger.info("Initializing AI and ReminderService")
    ai_service = AIIntegration()
    reminder_service = ReminderService(bot, ai_service)
    await reminder_service.start()
    logger.info("ReminderService started")



# Global app for Railway
app = web.Application()
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})
aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
aiohttp_session.setup(app, RedisStorage(redis_client))

async def yookassa_webhook(request):
    data = await request.json()
    if data.get('event') == 'payment.succeeded':
        payment = data['object']
        user_id = payment['metadata']['user_id']
        session = Session()
        user = session.query(User).filter_by(telegram_id=int(user_id)).first()
        if user:
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            if not subscription:
                subscription = Subscription(user_id=user.id)
                session.add(subscription)
            subscription.status = 'active'
            subscription.start_date = datetime.now(pytz.UTC)
            subscription.end_date = datetime.now(pytz.UTC) + timedelta(days=30)  # Месяц
            session.commit()
            await bot.send_message(int(user_id), "Подписка активирована! Теперь у вас доступ ко всем премиум-функциям.")
        session.close()
    return web.Response(text="OK")

# API handlers for dynamic updates
async def api_tasks_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session_db.close()
        return web.json_response({'error': 'User not found'}, status=404)
    
    tasks = session_db.query(Task).filter_by(user_id=user.id).all()
    session_db.close()
    
    # Format tasks
    user_tz = pytz.UTC
    if user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
        except:
            user_tz = pytz.UTC
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    
    tasks_data = []
    for task in tasks:
        task_data = {
            'id': task.id,
            'title': task.title,
            'status': task.status,
            'reminder_time_local': None,
            'overdue': False
        }
        if task.reminder_time:
            if task.reminder_time.tzinfo is None:
                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
            local_reminder = task.reminder_time.astimezone(user_tz)
            task_data['overdue'] = local_reminder < user_now and task.status == 'pending'
            task_data['reminder_time_local'] = local_reminder.strftime('%d.%m %H:%M')
        tasks_data.append(task_data)
    
    return web.json_response({'tasks': tasks_data})

async def api_partners_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    partners = get_partners_list(user_id=user_id)
    
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
    session_db.close()
    
    # Add common interests
    if profile and profile.interests:
        user_interests = set(i.strip().lower() for i in profile.interests.split(','))
        for p in partners:
            if hasattr(p, 'interests') and p.interests:
                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                common = user_interests & partner_interests
                p.common_interests = ', '.join(common) if common else 'Нет общих интересов'
            else:
                p.common_interests = 'Интересы не указаны'
    
    partners_data = []
    for p in partners:
        partners_data.append({
            'contact_info': getattr(p, 'contact_info', ''),
            'common_interests': getattr(p, 'common_interests', 'Нет общих интересов')
        })
    
    return web.json_response({'partners': partners_data})

async def api_profile_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
    session_db.close()
    
    profile_data = {}
    if profile:
        profile_data = {
            'username': user.username or 'unknown',
            'first_name': user.first_name or 'Пользователь',
            'skills': profile.skills or 'Не указаны',
            'interests': profile.interests or 'Не указаны',
            'goals': profile.goals or 'Не указаны',
            'city': profile.city or 'Не указан'
        }
    
    return web.json_response({'profile': profile_data})

async def api_reminders_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    tasks = session_db.query(Task).filter_by(user_id=user.id).all()
    session_db.close()
    
    user_tz = pytz.UTC
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
        except:
            user_tz = pytz.UTC
    base_now = datetime.now(pytz.UTC)
    user_now = base_now.astimezone(user_tz)
    
    upcoming_reminders = []
    for task in tasks:
        if task.reminder_time:
            if task.reminder_time.tzinfo is None:
                task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
            if task.reminder_time.astimezone(user_tz) > user_now and task.status == 'pending':
                reminder_time_local = task.reminder_time.astimezone(user_tz).strftime("%H:%M")
                upcoming_reminders.append(f"{task.title} в {reminder_time_local}")
    
    return web.json_response({'reminders': upcoming_reminders[:5]})

bot = Bot(token=TELEGRAM_TOKEN)

# Routes
app.router.add_get('/', login_handler)
app.router.add_get('/telegram_auth', auth_handler)
app.router.add_get('/logout', logout_handler)
app.router.add_get('/dashboard', dashboard_handler)
app.router.add_get('/tasks', tasks_handler)
app.router.add_get('/profile', profile_handler)
app.router.add_post('/chat', chat_handler)
app.router.add_static('/static', 'static')
app.router.add_post('/yookassa-webhook', yookassa_webhook)
# API routes for dynamic updates
app.router.add_get('/api/tasks', api_tasks_handler)
app.router.add_get('/api/partners', api_partners_handler)
app.router.add_get('/api/profile', api_profile_handler)
app.router.add_get('/api/reminders', api_reminders_handler)

# Setup for production
dp = Dispatcher()
dp.include_router(router)

webhook_requests_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
)
webhook_requests_handler.register(app, path="/webhook")

setup_application(app, dp, bot=bot)

# Add startup handler
app.on_startup.append(on_startup)


if __name__ == "__main__":
    port = int(os.getenv("PORT"))
    logger.info(f"Starting web app on port {port}")
    web.run_app(app, port=port, host='0.0.0.0')
