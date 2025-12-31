import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
import aiohttp_jinja2
import aiohttp_cors
import jinja2
from redis.asyncio import Redis
import aiohttp_session
from aiohttp_session import get_session
from aiohttp_session.redis_storage import RedisStorage
from config import TELEGRAM_TOKEN, WEBHOOK_URL, TELEGRAM_BOT_USERNAME
from datetime import datetime
from handlers import router
from ai_integration import AIIntegration, chat_with_ai, get_partners_list
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import os
import pytz
from datetime import timedelta
import hashlib
import hmac
import json
import logging

# Trigger rebuild on Railway - update 4
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


async def dashboard_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    
    logged_in = bool(user_id)
    
    if not logged_in:
        response = aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': int(datetime.now().timestamp())
        })
        response.headers['Content-Security-Policy'] = "default-src *; script-src * 'unsafe-inline'; style-src * 'unsafe-inline'; img-src * data:; connect-src *;"
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
    # Получить задачи пользователя
    session_db = Session()
    user = session_db.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session_db.close()
        response = aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': int(datetime.now().timestamp())
        })
        response.headers['Content-Security-Policy'] = "default-src *; script-src * 'unsafe-inline'; style-src * 'unsafe-inline'; img-src * data:; connect-src *;"
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
    logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")
    
    # Проверить подписку
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
    logger.info(f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}")
    
    if not subscription or subscription.status != 'active':
        logger.info("No active subscription, rendering no_subscription")
        session_db.close()
        response = aiohttp_jinja2.render_template('no_subscription.html', request, {'bot_username': TELEGRAM_BOT_USERNAME})
        response.headers['Content-Security-Policy'] = "default-src *; script-src * 'unsafe-inline'; style-src * 'unsafe-inline'; img-src * data:; connect-src *;"
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
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
            task.reminder_time_local = local_reminder.strftime('%d.%m.%Y %H:%M')
            if task.overdue:
                delta = user_now - local_reminder
                days = delta.days
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                if days > 0:
                    task.overdue_text = f"просрочено на {days} дн."
                elif hours > 0:
                    task.overdue_text = f"просрочено на {hours} ч."
                elif minutes > 0:
                    task.overdue_text = f"просрочено на {minutes} мин."
                else:
                    task.overdue_text = "просрочено"
            else:
                task.overdue_text = None
        else:
            task.overdue = False
            task.reminder_time_local = None
            task.overdue_text = None
    
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
    
    response = aiohttp_jinja2.render_template('dashboard_new.html', request, {
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
        'timestamp': int(datetime.now().timestamp())
    })
    response.headers['Content-Security-Policy'] = "default-src *; script-src * 'unsafe-inline'; style-src * 'unsafe-inline'; img-src * data:; connect-src *;"
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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
    if redis_client:
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
    if redis_client:
        try:
            await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
            logger.info("Context saved to Redis")
        except Exception as e:
            logger.error(f"Error saving context: {e}")

    # Save agent response
    if user:
        interaction_agent = Interaction(user_id=user.id, message_type='ai', content=response)
        session_db.add(interaction_agent)
        session_db.commit()

    session_db.close()

    return web.json_response({'response': response})


async def clear_history_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"Clear history for user_id: {user_id}")
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    if redis_client:
        try:
            await redis_client.set(f"context:{user_id}", json.dumps([]).encode('utf-8'))
            logger.info("Context cleared")
        except Exception as e:
            logger.error(f"Error clearing context: {e}")

    return web.json_response({'message': 'History cleared'})


async def clear_db_handler(request):
    from config import LOCAL
    if not LOCAL:
        return web.json_response({'error': 'Not allowed in production'}, status=403)
    
    session_db = Session()
    try:
        # Clear all tables
        session_db.query(Interaction).delete()
        session_db.query(Task).delete()
        session_db.query(UserProfile).delete()
        session_db.query(Subscription).delete()
        session_db.query(User).delete()
        session_db.commit()
        logger.info("Database cleared")
        return web.json_response({'message': 'Database cleared'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing database: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def clear_user_tasks_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Clear user's tasks
        session_db.query(Task).filter_by(user_id=user.id).delete()
        session_db.commit()
        logger.info(f"User {user_id} tasks cleared")
        return web.json_response({'message': 'Tasks cleared'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing user tasks: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def clear_single_task_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        task = session_db.query(Task).filter_by(id=task_id, user_id=user.id).first()
        if not task:
            return web.json_response({'error': 'Task not found'}, status=404)
        
        session_db.delete(task)
        session_db.commit()
        logger.info(f"Task {task_id} deleted by user {user_id}")
        return web.json_response({'message': 'Task deleted'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error deleting task: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


bot = Bot(token=TELEGRAM_TOKEN)


# Global app for Railway
app = web.Application()

cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    )
})
aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))

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
            task_data['reminder_time_local'] = local_reminder.strftime('%d.%m.%Y %H:%M')
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
    subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None
    session_db.close()
    
    # Calculate current time and date
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
        end_local = end_dt.astimezone(user_tz if user and user.timezone else pytz.UTC)
        formatted_end_date = f"{end_local.day} {months[end_local.month - 1]} {end_local.year}"
    
    profile_data = {}
    if profile:
        profile_data = {
            'username': user.username or 'unknown',
        'first_name': user.first_name or '',
            'goals': profile.goals or 'Не указаны',
            'city': profile.city or 'Не указан'
        }
    
    return web.json_response({
        'profile': profile_data,
        'current_time': current_time,
        'current_date': current_date,
        'formatted_end_date': formatted_end_date
    })

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


async def on_startup(app):
    from config import REDIS_URL, LOCAL
    global redis_client
    if LOCAL:
        # In local mode, use dict for Redis
        redis_client = None
        logger.info("Using local mode without Redis")
    else:
        try:
            from redis.asyncio import Redis
            redis_client = Redis.from_url(REDIS_URL, decode_responses=False)
            logger.info("Redis client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Redis: {e}")
            redis_client = None
    
    # Initialize session storage
    if redis_client:
        from aiohttp_session.redis_storage import RedisStorage
        storage = RedisStorage(redis_client)
        logger.info("Session storage initialized with Redis")
    else:
        from aiohttp_session import SimpleCookieStorage
        storage = SimpleCookieStorage()
        logger.info("Session storage initialized with SimpleCookieStorage")
    
    aiohttp_session.setup(app, storage)
    
    # Set webhook
    if not LOCAL:
        webhook_url = WEBHOOK_URL
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.info("Local mode: skipping webhook setup")
    
    # Initialize handlers Redis
    from handlers import init_redis
    await init_redis(redis_client)
    logger.info("Handlers Redis initialized")


async def api_tasks_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        tasks = session_db.query(Task).filter_by(user_id=user.id).all()
        
        # Set overdue flag and local time for tasks
        user_tz = pytz.UTC
        if user and user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
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
                'overdue': False,
                'overdue_text': None
            }
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task_data['reminder_time_local'] = local_reminder.strftime('%d.%m.%Y %H:%M')
                task_data['overdue'] = local_reminder < user_now and task.status == 'pending'
                if task_data['overdue']:
                    delta = user_now - local_reminder
                    if delta.days > 0:
                        task_data['overdue_text'] = f'просрочено на {delta.days} дн.'
                    elif delta.seconds // 3600 > 0:
                        task_data['overdue_text'] = f'просрочено на {delta.seconds // 3600} ч.'
                    else:
                        task_data['overdue_text'] = f'просрочено на {delta.seconds // 60} мин.'
            tasks_data.append(task_data)
        
        return web.json_response({'tasks': tasks_data})
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


# Routes
app.router.add_get('/', login_handler)
app.router.add_get('/telegram_auth', auth_handler)
app.router.add_get('/logout', logout_handler)
app.router.add_get('/dashboard', dashboard_handler)
app.router.add_get('/tasks', tasks_handler)
app.router.add_get('/profile', profile_handler)
app.router.add_post('/chat', chat_handler)
app.router.add_post('/clear_history', clear_history_handler)
app.router.add_get('/clear_db', clear_db_handler)
app.router.add_post('/clear_user_tasks', clear_user_tasks_handler)
app.router.add_post('/clear_single_task', clear_single_task_handler)
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


print("Starting main - version 4")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting web app on port {port}")
    web.run_app(app, port=port, host='0.0.0.0')
