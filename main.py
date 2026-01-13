import asyncio
import logging
import os
import pytz
import hashlib
import hmac
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import aiohttp
from aiohttp import web
import aiohttp_cors
import aiohttp_jinja2
import jinja2
from redis.asyncio import Redis
import aiohttp_session
from aiohttp_session import get_session
from aiohttp_session.redis_storage import RedisStorage
from aiohttp_session import SimpleCookieStorage
from config import TELEGRAM_TOKEN, WEBHOOK_URL, TELEGRAM_BOT_USERNAME, REDIS_URL, PORT, FREE_ACCESS_MODE, ADMIN_SECRET, LOCAL, CURRENT_DATE
from datetime import datetime, timedelta
from ai_integration import AIIntegration, chat_with_ai, get_partners_list, set_redis_client, decrypt_data, encrypt_data
from reminder_service import ReminderService
from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction, UserRating

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    Base.metadata.create_all(engine)
    logger.info("Database tables created or already exist")
except Exception as e:
    logger.error(f"Failed to create database tables: {e}")

def run_migrations():
    """Run database migrations"""
    from sqlalchemy import text, inspect
    try:
        session = Session()
        inspector = inspect(engine)
        
        if 'user_profiles' not in inspector.get_table_names():
            logger.info("user_profiles table does not exist, skipping migration")
            session.close()
            return
            
        columns = [col['name'] for col in inspector.get_columns('user_profiles')]
        if 'activity_streak' not in columns:
            logger.info("Adding activity_streak column to user_profiles table")
            session.execute(text('ALTER TABLE user_profiles ADD COLUMN activity_streak INTEGER DEFAULT 0'))
            session.commit()
            logger.info("Migration: activity_streak column added successfully")
        else:
            logger.info("Migration: activity_streak column already exists")
            
        # Migration for bio column
        logger.info(f"Checking bio column, current columns: {columns}")
        if 'bio' not in columns:
            try:
                logger.info("Adding bio column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN bio TEXT'))
                session.commit()
                logger.info("Migration: bio column added successfully")
            except Exception as e:
                logger.error(f"Failed to add bio column: {e}")
                session.rollback()
        else:
            logger.info("Migration: bio column already exists")
            
        # Migration for languages column
        if 'languages' not in columns:
            try:
                logger.info("Adding languages column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN languages VARCHAR(500)'))
                session.commit()
                logger.info("Migration: languages column added successfully")
            except Exception as e:
                logger.error(f"Failed to add languages column: {e}")
                session.rollback()
        else:
            logger.info("Migration: languages column already exists")
            
        # Migration for subscriptions table
        if 'subscriptions' in inspector.get_table_names():
            sub_columns = [col['name'] for col in inspector.get_columns('subscriptions')]
            if 'telegram_username' not in sub_columns:
                logger.info("Adding telegram_username column to subscriptions table")
                session.execute(text('ALTER TABLE subscriptions ADD COLUMN telegram_username VARCHAR(100)'))
                session.commit()
                logger.info("Migration: telegram_username column added successfully")
            else:
                logger.info("Migration: telegram_username column already exists")
        
        # Migration for users table
        if 'users' in inspector.get_table_names():
            user_columns = [col['name'] for col in inspector.get_columns('users')]
            if 'photo_url' not in user_columns:
                logger.info("Adding photo_url column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN photo_url VARCHAR(500)'))
                session.commit()
                logger.info("Migration: photo_url column added successfully")
            else:
                logger.info("Migration: photo_url column already exists")
            
            if 'updated_at' not in user_columns:
                logger.info("Adding updated_at column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN updated_at TIMESTAMP'))
                session.commit()
                logger.info("Migration: updated_at column added successfully")
            else:
                logger.info("Migration: updated_at column already exists")
            
            if 'invalid_chat' not in user_columns:
                logger.info("Adding invalid_chat column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN invalid_chat BOOLEAN DEFAULT FALSE'))
                session.commit()
                logger.info("Migration: invalid_chat column added successfully")
            else:
                logger.info("Migration: invalid_chat column already exists")
        
        session.close()
    except Exception as e:
        logger.error(f"Migration failed: {e}")

def add_test_sport_users():
    """Добавляет тестовых пользователей с интересами 'спорт' если их еще нет"""
    from sqlalchemy import text
    try:
        session = Session()
        
        test_users_data = [
            {'telegram_id': 111111, 'username': 'sportfan1', 'first_name': 'Алексей',
             'interests': 'спорт, бег, фитнес', 'city': 'Москва', 'company': 'Фитнес-клуб', 'position': 'Тренер'},
            {'telegram_id': 222222, 'username': 'sportfan2', 'first_name': 'Дмитрий',
             'interests': 'спорт, футбол, плавание', 'city': 'Санкт-Петербург', 'company': 'Спортивная школа', 'position': 'Инструктор'},
            {'telegram_id': 333333, 'username': 'sportfan3', 'first_name': 'Михаил',
             'interests': 'спорт, теннис, йога', 'city': 'Москва', 'company': 'Теннисный центр', 'position': 'Спортсмен'},
            {'telegram_id': 444444, 'username': 'sportfan4', 'first_name': 'Елена',
             'interests': 'спорт, волейбол, танцы', 'city': 'Казань', 'company': 'Спортивный комплекс', 'position': 'Администратор'},
            {'telegram_id': 555555, 'username': 'sportfan5', 'first_name': 'Анна',
             'interests': 'спорт, гимнастика, пилатес', 'city': 'Москва', 'company': 'Студия пилатес', 'position': 'Инструктор'}
        ]
        
        # Проверяем есть ли хоть один тестовый пользователь
        test_ids = [111111, 222222, 333333, 444444, 555555]
        existing_count = session.query(User).filter(User.telegram_id.in_(test_ids)).count()
        
        if existing_count == len(test_ids):
            logger.info(f"All {len(test_ids)} test sport users already exist")
            session.close()
            return
        
        logger.info(f"Found {existing_count}/{len(test_ids)} test users, adding missing ones...")
        
        added_count = 0
        for user_data in test_users_data:
            existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if not existing_user:
                # Создаем пользователя
                new_user = User(
                    telegram_id=user_data['telegram_id'],
                    username=user_data['username'],
                    first_name=user_data['first_name'],
                    timezone='Europe/Moscow'
                )
                session.add(new_user)
                session.flush()
                
                # Создаем профиль
                profile = UserProfile(
                    user_id=new_user.id,
                    interests=user_data['interests'],
                    city=user_data['city'],
                    company=user_data['company'],
                    position=user_data['position'],
                    average_rating=4.5,
                    rating_count=10
                )
                session.add(profile)
                added_count += 1
                logger.info(f"Added test user: {user_data['username']} (telegram_id: {user_data['telegram_id']})")
            else:
                logger.info(f"Test user {user_data['username']} already exists")
        
        session.commit()
        logger.info(f"Successfully added {added_count} test sport users")
        session.close()
    except Exception as e:
        logger.error(f"Failed to add test sport users: {e}", exc_info=True)

def ensure_sport_interest():
    """Добавляет 'спорт' к интересам всех пользователей если его нет"""
    try:
        session = Session()
        profiles = session.query(UserProfile).all()
        updated = 0
        for profile in profiles:
            if profile.interests:
                interests_lower = profile.interests.lower()
                if 'спорт' not in interests_lower:
                    profile.interests = profile.interests + ', спорт'
                    updated += 1
            else:
                profile.interests = 'спорт'
                updated += 1
        
        if updated > 0:
            session.commit()
            logger.info(f"Added 'спорт' interest to {updated} user profiles")
        else:
            logger.info("All users already have 'спорт' interest")
        session.close()
    except Exception as e:
        logger.error(f"Failed to add sport interest: {e}")
        
try:
    run_migrations()
    logger.info("Database migrations completed")
    add_test_sport_users()
    ensure_sport_interest()
except Exception as e:
    logger.error(f"Failed to run migrations: {e}")

redis_client = None
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


async def health_handler(request):
    """Health check endpoint for Railway"""
    return web.Response(text='OK', status=200)


async def login_handler(request):
    # Redirect to dashboard for unified experience
    return web.HTTPFound('/dashboard')


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
                
                # Get avatar from Telegram API
                avatar_url = None
                if 'bot' in request.app:
                    try:
                        avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
                        logger.info(f"Got avatar URL for new user {user_id}: {avatar_url}")
                    except Exception as e:
                        logger.error(f"Error getting avatar for new user {user_id}: {e}")
                
                user = User(telegram_id=user_id, username=data.get('username'), first_name=data.get('first_name'), photo_url=avatar_url, timezone=timezone)
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
                # Update avatar from Telegram API
                if 'bot' in request.app:
                    try:
                        avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
                        if avatar_url and avatar_url != user.photo_url:
                            user.photo_url = avatar_url
                            session_db.commit()
                            logger.info(f"Updated avatar for user {user_id}: {avatar_url}")
                    except Exception as e:
                        logger.error(f"Error updating avatar for user {user_id}: {e}")
            
            # Increment login count if subscription exists
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()
            if subscription:
                subscription.login_count += 1
                session_db.commit()
        finally:
            session_db.close()
        
        session = await get_session(request)
        session['user_id'] = user_id
        logger.info(f"Session set with user_id: {user_id}, session data: {dict(session)}")
        
        response = web.HTTPFound('/dashboard')
        logger.info(f"Redirecting to /dashboard after auth")
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
    logger.info(f"Session in dashboard: {dict(session) if session else 'None'}")
    try:
        user_id = session.get('user_id')
        logger.info(f"User ID from session: {user_id} (type: {type(user_id)})")
        
        # Преобразуем в int если нужно
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            logger.error(f"Invalid user_id in session: {user_id}")
            logged_in = False
        else:
            logged_in = bool(user_id)
        
        if not logged_in:
            # Show login page in dashboard
            bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'Asibiont_bot')
            logger.info(f"Rendering login page with bot_username: {bot_user}, original: {TELEGRAM_BOT_USERNAME}")
            return aiohttp_jinja2.render_template('dashboard_new.html', request, {
                'logged_in': False,
                'bot_username': bot_user,
                'current_date': '',
                'current_time': '',
                'formatted_end_date': None,
                'timestamp': int(datetime.now(pytz.UTC).timestamp())
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
                    'timestamp': int(datetime.now(pytz.UTC).timestamp())
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
            logger.info(f"Found {len(tasks)} tasks for user {user.id} (telegram_id: {user.telegram_id})")
            for task in tasks:
                logger.info(f"Task {task.id}: {task.title} (user_id: {task.user_id})")
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
                    Task.delegated_to_username.ilike(user.username),
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegator_ids = set()
                for task in delegated_tasks:
                    if task.user_id and task.user_id not in delegator_ids:
                        delegator_ids.add(task.user_id)
                        delegator = session_db.query(User).filter_by(id=task.user_id).first()
                        if delegator and delegator.id != user.id:
                            delegator_tasks = [t for t in delegated_tasks if t.user_id == delegator.id]
                            task_count = len(delegator_tasks)
                            task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegator_tasks[:3]]
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'reason': f'делегировал {task_count} задач',
                                'tasks': task_titles,
                                'task_count': task_count
                            })
                
                # Люди, которым я делегировал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(User.username.ilike(task.delegated_to_username.replace('@', ''))).first()
                        if delegatee and delegatee.id != user.id:
                            delegatee_tasks = [t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                            task_count = len(delegatee_tasks)
                            task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegatee_tasks[:3]]
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'reason': f'я делегировал {task_count} задач',
                                'tasks': task_titles,
                                'task_count': task_count
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
        
        except Exception as e:
            logger.error(f"Error building partners/delegations: {e}", exc_info=True)
            partners = []
            delegating_to_me = []
            delegating_by_me = []
        
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
        
        base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)
        
        current_time = str(int(datetime.now().timestamp()) + 12)
        
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
                'description': decrypt_data(task.description) if task.description else '',
                'status': task.status,
                'reminder_time': reminder_time_iso,  # Для группировки в JS
                'reminder_time_local': getattr(task, 'reminder_time_local', None),
                'overdue': getattr(task, 'overdue', False),
                'overdue_text': getattr(task, 'overdue_text', None),
                'recommendations': task.recommendations
            }
            tasks_dict.append(task_dict)
        
        # Get user avatar URL from database and update if needed
        user_avatar_url = user.photo_url if user and user.photo_url else None
        
        # Try to update avatar from Telegram API if bot is available
        if 'bot' in request.app and user:
            try:
                updated_avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
                if updated_avatar_url and updated_avatar_url != user.photo_url:
                    user.photo_url = updated_avatar_url
                    session_db.commit()
                    logger.info(f"Updated avatar URL for user {user_id}")
                    user_avatar_url = updated_avatar_url
            except Exception as e:
                logger.error(f"Error updating avatar for user {user_id}: {e}")
        
        # Add random parameter to prevent caching if URL exists
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
            'timestamp': int(datetime.now(pytz.UTC).timestamp()),
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
            'timestamp': int(datetime.now(pytz.UTC).timestamp())
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
                    from datetime import datetime, timezone
                    cutoff_time = datetime.now(timezone.utc).timestamp() - 24 * 3600
                    context = [msg for msg in full_context if datetime.fromisoformat(msg.get("timestamp", "2000-01-01T00:00:00")).timestamp() > cutoff_time]
                    logger.info(f"Loaded and filtered context with {len(context)} messages from last 24h")
                else:
                    logger.info("No context found in Redis")
            except Exception as e:
                logger.error(f"Error loading context: {e}")
                context = []

        # Save user message WITH PRECISE TIMESTAMP before AI call
        from datetime import datetime, timezone as dt_timezone
        user_message_timestamp = datetime.now(dt_timezone.utc)
        
        # Check for duplicate via Redis (web chat duplicate protection)
        message_key = f"web_chat_message:{user_id}:{message[:50]}"  # Use message prefix as key
        if redis_client:
            try:
                is_duplicate = await redis_client.exists(message_key)
                if is_duplicate:
                    logger.warning(f"[WEB DUPLICATE] Message from user {user_id} IGNORED (already processed)")
                    # Return cached response instead
                    cached_response = await redis_client.get(f"{message_key}:response")
                    if cached_response:
                        return web.json_response({'response': cached_response.decode('utf-8')})
                    # If no cached response, allow processing but log it
                    logger.warning(f"[WEB DUPLICATE] No cached response found, allowing reprocess")
            except Exception as e:
                logger.error(f"Error checking duplicate: {e}")
        
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            logger.info(f"User found: {user is not None}")
            if user:
                content = message
                if file:
                    content += f" [Файл: {file.filename}]"
                
                # Проверяем, не было ли уже сохранено такое же сообщение в последние 5 секунд
                # чтобы избежать дублирования при повторных запросах
                from datetime import timedelta
                five_seconds_ago = user_message_timestamp - timedelta(seconds=5)
                recent_interaction = session_db.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type == 'user',
                    Interaction.content == content,
                    Interaction.created_at >= five_seconds_ago
                ).first()
                
                if not recent_interaction:
                    interaction_user = Interaction(
                        user_id=user.id, 
                        message_type='user', 
                        content=content,
                        created_at=user_message_timestamp  # Точное время ДО вызова AI
                    )
                    session_db.add(interaction_user)
                    session_db.commit()
                    logger.info(f"Saved user message to database")
                else:
                    logger.info(f"Skipped duplicate user message")
                
                user_message_saved = True

            # Get AI response (will take time, so agent timestamp will be later)
            try:
                logger.info(f"Calling chat_with_ai with user_id: {user_id}")
                response = await chat_with_ai(message, context, user_id, file_content)
                logger.info(f"AI response: {response[:100]}...")
            except Exception as e:
                logger.error(f"Error getting AI response: {e}", exc_info=True)
                response = f"Ошибка: {str(e)}"

            # Save context back to Redis with timestamp
            from datetime import datetime, timezone
            context.append({
                "user": message, 
                "agent": response, 
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            # Keep only messages from last 24 hours
            cutoff_time = datetime.now(timezone.utc).timestamp() - 24 * 3600
            context = [msg for msg in context if datetime.fromisoformat(msg.get("timestamp", "2000-01-01T00:00:00")).timestamp() > cutoff_time]
            # Limit to last 50 messages to prevent excessive storage
            if len(context) > 50:
                context = context[-50:]
            if redis_client:
                try:
                    await redis_client.setex(f"context:{user_id}", 24*3600, json.dumps(context).encode('utf-8'))  # Expire in 24 hours
                    # Mark message as processed to prevent duplicates
                    await redis_client.setex(message_key, 30, "1")  # 30 second window
                    # Cache response for duplicate requests
                    await redis_client.setex(f"{message_key}:response", 30, response.encode('utf-8'))
                    logger.info(f"[WEB CHAT] Marked message as processed")

                    # НЕ удаляем timestamp - новые сообщения будут после него и будут видны
                    logger.info(f"Context saved to Redis with {len(context)} messages")
                except Exception as e:
                    logger.error(f"Error saving context: {e}")

            # Save agent response
            if user:
                # Проверяем, не было ли уже сохранено такое же сообщение AI в последние 5 секунд
                agent_response_timestamp = datetime.now(dt_timezone.utc)
                five_seconds_ago_agent = agent_response_timestamp - timedelta(seconds=5)
                recent_ai_interaction = session_db.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type == 'ai',
                    Interaction.content == response,
                    Interaction.created_at >= five_seconds_ago_agent
                ).first()
                
                if not recent_ai_interaction:
                    interaction_agent = Interaction(
                        user_id=user.id, 
                        message_type='ai', 
                        content=response,
                        created_at=agent_response_timestamp
                    )
                    session_db.add(interaction_agent)
                    session_db.commit()
                    logger.info(f"Saved AI response to database")
                else:
                    logger.info(f"Skipped duplicate AI response")
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
    logger.info("clear_user_tasks_handler called")
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"User ID from session: {user_id}")
    if not user_id:
        logger.warning("No user_id in session")
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        logger.info(f"User found: {user is not None}")
        if not user:
            logger.warning(f"User not found for telegram_id: {user_id}")
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Count tasks before deletion
        from sqlalchemy import or_
        task_count = session_db.query(Task).filter(
            or_(
                Task.user_id == user.id,
                Task.delegated_to_username.ilike(user.username)
            )
        ).count()
        logger.info(f"User {user_id} has {task_count} tasks to clear")
        
        # Clear user's tasks (both created by user and delegated to user)
        session_db.query(Task).filter(
            or_(
                Task.user_id == user.id,
                Task.delegated_to_username.ilike(user.username)
            )
        ).delete()
        session_db.commit()
        logger.info(f"User {user_id} tasks cleared successfully")
        return web.json_response({'message': 'Tasks cleared'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing user tasks: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def clear_single_task_handler(request):
    logger.info("clear_single_task_handler called")
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"User ID from session: {user_id}")
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    data = await request.json()
    task_id = data.get('task_id')
    logger.info(f"Task ID to delete: {task_id}")
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        logger.info(f"User found: {user is not None}")
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Ищем задачу либо среди своих, либо среди делегированных мне
        from sqlalchemy import or_
        task = session_db.query(Task).filter(
            Task.id == task_id,
            or_(
                Task.user_id == user.id,
                Task.delegated_to_username.ilike(user.username)
            )
        ).first()
        logger.info(f"Task found: {task is not None}")
        if not task:
            return web.json_response({'error': 'Task not found'}, status=404)
        
        session_db.delete(task)
        session_db.commit()
        logger.info(f"Task {task_id} deleted by user {user_id}")
        return web.json_response({'message': 'Task deleted'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error deleting task: {e}", exc_info=True)
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
    return web.json_response({'status': 'ok'}, status=200)


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

# Setup CORS
# cors = aiohttp_cors.setup(app, defaults={
#     "*": aiohttp_cors.ResourceOptions(
#         allow_credentials=True,
#         expose_headers="*",
#         allow_headers="*",
#     )
# })

# Add bot to app
if bot:
    app['bot'] = bot

# Middleware to add CSP headers and disable cache for static files
@web.middleware
async def logging_middleware(request, handler):
    """Log all incoming requests"""
    logger.info(f"Incoming request: {request.method} {request.path} from {request.remote}")
    try:
        response = await handler(request)
        logger.info(f"Response: {request.method} {request.path} -> {response.status}")
        return response
    except Exception as e:
        logger.error(f"Error handling {request.method} {request.path}: {e}")
        raise

@web.middleware
async def csp_middleware(request, handler):
    response = await handler(request)
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.deepseek.com; frame-src https://oauth.telegram.org;"
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

app.middlewares.append(logging_middleware)
app.middlewares.append(csp_middleware)

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
                subscription = Subscription(user_id=user.id, telegram_username=user.username)
                session.add(subscription)
            else:
                # Update telegram_username if not set
                if not subscription.telegram_username:
                    subscription.telegram_username = user.username
            
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

async def api_partners_handler(request):
    def pluralize_task(count):
        """Склонение слова 'задача' по числу"""
        last_digit = count % 10
        last_two_digits = count % 100
        
        if 11 <= last_two_digits <= 19:
            return 'задач'
        if last_digit == 1:
            return 'задачу'
        if 2 <= last_digit <= 4:
            return 'задачи'
        return 'задач'
    
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        logger.info(f"API partners handler called, session: {dict(session_req) if session_req else 'None'}, user_id: {user_id}")
        if not user_id:
            logger.error("No user_id in session for partners API")
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        try:
            partners = get_partners_list(user_id=user_id)
        except Exception as e:
            logger.error(f"Error getting partners: {e}")
            partners = []
        
        # Filter hidden contacts
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            
            # Get hidden contacts from memory
            hidden_contacts = set()
            if user and user.memory and len(user.memory.strip()) > 0:
                try:
                    import re
                    from datetime import datetime, timezone as dt_timezone
                    
                    decrypted = decrypt_data(user.memory)
                    if decrypted:  # Check decrypted result is not empty
                        hide_matches = re.findall(r'hide_contact:@?(\w+):(\d+)', decrypted, re.IGNORECASE)
                        current_time = int(datetime.now(dt_timezone.utc).timestamp())
                        for username, expiration_ts in hide_matches:
                            exp_ts = int(expiration_ts)
                            if exp_ts > current_time:  # Still hidden
                                hidden_contacts.add(username.lower())
                except Exception as e:
                    logger.error(f"Error parsing hidden contacts: {e}")
            
            # Filter partners
            if hidden_contacts:
                partners = [p for p in partners if p.contact_info and p.contact_info.replace('@', '').lower() not in hidden_contacts]
            
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
            interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at).all() if user else []
            
            # Получить контакты по делегированию
            delegating_to_me = []  # Люди, которые делегировали мне задачи
            delegating_by_me = []  # Люди, которым я делегировал задачи
            
            try:
                # Люди, которые делегировали мне задачи (я получаю задачи от них)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username.ilike(user.username),
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegator_ids = set()
                for task in delegated_tasks:
                    if task.user_id and task.user_id not in delegator_ids:
                        delegator_ids.add(task.user_id)
                        delegator = session_db.query(User).filter_by(id=task.user_id).first()
                        if delegator and delegator.id != user.id:
                            delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first()
                            task_titles = [t.title for t in delegated_tasks if t.user_id == delegator.id]
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'position': delegator_profile.position if delegator_profile else None,
                                'interests': delegator_profile.interests if delegator_profile else None,
                                'city': delegator_profile.city if delegator_profile else None,
                                'company': delegator_profile.company if delegator_profile else None,
                                'task_count': len(task_titles),
                                'reason': f'делегировал {len(task_titles)} {pluralize_task(len(task_titles))}'
                            })
                
                # Люди, которым я делегировал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()
                
                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(User.username.ilike(task.delegated_to_username.replace('@', ''))).first()
                        if delegatee and delegatee.id != user.id:
                            delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first()
                            task_titles = [t.title for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'position': delegatee_profile.position if delegatee_profile else None,
                                'interests': delegatee_profile.interests if delegatee_profile else None,
                                'city': delegatee_profile.city if delegatee_profile else None,
                                'company': delegatee_profile.company if delegatee_profile else None,
                                'task_count': len(task_titles),
                                'reason': f'я делегировал {len(task_titles)} {pluralize_task(len(task_titles))}'
                            })
            
            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []

            # Apply hidden contacts to delegation lists as well
            if hidden_contacts:
                delegating_to_me = [c for c in delegating_to_me if c.get('username') and c.get('username').replace('@', '').lower() not in hidden_contacts]
                delegating_by_me = [c for c in delegating_by_me if c.get('username') and c.get('username').replace('@', '').lower() not in hidden_contacts]
        
        except Exception as e:
            logger.error(f"Error processing partners data: {e}", exc_info=True)
            partners = []
            delegating_to_me = []
            delegating_by_me = []
            profile = None
            interactions = []
                
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
        
        # Calculate common_tasks for regular partners
        for p in partners:
            if profile and p:
                # Get user's tasks
                user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                user_task_titles = set()
                for task in user_tasks:
                    if task.title:
                        user_task_titles.add(task.title.lower().strip())
                
                # Get partner's tasks
                partner_user = session_db.query(User).filter_by(id=p.user_id).first()
                if partner_user:
                    partner_tasks = session_db.query(Task).filter_by(user_id=partner_user.id).all()
                    partner_task_titles = set()
                    for task in partner_tasks:
                        if task.title:
                            partner_task_titles.add(task.title.lower().strip())
                    
                    common_task_titles = user_task_titles & partner_task_titles
                    p.common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None
                else:
                    p.common_tasks = None
            else:
                p.common_tasks = None
        
        partners_data = []
        for p in partners:
            # Получаем telegram_id пользователя из базы
            partner_user = session_db.query(User).filter_by(id=p.user_id).first() if hasattr(p, 'user_id') and p.user_id else None
            
            # Update avatar from Telegram if available
            photo_url = partner_user.photo_url if partner_user and partner_user.photo_url else None
            if partner_user and partner_user.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], partner_user.telegram_id)
                    if updated_avatar and updated_avatar != partner_user.photo_url:
                        partner_user.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating partner avatar for {partner_user.telegram_id}: {e}")
            
            partners_data.append({
                'contact_info': partner_user.username if partner_user and partner_user.username else f"user{partner_user.telegram_id if partner_user else 'unknown'}",
                'telegram_id': partner_user.telegram_id if partner_user else None,
                'photo_url': photo_url,
                'city': getattr(p, 'city', None),
                'common_interests': getattr(p, 'common_interests', None),
                'common_skills': getattr(p, 'common_skills', None),
                'common_goals': getattr(p, 'common_goals', None),
                'common_tasks': getattr(p, 'common_tasks', None),
                'recommendation_reason': getattr(p, 'recommendation_reason', 'подходящий контакт'),
                'average_rating': getattr(p, 'average_rating', 0),
                'rating_count': getattr(p, 'rating_count', 0),
                'type': 'recommended'
            })
        
        # Add delegating contacts
        for contact in delegating_to_me:
            # Получить профиль делегатора для расчета общих интересов/навыков/целей
            delegator_profile = session_db.query(UserProfile).filter_by(user_id=contact['id']).first() if 'id' in contact else None
            
            common_interests = None
            common_skills = None
            common_goals = None
            
            if profile and delegator_profile:
                # Common interests
                if delegator_profile.interests and profile.interests:
                    user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                    partner_interests = set(i.strip().lower() for i in delegator_profile.interests.split(','))
                    common = user_interests & partner_interests
                    common_interests = ', '.join(common) if common else None
                
                # Common skills
                if delegator_profile.skills and profile.skills:
                    user_skills = set(s.strip().lower() for s in profile.skills.split(','))
                    partner_skills = set(s.strip().lower() for s in delegator_profile.skills.split(','))
                    common_sk = user_skills & partner_skills
                    common_skills = ', '.join(common_sk) if common_sk else None
                
                # Common goals
                if delegator_profile.goals and profile.goals:
                    user_goals = set(g.strip().lower() for g in profile.goals.split(','))
                    partner_goals = set(g.strip().lower() for g in delegator_profile.goals.split(','))
                    common_g = user_goals & partner_goals
                    common_goals = ', '.join(common_g) if common_g else None
            
            # Common tasks for delegating_to_me
            common_tasks = None
            if profile and delegator_profile:
                # Get user's tasks
                user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                user_task_titles = set()
                for task in user_tasks:
                    if task.title:
                        user_task_titles.add(task.title.lower().strip())
                
                # Get delegator's tasks
                delegator_user = session_db.query(User).filter_by(id=contact['id']).first()
                if delegator_user:
                    delegator_tasks = session_db.query(Task).filter_by(user_id=delegator_user.id).all()
                    delegator_task_titles = set()
                    for task in delegator_tasks:
                        if task.title:
                            delegator_task_titles.add(task.title.lower().strip())
                    
                    common_task_titles = user_task_titles & delegator_task_titles
                    common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None  # Limit to 5 common tasks
            
            # Get delegator user object
            delegator = session_db.query(User).filter_by(id=contact['id']).first() if 'id' in contact else None
            
            # Update avatar from Telegram if available
            photo_url = delegator.photo_url if delegator and delegator.photo_url else None
            if delegator and delegator.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id)
                    if updated_avatar and updated_avatar != delegator.photo_url:
                        delegator.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")
            
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegator.telegram_id if delegator else None,
                'photo_url': photo_url,
                'first_name': contact['first_name'],
                'position': contact.get('position'),
                'interests': contact.get('interests'),
                'city': contact.get('city'),
                'company': contact.get('company'),
                'common_interests': common_interests,
                'common_skills': common_skills,
                'common_goals': common_goals,
                'common_tasks': common_tasks,
                'average_rating': delegator_profile.average_rating if delegator_profile else 0,
                'rating_count': delegator_profile.rating_count if delegator_profile else 0,
                'reason': contact['reason'],
                'task_count': contact.get('task_count', 0),
                'type': 'delegating_to_me'
            })
        
        for contact in delegating_by_me:
            # Получить профиль делегата для расчета общих интересов/навыков/целей
            delegatee_profile = session_db.query(UserProfile).filter_by(user_id=contact['id']).first() if 'id' in contact else None
            delegatee = session_db.query(User).filter_by(id=contact['id']).first() if 'id' in contact else None
            
            common_interests = None
            common_skills = None
            common_goals = None
            
            if profile and delegatee_profile:
                # Common interests
                if delegatee_profile.interests and profile.interests:
                    user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                    partner_interests = set(i.strip().lower() for i in delegatee_profile.interests.split(','))
                    common = user_interests & partner_interests
                    common_interests = ', '.join(common) if common else None
                
                # Common skills
                if delegatee_profile.skills and profile.skills:
                    user_skills = set(s.strip().lower() for s in profile.skills.split(','))
                    partner_skills = set(s.strip().lower() for s in delegatee_profile.skills.split(','))
                    common_sk = user_skills & partner_skills
                    common_skills = ', '.join(common_sk) if common_sk else None
                
                # Common goals
                if delegatee_profile.goals and profile.goals:
                    user_goals = set(g.strip().lower() for g in profile.goals.split(','))
                    partner_goals = set(g.strip().lower() for g in delegatee_profile.goals.split(','))
                    common_g = user_goals & partner_goals
                    common_goals = ', '.join(common_g) if common_g else None
            
            # Common tasks for delegating_by_me
            common_tasks = None
            if profile and delegatee_profile:
                # Get user's tasks
                user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                user_task_titles = set()
                for task in user_tasks:
                    if task.title:
                        user_task_titles.add(task.title.lower().strip())
                
                # Get delegatee's tasks
                delegatee_user = session_db.query(User).filter_by(id=contact['id']).first()
                if delegatee_user:
                    delegatee_tasks = session_db.query(Task).filter_by(user_id=delegatee_user.id).all()
                    delegatee_task_titles = set()
                    for task in delegatee_tasks:
                        if task.title:
                            delegatee_task_titles.add(task.title.lower().strip())
                    
                    common_task_titles = user_task_titles & delegatee_task_titles
                    common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None  # Limit to 5 common tasks
            
            # Update avatar from Telegram if available
            photo_url = delegatee.photo_url if delegatee and delegatee.photo_url else None
            if delegatee and delegatee.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id)
                    if updated_avatar and updated_avatar != delegatee.photo_url:
                        delegatee.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegatee avatar for {delegatee.telegram_id}: {e}")
            
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegatee.telegram_id if delegatee else None,
                'photo_url': photo_url,
                'first_name': contact['first_name'],
                'position': contact.get('position'),
                'interests': contact.get('interests'),
                'city': contact.get('city'),
                'company': contact.get('company'),
                'common_interests': common_interests,
                'common_skills': common_skills,
                'common_goals': common_goals,
                'common_tasks': common_tasks,
                'average_rating': delegatee_profile.average_rating if delegatee_profile else 0,
                'rating_count': delegatee_profile.rating_count if delegatee_profile else 0,
                'reason': contact['reason'],
                'task_count': contact.get('task_count', 0),
                'type': 'delegating_by_me'
            })
        
        # Сортируем partners_data: сначала по городу (совпадение с пользователем), потом по рейтингу
        user_city = profile.city.lower() if profile and profile.city else None
        
        # Нормализация названий городов для правильного сравнения
        def normalize_city(city):
            if not city:
                return None
            city = city.lower().strip()
            # Маппинг русских названий на английские
            city_map = {
                'москва': 'moscow',
                'санкт-петербург': 'saint petersburg',
                'петербург': 'saint petersburg',
                'спб': 'saint petersburg',
                'екатеринбург': 'yekaterinburg',
                'новосибирск': 'novosibirsk',
                'казань': 'kazan'
            }
            return city_map.get(city, city)
        
        normalized_user_city = normalize_city(user_city)
        
        def sort_key(partner):
            partner_city = normalize_city(partner.get('city', ''))
            same_city = 0 if (normalized_user_city and partner_city == normalized_user_city) else 1
            
            rating = partner.get('average_rating', 0) or 0
            # Группы рейтинга:
            # 1. Высокий рейтинг (>= 5): сортируем по убыванию
            # 2. Нет рейтинга (0): нейтрально, выше плохих
            # 3. Низкий рейтинг (< 5): сортируем по убыванию
            if rating >= 5:
                rating_group = 0  # Лучшая группа
                rating_value = -rating  # Внутри группы по убыванию
            elif rating == 0:
                rating_group = 1  # Средняя группа (нет данных)
                rating_value = 0
            else:  # rating < 5
                rating_group = 2  # Худшая группа
                rating_value = -rating  # Внутри группы по убыванию
            
            return (same_city, rating_group, rating_value)
        
        partners_data.sort(key=sort_key)
        
        # Закрываем сессию перед возвратом ответа
        session_db.close()
        
        return web.json_response({'partners': partners_data})
    except Exception as e:
        logger.error(f"Unexpected error in api_partners_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        # На случай ранних ошибок закрываем сессию, если она еще открыта
        try:
            session_db.close()
        except Exception:
            pass

async def rate_user_handler(request):
    """Rate another user (1-10 scale)"""
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        data = await request.json()
        rated_username = data.get('username')
        rating = data.get('rating')
        
        if not rated_username or rating is None:
            return web.json_response({'error': 'Missing username or rating'}, status=400)
        
        # Validate rating type and range
        try:
            rating = int(rating)
        except (ValueError, TypeError):
            return web.json_response({'error': 'Rating must be a number'}, status=400)
        
        if not (1 <= rating <= 10):
            return web.json_response({'error': 'Rating must be between 1 and 10'}, status=400)
        
        session_db = Session()
        try:
            # Get rater user
            rater = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not rater:
                return web.json_response({'error': 'User not found'}, status=404)
            
            # Get rated user
            rated_user = session_db.query(User).filter(User.username.ilike(rated_username.replace('@', ''))).first()
            if not rated_user:
                return web.json_response({'error': 'Rated user not found'}, status=404)
            
            # Can't rate yourself
            if rater.id == rated_user.id:
                return web.json_response({'error': 'Cannot rate yourself'}, status=400)
            
            # Check if rating already exists
            existing_rating = session_db.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=rated_user.id
            ).first()
            
            if existing_rating:
                # Update existing rating
                existing_rating.rating = rating
                existing_rating.updated_at = datetime.now(pytz.UTC)
            else:
                # Create new rating
                new_rating = UserRating(
                    rater_user_id=rater.id,
                    rated_user_id=rated_user.id,
                    rating=rating
                )
                session_db.add(new_rating)
            
            session_db.commit()
            
            # Recalculate average rating for rated user
            all_ratings = session_db.query(UserRating).filter_by(rated_user_id=rated_user.id).all()
            if all_ratings:
                avg_rating = sum(r.rating for r in all_ratings) / len(all_ratings)
                rated_profile = session_db.query(UserProfile).filter_by(user_id=rated_user.id).first()
                if rated_profile:
                    rated_profile.average_rating = round(avg_rating, 1)
                    rated_profile.rating_count = len(all_ratings)
                    session_db.commit()
            
            # Сохранить сообщение в историю взаимодействий
            success_message = f'Оценка {rating}/10 для @{rated_username} сохранена'
            interaction = Interaction(
                user_id=rater.id,
                message_type='ai',
                content=success_message
            )
            session_db.add(interaction)
            session_db.commit()
            
            return web.json_response({
                'success': True,
                'message': success_message
            })
        
        finally:
            session_db.close()
    
    except Exception as e:
        logger.error(f"Error rating user: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def hide_contact_handler(request):
    """Hide contact for specified number of days"""
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        data = await request.json()
        username = data.get('username')
        days = data.get('days', 7)
        
        if not username:
            return web.json_response({'error': 'Missing username'}, status=400)
        
        # Validate days
        try:
            days = int(days)
            if days < 1 or days > 365:
                return web.json_response({'error': 'Days must be between 1 and 365'}, status=400)
        except (ValueError, TypeError):
            return web.json_response({'error': 'Days must be a number'}, status=400)
        
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            
            # Calculate expiration timestamp
            from datetime import datetime, timedelta, timezone as dt_timezone
            expiration = datetime.now(dt_timezone.utc) + timedelta(days=days)
            expiration_ts = int(expiration.timestamp())
            
            # Update user memory with hidden contact
            current_memory = ""
            if user.memory and len(user.memory.strip()) > 0:
                try:
                    current_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.error(f"Error decrypting memory in hide_contact: {e}")
                    current_memory = ""
            
            hide_entry = f"hide_contact:{username}:{expiration_ts}"
            
            # Remove old hide entries for this username
            import re
            current_memory = re.sub(rf'hide_contact:{username}:\d+[\n\s]*', '', current_memory)
            
            # Add new hide entry
            updated_memory = f"{current_memory.strip()}\n{hide_entry}".strip()
            user.memory = encrypt_data(updated_memory)
            
            session_db.commit()
            
            # Сохранить сообщение в историю взаимодействий
            success_message = f'@{username} скрыт на {days} дней'
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=success_message
            )
            session_db.add(interaction)
            session_db.commit()
            
            return web.json_response({
                'success': True,
                'message': success_message
            })
        
        finally:
            session_db.close()
    
    except Exception as e:
        logger.error(f"Error hiding contact: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def get_user_rating_handler(request):
    """Get current user's rating for another user"""
    try:
        session_req = await get_session(request)
        user_id = session_req.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)
        
        rated_username = request.rel_url.query.get('username')
        if not rated_username:
            return web.json_response({'error': 'Missing username'}, status=400)
        
        session_db = Session()
        try:
            rater = session_db.query(User).filter_by(telegram_id=user_id).first()
            rated_user = session_db.query(User).filter(User.username.ilike(rated_username.replace('@', ''))).first()
            
            if not rater or not rated_user:
                return web.json_response({'rating': None})
            
            existing_rating = session_db.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=rated_user.id
            ).first()
            
            if existing_rating:
                return web.json_response({'rating': existing_rating.rating})
            else:
                return web.json_response({'rating': None})
        
        finally:
            session_db.close()
    
    except Exception as e:
        logger.error(f"Error getting rating: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def api_avatar_handler(request):
    """API endpoint to get user avatar by telegram_id"""
    telegram_id = request.match_info.get('telegram_id')
    
    if not telegram_id:
        return web.Response(status=400, text='Missing telegram_id')
    
    try:
        telegram_id = int(telegram_id)
        
        # Check if bot is available
        if 'bot' not in request.app or not request.app['bot']:
            logger.warning(f"Bot not available for avatar request: {telegram_id}")
            return web.Response(status=404, text='Avatar service unavailable')
        
        avatar_url = await get_user_avatar_url(request.app['bot'], telegram_id)
        
        if avatar_url:
            # Redirect to the avatar URL
            return web.Response(status=302, headers={'Location': avatar_url})
        else:
            # Return 404 if no avatar found
            return web.Response(status=404, text='No avatar found')
    except ValueError:
        return web.Response(status=400, text='Invalid telegram_id')
    except Exception as e:
        logger.error(f"Error in api_avatar_handler: {e}")
        return web.Response(status=500, text='Internal server error')

async def api_reminders_handler(request):
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    logger.info(f"API reminders handler called, session: {dict(session_req) if session_req else 'None'}, user_id: {user_id}")
    if not user_id:
        logger.error("No user_id in session for reminders API")
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
    
    # Передаём redis_client в ai_integration
    set_redis_client(redis_client)
    logger.info(f"Redis client set in ai_integration: {redis_client is not None}")
    
    # Configure session cookie options
    # Note: Use secure=False for Railway as it uses internal HTTP behind proxy
    session_options = {
        'secure': False,  # Railway uses internal HTTP
        'httponly': True,
        'samesite': 'Lax',
        'domain': None,  # Let browser set domain automatically
        'max_age': 86400,  # 24 hours
        'path': '/'
    }
    
    # Initialize session storage
    # Use SimpleCookieStorage
    storage = SimpleCookieStorage(cookie_name='session', **session_options)
    logger.info(f"Session storage initialized with SimpleCookieStorage, options: {session_options}")
    
    aiohttp_session.setup(app, storage)
    logger.info("Session middleware configured successfully")
    
    # Set webhook
    if bot:
        webhook_url = WEBHOOK_URL
        await bot.set_webhook(webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.warning("Bot not created, skipping webhook setup")
    
    # Initialize handlers Redis
    async def init_handlers_redis(client):
        from handlers import init_redis as handlers_init_redis
        await handlers_init_redis(client)
    
    await init_handlers_redis(redis_client)
    logger.info("Handlers Redis initialized")
    
    # ReminderService will be started later in start_reminder_service



async def api_tasks_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"API tasks handler called, session: {dict(session) if session else 'None'}, user_id: {user_id}")
    if not user_id:
        logger.error("No user_id in session for tasks API")
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Get tasks created by me OR delegated to me
        from sqlalchemy import or_
        tasks = session_db.query(Task).filter(
            or_(
                Task.user_id == user.id,
                Task.delegated_to_username.ilike(user.username)
            )
        ).all()
        
        # Set overdue flag and local time for tasks
        user_tz = pytz.UTC
        if user and user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC
        base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)
        
        # Always use real current time - removed profile.current_time override
        
        tasks_data = []
        for task in tasks:
            # Format task title based on delegation
            title = task.title
            if task.delegated_to_username:
                # Remove leading @ if present
                delegated_username = task.delegated_to_username.lstrip('@')
                
                # Remove existing delegation markers from title to avoid duplication
                import re
                title = re.sub(r' - делегирована (от|на) @\w+$', '', title)
                
                # Check if task is delegated TO me or BY me
                if task.delegated_to_username.lower() == user.username.lower() or task.delegated_to_username.lower() == f"@{user.username.lower()}":
                    # Task delegated TO me
                    creator = session_db.query(User).filter_by(id=task.user_id).first()
                    if creator:
                        title = f"{title} - делегирована от @{creator.username}"
                elif task.user_id == user.id:
                    # Task delegated BY me to someone else
                    title = f"{title} - делегирована на @{delegated_username}"
            
            task_data = {
                'id': task.id,
                'title': title,
                'description': decrypt_data(task.description) if task.description else '',
                'status': task.status,
                'reminder_time': None,
                'reminder_time_local': None,
                'overdue': False,
                'overdue_text': None,
                'is_delegated': task.delegated_to_username is not None,
                'delegation_status': task.delegation_status if hasattr(task, 'delegation_status') else None
            }
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task_data['reminder_time'] = local_reminder.isoformat()
                task_data['reminder_time_local'] = local_reminder.strftime('%d.%m.%Y %H:%M')
                # Просрочка для незавершенных задач (pending или in_progress)
                task_data['overdue'] = local_reminder < user_now and task.status in ['pending', 'in_progress']
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

async def api_delegations_handler(request):
    """API для получения делегированных задач"""
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"API delegations handler called, session: {dict(session) if session else 'None'}, user_id: {user_id}")
    if not user_id:
        logger.error("No user_id in session for delegations API")
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Get user timezone
        user_tz = pytz.UTC
        if user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC
        
        # Tasks delegated TO me
        incoming = session_db.query(Task).filter(
            Task.delegated_to_username.ilike(user.username),
            Task.delegation_status == 'pending'
        ).all()
        incoming_data = []
        for task in incoming:
            delegator = session_db.query(User).filter_by(id=task.user_id).first()
            task_data = {
                'id': task.id,
                'title': task.title,
                'from_user': f"@{delegator.username}" if delegator else "Unknown",
                'status': task.delegation_status if hasattr(task, 'delegation_status') else 'pending',
                'reminder_time': task.reminder_time.astimezone(user_tz).strftime('%d.%m.%Y %H:%M') if task.reminder_time else None
            }
            incoming_data.append(task_data)
        
        # Tasks delegated BY me
        outgoing = session_db.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None)
        ).all()
        outgoing_data = []
        for task in outgoing:
            task_data = {
                'id': task.id,
                'title': task.title,
                'to_user': f"@{task.delegated_to_username}",
                'status': task.delegation_status if hasattr(task, 'delegation_status') else 'pending',
                'reminder_time': task.reminder_time.astimezone(user_tz).strftime('%d.%m.%Y %H:%M') if task.reminder_time else None
            }
            outgoing_data.append(task_data)
        
        return web.json_response({
            'incoming': incoming_data,
            'outgoing': outgoing_data
        })
    except Exception as e:
        logger.error(f"Error fetching delegations: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()

async def api_interactions_handler(request):
    """API для получения истории чата"""
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"API interactions handler called, session: {dict(session) if session else 'None'}, user_id: {user_id}")
    if not user_id:
        logger.error("No user_id in session for interactions API")
        return web.json_response({'error': 'Not authenticated'}, status=401)
    
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        interactions = session_db.query(Interaction).filter_by(user_id=user.id).order_by(Interaction.created_at.asc()).all()
        
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
        
        # Get user timezone
        user_tz = pytz.UTC
        if user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC
        
        interactions_data = []
        for interaction in filtered_interactions:
            # Convert UTC time to user timezone
            created_at_utc = interaction.created_at.replace(tzinfo=pytz.UTC) if interaction.created_at.tzinfo is None else interaction.created_at
            created_at_local = created_at_utc.astimezone(user_tz)
            
            interactions_data.append({
                'id': interaction.id,
                'content': interaction.content,
                'message_type': interaction.message_type,
                'created_at': created_at_local.isoformat()
            })
        
        return web.json_response({'interactions': interactions_data})
    except Exception as e:
        logger.error(f"Error fetching interactions: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()

async def api_search_contacts_handler(request):
    """API для поиска контактов по username"""
    try:
        session_obj = await get_session(request)
        user_id = session_obj.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        
        query = request.query.get('q', '').strip().lower().replace('@', '')
        if not query or len(query) < 2:
            return web.json_response({'contacts': []})
        
        session_db = Session()
        try:
            # Поиск пользователей по username (частичное совпадение)
            users = session_db.query(User).filter(
                User.username.ilike(f'%{query}%')
            ).limit(20).all()
            
            contacts_data = []
            for user in users:
                # Пропускаем текущего пользователя
                if user.telegram_id == user_id:
                    continue
                
                profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
                
                # Обновляем аватар если нужно
                photo_url = user.photo_url
                if user.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], user.telegram_id)
                        if updated_avatar and updated_avatar != user.photo_url:
                            user.photo_url = updated_avatar
                            session_db.commit()
                            photo_url = updated_avatar
                    except Exception as e:
                        logger.error(f"Error updating avatar for {user.telegram_id}: {e}")
                
                contacts_data.append({
                    'username': user.username,
                    'first_name': user.first_name,
                    'telegram_id': user.telegram_id,
                    'photo_url': photo_url,
                    'city': profile.city if profile else None,
                    'company': profile.company if profile else None,
                    'position': profile.position if profile else None,
                    'interests': profile.interests if profile else None,
                    'average_rating': profile.average_rating if profile else 0,
                    'rating_count': profile.rating_count if profile else 0
                })
            
            return web.json_response({'contacts': contacts_data})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error searching contacts: {e}")
        return web.json_response({'error': str(e)}, status=500)

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

async def api_profile_handler(request):
    """API для получения профиля пользователя"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        logger.info(f"API profile: session exists={session is not None}, user_id={user_id}")
        logger.info(f"API profile: session data={dict(session) if session else 'None'}")
        logger.info(f"API profile: cookies={request.cookies}")
        if not user_id:
            logger.error("No user_id in session for profile API")
            return web.json_response({'error': 'Not authenticated'}, status=401)
    except Exception as e:
        logger.error(f"Error getting session in api_profile: {e}", exc_info=True)
        return web.json_response({'error': 'Session error'}, status=500)
    
    # Try to get cached profile data first
    cache_key = f"profile:{user_id}"
    cached_profile = None
    if redis_client:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                cached_profile = json.loads(cached_data.decode('utf-8'))
                logger.info(f"Using cached profile data for user {user_id}")
        except Exception as e:
            logger.error(f"Error getting cached profile: {e}")
    
    if cached_profile:
        return web.json_response({'profile': cached_profile})
    
    # Get fresh data from database
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
        
        profile_data = {
            'username': user.username,
            'city': profile.city if profile else None,
            'company': profile.company if profile else None,
            'position': profile.position if profile else None,
            'goals': profile.goals if profile else None,
            'skills': profile.skills if profile else None,
            'interests': profile.interests if profile else None,
            'languages': profile.languages if profile else None,
            'bio': profile.bio if profile else None,
            'average_rating': profile.average_rating if profile else 0,
            'rating_count': profile.rating_count if profile else 0
        }
        
        # Cache the profile data for 1 hour
        if redis_client:
            try:
                await redis_client.setex(cache_key, 3600, json.dumps(profile_data).encode('utf-8'))
                logger.info(f"Cached profile data for user {user_id}")
            except Exception as e:
                logger.error(f"Error caching profile: {e}")
        
        return web.json_response({'profile': profile_data})
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()

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
                subscription = Subscription(user_id=user.id, telegram_username=user.username)
                session_db.add(subscription)
            else:
                # Update telegram_username if not set
                if not subscription.telegram_username:
                    subscription.telegram_username = user.username
            
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
app.router.add_get('/health', health_handler)
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
app.router.add_get('/api/avatar/{telegram_id}', api_avatar_handler)
app.router.add_post('/api/rate_user', rate_user_handler)
app.router.add_get('/api/get_user_rating', get_user_rating_handler)
app.router.add_post('/api/hide_contact', hide_contact_handler)
app.router.add_get('/api/profile', api_profile_handler)
app.router.add_get('/api/reminders', api_reminders_handler)
app.router.add_get('/api/delegations', api_delegations_handler)
app.router.add_get('/api/interactions', api_interactions_handler)
app.router.add_get('/api/search_contacts', api_search_contacts_handler)

# Setup for production
dp = Dispatcher()

# Include router from handlers
from handlers import router as handlers_router
dp.include_router(handlers_router)

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

if __name__ == "__main__":
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
                logger.info(f"Health check endpoint: http://{host}:{port}/health")
                logger.info(f"Dashboard endpoint: http://{host}:{port}/dashboard")
                logger.info("Server is ready to accept connections")
                
                # Keep the server running
                try:
                    # Keep server running indefinitely
                    while True:
                        await asyncio.sleep(3600)
                except KeyboardInterrupt:
                    logger.info("Shutting down server...")
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
