import asyncio
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import aiohttp
from aiohttp import web
import aiohttp_jinja2
import aiohttp_cors
import jinja2
from redis.asyncio import Redis
import aiohttp_session
from aiohttp_session import get_session
from aiohttp_session.redis_storage import RedisStorage
from aiohttp_session import SimpleCookieStorage
from config import TELEGRAM_TOKEN, WEBHOOK_URL, TELEGRAM_BOT_USERNAME, REDIS_URL, PORT, FREE_ACCESS_MODE, ADMIN_SECRET, LOCAL
from datetime import datetime, timedelta
from handlers import router
from ai_integration import AIIntegration, chat_with_ai, get_partners_list
from reminder_service import ReminderService
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction
import logging

logger = logging.getLogger(__name__)

# Create tables if not exist
try:
    Base.metadata.create_all(engine)
    logger.info("Database tables created or already exist")
except Exception as e:
    logger.error(f"Failed to create database tables: {e}")

# Run migrations
def run_migrations():
    """Run database migrations"""
    from sqlalchemy import text, inspect
    try:
            
        session = Session()
        inspector = inspect(engine)
        
        # Check if user_profiles table exists first
        if 'user_profiles' not in inspector.get_table_names():
            logger.info("user_profiles table does not exist, skipping migration")
            session.close()
            return
            
        # Check if activity_streak column exists
        columns = [col['name'] for col in inspector.get_columns('user_profiles')]
        if 'activity_streak' not in columns:
            logger.info("Adding activity_streak column to user_profiles table")
            session.execute(text('ALTER TABLE user_profiles ADD COLUMN activity_streak INTEGER DEFAULT 0'))
            session.commit()
            logger.info("Migration: activity_streak column added successfully")
        else:
            logger.info("Migration: activity_streak column already exists")
            
        session.close()
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        
try:
    run_migrations()
    logger.info("Database migrations completed")
except Exception as e:
    logger.error(f"Failed to run migrations: {e}")

import os
import pytz
from datetime import timedelta
import hashlib
import hmac
import json
import logging

# Production logging configuration
log_level = logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global Redis client
redis_client = None

async def get_timezone_from_ip(ip_address):
    """Определяет timezone по IP адресу через ipapi.co"""
    try:
        # Игнорируем локальные IP
        if ip_address.startswith(('127.', '192.168.', '10.', '172.')):
            return 'Europe/Moscow', 'Moscow'  # По умолчанию для локальных
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://ipapi.co/{ip_address}/json/', timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    timezone = data.get('timezone')
                    city = data.get('city')
                    logger.info(f"Detected timezone: {timezone}, city: {city} for IP: {ip_address}")
                    return timezone if timezone else 'UTC', city
    except Exception as e:
        logger.error(f"Error getting timezone from IP {ip_address}: {e}")
    return 'UTC', None

async def get_user_avatar_url(bot, user_id):
    """Получает URL аватара пользователя из Telegram"""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        logger.info(f"User {user_id} has {photos.total_count} profile photos")
        if photos.total_count > 0:
            photo = photos.photos[0][-1]  # Берем самое большое фото
            file = await bot.get_file(photo.file_id)
            avatar_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file.file_path}"
            logger.info(f"Avatar URL for user {user_id}: {avatar_url}")
            return avatar_url
        else:
            logger.info(f"User {user_id} has no profile photos")
    except Exception as e:
        logger.error(f"Error getting user avatar for {user_id}: {e}")
    return None

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
    # Redirect to dashboard for unified experience
    return web.HTTPFound('/dashboard')


# Temporary simple handler
async def simple_login_handler(request):
    return web.Response(text="Login page - Telegram auth available at /tg_auth")


async def auth_handler(request):
    data = dict(request.query)
    logger.info(f"Auth handler called with data: {data}")
    
    if check_telegram_authentication(data):
        user_id = int(data['id'])
        logger.info(f"Authentication successful for user_id: {user_id}")
        
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.info(f"Creating new user with telegram_id: {user_id}")
                
                # Определяем timezone по IP
                ip_address = request.headers.get('X-Forwarded-For', request.remote).split(',')[0].strip()
                timezone, city = await get_timezone_from_ip(ip_address)
                logger.info(f"Auto-detected timezone: {timezone}, city: {city} for new user {user_id}")
                
                user = User(telegram_id=user_id, username=data.get('username'), first_name=data.get('first_name'), timezone=timezone)
                session_db.add(user)
                session_db.commit()
                
                # Создаем профиль с городом, если определили
                if city:
                    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
                    if not profile:
                        profile = UserProfile(user_id=user.id, city=city, contact_info=f"user{user_id}")
                        session_db.add(profile)
                    else:
                        profile.city = city
                    session_db.commit()
            else:
                logger.info(f"Found existing user: {user.id}")
            
            # Increment login count if subscription exists
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
            if subscription:
                subscription.login_count += 1
                session_db.commit()
        finally:
            session_db.close()
        
        session = await get_session(request)
        session['user_id'] = user_id
        logger.info(f"Session set with user_id: {user_id}, redirecting to /dashboard")
        
        response = web.HTTPFound('/dashboard')
        return response
    else:
        logger.error(f"Authentication failed for data: {data}")
        return web.Response(text='Authentication failed', status=401)


async def logout_handler(request):
    session = await get_session(request)
    session.clear()
    return web.HTTPFound('/')


@aiohttp_jinja2.template('dashboard_new.html')
async def dashboard_handler(request):
    logger.info(f"Dashboard handler called for path: {request.path}")
    session = await get_session(request)
    try:
        user_id = session.get('user_id')
        logger.info(f"User ID from session: {user_id}")
        
        logged_in = bool(user_id)
        
        if not logged_in:
            # Show login page in dashboard
            bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'Asibiont_bot'
            logger.info(f"Rendering login page with bot_username: {bot_user}")
            return aiohttp_jinja2.render_template('dashboard_new.html', request, {
                'logged_in': False,
                'bot_username': bot_user,
                'current_date': '',
                'current_time': '',
                'formatted_end_date': None,
                'timestamp': int(datetime.now().timestamp())
            })
        
        # Получить задачи пользователя
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'Asibiont_bot'
                return aiohttp_jinja2.render_template('dashboard_new.html', request, {
                    'logged_in': False,
                    'bot_username': bot_user,
                    'current_date': '',
                    'current_time': '',
                    'formatted_end_date': None,
                    'timestamp': int(datetime.now().timestamp())
                })
            
            logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")
            
            # Проверить подписку
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
            logger.info(f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}")
            
            if not subscription or subscription.status != 'active':
                logger.info("No active subscription, rendering no_subscription")
                bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'Asibiont_bot'
                return aiohttp_jinja2.render_template('no_subscription.html', request, {'bot_username': bot_user})
            
            tasks = session_db.query(Task).filter_by(user_id=user.id).all()
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
            
            # Проверяем timestamp очистки истории
            history_cleared_timestamp = None
            if redis_client:
                try:
                    timestamp_bytes = await redis_client.get(f"history_cleared_timestamp:{user_id}")
                    if timestamp_bytes:
                        history_cleared_timestamp = float(timestamp_bytes.decode('utf-8'))
                        logger.info(f"History cleared timestamp from Redis: {history_cleared_timestamp}")
                except Exception as e:
                    logger.error(f"Error checking history_cleared_timestamp: {e}")
            else:
                # Fallback на session если Redis недоступен
                history_cleared_timestamp = session_req.get('history_cleared_timestamp')
                logger.info(f"History cleared timestamp from session: {history_cleared_timestamp}")
            
            # Берем последние 50 сообщений, но фильтруем по timestamp очистки
            if user:
                all_interactions = list(reversed(session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.id.desc()).limit(50).all()))
                if history_cleared_timestamp:
                    # Фильтруем только сообщения после очистки
                    from datetime import timezone as dt_timezone
                    filtered_interactions = []
                    for i in all_interactions:
                        try:
                            # Если created_at naive (без tzinfo), считаем его UTC и просто берем timestamp
                            # Если с tzinfo, используем его timestamp
                            if i.created_at.tzinfo is None:
                                # Naive datetime - интерпретируем как UTC напрямую через replace
                                interaction_ts = i.created_at.replace(tzinfo=dt_timezone.utc).timestamp()
                            else:
                                interaction_ts = i.created_at.timestamp()
                            
                            logger.info(f"Interaction ID {i.id}: created_at={i.created_at}, timestamp={interaction_ts}, clear_timestamp={history_cleared_timestamp}, include={interaction_ts > history_cleared_timestamp}")
                            
                            if interaction_ts > history_cleared_timestamp:
                                filtered_interactions.append(i)
                        except Exception as e:
                            logger.error(f"Error processing interaction {i.id} timestamp: {e}")
                            # В случае ошибки НЕ включаем сообщение (безопаснее скрыть)
                    
                    interactions = filtered_interactions
                    logger.info(f"Filtered {len(interactions)} interactions from {len(all_interactions)} total after timestamp {history_cleared_timestamp}")
                else:
                    interactions = all_interactions
                    logger.info(f"Loaded {len(interactions)} interactions (no filtering)")
            else:
                interactions = []
            
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None
            
            # Получить контакты по делегированию
            delegating_to_me = []  # Люди, которые делегировали мне задачи
            delegating_by_me = []  # Люди, которым я делегировал задачи
            
            try:
                # Люди, которые делегировали мне задачи (я получаю задачи от них)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username == user.username,
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegator_ids = set()
                for task in delegated_tasks:
                    if task.delegated_by and task.delegated_by not in delegator_ids:
                        delegator_ids.add(task.delegated_by)
                        delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                        if delegator and delegator.id != user.id:
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'reason': f'делегировал {len([t for t in delegated_tasks if t.delegated_by == delegator.id])} задач'
                            })
                
                # Люди, которым я делегировал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_by == user.id,
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(User.username.ilike(task.delegated_to_username.replace('@', ''))).first()
                        if delegatee and delegatee.id != user.id:
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'reason': f'я делегировал {len([t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username])} задач'
                            })
            
            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []
                
        finally:
            session_db.close()
        try:
            partners = get_partners_list(user_id=user_id)
        except Exception as e:
            logger.error(f"Error getting partners: {e}")
            partners = []
        
        # Add common interests, skills, goals and recommendation reason
        if profile and partners:
            user_interests = set(i.strip().lower() for i in profile.interests.split(',')) if profile.interests else set()
            user_skills = set(s.strip().lower() for s in profile.skills.split(',')) if profile.skills else set()
            user_goals = set(g.strip().lower() for g in profile.goals.split(',')) if profile.goals else set()
            
            # Получаем список контактов, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                import re
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)
            
            for p in partners:
                # Common interests
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                    common = user_interests & partner_interests
                    p.common_interests = ', '.join(common) if common else None
                else:
                    p.common_interests = None
                
                # Common skills
                if p.skills:
                    partner_skills = set(s.strip().lower() for s in p.skills.split(','))
                    common_skills = user_skills & partner_skills
                    p.common_skills = ', '.join(common_skills) if common_skills else None
                else:
                    p.common_skills = None
                
                # Common goals
                if p.goals:
                    partner_goals = set(g.strip().lower() for g in p.goals.split(','))
                    common_goals = user_goals & partner_goals
                    p.common_goals = ', '.join(common_goals) if common_goals else None
                else:
                    p.common_goals = None
                
                # Determine recommendation reason
                reasons = []
                if p.contact_info:
                    username = p.contact_info.replace('@', '')
                    if username in contacted_usernames:
                        reasons.append('уже общались')
                if p.common_skills:
                    reasons.append('общие навыки')
                if p.common_interests:
                    reasons.append('общие интересы')
                if p.common_goals:
                    reasons.append('общие цели')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('из вашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'
        user_tz = pytz.UTC
        if user and user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC
        
        # Использовать CURRENT_DATE из конфига если установлен, иначе текущее время
        from config import CURRENT_DATE
        if CURRENT_DATE:
            base_now = CURRENT_DATE.replace(tzinfo=pytz.UTC) if CURRENT_DATE.tzinfo is None else CURRENT_DATE
        else:
            base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)
        
        current_time = user_now.strftime('%H:%M')
        
        months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
        current_date = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        
        for task in tasks:
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task.overdue = local_reminder < user_now and task.status == 'pending'
                task.reminder_time_local = local_reminder.strftime('%d.%m.%Y %H:%M')
                if task.overdue:
                    delta = user_now - local_reminder
                    total_seconds = int(delta.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
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
        
        # Calculate metrics
        total_tasks = len(tasks)
        completed_tasks = len([t for t in tasks if t.status == 'completed'])
        pending_tasks = len([t for t in tasks if t.status == 'pending'])
        skipped_tasks = len([t for t in tasks if t.status == 'skipped'])
        
        # Format date and time in user's timezone
        from config import CURRENT_DATE
        if CURRENT_DATE:
            base_now = CURRENT_DATE.replace(tzinfo=pytz.UTC) if CURRENT_DATE.tzinfo is None else CURRENT_DATE
        else:
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
            formatted_end_date = f"{end_local.day:02d}.{end_local.month:02d}.{end_local.year}"
        
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
        
        # Преобразуем задачи в словари для JSON сериализации
        tasks_dict = []
        for task in tasks:
            # Подготовим reminder_time в ISO формате для JavaScript
            reminder_time_iso = None
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                reminder_time_iso = local_reminder.isoformat()
            
            task_dict = {
                'id': task.id,
                'title': task.title,
                'description': task.description or '',
                'status': task.status,
                'reminder_time': reminder_time_iso,  # Для группировки в JS
                'reminder_time_local': getattr(task, 'reminder_time_local', None),
                'overdue': getattr(task, 'overdue', False),
                'overdue_text': getattr(task, 'overdue_text', None)
            }
            tasks_dict.append(task_dict)
        
        # Get user avatar URL
        user_avatar_url = None
        if 'bot' in request.app:
            user_avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
            # Add random parameter to prevent caching
            if user_avatar_url:
                import random
                user_avatar_url += f"?r={random.randint(100000, 999999)}"
        
        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': True,
            'tasks': tasks_dict,
            'user': user, 
            'profile': profile,
            'interactions': interactions,
            'partners': partners,
            'delegating_to_me': delegating_to_me,
            'delegating_by_me': delegating_by_me,
            'subscription': subscription,
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'pending_tasks': pending_tasks,
            'skipped_tasks': skipped_tasks,
            'current_date': current_date,
            'current_time': current_time,
            'formatted_end_date': formatted_end_date,
            'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
            'timestamp': int(datetime.now().timestamp()),
            'bot_username': TELEGRAM_BOT_USERNAME.replace('@', ''),
            'user_avatar_url': user_avatar_url
        })
    except Exception as e:
        logger.error(f"Unexpected error in dashboard_handler: {e}", exc_info=True)
        bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'Asibiont_bot'
        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'bot_username': bot_user,
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': int(datetime.now().timestamp())
        })


async def tasks_handler(request):
    return web.HTTPFound('/dashboard')


async def profile_handler(request):
    return web.HTTPFound('/dashboard')


async def chat_handler(request):
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        logger.info(f"Chat handler called, session user_id: {user_id}")
        logger.info(f"Session keys: {list(session.keys())}")
        logger.info(f"Session data: {dict(session)}")
        
        if not user_id:
            logger.warning("No user_id in session for chat")
            return web.json_response({'error': 'Not authenticated'}, status=401)

        data = await request.post()
        message = data.get('message', '')
        file = data.get('file')
        file_content = None
        if file:
            # Read file content
            file_content = file.file.read().decode('utf-8', errors='ignore')  # For text files, ignore errors for binary
            logger.info(f"File received: {file.filename}, size: {len(file_content)}")
        logger.info(f"Message received: {message}")

        # Load context from Redis
        context = []
        if redis_client:
            try:
                context_data = await redis_client.get(f"context:{user_id}")
                if context_data:
                    full_context = json.loads(context_data.decode('utf-8'))
                    # Filter messages from last 24 hours
                    from datetime import datetime
                    cutoff_time = datetime.utcnow().timestamp() - 24 * 3600
                    context = [msg for msg in full_context if datetime.fromisoformat(msg.get("timestamp", "2000-01-01T00:00:00")).timestamp() > cutoff_time]
                    logger.info(f"Loaded and filtered context with {len(context)} messages from last 24h")
                else:
                    logger.info("No context found in Redis")
            except Exception as e:
                logger.error(f"Error loading context: {e}")
                context = []

        # Save user message
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            logger.info(f"User found: {user is not None}")
            if user:
                content = message
                if file:
                    content += f" [Файл: {file.filename}]"
                interaction_user = Interaction(user_id=user.id, message_type='user', content=content)
                session_db.add(interaction_user)
                session_db.commit()

            # Get AI response
            try:
                logger.info(f"Calling chat_with_ai with user_id: {user_id}")
                response = await chat_with_ai(message, context, user_id, file_content)
                logger.info(f"AI response: {response[:100]}...")
            except Exception as e:
                logger.error(f"Error getting AI response: {e}", exc_info=True)
                response = f"Ошибка: {str(e)}"

            # Save context back to Redis with timestamp
            from datetime import datetime
            context.append({
                "user": message, 
                "agent": response, 
                "timestamp": datetime.utcnow().isoformat()
            })
            # Keep only messages from last 24 hours
            cutoff_time = datetime.utcnow().timestamp() - 24 * 3600
            context = [msg for msg in context if datetime.fromisoformat(msg.get("timestamp", "2000-01-01T00:00:00")).timestamp() > cutoff_time]
            # Limit to last 50 messages to prevent excessive storage
            if len(context) > 50:
                context = context[-50:]
            if redis_client:
                try:
                    await redis_client.setex(f"context:{user_id}", 24*3600, json.dumps(context).encode('utf-8'))  # Expire in 24 hours
                    # НЕ удаляем timestamp - новые сообщения будут после него и будут видны
                    logger.info(f"Context saved to Redis with {len(context)} messages")
                except Exception as e:
                    logger.error(f"Error saving context: {e}")

            # Save agent response
            if user:
                interaction_agent = Interaction(user_id=user.id, message_type='ai', content=response)
                session_db.add(interaction_agent)
                session_db.commit()
        finally:
            session_db.close()

        return web.json_response({'response': response})
    except Exception as e:
        logger.error(f"Unexpected error in chat_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def clear_history_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"Clear history for user_id: {user_id}")
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    # Очищаем контекст в Redis и сохраняем timestamp
    from datetime import datetime, timezone
    clear_timestamp = datetime.now(timezone.utc).timestamp()
    
    if redis_client:
        try:
            await redis_client.set(f"context:{user_id}", json.dumps([]).encode('utf-8'))
            # Сохраняем timestamp очистки на 24 часа
            await redis_client.setex(f"history_cleared_timestamp:{user_id}", 24*3600, str(clear_timestamp))
            logger.info(f"Context cleared and history_cleared_timestamp set to {clear_timestamp}")
        except Exception as e:
            logger.error(f"Error clearing context: {e}")
    else:
        # Если Redis недоступен, используем session
        session['history_cleared_timestamp'] = clear_timestamp
        logger.info(f"History cleared timestamp set in session: {clear_timestamp}")

    return web.json_response({'success': True, 'message': 'History cleared'})

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


async def complete_task_handler(request):
    """Завершает задачу по ID"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)
    
    from ai_integration import complete_task
    try:
        result = complete_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} completed by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error completing task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def clear_old_tasks_handler(request):
    """Admin endpoint to clear old test tasks"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)
    
    session_db = Session()
    try:
        cutoff_date = datetime(2026, 1, 1, tzinfo=pytz.UTC)
        old_tasks = session_db.query(Task).filter(Task.reminder_time < cutoff_date).all()
        
        count = len(old_tasks)
        for task in old_tasks:
            session_db.delete(task)
        
        session_db.commit()
        logger.info(f"Cleared {count} old tasks")
        return web.json_response({'message': f'Cleared {count} old tasks'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing old tasks: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def clear_database_handler(request):
    """Admin endpoint to clear entire database"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)
    
    session_db = Session()
    try:
        # Delete all data
        session_db.query(Interaction).delete()
        session_db.query(Task).delete()
        session_db.query(UserProfile).delete()
        session_db.query(Subscription).delete()
        session_db.query(User).delete()
        
        session_db.commit()
        logger.info("Database cleared successfully")
        return web.json_response({'message': 'Database cleared successfully'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing database: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def clear_redis_handler(request):
    """Admin endpoint to clear Redis cache"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)
    
    if not redis_client:
        return web.json_response({'error': 'Redis not configured'}, status=400)
    
    try:
        await redis_client.flushdb()
        logger.info("Redis cleared successfully")
        return web.json_response({'message': 'Redis cleared successfully'})
    except Exception as e:
        logger.error(f"Error clearing Redis: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def direct_login_handler(request):
    """Direct login endpoint for testing (bypasses Telegram auth)"""
    user_id = request.query.get('user_id')
    if not user_id:
        return web.json_response({'error': 'user_id required'}, status=400)
    
    try:
        user_id = int(user_id)
    except ValueError:
        return web.json_response({'error': 'Invalid user_id'}, status=400)
    
    # Check user exists and has active subscription
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found in database'}, status=404)
        
        subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription or subscription.status != 'active':
            return web.json_response({'error': 'User has no active subscription'}, status=403)
    finally:
        session_db.close()
    
    # Set session
    session = await get_session(request)
    session['user_id'] = user_id
    logger.info(f"Direct login successful for user_id: {user_id}")
    
    response = web.HTTPFound('/dashboard')
    return response


try:
    if TELEGRAM_TOKEN:
        bot = Bot(token=TELEGRAM_TOKEN)
        logger.info("Bot created successfully")
    else:
        bot = None
        logger.info("Bot not created (no token)")
except Exception as e:
    logger.error(f"Failed to create bot: {e}", exc_info=True)
    bot = None


# Global app for Railway
app = web.Application()

# Add bot to app
if bot:
    app['bot'] = bot

# Middleware to add CSP headers and disable cache for static files
@web.middleware
async def csp_middleware(request, handler):
    response = await handler(request)
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.deepseek.com; frame-src https://oauth.telegram.org;"
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

app.middlewares.append(csp_middleware)

# cors = aiohttp_cors.setup(app, defaults={
#     "*": aiohttp_cors.ResourceOptions(
#         allow_credentials=True,
#         expose_headers="*",
#         allow_headers="*",
#         allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
#     )
# })
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
            
            # Если подписка еще активна, продлеваем от end_date, иначе от текущей даты
            now = datetime.now(pytz.UTC)
            if subscription.end_date and subscription.end_date > now:
                subscription.end_date = subscription.end_date + timedelta(days=30)
            else:
                subscription.end_date = now + timedelta(days=30)
            
            session.commit()
            await bot.send_message(int(user_id), "Подписка активирована! Теперь у вас доступ ко всем премиум-функциям.")
        session.close()
    return web.Response(text="OK")

# API handlers for dynamic updates


async def api_partners_handler(request):
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        try:
            partners = get_partners_list(user_id=user_id)
        except Exception as e:
            logger.error(f"Error getting partners: {e}")
            partners = []
        
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
            interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at).all() if user else []
            
            # Получить контакты по делегированию
            delegating_to_me = []  # Люди, которые делегировали мне задачи
            delegating_by_me = []  # Люди, которым я делегировал задачи
            
            try:
                # Люди, которые делегировали мне задачи (я получаю задачи от них)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username == user.username,
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegator_ids = set()
                for task in delegated_tasks:
                    if task.delegated_by and task.delegated_by not in delegator_ids:
                        delegator_ids.add(task.delegated_by)
                        delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                        if delegator and delegator.id != user.id:
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'reason': f'делегировал {len([t for t in delegated_tasks if t.delegated_by == delegator.id])} задач'
                            })
                
                # Люди, которым я делегировал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_by == user.id,
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(User.username.ilike(task.delegated_to_username.replace('@', ''))).first()
                        if delegatee and delegatee.id != user.id:
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'reason': f'я делегировал {len([t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username])} задач'
                            })
            
            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []
                
        finally:
            session_db.close()
        
        # Add common interests, skills, goals and recommendation reason
        if profile and partners:
            user_interests = set(i.strip().lower() for i in profile.interests.split(',')) if profile.interests else set()
            user_skills = set(s.strip().lower() for s in profile.skills.split(',')) if profile.skills else set()
            user_goals = set(g.strip().lower() for g in profile.goals.split(',')) if profile.goals else set()
            
            # Получаем список контактов, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                import re
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)
            
            for p in partners:
                # Common interests
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                    common = user_interests & partner_interests
                    p.common_interests = ', '.join(common) if common else None
                else:
                    p.common_interests = None
                
                # Common skills
                if p.skills:
                    partner_skills = set(s.strip().lower() for s in p.skills.split(','))
                    common_skills = user_skills & partner_skills
                    p.common_skills = ', '.join(common_skills) if common_skills else None
                else:
                    p.common_skills = None
                
                # Common goals
                if p.goals:
                    partner_goals = set(g.strip().lower() for g in p.goals.split(','))
                    common_goals = user_goals & partner_goals
                    p.common_goals = ', '.join(common_goals) if common_goals else None
                else:
                    p.common_goals = None
                
                # Determine recommendation reason
                reasons = []
                if p.contact_info:
                    username = p.contact_info.replace('@', '')
                    if username in contacted_usernames:
                        reasons.append('уже общались')
                if p.common_skills:
                    reasons.append('общие навыки')
                if p.common_interests:
                    reasons.append('общие интересы')
                if p.common_goals:
                    reasons.append('общие цели')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('из вашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'
        
        partners_data = []
        for p in partners:
            partners_data.append({
                'contact_info': getattr(p, 'contact_info', ''),
                'city': getattr(p, 'city', None),
                'common_interests': getattr(p, 'common_interests', None),
                'common_skills': getattr(p, 'common_skills', None),
                'common_goals': getattr(p, 'common_goals', None),
                'recommendation_reason': getattr(p, 'recommendation_reason', 'подходящий контакт'),
                'type': 'recommended'
            })
        
        # Add delegating contacts
        for contact in delegating_to_me:
            partners_data.append({
                'contact_info': contact['username'],
                'first_name': contact['first_name'],
                'reason': contact['reason'],
                'type': 'delegating_to_me'
            })
        
        for contact in delegating_by_me:
            partners_data.append({
                'contact_info': contact['username'],
                'first_name': contact['first_name'],
                'reason': contact['reason'],
                'type': 'delegating_by_me'
            })
        
        return web.json_response({'partners': partners_data})
    except Exception as e:
        logger.error(f"Unexpected error in api_partners_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)

async def api_profile_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
        subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None
    finally:
        session_db.close()
    
    # Calculate current time and date
    if CURRENT_DATE:
        base_now = CURRENT_DATE.replace(tzinfo=pytz.UTC) if CURRENT_DATE.tzinfo is None else CURRENT_DATE
    else:
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
    
    # Use profile.current_time if set
    if profile and profile.current_time:
        current_time = profile.current_time
    
    # Format subscription end date
    formatted_end_date = None
    if subscription and subscription.end_date:
        end_dt = subscription.end_date
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=pytz.UTC)
        end_local = end_dt.astimezone(user_tz if user and user.timezone else pytz.UTC)
        formatted_end_date = f"{end_local.day:02d}.{end_local.month:02d}.{end_local.year}"
    
    profile_data = {}
    if profile:
        profile_data = {
            'username': user.username or 'unknown',
            'first_name': user.first_name or '',
            'goals': profile.goals or 'Не указаны',
            'skills': profile.skills or 'Не указаны',
            'interests': profile.interests or 'Не указаны',
            'city': profile.city or 'Не указан',
            'company': profile.company or 'Не указана',
            'position': profile.position or 'Не указана'
        }
    
    # Get user avatar URL
    user_avatar_url = None
    if 'bot' in request.app:
        user_avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
        # Add random parameter to prevent caching
        if user_avatar_url:
            import random
            user_avatar_url += f"?r={random.randint(100000, 999999)}"
    
    return web.json_response({
        'profile': profile_data,
        'current_time': current_time,
        'current_date': current_date,
        'formatted_end_date': formatted_end_date,
        'user_avatar_url': user_avatar_url
    })

async def api_reminders_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not logged in'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        tasks = session_db.query(Task).filter_by(user_id=user.id).all()
    finally:
        session_db.close()
    
    user_tz = pytz.UTC
    if user and user.timezone:
        try:
            user_tz = pytz.timezone(user.timezone)
        except:
            user_tz = pytz.UTC
    if CURRENT_DATE:
        base_now = CURRENT_DATE.replace(tzinfo=pytz.UTC) if CURRENT_DATE.tzinfo is None else CURRENT_DATE
    else:
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
        # Custom storage to handle invalid JSON cookies
        class SafeSimpleCookieStorage(SimpleCookieStorage):
            async def load_session(self, request):
                from aiohttp_session import Session as AiohttpSession
                cookie = self.load_cookie(request)
                if cookie is None:
                    return await self.new_session()
                try:
                    data = self._decoder(cookie)
                    return AiohttpSession(None, data=data, new=False, max_age=self.max_age)
                except json.JSONDecodeError:
                    # Invalid cookie, create new session
                    return await self.new_session()
        
        storage = SafeSimpleCookieStorage()
        logger.info("Session storage initialized with SafeSimpleCookieStorage")
    
    aiohttp_session.setup(app, storage)
    logger.info("Session middleware configured successfully")
    
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
        if CURRENT_DATE:
            base_now = CURRENT_DATE.replace(tzinfo=pytz.UTC) if CURRENT_DATE.tzinfo is None else CURRENT_DATE
        else:
            base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)
        
        # Use profile.current_time if set
        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.current_time:
            # Parse current_time as HH:MM and set to today's date in user timezone
            try:
                hours, minutes = map(int, profile.current_time.split(':'))
                user_now = user_now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
            except ValueError:
                pass  # Keep default user_now
        
        tasks_data = []
        for task in tasks:
            # Format task title based on delegation
            title = task.title
            if task.delegated_by and task.delegated_by != user.id:
                # Task delegated TO me
                delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                if delegator:
                    title = f"{task.title} от @{delegator.username}"
            elif task.delegated_to_username:
                # Task delegated BY me to someone else
                title = f"{task.title} для @{task.delegated_to_username}"
            
            task_data = {
                'id': task.id,
                'title': title,
                'status': task.status,
                'reminder_time': None,
                'reminder_time_local': None,
                'overdue': False,
                'overdue_text': None,
                'is_delegated': task.delegated_by is not None or task.delegated_to_username is not None,
                'delegation_status': task.delegation_status if hasattr(task, 'delegation_status') else None
            }
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task_data['reminder_time'] = local_reminder.isoformat()
                task_data['reminder_time_local'] = local_reminder.strftime('%d.%m.%Y %H:%M')
                task_data['overdue'] = local_reminder < user_now and task.status == 'pending'
                if task_data['overdue']:
                    delta = user_now - local_reminder
                    total_seconds = int(delta.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    if days > 0:
                        task_data['overdue_text'] = f'просрочено на {days} дн.'
                    elif hours > 0:
                        task_data['overdue_text'] = f'просрочено на {hours} ч.'
                    else:
                        task_data['overdue_text'] = f'просрочено на {minutes} мин.'
            tasks_data.append(task_data)
        
        return web.json_response({'tasks': tasks_data})
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()

async def api_interactions_handler(request):
    """API для получения истории чата"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at).all()
        
        # Get history cleared timestamp from Redis
        history_cleared_timestamp = 0
        if redis_client:
            cleared_data = await redis_client.get(f"history_cleared_timestamp:{user_id}")
            if cleared_data:
                history_cleared_timestamp = float(cleared_data.decode('utf-8'))
        
        # Filter interactions based on cleared timestamp
        from datetime import timezone as dt_timezone
        filtered_interactions = [
            i for i in interactions 
            if i.created_at.replace(tzinfo=dt_timezone.utc).timestamp() > history_cleared_timestamp
        ]
        
        interactions_data = []
        for interaction in filtered_interactions:
            interactions_data.append({
                'content': interaction.content,
                'message_type': interaction.message_type,
                'created_at': interaction.created_at.isoformat()
            })
        
        return web.json_response({'interactions': interactions_data})
    except Exception as e:
        logger.error(f"Error fetching interactions: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()

async def update_timezone_handler(request):
    """Обновляет timezone пользователя через веб-панель"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        if not user_id:
            return web.json_response({'status': 'error', 'message': 'Not authenticated'}, status=401)
        
        data = await request.json()
        timezone = data.get('timezone')
        
        if not timezone:
            return web.json_response({'status': 'error', 'message': 'Timezone required'}, status=400)
        
        # Проверка валидности timezone
        try:
            pytz.timezone(timezone)
        except:
            return web.json_response({'status': 'error', 'message': 'Invalid timezone'}, status=400)
        
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(id=user_id).first()
            if user:
                user.timezone = timezone
                session_db.commit()
                logger.info(f"Updated timezone for user {user_id} to {timezone}")
        finally:
            session_db.close()
        
        return web.json_response({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error updating timezone: {e}")
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)

async def extend_subscription_handler(request):
    """Создает платеж для продления подписки"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')
    
    if not user_id:
        return web.Response(text='Unauthorized', status=401)
    
    try:
        from payments import create_payment
        # Создаем платеж на 30 дней (можно настроить сумму)
        payment_url = create_payment(
            amount="3000.00",  # Цена за месяц
            description="Продление подписки ASI Biont на 30 дней",
            user_id=user_id
        )
        # Редирект на страницу оплаты
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return web.Response(text=f'Ошибка создания платежа: {str(e)}', status=500)


async def test_payment_handler(request):
    """Тестовый эндпоинт для симуляции успешной оплаты (только для разработки)"""
    # Отключено в продакшене
    return web.Response(text='Not available in production', status=403)
    
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')
    
    if not user_id:
        return web.Response(text='Unauthorized', status=401)
    
    try:
        session_db = Session()
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
            if not subscription:
                subscription = Subscription(user_id=user.id)
                session_db.add(subscription)
            
            subscription.status = 'active'
            subscription.start_date = datetime.now(pytz.UTC)
            
            # Если подписка еще активна, продлеваем от end_date, иначе от текущей даты
            now = datetime.now(pytz.UTC)
            old_end_date = subscription.end_date
            if subscription.end_date and subscription.end_date > now:
                subscription.end_date = subscription.end_date + timedelta(days=30)
            else:
                subscription.end_date = now + timedelta(days=30)
            
            session_db.commit()
            
            # Форматируем даты для отображения
            user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
            new_end = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y')
            old_end = old_end_date.astimezone(user_tz).strftime('%d.%m.%Y') if old_end_date else 'нет'
            
            logger.info(f"Test payment: extended subscription for user {user_id} from {old_end} to {new_end}")
        session_db.close()
        return web.HTTPFound('/dashboard')
    except Exception as e:
        logger.error(f"Error in test payment: {e}")
        return web.Response(text=f'Ошибка: {str(e)}', status=500)


# Routes
app.router.add_get('/', login_handler)
app.router.add_get('/tg_auth', auth_handler)
app.router.add_get('/telegram_auth', auth_handler)  # Keep old route for compatibility
app.router.add_get('/logout', logout_handler)
app.router.add_get('/dashboard', dashboard_handler)
app.router.add_get('/tasks', tasks_handler)
app.router.add_get('/profile', profile_handler)
app.router.add_post('/chat', chat_handler)
app.router.add_post('/clear_history', clear_history_handler)

app.router.add_post('/clear_user_tasks', clear_user_tasks_handler)
app.router.add_post('/clear_single_task', clear_single_task_handler)
app.router.add_post('/complete_task', complete_task_handler)
app.router.add_post('/update_timezone', update_timezone_handler)
app.router.add_get('/extend_subscription', extend_subscription_handler)
app.router.add_get('/test_payment', test_payment_handler)  # Тестовый эндпоинт для симуляции оплаты
app.router.add_get('/clear_old_tasks', clear_old_tasks_handler)
app.router.add_get('/clear_database', clear_database_handler)
app.router.add_get('/clear_redis', clear_redis_handler)
app.router.add_get('/direct_login', direct_login_handler)  # Тестовый логин (только для пользователей с активной подпиской)
app.router.add_static('/static', 'static')
app.router.add_post('/yookassa-webhook', yookassa_webhook)
# API routes for dynamic updates
app.router.add_get('/api/tasks', api_tasks_handler)
app.router.add_get('/api/partners', api_partners_handler)
app.router.add_get('/api/profile', api_profile_handler)
app.router.add_get('/api/reminders', api_reminders_handler)
app.router.add_get('/api/interactions', api_interactions_handler)

# Setup for production
dp = Dispatcher()
dp.include_router(router)

# Session storage will be initialized in on_startup handler

# Initialize ReminderService
ai_service = AIIntegration()
reminder_service = ReminderService(bot=bot, ai_service=ai_service)
logger.info("ReminderService initialized")

# Start ReminderService on app startup
async def start_reminder_service(app):
    logger.info("Starting ReminderService...")
    await reminder_service.start()
    logger.info("ReminderService started successfully")
    # Log existing jobs
    jobs = reminder_service.scheduler.get_jobs()
    logger.info(f"Scheduled jobs after start: {len(jobs)}")
    for job in jobs[:5]:  # Log first 5 jobs
        logger.info(f"Job: {job.id} at {job.next_run_time}")

app.on_startup.append(start_reminder_service)
app.on_startup.append(on_startup)

if bot:
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
else:
    logger.warning("Bot not created, skipping webhook setup")

logger.info("App created successfully")

if __name__ == "__main__":
    logger.info(f"Starting application on port {PORT}")

    try:
        port = PORT
        host = '0.0.0.0'
        logger.info(f"Starting web server on {host}:{port}")
        
        # Use asyncio AppRunner
        logger.info("Using asyncio AppRunner")
        try:
            async def run_server():
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()
                logger.info(f"Server started on {host}:{port}")
                
                # Start polling if local mode
                if LOCAL and bot:  # Enabled for local testing
                    logger.info("Starting bot polling for local mode")
                    await bot.delete_webhook()
                    polling_task = asyncio.create_task(dp.start_polling(bot))
                else:
                    polling_task = None
                
                # Keep the server running
                try:
                    for _ in range(60):  # Run for 60 seconds for testing
                        await asyncio.sleep(1)
                except KeyboardInterrupt:
                    logger.info("Shutting down server...")
                    if polling_task:
                        polling_task.cancel()
                finally:
                    await runner.cleanup()
                    logger.info("Server shut down")

            asyncio.run(run_server())
        except Exception as serve_error:
            logger.error(f"Error in asyncio run: {serve_error}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Failed to start application: {e}", exc_info=True)
        raise
