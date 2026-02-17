from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction, UserRating, SubscriptionTier, PromoCode, PaymentHistory, Post, PostLike, Comment, PostView, init_db
from reminder_service import ReminderService
from ai_integration import chat_with_ai, get_partners_list, decrypt_data, encrypt_data
from datetime import datetime, timedelta, timezone as dt_timezone
from config import TELEGRAM_TOKEN, TELEGRAM_BOT_USERNAME, PORT, CURRENT_DATE, DATABASE_URL, LOCAL
from aiohttp_session import SimpleCookieStorage
from aiohttp_session import get_session
import aiohttp_session
import os
from sqlalchemy import text, or_, and_
import re
import jinja2
import aiohttp_jinja2
from aiohttp import web
import aiohttp
import asyncio
import logging
import pytz
import time

# Import handlers
from handlers import router as handlers_router
import hashlib
import hmac
import json
import warnings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_db_session():
    """Get a new database session"""
    return Session()

from contextlib import contextmanager

@contextmanager
def get_db_session_context():
    """Context manager for database sessions - ensures proper cleanup"""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# Aiogram imports
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram import Bot, Dispatcher

# Скрываем некритичные предупреждения
warnings.filterwarnings('ignore', message='Couldn\'t find ffmpeg or avconv')


def normalize_city(city):
    """Normalize city names for comparison"""
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


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info("Database Connection")
logger.info("Attempting to connect to the database...")

# Test database connection
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
logger.info("✅ Database connection successful")

# Clear database if requested
if os.getenv('CLEAR_DB') == '1':
    logger.warning("CLEAR_DB=1 detected, clearing all database data...")
    try:
        Base.metadata.drop_all(engine)
        logger.warning("All tables dropped successfully")
    except Exception as e:
        logger.error(f"Error dropping tables: {e}")

# Initialize database tables
init_db()

# Run database migrations (extracted to migrations.py)
from migrations import run_migrations
run_migrations()

# Test database connection before starting
try:
    test_session = Session()
    test_session.execute(text('SELECT 1'))
    test_session.close()
    logger.info("✅ Database connection successful")
except Exception as e:
    logger.error(f"❌ CRITICAL: Cannot connect to database: {e}", exc_info=True)
    logger.error(f"DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "DATABASE_URL not set")

# Create test users ONLY when explicitly enabled with CREATE_TEST_USERS=1
if os.getenv('CREATE_TEST_USERS') == '1':
    try:
        session_db = Session()
        logger.info("Creating test users with different subscription tiers")

        test_users_data = [
            # LIGHT tier users
            {'telegram_id': 1001, 'tier': 'LIGHT', 'name': 'Test User 1', 'city': 'Москва', 'username': 'test1'},
            {'telegram_id': 1002, 'tier': 'LIGHT', 'name': 'Test User 2', 'city': 'Санкт-Петербург', 'username': 'test2'},
            {'telegram_id': 1003, 'tier': 'LIGHT', 'name': 'Test User 3', 'city': 'Екатеринбург', 'username': 'test3'},
            {'telegram_id': 1004, 'tier': 'LIGHT', 'name': 'Test User 4', 'city': 'Новосибирск', 'username': 'test4'},
            {'telegram_id': 1005, 'tier': 'LIGHT', 'name': 'Test User 5', 'city': 'Казань', 'username': 'test5'},
            {'telegram_id': 1006, 'tier': 'LIGHT', 'name': 'Test User 6', 'city': 'Нижний Новгород', 'username': 'test6'},
            {'telegram_id': 1007, 'tier': 'LIGHT', 'name': 'Test User 7', 'city': 'Челябинск', 'username': 'test7'},
            {'telegram_id': 1008, 'tier': 'LIGHT', 'name': 'Test User 8', 'city': 'Омск', 'username': 'test8'},

            # STANDARD tier users
            {'telegram_id': 1009, 'tier': 'STANDARD', 'name': 'Test User 9', 'city': 'Ростов-на-Дону', 'username': 'test9'},
            {'telegram_id': 1010, 'tier': 'STANDARD', 'name': 'Test User 10', 'city': 'Уфа', 'username': 'test10'},
            {'telegram_id': 1011, 'tier': 'STANDARD', 'name': 'Test User 11', 'city': 'Волгоград', 'username': 'test11'},
            {'telegram_id': 1012, 'tier': 'STANDARD', 'name': 'Test User 12', 'city': 'Красноярск', 'username': 'test12'},
            {'telegram_id': 1013, 'tier': 'STANDARD', 'name': 'Test User 13', 'city': 'Воронеж', 'username': 'test13'},
            {'telegram_id': 1014, 'tier': 'STANDARD', 'name': 'Test User 14', 'city': 'Пермь', 'username': 'test14'},

            # PREMIUM tier users
            {'telegram_id': 1015, 'tier': 'PREMIUM', 'name': 'Test User 15', 'city': 'Краснодар', 'username': 'test15'},
            {'telegram_id': 1016, 'tier': 'PREMIUM', 'name': 'Test User 16', 'city': 'Тюмень', 'username': 'test16'},
            {'telegram_id': 1017, 'tier': 'PREMIUM', 'name': 'Test User 17', 'city': 'Барнаул', 'username': 'test17'},
            {'telegram_id': 1018, 'tier': 'PREMIUM', 'name': 'Test User 18', 'city': 'Ижевск', 'username': 'test18'},
            {'telegram_id': 1019, 'tier': 'PREMIUM', 'name': 'Test User 19', 'city': 'Владивосток', 'username': 'test19'},
            {'telegram_id': 1020, 'tier': 'PREMIUM', 'name': 'Test User 20', 'city': 'Ярославль', 'username': 'test20'},
        ]

        now = datetime.now()

        added_count = 0
        updated_count = 0
        for user_data in test_users_data:
            existing_user = session_db.query(User).filter(User.telegram_id == user_data['telegram_id']).first()
            if existing_user:
                existing_profile = session_db.query(UserProfile).filter_by(user_id=existing_user.id).first()
                if existing_profile:
                    existing_profile.interests = 'спорт'
                    updated_count += 1
                continue

            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                first_name=user_data['name'],
                photo_url=f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN or 'fake_token'}/photos/file_test_{user_data['telegram_id']}.jpg?r={user_data['telegram_id']}",
                subscription_tier=user_data['tier'],
                created_at=now
            )
            session_db.add(user)
            session_db.flush()

            profile = UserProfile(
                user_id=user.id,
                interests='спорт',
                city=user_data['city'],
                contact_info=f'user{user_data["telegram_id"]}@test.com'
            )
            session_db.add(profile)

            subscription = Subscription(
                user_id=user.id,
                telegram_id=user.telegram_id,
                telegram_username=user.username,
                status='active',
                tier=user_data['tier'],
                start_date=now,
                end_date=now + timedelta(days=30)
            )
            session_db.add(subscription)
            added_count += 1

        if added_count > 0 or updated_count > 0:
            session_db.commit()
            logger.info(f"Test users: added={added_count}, updated={updated_count}")
        else:
            logger.info("All test users already exist")
    except Exception as e:
        logger.error(f"Error creating test users: {e}")
        if 'session_db' in locals():
            session_db.rollback()
    finally:
        if 'session_db' in locals():
            session_db.close()


# Helper functions for context management
def get_context_from_db(user_id, limit=10):
    """Get chat context from Interaction table"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return []
        
        # Get history_cleared_at timestamp
        cleared_at = user.history_cleared_at
        
        # Get last N interactions after clear timestamp
        query = session.query(Interaction).filter(Interaction.user_id == user.id)
        if cleared_at:
            query = query.filter(Interaction.created_at > cleared_at)
        
        interactions = query.order_by(Interaction.created_at.desc()).limit(limit * 3).all()
        interactions.reverse()  # Oldest first
        
        # Convert to context format, handling reminders (standalone 'ai' messages)
        context = []
        i = 0
        while i < len(interactions):
            msg = interactions[i]
            
            # If it's a user message, expect AI response next
            if msg.message_type == 'user':
                if i + 1 < len(interactions) and interactions[i + 1].message_type == 'ai':
                    context.append({
                        'user': msg.content,
                        'agent': interactions[i + 1].content
                    })
                    i += 2
                else:
                    # User message without AI response - skip
                    i += 1
            
            # If it's a standalone AI message (reminder), add it as system context
            elif msg.message_type == 'ai':
                # Add reminder as a synthetic userai pair for context continuity
                context.append({
                    'user': '[  ]',
                    'agent': msg.content
                })
                i += 1
            else:
                i += 1
        
        return context[-limit:] if len(context) > limit else context
    finally:
        session.close()


def save_context_to_db(user_id, user_message, ai_message):
    """Save chat interaction to Interaction table"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return
        
        # Save user message
        user_interaction = Interaction(
            user_id=user.id,
            message_type='user',
            content=user_message,
            created_at=datetime.now(dt_timezone.utc)
        )
        session.add(user_interaction)
        
        # Save AI message only if provided
        if ai_message is not None:
            ai_interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=ai_message,
                created_at=datetime.now(dt_timezone.utc)
            )
            session.add(ai_interaction)
        
        session.commit()
    finally:
        session.close()


async def get_timezone_from_ip(ip_address):
    """Определяет timezone по IP адресу через ipapi.co"""
    # Маппи алийских зай городо  русские
    city_mapping = {
        'Moscow': 'Москва',
        'Saint Petersburg': 'Санкт-Петербург',
        'Kazan': 'Казань',
        'Novosibirsk': 'Новосибирск',
        'Yekaterinburg': 'Екатеринбург',
        'Nizhny Novgorod': 'Нижний Новгород',
        'Chelyabinsk': 'Челябинск',
        'Omsk': 'Омск',
        'Samara': 'Самара',
        'Rostov-on-Don': 'Ростов-на-Дону',
        'Ufa': 'Уфа',
        'Krasnoyarsk': 'Красноярск',
        'Voronezh': 'Воронеж',
        'Perm': 'Пермь',
        'Volgograd': 'Волгоград',
        'Krasnodar': 'Краснодар',
        'Saratov': 'Саратов',
        'Tyumen': 'Тюмень',
        'Tolyatti': 'Тольятти',
        'Izhevsk': 'Ижевск',
        'Barnaul': 'Барнаул',
        'Ulyanovsk': 'Ульяск',
        'Irkutsk': 'ркутск',
        'Khabarovsk': 'Хабароск',
        'Vladivostok': 'Владивосток',
        'Yaroslavl': 'Ярослаль',
        'Vladimir': 'Владимир',
        'Ivanovo': 'ао',
        'Bryansk': 'Брянск',
        'Smolensk': 'Смоленск',
        'Kaluga': 'Калуга',
        'Tula': 'Тула',
        'Ryazan': 'Рязань',
        'Moscow Oblast': 'Москоская область',
        'Leningrad Oblast': 'Лерадская область'
    }

    try:
        # грируем локальные IP
        if ip_address.startswith(('127.', '192.168.', '10.', '172.')):
            return 'Europe/Moscow', 'Москва'  # По умолчанию для локальных

        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://ipapi.co/{ip_address}/json/', timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    timezone = data.get('timezone')
                    city = data.get('city')

                    # Преобразуем алийское зае города  русское, если есть  маппие
                    if city and city in city_mapping:
                        city = city_mapping[city]

                    logger.info(f"Detected timezone: {timezone}, city: {city} for IP: {ip_address}")
                    return timezone if timezone else 'UTC', city
    except Exception as e:
        logger.error(f"Error getting timezone from IP {ip_address}: {e}")
    return 'UTC', None


async def get_user_avatar_url(bot, user_id, force_refresh=False):
    """Получает URL аватара пользователя из Telegram или БД
    
    Args:
        bot: Telegram bot instance
        user_id: Telegram user ID
        force_refresh: If True, always fetch fresh avatar from Telegram API, bypassing cache
    """
    try:
        from models import User
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            
            # Если  требуется принудителье облее и есть кэшироаый аатар, озращаем его
            if not force_refresh and user and user.photo_url:
                logger.debug(f"Returning cached avatar for user {user_id}")
                return user.photo_url
            
            # Загружаем сежий аатар из Telegram
            if bot:
                try:
                    photos = await bot.get_user_profile_photos(user_id, limit=1)
                    if photos.total_count > 0:
                        file = await bot.get_file(photos.photos[0][-1].file_id)
                        avatar_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
                        
                        # Сохраняем  БД для кэшироая
                        if user:
                            user.photo_url = avatar_url
                            db.commit()
                            logger.info(f"Updated avatar for user {user_id} (force_refresh={force_refresh})")
                        
                        return avatar_url
                except Exception as e:
                    logger.debug(f"Could not fetch avatar from Telegram for user {user_id}: {e}")
            
            logger.debug(f"No avatar available for user {user_id}")
            return None
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error getting avatar for user {user_id}: {e}")
        return None


def check_telegram_authentication(data):
    # Проерка аторизации от Telegram
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


# ═══ IndexNow: мгновенное уведомление поисковиков ═══
INDEXNOW_KEY = 'd6193b04262141bba808b1279123715b'

async def notify_indexnow(urls: list):
    """Отправить URL-ы в IndexNow для мгновенной индексации Bing/Yandex"""
    if LOCAL:
        return
    try:
        import aiohttp
        payload = {
            "host": "asibiont.ru",
            "key": INDEXNOW_KEY,
            "keyLocation": f"https://asibiont.ru/{INDEXNOW_KEY}.txt",
            "urlList": urls
        }
        async with aiohttp.ClientSession() as session:
            # Bing/Yandex оба поддерживают IndexNow
            for endpoint in ['https://api.indexnow.org/indexnow',
                             'https://yandex.com/indexnow']:
                try:
                    async with session.post(endpoint, json=payload,
                                           headers={'Content-Type': 'application/json'},
                                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logger.info(f"[IndexNow] {endpoint} -> {resp.status}")
                except Exception as e:
                    logger.warning(f"[IndexNow] {endpoint} error: {e}")
    except Exception as e:
        logger.warning(f"[IndexNow] Failed: {e}")


async def login_handler(request):
    """Страца аторизации"""
    session = await get_session(request)
    user_id = session.get('user_id')

    # Check for logout parameter
    if request.query.get('logout') == '1':
        session.pop('user_id', None)
        session.pop('history_cleared_timestamp', None)
        user_id = None

    # Если пользователь уже залогинен, редиректим в dashboard
    if user_id:
        try:
            user_id = int(user_id)
            return web.HTTPFound('/dashboard')
        except (ValueError, TypeError):
            pass

    # Показыаем страцу аторизации
    bot_user = TELEGRAM_BOT_USERNAME.replace(
        '@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'asibiont_bot')
    
    # Формируем auth_url для иджета Telegram
    base_url = str(request.url.origin())
    auth_url = f"{base_url}/tg_auth"
    
    return aiohttp_jinja2.render_template('index.html', request, {
        'logged_in': False,
        'bot_username': bot_user,
        'auth_url': auth_url,
        'subscription_tier': 'LIGHT',
        'current_date': '',
        'current_time': '',
        'formatted_end_date': None,
        'timestamp': 1769939740,
        'user_timezone': 'UTC',
        'user': None,
        'profile': None,
        'tasks': [],
        'messages': [],
        'partners': [],
        'subscription': None
    })


async def auth_handler(request):
    try:
        data = dict(request.query)
        logger.info(f"Auth handler called with data keys: {list(data.keys())}")

        if check_telegram_authentication(data):
            user_id = int(data['id'])
            logger.info(f"Authentication successful for user_id: {user_id}")

            # Check for referral
            referrer_telegram_id = None
            if 'start' in data and data['start'].startswith('ref'):
                try:
                    referrer_telegram_id = int(data['start'][3:])
                    logger.info(f"Referral detected: referrer_telegram_id={referrer_telegram_id}")
                except ValueError:
                    logger.error(f"Invalid referrer ID in start parameter: {data['start']}")

            session_db = None
            try:
                session_db = Session()
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
                            avatar_url = await get_user_avatar_url(request.app['bot'], user_id, force_refresh=True)
                            logger.info(f"Got avatar URL for new user {user_id}: {avatar_url}")
                        except Exception as e:
                            logger.error(f"Error getting avatar for new user {user_id}: {e}")

                    # Find referrer
                    referrer = None
                    if referrer_telegram_id:
                        referrer = session_db.query(User).filter_by(telegram_id=referrer_telegram_id).first()
                        if referrer:
                            logger.info(f"Referrer found: {referrer.id}")
                        else:
                            logger.warning(f"Referrer not found for telegram_id: {referrer_telegram_id}")

                    user = User(
                        telegram_id=user_id,
                        username=data.get('username'),
                        first_name=data.get('first_name'),
                        photo_url=avatar_url,
                        timezone=timezone,
                        referrer_id=referrer.id if referrer else None)
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
                    # Update avatar from Telegram API on every login to ensure it's always fresh
                    if 'bot' in request.app:
                        try:
                            avatar_url = await get_user_avatar_url(request.app['bot'], user_id, force_refresh=True)
                            if avatar_url:
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
            except Exception as e:
                logger.error(f"Database error in auth_handler: {e}", exc_info=True)
                if session_db:
                    session_db.rollback()
                return web.Response(text='Ошибка подключения к базе данных. Попробуйте позже.', status=500)
            finally:
                if session_db:
                    session_db.close()

            try:
                session = await get_session(request)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Corrupted session during auth, creating new: {e}")
                from aiohttp_session import new_session
                session = await new_session(request)
            
            session['user_id'] = user_id
            logger.info(f"Session set with user_id: {user_id}, session keys: {list(session.keys())}")

            response = web.HTTPFound('/dashboard')
            logger.info("Redirecting to /dashboard after auth")
            return response
        else:
            logger.error(f"Authentication failed for data: {data}")
            return web.Response(text='Authentication failed', status=401)
    except Exception as e:
        logger.error(f"CRITICAL ERROR in auth_handler: {e}", exc_info=True)
        return web.Response(text=f'Internal server error: {str(e)}', status=500)


async def logout_handler(request):
    session = await get_session(request)
    session.clear()
    return web.HTTPFound('/')


@aiohttp_jinja2.template('dashboard_new.html')
async def dashboard_handler(request):
    logger.info(f"Dashboard handler called for path: {request.path}")
    try:
        user_id = await get_user_id_from_request(request)
        logger.info(f"User ID: {user_id} (type: {type(user_id)})")

        logged_in = bool(user_id)

        # Redirect to login page if not logged in
        if not logged_in:
            return web.HTTPFound('/')

        # Получить задачи пользователя
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                # Redirect to login page if user not found
                return web.HTTPFound('/')

            logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")
            
            # Проерить подписку
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()

            # Проерить и обить статус истекших подписок
            if subscription and subscription.status == 'active' and subscription.end_date:
                now = datetime.now(pytz.UTC)
                if subscription.end_date.tzinfo is None:
                    subscription.end_date = subscription.end_date.replace(tzinfo=pytz.UTC)
                if subscription.end_date < now:
                    subscription.status = 'expired'
                    # user.subscription_tier = SubscriptionTier.LIGHT  # Сбросить тариф в бронзу при истечении - убрано по просьбе пользователя
                    session_db.commit()
                    logger.info(f"Subscription {subscription.id} expired, status set to 'expired'")

            # Синхронизировать тариф пользователя с активной подпиской
            if subscription and subscription.status == 'active' and subscription.tier:
                sub_tier = subscription.tier.value if hasattr(subscription.tier, 'value') else str(subscription.tier).upper()
                user_tier = user.subscription_tier.value if user.subscription_tier else None

                if sub_tier != user_tier:
                    logger.info(f"Syncing user tier: {user_tier} -> {sub_tier}")
                    if sub_tier == 'LIGHT':
                        user.subscription_tier = SubscriptionTier.LIGHT
                    elif sub_tier == 'STANDARD':
                        user.subscription_tier = SubscriptionTier.STANDARD
                    elif sub_tier == 'PREMIUM':
                        user.subscription_tier = SubscriptionTier.PREMIUM
                    session_db.commit()
                    logger.info(f"User {user.username} tier synced to {sub_tier}")

            logger.info(
                f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}, tier: {subscription.tier if subscription else None}, user_tier: {user.subscription_tier.value if user.subscription_tier else None}")

            # В FREE_ACCESS_MODE  требуется актия подписка
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
                logger.info("No active subscription, redirecting to subscription_tiers")
                return web.HTTPFound('/subscription_tiers')

            tasks = session_db.query(Task).filter(
                or_(
                    Task.user_id == user.id,
                    and_(Task.delegated_to_username.isnot(None), Task.delegated_to_username.ilike(user.username)) if user.username else False
                )
            ).all()
            logger.info(f"Found {len(tasks)} tasks for user {user.id} (telegram_id: {user.telegram_id})")
            for task in tasks:
                logger.info(f"Task {task.id}: {task.title} (user_id: {task.user_id})")
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None

            # Проеряем timestamp очистки истории из БД
            history_cleared_timestamp = None
            if user.history_cleared_at:
                history_cleared_timestamp = user.history_cleared_at.timestamp()
                logger.info(f"History cleared timestamp from DB: {history_cleared_timestamp}")

            # Берем последе 50 сообщей,  фильтруем по timestamp очистки
            if user:
                all_interactions = list(
                    reversed(
                        session_db.query(Interaction).filter_by(
                            user_id=user.id).order_by(
                            Interaction.id.desc()).limit(50).all()))
                if history_cleared_timestamp:
                    # Фильтруем только сообщения после очистки
                    filtered_interactions = []
                    for i in all_interactions:
                        try:
                            # Если created_at naive (без tzinfo), считаем его UTC и просто берем timestamp
                            # Если с tzinfo, используем его timestamp
                            if i.created_at.tzinfo is None:
                                # Naive datetime - интерпретируем как UTC прямую через replace
                                interaction_ts = i.created_at.replace(tzinfo=dt_timezone.utc).timestamp()
                            else:
                                interaction_ts = i.created_at.timestamp()

                            logger.info(
                                f"Interaction ID {i.id}: created_at={i.created_at}, timestamp={interaction_ts}, clear_timestamp={history_cleared_timestamp}, include={interaction_ts > history_cleared_timestamp}")

                            if interaction_ts > history_cleared_timestamp:
                                filtered_interactions.append(i)
                        except Exception as e:
                            logger.error(f"Error processing interaction {i.id} timestamp: {e}")
                            # В случае ошибки НЕ включаем сообщение (безопаснее скрыть)

                    interactions = filtered_interactions
                    logger.info(
                        f"Filtered {len(interactions)} interactions from {len(all_interactions)} total after timestamp {history_cleared_timestamp}")
                else:
                    interactions = all_interactions
                    logger.info(f"Loaded {len(interactions)} interactions (no filtering)")
            else:
                interactions = []

            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first() if user else None

            # Get user subscription tier
            user_subscription_tier = user.subscription_tier if user and user.subscription_tier else SubscriptionTier.LIGHT
            display_tier = user_subscription_tier.value if user_subscription_tier else 'LIGHT'

            # Получить контакты по делегироаю
            delegating_to_me = []  # Люди, которые делегироали м задачи
            delegating_by_me = []  # Люди, которым я делегироал задачи

            try:
                # Получить список избраых контакто
                favorite_contacts = []
                if profile and profile.favorite_contacts:
                    try:
                        raw_favorites = json.loads(profile.favorite_contacts)
                        favorite_contacts = [str(c).lower().replace('@', '') if isinstance(c, str) else str(c) for c in raw_favorites]
                    except json.JSONDecodeError:
                        favorite_contacts = []

                # Люди, которые делегироали м задачи (я получаю задачи от х)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username.ilike(user.username.replace('@', '')),
                    Task.delegation_status.in_(['pending', 'accepted']),
                    Task.status != 'deleted',
                    Task.status != 'rejected'
                ).all()

                delegator_ids = set()
                for task in delegated_tasks:
                    if task.delegated_by and task.delegated_by not in delegator_ids:
                        delegator_ids.add(task.delegated_by)
                        delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                        if delegator and delegator.id != user.id:
                            delegator_tasks = [t for t in delegated_tasks if t.delegated_by == delegator.id]
                            task_count = len(delegator_tasks)
                            task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegator_tasks[:3]]
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'reason': f'делегироал {task_count} задач',
                                'tasks': task_titles,
                                'task_count': task_count
                            })

                # Добаить избраые контакты, у которых се задачи отклоны,  контакт  избраом
                for favorite_username in favorite_contacts:
                    favorite_user = session_db.query(User).filter(
                        User.username.ilike(favorite_username)
                    ).first()
                    
                    if favorite_user and favorite_user.id != user.id and favorite_user.id not in delegator_ids:
                        # Проерить, были ли у этого контакта задачи (ключая отклоые)
                        all_tasks_from_favorite = session_db.query(Task).filter(
                            Task.user_id == favorite_user.id,
                            Task.delegated_to_username.ilike(user.username.replace('@', ''))
                        ).all()
                        
                        if all_tasks_from_favorite:
                            # Есть история делегирования - добавляем в список
                            rejected_count = sum(1 for t in all_tasks_from_favorite if t.status == 'rejected')
                            if rejected_count > 0:
                                delegating_to_me.append({
                                    'id': favorite_user.id,
                                    'username': favorite_user.username,
                                    'first_name': favorite_user.first_name,
                                    'reason': ' избраом',
                                    'tasks': [],
                                    'task_count': 0
                                })

                # Люди, которым я делегироал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_by == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(['pending', 'accepted'])
                ).all()

                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(
                            or_(
                                User.username.ilike(task.delegated_to_username.replace('@', '')),
                                User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                            )
                        ).first()
                        if delegatee and delegatee.id != user.id:
                            delegatee_tasks = [
                                t for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                            task_count = len(delegatee_tasks)
                            task_titles = [t.title[:30] + '...' if len(t.title) > 30 else t.title for t in delegatee_tasks[:3]]
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'reason': f'я делегироал {task_count} задач',
                                'tasks': task_titles,
                                'task_count': task_count
                            })

                # Добавляем рекомендованные контакты для всех пользователей
                # Получаем се рекомеоаые контакты
                all_partners = get_partners_list(user.id, session_db)
                
                # Добавляем контакты, которые еще не в списках делегирования
                existing_contact_ids = set()
                for contact in delegating_to_me + delegating_by_me:
                    existing_contact_ids.add(contact['id'])
                
                for partner in all_partners:
                    partner_user = session_db.query(User).filter_by(id=partner.user_id).first()
                    if partner_user and partner_user.id not in existing_contact_ids and partner_user.id != user.id:
                        # Определяем причину рекомеации
                        reason_parts = []
                        if hasattr(partner, 'common_interests') and partner.common_interests:
                            reason_parts.append(f"общие интересы: {partner.common_interests}")
                        if hasattr(partner, 'common_skills') and partner.common_skills:
                            reason_parts.append(f"общие ыки: {partner.common_skills}")
                        if hasattr(partner, 'common_goals') and partner.common_goals:
                            reason_parts.append(f"общие цели: {partner.common_goals}")
                        if hasattr(partner, 'task_relevance') and partner.task_relevance:
                            reason_parts.append(partner.task_relevance)
                        
                        reason = ', '.join(reason_parts) if reason_parts else 'рекомеоан системой'
                        
                        # Добавляем в delegating_to_me как рекомендованный контакт
                        delegating_to_me.append({
                            'id': partner_user.id,
                            'username': partner_user.username,
                            'first_name': partner_user.first_name,
                            'reason': reason,
                            'tasks': [],
                            'task_count': 0,
                            'common_interests': partner.common_interests if hasattr(partner, 'common_interests') else None,
                            'common_skills': partner.common_skills if hasattr(partner, 'common_skills') else None,
                            'common_goals': partner.common_goals if hasattr(partner, 'common_goals') else None,
                            'common_tasks': partner.common_tasks if hasattr(partner, 'common_tasks') else None,
                            'contact_info': partner_user.username if partner_user.username else None,
                            'photo_url': partner_user.photo_url if hasattr(partner_user, 'photo_url') else None,
                            'city': partner.city if hasattr(partner, 'city') else None,
                            'average_rating': partner.average_rating if hasattr(partner, 'average_rating') else 0
                        })

            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []

            # Получить заблокироаые контакты
            blocked_contacts = []
            try:
                if profile and profile.blocked_contacts:
                    blocked_usernames = json.loads(profile.blocked_contacts)
                    for username in blocked_usernames:
                        blocked_user = session_db.query(User).filter(User.username.ilike(username.replace('@', ''))).first()
                        if blocked_user and blocked_user.id != user.id:
                            blocked_contacts.append({
                                'id': blocked_user.id,
                                'username': blocked_user.username,
                                'first_name': blocked_user.first_name,
                                'photo_url': blocked_user.photo_url,
                                'reason': 'заблокироаый контакт'
                            })
            except Exception as e:
                logger.error(f"Error getting blocked contacts: {e}")
                blocked_contacts = []

        finally:
            session_db.close()

        try:
            # Get user to convert telegram_id to database user.id
            session_db = Session()
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                session_db.close()
                return web.json_response({'error': 'User not found'}, status=404)
            
            partners = get_partners_list(user_id=user.id)
            session_db.close()

            # Apply subscription-based contact limits
            if partners and user_subscription_tier:
                tier = user_subscription_tier.value
                if tier == 'LIGHT':
                    partners = partners[:1]  # Light: 1 contact
                elif tier == 'STANDARD':
                    partners = partners[:5]  # Standard: 5 contacts
                # Premium: unlimited (already limited to 20 in get_partners_list)

        except Exception as e:
            logger.error(f"Error getting partners: {e}", exc_info=True)
            partners = []
            delegating_to_me = []
            delegating_by_me = []

        # Add common interests, skills, goals and recommendation reason
        if profile and partners:
            user_interests = set(i.strip().lower()
                                 for i in profile.interests.split(',')) if profile.interests else set()
            user_skills = set(s.strip().lower() for s in profile.skills.split(',')) if profile.skills else set()
            user_goals = set(g.strip().lower() for g in profile.goals.split(',')) if profile.goals else set()

            # Получаем список контакто, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - improved matching with partial string matching
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(',') if i.strip())
                    common = user_interests & partner_interests
                    # Also check for partial matches (e.g., "спорт" matches "спорт, футбол")
                    if not common:
                        for ui in user_interests:
                            for pi in partner_interests:
                                if ui and pi and (ui in pi or pi in ui):
                                    common.add(pi)
                    p.common_interests = ', '.join(sorted(common)) if common else None
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
                    reasons.append('общие ыки')
                if p.common_interests:
                    reasons.append('общие интересы')
                if p.common_goals:
                    reasons.append('общие цели')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('из ашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'

        # Add photo_url to partners
        if partners:
            session_db = Session()
            try:
                for p in partners:
                    partner_user = session_db.query(User).filter_by(id=p.user_id).first()
                    if partner_user:
                        p.photo_url = partner_user.photo_url
                    else:
                        p.photo_url = None
            finally:
                session_db.close()

        user_tz = pytz.UTC
        if user and user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC

        base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)

        current_time = user_now.strftime('%H:%M')

        months = [
            'яаря',
            'фераля',
            'марта',
            'апреля',
            'мая',
            'июня',
            'июля',
            'агуста',
            'сентября',
            'октября',
            'ября',
            'декабря']
        current_date = user_now.strftime('%d.%m.%Y')

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
                        day_word = "день" if days == 1 else "дня" if days < 5 else "дней"
                        task.overdue_text = f"{days} {day_word}"
                    elif hours > 0:
                        hour_word = "час" if hours == 1 else "часа" if hours < 5 else "часов"
                        task.overdue_text = f"{hours} {hour_word}"
                    elif minutes > 0:
                        min_word = "минуту" if minutes == 1 else "минуты" if minutes < 5 else "минут"
                        task.overdue_text = f"{minutes} {min_word}"
                    else:
                        task.overdue_text = "только что"
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
            end_local = end_dt.astimezone(user_tz if user.timezone else pytz.timezone('Europe/Moscow'))
            formatted_end_date = f"{end_local.day:02d}.{end_local.month:02d}.{end_local.year}"

        # Calculate upcoming reminders
        upcoming_reminders = []
        if user:
            for task in tasks:
                if task.reminder_time:
                    if task.reminder_time.tzinfo is None:
                        task.reminder_time = task.reminder_time.replace(tzinfo=pytz.UTC)
                    if task.reminder_time.astimezone(
                            user_tz if user.timezone else pytz.timezone('Europe/Moscow')) > user_now and task.status == 'pending':
                        reminder_time_local = task.reminder_time.astimezone(
                            user_tz if user.timezone else pytz.timezone('Europe/Moscow')).strftime("%H:%M")
                        upcoming_reminders.append(f"{task.title}  {reminder_time_local}")

        #      JSON 
        tasks_dict = []
        for task in tasks:
            # Подготоим reminder_time  ISO формате для JavaScript
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
                'reminder_time': reminder_time_iso,  #    JS
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
                updated_avatar_url = await get_user_avatar_url(request.app['bot'], user_id, force_refresh=True)
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

        logger.info(f"Rendering dashboard for user {user.id} with subscription_tier: {display_tier}")

        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': True,
            'tasks': tasks_dict,
            'user': user,
            'profile': profile,
            'telegram_channel': user.telegram_channel if user else None,
            'interactions': interactions,
            'partners': partners,
            'delegating_to_me': delegating_to_me,
            'delegating_by_me': delegating_by_me,
            'blocked_contacts': blocked_contacts,
            'subscription': subscription,
            'subscription_tier': display_tier,
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'pending_tasks': pending_tasks,
            'skipped_tasks': skipped_tasks,
            'current_date': current_date,
            'current_time': current_time,
            'user_timezone': user.timezone if user and user.timezone else 'UTC',
            'formatted_end_date': formatted_end_date,
            'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
            'timestamp': 1769939740,
            'bot_username': TELEGRAM_BOT_USERNAME.replace('@', ''),
            'user_avatar_url': user_avatar_url,
            'referral_balance': user.referral_balance
        })
    except Exception as e:
        logger.error(f"Unexpected error in dashboard_handler: {e}", exc_info=True)
        bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'asibiont_bot'
        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'bot_username': bot_user,
            'subscription_tier': 'LIGHT',
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': 1738138953
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

        # Load context from DB
        context = get_context_from_db(user_id, limit=10)
        logger.info(f"Loaded context with {len(context)} message pairs from DB")

        logger.info(f"[WEB CHAT] New message from user {user_id}: '{message[:100]}...'")

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            logger.info(f"User found: {user is not None}")

            # КРИТИЧНО: Сохраняем сообщение пользователя ДО вызова AI
            # Это гарантирует, что сообщение появится в истории даже если AI упадет
            #  предотращает race condition с дубликатами
            save_context_to_db(user_id, message, None)
            logger.info("User message saved to DB BEFORE AI call")

            # Get AI response (will take time, so agent timestamp will be later)
            try:
                logger.info(f"Calling chat_with_ai with user_id: {user_id}")
                ai_result = await chat_with_ai(message, context, user_id, file_content, db_session=session_db)
                response = ai_result['response']
                logger.info("AI response: %s...", response[:100])
            except Exception as e:
                logger.error(f"Error getting AI response: {e}", exc_info=True)
                response = f"Ошибка: {str(e)}"

            # Save agent response to Interaction table
            if user:
                agent_response_timestamp = datetime.now(dt_timezone.utc)
                interaction_agent = Interaction(
                    user_id=user.id,
                    message_type='ai',
                    content=response,
                    created_at=agent_response_timestamp
                )
                session_db.add(interaction_agent)
                session_db.commit()
                logger.info("Saved AI response to database")
        finally:
            session_db.close()

        return web.json_response({'response': response})
    except Exception as e:
        logger.error(f"Unexpected error in chat_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_send_message_handler(request):
    """API endpoint for sending messages from frontend (delegation, task actions)"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        logger.info(f"[API_SEND_MESSAGE] Called, session user_id: {user_id}")

        if not user_id:
            logger.warning("[API_SEND_MESSAGE] No user_id in session")
            return web.json_response({'error': 'Not authenticated'}, status=401)

        data = await request.json()
        message = data.get('message', '')
        logger.info(f"[API_SEND_MESSAGE] Message received from user {user_id}: '{message}'")

        # Check for duplicate first
        # Duplicate check removed

        # Load context from DB
        context = get_context_from_db(user_id, limit=20)
        logger.info(f"[API_SEND_MESSAGE] Loaded context: {len(context)} messages")

        # Import chat function
        from ai_integration.chat import chat_with_ai as chat

        # Get user from database
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.error(f"[API_SEND_MESSAGE] User not found: {user_id}")
                return web.json_response({'error': 'User not found'}, status=404)

            logger.info(f"[API_SEND_MESSAGE] Calling AI for user {user_id}...")
            # Call AI chat
            try:
                result = await chat(message, context=context, user_id=user_id, file_content=None, db_session=session_db)
                logger.info(f"[API_SEND_MESSAGE] AI result received, type: {type(result)}")
                
                # Handle both string and dict responses
                if isinstance(result, dict):
                    response = result.get('response', '')
                    tool_calls = result.get('tool_calls', [])
                    logger.info(f"[API_SEND_MESSAGE] Dict response: response length {len(response)}, tool_calls: {len(tool_calls)}")
                else:
                    response = result
                    tool_calls = []
                    logger.info(f"[API_SEND_MESSAGE] String response, length: {len(response) if response else 0}")
                
                logger.info(f"[API_SEND_MESSAGE] AI response preview: '{response[:100]}...'")
                
                # Execute tool calls if any
                if tool_calls:
                    logger.info(f"[API_SEND_MESSAGE] Executing {len(tool_calls)} tool calls")
                    from ai_integration import handlers
                    for tool_call in tool_calls:
                        try:
                            func_name = tool_call.get('function', {}).get('name')
                            args = tool_call.get('function', {}).get('arguments', '{}')
                            args_dict = json.loads(args) if args else {}
                            logger.info(f"[API_SEND_MESSAGE] Executing {func_name} with args {args_dict}")
                            
                            func = getattr(handlers, func_name, None)
                            if func:
                                if asyncio.iscoroutinefunction(func):
                                    await func(user_id=user_id, **args_dict)
                                else:
                                    func(user_id=user_id, **args_dict)
                                logger.info(f"[API_SEND_MESSAGE] Tool {func_name} executed successfully")
                            else:
                                logger.error(f"[API_SEND_MESSAGE] Tool {func_name} not found in handlers")
                        except Exception as e:
                            logger.error(f"[API_SEND_MESSAGE] Error executing tool {func_name}: {e}")
                else:
                    logger.info(f"[API_SEND_MESSAGE] No tool calls to execute")
                if response is None or response == '':
                    logger.error("[API_SEND_MESSAGE] AI response is empty!")
                    response = "зите, произошла ошибка при обработке ашего запроса. Попробуйте еще раз."
            except Exception as e:
                logger.error(f"[API_SEND_MESSAGE] Error calling AI chat: {e}", exc_info=True)
                return web.json_response({'error': 'AI service error'}, status=500)

            # Check if response contains tier restriction error
            if "Делегироае задач доступ только  тарифах" in response:
                logger.info(f"[API_SEND_MESSAGE] Tier restriction detected for user {user_id}")
                return web.json_response({
                    'error': 'tier_restriction',
                    'message': '🥉 Делегироае задач доступ только  тарифах Стаарт и Премиум',
                    'tier': 'LIGHT',
                    'upgrade_url': '/subscription_tiers'
                }, status=403)

            # Check for duplicate message before saving
            # Duplicate check removed - always save
                logger.info(f"[API_SEND_MESSAGE] Context saved to DB: user_msg='{message[:50]}...', ai_response='{response[:50]}...'")
        finally:
            session_db.close()
            logger.info(f"[API_SEND_MESSAGE] DB session closed for user {user_id}")

        logger.info(f"[API_SEND_MESSAGE] Returning success response for user {user_id}")
        return web.json_response({'response': response, 'success': True})
    except Exception as e:
        logger.error(f"Unexpected error in api_send_message_handler: {e}", exc_info=True)
        # Return detailed error for debugging
        return web.json_response({
            'error': 'Internal server error',
            'details': str(e),
            'type': type(e).__name__
        }, status=500)


async def clear_history_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"Clear history for user_id: {user_id}")
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    # Обляем history_cleared_at  БД
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            user.history_cleared_at = datetime.now(dt_timezone.utc)
            session_db.commit()
            logger.info(f"History cleared, timestamp set to {user.history_cleared_at}")
    finally:
        session_db.close()

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

        # щем задачу либо среди соих, либо среди делегироаых м
        query_conditions = [Task.id == task_id, Task.user_id == user.id]
        if user.username:
            query_conditions.append(Task.delegated_to_username.ilike(user.username))
        
        task = session_db.query(Task).filter(or_(*query_conditions)).first()
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
    """Заершает задачу по ID"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    logger.info(f"[COMPLETE_TASK_HANDLER] Starting completion for task_id={task_id}, user_id={user_id}")

    from ai_integration.handlers import complete_task
    try:
        result = await complete_task(task_id=task_id, user_id=user_id)
        logger.info(f"[COMPLETE_TASK_HANDLER] Task {task_id} completed by user {user_id}: {result}")
        
        # Проеряем статус задачи после заершея
        from models import Task
        db_session = Session()
        try:
            task = db_session.query(Task).filter_by(id=task_id).first()
            if task:
                logger.info(f"[COMPLETE_TASK_HANDLER] Task {task_id} status after completion: {task.status}")
            else:
                logger.error(f"[COMPLETE_TASK_HANDLER] Task {task_id} not found after completion")
        finally:
            db_session.close()
        
        # Отправляем уведомление в Telegram через AI обработку, как будто пользователь писал о выполнении
        try:
            if 'bot' in request.app:
                from models import Session as DBSession, User
                from ai_integration.chat import chat_with_ai
                db_session = DBSession()
                try:
                    # Находим пользователя по user_id (это telegram_id)
                    user = db_session.query(User).filter_by(telegram_id=user_id).first()
                    if user:
                        from models import Task
                        task = db_session.query(Task).filter_by(id=task_id, user_id=user.id).first()
                        if task:
                            # Отправляем сообщение через AI, как будто пользователь писал о выполнении
                            ai_message = f"я выполнил задачу '{task.title}'"
                            try:
                                ai_result = await chat_with_ai(ai_message, user_id=user_id)
                                ai_response = ai_result['response']
                                await request.app['bot'].send_message(chat_id=user_id, text=ai_response)
                                
                                # Сохраняем взаимодействие в базу данных для отображения в веб-панели
                                interaction = Interaction(
                                    user_id=user.id,
                                    message_type='ai',
                                    content=ai_response,
                                    created_at=datetime.now(dt_timezone.utc)
                                )
                                db_session.add(interaction)
                                db_session.commit()
                                
                                logger.info(f"Sent AI-processed task completion notification to Telegram user {user_id}")
                            except Exception as ai_error:
                                # Fallback на простое уведомление, если AI не сработал
                                logger.warning(f"AI processing failed, using fallback: {ai_error}")
                                notification_text = f"✅ Задача выполнена: {task.title}"
                                await request.app['bot'].send_message(chat_id=user_id, text=notification_text)
                                
                                # Сохраняем fallback взаимодействие в базу данных
                                interaction = Interaction(
                                    user_id=user.id,
                                    message_type='ai',
                                    content=notification_text,
                                    created_at=datetime.now(dt_timezone.utc)
                                )
                                db_session.add(interaction)
                                db_session.commit()
                                
                                logger.info(f"Sent fallback task completion notification to Telegram user {user_id}")
                finally:
                    db_session.close()
        except Exception as notification_error:
            logger.error(f"Error sending completion notification: {notification_error}")
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error completing task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def restore_task_handler(request):
    """Воссталиает задачу  работу"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration.handlers import restore_task
    try:
        result = await restore_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} restored by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error restoring task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def skip_task_handler(request):
    """Пропускает задачу"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration.handlers import skip_task
    try:
        result = await skip_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} skipped by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error skipping task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def delete_task_handler(request):
    """Удаляет задачу"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration.handlers import delete_task
    try:
        # Передаём confirmed=True, поскольку пользователь уже подтвердил удаление в UI
        result = await delete_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} deleted by user {user_id}: {result}")
        
        # Если результат содержит флаг, обработаем через AI и отпраим  Telegram
        if result.startswith('TASK_COMPLETED_ASK_RESULT:') or result.startswith('TASK_UPDATED:') or result.startswith('TASK_DELETED_ASK_REASON:'):
            try:
                from ai_integration.chat import chat_with_ai
                from models import Session as DBSession, User
                db_session = DBSession()
                try:
                    # Обработка через AI для генерации естественного ответа
                    ai_result = await chat_with_ai(result, user_id=user_id, db_session=db_session)
                    ai_response = ai_result['response']
                    
                    # Отправляем AI ответ в Telegram если бот доступен
                    if 'bot' in request.app and ai_response:
                        await request.app['bot'].send_message(chat_id=user_id, text=ai_response)
                        
                        # Сохраняем взаимодействие в базу данных для отображения в веб-панели
                        user = db_session.query(User).filter_by(telegram_id=user_id).first()
                        if user:
                            interaction = Interaction(
                                user_id=user.id,
                                message_type='ai',
                                content=ai_response,
                                created_at=datetime.now(dt_timezone.utc)
                            )
                            db_session.add(interaction)
                            db_session.commit()
                        
                        logger.info(f"Sent AI response to Telegram user {user_id}")
                finally:
                    db_session.close()
            except Exception as ai_error:
                logger.error(f"Error processing result through AI: {ai_error}")
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def cancel_delegation_handler(request):
    """Отменяет делегироае задачи"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration.handlers import cancel_delegation
    try:
        result = cancel_delegation(task_id=task_id, user_id=user_id)
        logger.info(f"Delegation cancelled for task {task_id} by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error cancelling delegation for task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def reschedule_task_handler(request):
    """Пересит задачу  ое ремя"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_title = data.get('task_title')
    new_time = data.get('new_time')
    if not task_title or not new_time:
        return web.json_response({'error': 'Task title and new time required'}, status=400)

    from ai_integration.handlers import reschedule_task
    try:
        result = await reschedule_task(task_title=task_title, new_time=new_time, user_id=user_id)
        logger.info(f"Task '{task_title}' rescheduled by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error rescheduling task '{task_title}': {e}")
        return web.json_response({'error': str(e)}, status=500)












async def direct_login_handler(request):
    """Direct login for local testing"""
    from config import LOCAL
    if not LOCAL:
        return web.json_response({'status': 'disabled'}, status=403)

    # For local testing, allow direct login with user_id parameter
    user_id = request.query.get('user_id')
    if not user_id:
        return web.json_response({'error': 'user_id parameter required'}, status=400)

    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        return web.json_response({'error': 'Invalid user_id'}, status=400)

    session = await get_session(request)
    session['user_id'] = user_id
    return web.json_response({'status': 'logged_in', 'user_id': user_id})


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
    dp = Dispatcher()
    dp.include_router(handlers_router)

# Middleware to add CSP headers and disable cache for static files


@web.middleware
async def session_error_middleware(request, handler):
    """Handle corrupted session cookies"""
    try:
        return await handler(request)
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted session cookie detected: {e}, clearing cookie")
        # Create response without session cookie
        response = web.Response(status=302)
        response.headers['Location'] = request.path
        response.del_cookie('AIOHTTP_SESSION', domain=None, path='/')
        return response
    except Exception as e:
        logger.error(f"Session error: {e}")
        raise


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
async def redirect_to_root_middleware(request, handler):
    """Redirect www subdomain to root domain"""
    host = request.host
    if host.startswith('www.asibiont.ru'):
        new_url = f"https://asibiont.ru{request.path_qs}"
        logger.info(f"Redirecting from {host} to asibiont.ru")
        return web.HTTPMovedPermanently(new_url)
    return await handler(request)


@web.middleware
async def csp_middleware(request, handler):
    response = await handler(request)
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org https://fonts.googleapis.com https://mc.yandex.ru https://yastatic.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.deepseek.com https://mc.yandex.ru wss://mc.yandex.ru; frame-src https://oauth.telegram.org;"
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@web.middleware
async def cors_middleware(request, handler):
    """Add CORS headers for local development"""
    if LOCAL:
        # Preflight request
        if request.method == 'OPTIONS':
            response = web.Response()
        else:
            response = await handler(request)
        
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response
    return await handler(request)

# app.middlewares.append(security_middleware)
app.middlewares.append(cors_middleware)
app.middlewares.append(redirect_to_root_middleware)
app.middlewares.append(session_error_middleware)
app.middlewares.append(logging_middleware)
app.middlewares.append(csp_middleware)

# Setup Jinja2 with custom filters
def unique_interests(value):
    """Remove duplicate interests (case-insensitive)"""
    if not value:
        return value
    interests = [i.strip() for i in value.split(',') if i.strip()]
    seen = set()
    unique = []
    for i in interests:
        if i.lower() not in seen:
            unique.append(i)
            seen.add(i.lower())
    return ', '.join(unique)

def strptime_filter(value, format_string):
    return datetime.strptime(value, format_string)

jinja_env = aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))
jinja_env.filters['unique_interests'] = unique_interests
jinja_env.filters['strptime'] = strptime_filter


async def yookassa_webhook(request):
    data = await request.json()
    if data.get('event') == 'payment.succeeded':
        payment = data['object']
        user_id = payment['metadata']['user_id']
        tier = payment['metadata'].get('tier', 'light')  # Get tier from payment metadata
        promo_code = payment['metadata'].get('promo_code')  # Get promo code if used

        session = Session()
        user = session.query(User).filter_by(telegram_id=int(user_id)).first()
        if user:
            # Handle promo code if provided
            if promo_code:
                promo = session.query(PromoCode).filter_by(code=promo_code.upper()).first()
                if promo:
                    # Mark promo code as used by this user
                    used_by_users = json.loads(promo.used_by_users or '[]')
                    if user_id not in used_by_users:
                        used_by_users.append(user_id)
                        promo.used_by_users = json.dumps(used_by_users)
                        promo.used_count += 1
                        logger.info(f"Promo code {promo_code} used by user {user.username} (user_id: {user_id})")
                    else:
                        logger.warning(f"User {user.username} already used promo code {promo_code}")

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

            # Update tier
            tier_mapping = {
                'light': SubscriptionTier.LIGHT,
                'standard': SubscriptionTier.STANDARD,
                'premium': SubscriptionTier.PREMIUM
            }
            tier_enum = tier_mapping.get(tier, SubscriptionTier.LIGHT)
            subscription.tier = tier_enum
            user.subscription_tier = tier_enum

            # Если подписка еще акти, продлеаем от end_date, иче от текущей даты
            now = datetime.now(pytz.UTC)
            if subscription.end_date and subscription.end_date > now:
                subscription.end_date = subscription.end_date + timedelta(days=30)
            else:
                subscription.end_date = now + timedelta(days=30)

            session.commit()

            # Логируем платеж в payment_history для защиты от потери данных
            try:
                payment_history = PaymentHistory(
                    user_id=user.id,
                    telegram_username=user.username,
                    action='payment',
                    tier=tier_enum,
                    amount=payment['amount']['value'],
                    payment_id=payment['id'],
                    duration_days=30,
                    start_date=subscription.start_date,
                    end_date=subscription.end_date,
                    details=json.dumps({
                        'payment_method': payment.get('payment_method', {}).get('type'), 
                        'status': payment.get('status'),
                        'promo_code': promo_code
                    })
                )
                session.add(payment_history)
                session.commit()
                logger.info(f"💾 Payment logged to history: user={user.username}, tier={tier}, payment_id={payment['id']}, promo_code={promo_code}")
            except Exception as e:
                logger.error(f"❌ Failed to log payment to history: {e}")
                # Не падаем, платеж уже обработан

            from payments import get_tier_name, TIER_PRICES
            tier_name = get_tier_name(tier)
            promo_msg = f" с промокодом {promo_code}" if promo_code else ""
            await bot.send_message(int(user_id), f"Подписка {tier_name} актиироа{promo_msg}! Теперь у ас доступ ко сем премиум-фуциям.")

            # Handle referral commission (20% of payment amount)
            if user.referrer_id:
                try:
                    referrer = session.query(User).filter_by(id=user.referrer_id).first()
                    if referrer:
                        # Calculate commission from actual payment amount
                        payment_amount = float(payment['amount']['value'])
                        commission_amount = int(payment_amount * 0.20)
                        referrer.referral_balance += commission_amount
                        session.commit()
                        logger.info(f"Referral commission: {commission_amount} RUB added to referrer {referrer.telegram_id} (balance: {referrer.referral_balance}) from payment amount {payment_amount} RUB")
                        
                        # Notify referrer about commission
                        try:
                            await bot.send_message(
                                int(referrer.telegram_id), 
                                f"💰 Ваш реферал оплатил подписку! Вы получили {commission_amount} рублей комиссии. Текущий баланс: {referrer.referral_balance} рублей."
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify referrer {referrer.telegram_id} about commission: {e}")
                except Exception as e:
                    logger.error(f"Error processing referral commission: {e}")
                    session.rollback()
        session.close()
    return web.Response(text="OK")


async def get_user_id_from_request(request):
    """Helper function to get user_id from session or query parameters"""
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    logger.info(f"Session keys: {list(session_req.keys())}, user_id: {user_id}")
    
    # Check for telegram_id in query parameters (for local testing)
    if not user_id:
        telegram_id_param = request.query.get('telegram_id')
        if telegram_id_param:
            try:
                user_id = int(telegram_id_param)
                logger.info(f"Set user_id from query parameter: {user_id}")
                # Save to session for subsequent API calls
                session_req['user_id'] = user_id
                logger.info(f"Saved user_id {user_id} to session")
            except ValueError:
                logger.error(f"Invalid telegram_id in query: {telegram_id_param}")
    
    return user_id


async def api_partners_handler(request):
    def pluralize_task(count):
        """Склое слоа 'задача' по числу"""
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
        user_id = await get_user_id_from_request(request)
        logger.info(f"API partners handler called, user_id: {user_id}")
        if not user_id:
            logger.error("No user_id in session for partners API")
            return web.json_response({'error': 'Not logged in'}, status=401)

        try:
            # Filter hidden contacts
            session_db = Session()
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.error(f"User not found for telegram_id: {user_id}")
                return web.json_response({'error': 'User not found'}, status=404)
            
            try:
                partners = get_partners_list(user_id=user.id)  # Передаем user.id (базоый ID), а  telegram_id
                logger.info(f"Got {len(partners)} partners from get_partners_list")
            except Exception as e:
                logger.error(f"Error getting partners: {e}")
                partners = []

            # Get hidden contacts from memory
            hidden_contacts = set()
            if user and user.memory and len(user.memory.strip()) > 0:
                try:
                    from datetime import timezone as dt_timezone_local

                    decrypted = decrypt_data(user.memory)
                    if decrypted:  # Check decrypted result is not empty
                        hide_matches = re.findall(r'hide_contact:@?(\w+):(\d+)', decrypted, re.IGNORECASE)
                        current_time = int(datetime.now(dt_timezone_local.utc).timestamp())
                        for username, expiration_ts in hide_matches:
                            exp_ts = int(expiration_ts)
                            if exp_ts > current_time:  # Still hidden
                                hidden_contacts.add(username.lower())
                except Exception as e:
                    logger.error(f"Error parsing hidden contacts: {e}")

            # Filter partners
            if hidden_contacts:
                filtered_partners = []
                for p in partners:
                    if hasattr(p, 'user_id') and p.user_id is not None:
                        partner_user = session_db.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            username_clean = partner_user.username.replace('@', '').lower()
                            if username_clean not in hidden_contacts:
                                filtered_partners.append(p)
                        else:
                            filtered_partners.append(p)  # Include if no username
                    else:
                        filtered_partners.append(p)  # Include if no user_id
                partners = filtered_partners

            # Don't filter by tier - everyone sees everyone
            # But we'll add tier info to determine access on frontend

            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None
            interactions = session_db.query(Interaction).filter_by(
                user_id=user.id).order_by(
                Interaction.created_at).all() if user else []

            # Получить контакты по делегироаю
            delegating_to_me = []  # Люди, которые делегироали м задачи
            delegating_by_me = []  # Люди, которым я делегироал задачи

            try:
                # Люди, которые делегироали м задачи (я получаю задачи от х)
                username_clean = user.username.replace('@', '') if user.username else ''
                delegated_tasks = session_db.query(Task).filter(
                    or_(
                        Task.delegated_to_username.ilike(username_clean),
                        Task.delegated_to_username.ilike(f'@{username_clean}')
                    ),
                    Task.delegation_status.in_(['pending', 'accepted']),
                    Task.status != 'deleted',
                    Task.status != 'rejected'
                ).all()

                delegator_ids = set()
                for task in delegated_tasks:
                    if task.delegated_by and task.delegated_by not in delegator_ids:
                        delegator_ids.add(task.delegated_by)
                        delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                        if delegator and delegator.id != user.id:
                            delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first()
                            task_titles = [t.title for t in delegated_tasks if t.delegated_by == delegator.id]
                            delegating_to_me.append({
                                'id': delegator.id,
                                'username': delegator.username,
                                'first_name': delegator.first_name,
                                'position': delegator_profile.position if delegator_profile else None,
                                'interests': delegator_profile.interests if delegator_profile else None,
                                'city': delegator_profile.city if delegator_profile else None,
                                'company': delegator_profile.company if delegator_profile else None,
                                'task_count': len(task_titles),
                                'reason': f'делегироал {len(task_titles)} {pluralize_task(len(task_titles))}'
                            })

                # Люди, которым я делегироал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_by == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(['pending', 'accepted']),
                    Task.status != 'deleted'
                ).all()

                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(
                            or_(
                                User.username.ilike(task.delegated_to_username.replace('@', '')),
                                User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                            )
                        ).first()
                        if delegatee and delegatee.id != user.id:
                            delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first()
                            task_titles = [
                                t.title for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                            delegating_by_me.append({
                                'id': delegatee.id,
                                'username': delegatee.username,
                                'first_name': delegatee.first_name,
                                'position': delegatee_profile.position if delegatee_profile else None,
                                'interests': delegatee_profile.interests if delegatee_profile else None,
                                'city': delegatee_profile.city if delegatee_profile else None,
                                'company': delegatee_profile.company if delegatee_profile else None,
                                'task_count': len(task_titles),
                                'reason': f'я делегироал {len(task_titles)} {pluralize_task(len(task_titles))}'
                            })

            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []

            # Apply hidden contacts to delegation lists as well
            if hidden_contacts:
                delegating_to_me = [c for c in delegating_to_me if not c.get('username') or c.get(
                    'username').replace('@', '').lower() not in hidden_contacts]
                delegating_by_me = [c for c in delegating_by_me if not c.get('username') or c.get(
                    'username').replace('@', '').lower() not in hidden_contacts]

        except Exception as e:
            logger.error(f"Error processing partners data: {e}", exc_info=True)
            partners = []
            delegating_to_me = []
            delegating_by_me = []
            profile = None
            interactions = []

        # Add common interests, skills, goals and recommendation reason
        if profile and partners:
            user_interests = set(i.strip().lower()
                                 for i in profile.interests.split(',')) if profile.interests else set()
            user_skills = set(s.strip().lower() for s in profile.skills.split(',')) if profile.skills else set()
            user_goals = set(g.strip().lower() for g in profile.goals.split(',')) if profile.goals else set()

            # Получаем список контакто, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - improved matching with partial string matching
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(',') if i.strip())
                    common = user_interests & partner_interests
                    # Also check for partial matches (e.g., "спорт" matches "спорт, футбол")
                    if not common:
                        for ui in user_interests:
                            for pi in partner_interests:
                                if ui and pi and (ui in pi or pi in ui):
                                    common.add(pi)
                    p.common_interests = ', '.join(sorted(common)) if common else None
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
                    reasons.append('общие ыки')
                if p.common_interests:
                    reasons.append('общие интересы')
                if p.common_goals:
                    reasons.append('общие цели')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('из ашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'

        # Calculate common_tasks for regular partners - улучшенная логика с частичным совпадением
        for p in partners:
            if profile and p:
                # Get user's tasks
                user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                user_task_titles = set()
                for task in user_tasks:
                    if task.title:
                        user_task_titles.add(task.title.lower().strip())

                # Get partner's tasks
                if hasattr(p, 'user_id') and p.user_id is not None:
                    partner_user = session_db.query(User).filter_by(id=p.user_id).first()
                    if partner_user:
                        partner_tasks = session_db.query(Task).filter_by(user_id=partner_user.id).all()
                        partner_task_titles = set()
                        for task in partner_tasks:
                            if task.title:
                                partner_task_titles.add(task.title.lower().strip())

                        # Точное совпадение задач
                        common_task_titles = user_task_titles & partner_task_titles
                        
                        # Частичное совпадение - если хотя бы 2 слова совпадают
                        if not common_task_titles:
                            partial_matches = set()
                            for user_task in user_task_titles:
                                user_words = set(user_task.split())
                                if len(user_words) < 2:  # Пропускаем слишком короткие
                                    continue
                                for partner_task in partner_task_titles:
                                    partner_words = set(partner_task.split())
                                    # Если сопадает >= 2 сло
                                    common_words = user_words & partner_words
                                    if len(common_words) >= 2:
                                        partial_matches.add(user_task)
                            if partial_matches:
                                common_task_titles = partial_matches
                        
                        p.common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None
                    else:
                        p.common_tasks = None
                else:
                    p.common_tasks = None
            else:
                p.common_tasks = None

        partners_data = []
        for p in partners:
            try:
                if not hasattr(p, 'user_id') or p.user_id is None:
                    continue  # Skip partners without user_id
                # Получаем telegram_id пользователя из базы
                partner_user = session_db.query(User).filter_by(
                    id=p.user_id).first() if hasattr(
                    p, 'user_id') and p.user_id is not None else None

                # Skip if partner user not found
                if not partner_user:
                    logger.warning(f"Partner user not found for profile user_id: {p.user_id}")
                    continue

                # Use cached avatar from DB (updated daily by scheduler)
                photo_url = partner_user.photo_url if partner_user and partner_user.photo_url else None

                # Check tier access - use user.subscription_tier for now since update script uses it
                user_tier = user.subscription_tier if user and hasattr(user, 'subscription_tier') and user.subscription_tier else SubscriptionTier.LIGHT
                partner_tier = partner_user.subscription_tier if partner_user and hasattr(partner_user, 'subscription_tier') and partner_user.subscription_tier else SubscriptionTier.LIGHT

                # Ensure tiers are proper enum values
                if not hasattr(user_tier, 'value'):
                    user_tier = SubscriptionTier.LIGHT
                if not hasattr(partner_tier, 'value'):
                    partner_tier = SubscriptionTier.LIGHT

                # Convert to string for comparison if needed
                user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
                partner_tier_str = partner_tier.value if hasattr(partner_tier, 'value') else str(partner_tier).lower()

                logger.info(f"User {user.username} (id:{user.telegram_id}) has tier {user_tier} ({user_tier_str}), partner {partner_user.username if partner_user else 'unknown'} has tier {partner_tier} ({partner_tier_str})")

                # Determine if user can access this contact
                # LIGHT идит LIGHT и STANDARD контакты
                # STANDARD идит LIGHT и STANDARD контакты
                # PREMIUM идит се контакты (LIGHT, STANDARD, PREMIUM)
                can_access = False

                if user_tier_str.lower() == 'light':
                    # LIGHT идит LIGHT и STANDARD контакты
                    can_access = (partner_tier_str.lower() in ['light', 'standard'])
                    logger.info(f"User {user_tier_str} checking partner {partner_tier_str}: can_access = {can_access}")
                elif user_tier_str.lower() == 'standard':
                    # STANDARD идит LIGHT и STANDARD контакты
                    can_access = (partner_tier_str.lower() in ['light', 'standard'])
                    logger.info(f"User {user_tier_str} checking partner {partner_tier_str}: can_access = {can_access}")
                elif user_tier_str.lower() == 'premium':
                    # PREMIUM идит сех
                    can_access = True
                    logger.info(f"User {user_tier_str} can access all partners")

                # Add only contacts that user can access (hide inaccessible contacts)
                if partner_user and can_access:
                    # Get partner's profile for rating info
                    partner_profile = session_db.query(UserProfile).filter_by(user_id=partner_user.id).first()
                    
                    logger.info(f"Adding recommended contact {partner_user.username if partner_user else 'unknown'} with tier {partner_tier_str} for user {user.username} with tier {user_tier_str} (can_access: {can_access})")
                    partners_data.append(
                        {
                            'contact_info': partner_user.username if (partner_user and partner_user.username) else None,
                            'telegram_id': partner_user.telegram_id if partner_user else None,
                            'photo_url': photo_url,
                            'first_name': partner_user.first_name,
                            'can_access': can_access,
                            'subscription_tier': (partner_tier.value if partner_tier and hasattr(partner_tier, 'value') else 'light').lower(),
                            'city': getattr(
                                p,
                                'city',
                                None),
                            'common_interests': getattr(
                                p,
                                'common_interests',
                                None),
                            'common_skills': getattr(
                                p,
                                'common_skills',
                                None),
                            'common_goals': getattr(
                                p,
                                'common_goals',
                                None),
                            'common_tasks': getattr(
                                p,
                                'common_tasks',
                                None),
                            'recommendation_reason': getattr(
                                p,
                                'recommendation_reason',
                                'подходящий контакт'),
                            'average_rating': partner_profile.average_rating if partner_profile else 0,
                            'rating_count': partner_profile.rating_count if partner_profile else 0,
                            'type': 'recommended'})
            except Exception as e:
                logger.error(f"Error processing partner {getattr(p, 'user_id', 'unknown')}: {e}", exc_info=True)
                continue

        # Add delegating contacts
        for contact in delegating_to_me:
            # Skip contacts without username
            if not contact.get('username'):
                logger.warning(f"Skipping delegating_to_me contact without username: user_id={contact.get('id')}")
                continue
                
            # Получить профиль делегатора для расчета общих интересо/ыко/целей
            delegator_profile = session_db.query(UserProfile).filter_by(
                user_id=contact['id']).first() if 'id' in contact else None

            common_interests = None
            common_skills = None
            common_goals = None

            if profile and delegator_profile:
                # Common interests (partial match)
                if delegator_profile.interests and profile.interests:
                    user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                    partner_interests = set(i.strip().lower() for i in delegator_profile.interests.split(','))
                    common = set()
                    for ui in user_interests:
                        for pi in partner_interests:
                            if ui in pi or pi in ui:
                                common.add(pi)
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

                    # Точное совпадение задач
                    common_task_titles = user_task_titles & delegator_task_titles
                    
                    # Частичное совпадение - если хотя бы 2 слова совпадают
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(user_task.split())
                            if len(user_words) < 2:
                                continue
                            for delegator_task in delegator_task_titles:
                                delegator_words = set(delegator_task.split())
                                common_words = user_words & delegator_words
                                if len(common_words) >= 2:
                                    partial_matches.add(user_task)
                        if partial_matches:
                            common_task_titles = partial_matches
                    
                    common_tasks = ', '.join(
                        list(common_task_titles)[
                            :5]) if common_task_titles else None  # Limit to 5 common tasks

            # Get delegator user object
            delegator = session_db.query(User).filter_by(id=contact['id']).first() if 'id' in contact else None

            # Update avatar from Telegram if available
            photo_url = delegator.photo_url if delegator and delegator.photo_url else None
            if delegator and delegator.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id, force_refresh=True)
                    if updated_avatar and updated_avatar != delegator.photo_url:
                        delegator.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")

            #   " "     
            #         
            delegator_tier = delegator.subscription_tier if delegator and delegator.subscription_tier else SubscriptionTier.LIGHT
            
            # Ensure tier is proper enum value
            if not hasattr(delegator_tier, 'value'):
                delegator_tier = SubscriptionTier.LIGHT
            
            delegator_tier_str = delegator_tier.value if hasattr(delegator_tier, 'value') else str(delegator_tier).lower()
            
            logger.info(f"Adding delegating contact {contact['username']} with tier {delegator_tier_str} for user {user.username} (no tier restrictions)")
            delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first() if delegator else None
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegator.telegram_id if delegator else None,
                'can_access': True,  # Всегда доступен
                'required_tier': None,  # Нет ограчей
                'subscription_tier': delegator_tier.value if delegator_tier else 'light',
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
            # Skip contacts without username
            if not contact.get('username'):
                logger.warning(f"Skipping delegation contact without username: user_id={contact.get('id')}")
                continue
            
            # Получить профиль делегата для расчета общих интересо/ыко/целей
            delegatee_profile = session_db.query(UserProfile).filter_by(
                user_id=contact['id']).first() if 'id' in contact else None
            delegatee = session_db.query(User).filter_by(id=contact['id']).first() if 'id' in contact else None

            common_interests = None
            common_skills = None
            common_goals = None

            if profile and delegatee_profile:
                # Common interests (partial match)
                if delegatee_profile.interests and profile.interests:
                    user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                    partner_interests = set(i.strip().lower() for i in delegatee_profile.interests.split(','))
                    common = set()
                    for ui in user_interests:
                        for pi in partner_interests:
                            if ui in pi or pi in ui:
                                common.add(pi)
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

                    # Точное совпадение задач
                    common_task_titles = user_task_titles & delegatee_task_titles
                    
                    # Частичное совпадение - если хотя бы 2 слова совпадают
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(user_task.split())
                            if len(user_words) < 2:
                                continue
                            for delegatee_task in delegatee_task_titles:
                                delegatee_words = set(delegatee_task.split())
                                common_words = user_words & delegatee_words
                                if len(common_words) >= 2:
                                    partial_matches.add(user_task)
                        if partial_matches:
                            common_task_titles = partial_matches
                    
                    common_tasks = ', '.join(
                        list(common_task_titles)[
                            :5]) if common_task_titles else None  # Limit to 5 common tasks

            # Update avatar from Telegram if available
            photo_url = delegatee.photo_url if delegatee and delegatee.photo_url else None
            if delegatee and delegatee.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id, force_refresh=True)
                    if updated_avatar and updated_avatar != delegatee.photo_url:
                        delegatee.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegatee avatar for {delegatee.telegram_id}: {e}")

            # Check tier access
            user_tier = user.subscription_tier if user else SubscriptionTier.LIGHT
            delegatee_tier = delegatee.subscription_tier if delegatee and delegatee.subscription_tier else SubscriptionTier.LIGHT

            # Ensure tiers are proper enum values
            if not hasattr(user_tier, 'value'):
                user_tier = SubscriptionTier.LIGHT
            if not hasattr(delegatee_tier, 'value'):
                delegatee_tier = SubscriptionTier.LIGHT

            # Convert to string for comparison
            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
            delegatee_tier_str = delegatee_tier.value if hasattr(delegatee_tier, 'value') else str(delegatee_tier).lower()

            can_access = False

            if user_tier_str.lower() == 'light':
                # LIGHT идит LIGHT и STANDARD контакты
                can_access = (delegatee_tier_str.lower() in ['light', 'standard'])
                logger.info(f"Delegatee check: User {user_tier_str} checking delegatee {delegatee_tier_str}: can_access = {can_access}")
            elif user_tier_str.lower() == 'standard':
                # STANDARD идит LIGHT и STANDARD контакты
                can_access = (delegatee_tier_str.lower() in ['light', 'standard'])
                logger.info(f"Delegatee check: User {user_tier_str} checking delegatee {delegatee_tier_str}: can_access = {can_access}")
            elif user_tier_str.lower() == 'premium':
                can_access = True
                logger.info(f"Delegatee check: User {user_tier_str} can access all delegatees")

            # Only add contact if user can access it
            if can_access:
                logger.info(f"Adding delegating_by_me contact {contact['username']} with tier {delegatee_tier_str} for user {user.username} with tier {user_tier_str}")
                delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first() if delegatee else None
                partners_data.append({
                    'contact_info': contact['username'],
                    'telegram_id': delegatee.telegram_id if delegatee else None,
                    'can_access': can_access,
                    'subscription_tier': delegatee_tier.value if delegatee_tier else 'light',
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

        normalized_user_city = normalize_city(user_city)

        def sort_key(partner):
            partner_city = normalize_city(partner.get('city', ''))
            same_city = 0 if (normalized_user_city and partner_city == normalized_user_city) else 1
            
            # Premium-приоритет (PREMIUM=3, STANDARD=2, LIGHT=1)
            tier = partner.get('subscription_tier', 'light').upper()
            tier_priority = -{'PREMIUM': 3, 'STANDARD': 2, 'LIGHT': 1}.get(tier, 1)

            rating = partner.get('average_rating', 0) or 0
            # Группы рейтиа:
            # 1. Высокий рейти (>= 5): сортируем по убыаю
            # 2. Нет рейтиа (0): йтраль, ыше плохих
            # 3. Низкий рейти (< 5): сортируем по убыаю
            if rating >= 5:
                rating_group = 0  # Лучшая группа
                rating_value = -rating  # Внутри группы по убыаю
            elif rating == 0:
                rating_group = 1  # Средняя группа (нет данных)
                rating_value = 0
            else:  # rating < 5
                rating_group = 2  # Худшая группа
                rating_value = -rating  # Внутри группы по убыаю

            return (same_city, rating_group, rating_value)

        # Add favorite contacts
        if profile and profile.favorite_contacts:
            try:
                favorite_data = json.loads(profile.favorite_contacts)
                for item in favorite_data:
                    favorite_username = None
                    # Определить username по ID или использовать прямую
                    if isinstance(item, int):
                        # Это user_id
                        fav_user = session_db.query(User).filter_by(id=item).first()
                        if fav_user:
                            favorite_username = fav_user.username
                    elif isinstance(item, str):
                        # Это username
                        favorite_username = item
                    
                    if not favorite_username:
                        continue
                    
                    # Check if already in partners_data
                    if not any(p.get('contact_info') == favorite_username for p in partners_data):
                        # Find user by username
                        favorite_user = session_db.query(User).filter(
                            or_(
                                User.username == favorite_username,
                                User.username == favorite_username.replace('@', '')
                            )
                        ).first()
                        if favorite_user:
                            favorite_profile = session_db.query(UserProfile).filter_by(user_id=favorite_user.id).first()

                            # Check tier access
                            user_tier = user.subscription_tier if user else SubscriptionTier.LIGHT
                            favorite_tier = favorite_user.subscription_tier if favorite_user.subscription_tier else SubscriptionTier.LIGHT

                            # Ensure tiers are proper enum values
                            if not hasattr(user_tier, 'value'):
                                user_tier = SubscriptionTier.LIGHT
                            if not hasattr(favorite_tier, 'value'):
                                favorite_tier = SubscriptionTier.LIGHT

                            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()

                            # збраые контакты сегда доступны заисимо от тарифа
                            can_access = True
                            required_tier = None

                            # Update avatar from Telegram if available
                            photo_url = favorite_user.photo_url if favorite_user.photo_url else None
                            if favorite_user.telegram_id and 'bot' in request.app:
                                try:
                                    updated_avatar = await get_user_avatar_url(request.app['bot'], favorite_user.telegram_id, force_refresh=True)
                                    if updated_avatar and updated_avatar != favorite_user.photo_url:
                                        favorite_user.photo_url = updated_avatar
                                        session_db.commit()
                                        photo_url = updated_avatar
                                except Exception as e:
                                    logger.error(f"Error updating favorite avatar for {favorite_user.telegram_id}: {e}")

                            partners_data.append({
                                'contact_info': favorite_user.username,
                                'telegram_id': favorite_user.telegram_id,
                                'photo_url': photo_url,
                                'can_access': can_access,
                                'required_tier': required_tier,
                                'subscription_tier': favorite_tier.value if favorite_tier else 'light',
                                'first_name': favorite_user.first_name,
                                'position': favorite_profile.position if favorite_profile else None,
                                'interests': favorite_profile.interests if favorite_profile else None,
                                'city': favorite_profile.city if favorite_profile else None,
                                'company': favorite_profile.company if favorite_profile else None,
                                'common_interests': None,  # Will be calculated later if needed
                                'common_skills': None,
                                'common_goals': None,
                                'common_tasks': None,
                                'average_rating': favorite_profile.average_rating if favorite_profile else 0,
                                'rating_count': favorite_profile.rating_count if favorite_profile else 0,
                                'reason': 'избраый контакт',
                                'task_count': 0,
                                'type': 'favorite'
                            })
            except json.JSONDecodeError:
                pass

        # Filter out blocked contacts
        user_profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
        blocked_by_me = set()
        if user_profile and user_profile.blocked_contacts:
            try:
                blocked_by_me = set(json.loads(user_profile.blocked_contacts))
            except json.JSONDecodeError:
                pass

        # Also check who blocked the current user
        blocked_me = set()
        all_profiles = session_db.query(UserProfile).filter(UserProfile.blocked_contacts.isnot(None)).all()
        for profile in all_profiles:
            try:
                blocked_list = json.loads(profile.blocked_contacts)
                if user.username and user.username in blocked_list:
                    blocker_user = session_db.query(User).filter_by(id=profile.user_id).first()
                    if blocker_user and blocker_user.username:
                        blocked_me.add(blocker_user.username)
            except json.JSONDecodeError:
                continue

        # Filter partners_data but save blocked contacts info
        filtered_partners_data = []
        blocked_partners_data = []  # Сохраняем информацию о заблокироаых
        for partner in partners_data:
            partner_username = (partner.get('contact_info') or '').replace('@', '')
            if partner_username in blocked_by_me or partner_username in blocked_me:
                blocked_partners_data.append(partner)  # Сохраняем заблокироаые
                continue  # Skip blocked contacts from main list
            filtered_partners_data.append(partner)

        partners_data = filtered_partners_data
        partners_data.sort(key=sort_key)

        # Добаить флаг is_favorite для сех контакто
        favorite_usernames = set()
        if profile and profile.favorite_contacts:
            try:
                favorite_data = json.loads(profile.favorite_contacts)
                for item in favorite_data:
                    if isinstance(item, int):
                        # Это user_id
                        fav_user = session_db.query(User).filter_by(id=item).first()
                        if fav_user and fav_user.username:
                            favorite_usernames.add(fav_user.username.replace('@', '').lower())
                    elif isinstance(item, str):
                        # Это username
                        favorite_usernames.add(item.replace('@', '').lower())
            except json.JSONDecodeError:
                pass

        # Устаить флаг is_favorite для сех контакто
        for partner in partners_data:
            contact_info = partner.get('contact_info')
            if contact_info is None:
                contact_info = ''
            contact_username = contact_info.replace('@', '').lower()
            partner['is_favorite'] = contact_username in favorite_usernames

        logger.info(f"Returning {len(partners_data)} partners for user {user_id}")
        return web.json_response({
            'partners': partners_data,
            'blocked_partners_info': blocked_partners_data  # Добавляем информацию о заблокированных
        })
    except Exception as e:
        logger.error(f"Unexpected error in api_partners_handler: {e}", exc_info=True)
        return web.json_response({'partners': []}, status=200)
    finally:
        # На случай раих ошибок закрыаем сессию, если о еще открыта
        try:
            if 'session_db' in locals():
                session_db.close()
        except Exception:
            pass


async def api_elite_partners_handler(request):
    """Get ALL Premium partners for Premium users (Premium status filter)"""
    def pluralize_task(count):
        """Склое слоа 'задача' по числу"""
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
        user_id = await get_user_id_from_request(request)
        logger.info(f"API elite partners handler called for user_id: {user_id}")
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.warning(f"User not found for telegram_id: {user_id}")
                return web.json_response({'error': 'User not found'}, status=404)

            # Check if user has Premium tier
            user_tier = user.subscription_tier if user and hasattr(user, 'subscription_tier') else SubscriptionTier.LIGHT
            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
            
            logger.info(f"User {user.username} has tier: {user_tier_str}")
            
            if user_tier_str.lower() != 'premium':
                # Only Premium users can access elite partners
                logger.info(f"User {user.username} does not have Premium tier, returning empty partners list")
                return web.json_response({'partners': []})

            # Get user profile for comparison
            user_profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
            if not user_profile:
                return web.json_response({'partners': []})

            # Get hidden contacts from memory
            hidden_contacts = set()
            if user and user.memory and len(user.memory.strip()) > 0:
                try:
                    from datetime import timezone as dt_timezone_local
                    decrypted = decrypt_data(user.memory)
                    if decrypted:
                        hide_matches = re.findall(r'hide_contact:@?(\w+):(\d+)', decrypted, re.IGNORECASE)
                        current_time = int(datetime.now(dt_timezone_local.utc).timestamp())
                        for username, expiration_ts in hide_matches:
                            exp_ts = int(expiration_ts)
                            if exp_ts > current_time:
                                hidden_contacts.add(username.lower())
                except Exception as e:
                    logger.error(f"Error parsing hidden contacts: {e}")

            # Get blocked contacts
            blocked_by_me = set()
            if user_profile.blocked_contacts:
                try:
                    blocked_by_me = set(json.loads(user_profile.blocked_contacts))
                except json.JSONDecodeError:
                    pass

            # Get all Premium users (except self)
            premium_users = session_db.query(User).filter(
                User.subscription_tier == SubscriptionTier.PREMIUM,
                User.id != user.id
            ).all()
            
            logger.info(f"Found {len(premium_users)} other Premium users for user {user.username}")

            partners_data = []
            for premium_user in premium_users:
                # Skip hidden and blocked contacts
                username_clean = premium_user.username.replace('@', '').lower() if premium_user.username else ''
                if username_clean in hidden_contacts or premium_user.username in blocked_by_me:
                    logger.info(f"Skipping Premium user {premium_user.username} - hidden or blocked")
                    continue

                premium_profile = session_db.query(UserProfile).filter_by(user_id=premium_user.id).first()
                
                logger.info(f"Adding Premium user to elite partners: {premium_user.username}")

                # Update avatar from Telegram if available
                photo_url = premium_user.photo_url if premium_user.photo_url else None
                if premium_user.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], premium_user.telegram_id, force_refresh=True)
                        if updated_avatar and updated_avatar != premium_user.photo_url:
                            premium_user.photo_url = updated_avatar
                            session_db.commit()
                            photo_url = updated_avatar
                    except Exception as e:
                        logger.error(f"Error updating Premium user avatar for {premium_user.telegram_id}: {e}")

                # Calculate common interests/skills/goals/tasks
                common_interests = None
                common_skills = None
                common_goals = None
                common_tasks = None

                if premium_profile:
                    # Common interests
                    if premium_profile.interests and user_profile.interests:
                        user_interests = set(i.strip().lower() for i in user_profile.interests.split(','))
                        premium_interests = set(i.strip().lower() for i in premium_profile.interests.split(','))
                        common = user_interests & premium_interests
                        common_interests = ', '.join(common) if common else None

                    # Common skills
                    if premium_profile.skills and user_profile.skills:
                        user_skills = set(s.strip().lower() for s in user_profile.skills.split(','))
                        premium_skills = set(s.strip().lower() for s in premium_profile.skills.split(','))
                        common_sk = user_skills & premium_skills
                        common_skills = ', '.join(common_sk) if common_sk else None

                    # Common goals
                    if premium_profile.goals and user_profile.goals:
                        user_goals = set(g.strip().lower() for g in user_profile.goals.split(','))
                        premium_goals = set(g.strip().lower() for g in premium_profile.goals.split(','))
                        common_g = user_goals & premium_goals
                        common_goals = ', '.join(common_g) if common_g else None

                    # Common tasks
                    user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                    premium_tasks = session_db.query(Task).filter_by(user_id=premium_user.id).all()
                    
                    user_task_titles = set(t.title.lower().strip() for t in user_tasks if t.title)
                    premium_task_titles = set(t.title.lower().strip() for t in premium_tasks if t.title)
                    
                    common_task_titles = user_task_titles & premium_task_titles
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(user_task.split())
                            if len(user_words) < 2:
                                continue
                            for premium_task in premium_task_titles:
                                premium_words = set(premium_task.split())
                                common_words = user_words & premium_words
                                if len(common_words) >= 2:
                                    partial_matches.add(user_task)
                        if partial_matches:
                            common_task_titles = partial_matches
                    
                    common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None

                partners_data.append({
                    'contact_info': premium_user.username if premium_user.username else None,
                    'telegram_id': premium_user.telegram_id,
                    'photo_url': photo_url,
                    'can_access': True,  # Premium users can access all Premium users
                    'required_tier': None,
                    'subscription_tier': 'premium',
                    'first_name': premium_user.first_name,
                    'city': premium_profile.city if premium_profile else None,
                    'company': premium_profile.company if premium_profile else None,
                    'position': premium_profile.position if premium_profile else None,
                    'interests': premium_profile.interests if premium_profile else None,
                    'skills': premium_profile.skills if premium_profile else None,
                    'goals': premium_profile.goals if premium_profile else None,
                    'common_interests': common_interests,
                    'common_skills': common_skills,
                    'common_goals': common_goals,
                    'common_tasks': common_tasks,
                    'average_rating': premium_profile.average_rating if premium_profile else 0,
                    'rating_count': premium_profile.rating_count if premium_profile else 0,
                    'type': 'elite'
                })

            # Add delegation contacts for Premium users
            delegating_to_me = []
            delegating_by_me = []
            
            try:
                # Люди, которые делегироали задачи м (accepted delegation)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status == 'accepted',
                    Task.status != 'deleted'
                ).all()
                
                for task in delegated_tasks:
                    # Check if this task is delegated to current user
                    if task.delegated_to_username:
                        # Clean username for comparison
                        task_username_clean = task.delegated_to_username.replace('@', '').lower()
                        user_username_clean = user.username.replace('@', '').lower() if user.username else ''
                        
                        if task_username_clean == user_username_clean:
                            delegator = session_db.query(User).filter_by(id=task.user_id).first()
                            if delegator and delegator.id != user.id:
                                # Skip contacts without username
                                if not delegator.username:
                                    logger.warning(f"Skipping elite delegation contact without username: user_id={delegator.id}")
                                    continue
                                    
                                # Skip if already in partners_data
                                if any(p.get('contact_info') == delegator.username for p in partners_data):
                                    continue
                                    
                                # Skip hidden and blocked contacts
                                delegator_username_clean = delegator.username.replace('@', '').lower() if delegator.username else ''
                                if delegator_username_clean in hidden_contacts or delegator.username in blocked_by_me:
                                    continue
                                    
                                delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first()
                                task_titles = [t.title for t in delegated_tasks if t.user_id == delegator.id and 
                                             t.delegated_to_username.replace('@', '').lower() == user_username_clean]
                                
                                # Update avatar from Telegram if available
                                photo_url = delegator.photo_url if delegator.photo_url else None
                                if delegator.telegram_id and 'bot' in request.app:
                                    try:
                                        updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id, force_refresh=True)
                                        if updated_avatar and updated_avatar != delegator.photo_url:
                                            delegator.photo_url = updated_avatar
                                            session_db.commit()
                                            photo_url = updated_avatar
                                    except Exception as e:
                                        logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")
                                
                                delegating_to_me.append({
                                    'contact_info': delegator.username,
                                    'telegram_id': delegator.telegram_id,
                                    'photo_url': photo_url,
                                    'can_access': True,
                                    'required_tier': None,
                                    'subscription_tier': delegator.subscription_tier.value if delegator.subscription_tier else 'light',
                                    'first_name': delegator.first_name,
                                    'city': delegator_profile.city if delegator_profile else None,
                                    'company': delegator_profile.company if delegator_profile else None,
                                    'position': delegator_profile.position if delegator_profile else None,
                                    'interests': delegator_profile.interests if delegator_profile else None,
                                    'skills': delegator_profile.skills if delegator_profile else None,
                                    'goals': delegator_profile.goals if delegator_profile else None,
                                    'common_interests': None,  # Will be calculated later
                                    'common_skills': None,
                                    'common_goals': None,
                                    'common_tasks': None,
                                    'average_rating': delegator_profile.average_rating if delegator_profile else 0,
                                    'rating_count': delegator_profile.rating_count if delegator_profile else 0,
                                    'task_count': len(task_titles),
                                    'reason': f'делегироал {len(task_titles)} {pluralize_task(len(task_titles))}',
                                    'type': 'delegation'
                                })

                # Люди, которым я делегироал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_by == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(['pending', 'accepted']),
                    Task.status != 'deleted'
                ).all()

                delegatee_usernames = set()
                for task in my_delegated_tasks:
                    if task.delegated_to_username and task.delegated_to_username not in delegatee_usernames:
                        delegatee_usernames.add(task.delegated_to_username)
                        delegatee = session_db.query(User).filter(
                            or_(
                                User.username.ilike(task.delegated_to_username.replace('@', '')),
                                User.username.ilike(f'@{task.delegated_to_username.replace("@", "")}')
                            )
                        ).first()
                        if delegatee and delegatee.id != user.id:
                            # Skip contacts without username
                            if not delegatee.username:
                                logger.warning(f"Skipping elite delegating_by_me contact without username: user_id={delegatee.id}")
                                continue
                                
                            # Skip if already in partners_data
                            if any(p.get('contact_info') == delegatee.username for p in partners_data):
                                continue
                                
                            # Skip hidden and blocked contacts
                            delegatee_username_clean = delegatee.username.replace('@', '').lower() if delegatee.username else ''
                            if delegatee_username_clean in hidden_contacts or delegatee.username in blocked_by_me:
                                continue
                                
                            delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first()
                            task_titles = [
                                t.title for t in my_delegated_tasks if t.delegated_to_username == task.delegated_to_username]
                            
                            # Update avatar from Telegram if available
                            photo_url = delegatee.photo_url if delegatee.photo_url else None
                            if delegatee.telegram_id and 'bot' in request.app:
                                try:
                                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id, force_refresh=True)
                                    if updated_avatar and updated_avatar != delegatee.photo_url:
                                        delegatee.photo_url = updated_avatar
                                        session_db.commit()
                                        photo_url = updated_avatar
                                except Exception as e:
                                    logger.error(f"Error updating delegatee avatar for {delegatee.telegram_id}: {e}")
                            
                            delegating_by_me.append({
                                'contact_info': delegatee.username,
                                'telegram_id': delegatee.telegram_id,
                                'photo_url': photo_url,
                                'can_access': True,
                                'required_tier': None,
                                'subscription_tier': delegatee.subscription_tier.value if delegatee.subscription_tier else 'light',
                                'first_name': delegatee.first_name,
                                'city': delegatee_profile.city if delegatee_profile else None,
                                'company': delegatee_profile.company if delegatee_profile else None,
                                'position': delegatee_profile.position if delegatee_profile else None,
                                'interests': delegatee_profile.interests if delegatee_profile else None,
                                'skills': delegatee_profile.skills if delegatee_profile else None,
                                'goals': delegatee_profile.goals if delegatee_profile else None,
                                'common_interests': None,  # Will be calculated later
                                'common_skills': None,
                                'common_goals': None,
                                'common_tasks': None,
                                'average_rating': delegatee_profile.average_rating if delegatee_profile else 0,
                                'rating_count': delegatee_profile.rating_count if delegatee_profile else 0,
                                'task_count': len(task_titles),
                                'reason': f'я делегироал {len(task_titles)} {pluralize_task(len(task_titles))}',
                                'type': 'delegation'
                            })

            except Exception as e:
                logger.error(f"Error getting delegation contacts for elite: {e}")
                delegating_to_me = []
                delegating_by_me = []

            # Add delegation contacts to partners_data
            partners_data.extend(delegating_to_me)
            partners_data.extend(delegating_by_me)

            # Calculate common interests/skills/goals/tasks for delegation contacts
            for partner in partners_data:
                if partner.get('type') == 'delegation':
                    partner_profile = None
                    if partner.get('contact_info'):
                        partner_user = session_db.query(User).filter_by(username=partner['contact_info']).first()
                        if partner_user:
                            partner_profile = session_db.query(UserProfile).filter_by(user_id=partner_user.id).first()
                    
                    if partner_profile and user_profile:
                        # Common interests
                        if partner_profile.interests and user_profile.interests:
                            user_interests = set(i.strip().lower() for i in user_profile.interests.split(','))
                            partner_interests = set(i.strip().lower() for i in partner_profile.interests.split(','))
                            common = user_interests & partner_interests
                            partner['common_interests'] = ', '.join(common) if common else None

                        # Common skills
                        if partner_profile.skills and user_profile.skills:
                            user_skills = set(s.strip().lower() for s in user_profile.skills.split(','))
                            partner_skills = set(s.strip().lower() for s in partner_profile.skills.split(','))
                            common_sk = user_skills & partner_skills
                            partner['common_skills'] = ', '.join(common_sk) if common_sk else None

                        # Common goals
                        if partner_profile.goals and user_profile.goals:
                            user_goals = set(g.strip().lower() for g in user_profile.goals.split(','))
                            partner_goals = set(g.strip().lower() for g in partner_profile.goals.split(','))
                            common_g = user_goals & partner_goals
                            partner['common_goals'] = ', '.join(common_g) if common_g else None

                        # Common tasks
                        user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                        partner_tasks = session_db.query(Task).filter_by(user_id=partner_user.id).all() if partner_user else []
                        
                        user_task_titles = set(t.title.lower().strip() for t in user_tasks if t.title)
                        partner_task_titles = set(t.title.lower().strip() for t in partner_tasks if t.title)
                        
                        common_task_titles = user_task_titles & partner_task_titles
                        if not common_task_titles:
                            partial_matches = set()
                            for user_task in user_task_titles:
                                user_words = set(user_task.split())
                                if len(user_words) < 2:
                                    continue
                                for partner_task in partner_task_titles:
                                    partner_words = set(partner_task.split())
                                    common_words = user_words & partner_words
                                    if len(common_words) >= 2:
                                        partial_matches.add(user_task)
                            if partial_matches:
                                common_task_titles = partial_matches
                        
                        partner['common_tasks'] = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None

            # Sort: first by same city, then by rating
            user_city = user_profile.city.lower() if user_profile.city else None
            normalized_user_city = normalize_city(user_city)

            def sort_key(partner):
                partner_city = normalize_city(partner.get('city', ''))
                same_city = 0 if (normalized_user_city and partner_city == normalized_user_city) else 1

                rating = partner.get('average_rating', 0) or 0
                # Группы рейтиа:
                # 1. Высокий рейти (>= 5): сортируем по убыаю
                # 2. Нет рейтиа (0): йтраль, ыше плохих
                # 3. Низкий рейти (< 5): сортируем по убыаю
                if rating >= 5:
                    rating_group = 0  # Лучшая группа
                    rating_value = -rating  # Внутри группы по убыаю
                elif rating == 0:
                    rating_group = 1  # Средняя группа (нет данных)
                    rating_value = 0
                else:  # rating < 5
                    rating_group = 2  # Худшая группа
                    rating_value = -rating  # Внутри группы по убыаю

                return (same_city, rating_group, rating_value)

            partners_data.sort(key=sort_key)

            logger.info(f"Returning {len(partners_data)} elite (Premium) partners for user {user_id}")
            return web.json_response({'partners': partners_data})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error in api_elite_partners_handler: {e}", exc_info=True)
        return web.json_response({'partners': []}, status=200)


async def api_contact_profile_handler(request):
    """Get detailed profile of a contact"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        username = request.query.get('username')
        if not username:
            return web.json_response({'error': 'Username required'}, status=400)

        session_db = Session()
        try:
            # Find the contact user
            contact_user = session_db.query(User).filter_by(username=username).first()
            if not contact_user:
                return web.json_response({'error': 'Contact not found'}, status=404)

            # Update avatar from Telegram if available
            if contact_user.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], contact_user.telegram_id, force_refresh=True)
                    if updated_avatar and updated_avatar != contact_user.photo_url:
                        contact_user.photo_url = updated_avatar
                        session_db.commit()
                except Exception as e:
                    logger.error(f"Error updating contact avatar for {contact_user.telegram_id}: {e}")

            # Get contact profile (if doesn't exist, use defaults)
            profile = session_db.query(UserProfile).filter_by(user_id=contact_user.id).first()

            # Get current user's profile for common interests/skills
            current_user = session_db.query(User).filter_by(telegram_id=user_id).first()
            current_profile = session_db.query(UserProfile).filter_by(
                user_id=current_user.id).first() if current_user else None

            # Calculate common interests/skills
            common_interests = None
            if profile and current_profile and current_profile.interests and profile.interests:
                current_interests = set(i.strip().lower() for i in current_profile.interests.split(','))
                profile_interests = set(i.strip().lower() for i in profile.interests.split(','))
                common = current_interests & profile_interests
                common_interests = ', '.join(common) if common else None

            # Get active task count
            active_tasks = session_db.query(Task).filter(
                Task.user_id == contact_user.id,
                Task.status.in_(['in_progress', 'pending'])
            ).count()

            # Prepare profile data (use defaults if profile doesn't exist)
            try:
                profile_data = {
                    'contact_info': contact_user.username if hasattr(contact_user, 'username') else None,
                    'first_name': getattr(contact_user, 'first_name', None),
                    'last_name': getattr(contact_user, 'last_name', None),
                    'photo_url': getattr(contact_user, 'photo_url', None),
                    'city': getattr(profile, 'city', None) if profile else None,
                    'company': getattr(profile, 'company', None) if profile else None,
                    'position': getattr(profile, 'position', None) if profile else None,
                    'goals': getattr(profile, 'goals', None) if profile else None,
                    'skills': getattr(profile, 'skills', None) if profile else None,
                    'interests': getattr(profile, 'interests', None) if profile else None,
                    'languages': getattr(profile, 'languages', None) if profile else None,
                    'bio': getattr(profile, 'bio', None) if profile else None,
                    'current_plans': getattr(profile, 'current_plans', None) if profile else None,
                    'birthdate': getattr(profile, 'birthdate', None) if profile else None,
                    'zodiac_sign': getattr(profile, 'zodiac_sign', None) if profile else None,
                    'common_interests': common_interests,
                    'average_rating': getattr(profile, 'average_rating', 0) if profile else 0,
                    'task_count': active_tasks,
                    'subscription_tier': contact_user.subscription_tier.value if hasattr(contact_user, 'subscription_tier') and contact_user.subscription_tier else 'light'
                }
            except Exception as profile_error:
                logger.error(f"Error building profile data: {profile_error}", exc_info=True)
                # Fallback to minimal data
                profile_data = {
                    'contact_info': username,
                    'first_name': None,
                    'last_name': None,
                    'photo_url': None,
                    'city': None,
                    'company': None,
                    'position': None,
                    'goals': None,
                    'skills': None,
                    'interests': None,
                    'languages': None,
                    'bio': None,
                    'common_interests': None,
                    'average_rating': 0,
                    'task_count': 0,
                    'subscription_tier': 'light'
                }

            return web.json_response({'partner': profile_data})

        except Exception as e:
            logger.error(f"Error getting contact profile for username '{username}': {e}", exc_info=True)
            return web.json_response({'error': f'Internal server error: {str(e)}'}, status=500)
        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Unexpected error in api_contact_profile_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_favorite_contacts_handler(request):
    """Get or update favorite contacts"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
            if not profile:
                profile = UserProfile(user_id=user.id, favorite_contacts='[]')
                session_db.add(profile)
                session_db.commit()

            if request.method == 'GET':
                # Return favorite contacts
                favorites = []
                if profile.favorite_contacts:
                    try:
                        favorites = json.loads(profile.favorite_contacts)
                    except (json.JSONDecodeError, TypeError):
                        favorites = []
                        profile.favorite_contacts = '[]'
                        session_db.commit()
                else:
                    favorites = []
                return web.json_response({'favorites': favorites})

            elif request.method == 'POST':
                # Update favorite contacts
                try:
                    data = await request.json()
                except json.JSONDecodeError:
                    return web.json_response({'error': 'Invalid JSON'}, status=400)
                
                favorites = data.get('favorites', [])

                if not isinstance(favorites, list):
                    return web.json_response({'error': 'Favorites must be a list'}, status=400)

                # Convert all favorites to strings (handle both strings and integers)
                favorites = [str(f) for f in favorites]

                profile.favorite_contacts = json.dumps(favorites)
                session_db.commit()

                return web.json_response({'success': True})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Unexpected error in api_favorite_contacts_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_blocked_contacts_handler(request):
    """Get or update blocked contacts"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
            if not profile:
                profile = UserProfile(user_id=user.id)
                session_db.add(profile)
                session_db.commit()

            if request.method == 'GET':
                # Return blocked contacts
                blocked = []
                if profile.blocked_contacts:
                    try:
                        blocked = json.loads(profile.blocked_contacts)
                    except json.JSONDecodeError:
                        blocked = []
                return web.json_response({'blocked': blocked})

            elif request.method == 'POST':
                # Update blocked contacts
                data = await request.json()
                blocked = data.get('blocked', [])

                if not isinstance(blocked, list):
                    return web.json_response({'error': 'Blocked must be a list'}, status=400)

                # Convert all blocked to strings (handle both strings and integers)
                blocked = [str(b) for b in blocked]

                # Get old blocked list to detect newly blocked users
                old_blocked = []
                if profile.blocked_contacts:
                    try:
                        old_blocked = json.loads(profile.blocked_contacts)
                    except json.JSONDecodeError:
                        old_blocked = []
                
                # Find newly blocked users
                newly_blocked = set(blocked) - set(old_blocked)
                
                # Delete all delegated tasks from newly blocked users
                if newly_blocked:
                    for blocked_username in newly_blocked:
                        # Find and delete tasks delegated by this blocked user to current user
                        try:
                            # Clean username (remove @)
                            clean_blocked = blocked_username.replace('@', '').lower()
                            clean_current = (user.username or '').replace('@', '').lower()
                            
                            # Find the blocked user first
                            blocked_user = session_db.query(User).filter(
                                User.username != None,
                                User.username.ilike(clean_blocked)
                            ).first()
                            
                            if blocked_user and user.username:
                                # Delete tasks delegated from blocked user to current user
                                tasks_deleted = session_db.query(Task).filter(
                                    Task.user_id == blocked_user.id,
                                    Task.delegated_to_username.ilike(clean_current)
                                ).delete(synchronize_session=False)
                                
                                if tasks_deleted > 0:
                                    # Notify blocked user via bot (don't await to avoid blocking)
                                    try:
                                        message = f"@{user.username}  гото примать задачи от ас. Ваши делегироаые задачи были отклоны."
                                        # Schedule notification asynchronously to avoid blocking
                                        asyncio.create_task(bot.send_message(blocked_user.telegram_id, message))
                                    except Exception as e:
                                        logger.error(f"Failed to notify blocked user {blocked_username}: {e}")
                        except Exception as e:
                            logger.error(f"Error processing blocked user {blocked_username}: {e}")

                    session_db.commit()

                profile.blocked_contacts = json.dumps(blocked)
                session_db.commit()

                return web.json_response({'success': True})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Unexpected error in api_blocked_contacts_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def rate_user_handler(request):
    """Rate another user (1-10 scale)"""
    try:
        user_id = await get_user_id_from_request(request)
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

            # Don't save to Interaction - show notification instead
            success_message = f'Оцеа {rating}/10 для @{rated_username} сохра'

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
        user_id = await get_user_id_from_request(request)
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
            success_message = f'@{username} скрыт  {days} дй'
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
    """Get current user rating for another user"""
    try:
        user_id = await get_user_id_from_request(request)
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


async def set_user_rating_handler(request):
    """Set user rating for another user"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        rated_username = data.get('username')
        rating = data.get('rating')

        if not rated_username or rating is None:
            return web.json_response({'error': 'Missing username or rating'}, status=400)

        try:
            rating = int(rating)
            if rating < 1 or rating > 10:
                return web.json_response({'error': 'Rating must be between 1 and 10'}, status=400)
        except ValueError:
            return web.json_response({'error': 'Invalid rating value'}, status=400)

        session_db = Session()
        try:
            rater = session_db.query(User).filter_by(telegram_id=user_id).first()
            rated_user = session_db.query(User).filter(User.username.ilike(rated_username.replace('@', ''))).first()

            if not rater or not rated_user:
                return web.json_response({'error': 'User not found'}, status=404)

            if rater.id == rated_user.id:
                return web.json_response({'error': 'Cannot rate yourself'}, status=400)

            # Check if rating already exists
            existing_rating = session_db.query(UserRating).filter_by(
                rater_user_id=rater.id,
                rated_user_id=rated_user.id
            ).first()

            if existing_rating:
                existing_rating.rating = rating
            else:
                new_rating = UserRating(
                    rater_user_id=rater.id,
                    rated_user_id=rated_user.id,
                    rating=rating
                )
                session_db.add(new_rating)

            # Update average rating for rated user
            all_ratings = session_db.query(UserRating).filter_by(rated_user_id=rated_user.id).all()
            if all_ratings:
                avg_rating = sum(r.rating for r in all_ratings) / len(all_ratings)
                rated_user.average_rating = round(avg_rating)
                rated_user.rating_count = len(all_ratings)
            else:
                rated_user.average_rating = 0
                rated_user.rating_count = 0

            # Also update UserProfile.average_rating
            rated_profile = session_db.query(UserProfile).filter_by(user_id=rated_user.id).first()
            if rated_profile:
                rated_profile.average_rating = rated_user.average_rating

            session_db.commit()
            return web.json_response({'success': True, 'message': 'Rating submitted'})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error setting rating: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def create_post_handler(request):
    """API endpoint to create a new post"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        
        if not user_id:
            logger.warning("create_post_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.warning(f"create_post_handler: User not found for telegram_id {user_id}")
                return web.json_response({'error': 'User not found'}, status=401)

            data = await request.json()
            content = data.get('content', '').strip()

            if not content:
                return web.json_response({'error': 'Post content is required'}, status=400)

            if len(content) > 2000:
                return web.json_response({'error': 'Post is too long (max 2000 characters)'}, status=400)

            post = Post(
                user_id=user.id,
                username=user.username,
                content=content
            )
            session_db.add(post)
            session_db.commit()

            logger.info(f"Post created: id={post.id}, user_id={user.id}, username={user.username}")

            # Ensure created_at has UTC timezone info
            created_at_str = post.created_at.isoformat()
            if post.created_at and post.created_at.tzinfo is None:
                created_at_str = post.created_at.replace(tzinfo=dt_timezone.utc).isoformat()
            
            return web.json_response({
                'success': True,
                'post': {
                    'id': post.id,
                    'content': post.content,
                    'created_at': created_at_str,
                    'like_count': 0,
                    'user_liked': False,
                    'author': {
                        'username': user.username,
                        'first_name': user.first_name,
                        'photo_url': user.photo_url,
                        'is_current_user': True
                    }
                }
            })
        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error creating post: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_accept_delegated_task_handler(request):
    """Direct API endpoint to accept a delegated task"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        task_id = data.get('task_id')

        if not task_id:
            return web.json_response({'error': 'Missing task_id'}, status=400)

        session_db = Session()
        try:
            # Get the user
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user or not user.username:
                return web.json_response({'error': 'User not found or no username'}, status=404)

            # Get the task
            task = session_db.query(Task).filter_by(id=task_id).first()
            if not task:
                return web.json_response({'error': 'Task not found'}, status=404)

            # Check if user is the delegatee (compare usernames without @)
            username_clean = user.username.replace('@', '')
            if not task.delegated_to_username or task.delegated_to_username.lower() != username_clean.lower():
                return web.json_response({'error': 'Not authorized to accept this task'}, status=403)

            # Check if task is in pending delegation status
            if task.delegation_status != 'pending':
                return web.json_response({'error': 'Task is not in pending delegation status'}, status=400)

            # Accept task: status -> in_progress, delegation_status -> accepted
            task.status = 'in_progress'
            task.delegation_status = 'accepted'
            task.updated_at = datetime.now(pytz.UTC)

            # Create interaction record
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=f'Задача "{task.title}" принята и зята  работу'
            )
            session_db.add(interaction)

            session_db.commit()

            return web.json_response({
                'success': True,
                'message': f'Задача "{task.title}" принята'
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error accepting delegated task: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_reject_delegated_task_handler(request):
    """Direct API endpoint to reject a delegated task"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        task_id = data.get('task_id')

        if not task_id:
            return web.json_response({'error': 'Missing task_id'}, status=400)

        session_db = Session()
        try:
            # Get the user
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user or not user.username:
                return web.json_response({'error': 'User not found or no username'}, status=404)

            # Get the task
            task = session_db.query(Task).filter_by(id=task_id).first()
            if not task:
                return web.json_response({'error': 'Task not found'}, status=404)

            # Check if user is the delegatee (compare usernames without @)
            username_clean = user.username.replace('@', '')
            if not task.delegated_to_username or task.delegated_to_username.lower() != username_clean.lower():
                return web.json_response({'error': 'Not authorized to reject this task'}, status=403)

            # Check if task is in pending delegation status
            if task.delegation_status != 'pending':
                return web.json_response({'error': 'Task is not in pending delegation status'}, status=400)

            # Update task status to rejected
            task.status = 'rejected'
            task.delegation_status = 'rejected'
            task.updated_at = datetime.now(pytz.UTC)

            # Create interaction record
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=f'Задача "{task.title}" откло'
            )
            session_db.add(interaction)

            session_db.commit()

            return web.json_response({
                'success': True,
                'message': f'Задача "{task.title}" откло'
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error rejecting delegated task: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_update_profile_handler(request):
    """API endpoint to update user profile"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        city = data.get('city')
        company = data.get('company')
        position = data.get('position')
        skills = data.get('skills')
        interests = data.get('interests')
        goals = data.get('goals')

        session_db = Session()
        try:
            # Import the update_profile function
            from ai_integration.handlers import update_profile

            # Call the update_profile function
            update_profile(
                city=city,
                company=company,
                position=position,
                skills=skills,
                interests=interests,
                goals=goals,
                user_id=user_id,
                session=session_db
            )

            return web.json_response({
                'success': True,
                'message': 'Профиль облен'
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def get_feed_handler(request):
    """API endpoint to get posts from favorite contacts"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        logger.info(f"Feed handler called, session: {dict(session) if session else 'None'}, user_id: {user_id}")
        if not user_id:
            logger.error("No user_id in session for feed API")
            return web.json_response({'error': 'Not authenticated'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            
            # Get user's profile with favorites
            user_profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
            
            # Parse favorite contacts from JSON
            favorite_user_ids = []
            if user_profile and user_profile.favorite_contacts:
                try:
                    import json
                    favorite_data = json.loads(user_profile.favorite_contacts)
                    logger.info(f"Feed: favorite_data from profile: {favorite_data}")
                    # favorite_contacts может содержать как ID, так и usernames
                    for item in favorite_data:
                        if isinstance(item, int):
                            # Это user_id
                            favorite_user_ids.append(item)
                            logger.info(f"Feed: Added favorite user_id: {item}")
                        elif isinstance(item, str):
                            # Это username - йти user_id
                            username_clean = item.replace('@', '')
                            fav_user = session_db.query(User).filter(
                                or_(
                                    User.username == item,
                                    User.username == username_clean
                                )
                            ).first()
                            if fav_user:
                                favorite_user_ids.append(fav_user.id)
                                logger.info(f"Feed: Found favorite username '{item}' -> user_id {fav_user.id}")
                            else:
                                logger.warning(f"Feed: Favorite username '{item}' not found in database")
                except Exception as e:
                    logger.error(f"Error parsing favorite_contacts: {e}")
                    favorite_user_ids = []
            
            logger.info(f"Feed: final favorite_user_ids: {favorite_user_ids}")

            logger.info(f"Feed: final favorite_user_ids: {favorite_user_ids}")

            # Get users who blocked current user (exclude their posts)
            # Only check profiles that actually have blocked_contacts with our user
            blocked_by_users = set()
            try:
                from sqlalchemy import text
                # Use SQL LIKE to pre-filter, avoiding loading all profiles
                blocking_profiles = session_db.query(UserProfile.user_id, UserProfile.blocked_contacts).filter(
                    UserProfile.blocked_contacts.isnot(None),
                    UserProfile.blocked_contacts.contains(str(user.id))
                ).all()
                for profile_uid, blocked_json in blocking_profiles:
                    try:
                        blocked_list = json.loads(blocked_json)
                        if user.id in blocked_list:
                            blocked_by_users.add(profile_uid)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[FEED] Failed to check blocked_contacts: {e}")

            logger.info(f"Feed: blocked_by_users: {blocked_by_users}")

            # Include own posts too, but exclude users who blocked current user
            all_user_ids = [uid for uid in (favorite_user_ids + [user.id]) if uid not in blocked_by_users]
            
            logger.info(f"Feed: all_user_ids for feed (favorites + self - blocked): {all_user_ids}")

            # Get posts from favorites and self
            if all_user_ids:
                posts = session_db.query(Post).filter(
                    Post.user_id.in_(all_user_ids)
                ).order_by(Post.created_at.desc()).limit(20).all()
                logger.info(f"Found {len(posts)} posts for feed from users: {all_user_ids}")
                for post in posts:
                    post_author = session_db.query(User).filter_by(id=post.user_id).first()
                    logger.info(f"Feed post: ID={post.id}, author={post_author.username if post_author else 'unknown'} (user_id={post.user_id}), content={post.content[:30]}...")
            else:
                posts = []
                logger.info("No favorite contacts found, returning empty feed")

            # Get user profiles for author info
            user_ids = list(set([p.user_id for p in posts]))
            users_data = session_db.query(User, UserProfile).join(
                UserProfile, User.id == UserProfile.user_id, isouter=True
            ).filter(User.id.in_(user_ids)).all()

            users_map = {}
            for u, profile in users_data:
                # Update avatar from Telegram if available
                photo_url = u.photo_url
                if u.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], u.telegram_id, force_refresh=True)
                        if updated_avatar and updated_avatar != u.photo_url:
                            u.photo_url = updated_avatar
                            session_db.commit()
                            photo_url = updated_avatar
                    except Exception as e:
                        logger.error(f"Error updating avatar in feed for {u.telegram_id}: {e}")
                
                users_map[u.id] = {
                    'telegram_id': u.telegram_id,
                    'username': u.username,
                    'first_name': u.first_name,
                    'photo_url': photo_url,
                    'company': profile.company if profile else None,
                    'position': profile.position if profile else None,
                    'subscription_tier': u.subscription_tier.value if u.subscription_tier else 'LIGHT'
                }

            # Build feed response
            feed = []
            for post in posts:
                try:
                    author = users_map.get(post.user_id, {})
                    
                    # Get likes count and check if current user liked
                    likes_count = session_db.query(PostLike).filter_by(post_id=post.id).count()
                    user_liked = session_db.query(PostLike).filter_by(
                        post_id=post.id, 
                        user_id=user.id
                    ).first() is not None
                    
                    # Ensure created_at has UTC timezone info for proper browser conversion
                    created_at_str = None
                    if post.created_at:
                        if post.created_at.tzinfo is None:
                            # Assume UTC if no timezone
                            created_at_str = post.created_at.replace(tzinfo=dt_timezone.utc).isoformat()
                        else:
                            created_at_str = post.created_at.isoformat()
                    feed.append({
                        'id': post.id,
                        'content': post.content,
                        'created_at': created_at_str,
                        'likes_count': likes_count,
                        'user_liked': user_liked,
                        'author': {
                            'telegram_id': author.get('telegram_id'),
                            'username': author.get('username'),
                            'first_name': author.get('first_name'),
                            'photo_url': author.get('photo_url'),
                            'company': author.get('company'),
                            'position': author.get('position'),
                            'subscription_tier': author.get('subscription_tier', 'LIGHT'),
                            'is_current_user': post.user_id == user.id
                        }
                    })
                except Exception as post_error:
                    logger.error(f"Error processing post {post.id}: {post_error}")
                    continue

            # Проерить, есть ли прочитаые посты
            has_unread_posts = False
            if posts:
                # Получить ID сех посто
                post_ids = [p.id for p in posts]
                # Проверить, сколько из них пользователь уже видел
                viewed_count = session_db.query(PostView).filter(
                    PostView.user_id == user.id,
                    PostView.post_id.in_(post_ids)
                ).count()
                has_unread_posts = viewed_count < len(post_ids)

            return web.json_response({
                'success': True, 
                'posts': feed,
                'has_unread_posts': has_unread_posts
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error getting feed: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def mark_posts_viewed_handler(request):
    """API endpoint to mark posts as viewed"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')

        if not user_id:
            logger.warning("mark_posts_viewed_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        data = await request.json()
        post_ids = data.get('post_ids', [])

        if not post_ids:
            return web.json_response({'error': 'No post_ids provided'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            # Отметить посты как просмотреые (используем on_conflict_do_nothing для избежая дубликато)
            for post_id in post_ids:
                try:
                    # Проеряем, сущестует ли пост
                    post = session_db.query(Post).filter_by(id=post_id).first()
                    if post:
                        # Создаем запись о просмотре (если  сущестует)
                        existing_view = session_db.query(PostView).filter_by(
                            user_id=user.id, 
                            post_id=post_id
                        ).first()
                        
                        if not existing_view:
                            post_view = PostView(
                                user_id=user.id,
                                post_id=post_id,
                                viewed_at=datetime.now(dt_timezone.utc)
                            )
                            session_db.add(post_view)
                except Exception as e:
                    logger.error(f"Error marking post {post_id} as viewed: {e}")
                    continue

            session_db.commit()
            return web.json_response({'success': True})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error marking posts as viewed: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def delete_post_handler(request):
    """API endpoint to delete a post"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        
        if not user_id:
            logger.warning("delete_post_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        post_id = request.match_info.get('post_id')
        if not post_id:
            return web.json_response({'error': 'Post ID is required'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.warning(f"delete_post_handler: User not found for telegram_id {user_id}")
                return web.json_response({'error': 'User not found'}, status=401)
            
            post = session_db.query(Post).filter_by(id=post_id).first()
            
            if not post:
                return web.json_response({'error': 'Post not found'}, status=404)
            
            # Only owner can delete
            if post.user_id != user.id:
                return web.json_response({'error': 'You can only delete your own posts'}, status=403)

            # Delete all likes first to avoid constraint violation
            from models import PostLike
            session_db.query(PostLike).filter_by(post_id=post_id).delete()
            
            # Delete all comments first to avoid constraint violation
            from models import Comment
            session_db.query(Comment).filter_by(post_id=post_id).delete()
            
            # Delete all post views first to avoid constraint violation
            from models import PostView
            session_db.query(PostView).filter_by(post_id=post_id).delete()
            
            session_db.delete(post)
            session_db.commit()
            
            logger.info(f"Post {post_id} deleted by user {user.username}")

            return web.json_response({'success': True, 'message': 'Post deleted'})
        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error deleting post: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def create_comment_handler(request):
    """API endpoint to create a comment on a post"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        
        if not user_id:
            logger.warning("create_comment_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.warning(f"create_comment_handler: User not found for telegram_id {user_id}")
                return web.json_response({'error': 'User not found'}, status=401)

            data = await request.json()
            post_id = data.get('post_id')
            content = data.get('content', '').strip()

            if not post_id:
                return web.json_response({'error': 'Post ID is required'}, status=400)

            if not content:
                return web.json_response({'error': 'Comment content is required'}, status=400)

            if len(content) > 1000:
                return web.json_response({'error': 'Comment is too long (max 1000 characters)'}, status=400)

            # Check if post exists
            post = session_db.query(Post).filter_by(id=post_id).first()
            if not post:
                return web.json_response({'error': 'Post not found'}, status=404)

            from models import Comment
            comment = Comment(
                post_id=post_id,
                user_id=user.id,
                username=user.username,
                content=content
            )
            session_db.add(comment)
            session_db.commit()

            logger.info(f"Comment created: id={comment.id}, post_id={post_id}, user_id={user.id}")

            # Ensure created_at has UTC timezone info
            created_at_str = comment.created_at.isoformat()
            if comment.created_at and comment.created_at.tzinfo is None:
                created_at_str = comment.created_at.replace(tzinfo=dt_timezone.utc).isoformat()

            return web.json_response({
                'success': True,
                'comment': {
                    'id': comment.id,
                    'post_id': comment.post_id,
                    'content': comment.content,
                    'created_at': created_at_str,
                    'author': {
                        'username': user.username,
                        'first_name': user.first_name,
                        'photo_url': user.photo_url,
                        'is_current_user': True
                    }
                }
            })
        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error creating comment: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def get_comments_handler(request):
    """API endpoint to get comments for a post"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        
        if not user_id:
            logger.warning("get_comments_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        post_id = request.match_info.get('post_id')
        if not post_id:
            return web.json_response({'error': 'Post ID is required'}, status=400)

        session_db = Session()
        try:
            from models import Comment
            comments = session_db.query(Comment).filter_by(post_id=post_id).order_by(Comment.created_at.asc()).all()

            # Get user info for comment authors
            user_ids = list(set([c.user_id for c in comments]))
            
            # If no comments, return empty list
            if not user_ids:
                return web.json_response({'success': True, 'comments': []})
            
            users_data = session_db.query(User).filter(User.id.in_(user_ids)).all()
            
            # Update avatars from Telegram
            for u in users_data:
                if u.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], u.telegram_id, force_refresh=True)
                        if updated_avatar and updated_avatar != u.photo_url:
                            u.photo_url = updated_avatar
                            session_db.commit()
                    except Exception as e:
                        logger.error(f"Error updating avatar in comments for {u.telegram_id}: {e}")
            
            users_map = {u.id: u for u in users_data}

            # Get current user's database id
            current_user = session_db.query(User).filter_by(telegram_id=user_id).first()
            current_user_id = current_user.id if current_user else None

            result = []
            for comment in comments:
                author = users_map.get(comment.user_id)
                if author:
                    # Ensure created_at has UTC timezone info
                    created_at_str = comment.created_at.isoformat()
                    if comment.created_at and comment.created_at.tzinfo is None:
                        created_at_str = comment.created_at.replace(tzinfo=dt_timezone.utc).isoformat()
                    
                    result.append({
                        'id': comment.id,
                        'content': comment.content,
                        'created_at': created_at_str,
                        'author': {
                            'username': author.username,
                            'first_name': author.first_name,
                            'photo_url': author.photo_url,
                            'is_current_user': comment.user_id == current_user_id
                        }
                    })

            return web.json_response({'success': True, 'comments': result})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error getting comments: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def delete_comment_handler(request):
    """Delete a comment"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        
        if not user_id:
            logger.warning("delete_comment_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        comment_id = int(request.match_info['comment_id'])
        logger.info(f"Deleting comment {comment_id} by user {user_id}")

        db_session = Session()
        
        # Get the comment
        comment = db_session.query(Comment).filter_by(id=comment_id).first()
        if not comment:
            logger.warning(f"Comment {comment_id} not found")
            return web.json_response({'error': 'Comment not found'}, status=404)

        logger.info(f"Comment found: user_id={comment.user_id}, post_id={comment.post_id}")

        # Get current user
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.warning(f"User with telegram_id {user_id} not found")
            return web.json_response({'error': 'User not found'}, status=404)

        logger.info(f"User found: id={user.id}")

        # Check if user owns the comment
        if comment.user_id != user.id:
            logger.warning(f"User {user.id} trying to delete comment owned by {comment.user_id}")
            return web.json_response({'error': 'Forbidden'}, status=403)

        # Delete the comment - expunge first to avoid relationship issues
        db_session.expunge(comment)
        db_session.query(Comment).filter_by(id=comment_id).delete()
        db_session.commit()
        logger.info(f"Comment {comment_id} deleted successfully")

        return web.json_response({'success': True})

    except Exception as e:
        if db_session:
            db_session.rollback()
        logger.error(f"Error deleting comment: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)
    finally:
        if db_session:
            db_session.close()


async def toggle_like_handler(request):
    """Toggle like on a post"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        
        if not user_id:
            logger.warning("toggle_like_handler: No user_id in session")
            return web.json_response({'error': 'Unauthorized'}, status=401)

        post_id = int(request.match_info['post_id'])
        logger.info(f"Toggling like on post {post_id} by user {user_id}")

        db_session = Session()
        
        # Get current user
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.warning(f"User with telegram_id {user_id} not found")
            return web.json_response({'error': 'User not found'}, status=404)

        # Check if post exists
        post = db_session.query(Post).filter_by(id=post_id).first()
        if not post:
            logger.warning(f"Post {post_id} not found")
            return web.json_response({'error': 'Post not found'}, status=404)

        # Check if like already exists
        existing_like = db_session.query(PostLike).filter_by(
            post_id=post_id,
            user_id=user.id
        ).first()

        if existing_like:
            # Unlike: remove like
            db_session.delete(existing_like)
            db_session.commit()
            logger.info(f"User {user.id} unliked post {post_id}")
            action = 'unliked'
        else:
            # Like: add new like
            new_like = PostLike(post_id=post_id, user_id=user.id)
            db_session.add(new_like)
            db_session.commit()
            logger.info(f"User {user.id} liked post {post_id}")
            action = 'liked'

        # Get updated likes count
        likes_count = db_session.query(PostLike).filter_by(post_id=post_id).count()

        return web.json_response({
            'success': True,
            'action': action,
            'likes_count': likes_count,
            'user_liked': action == 'liked'
        })

    except Exception as e:
        if db_session:
            db_session.rollback()
        logger.error(f"Error toggling like: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)
    finally:
        if db_session:
            db_session.close()


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

        avatar_url = await get_user_avatar_url(request.app['bot'], telegram_id, force_refresh=True)

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
    user_id = await get_user_id_from_request(request)
    logger.info(f"API reminders handler called, user_id: {user_id}")
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
        except BaseException:
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
                upcoming_reminders.append(f"{task.title}  {reminder_time_local}")

    return web.json_response({'reminders': upcoming_reminders[:5]})


async def on_startup(app):
    from config import LOCAL
    # Always use SimpleCookieStorage (no Redis)
    
    # Setup session middleware with proper cookie settings
    cookie_params = {'httponly': True}
    if not LOCAL:
        cookie_params.update({
            'secure': True,  # HTTPS only in production
            'samesite': 'None'  # Allow cross-site for Telegram auth
        })
    else:
        cookie_params['samesite'] = 'Lax'
    
    storage = SimpleCookieStorage(**cookie_params)
    logger.info("Using SimpleCookieStorage for sessions")
    
    aiohttp_session.setup(app, storage)
    logger.info("Session middleware set up")
    
    # Синхрозируем users.subscription_tier с subscriptions.tier при старте
    try:
        from datetime import datetime
        import pytz
        session_db = Session()
        active_subscriptions = session_db.query(Subscription).filter_by(status='active').all()
        synced_count = 0
        
        for sub in active_subscriptions:
            user = session_db.query(User).filter_by(id=sub.user_id).first()
            if not user:
                continue
            
            # Проеряем,  истекла ли подписка
            now = datetime.now(pytz.UTC)
            if sub.end_date and sub.end_date.tzinfo is None:
                sub.end_date = sub.end_date.replace(tzinfo=pytz.UTC)
            
            if sub.end_date and sub.end_date < now:
                continue
            
            # Синхрозируем тарифы
            user_tier_str = str(user.subscription_tier).split('.')[-1] if user.subscription_tier else None
            sub_tier_str = str(sub.tier).split('.')[-1] if sub.tier else None
            
            if user_tier_str != sub_tier_str:
                logger.info(f"Syncing tier for @{user.username}: users.{user_tier_str} -> subscriptions.{sub_tier_str}")
                user.subscription_tier = sub.tier
                synced_count += 1
        
        if synced_count > 0:
            session_db.commit()
            logger.info(f"✅ Synced {synced_count} user tiers with subscriptions on startup")
        
        # Синхрозируем users.average_rating с user_profiles.average_rating
        all_profiles = session_db.query(UserProfile).all()
        rating_synced_count = 0
        
        for profile in all_profiles:
            user = session_db.query(User).filter_by(id=profile.user_id).first()
            if not user:
                continue
            
            # Синхрозируем рейти
            if user.average_rating != profile.average_rating or user.rating_count != profile.rating_count:
                logger.info(f"Syncing rating for @{user.username}: users.{user.average_rating} -> profile.{profile.average_rating}")
                user.average_rating = profile.average_rating
                user.rating_count = profile.rating_count
                rating_synced_count += 1
        
        if rating_synced_count > 0:
            session_db.commit()
            logger.info(f"✅ Synced {rating_synced_count} user ratings with profiles on startup")
        
        session_db.close()
    except Exception as e:
        logger.error(f"❌ Error syncing subscription tiers on startup: {e}")

    # Очищаем накопленную историю диалогов (содержит галлюцинации)
    try:
        session_db = Session()
        users_with_history = session_db.query(User).filter(User.conversation_context.isnot(None)).all()
        cleared = 0
        for user in users_with_history:
            user.conversation_context = None
            cleared += 1
        if cleared:
            session_db.commit()
            logger.info(f"✅ Cleared conversation history for {cleared} users (anti-hallucination reset)")
        session_db.close()
    except Exception as e:
        logger.error(f"❌ Error clearing conversation history: {e}")

    # Set webhook for production mode
    if bot and not LOCAL:
        webhook_url = os.getenv('WEBHOOK_URL', 'https://asibiont.ru/webhook')
        try:
            await bot.set_webhook(webhook_url)
            logger.info(f"✅ Webhook set to: {webhook_url}")
        except Exception as e:
            logger.error(f"❌ Failed to set webhook: {e}")


async def on_shutdown(app):
    """Cleanup on application shutdown"""
    logger.info("Application shutting down...")
    if bot and not LOCAL:
        try:
            await bot.delete_webhook()
            logger.info("✅ Webhook deleted on shutdown")
        except Exception as e:
            logger.error(f"❌ Failed to delete webhook: {e}")
    
    # Закрываем единый API-клиент
    try:
        from ai_integration.api_client import close_api_client
        await close_api_client()
        logger.info("✅ API client closed")
    except Exception as e:
        logger.error(f"❌ Failed to close API client: {e}")


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

        # Get tasks created by me OR delegated to me OR delegated by me
        query_conditions = [Task.user_id == user.id]
        if user.username:
            # Compare without @ symbol to handle both @username and username formats
            username_clean = user.username.replace('@', '')
            query_conditions.append(or_(
                Task.delegated_to_username.ilike(username_clean),
                Task.delegated_to_username.ilike(f'@{username_clean}')
            ))
        # Add tasks delegated BY me
        query_conditions.append(Task.delegated_by == user.id)
        
        tasks = session_db.query(Task).filter(or_(*query_conditions)).all()
        
        # Exclude rejected tasks from the list
        tasks = [t for t in tasks if t.status != 'rejected' and (not hasattr(t, 'delegation_status') or t.delegation_status != 'rejected')]
        
        logger.info(f"Found {len(tasks)} tasks for user {user_id}")

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
                title = re.sub(r' - [Дд]елегироа (от|) @\w+$', '', title)

                # Check if task is delegated TO me or BY me
                if user.username and (task.delegated_to_username.lower() == user.username.lower(
                ) or task.delegated_to_username.lower() == f"@{user.username.lower()}"):
                    # Task delegated TO me
                    creator = session_db.query(User).filter_by(id=task.delegated_by).first()
                    if creator:
                        title = f"{title} - Делегироа от @{creator.username}"
                elif task.user_id == user.id:
                    # Task delegated BY me to someone else
                    title = f"{title} - Делегироа  @{delegated_username}"

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
                'delegation_status': task.delegation_status if hasattr(task, 'delegation_status') else None,
                'delegated_to': task.delegated_to_username,
                'delegated_to_username': task.delegated_to_username,  # Дублируем для удобста
                'delegated_by': None,  # Будет устале же
                'delegated_by_username': None,  # Username того кто поручил
                'delegated_by_me': task.delegated_by == user.id  # True если я делегироал эту задачу
            }
            
            # Определяем delegated_by и delegated_by_username
            if task.delegated_by and task.delegated_by != user.id:
                # Задача была делегироа м кем-то
                delegator = session_db.query(User).filter_by(id=task.delegated_by).first()
                if delegator and delegator.username:
                    task_data['delegated_by'] = delegator.username
                    task_data['delegated_by_username'] = delegator.username
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task_data['reminder_time'] = local_reminder.isoformat()
                task_data['reminder_time_local'] = local_reminder.strftime('%d.%m.%Y %H:%M')
                # Просрочка для заершеых задач (pending или in_progress)
                task_data['overdue'] = local_reminder < user_now and task.status in ['pending', 'in_progress']
                if task_data['overdue']:
                    delta = user_now - local_reminder
                    total_seconds = int(delta.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    if days > 0:
                        day_word = "день" if days == 1 else "дня" if days < 5 else "дней"
                        task_data['overdue_text'] = f'{days} {day_word}'
                    elif hours > 0:
                        hour_word = "час" if hours == 1 else "часа" if hours < 5 else "часов"
                        task_data['overdue_text'] = f'{hours} {hour_word}'
                    else:
                        min_word = "минуту" if minutes == 1 else "минуты" if minutes < 5 else "минут"
                        task_data['overdue_text'] = f'{minutes} {min_word}'
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
        # Search for both @username and username formats
        username_variants = [f"@{user.username}", user.username]
        incoming = session_db.query(Task).filter(
            Task.delegated_to_username.in_(username_variants)
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

        # Only load last 100 interactions (not ALL) for performance
        interactions = session_db.query(Interaction).filter_by(
            user_id=user.id).order_by(
            Interaction.created_at.desc()).limit(100).all()
        interactions.reverse()  # Back to chronological order
        
        logger.info(f"Loaded last {len(interactions)} interactions for user {user.id}")

        # Get history cleared timestamp from DB
        history_cleared_timestamp = 0
        if user.history_cleared_at:
            history_cleared_timestamp = user.history_cleared_at.timestamp()

        # Filter interactions based on cleared timestamp and non-null content
        filtered_interactions = [
            i for i in interactions
            if i.created_at.replace(tzinfo=dt_timezone.utc).timestamp() > history_cleared_timestamp
            and i.content is not None and i.content.strip() != ''
        ]
        
        logger.info(f"After filtering: {len(filtered_interactions)} interactions")

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
            created_at_utc = interaction.created_at
            if created_at_utc.tzinfo is None:
                created_at_utc = pytz.UTC.localize(created_at_utc)
            elif hasattr(created_at_utc.tzinfo, 'zone'):  # pytz timezone
                pass  # already pytz
            else:  # datetime.timezone
                created_at_utc = created_at_utc.replace(tzinfo=pytz.UTC)
            created_at_local = created_at_utc.astimezone(user_tz)

            interactions_data.append({
                'id': interaction.id,
                'content': interaction.content,
                'message_type': interaction.message_type,
                'created_at': created_at_local.isoformat()
            })

        logger.info(f"Returning {len(interactions_data)} interactions to frontend")
        return web.json_response({'interactions': interactions_data})
    except Exception as e:
        logger.error(f"Error fetching interactions: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def api_search_contacts_handler(request):
    """API для поиска контакто по username"""
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

                # Обляем аатар если нуж
                photo_url = user.photo_url
                if user.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], user.telegram_id, force_refresh=True)
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

        # Проерка алидсти timezone
        try:
            pytz.timezone(timezone)
        except BaseException:
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
    """API для получения и обновления профиля пользователя"""
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

    # Handle POST request - update profile
    if request.method == 'POST':
        try:
            data = await request.json()
            logger.info(f"[API PROFILE POST] Received data: {data}")
            
            session_db = Session()
            try:
                user = session_db.query(User).filter_by(telegram_id=user_id).first()
                if not user:
                    return web.json_response({'error': 'User not found'}, status=404)

                profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
                if not profile:
                    profile = UserProfile(user_id=user.id)
                    session_db.add(profile)

                # Update profile fields (пустые строки удаляют данные)
                if 'city' in data:
                    profile.city = data['city'].strip() if data['city'] and data['city'].strip() else None
                if 'birthdate' in data:
                    profile.birthdate = data['birthdate'].strip() if data['birthdate'] and data['birthdate'].strip() else None
                if 'zodiac_sign' in data:
                    profile.zodiac_sign = data['zodiac_sign'].strip() if data['zodiac_sign'] and data['zodiac_sign'].strip() else None
                if 'company' in data:
                    profile.company = data['company'].strip() if data['company'] and data['company'].strip() else None
                if 'position' in data:
                    profile.position = data['position'].strip() if data['position'] and data['position'].strip() else None
                if 'interests' in data:
                    profile.interests = data['interests'].strip() if data['interests'] and data['interests'].strip() else None
                if 'skills' in data:
                    profile.skills = data['skills'].strip() if data['skills'] and data['skills'].strip() else None
                if 'goals' in data:
                    profile.goals = data['goals'].strip() if data['goals'] and data['goals'].strip() else None
                if 'bio' in data:
                    profile.bio = data['bio'].strip() if data['bio'] and data['bio'].strip() else None

                # Update user fields (telegram_channel хранится в User, не в UserProfile)
                if 'telegram_channel' in data:
                    user.telegram_channel = data['telegram_channel'].strip() if data['telegram_channel'] and data['telegram_channel'].strip() else None

                session_db.commit()
                logger.info(f"[API PROFILE POST] Profile updated for user {user_id}")
                
                return web.json_response({'success': True, 'message': 'Profile updated'})
            finally:
                session_db.close()
        except Exception as e:
            logger.error(f"Error updating profile: {e}", exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    # Get fresh data from database (убрали кеширование для мгновенного обновления)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)

        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()

        profile_data = {
            'username': user.username,
            'telegram_channel': user.telegram_channel,
            'city': profile.city if profile else None,
            'birthdate': profile.birthdate if profile else None,
            'zodiac_sign': profile.zodiac_sign if profile else None,
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

        # Get subscription and user data for additional fields
        subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()

        # Calculate current time and date in user's timezone
        user_tz = pytz.UTC
        if user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                user_tz = pytz.UTC

        base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz)

        months = [
            'яаря', 'фераля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'агуста', 'сентября', 'октября', 'ября', 'декабря'
        ]
        current_time = user_now.strftime('%H:%M')
        current_date = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

        # Format subscription end date
        formatted_end_date = None
        if subscription and subscription.end_date:
            end_dt = subscription.end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=pytz.UTC)
            end_local = end_dt.astimezone(user_tz if user.timezone else pytz.timezone('Europe/Moscow'))
            formatted_end_date = f"{end_local.day:02d}.{end_local.month:02d}.{end_local.year}"

        # Get user avatar URL
        user_avatar_url = user.photo_url if user.photo_url else None
        if user_avatar_url:
            import random
            user_avatar_url += f"?r={random.randint(100000, 999999)}"

        # Add additional data to response
        response_data = {
            'profile': profile_data,
            'current_time': current_time,
            'current_date': current_date,
            'formatted_end_date': formatted_end_date,
            'user_avatar_url': user_avatar_url,
            'first_name': user.first_name,
            'telegram_id': user.telegram_id,
            'referral_balance': user.referral_balance,
            'timezone': user.timezone or 'UTC'
        }

        return web.json_response(response_data)
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def extend_subscription_handler(request):
    """Перепралее  страцу ыбора тарифа"""
    return web.HTTPFound('/subscription_tiers')


@aiohttp_jinja2.template('subscription_tiers.html')
async def subscription_tiers_handler(request):
    """Страца ыбора тарифа подписки"""
    return {}


async def apply_promo_code_handler(request):
    """Применяет промокод и актиирует подписку"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    if not user_id:
        return web.json_response({'success': False, 'message': 'Не аторизоан'}, status=401)

    data = await request.post()
    promo_code = data.get('promo_code', '').strip().upper()

    if not promo_code:
        return web.json_response({'success': False, 'message': 'Ведите промокод'})

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'success': False, 'message': 'Пользователь не найден'})

        # Проеряем промокод
        promo = session.query(PromoCode).filter_by(code=promo_code).first()
        if not promo:
            return web.json_response({'success': False, 'message': 'Неерный промокод'})

        # Проеряем срок дейстия - приодим обе даты к одму формату
        now = datetime.now(dt_timezone.utc)
        expires_at = promo.expires_at.replace(tzinfo=dt_timezone.utc) if promo.expires_at.tzinfo is None else promo.expires_at
        if expires_at < now:
            return web.json_response({'success': False, 'message': 'Срок дейстия промокода истек'})

        # Проверяем лимит использований
        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            return web.json_response({'success': False, 'message': 'Промокод достиг лимита использований'})

        # Проверяем, использовал ли уже этот пользователь этот промокод
        import json
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id in used_by_users:
            return web.json_response({'success': False, 'message': 'Вы уже использовали этот промокод'})

        # Актиируем подписку
        start_date = now
        end_date = start_date + timedelta(days=promo.duration_days)

        # щем сущестующую подписку или создаем ую
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription:
            subscription = Subscription(
                user_id=user.id,
                telegram_id=user.telegram_id,
                telegram_username=user.username,
                status='active',
                tier=promo.tier,
                start_date=start_date,
                end_date=end_date
            )
            session.add(subscription)
            logger.info(f"Created new subscription for user {user.id} with tier {promo.tier}")
        else:
            old_tier = subscription.tier
            subscription.status = 'active'
            subscription.tier = promo.tier
            subscription.start_date = start_date
            subscription.end_date = end_date
            logger.info(f"Updated existing subscription for user {user.id}: tier {old_tier} -> {promo.tier}")

        # Обляем user.subscription_tier для синхрозации
        user.subscription_tier = promo.tier

        # Обновляем счетчик использований
        promo.used_count += 1
        if promo.max_uses is None or promo.used_count >= promo.max_uses:
            promo.is_used = True

        # Добавляем пользователя в список использовавших
        import json
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id not in used_by_users:
            used_by_users.append(user.id)
        promo.used_by_users = json.dumps(used_by_users)

        # Устарешие поля для соместимости
        promo.used_by_user_id = user.id
        promo.used_at = now

        session.commit()
        logger.info(f"Promo code {promo_code} activated for user {user.id}, subscription created/updated with tier {subscription.tier}")

        # Сохраняем зчея до закрытия сессии
        tier_name = promo.tier.value if hasattr(promo.tier, 'value') else str(promo.tier)
        duration = promo.duration_days
        end_date_str = end_date.strftime("%d.%m.%Y")

        return web.json_response({
            'success': True,
            'message': f'Промокод актиироан! Подписка {tier_name}  {duration} дй до {end_date_str}'
        })

    except Exception as e:
        logger.error(f"Error applying promo code: {e}", exc_info=True)
        session.rollback()
        return web.json_response({'success': False, 'message': f'Ошибка актиации промокода: {str(e)}'}, status=500)
    finally:
        session.close()


async def create_payment_handler(request):
    """Создает платеж для ыбраого тарифа"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    logger.info(f"Create payment handler called with user_id: {user_id}")

    if not user_id:
        logger.warning("No user_id in session, redirecting to login")
        return web.HTTPFound('/')

    tier = request.query.get('tier', 'light')
    logger.info(f"Creating payment for tier: {tier}")

    # Validate tier
    if tier not in ['light', 'standard', 'premium']:
        tier = 'light'

    try:
        from payments import create_payment, get_tier_price, get_tier_name

        amount = get_tier_price(tier)
        tier_name = get_tier_name(tier)

        logger.info(f"Creating payment: amount={amount}, tier={tier}, user_id={user_id}")

        payment_url = create_payment(
            amount=str(amount),
            description=f"Подписка ASI Biont - {tier_name}  30 дй",
            user_id=user_id,
            tier=tier
        )

        logger.info(f"Payment URL created: {payment_url}")
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return web.Response(text=f'Ошибка создая платежа: {str(e)}', status=500)


async def clear_database_handler(request):
    """Clear all data from database (admin only)"""
    try:
        # Security check - require admin secret
        admin_secret = request.headers.get('X-Admin-Secret') or request.query.get('admin_secret')
        expected_secret = os.getenv('ADMIN_SECRET')
        
        if not admin_secret or admin_secret != expected_secret:
            return web.json_response({'error': 'Unauthorized'}, status=403)
        
        logger.warning("Database clear requested by admin")
        
        # Clear all data by dropping and recreating tables
        from models import Base
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        
        logger.warning("Database cleared successfully")
        return web.json_response({'message': 'Database cleared successfully'})
        
    except Exception as e:
        logger.error(f"Error clearing database: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def add_test_users_handler(request):
    """Add test users with different tiers and interests (admin only)"""
    try:
        # Security check
        admin_secret = request.query.get('secret', '')
        expected_secret = os.getenv('ADMIN_SECRET')
        
        if not admin_secret or admin_secret != expected_secret:
            return web.json_response({'error': 'Unauthorized'}, status=403)
        
        session = Session()
        
        # Данные пользователей
        sport_users = [
            {'username': 'sport_alex', 'telegram_id': 1000001, 'interests': 'футбол, баскетбол, олейбол', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_maria', 'telegram_id': 1000002, 'interests': 'бег, йога, пилатес', 'tier': SubscriptionTier.STANDARD},
            {'username': 'sport_ivan', 'telegram_id': 1000003, 'interests': 'теис, плаае, елоспорт', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'sport_olga', 'telegram_id': 1000004, 'interests': 'фитс, кроссфит, бодибилди', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_dmitry', 'telegram_id': 1000005, 'interests': 'хоккей, биатлон, лыжи', 'tier': SubscriptionTier.STANDARD},
        ]
        
        business_users = [
            {'username': 'biz_anna', 'telegram_id': 2000001, 'interests': 'стартапы, маркети, продажи', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_sergey', 'telegram_id': 2000002, 'interests': 'иестиции, финсы, криптоалюта', 'tier': SubscriptionTier.LIGHT},
            {'username': 'biz_elena', 'telegram_id': 2000003, 'interests': 'упралее проектами, agile, scrum', 'tier': SubscriptionTier.STANDARD},
            {'username': 'biz_maxim', 'telegram_id': 2000004, 'interests': 'e-commerce, оайн-торголя, логистика', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_victoria', 'telegram_id': 2000005, 'interests': 'HR, рекрути, обучее персола', 'tier': SubscriptionTier.LIGHT},
        ]
        
        all_users = sport_users + business_users
        added = []
        skipped = []
        
        for user_data in all_users:
            existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            
            if existing_user:
                # Проеряем есть ли subscription
                existing_sub = session.query(Subscription).filter_by(user_id=existing_user.id).first()
                if existing_sub:
                    skipped.append(user_data['username'])
                    continue
                else:
                    # User есть,  subscription т - добаляем только subscription
                    end_date = datetime.now(dt_timezone.utc) + timedelta(days=365)
                    subscription = Subscription(
                        user_id=existing_user.id,
                        telegram_id=user_data['telegram_id'],
                        telegram_username=user_data['username'],
                        username=user_data['username'],
                        status='active',
                        plan='yearly',
                        tier=user_data['tier'],
                        start_date=datetime.now(dt_timezone.utc),
                        end_date=end_date,
                        login_count=1,
                        created_at=datetime.now(dt_timezone.utc)
                    )
                    session.add(subscription)
                    added.append(f"@{user_data['username']} (subscription only - {user_data['tier'].value})")
                    continue
            
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                subscription_tier=user_data['tier'],
                created_at=datetime.now(dt_timezone.utc)
            )
            session.add(user)
            session.flush()
            
            profile = UserProfile(
                user_id=user.id,
                interests=user_data['interests'],
                skills='',
                goals=''
            )
            session.add(profile)
            
            end_date = datetime.now(dt_timezone.utc) + timedelta(days=365)
            subscription = Subscription(
                user_id=user.id,
                telegram_id=user_data['telegram_id'],
                telegram_username=user_data['username'],
                username=user_data['username'],
                status='active',
                plan='yearly',
                tier=user_data['tier'],
                start_date=datetime.now(dt_timezone.utc),
                end_date=end_date,
                login_count=1,
                created_at=datetime.now(dt_timezone.utc)
            )
            session.add(subscription)
            
            added.append(f"@{user_data['username']} ({user_data['tier'].value})")
        
        session.commit()
        total = session.query(User).count()
        session.close()
        
        logger.info(f"Test users added: {len(added)}, skipped: {len(skipped)}")
        
        return web.json_response({
            'success': True,
            'added': added,
            'skipped': skipped,
            'total_users': total
        })
        
    except Exception as e:
        logger.error(f"Error adding test users: {e}")
        if 'session' in locals():
            session.rollback()
            session.close()
        return web.json_response({'error': str(e)}, status=500)


# Routes
app.router.add_get('/health', health_handler)
app.router.add_get('/', login_handler)
app.router.add_get('/admin/index.html', lambda r: web.HTTPFound('/dashboard'))  # Redirect old admin URL
app.router.add_get('/tg_auth', auth_handler)
app.router.add_get('/telegram_auth', auth_handler)  # Keep old route for compatibility
app.router.add_get('/logout', logout_handler)
app.router.add_get('/dashboard', dashboard_handler)
app.router.add_get('/tasks', tasks_handler)
app.router.add_get('/profile', profile_handler)
app.router.add_post('/chat', chat_handler)
app.router.add_post('/api/send_message', api_send_message_handler)
app.router.add_post('/clear_history', clear_history_handler)

app.router.add_post('/clear_user_tasks', clear_user_tasks_handler)
app.router.add_post('/clear_single_task', clear_single_task_handler)
app.router.add_post('/complete_task', complete_task_handler)
app.router.add_post('/restore_task', restore_task_handler)
app.router.add_post('/skip_task', skip_task_handler)
app.router.add_post('/delete_task', delete_task_handler)
app.router.add_post('/reschedule_task', reschedule_task_handler)
app.router.add_post('/update_timezone', update_timezone_handler)
app.router.add_get('/extend_subscription', extend_subscription_handler)
app.router.add_get('/subscription_tiers', subscription_tiers_handler)
app.router.add_get('/subscription-tiers', subscription_tiers_handler)  # Alias with dash
app.router.add_post('/apply_promo_code', apply_promo_code_handler)
app.router.add_get('/create_payment', create_payment_handler)
# app.router.add_get('/check_sportfan3', check_sportfan3_handler)  # Disabled - user deleted from production
app.router.add_get('/direct_login', direct_login_handler)
# SEO: verification files
app.router.add_get('/yandex_48ffa2026650f03f.html', lambda r: web.FileResponse('static/yandex_48ffa2026650f03f.html'))
app.router.add_get('/BingSiteAuth.xml', lambda r: web.FileResponse('static/BingSiteAuth.xml'))
# SEO: robots.txt и sitemap.xml в корне (поисковые ищут именно тут)
app.router.add_get('/robots.txt', lambda r: web.FileResponse('static/robots.txt', headers={'Content-Type': 'text/plain; charset=utf-8'}))
app.router.add_get('/sitemap.xml', lambda r: web.FileResponse('static/sitemap.xml', headers={'Content-Type': 'application/xml; charset=utf-8'}))
# SEO: IndexNow key file
app.router.add_get('/d6193b04262141bba808b1279123715b.txt', lambda r: web.FileResponse('static/d6193b04262141bba808b1279123715b.txt'))
app.router.add_static('/static', 'static')
app.router.add_post('/webhook/yookassa', yookassa_webhook)
# API routes for dynamic updates
app.router.add_get('/api/tasks', api_tasks_handler)
app.router.add_get('/api/partners', api_partners_handler)
app.router.add_post('/admin/clear_database', clear_database_handler)
app.router.add_get('/admin/add_test_users', add_test_users_handler)
app.router.add_get('/api/elite_partners', api_elite_partners_handler)
app.router.add_get('/api/contact_profile', api_contact_profile_handler)
app.router.add_get('/api/favorite_contacts', api_favorite_contacts_handler)
app.router.add_post('/api/favorite_contacts', api_favorite_contacts_handler)
app.router.add_get('/api/blocked_contacts', api_blocked_contacts_handler)
app.router.add_post('/api/blocked_contacts', api_blocked_contacts_handler)
app.router.add_get('/api/avatar/{telegram_id}', api_avatar_handler)
app.router.add_post('/api/rate_user', rate_user_handler)
app.router.add_get('/api/get_user_rating', get_user_rating_handler)
app.router.add_post('/api/set_user_rating', set_user_rating_handler)
app.router.add_post('/api/posts', create_post_handler)
app.router.add_post('/api/update_profile', api_update_profile_handler)
app.router.add_post('/api/accept_delegated_task', api_accept_delegated_task_handler)
app.router.add_post('/api/reject_delegated_task', api_reject_delegated_task_handler)
app.router.add_post('/api/cancel_delegation', cancel_delegation_handler)
app.router.add_get('/api/feed', get_feed_handler)
app.router.add_post('/api/feed/mark-viewed', mark_posts_viewed_handler)
app.router.add_delete('/api/posts/{post_id}', delete_post_handler)
app.router.add_post('/api/comments', create_comment_handler)
app.router.add_get('/api/comments/{post_id}', get_comments_handler)
app.router.add_delete('/api/comments/{comment_id}', delete_comment_handler)
app.router.add_post('/api/posts/{post_id}/like', toggle_like_handler)
app.router.add_post('/api/hide_contact', hide_contact_handler)
app.router.add_get('/api/profile', api_profile_handler)
app.router.add_post('/api/profile', api_profile_handler)
app.router.add_get('/api/reminders', api_reminders_handler)
app.router.add_get('/api/delegations', api_delegations_handler)
app.router.add_get('/api/interactions', api_interactions_handler)
app.router.add_get('/api/search_contacts', api_search_contacts_handler)


# Setup for production
# dp = Dispatcher()

# Include router from handlers
# dp.include_router(handlers_router)

# Session storage will be initialized in on_startup handler

# Initialize ReminderService
import reminder_service as reminder_service_module
reminder_service = ReminderService(bot=bot if not LOCAL else None)
reminder_service_module.REMINDER_SERVICE = reminder_service  # Set global variable for use in handlers
logger.info("ReminderService initialized and set as global REMINDER_SERVICE")

#      
try:
    from ai_integration.utils import preload_common_data
    logger.info("Starting preload of weather/news cache...")
    preload_common_data()
    logger.info("Cache preload completed")
except Exception as e:
    logger.warning(f"Cache preload failed: {e}")

# Start ReminderService on app startup


async def ensure_database_schema(app):
    """Ensure database schema is up to date with migrations"""
    logger.info("Checking database schema...")
    try:
        from sqlalchemy import inspect as sql_inspect, text as sql_text, create_engine as sql_engine
        
        engine = sql_engine(DATABASE_URL)
        inspector = sql_inspect(engine)
        
        # Check if tasks table exists
        if 'tasks' not in inspector.get_table_names():
            logger.info("Tasks table doesn't exist yet, skipping migration")
            return
        
        # Check if pending_delegator_report column exists
        columns = [col['name'] for col in inspector.get_columns('tasks')]
        
        if 'pending_delegator_report' not in columns:
            logger.info("Adding pending_delegator_report column to tasks table...")
            with engine.connect() as conn:
                conn.execute(sql_text("""
                    ALTER TABLE tasks 
                    ADD COLUMN pending_delegator_report BIGINT
                """))
                conn.commit()
            logger.info("✅ Successfully added pending_delegator_report column")
        else:
            logger.info("✅ pending_delegator_report column already exists")
            
        # Check if goal_id column exists
        if 'goal_id' not in columns:
            logger.info("Adding goal_id column to tasks table...")
            with engine.connect() as conn:
                conn.execute(sql_text("""
                    ALTER TABLE tasks 
                    ADD COLUMN goal_id INTEGER REFERENCES goals(id)
                """))
                conn.commit()
            logger.info("✅ Successfully added goal_id column")
        else:
            logger.info("✅ goal_id column already exists")
            
    except Exception as e:
        logger.error(f"Error during database schema check: {e}")


async def start_reminder_service(app):
    logger.info("Starting ReminderService...")
    await reminder_service.start()
    logger.info("ReminderService started successfully")

    # Пинг IndexNow при старте — уведомляем поисковики о всех страницах
    try:
        await notify_indexnow([
            "https://asibiont.ru/",
            "https://asibiont.ru/subscription-tiers",
            "https://asibiont.ru/dashboard"
        ])
        logger.info("[IndexNow] Pinged search engines about all pages")
    except Exception as e:
        logger.warning(f"[IndexNow] Startup ping failed: {e}")

    # Log existing jobs
    jobs = reminder_service.scheduler.get_jobs()
    logger.info(f"Scheduled jobs after start: {len(jobs)}")
    for job in jobs[:5]:  # Log first 5 jobs
        logger.info(f"Job: {job.id} at {job.next_run_time}")


app.on_startup.append(ensure_database_schema)  # Run migrations first
app.on_startup.append(start_reminder_service)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if bot:
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    logger.info("Bot created with webhook setup for production mode")
else:
    logger.warning("Bot not created or local mode, skipping webhook setup")

logger.info("App created successfully")

if __name__ == "__main__":
    from config import LOCAL

    # Production mode or local web mode: run web server
    try:
        port = PORT
        host = '0.0.0.0'
        mode = "LOCAL" if LOCAL else "PRODUCTION"
        logger.info(f"Starting web server in {mode} mode on {host}:{port}")

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

                # Start auto-post service as background task
                auto_post_task = None
                if not LOCAL:  # Only in production
                    try:
                        from auto_post_service import run_service as run_auto_post_service
                        logger.info("Starting auto-post service in background...")
                        auto_post_task = asyncio.create_task(run_auto_post_service())
                        logger.info("Auto-post service task created")
                    except Exception as e:
                        logger.error(f"Failed to start auto-post service: {e}")

                # Start auto-marketing service for Premium users (every 30 minutes)
                auto_marketing_task = None
                try:
                    from auto_marketing_service import start_marketing_service
                    logger.info("Starting auto-marketing service in background...")
                    auto_marketing_task = asyncio.create_task(start_marketing_service(bot, check_interval_minutes=30))
                    logger.info("Auto-marketing service task created")
                except Exception as e:
                    logger.error(f"Failed to start auto-marketing service: {e}")

                # Start contact alerts service for Premium users (every 30 minutes)
                contact_alerts_task = None
                try:
                    from contact_alerts_service import start_contact_alerts_service
                    logger.info("Starting contact alerts service in background...")
                    contact_alerts_task = asyncio.create_task(start_contact_alerts_service(bot, check_interval_minutes=30))
                    logger.info("Contact alerts service task created")
                except Exception as e:
                    logger.error(f"Failed to start contact alerts service: {e}")

                # Start polling for bot ONLY in local mode
                polling_task = None
                if LOCAL and bot and dp:
                    logger.info("Starting Telegram bot polling for local mode")
                    await bot.delete_webhook()  # Delete webhook before polling
                    polling_task = asyncio.create_task(dp.start_polling(bot))
                else:
                    logger.info("Production mode: Using webhooks instead of polling")
                
                # Keep the server running
                try:
                    if polling_task:
                        # Don't await polling_task to avoid blocking server
                        # Just keep server running indefinitely
                        while True:
                            await asyncio.sleep(3600)
                    else:
                        # Keep server running indefinitely in production
                        while True:
                            await asyncio.sleep(3600)
                except KeyboardInterrupt:
                    logger.info("Shutting down server...")
                except Exception as e:
                    logger.error(f"Server interrupted: {e}")
                finally:
                    # Cancel background tasks
                    if auto_post_task and not auto_post_task.done():
                        logger.info("Cancelling auto-post service...")
                        auto_post_task.cancel()
                    if auto_marketing_task and not auto_marketing_task.done():
                        logger.info("Cancelling auto-marketing service...")
                        auto_marketing_task.cancel()
                    if contact_alerts_task and not contact_alerts_task.done():
                        logger.info("Cancelling contact alerts service...")
                        contact_alerts_task.cancel()
                    await runner.cleanup()
                    logger.info("Server shut down")

            asyncio.run(run_server())
        except Exception as serve_error:
            logger.error(f"Error in asyncio run: {serve_error}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Failed to start application: {e}", exc_info=True)
        raise
