from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction, UserRating, PaymentHistory, Post, PostLike, Comment, PostView, Goal, Note, PushSubscription, EmailCampaign, EmailOutreach, EmailContact, AgentActivityLog, TokenTransaction, init_db
from reminder_service import ReminderService
from auto_post_service import run_service as auto_post_run_service
from ai_integration import chat_with_ai, get_partners_list, decrypt_data, encrypt_data
from datetime import datetime, timedelta, timezone as dt_timezone
from config import TELEGRAM_TOKEN, TELEGRAM_BOT_USERNAME, PORT, CURRENT_DATE, DATABASE_URL, LOCAL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL, NOWPAYMENTS_API_KEY, NOWPAYMENTS_IPN_SECRET, WEBHOOK_SECRET, SENTRY_DSN, ADMIN_TELEGRAM_USERNAME
from aiohttp_session.cookie_storage import EncryptedCookieStorage
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

# Sentry error tracking (optional)
try:
    import sentry_sdk as _sentry_sdk
    if SENTRY_DSN:
        _sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=0.05,
            environment='production' if not LOCAL else 'development',
        )
        logging.getLogger(__name__).info("✅ Sentry initialized")
except ImportError:
    pass

# Скрываем некритичные предупреждения
warnings.filterwarnings('ignore', message='Couldn\'t find ffmpeg or avconv')


def normalize_city(city):
    """Normalize city names for comparison (bidirectional RU ↔ EN)"""
    if not city:
        return None
    city = city.lower().strip()
    # RU → EN mapping
    city_map = {
        'москва': 'moscow',
        'санкт-петербург': 'saint petersburg',
        'петербург': 'saint petersburg',
        'спб': 'saint petersburg',
        'екатеринбург': 'yekaterinburg',
        'новосибирск': 'novosibirsk',
        'казань': 'kazan',
        'пермь': 'perm',
        'нижний новгород': 'nizhny novgorod',
        'самара': 'samara',
        'омск': 'omsk',
        'красноярск': 'krasnoyarsk',
        'уфа': 'ufa',
        'ростов-на-дону': 'rostov-on-don',
        'челябинск': 'chelyabinsk',
        'воронеж': 'voronezh',
        'краснодар': 'krasnodar',
        'саратов': 'saratov',
        'тюмень': 'tyumen',
        'тольятти': 'togliatti',
        'ижевск': 'izhevsk',
        'барнаул': 'barnaul',
        'томск': 'tomsk',
        'рязань': 'ryazan',
        'тула': 'tula',
        'ярославль': 'yaroslavl',
        'иркутск': 'irkutsk',
        'волгоград': 'volgograd',
        'хабаровск': 'khabarovsk',
        'владивосток': 'vladivostok',
        'астрахань': 'astrakhan',
        'сочи': 'sochi',
        'белгород': 'belgorod',
        'киров': 'kirov',
        'липецк': 'lipetsk',
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

# Clear database if requested (LOCAL only — safety guard)
if os.getenv('CLEAR_DB') == '1':
    if LOCAL:
        logger.warning("CLEAR_DB=1 detected (LOCAL mode), clearing all database data...")
        try:
            Base.metadata.drop_all(engine)
            logger.warning("All tables dropped successfully")
        except Exception as e:
            logger.error(f"Error dropping tables: {e}")
    else:
        logger.error("❌ CLEAR_DB=1 IGNORED — not allowed in production! Remove this env var.")

# Initialize database tables
init_db()

# Run database migrations (extracted to migrations.py)
from migrations import run_migrations
run_migrations()

# Seed arena test agents
try:
    from ai_integration.agent_arena import seed_test_agents, start_global_arena
    seed_test_agents()
except Exception as _arena_init_err:
    import logging as _l; _l.getLogger(__name__).warning(f'Arena init: {_arena_init_err}')

# Database connection already verified above

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
                # Add reminder as a synthetic user-ai pair for context continuity
                context.append({
                    'user': '[напоминание]',
                    'agent': msg.content
                })
                i += 1
            
            # Proactive messages from the bot — include so AI knows what it already sent
            elif msg.message_type == 'proactive':
                context.append({
                    'user': '[проактивное сообщение]',
                    'agent': msg.content
                })
                i += 1
            
            # Reminder messages — include for full context
            elif msg.message_type == 'reminder':
                context.append({
                    'user': '[напоминание о задаче]',
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
        'Ulyanovsk': 'Ульяновск',
        'Irkutsk': 'Иркутск',
        'Khabarovsk': 'Хабаровск',
        'Vladivostok': 'Владивосток',
        'Yaroslavl': 'Ярославль',
        'Vladimir': 'Владимир',
        'Ivanovo': 'Иваново',
        'Bryansk': 'Брянск',
        'Smolensk': 'Смоленск',
        'Kaluga': 'Калуга',
        'Tula': 'Тула',
        'Ryazan': 'Рязань',
        'Moscow Oblast': 'Московская область',
        'Leningrad Oblast': 'Ленинградская область'
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
    """Получает URL аватара пользователя из Telegram (или кэша для Discord-юзеров)
    
    Args:
        bot: Telegram bot instance
        user_id: Telegram user ID (negative for Discord-only users)
        force_refresh: If True, always fetch fresh avatar from Telegram API, bypassing cache
    """
    try:
        from models import User
        db = Session()
        try:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            
            # Discord-only users (negative telegram_id): return cached photo_url
            if user_id < 0:
                if user and user.photo_url:
                    return user.photo_url
                return None
            
            # Если не требуется принудительное обновление и есть кэшированный аватар, возвращаем его
            if not force_refresh and user and user.photo_url:
                logger.debug(f"Returning cached avatar for user {user_id}")
                return user.photo_url
            
            # Загружаем свежий аватар из Telegram
            if bot:
                try:
                    photos = await bot.get_user_profile_photos(user_id, limit=1)
                    if photos.total_count > 0:
                        file = await bot.get_file(photos.photos[0][-1].file_id)
                        avatar_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
                        
                        # Сохраняем в БД для кэширования
                        if user:
                            user.photo_url = avatar_url
                            db.commit()
                            logger.info(f"Updated avatar for user {user_id} (force_refresh={force_refresh})")
                        
                        return avatar_url
                except Exception as e:
                    logger.debug(f"Could not fetch avatar from Telegram for user {user_id}: {e}")
            
            # Fallback: если Telegram API не вернул фото — используем кешированный photo_url из БД
            if user and user.photo_url:
                logger.debug(f"Using cached photo_url for user {user_id} after failed force_refresh")
                return user.photo_url

            logger.debug(f"No avatar available for user {user_id}")
            return None
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error getting avatar for user {user_id}: {e}")
        return None


def safe_avatar_url(telegram_id):
    """Return safe proxied avatar URL (without bot token) for API responses."""
    if telegram_id:
        return f"/api/avatar/{telegram_id}"
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


# ═══ Password helpers for email auth ═══
import base64

def hash_password(password):
    """Hash password with PBKDF2-SHA256 + random salt"""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + key).decode('utf-8')

def verify_password(password, stored_hash):
    """Verify password against stored PBKDF2 hash"""
    try:
        decoded = base64.b64decode(stored_hash)
        salt = decoded[:16]
        stored_key = decoded[16:]
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return key == stored_key
    except Exception:
        return False


# ═══ Email sending (Resend HTTP API primary, SMTP fallback) ═══

async def send_email(to: str, subject: str, body: str):
    """Send email via Resend HTTP API (primary) or SMTP (fallback)"""
    from config import RESEND_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    
    # Build HTML body
    html_body = body.replace('\n', '<br>')
    html = f"""<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 14px; color: #374151; line-height: 1.6;">
<div style="max-width: 500px; margin: 0 auto; padding: 24px;">
{html_body}
</div>
</body></html>"""
    
    errors = []
    
    # Method 1: Resend HTTP API (works on Railway, no SMTP ports needed)
    if RESEND_API_KEY:
        try:
            logger.info(f"Sending email via Resend API to {to}")
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    'https://api.resend.com/emails',
                    headers={
                        'Authorization': f'Bearer {RESEND_API_KEY}',
                        'Content-Type': 'application/json'
                    },
                    json={
                        'from': 'ASI Biont <support@asibiont.com>',
                        'to': [to],
                        'subject': subject,
                        'text': body,
                        'html': html,
                    },
                    timeout=aiohttp.ClientTimeout(total=15)
                )
                resp_data = await resp.json()
                if resp.status in (200, 201):
                    logger.info(f"Email sent via Resend API to {to}: {resp_data.get('id', 'ok')}")
                    return
                else:
                    err = resp_data.get('message', resp_data.get('error', str(resp_data)))
                    errors.append(f"Resend API {resp.status}: {err}")
                    logger.warning(f"Resend API failed: {resp.status} {err}")
        except Exception as e:
            errors.append(f"Resend API: {e}")
            logger.warning(f"Resend API error: {e}")
    
    # Method 2: SMTP fallback (works when ports aren't blocked)
    if SMTP_PASSWORD:
        import smtplib, ssl, socket
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        password = SMTP_PASSWORD.replace(' ', '')
        
        # Resolve to IPv4
        try:
            infos = socket.getaddrinfo(SMTP_HOST, None, socket.AF_INET)
            host_ip = infos[0][4][0] if infos else SMTP_HOST
        except:
            host_ip = SMTP_HOST
        
        def _smtp_send():
            msg = MIMEMultipart('alternative')
            msg['From'] = SMTP_FROM
            msg['To'] = to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html, 'html', 'utf-8'))
            msg_string = msg.as_string()
            
            smtp_errors = []
            for port, use_ssl in [(587, False), (465, True)]:
                try:
                    logger.info(f"SMTP attempt: {SMTP_HOST}({host_ip}):{port}")
                    if use_ssl:
                        with smtplib.SMTP_SSL(host_ip, port, timeout=10) as s:
                            s.ehlo(SMTP_HOST)
                            s.login(SMTP_USER, password)
                            s.sendmail(SMTP_USER, to, msg_string)
                            return
                    else:
                        ctx = ssl.create_default_context()
                        with smtplib.SMTP(host_ip, port, timeout=10) as s:
                            s.ehlo(SMTP_HOST)
                            s.starttls(context=ctx)
                            s.ehlo(SMTP_HOST)
                            s.login(SMTP_USER, password)
                            s.sendmail(SMTP_USER, to, msg_string)
                            return
                except Exception as e:
                    smtp_errors.append(f"{port}: {e}")
                    logger.warning(f"SMTP {port} failed: {e}")
            raise RuntimeError('; '.join(smtp_errors))
        
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _smtp_send)
            logger.info(f"Email sent via SMTP to {to}")
            return
        except Exception as e:
            errors.append(f"SMTP: {e}")
    
    if not RESEND_API_KEY and not SMTP_PASSWORD:
        raise RuntimeError("Email not configured: set RESEND_API_KEY or SMTP_PASSWORD")
    
    raise RuntimeError(f"All email methods failed: {'; '.join(errors)}")


async def health_handler(request):
    """Health check endpoint for Railway"""
    return web.Response(text='OK', status=200)

async def smtp_check_handler(request):
    """Diagnostic: check email config"""
    from config import SMTP_HOST, SMTP_USER, SMTP_FROM, SMTP_PASSWORD, RESEND_API_KEY
    return web.json_response({
        'resend_configured': bool(RESEND_API_KEY),
        'resend_key_prefix': RESEND_API_KEY[:8] + '...' if RESEND_API_KEY else None,
        'smtp_host': SMTP_HOST,
        'smtp_user': SMTP_USER,
        'smtp_from': SMTP_FROM,
        'smtp_password_set': bool(SMTP_PASSWORD),
        'note': 'Railway blocks SMTP ports, use RESEND_API_KEY for email delivery'
    })


# ═══ IndexNow: мгновенное уведомление поисковиков ═══
INDEXNOW_KEY = 'd6193b04262141bba808b1279123715b'

async def notify_indexnow(urls: list):
    """Отправить URL-ы в IndexNow для мгновенной индексации Bing/Yandex"""
    if LOCAL:
        return
    try:
        import aiohttp
        payload = {
            "host": "asibiont.com",
            "key": INDEXNOW_KEY,
            "keyLocation": f"https://asibiont.com/{INDEXNOW_KEY}.txt",
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
    # (но не если он пришёл привязать Telegram к Discord-аккаунту)
    if user_id and request.query.get('link_tg') != '1':
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
        'lang': request.match_info.get('lang', 'ru') if hasattr(request, 'match_info') and 'lang' in request.match_info else 'ru',
        'subscription_tier': 'Токены',
        'current_date': '',
        'current_time': '',
        'formatted_end_date': None,
        'timestamp': int(time.time()),
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

            # Check if current session belongs to a Discord-only user → link TG to their account
            try:
                existing_session = await get_session(request)
                existing_uid = existing_session.get('user_id')
                if existing_uid and int(existing_uid) < 0:
                    # Discord-only user is linking Telegram
                    link_db = Session()
                    try:
                        discord_user = link_db.query(User).filter_by(telegram_id=int(existing_uid)).first()
                        if discord_user:
                            # Check if TG user already exists separately
                            tg_user = link_db.query(User).filter_by(telegram_id=user_id).first()
                            if tg_user and tg_user.id != discord_user.id:
                                # Merge: move Discord data → TG account
                                tg_user.discord_id = discord_user.discord_id
                                # Move interactions
                                link_db.query(Interaction).filter_by(user_id=discord_user.id).update(
                                    {Interaction.user_id: tg_user.id}, synchronize_session=False
                                )
                                # Transfer tokens
                                if discord_user.token_balance and discord_user.token_balance > 0:
                                    tg_user.token_balance = (tg_user.token_balance or 0) + discord_user.token_balance
                                link_db.delete(discord_user)
                                link_db.commit()
                                logger.info(f"Merged Discord account (id={discord_user.id}) into TG account (id={tg_user.id})")
                            else:
                                # No separate TG account — upgrade Discord account to TG
                                discord_user.telegram_id = user_id
                                discord_user.username = data.get('username') or discord_user.username
                                discord_user.first_name = data.get('first_name') or discord_user.first_name
                                discord_user.platform = 'telegram'
                                # Update avatar from TG
                                if 'bot' in request.app:
                                    try:
                                        av = await get_user_avatar_url(request.app['bot'], user_id, force_refresh=True)
                                        if av:
                                            discord_user.photo_url = av
                                    except Exception:
                                        pass
                                link_db.commit()
                                logger.info(f"Linked TG {user_id} to Discord user (id={discord_user.id})")
                            existing_session['user_id'] = user_id
                            return web.HTTPFound('/dashboard')
                    finally:
                        link_db.close()
            except Exception as e:
                logger.warning(f"Error checking existing session for linking: {e}")

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

                    # Начисляем бесплатные токены при регистрации
                    try:
                        from token_service import grant_signup_tokens
                        grant_signup_tokens(user_id, session=session_db)
                        logger.info(f"Granted signup tokens to new user {user_id}")
                    except Exception as e:
                        logger.error(f"Error granting signup tokens to {user_id}: {e}")

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

            next_url = session.pop('next_url', None)
            redirect_to = next_url if next_url else '/dashboard'
            response = web.HTTPFound(redirect_to)
            logger.info(f"Redirecting to {redirect_to} after auth")
            return response
        else:
            logger.error(f"Authentication failed for data: {data}")
            return web.Response(text='Authentication failed', status=401)
    except Exception as e:
        logger.error(f"CRITICAL ERROR in auth_handler: {e}", exc_info=True)
        return web.Response(text='Internal server error', status=500)


# ═══ Email registration and login ═══

async def email_register_handler(request):
    """Register a new user with email + password"""
    try:
        data = await request.json()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password', '')

        if not email or '@' not in email or '.' not in email:
            return web.json_response({'error': 'Некорректный email'}, status=400)
        if len(password) < 6:
            return web.json_response({'error': 'Пароль минимум 6 символов'}, status=400)

        session_db = Session()
        try:
            existing = session_db.query(User).filter_by(email=email).first()
            if existing:
                return web.json_response({'error': 'Email уже зарегистрирован'}, status=409)

            # Generate unique negative telegram_id for email-only users
            import random
            while True:
                fake_tg_id = -random.randint(10**14, 10**15)
                if not session_db.query(User).filter_by(telegram_id=fake_tg_id).first():
                    break

            # Detect timezone from IP
            ip_address = request.headers.get('X-Forwarded-For', request.remote or '').split(',')[0].strip()
            tz, city = await get_timezone_from_ip(ip_address)

            user = User(
                telegram_id=fake_tg_id,
                email=email,
                password_hash=hash_password(password),
                first_name=email.split('@')[0],
                platform='web',
                timezone=tz,
            )
            session_db.add(user)
            session_db.commit()

            # Create profile with detected city
            if city:
                profile = UserProfile(user_id=user.id, city=city)
                session_db.add(profile)
                session_db.commit()

            # Grant signup tokens
            try:
                from token_service import grant_signup_tokens
                grant_signup_tokens(fake_tg_id, session=session_db)
            except Exception as e:
                logger.warning(f"Failed to grant signup tokens for email user: {e}")

            # Log in immediately
            session = await get_session(request)
            session['user_id'] = fake_tg_id
            logger.info(f"Email registration successful: {email}, user_id={user.id}")

            return web.json_response({'success': True, 'redirect': '/dashboard'})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in email_register_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def email_login_handler(request):
    """Login with email + password"""
    try:
        data = await request.json()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password', '')

        if not email or not password:
            return web.json_response({'error': 'Укажите email и пароль'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(email=email).first()
            if not user or not user.password_hash:
                return web.json_response({'error': 'Неверный email или пароль'}, status=401)

            if not verify_password(password, user.password_hash):
                return web.json_response({'error': 'Неверный email или пароль'}, status=401)

            session = await get_session(request)
            session['user_id'] = user.telegram_id
            logger.info(f"Email login successful: {email}")

            return web.json_response({'success': True, 'redirect': '/dashboard'})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in email_login_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def password_reset_handler(request):
    """Reset password — generate new random password and send it to email"""
    try:
        data = await request.json()
        email = (data.get('email') or '').strip().lower()
        if not email or '@' not in email:
            return web.json_response({'error': 'Укажите корректный email'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(email=email).first()
            if not user:
                # Don't reveal whether email exists — always say "sent"
                return web.json_response({'success': True, 'message': 'Если аккаунт с таким email существует, новый пароль будет отправлен на почту.'})

            import secrets
            import string
            alphabet = string.ascii_letters + string.digits
            new_password = ''.join(secrets.choice(alphabet) for _ in range(10))

            # Send email FIRST, only save password if email succeeds
            try:
                await send_email(
                    to=email,
                    subject='Сброс пароля — ASI Biont',
                    body=f"""Здравствуйте!

Вы запросили сброс пароля для аккаунта ASI Biont.

Ваш новый пароль: {new_password}

Рекомендуем сменить пароль в настройках профиля после входа.

Если вы не запрашивали сброс пароля, проигнорируйте это письмо.

— ASI Biont
https://asibiont.com"""
                )
                logger.info(f"Password reset email sent to {email}")
            except Exception as mail_err:
                logger.error(f"Failed to send password reset email to {email}: {mail_err}")
                return web.json_response({
                    'error': 'Не удалось отправить письмо. Попробуйте позже или обратитесь в поддержку.'
                }, status=500)

            # Email sent successfully — now save the new password
            user.password_hash = hash_password(new_password)
            session_db.commit()
            logger.info(f"Password reset for email: {email}")

            return web.json_response({
                'success': True,
                'message': 'Новый пароль отправлен на вашу почту'
            })
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in password_reset_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def delete_account_handler(request):
    """DELETE /api/account/delete — permanently delete current user and all their data"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        from models import (
            Task, Interaction, Note, UserProfile, Goal, UserRating,
            Subscription, PaymentHistory, Post, PostLike, Comment, PostView,
            ActivityAlert, ContactAlert, Anchor, AnchorDeliveryLog,
            PushSubscription, TokenTransaction, EmailContact, EmailCampaign,
            EmailOutreach, AgentActivityLog
        )
        # UserMessage may not exist in all deployments
        try:
            from models import UserMessage
            has_user_message = True
        except ImportError:
            has_user_message = False

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            uid = user.id

            # Delete child records in dependency order (leaves first)
            session_db.query(AnchorDeliveryLog).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(PostLike).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(PostView).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Comment).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Post).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(EmailOutreach).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(EmailCampaign).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(EmailContact).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(AgentActivityLog).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(PushSubscription).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(TokenTransaction).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(PaymentHistory).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Subscription).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(UserRating).filter(
                (UserRating.rater_user_id == uid) | (UserRating.rated_user_id == uid)
            ).delete(synchronize_session=False)
            session_db.query(ActivityAlert).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(ContactAlert).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Anchor).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Goal).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Task).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Interaction).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(Note).filter_by(user_id=uid).delete(synchronize_session=False)
            session_db.query(UserProfile).filter_by(user_id=uid).delete(synchronize_session=False)
            if has_user_message:
                session_db.query(UserMessage).filter(
                    (UserMessage.sender_id == uid) | (UserMessage.recipient_id == uid)
                ).delete(synchronize_session=False)
            session_db.delete(user)
            session_db.commit()

            logger.info(f"[DELETE ACCOUNT] User {user_id} (db id={uid}) fully deleted")
            return web.json_response({'success': True})
        except Exception as e:
            session_db.rollback()
            logger.error(f"[DELETE ACCOUNT] DB error: {e}", exc_info=True)
            return web.json_response({'error': 'Ошибка при удалении'}, status=500)
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[DELETE ACCOUNT] Unexpected error: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def password_change_handler(request):
    """Change password for logged-in user"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')

        if not new_password or len(new_password) < 6:
            return web.json_response({'error': 'Новый пароль минимум 6 символов'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            # If user already has a password, verify current
            if user.password_hash:
                if not current_password:
                    return web.json_response({'error': 'Введите текущий пароль'}, status=400)
                if not verify_password(current_password, user.password_hash):
                    return web.json_response({'error': 'Неверный текущий пароль'}, status=401)

            user.password_hash = hash_password(new_password)
            session_db.commit()
            logger.info(f"Password changed for user_id: {user_id}")

            return web.json_response({'success': True, 'message': 'Пароль изменён'})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in password_change_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


# ═══ Push subscription ═══

async def push_subscribe_handler(request):
    """Save Web Push subscription for user"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        endpoint = data.get('endpoint')
        keys = data.get('keys', {})
        p256dh = keys.get('p256dh')
        auth = keys.get('auth')

        if not endpoint or not p256dh or not auth:
            return web.json_response({'error': 'Invalid subscription'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            # Remove old subscription with same endpoint
            session_db.query(PushSubscription).filter_by(
                user_id=user.id, endpoint=endpoint
            ).delete()

            sub = PushSubscription(
                user_id=user.id,
                endpoint=endpoint,
                keys_p256dh=p256dh,
                keys_auth=auth,
            )
            session_db.add(sub)
            session_db.commit()

            return web.json_response({'success': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in push_subscribe_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def push_vapid_key_handler(request):
    """Return VAPID public key for push subscription"""
    from config import VAPID_PUBLIC_KEY
    if not VAPID_PUBLIC_KEY:
        return web.json_response({'error': 'Push not configured'}, status=503)
    return web.json_response({'publicKey': VAPID_PUBLIC_KEY})


async def send_web_push(user_id_db, title, body, url='/dashboard'):
    """Send Web Push notification to all user's subscriptions"""
    try:
        from config import VAPID_PRIVATE_KEY, VAPID_EMAIL, VAPID_PUBLIC_KEY
        if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
            return

        from pywebpush import webpush, WebPushException
        session_db = Session()
        try:
            subs = session_db.query(PushSubscription).filter_by(user_id=user_id_db).all()
            for sub in subs:
                try:
                    webpush(
                        subscription_info={
                            'endpoint': sub.endpoint,
                            'keys': {'p256dh': sub.keys_p256dh, 'auth': sub.keys_auth}
                        },
                        data=json.dumps({'title': title, 'body': body, 'url': url}),
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims={'sub': VAPID_EMAIL}
                    )
                except WebPushException as e:
                    if '410' in str(e) or '404' in str(e):
                        session_db.delete(sub)
                        session_db.commit()
                    logger.warning(f"Push failed for sub {sub.id}: {e}")
                except Exception as e:
                    logger.warning(f"Push error for sub {sub.id}: {e}")
        finally:
            session_db.close()
    except ImportError:
        logger.debug("pywebpush not installed, skipping push notification")
    except Exception as e:
        logger.warning(f"send_web_push error: {e}")


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
                    session_db.commit()
                    logger.info(f"Subscription {subscription.id} expired, status set to 'expired'")

            # Токенная модель — синхронизация тарифов больше не нужна (все функции открыты)

            logger.info(
                f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}")

            # Токенная модель — подписка не требуется, доступ всегда открыт
            # (старый код проверки подписки удалён)

            # Query tasks: same logic as /api/tasks to avoid SSR↔API mismatch
            _task_conditions = [Task.user_id == user.id]
            if user.username:
                _uname_clean = user.username.replace('@', '')
                _task_conditions.append(or_(
                    Task.delegated_to_username.ilike(_uname_clean),
                    Task.delegated_to_username.ilike(f'@{_uname_clean}')
                ))
            _task_conditions.append(Task.delegated_by == user.id)
            tasks = session_db.query(Task).filter(or_(*_task_conditions)).all()
            # Exclude rejected/cancelled — same filter as /api/tasks
            tasks = [t for t in tasks if t.status not in ('rejected', 'cancelled') and (not hasattr(t, 'delegation_status') or t.delegation_status != 'rejected')]
            logger.info(f"Found {len(tasks)} tasks for user {user.id} (telegram_id: {user.telegram_id})")
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

            # Get user token balance for display
            display_tier = 'Токены'  # Унифицированная модель

            # Helper: pick translated field based on viewer language
            _dash_lang = user.language if user and hasattr(user, 'language') and user.language else 'ru'
            def _pick_dash(profile_obj, field_name):
                if not profile_obj:
                    return None
                original = getattr(profile_obj, field_name, None)
                if not original:
                    return None
                if _dash_lang == 'en':
                    return getattr(profile_obj, f'{field_name}_normalized', None) or original
                else:
                    return getattr(profile_obj, f'{field_name}_normalized_ru', None) or original

            # Auto-renormalize profile if translated fields are missing (background, non-blocking)
            if profile and _dash_lang:
                _needs_norm = False
                for _nf in ['city', 'country', 'company', 'position', 'goals', 'skills', 'interests']:
                    _orig = getattr(profile, _nf, None)
                    if _orig and _orig.strip():
                        _en = getattr(profile, f'{_nf}_normalized', None)
                        _ru = getattr(profile, f'{_nf}_normalized_ru', None)
                        if not _en or not _ru:
                            _needs_norm = True
                            break
                if _needs_norm:
                    _profile_id = profile.id
                    async def _bg_normalize(pid):
                        try:
                            from ai_integration.utils import normalize_profile_fields
                            _s = Session()
                            try:
                                _p = _s.query(UserProfile).filter_by(id=pid).first()
                                if _p:
                                    ok = await normalize_profile_fields(_p)
                                    if ok:
                                        _s.commit()
                                        logger.info(f"[DASHBOARD] Background normalized profile id={pid}")
                            finally:
                                _s.close()
                        except Exception as _ne:
                            logger.warning(f"[DASHBOARD] Background normalization failed: {_ne}")
                    import asyncio
                    asyncio.ensure_future(_bg_normalize(_profile_id))

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
                    Task.delegated_to_username.ilike((user.username or '').replace('@', '')),
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
                        # Определяем причину рекомендации
                        reason_parts = []
                        if hasattr(partner, 'common_interests') and partner.common_interests:
                            reason_parts.append(f"общие интересы: {partner.common_interests}")
                        if hasattr(partner, 'common_skills') and partner.common_skills:
                            reason_parts.append(f"общие навыки: {partner.common_skills}")
                        if hasattr(partner, 'common_goals') and partner.common_goals:
                            reason_parts.append(f"общие цели: {partner.common_goals}")
                        reason = ', '.join(reason_parts) if reason_parts else 'рекомендован системой'
                        
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
                            'contact_info': partner_user.username if partner_user.username else None,
                            'photo_url': safe_avatar_url(partner_user.telegram_id),
                            'city': _pick_dash(partner, 'city'),
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
                                'photo_url': safe_avatar_url(blocked_user.telegram_id),
                                'reason': 'заблокироаый контакт'
                            })
            except Exception as e:
                logger.error(f"Error getting blocked contacts: {e}")
                blocked_contacts = []

        finally:
            session_db.close()

        # Reuse all_partners from the first block (already fetched above — avoid double DB scan)
        try:
            partners = all_partners
        except NameError:
            # all_partners not defined means the first try-block failed entirely
            _fallback_session = None
            try:
                _fallback_session = Session()
                user_tmp = _fallback_session.query(User).filter_by(telegram_id=user_id).first()
                partners = get_partners_list(user_id=user_tmp.id) if user_tmp else []
            except Exception as e:
                logger.error(f"Error getting partners: {e}", exc_info=True)
                partners = []
                # Do NOT reset delegating_to_me/delegating_by_me here —
                # they may already be populated from the first try block above
            finally:
                if _fallback_session:
                    _fallback_session.close()

        # Add common interests, skills, goals and recommendation reason
        # Uses normalized (English) fields for cross-language matching with fallback to originals
        if profile and partners:
            def _get_match_set(obj, field):
                """Get set of items from normalized field, falling back to original."""
                normalized = getattr(obj, f'{field}_normalized', None)
                original = getattr(obj, field, None)
                source = normalized or original
                if source:
                    items = set()
                    for item in source.replace(';', ',').split(','):
                        item = item.strip().lower()
                        if item:
                            items.add(item)
                    return items
                return set()

            user_interests = _get_match_set(profile, 'interests')
            user_skills = _get_match_set(profile, 'skills')
            user_goals = _get_match_set(profile, 'goals')

            # Получаем список контактов, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - cross-language matching via normalized fields
                partner_interests = _get_match_set(p, 'interests')
                if partner_interests:
                    common = user_interests & partner_interests
                    if not common:
                        for ui in user_interests:
                            for pi in partner_interests:
                                if ui and pi and (ui in pi or pi in ui):
                                    common.add(pi)
                    p.common_interests = ', '.join(sorted(common)) if common else None
                else:
                    p.common_interests = None

                # Common skills - cross-language matching
                partner_skills = _get_match_set(p, 'skills')
                if partner_skills:
                    common_skills = user_skills & partner_skills
                    if not common_skills:
                        for us in user_skills:
                            for ps in partner_skills:
                                if us and ps and (us in ps or ps in us):
                                    common_skills.add(ps)
                    p.common_skills = ', '.join(sorted(common_skills)) if common_skills else None
                else:
                    p.common_skills = None

                # Common goals - cross-language matching
                partner_goals = _get_match_set(p, 'goals')
                if partner_goals:
                    common_goals = user_goals & partner_goals
                    if not common_goals:
                        for ug in user_goals:
                            for pg in partner_goals:
                                if ug and pg and (ug in pg or pg in ug):
                                    common_goals.add(pg)
                    p.common_goals = ', '.join(sorted(common_goals)) if common_goals else None
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
                def _city_vars_a(obj):
                    vs = set()
                    for attr in ('city_normalized', 'city_normalized_ru', 'city'):
                        v = (getattr(obj, attr, None) or '').strip().lower()
                        if v:
                            vs.add(v)
                    return vs
                if _city_vars_a(profile) & _city_vars_a(p):
                    reasons.append('из вашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'

        # Add photo_url to partners (safe proxy URL without bot token)
        if partners:
            session_db = Session()
            try:
                for p in partners:
                    partner_user = session_db.query(User).filter_by(id=p.user_id).first()
                    if partner_user:
                        p.photo_url = safe_avatar_url(partner_user.telegram_id)
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
            'января',
            'февраля',
            'марта',
            'апреля',
            'мая',
            'июня',
            'июля',
            'августа',
            'сентября',
            'октября',
            'ноября',
            'декабря']
        current_date = user_now.strftime('%d.%m.%Y')

        for task in tasks:
            if task.reminder_time:
                if task.reminder_time.tzinfo is None:
                    task.reminder_time = pytz.UTC.localize(task.reminder_time)
                local_reminder = task.reminder_time.astimezone(user_tz)
                task.overdue = local_reminder < user_now and task.status in ['pending', 'in_progress']
                task.reminder_time_local = local_reminder.strftime('%d.%m.%Y %H:%M')
                if task.overdue:
                    delta = user_now - local_reminder
                    total_seconds = int(delta.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    if days > 0:
                        task.overdue_value = days
                        task.overdue_unit = 'days'
                    elif hours > 0:
                        task.overdue_value = hours
                        task.overdue_unit = 'hours'
                    elif minutes > 0:
                        task.overdue_value = minutes
                        task.overdue_unit = 'minutes'
                    else:
                        task.overdue_value = 0
                        task.overdue_unit = 'just_now'
                else:
                    task.overdue_value = None
                    task.overdue_unit = None
            else:
                task.overdue = False
                task.reminder_time_local = None
                task.overdue_value = None
                task.overdue_unit = None

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

        # Convert to JSON — same schema as /api/tasks to avoid SSR↔API mismatch
        # Pre-cache delegator usernames (session may be closed, so use a fresh one)
        _delegator_cache = {}
        _deleg_ids = set(t.delegated_by for t in tasks if t.delegated_by and t.delegated_by != user.id)
        if _deleg_ids:
            _deleg_session = Session()
            try:
                for _du in _deleg_session.query(User).filter(User.id.in_(_deleg_ids)).all():
                    if _du.username:
                        _delegator_cache[_du.id] = _du.username
            finally:
                _deleg_session.close()

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
                'reminder_time': reminder_time_iso,
                'reminder_time_local': getattr(task, 'reminder_time_local', None),
                'overdue': getattr(task, 'overdue', False),
                'overdue_value': getattr(task, 'overdue_value', None),
                'overdue_unit': getattr(task, 'overdue_unit', None),
                'is_delegated': task.delegated_to_username is not None,
                'delegation_status': task.delegation_status if hasattr(task, 'delegation_status') else None,
                'delegated_to': task.delegated_to_username,
                'delegated_to_username': task.delegated_to_username,
                'delegated_by': _delegator_cache.get(task.delegated_by),
                'delegated_by_username': _delegator_cache.get(task.delegated_by),
                'delegated_by_me': task.delegated_by == user.id if task.delegated_by else False,
                'updated_at': (task.actual_completion_time.isoformat() + 'Z') if task.actual_completion_time else ((task.created_at.isoformat() + 'Z') if task.created_at else None),
            }
            tasks_dict.append(task_dict)

        # Get user avatar URL — always use safe proxy URL (no bot token)
        user_avatar_url = safe_avatar_url(user_id) if user else None

        # Refresh avatar in DB from Telegram API if bot is available
        if 'bot' in request.app and user:
            try:
                updated_avatar_url = await get_user_avatar_url(request.app['bot'], user_id, force_refresh=True)
                if updated_avatar_url and updated_avatar_url != user.photo_url:
                    avatar_session = Session()
                    try:
                        avatar_user = avatar_session.query(User).filter_by(telegram_id=user_id).first()
                        if avatar_user:
                            avatar_user.photo_url = updated_avatar_url
                            avatar_session.commit()
                            logger.info(f"Updated avatar URL for user {user_id}")
                    finally:
                        avatar_session.close()
            except Exception as e:
                logger.error(f"Error updating avatar for user {user_id}: {e}")

        # Add random parameter to prevent caching if URL exists
        if user_avatar_url:
            import random
            user_avatar_url += f"?r={random.randint(100000, 999999)}"

        logger.info(f"Rendering dashboard for user {user.id}")

        # Pre-translate profile fields for the template
        _profile_i18n = {
            'city': _pick_dash(profile, 'city'),
            'company': _pick_dash(profile, 'company'),
            'position': _pick_dash(profile, 'position'),
            'goals': _pick_dash(profile, 'goals'),
            'skills': _pick_dash(profile, 'skills'),
            'interests': _pick_dash(profile, 'interests'),
            'status_text': _pick_dash(profile, 'status_text'),
            'country': _pick_dash(profile, 'country') if profile and hasattr(profile, 'country') else None,
        }

        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': True,
            'bot_username': 'asibiont_bot',
            'tasks': tasks_dict,
            'user': user,

            'profile': profile,
            'profile_i18n': _profile_i18n,
            'lang': _dash_lang,
            'telegram_channel': user.telegram_channel if user else None,
            'discord_webhook': user.discord_webhook if user else None,
            'discord_server_name': user.discord_server_name if user and hasattr(user, 'discord_server_name') else None,
            'discord_guild_id': user.discord_guild_id if user and hasattr(user, 'discord_guild_id') else None,
            'discord_channel_id': user.discord_channel_id if user and hasattr(user, 'discord_channel_id') else None,
            'interactions': interactions,
            'partners': partners,
            'delegating_to_me': delegating_to_me,
            'delegating_by_me': delegating_by_me,
            'blocked_contacts': blocked_contacts,
            'subscription': subscription,
            'subscription_tier': display_tier,
            'token_balance': user.token_balance if user else 0,
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'pending_tasks': pending_tasks,
            'skipped_tasks': skipped_tasks,
            'current_date': current_date,
            'current_time': current_time,
            'user_timezone': user.timezone if user and user.timezone else 'UTC',
            'formatted_end_date': formatted_end_date,
            'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
            'timestamp': int(time.time()),
            'user_avatar_url': user_avatar_url,
            'referral_balance': user.referral_balance,
            'discord_linked': bool(user.discord_id) if user else False,
            'discord_username': ('@' + (user.discord_username or user.username or user.first_name or '')) if user and user.discord_id else '',
            'is_discord_user': (user.telegram_id < 0) if user else False,
            'telegram_linked': (user.telegram_id > 0) if user else False,
            'telegram_username': user.username if user and user.telegram_id > 0 else '',
            'gmail_linked': bool(getattr(user, 'google_oauth_token', None)) if user else False,
            'gmail_email': (lambda t: (json.loads(t).get('email','') if t else ''))(getattr(user,'google_oauth_token',None) or '') if user else '',
        })
    except Exception as e:
        logger.error(f"Unexpected error in dashboard_handler: {e}", exc_info=True)
        bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'asibiont_bot'
        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'bot_username': bot_user,
            'subscription_tier': 'Токены',
            'token_balance': 0,

            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': int(time.time())
        })


async def tasks_handler(request):
    return web.HTTPFound('/dashboard')


async def profile_handler(request):
    return web.HTTPFound('/dashboard')


# ═══ SSE Progress для WebChat ═══
# In-memory хранилище очередей прогресса (user_id → asyncio.Queue)
_chat_progress_queues = {}


async def chat_progress_handler(request):
    """SSE endpoint — стримит прогресс обработки сообщения на дашборд."""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )
    await response.prepare(request)

    # Создаём очередь если ещё нет
    if user_id not in _chat_progress_queues:
        _chat_progress_queues[user_id] = asyncio.Queue()

    queue = _chat_progress_queues[user_id]

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                # keepalive
                await response.write(b': keepalive\n\n')
                continue

            if msg is None:
                # Сигнал завершения
                break

            data = json.dumps(msg, ensure_ascii=False)
            await response.write(f'data: {data}\n\n'.encode('utf-8'))

            if msg.get('type') == 'done':
                break
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        # Очищаем очередь
        _chat_progress_queues.pop(user_id, None)

    return response


async def chat_handler(request):
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        logger.info(f"Chat handler called, session user_id: {user_id}")

        if not user_id:
            logger.warning("No user_id in session for chat")
            return web.json_response({'error': 'Not authenticated'}, status=401)

        data = await request.post()
        message = data.get('message', '')
        file = data.get('file')
        file_content = None
        if file:
            # Read file content
            file_content = file.file.read().decode('utf-8', errors='ignore')
            logger.info(f"File received: {file.filename}, size: {len(file_content)}")
        logger.info(f"Message received: {message}")

        # Load context from DB
        context = get_context_from_db(user_id, limit=10)

        logger.info(f"[WEB CHAT] New message from user {user_id}: '{message[:100]}...'")

        # ═══ Progress callback для SSE стриминга ═══
        # Заранее создаём очередь, если SSE ещё не подключился (race condition fix)
        if user_id not in _chat_progress_queues:
            _chat_progress_queues[user_id] = asyncio.Queue()

        async def web_progress_callback(text):
            """Отправляет прогресс в SSE очередь для дашборда."""
            queue = _chat_progress_queues.get(user_id)
            if queue:
                await queue.put({'type': 'progress', 'text': text})

        response = "Произошла ошибка при обработке запроса. Попробуйте ещё раз."
        ai_result = {'agent_info': None}
        try:
            session_db = Session()
            try:
                user = session_db.query(User).filter_by(telegram_id=user_id).first()
                # Запоминаем user.id ДО вызова AI — после него сессия может быть в состоянии expunged
                user_db_id = user.id if user else None

                # Сохраняем сообщение пользователя ДО вызова AI
                save_context_to_db(user_id, message, None)

                # Get AI response с progress_callback (таймаут 100с — UI не зависает)
                try:
                    ai_result = await asyncio.wait_for(
                        chat_with_ai(
                            message, context, user_id, file_content,
                            db_session=session_db,
                            progress_callback=web_progress_callback,
                            web_context=True
                        ),
                        timeout=100
                    )
                    response = ai_result['response']
                    logger.info("AI response: %s...", response[:100])
                except asyncio.TimeoutError:
                    logger.warning(f"[CHAT] AI timeout (>100s) for user {user_id}")
                    response = "Думаю слишком долго... Попробуй ещё раз или переформулируй запрос."
                except Exception as e:
                    logger.error(f"Error getting AI response: {e}", exc_info=True)
                    response = "Произошла ошибка при обработке запроса. Попробуйте ещё раз."

                # Save agent response to Interaction table (skip empty — agents already saved their own messages)
                if user_db_id and response and response.strip():
                    agent_response_timestamp = datetime.now(dt_timezone.utc)
                    # Wrap with __agent JSON so agent name/avatar survives page reload
                    _ai_saved_agent_info = ai_result.get('agent_info')
                    if _ai_saved_agent_info and _ai_saved_agent_info.get('name'):
                        import json as _json_chat
                        _save_content = _json_chat.dumps({
                            '__agent': {
                                'name': _ai_saved_agent_info['name'],
                                'id': _ai_saved_agent_info.get('id'),
                                'avatar_url': _ai_saved_agent_info.get('avatar_url', ''),
                            },
                            'text': response,
                        }, ensure_ascii=False)
                    else:
                        _save_content = response
                    interaction_agent = Interaction(
                        user_id=user_db_id,
                        message_type='ai',
                        content=_save_content,
                        created_at=agent_response_timestamp
                    )
                    session_db.add(interaction_agent)
                    session_db.commit()
                    logger.info("Saved AI response to database")
                elif user_db_id and not (response and response.strip()):
                    logger.debug("[CHAT] Skipping empty response save to Interaction")
            finally:
                session_db.close()
        finally:
            # Сигнализируем SSE что ответ готов — ВСЕГДА, даже при исключении
            queue = _chat_progress_queues.get(user_id)
            if queue:
                await queue.put({'type': 'done'})

        # Ответ возвращается только в веб-чат.
        # TG и Discord получают ответы только когда пользователь пишет напрямую
        # через соответствующий канал (TG-бот / Discord-бот).
        return web.json_response({'response': response, 'agent_info': ai_result.get('agent_info')})
    except Exception as e:
        logger.error(f"Unexpected error in chat_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def transcribe_handler(request):
    """POST /api/transcribe — транскрипция аудио через Groq Whisper.

    Принимает multipart/form-data с полем 'audio' (WebM/OGG/M4A/MP3).
    Возвращает {'text': '...'} или {'error': '...', 'status': ...}.
    """
    from config import GROQ_API_KEY
    try:
        session = await get_session(request)
        if not session.get('user_id'):
            return web.json_response({'error': 'Not authenticated'}, status=401)

        data = await request.post()
        audio_field = data.get('audio')
        if not audio_field:
            return web.json_response({'error': 'audio field required'}, status=400)

        audio_bytes = audio_field.file.read()
        filename = getattr(audio_field, 'filename', None) or 'audio.webm'
        if not filename or filename == 'blob':
            filename = 'audio.webm'

        if not GROQ_API_KEY:
            return web.json_response({'error': 'Транскрипция не настроена. Установите GROQ_API_KEY.'}, status=503)

        try:
            form = aiohttp.FormData()
            form.add_field('file', audio_bytes, filename=filename, content_type='audio/webm')
            form.add_field('model', 'whisper-large-v3')
            form.add_field('language', 'ru')
            form.add_field('response_format', 'json')
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    'https://api.groq.com/openai/v1/audio/transcriptions',
                    headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    body = await resp.json()
                    if resp.status == 200:
                        text = body.get('text', '').strip()
                        logger.info(f"[TRANSCRIBE] OK: '{text[:80]}'")
                        return web.json_response({'text': text})
                    logger.warning(f"[TRANSCRIBE] Groq {resp.status}: {body}")
                    return web.json_response({'error': 'Не удалось распознать речь'}, status=500)
        except Exception as e:
            logger.warning(f"[TRANSCRIBE] Groq error: {e}")
            return web.json_response({'error': 'Не удалось распознать речь'}, status=500)

    except Exception as e:
        logger.error(f"[TRANSCRIBE] Unexpected error: {e}", exc_info=True)
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
                    response = "Извините, произошла ошибка при обработке вашего запроса. Попробуйте ещё раз."
            except Exception as e:
                logger.error(f"[API_SEND_MESSAGE] Error calling AI chat: {e}", exc_info=True)
                return web.json_response({'error': 'AI service error'}, status=500)

            # Делегирование доступно всем пользователям (оплата токенами)

            # Check for duplicate message before saving
            # Save to Interaction table (user message + AI response)
            save_context_to_db(user_id, message, response)
            logger.info(f"[API_SEND_MESSAGE] Context saved to DB: user_msg='{message[:50]}...', ai_response='{response[:50]}...'")
        finally:
            session_db.close()
            logger.info(f"[API_SEND_MESSAGE] DB session closed for user {user_id}")

        logger.info(f"[API_SEND_MESSAGE] Returning success response for user {user_id}")
        return web.json_response({'response': response, 'success': True})
    except Exception as e:
        logger.error(f"Unexpected error in api_send_message_handler: {e}", exc_info=True)
        return web.json_response({
            'error': 'Внутренняя ошибка сервера. Попробуйте ещё раз.'
        }, status=500)


async def clear_history_handler(request):
    session = await get_session(request)
    user_id = session.get('user_id')
    logger.info(f"Clear history for user_id: {user_id}")
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    # Очищаем conversation_context (JSON) и обновляем history_cleared_at
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            user.conversation_context = None
            user.history_cleared_at = datetime.now(dt_timezone.utc)
            session_db.commit()
            logger.info(f"History cleared, conversation_context=None, timestamp set to {user.history_cleared_at}")
    finally:
        session_db.close()

    return web.json_response({'success': True, 'message': 'History cleared'})


async def rollback_checkpoint_handler(request):
    """Удаляет все взаимодействия начиная с указанного interaction_id (контрольная точка)"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    try:
        data = await request.json()
        interaction_id = data.get('interaction_id')
        if not interaction_id:
            return web.json_response({'error': 'interaction_id required'}, status=400)
        interaction_id = int(interaction_id)
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        deleted = session_db.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.id >= interaction_id
        ).delete(synchronize_session=False)
        session_db.commit()
        logger.info(f"Rollback checkpoint: deleted {deleted} interactions >= {interaction_id} for user {user.id}")
        from ai_integration.conversation_history import clear_conversation_history
        clear_conversation_history(user_id)
        return web.json_response({'success': True, 'deleted': deleted})
    except Exception as e:
        logger.error(f"Error in rollback_checkpoint: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        session_db.close()


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
        query_filter = [Task.user_id == user.id]
        if user.username:
            query_filter.append(Task.delegated_to_username.ilike(user.username))
        task_count = session_db.query(Task).filter(
            or_(*query_filter)
        ).count()
        logger.info(f"User {user_id} has {task_count} tasks to clear")

        # Clear user's tasks (both created by user and delegated to user)
        del_filter = [Task.user_id == user.id]
        if user.username:
            del_filter.append(Task.delegated_to_username.ilike(user.username))
        
        # Собираем ID всех задач для удаления
        tasks_to_delete = session_db.query(Task).filter(or_(*del_filter)).all()
        task_ids = [t.id for t in tasks_to_delete]
        
        if task_ids:
            # Сбрасываем current_task_id у всех пользователей, ссылающихся на эти задачи
            session_db.query(User).filter(User.current_task_id.in_(task_ids)).update(
                {User.current_task_id: None}, synchronize_session='fetch'
            )
            
            # Удаляем дочерние задачи (parent_task_id FK)
            child_ids = [c.id for c in session_db.query(Task).filter(Task.parent_task_id.in_(task_ids)).all()]
            if child_ids:
                session_db.query(User).filter(User.current_task_id.in_(child_ids)).update(
                    {User.current_task_id: None}, synchronize_session='fetch'
                )
                session_db.query(Task).filter(Task.id.in_(child_ids)).delete(synchronize_session='fetch')
            
            # Удаляем сами задачи
            session_db.query(Task).filter(Task.id.in_(task_ids)).delete(synchronize_session='fetch')
        
        session_db.commit()
        logger.info(f"User {user_id} tasks cleared successfully")
        return web.json_response({'message': 'Tasks cleared'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing user tasks: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        session_db.close()


async def clear_email_contacts_handler(request):
    """POST /clear_email_contacts — delete all email contacts for current user."""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        deleted = session_db.query(EmailContact).filter_by(user_id=user.id).delete(synchronize_session='fetch')
        session_db.commit()
        logger.info(f"User {user_id} cleared {deleted} email contacts")
        return web.json_response({'ok': True, 'deleted': deleted})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error clearing email contacts: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
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

        # Ищем задачу: ID должен совпадать И пользователь должен быть владельцем или делегатом
        ownership_conditions = [Task.user_id == user.id]
        if user.username:
            ownership_conditions.append(Task.delegated_to_username.ilike(user.username))
        
        task = session_db.query(Task).filter(
            Task.id == task_id,
            or_(*ownership_conditions)
        ).first()
        logger.info(f"Task found: {task is not None}")
        if not task:
            return web.json_response({'error': 'Task not found'}, status=404)

        # Сбрасываем current_task_id у всех пользователей, ссылающихся на эту задачу
        users_with_task = session_db.query(User).filter(User.current_task_id == task.id).all()
        for u in users_with_task:
            u.current_task_id = None
            logger.info(f"[CLEAR_SINGLE_TASK] Reset current_task_id for user {u.telegram_id}")
        
        # Удаляем дочерние задачи (рекурентные инстансы)
        child_tasks = session_db.query(Task).filter(Task.parent_task_id == task.id).all()
        for child in child_tasks:
            child_users = session_db.query(User).filter(User.current_task_id == child.id).all()
            for cu in child_users:
                cu.current_task_id = None
            session_db.delete(child)
            logger.info(f"[CLEAR_SINGLE_TASK] Deleted child task ID: {child.id}")
        
        session_db.delete(task)
        session_db.commit()
        logger.info(f"Task {task_id} deleted by user {user_id}")
        return web.json_response({'message': 'Task deleted'})
    except Exception as e:
        session_db.rollback()
        logger.error(f"Error deleting task: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
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
        
        # Проверяем успешность операции
        result_lower = result.lower() if isinstance(result, str) else str(result).lower()
        if 'не найден' in result_lower or 'ошибка' in result_lower or 'error' in result_lower or 'нет активных' in result_lower:
            logger.warning(f"[COMPLETE_TASK_HANDLER] Failed for task_id={task_id}: {result}")
            return web.json_response({'error': result}, status=404)
        
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
        
        # Очищаем внутренние маркеры перед отправкой клиенту
        clean_result = result
        for prefix in ['TASK_COMPLETED_ASK_RESULT:', 'TASK_UPDATED:', 'TASK_DELETED_ASK_REASON:']:
            if isinstance(clean_result, str) and clean_result.startswith(prefix):
                clean_result = clean_result[len(prefix):].strip() or 'Задача выполнена'
        
        return web.json_response({'message': clean_result})
    except Exception as e:
        logger.error(f"Error completing task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        
        # Проверяем успешность операции
        result_lower = result.lower() if isinstance(result, str) else str(result).lower()
        if 'не найден' in result_lower or 'ошибка' in result_lower or 'error' in result_lower or 'некорректн' in result_lower:
            logger.warning(f"Task restore failed for task_id={task_id}: {result}")
            return web.json_response({'error': result}, status=404)
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error restoring task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        
        # Проверяем успешность операции
        result_lower = result.lower() if isinstance(result, str) else str(result).lower()
        if 'не найден' in result_lower or 'ошибка' in result_lower or 'error' in result_lower or 'некорректн' in result_lower:
            logger.warning(f"Task skip failed for task_id={task_id}: {result}")
            return web.json_response({'error': result}, status=404)
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error skipping task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def edit_task_handler(request):
    """Edit task title and/or description"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    new_title = (data.get('title') or '').strip()
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)
    if not new_title:
        return web.json_response({'error': 'Title required'}, status=400)
    if len(new_title) > 255:
        return web.json_response({'error': 'Title too long'}, status=400)

    # Description is optional
    new_description = None
    if 'description' in data:
        new_description = (data.get('description') or '').strip()
        if len(new_description) > 500:
            return web.json_response({'error': 'Description too long'}, status=400)

    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        task = session_db.query(Task).filter_by(id=task_id, user_id=user.id).first()
        if not task:
            return web.json_response({'error': 'Task not found'}, status=404)
        task.title = new_title
        if new_description is not None:
            task.description = new_description if new_description else None
        session_db.commit()
        logger.info(f"[EDIT_TASK] Task {task_id} updated by user {user_id} (title + desc)")
        return web.json_response({'success': True})
    except Exception as e:
        logger.error(f"Error editing task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        session_db.close()


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
        
        # Проверяем успешность удаления
        if 'не найден' in result.lower() or 'ошибка' in result.lower():
            logger.warning(f"Task deletion failed for task_id={task_id}, user_id={user_id}: {result}")
            return web.json_response({'error': result}, status=404)
        
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
        
        # Очищаем внутренние маркеры перед отправкой клиенту
        clean_result = result
        for prefix in ['TASK_COMPLETED_ASK_RESULT:', 'TASK_UPDATED:', 'TASK_DELETED_ASK_REASON:']:
            if isinstance(clean_result, str) and clean_result.startswith(prefix):
                clean_result = clean_result[len(prefix):].strip() or 'Задача удалена'
        
        return web.json_response({'message': clean_result})
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        result = await cancel_delegation(task_id=task_id, user_id=user_id)
        logger.info(f"Delegation cancelled for task {task_id} by user {user_id}: {result}")
        
        # Проверяем успешность операции
        result_lower = result.lower() if isinstance(result, str) else str(result).lower()
        if 'не найден' in result_lower or 'ошибка' in result_lower or 'error' in result_lower or 'нельзя' in result_lower or 'не является' in result_lower or 'не делегирован' in result_lower:
            logger.warning(f"Cancel delegation failed for task_id={task_id}: {result}")
            return web.json_response({'error': result}, status=400)
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error cancelling delegation for task {task_id}: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        
        # Проверяем успешность операции
        result_lower = result.lower() if isinstance(result, str) else str(result).lower()
        if 'не найден' in result_lower or 'ошибка' in result_lower or 'error' in result_lower or 'не могу понять' in result_lower:
            logger.warning(f"Task reschedule failed for '{task_title}': {result}")
            return web.json_response({'error': result}, status=400)
        
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error rescheduling task '{task_title}': {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)












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


async def _notify_admin_error(error_msg: str, url: str = ''):
    """Send critical error notification to admin Telegram."""
    if not bot:
        return
    try:
        with Session() as _sdb:
            _admin = _sdb.query(User).filter_by(username=ADMIN_TELEGRAM_USERNAME).first()
            if _admin and _admin.telegram_id:
                _text = f"🚨 <b>Ошибка сервера</b>\n\n<code>{error_msg[:450]}</code>"
                if url:
                    _text += f"\n\n🔗 <code>{url[:200]}</code>"
                await bot.send_message(_admin.telegram_id, _text, parse_mode='HTML')
    except Exception as _ex:
        logger.warning(f"[ADMIN_NOTIFY] {_ex}")

app = web.Application(client_max_size=5 * 1024 * 1024)  # 5MB for avatar uploads

# Add bot to app
if bot:
    app['bot'] = bot
    dp = Dispatcher()
    dp.include_router(handlers_router)

# Middleware to add CSP headers and disable cache for static files


@web.middleware
async def custom_404_middleware(request, handler):
    """Return custom 404 page for unknown routes (HTML requests only)"""
    try:
        return await handler(request)
    except web.HTTPNotFound:
        # API and static paths — return plain 404
        path = request.path
        if path.startswith('/api/') or path.startswith('/static/') or path.startswith('/webhook'):
            raise
        accept = request.headers.get('Accept', '')
        if 'text/html' not in accept and accept:
            raise
        try:
            with open('templates/404.html', 'r', encoding='utf-8') as f:
                html = f.read()
            return web.Response(text=html, status=404, content_type='text/html')
        except Exception:
            raise


@web.middleware
async def session_error_middleware(request, handler):
    """Handle corrupted session cookies"""
    try:
        return await handler(request)
    except web.HTTPException:
        # Normal HTTP responses (404, 403, etc.) — pass through without logging
        raise
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


# Paths commonly probed by bots/scanners — suppress noisy 404 logs for these
_BOT_SCAN_PATHS = ('/wp-admin', '/wordpress', '/wp-login', '/xmlrpc.php',
                   '/.env', '/phpmyadmin', '/admin/config', '/setup-config')


@web.middleware
async def logging_middleware(request, handler):
    """Log all incoming requests"""
    path = request.path
    is_bot_probe = any(p in path for p in _BOT_SCAN_PATHS)
    if not is_bot_probe:
        logger.info(f"Incoming request: {request.method} {path} from {request.remote}")
    try:
        response = await handler(request)
        if not is_bot_probe:
            logger.info(f"Response: {request.method} {path} -> {response.status}")
        return response
    except web.HTTPException as e:
        if e.status == 404 and is_bot_probe:
            # Silently drop 404s for bot scanner paths
            raise
        if e.status >= 500:
            logger.error(f"HTTP {e.status} on {request.method} {path}: {e}")
        else:
            logger.debug(f"HTTP {e.status} on {request.method} {path}")
        raise
    except Exception as e:
        logger.error(f"Error handling {request.method} {path}: {e}")
        asyncio.create_task(_notify_admin_error(f"{type(e).__name__}: {e}", str(request.url)))
        raise


@web.middleware
async def redirect_to_root_middleware(request, handler):
    """Redirect www subdomain, old .ru domain, and HTTP to HTTPS"""
    host = request.host
    # Force HTTPS (Railway sets X-Forwarded-Proto)
    forwarded_proto = request.headers.get('X-Forwarded-Proto', 'https')
    if forwarded_proto == 'http' and not LOCAL:
        new_url = f"https://{host}{request.path_qs}"
        return web.HTTPMovedPermanently(new_url)
    if host.startswith('www.asibiont.com'):
        new_url = f"https://asibiont.com{request.path_qs}"
        logger.info(f"Redirecting from {host} to asibiont.com")
        return web.HTTPMovedPermanently(new_url)
    if 'asibiont.ru' in host:
        new_url = f"https://asibiont.com{request.path_qs}"
        logger.info(f"Redirecting from {host} (old .ru domain) to asibiont.com")
        return web.HTTPMovedPermanently(new_url)
    return await handler(request)


@web.middleware
async def csp_middleware(request, handler):
    response = await handler(request)
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org https://fonts.googleapis.com https://mc.yandex.ru https://mc.yandex.com https://yastatic.net https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.deepseek.com https://mc.yandex.ru https://mc.yandex.com wss://mc.yandex.ru wss://mc.yandex.com; frame-src https://oauth.telegram.org;"
    if not LOCAL:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
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
        
        origin = request.headers.get('Origin', 'http://localhost:8080')
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response
    return await handler(request)

# app.middlewares.append(security_middleware)

import time as _time_module
from collections import defaultdict

_rate_limit_store = defaultdict(list)  # ip -> [timestamp, ...]
_RATE_LIMIT_WINDOW = 60   # секунд
_RATE_LIMIT_MAX = 200     # запросов в окне (для API) — увеличено, т.к. дашборд опрашивает ~8 эндпоинтов каждые 30сек
_RATE_LIMIT_AUTH_MAX = 10  # запросов на аутентификацию


@web.middleware
async def rate_limit_middleware(request, handler):
    """Rate limiting для API endpoints"""
    path = request.path
    
    # Rate limiting только для API и аутентификации
    if not (path.startswith('/api/') or path.startswith('/webhook/') or path == '/auth'):
        return await handler(request)
    
    client_ip = request.headers.get('X-Forwarded-For', request.remote or '0.0.0.0')
    if ',' in client_ip:
        client_ip = client_ip.split(',')[0].strip()
    
    now = _time_module.time()
    key = f"{client_ip}:api"  # group all API by IP
    
    # Очищаем старые записи
    _rate_limit_store[key] = [ts for ts in _rate_limit_store[key] if now - ts < _RATE_LIMIT_WINDOW]
    
    max_requests = _RATE_LIMIT_AUTH_MAX if path == '/auth' else _RATE_LIMIT_MAX
    
    if len(_rate_limit_store[key]) >= max_requests:
        logger.warning(f"[RATE_LIMIT] {client_ip} exceeded {max_requests} req/{_RATE_LIMIT_WINDOW}s on {path}")
        return web.json_response(
            {'error': 'Too many requests. Please try again later.'},
            status=429,
            headers={'Retry-After': str(_RATE_LIMIT_WINDOW)}
        )
    
    _rate_limit_store[key].append(now)
    return await handler(request)


app.middlewares.append(rate_limit_middleware)
app.middlewares.append(cors_middleware)
app.middlewares.append(redirect_to_root_middleware)
app.middlewares.append(session_error_middleware)
app.middlewares.append(logging_middleware)
app.middlewares.append(csp_middleware)
app.middlewares.append(custom_404_middleware)

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


# ═══════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# YOOKASSA WEBHOOK IP VERIFICATION
# ═══════════════════════════════════════════════════════════════
# Официальные IP-адреса Yookassa для вебхуков
# https://yookassa.ru/developers/using-api/webhooks
YOOKASSA_ALLOWED_IPS = {
    '185.71.76.0/27', '185.71.77.0/27', '77.75.153.0/25',
    '77.75.156.11', '77.75.156.35', '77.75.154.128/25',
    '2a02:5180::/32'
}

import ipaddress

def _build_yookassa_networks():
    """Pre-build IP networks for fast checking"""
    networks = []
    for ip_str in YOOKASSA_ALLOWED_IPS:
        try:
            networks.append(ipaddress.ip_network(ip_str, strict=False))
        except ValueError:
            try:
                networks.append(ipaddress.ip_network(f"{ip_str}/32", strict=False))
            except ValueError:
                logger.warning(f"Invalid Yookassa IP: {ip_str}")
    return networks

_yookassa_networks = _build_yookassa_networks()


def is_yookassa_ip(ip_str):
    """Check if IP belongs to Yookassa"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in network for network in _yookassa_networks)
    except ValueError:
        return False


async def yookassa_webhook(request):
    # Верификация IP-адреса отправителя
    if not LOCAL:
        client_ip = request.headers.get('X-Forwarded-For', request.remote or '')
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        if not is_yookassa_ip(client_ip):
            logger.warning(f"[YOOKASSA] Rejected webhook from unauthorized IP: {client_ip}")
            return web.Response(text="Forbidden", status=403)

    data = await request.json()
    if data.get('event') == 'payment.succeeded':
        payment = data.get('object')
        if not payment or not isinstance(payment, dict):
            logger.error("[YOOKASSA] Missing or invalid 'object' in webhook payload")
            return web.Response(text="OK")
        
        metadata = payment.get('metadata') or {}
        user_id = metadata.get('user_id')
        if not user_id:
            logger.error(f"[YOOKASSA] Missing user_id in metadata: {metadata}")
            return web.Response(text="OK")
        
        tier = metadata.get('tier', 'light')  # tier or tokens_small/medium/large

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=int(user_id)).first()
            if user:
                # ═══ IDEMPOTENCY CHECK — prevent double processing on webhook retry ═══
                existing_payment = session.query(PaymentHistory).filter_by(
                    payment_id=payment['id']
                ).first()
                if existing_payment:
                    logger.warning(f"[YOOKASSA] Duplicate webhook for payment {payment['id']}, skipping")
                    return web.Response(text="OK")

                # ═══ TOKEN PACK PURCHASE ═══
                if tier.startswith('tokens_'):
                    from token_service import add_tokens, TOKEN_PACKAGES
                    from payments import TOKEN_PACK_PRICES
                    pack_key = tier.replace('tokens_', '')  # small / medium / large
                    pack_info = TOKEN_PACKAGES.get(pack_key) or TOKEN_PACK_PRICES.get(tier)
                    tokens_to_add = pack_info['tokens'] if pack_info else int(float(payment['amount']['value']))

                    result = add_tokens(int(user_id), tokens_to_add, reason='purchase', session=session)
                    logger.info(f"💰 Token purchase: user={user.username}, pack={pack_key}, tokens={tokens_to_add}, result={result}")

                    # Log to payment history
                    try:
                        payment_history = PaymentHistory(
                            user_id=user.id,
                            telegram_username=user.username,
                            action='token_purchase',
                            tier='LIGHT',  # placeholder (legacy column)
                            amount=payment['amount']['value'],
                            payment_id=payment['id'],
                            duration_days=0,
                            start_date=datetime.now(pytz.UTC),
                            end_date=datetime.now(pytz.UTC),
                            details=json.dumps({
                                'type': 'token_purchase',
                                'pack': pack_key,
                                'tokens_added': tokens_to_add,
                                'balance_after': result.get('balance', 0),
                                'payment_method': payment.get('payment_method', {}).get('type'),
                                'status': payment.get('status')
                            })
                        )
                        session.add(payment_history)
                        session.commit()
                    except Exception as e:
                        logger.error(f"❌ Failed to log token payment to history: {e}")

                    # Notify user
                    if bot:
                        try:
                            await bot.send_message(
                                int(user_id),
                                f"✅ Пополнение успешно!\n\n"
                                f"➕ Начислено: {tokens_to_add} токенов\n"
                                f"💰 Баланс: {result.get('balance', 0)} токенов\n\n"
                                f"Проверить баланс: /balance"
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify user {user_id} about token purchase: {e}")

                # Legacy tier subscription removed — only token purchases supported now

                # Handle referral commission (20% of payment → tokens to referrer)
                if user.referrer_id:
                    try:
                        referrer = session.query(User).filter_by(id=user.referrer_id).first()
                        if referrer:
                            payment_amount = float(payment['amount']['value'])
                            commission_tokens = int(payment_amount * 0.20)
                            referrer.token_balance = (referrer.token_balance or 0) + commission_tokens
                            referrer.referral_balance = (referrer.referral_balance or 0) + commission_tokens
                            session.commit()
                            logger.info(f"Referral commission: {commission_tokens} tokens added to referrer {referrer.telegram_id} from payment {payment_amount} RUB")
                            
                            if bot:
                                try:
                                    await bot.send_message(
                                        int(referrer.telegram_id),
                                        f"💰 Ваш реферал пополнил баланс! Вам начислено {commission_tokens} токенов (20% комиссия). Баланс: {referrer.token_balance} токенов."
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to notify referrer {referrer.telegram_id} about commission: {e}")
                    except Exception as e:
                        logger.error(f"Error processing referral commission: {e}")
                        session.rollback()
        except Exception as e:
            logger.error(f"Error processing yookassa webhook: {e}", exc_info=True)
            session.rollback()
        finally:
            session.close()
    return web.Response(text="OK")


async def get_user_id_from_request(request):
    """Helper function to get user_id from session or query parameters"""
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    logger.info(f"Session keys: {list(session_req.keys())}, user_id: {user_id}")
    
    # Check for telegram_id in query parameters (for local testing ONLY)
    if not user_id:
        telegram_id_param = request.query.get('telegram_id')
        if telegram_id_param and LOCAL:
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

            # Helper: pick translated field based on viewer language
            viewer_lang = request.query.get('lang') or (user.language if user and hasattr(user, 'language') and user.language else 'ru')
            if viewer_lang not in ('ru', 'en'):
                viewer_lang = 'ru'
            def _pick_field(profile_obj, field_name):
                """Return translated profile field based on viewer language."""
                if not profile_obj:
                    return None
                original = getattr(profile_obj, field_name, None)
                if not original:
                    return None
                if viewer_lang == 'en':
                    return getattr(profile_obj, f'{field_name}_normalized', None) or original
                else:
                    return getattr(profile_obj, f'{field_name}_normalized_ru', None) or original

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
                                'position': _pick_field(delegator_profile, 'position'),
                                'interests': _pick_field(delegator_profile, 'interests'),
                                'city': _pick_field(delegator_profile, 'city'),
                                'company': _pick_field(delegator_profile, 'company'),
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
                                'position': _pick_field(delegatee_profile, 'position'),
                                'interests': _pick_field(delegatee_profile, 'interests'),
                                'city': _pick_field(delegatee_profile, 'city'),
                                'company': _pick_field(delegatee_profile, 'company'),
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
        # Uses normalized (English) fields for cross-language matching with fallback to originals
        if profile and partners:
            def _get_match_set2(obj, field):
                """Get set of items from normalized field, falling back to original."""
                normalized = getattr(obj, f'{field}_normalized', None)
                original = getattr(obj, field, None)
                source = normalized or original
                if source:
                    items = set()
                    for item in source.replace(';', ',').split(','):
                        item = item.strip().lower()
                        if item:
                            items.add(item)
                    return items
                return set()

            user_interests = _get_match_set2(profile, 'interests')
            user_skills = _get_match_set2(profile, 'skills')
            user_goals = _get_match_set2(profile, 'goals')

            # Получаем список контактов, с которыми уже общались
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - cross-language matching via normalized fields
                partner_interests = _get_match_set2(p, 'interests')
                if partner_interests:
                    common = user_interests & partner_interests
                    if not common:
                        for ui in user_interests:
                            for pi in partner_interests:
                                if ui and pi and (ui in pi or pi in ui):
                                    common.add(pi)
                    p.common_interests = ', '.join(sorted(common)) if common else None
                else:
                    p.common_interests = None

                # Common skills - cross-language matching
                partner_skills = _get_match_set2(p, 'skills')
                if partner_skills:
                    common_skills = user_skills & partner_skills
                    if not common_skills:
                        for us in user_skills:
                            for ps in partner_skills:
                                if us and ps and (us in ps or ps in us):
                                    common_skills.add(ps)
                    p.common_skills = ', '.join(sorted(common_skills)) if common_skills else None
                else:
                    p.common_skills = None

                # Common goals - cross-language matching
                partner_goals = _get_match_set2(p, 'goals')
                if partner_goals:
                    common_goals = user_goals & partner_goals
                    if not common_goals:
                        for ug in user_goals:
                            for pg in partner_goals:
                                if ug and pg and (ug in pg or pg in ug):
                                    common_goals.add(pg)
                    p.common_goals = ', '.join(sorted(common_goals)) if common_goals else None
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
                _CITY_ALIASES_LOCAL = {
                    'пермь': 'perm', 'perm': 'пермь',
                    'москва': 'moscow', 'moscow': 'москва',
                    'санкт-петербург': 'saint petersburg', 'saint petersburg': 'санкт-петербург',
                    'санкт петербург': 'saint petersburg', 'питер': 'saint petersburg',
                    'екатеринбург': 'yekaterinburg', 'yekaterinburg': 'екатеринбург',
                    'новосибирск': 'novosibirsk', 'novosibirsk': 'новосибирск',
                    'казань': 'kazan', 'kazan': 'казань',
                    'нижний новгород': 'nizhny novgorod', 'nizhny novgorod': 'нижний новгород',
                    'уфа': 'ufa', 'ufa': 'уфа',
                    'самара': 'samara', 'samara': 'самара',
                    'омск': 'omsk', 'omsk': 'омск',
                    'челябинск': 'chelyabinsk', 'chelyabinsk': 'челябинск',
                    'ростов-на-дону': 'rostov-on-don', 'rostov-on-don': 'ростов-на-дону',
                    'красноярск': 'krasnoyarsk', 'krasnoyarsk': 'красноярск',
                    'воронеж': 'voronezh', 'voronezh': 'воронеж',
                    'волгоград': 'volgograd', 'volgograd': 'волгоград',
                    'краснодар': 'krasnodar', 'krasnodar': 'краснодар',
                    'саратов': 'saratov', 'saratov': 'саратов',
                    'тюмень': 'tyumen', 'tyumen': 'тюмень',
                    'тольятти': 'tolyatti', 'tolyatti': 'тольятти',
                    'ижевск': 'izhevsk', 'izhevsk': 'ижевск',
                    'барнаул': 'barnaul', 'barnaul': 'барнаул',
                    'ульяновск': 'ulyanovsk', 'ulyanovsk': 'ульяновск',
                    'хабаровск': 'khabarovsk', 'khabarovsk': 'хабаровск',
                    'оренбург': 'orenburg', 'orenburg': 'оренбург',
                    'владивосток': 'vladivostok', 'vladivostok': 'владивосток',
                    'ярославль': 'yaroslavl', 'yaroslavl': 'ярославль',
                    'пермский край': 'perm krai', 'perm krai': 'пермский край',
                }
                def _city_vars_b(obj):
                    vs = set()
                    for attr in ('city_normalized', 'city_normalized_ru', 'city'):
                        v = (getattr(obj, attr, None) or '').strip().lower()
                        if v:
                            vs.add(v)
                            alias = _CITY_ALIASES_LOCAL.get(v)
                            if alias:
                                vs.add(alias)
                    return vs
                if _city_vars_b(profile) & _city_vars_b(p):
                    reasons.append('из вашего города')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'подходящий контакт'

        # Auto-renormalize partner profiles if EN viewer and translated fields are missing
        if viewer_lang == 'en':
            _norm_count = 0
            for _p in partners:
                if _norm_count >= 10:  # Limit normalization per request to avoid timeout
                    break
                if any(getattr(_p, f, None) and not getattr(_p, f'{f}_normalized', None)
                       for f in ['city', 'company', 'position', 'interests']):
                    try:
                        from ai_integration.utils import normalize_profile_fields
                        _norm_ok = await normalize_profile_fields(_p)
                        if _norm_ok:
                            session_db.commit()
                            logger.info(f"[PARTNERS] Auto-normalized profile for user_id {getattr(_p, 'user_id', '?')}")
                            _norm_count += 1
                    except Exception as _ne:
                        logger.warning(f"[PARTNERS] Auto-normalization failed: {_ne}")

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

                # Use safe proxied avatar URL (no bot token)
                photo_url = safe_avatar_url(partner_user.telegram_id) if partner_user else None

                # Все контакты доступны всем (токенная модель)
                can_access = True

                # Add only contacts that user can access
                if partner_user and can_access:
                    # Get partner's profile for rating info
                    partner_profile = session_db.query(UserProfile).filter_by(user_id=partner_user.id).first()
                    
                    logger.info(f"Adding recommended contact {partner_user.username if partner_user else 'unknown'} for user {user.username}")
                    partners_data.append(
                        {
                            'contact_info': partner_user.username if (partner_user and partner_user.username) else None,
                            'user_id': partner_user.id if partner_user else None,
                            'telegram_id': partner_user.telegram_id if partner_user else None,
                            'photo_url': photo_url,
                            'first_name': partner_user.first_name,
                            'can_access': can_access,
                            'subscription_tier': 'tokens',  # Токенная модель, тарифы убраны
                            'city': _pick_field(p, 'city'),
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
                            'recommendation_reason': getattr(
                                p,
                                'recommendation_reason',
                                'подходящий контакт'),
                            'average_rating': partner_profile.average_rating if partner_profile else 0,
                            'rating_count': partner_profile.rating_count if partner_profile else 0,
                            'platform': partner_user.platform if partner_user else 'telegram',
                            'discord_id': str(partner_user.discord_id) if (partner_user and partner_user.discord_id) else None,
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
                    
                    # Частичное совпадение - только значимые слова (4+ символов)
                    _stop = {'для', 'как', 'что', 'это', 'все', 'они', 'его', 'были', 'или', 'при', 'так', 'уже', 'нет', 'без', 'под', 'над', 'между', 'через', 'после', 'перед', 'список', 'составить', 'сделать', 'создать'}
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(w for w in user_task.split() if len(w) >= 4 and w not in _stop)
                            if len(user_words) < 2:
                                continue
                            for delegator_task in delegator_task_titles:
                                delegator_words = set(w for w in delegator_task.split() if len(w) >= 4 and w not in _stop)
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

            # Use safe proxy URL for avatar (no bot token leak)
            photo_url = safe_avatar_url(delegator.telegram_id) if delegator and delegator.telegram_id else None
            if delegator and delegator.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id, force_refresh=True)
                    if updated_avatar and updated_avatar != delegator.photo_url:
                        delegator.photo_url = updated_avatar
                        session_db.commit()
                except Exception as e:
                    logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")

            #   \" \"     \n            #         \n            # \u0422\u0430\u0440\u0438\u0444\u044b \u0443\u0431\u0440\u0430\u043d\u044b, \u0432\u0441\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u044b \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b\n            \n            logger.info(f\"Adding delegating contact {contact['username']} for user {user.username}\")
            delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first() if delegator else None
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegator.telegram_id if delegator else None,
                'can_access': True,  # Всегда доступен
                'required_tier': None,  # Нет ограчей
                'subscription_tier': 'tokens',  # Токенная модель
                'photo_url': photo_url,
                'first_name': contact['first_name'],
                'position': _pick_field(delegator_profile, 'position'),
                'interests': _pick_field(delegator_profile, 'interests'),
                'city': _pick_field(delegator_profile, 'city'),
                'company': _pick_field(delegator_profile, 'company'),
                'common_interests': common_interests,
                'common_skills': common_skills,
                'common_goals': common_goals,
                'common_tasks': common_tasks,
                'average_rating': delegator_profile.average_rating if delegator_profile else 0,
                'rating_count': delegator_profile.rating_count if delegator_profile else 0,
                'platform': delegator.platform if delegator else 'telegram',
                'discord_id': str(delegator.discord_id) if (delegator and delegator.discord_id) else None,
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
                    
                    # Частичное совпадение - только значимые слова (4+ символов)
                    _stop = {'для', 'как', 'что', 'это', 'все', 'они', 'его', 'были', 'или', 'при', 'так', 'уже', 'нет', 'без', 'под', 'над', 'между', 'через', 'после', 'перед', 'список', 'составить', 'сделать', 'создать'}
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(w for w in user_task.split() if len(w) >= 4 and w not in _stop)
                            if len(user_words) < 2:
                                continue
                            for delegatee_task in delegatee_task_titles:
                                delegatee_words = set(w for w in delegatee_task.split() if len(w) >= 4 and w not in _stop)
                                common_words = user_words & delegatee_words
                                if len(common_words) >= 2:
                                    partial_matches.add(user_task)
                        if partial_matches:
                            common_task_titles = partial_matches
                    
                    common_tasks = ', '.join(
                        list(common_task_titles)[
                            :5]) if common_task_titles else None  # Limit to 5 common tasks

            # Use safe proxy URL for avatar (no bot token leak)
            photo_url = safe_avatar_url(delegatee.telegram_id) if delegatee and delegatee.telegram_id else None
            if delegatee and delegatee.telegram_id and 'bot' in request.app:
                try:
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id, force_refresh=True)
                    if updated_avatar and updated_avatar != delegatee.photo_url:
                        delegatee.photo_url = updated_avatar
                        session_db.commit()
                except Exception as e:
                    logger.error(f"Error updating delegatee avatar for {delegatee.telegram_id}: {e}")

            # Все контакты доступны (токенная модель)
            can_access = True

            logger.info(f"Adding delegating_by_me contact {contact['username']} for user {user.username}")
            delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first() if delegatee else None
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegatee.telegram_id if delegatee else None,
                'can_access': True,
                'photo_url': photo_url,
                'first_name': contact['first_name'],
                'position': _pick_field(delegatee_profile, 'position'),
                'interests': _pick_field(delegatee_profile, 'interests'),
                'city': _pick_field(delegatee_profile, 'city'),
                'company': _pick_field(delegatee_profile, 'company'),
                'common_interests': common_interests,
                'common_skills': common_skills,
                'common_goals': common_goals,
                'common_tasks': common_tasks,
                'average_rating': delegatee_profile.average_rating if delegatee_profile else 0,
                'rating_count': delegatee_profile.rating_count if delegatee_profile else 0,
                'platform': delegatee.platform if delegatee else 'telegram',
                'discord_id': str(delegatee.discord_id) if (delegatee and delegatee.discord_id) else None,
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

            rating = partner.get('average_rating', 0) or 0
            # Группы рейтинга:
            # 1. Высокий рейтинг (>= 5): сортируем по убыванию
            # 2. Нет рейтинга (0): нейтраль
            # 3. Низкий рейтинг (< 5): сортируем по убыванию
            if rating >= 5:
                rating_group = 0
                rating_value = -rating
            elif rating == 0:
                rating_group = 1
                rating_value = 0
            else:
                rating_group = 2
                rating_value = -rating

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

                            # Тарифы убраны — все контакты доступны
                            can_access = True
                            required_tier = None

                            # Use safe proxy URL for avatar (no bot token leak)
                            photo_url = safe_avatar_url(favorite_user.telegram_id) if favorite_user.telegram_id else None
                            if favorite_user.telegram_id and 'bot' in request.app:
                                try:
                                    updated_avatar = await get_user_avatar_url(request.app['bot'], favorite_user.telegram_id, force_refresh=True)
                                    if updated_avatar and updated_avatar != favorite_user.photo_url:
                                        favorite_user.photo_url = updated_avatar
                                        session_db.commit()
                                except Exception as e:
                                    logger.error(f"Error updating favorite avatar for {favorite_user.telegram_id}: {e}")

                            partners_data.append({
                                'contact_info': favorite_user.username,
                                'telegram_id': favorite_user.telegram_id,
                                'photo_url': photo_url,
                                'can_access': can_access,
                                'required_tier': required_tier,
                                'subscription_tier': 'tokens',  # Токенная модель
                                'first_name': favorite_user.first_name,
                                'position': _pick_field(favorite_profile, 'position'),
                                'interests': _pick_field(favorite_profile, 'interests'),
                                'city': _pick_field(favorite_profile, 'city'),
                                'company': _pick_field(favorite_profile, 'company'),
                                'common_interests': None,  # Will be calculated later if needed
                                'common_skills': None,
                                'common_goals': None,
                                'common_tasks': None,
                                'average_rating': favorite_profile.average_rating if favorite_profile else 0,
                                'rating_count': favorite_profile.rating_count if favorite_profile else 0,
                                'platform': favorite_user.platform if favorite_user else 'telegram',
                                'discord_id': str(favorite_user.discord_id) if (favorite_user and favorite_user.discord_id) else None,
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

        # Добавить my_rating — оценку текущего пользователя для каждого контакта
        my_ratings_map = {}
        try:
            my_ratings = session_db.query(UserRating).filter_by(rater_user_id=user.id).all()
            for r in my_ratings:
                rated_u = session_db.query(User).filter_by(id=r.rated_user_id).first()
                if rated_u and rated_u.username:
                    my_ratings_map[rated_u.username.replace('@', '').lower()] = r.rating
        except Exception as e:
            logger.error(f"Error fetching my ratings: {e}")

        for partner in partners_data:
            contact_info = partner.get('contact_info')
            if contact_info is None:
                contact_info = ''
            contact_username = contact_info.replace('@', '').lower()
            partner['my_rating'] = my_ratings_map.get(contact_username, None)

        # Также добавить my_rating для заблокированных контактов
        for partner in blocked_partners_data:
            contact_info = partner.get('contact_info')
            if contact_info is None:
                contact_info = ''
            contact_username = contact_info.replace('@', '').lower()
            partner['my_rating'] = my_ratings_map.get(contact_username, None)

        logger.info(f"Returning {len(partners_data)} partners for user {user_id}")
        return web.json_response({
            'partners': partners_data,
            'blocked_partners_info': blocked_partners_data,  # Добавляем информацию о заблокированных
            'my_ratings': my_ratings_map  # Карта всех оценок текущего пользователя (username → rating)
        })
    except Exception as e:
        logger.error(f"Unexpected error in api_partners_handler: {e}", exc_info=True)
        return web.json_response({'partners': []}, status=200)
    finally:
        # На случай раих ошибок закрыаем сессию, если о еще открыта
        try:
            if 'session_db' in locals():
                session_db.close()
        except Exception as e:
            logger.debug(f"Session cleanup error: {e}")


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

            # Тарифы убраны — элитные партнёры доступны всем

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

            # Получить всех пользователей (кроме себя) — тарифы убраны
            premium_users = session_db.query(User).filter(
                User.id != user.id
            ).all()
            
            logger.info(f"Found {len(premium_users)} other users for elite partners for user {user.username}")

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
                    _stop = {'для', 'как', 'что', 'это', 'все', 'они', 'его', 'были', 'или', 'при', 'так', 'уже', 'нет', 'без', 'под', 'над', 'между', 'через', 'после', 'перед', 'список', 'составить', 'сделать', 'создать'}
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(w for w in user_task.split() if len(w) >= 4 and w not in _stop)
                            if len(user_words) < 2:
                                continue
                            for premium_task in premium_task_titles:
                                premium_words = set(w for w in premium_task.split() if len(w) >= 4 and w not in _stop)
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
                    'subscription_tier': 'tokens',  # Токенная модель
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
                                    'subscription_tier': 'tokens',  # Токенная модель
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
                                'subscription_tier': 'tokens',  # Токенная модель
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
                        _stop = {'для', 'как', 'что', 'это', 'все', 'они', 'его', 'были', 'или', 'при', 'так', 'уже', 'нет', 'без', 'под', 'над', 'между', 'через', 'после', 'перед', 'список', 'составить', 'сделать', 'создать'}
                        if not common_task_titles:
                            partial_matches = set()
                            for user_task in user_task_titles:
                                user_words = set(w for w in user_task.split() if len(w) >= 4 and w not in _stop)
                                if len(user_words) < 2:
                                    continue
                                for partner_task in partner_task_titles:
                                    partner_words = set(w for w in partner_task.split() if len(w) >= 4 and w not in _stop)
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

            # Добавить my_rating — оценку текущего пользователя для каждого контакта
            my_ratings_map = {}
            try:
                my_ratings = session_db.query(UserRating).filter_by(rater_user_id=user.id).all()
                for r in my_ratings:
                    rated_u = session_db.query(User).filter_by(id=r.rated_user_id).first()
                    if rated_u and rated_u.username:
                        my_ratings_map[rated_u.username.replace('@', '').lower()] = r.rating
            except Exception as e:
                logger.error(f"Error fetching my ratings in elite: {e}")

            for partner in partners_data:
                contact_info = partner.get('contact_info')
                if contact_info is None:
                    contact_info = ''
                contact_username = contact_info.replace('@', '').lower()
                partner['my_rating'] = my_ratings_map.get(contact_username, None)

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

        username = request.query.get('username') or ''
        lookup_user_id = request.query.get('user_id')

        if not username and not lookup_user_id:
            return web.json_response({'error': 'Username or user_id required'}, status=400)

        session_db = Session()
        try:
            # Find the contact user — by username or by internal user_id
            contact_user = None
            if username:
                contact_user = session_db.query(User).filter_by(username=username).first()
            if not contact_user and lookup_user_id:
                try:
                    contact_user = session_db.query(User).filter_by(id=int(lookup_user_id)).first()
                except (ValueError, TypeError):
                    pass
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

            # Calculate common interests/skills (cross-language via normalized fields)
            common_interests = None
            if profile and current_profile:
                # Use normalized fields for cross-language matching, fallback to originals
                ci = getattr(current_profile, 'interests_normalized', None) or (current_profile.interests if current_profile.interests else None)
                pi = getattr(profile, 'interests_normalized', None) or (profile.interests if profile.interests else None)
                if ci and pi:
                    current_interests = set(i.strip().lower() for i in ci.replace(';', ',').split(',') if i.strip())
                    profile_interests = set(i.strip().lower() for i in pi.replace(';', ',').split(',') if i.strip())
                    common = current_interests & profile_interests
                    # Also partial matching
                    if not common:
                        for ciu in current_interests:
                            for piu in profile_interests:
                                if ciu and piu and (ciu in piu or piu in ciu):
                                    common.add(piu)
                    common_interests = ', '.join(sorted(common)) if common else None

            # Get active task count
            active_tasks = session_db.query(Task).filter(
                Task.user_id == contact_user.id,
                Task.status.in_(['in_progress', 'pending'])
            ).count()

            # Prepare profile data (use defaults if profile doesn't exist)
            # Translate fields based on viewer's language
            # Accept ?lang= query param from client-side language switch
            viewer_lang = request.query.get('lang') or (current_user.language if current_user and hasattr(current_user, 'language') and current_user.language else 'ru')
            if viewer_lang not in ('ru', 'en'):
                viewer_lang = 'ru'
            
            def _pick(field_name):
                """Pick translated or original field based on viewer language."""
                if not profile:
                    return None
                original = getattr(profile, field_name, None)
                if not original:
                    return None
                if viewer_lang == 'en':
                    return getattr(profile, f'{field_name}_normalized', None) or original
                else:
                    return getattr(profile, f'{field_name}_normalized_ru', None) or original

            # Auto-renormalize contact profile if translated fields are missing
            if profile:
                _needs_norm = False
                for _nf in ['city', 'country', 'company', 'position', 'goals', 'skills', 'interests']:
                    _orig = getattr(profile, _nf, None)
                    if _orig and _orig.strip():
                        _en = getattr(profile, f'{_nf}_normalized', None)
                        _ru = getattr(profile, f'{_nf}_normalized_ru', None)
                        if not _en or not _ru:
                            _needs_norm = True
                            break
                if _needs_norm:
                    try:
                        from ai_integration.utils import normalize_profile_fields
                        _norm_ok = await normalize_profile_fields(profile)
                        if _norm_ok:
                            session_db.commit()
                            logger.info(f"[CONTACT PROFILE] Auto-normalized profile for contact {contact_user.id}")
                    except Exception as _ne:
                        logger.warning(f"[CONTACT PROFILE] Auto-normalization failed: {_ne}")

            try:
                profile_data = {
                    'contact_info': contact_user.username if hasattr(contact_user, 'username') else None,
                    'first_name': getattr(contact_user, 'first_name', None),
                    'last_name': getattr(contact_user, 'last_name', None),
                    'photo_url': safe_avatar_url(contact_user.telegram_id) if hasattr(contact_user, 'telegram_id') else None,
                    'city': _pick('city'),
                    'country': _pick('country') if profile and hasattr(profile, 'country') else None,
                    'company': _pick('company'),
                    'position': _pick('position'),
                    'goals': _pick('goals'),
                    'skills': _pick('skills'),
                    'interests': _pick('interests'),
                    'languages': getattr(profile, 'languages', None) if profile else None,
                    'bio': _pick('bio'),
                    'current_plans': _pick('current_plans'),
                    'birthdate': getattr(profile, 'birthdate', None) if profile else None,
                    'zodiac_sign': getattr(profile, 'zodiac_sign', None) if profile else None,
                    'common_interests': common_interests,
                    'average_rating': getattr(profile, 'average_rating', 0) if profile else 0,
                    'status_text': _pick('status_text'),
                    'task_count': active_tasks,
                    'subscription_tier': contact_user.subscription_tier.value if hasattr(contact_user, 'subscription_tier') and contact_user.subscription_tier else 'light',
                    'telegram_channel': contact_user.telegram_channel if hasattr(contact_user, 'telegram_channel') else None,
                    'discord_webhook': True if hasattr(contact_user, 'discord_webhook') and contact_user.discord_webhook else False,
                    'discord_server_name': contact_user.discord_server_name if hasattr(contact_user, 'discord_server_name') and contact_user.discord_server_name else None,
                    'discord_guild_id': contact_user.discord_guild_id if hasattr(contact_user, 'discord_guild_id') and contact_user.discord_guild_id else None,
                    'discord_channel_id': contact_user.discord_channel_id if hasattr(contact_user, 'discord_channel_id') and contact_user.discord_channel_id else None,
                    'phone': contact_user.phone if hasattr(contact_user, 'phone') and contact_user.phone else None,
                    'platform': contact_user.platform if hasattr(contact_user, 'platform') else 'telegram',
                    'discord_id': str(contact_user.discord_id) if hasattr(contact_user, 'discord_id') and contact_user.discord_id else None
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
                    'status_text': None,
                    'task_count': 0,
                    'subscription_tier': 'light'
                }

            return web.json_response({'partner': profile_data})

        except Exception as e:
            logger.error(f"Error getting contact profile for username '{username}': {e}", exc_info=True)
            return web.json_response({'error': 'Internal server error'}, status=500)
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
                                        if bot:
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
            success_message = f'Оценка {rating}/10 для @{rated_username} сохранена'

            return web.json_response({
                'success': True,
                'message': success_message
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error rating user: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
            image_url = data.get('image_url', '').strip() or None

            if not content and not image_url:
                return web.json_response({'error': 'Post content or image is required'}, status=400)

            if len(content) > 2000:
                return web.json_response({'error': 'Post is too long (max 2000 characters)'}, status=400)

            # Validate image size (max ~2MB base64)
            if image_url and len(image_url) > 2_800_000:
                return web.json_response({'error': 'Image is too large (max 2MB)'}, status=400)

            post = Post(
                user_id=user.id,
                username=user.username,
                content=content,
                image_url=image_url
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
                    'image_url': post.image_url,
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
        return web.json_response({'error': 'Internal server error'}, status=500)


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

            # Create interaction record
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=f'Задача "{task.title}" принята и взята в работу'
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
        return web.json_response({'error': 'Internal server error'}, status=500)


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

            # Create interaction record
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=f'Задача "{task.title}" отклонена'
            )
            session_db.add(interaction)

            session_db.commit()

            return web.json_response({
                'success': True,
                'message': f'Задача "{task.title}" отклонена'
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error rejecting delegated task: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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

            # Handle status_text separately (not in update_profile)
            if 'status_text' in data:
                user = session_db.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
                    if profile:
                        profile.status_text = data['status_text'].strip()[:100] if data['status_text'] and data['status_text'].strip() else None
                        session_db.commit()

            # Normalize profile for cross-language matching
            try:
                user_obj = session_db.query(User).filter_by(telegram_id=user_id).first()
                if user_obj:
                    prof = session_db.query(UserProfile).filter_by(user_id=user_obj.id).first()
                    if prof:
                        from ai_integration.utils import normalize_profile_fields
                        normalized = await normalize_profile_fields(prof)
                        if normalized:
                            session_db.commit()
            except Exception as norm_err:
                logger.warning(f"[API UPDATE PROFILE] Normalization failed (non-critical): {norm_err}")

            return web.json_response({
                'success': True,
                'message': 'Профиль обновлён'
            })

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
                    except Exception as e:
                        logger.debug(f"Failed to parse blocked_contacts for user {profile_uid}: {e}")
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
                        'image_url': post.image_url,
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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


async def edit_post_handler(request):
    """API endpoint to edit a post"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        
        if not user_id:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        post_id = int(request.match_info['post_id'])
        data = await request.json()
        new_content = data.get('content', '').strip()
        
        if not new_content:
            return web.json_response({'error': 'Content is required'}, status=400)
        
        if len(new_content) > 2000:
            return web.json_response({'error': 'Post too long (max 2000 chars)'}, status=400)

        db_session = Session()
        
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=401)
        
        post = db_session.query(Post).filter_by(id=post_id).first()
        if not post:
            return web.json_response({'error': 'Post not found'}, status=404)
        
        if post.user_id != user.id:
            return web.json_response({'error': 'You can only edit your own posts'}, status=403)

        post.content = new_content
        db_session.commit()
        
        logger.info(f"Post {post_id} edited by user {user.username}")
        return web.json_response({'success': True, 'message': 'Post updated'})
        
    except Exception as e:
        if db_session:
            db_session.rollback()
        logger.error(f"Error editing post: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        if db_session:
            db_session.close()


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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


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
        return web.json_response({'error': 'Internal server error'}, status=500)


async def edit_comment_handler(request):
    """API endpoint to edit a comment"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        
        if not user_id:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        comment_id = int(request.match_info['comment_id'])
        data = await request.json()
        new_content = data.get('content', '').strip()
        
        if not new_content:
            return web.json_response({'error': 'Content is required'}, status=400)
        
        if len(new_content) > 1000:
            return web.json_response({'error': 'Comment too long (max 1000 chars)'}, status=400)

        db_session = Session()
        
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=401)
        
        comment = db_session.query(Comment).filter_by(id=comment_id).first()
        if not comment:
            return web.json_response({'error': 'Comment not found'}, status=404)
        
        if comment.user_id != user.id:
            return web.json_response({'error': 'You can only edit your own comments'}, status=403)

        comment.content = new_content
        db_session.commit()
        
        logger.info(f"Comment {comment_id} edited by user {user.username}")
        return web.json_response({'success': True, 'message': 'Comment updated'})
        
    except Exception as e:
        if db_session:
            db_session.rollback()
        logger.error(f"Error editing comment: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        if db_session:
            db_session.close()


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
        return web.json_response({'error': 'Internal server error'}, status=500)
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
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        if db_session:
            db_session.close()


async def translate_post_handler(request):
    """Translate a post to the specified language using DeepSeek"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        post_id = int(request.match_info['post_id'])
        data = await request.json()
        target_lang = data.get('lang', 'en')

        db_session = Session()
        post = db_session.query(Post).filter_by(id=post_id).first()
        if not post:
            return web.json_response({'error': 'Post not found'}, status=404)

        content = post.content
        if not content or len(content.strip()) < 2:
            return web.json_response({'error': 'Nothing to translate'}, status=400)

        lang_names = {
            'ru': 'Russian', 'en': 'English', 'es': 'Spanish', 'fr': 'French',
            'de': 'German', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
            'pt': 'Portuguese', 'it': 'Italian', 'ar': 'Arabic', 'hi': 'Hindi',
            'tr': 'Turkish', 'pl': 'Polish', 'uk': 'Ukrainian',
        }
        lang_name = lang_names.get(target_lang, target_lang)

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [
                        {'role': 'system', 'content': f'Translate the following text to {lang_name}. Return ONLY the translated text, nothing else. Preserve formatting and line breaks.'},
                        {'role': 'user', 'content': content},
                    ],
                    'max_tokens': 2000,
                    'temperature': 0.3,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            result = await resp.json()

        translated = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if not translated:
            return web.json_response({'error': 'Translation failed'}, status=500)

        return web.json_response({'success': True, 'translated': translated, 'lang': target_lang})

    except Exception as e:
        logger.error(f"Error translating post: {e}", exc_info=True)
        return web.json_response({'error': 'Translation error'}, status=500)
    finally:
        if db_session:
            db_session.close()


async def translate_text_handler(request):
    """Universal text translation endpoint — accepts plain text, no DB lookup needed."""
    try:
        data = await request.json()
        text = (data.get('text') or '').strip()
        target_lang = data.get('lang', 'ru')
        if not text or len(text) < 2:
            return web.json_response({'error': 'Nothing to translate'}, status=400)
        lang_names = {
            'ru': 'Russian', 'en': 'English', 'es': 'Spanish', 'fr': 'French',
            'de': 'German', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
            'pt': 'Portuguese', 'it': 'Italian', 'ar': 'Arabic', 'hi': 'Hindi',
            'tr': 'Turkish', 'pl': 'Polish', 'uk': 'Ukrainian',
        }
        lang_name = lang_names.get(target_lang, target_lang)
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                'https://api.deepseek.com/chat/completions',
                headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [
                        {'role': 'system', 'content': f'Translate the following text to {lang_name}. Return ONLY the translated text, nothing else. Preserve formatting and line breaks.'},
                        {'role': 'user', 'content': text},
                    ],
                    'max_tokens': 1000,
                    'temperature': 0.3,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            result = await resp.json()
        translated = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if not translated:
            return web.json_response({'error': 'Translation failed'}, status=500)
        return web.json_response({'success': True, 'translated': translated})
    except Exception as e:
        logger.error(f'translate_text_handler error: {e}')
        return web.json_response({'error': 'Translation error'}, status=500)


async def translate_comment_handler(request):
    """Translate a comment to the specified language using DeepSeek"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        comment_id = int(request.match_info['comment_id'])
        data = await request.json()
        target_lang = data.get('lang', 'en')

        db_session = Session()
        comment = db_session.query(Comment).filter_by(id=comment_id).first()
        if not comment:
            return web.json_response({'error': 'Comment not found'}, status=404)

        content = comment.content
        if not content or len(content.strip()) < 2:
            return web.json_response({'error': 'Nothing to translate'}, status=400)

        lang_names = {
            'ru': 'Russian', 'en': 'English', 'es': 'Spanish', 'fr': 'French',
            'de': 'German', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
            'pt': 'Portuguese', 'it': 'Italian', 'ar': 'Arabic', 'hi': 'Hindi',
            'tr': 'Turkish', 'pl': 'Polish', 'uk': 'Ukrainian',
        }
        lang_name = lang_names.get(target_lang, target_lang)

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [
                        {'role': 'system', 'content': f'Translate the following text to {lang_name}. Return ONLY the translated text, nothing else. Preserve formatting and line breaks.'},
                        {'role': 'user', 'content': content},
                    ],
                    'max_tokens': 1000,
                    'temperature': 0.3,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            result = await resp.json()

        translated = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if not translated:
            return web.json_response({'error': 'Translation failed'}, status=500)

        return web.json_response({'success': True, 'translated': translated, 'lang': target_lang})

    except Exception as e:
        logger.error(f"Error translating comment: {e}", exc_info=True)
        return web.json_response({'error': 'Translation error'}, status=500)
    finally:
        if db_session:
            db_session.close()


def _default_avatar_response():
    """Return a neutral SVG avatar placeholder (200 OK) to avoid browser 404 console errors."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        '<circle cx="32" cy="32" r="32" fill="#E8ECEF"/>'
        '<circle cx="32" cy="24" r="10" fill="#B0B8C1"/>'
        '<ellipse cx="32" cy="52" rx="18" ry="12" fill="#B0B8C1"/>'
        '</svg>'
    )
    return web.Response(
        body=svg.encode(),
        content_type='image/svg+xml',
        headers={'Cache-Control': 'public, max-age=60'}
    )


async def api_avatar_handler(request):
    """API endpoint to get user avatar by telegram_id.
    Priority: custom_avatar > Telegram/Discord avatar.
    For Telegram users: proxies avatar via server to hide Bot Token.
    For Discord users (negative id): serves cached Discord CDN avatar directly.
    """
    telegram_id = request.match_info.get('telegram_id')

    if not telegram_id:
        return web.Response(status=400, text='Missing telegram_id')

    try:
        telegram_id = int(telegram_id)

        # Check for custom avatar first (highest priority)
        from models import User as _User
        _db = Session()
        try:
            _user = _db.query(_User).filter_by(telegram_id=telegram_id).first()
            if _user and _user.custom_avatar:
                import base64
                # Parse data URI: data:image/jpeg;base64,/9j/...
                parts = _user.custom_avatar.split(',', 1)
                if len(parts) == 2:
                    meta = parts[0]  # data:image/jpeg;base64
                    ct = meta.split(':')[1].split(';')[0] if ':' in meta else 'image/jpeg'
                    img_data = base64.b64decode(parts[1])
                    return web.Response(
                        body=img_data,
                        content_type=ct,
                        headers={'Cache-Control': 'public, max-age=300'}
                    )
        finally:
            _db.close()

        # Discord-only users: serve cached photo_url (Discord CDN)
        if telegram_id < 0:
            from models import User as _User
            _db = Session()
            try:
                _user = _db.query(_User).filter_by(telegram_id=telegram_id).first()
                if _user and _user.photo_url:
                    # Discord CDN URLs are public, redirect directly
                    return web.HTTPFound(_user.photo_url)
                return _default_avatar_response()
            finally:
                _db.close()

        # Telegram users: proxy through server
        if 'bot' not in request.app or not request.app['bot']:
            logger.warning(f"Bot not available for avatar request: {telegram_id}")
            return _default_avatar_response()

        avatar_url = await get_user_avatar_url(request.app['bot'], telegram_id, force_refresh=True)

        if avatar_url:
            # Проксируем изображение через сервер, чтобы не раскрывать Bot Token
            try:
                async with aiohttp.ClientSession() as proxy_session:
                    async with proxy_session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            content_type = resp.headers.get('Content-Type', 'image/jpeg')
                            return web.Response(
                                body=data,
                                content_type=content_type,
                                headers={'Cache-Control': 'public, max-age=3600'}
                            )
                        else:
                            return _default_avatar_response()
            except Exception as e:
                logger.warning(f"Failed to proxy avatar for {telegram_id}: {e}")
                return _default_avatar_response()
        else:
            # Return default avatar placeholder if no avatar found
            return _default_avatar_response()
    except ValueError:
        return web.Response(status=400, text='Invalid telegram_id')
    except Exception as e:
        logger.error(f"Error in api_avatar_handler: {e}")
        return web.Response(status=500, text='Internal server error')


async def api_avatar_upload_handler(request):
    """API endpoint to upload custom avatar"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        # Read multipart form data with chunked approach for reliability
        try:
            reader = await request.multipart()
        except Exception as mp_err:
            logger.error(f"Avatar multipart parse error: {mp_err}")
            return web.json_response({'error': f'Multipart parse error: {mp_err}'}, status=400)

        field = await reader.next()
        if not field:
            return web.json_response({'error': 'No file field in form'}, status=400)

        # Skip non-file fields to find 'avatar'
        while field and field.name != 'avatar':
            await field.read()  # consume and skip
            field = await reader.next()

        if not field or field.name != 'avatar':
            return web.json_response({'error': 'No avatar file field'}, status=400)

        # BodyPartReader не имеет .content_type — читаем из headers
        content_type = (
            field.headers.get('Content-Type')
            or field.headers.get('content-type')
            or 'image/jpeg'
        )
        # Убираем параметры типа "; charset=..." если есть
        if ';' in content_type:
            content_type = content_type.split(';')[0].strip()
        if content_type not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
            return web.json_response({'error': f'Invalid image format: {content_type}'}, status=400)

        # Read file data in chunks (max 4MB)
        MAX_SIZE = 4 * 1024 * 1024
        chunks = []
        total_size = 0
        while True:
            chunk = await field.read_chunk(size=65536)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_SIZE:
                return web.json_response({'error': f'File too large (max 4MB, got {total_size})'}, status=400)
            chunks.append(chunk)

        data = b''.join(chunks)
        if not data:
            return web.json_response({'error': 'Empty file'}, status=400)

        logger.info(f"Avatar upload: user={user_id}, size={len(data)}, type={content_type}")

        import base64
        data_uri = f"data:{content_type};base64,{base64.b64encode(data).decode()}"

        db_session = Session()
        try:
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            user.custom_avatar = data_uri
            db_session.commit()
            logger.info(f"Custom avatar saved for user {user_id}, data_uri_len={len(data_uri)}")

            import random
            return web.json_response({'success': True, 'avatar_url': f'/api/avatar/{user_id}?r={random.randint(100000,999999)}'})
        finally:
            db_session.close()

    except Exception as e:
        logger.error(f"Error uploading avatar: {e}", exc_info=True)
        return web.json_response({'error': f'Upload error: {str(e)}'}, status=500)


async def api_avatar_delete_handler(request):
    """API endpoint to delete custom avatar (revert to Telegram)"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        db_session = Session()
        try:
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                user.custom_avatar = None
                db_session.commit()
            return web.json_response({'success': True})
        finally:
            db_session.close()

    except Exception as e:
        logger.error(f"Error deleting avatar: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_reminders_handler(request):
    user_id = await get_user_id_from_request(request)
    logger.info(f"API reminders handler called, user_id: {user_id}")
    if not user_id:
        logger.error("No user_id in session for reminders API")
        return web.json_response({'error': 'Not logged in'}, status=401)

    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found', 'reminders': []}, status=404)
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
    from config import LOCAL, SESSION_SECRET
    import hashlib
    
    # Setup session middleware with proper cookie settings
    cookie_params = {'httponly': True}
    if not LOCAL:
        cookie_params.update({
            'secure': True,  # HTTPS only in production
            'samesite': 'None'  # Allow cross-site for Telegram auth
        })
    else:
        cookie_params['samesite'] = 'Lax'
    
    # Generate 32-byte key from SESSION_SECRET for Fernet encryption
    secret_key = hashlib.sha256(SESSION_SECRET.encode()).digest()
    
    storage = EncryptedCookieStorage(secret_key, **cookie_params)
    logger.info("Using EncryptedCookieStorage for sessions")
    
    aiohttp_session.setup(app, storage)
    logger.info("Session middleware set up")

    # Запускаем глобальную ленту арены агентов
    try:
        from ai_integration.agent_arena import start_global_arena
        start_global_arena()
        logger.info("[ARENA] Global arena started.")
    except Exception as _ae:
        logger.warning(f"[ARENA] Failed to start global arena: {_ae}")
    
    # Синхрозируем users.subscription_tier с subscriptions.tier при старте
    session_db = None
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
    except Exception as e:
        logger.error(f"❌ Error syncing subscription tiers on startup: {e}")
    finally:
        if session_db:
            session_db.close()

    # One-time: clear all normalized profile fields so they re-translate with fixed prompts
    session_db = None
    try:
        session_db = Session()
        _norm_fields = ['city', 'country', 'company', 'position', 'goals', 'skills', 'interests', 'bio', 'status_text']
        _cleared = 0
        for _prof in session_db.query(UserProfile).all():
            _had = False
            for _f in _norm_fields:
                if getattr(_prof, f'{_f}_normalized', None) or getattr(_prof, f'{_f}_normalized_ru', None):
                    setattr(_prof, f'{_f}_normalized', None)
                    setattr(_prof, f'{_f}_normalized_ru', None)
                    _had = True
            if _had:
                _cleared += 1
        if _cleared:
            session_db.commit()
            logger.info(f"✅ Reset normalized fields for {_cleared} profiles (prompt fix v2)")
    except Exception as e:
        logger.error(f"❌ Error resetting normalized fields: {e}")
    finally:
        if session_db:
            session_db.close()

    # Очищаем накопленную историю диалогов (содержит галлюцинации)
    session_db = None
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
    except Exception as e:
        logger.error(f"❌ Error clearing conversation history: {e}")
    finally:
        if session_db:
            session_db.close()

    # Set webhook for production mode
    if bot and not LOCAL:
        webhook_url = os.getenv('WEBHOOK_URL', 'https://asibiont.com/webhook')
        try:
            await bot.set_webhook(webhook_url, secret_token=WEBHOOK_SECRET or None)
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

    # Close Discord bot
    try:
        from discord_bot import stop_discord_bot
        await stop_discord_bot()
        logger.info("✅ Discord bot stopped")
    except Exception:
        pass


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
        
        # Exclude rejected and cancelled (soft-deleted) tasks from the list
        tasks = [t for t in tasks if t.status not in ('rejected', 'cancelled') and (not hasattr(t, 'delegation_status') or t.delegation_status != 'rejected')]
        
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
                'overdue_value': None,
                'overdue_unit': None,
                'is_delegated': task.delegated_to_username is not None,
                'delegation_status': task.delegation_status if hasattr(task, 'delegation_status') else None,
                'delegated_to': task.delegated_to_username,
                'delegated_to_username': task.delegated_to_username,  # Дублируем для удобста
                'delegated_by': None,  # Будет устале же
                'delegated_by_username': None,  # Username того кто поручил
                'delegated_by_me': task.delegated_by == user.id,  # True если я делегироал эту задачу
                'updated_at': (task.actual_completion_time.isoformat() + 'Z') if task.actual_completion_time else ((task.created_at.isoformat() + 'Z') if task.created_at else None),
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
                        task_data['overdue_value'] = days
                        task_data['overdue_unit'] = 'days'
                    elif hours > 0:
                        task_data['overdue_value'] = hours
                        task_data['overdue_unit'] = 'hours'
                    else:
                        task_data['overdue_value'] = minutes
                        task_data['overdue_unit'] = 'minutes'
            tasks_data.append(task_data)

        return web.json_response({'tasks': tasks_data})
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)
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
        return web.json_response({'error': 'Internal server error'}, status=500)
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
        return web.json_response({'error': 'Internal server error'}, status=500)
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
            # Determine viewer language for translation
            current_user_for_lang = session_db.query(User).filter_by(telegram_id=user_id).first()
            _search_lang = current_user_for_lang.language if current_user_for_lang and hasattr(current_user_for_lang, 'language') and current_user_for_lang.language else 'ru'
            def _pick_search(profile_obj, field_name):
                if not profile_obj:
                    return None
                original = getattr(profile_obj, field_name, None)
                if not original:
                    return None
                if _search_lang == 'en':
                    return getattr(profile_obj, f'{field_name}_normalized', None) or original
                else:
                    return getattr(profile_obj, f'{field_name}_normalized_ru', None) or original

            # Поиск пользователей по username, discord_username, first_name, city, interests
            from sqlalchemy import or_
            users = (session_db.query(User)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .filter(
                    or_(
                        User.username.ilike(f'%{query}%'),
                        User.discord_username.ilike(f'%{query}%'),
                        User.first_name.ilike(f'%{query}%'),
                        UserProfile.city.ilike(f'%{query}%'),
                        UserProfile.interests.ilike(f'%{query}%'),
                    )
                ).distinct().limit(20).all())

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

                # Auto-renormalize profile if EN viewer and translated fields are missing
                if _search_lang == 'en' and profile:
                    _needs_norm = any(
                        getattr(profile, f, None) and not getattr(profile, f'{f}_normalized', None)
                        for f in ['city', 'company', 'position', 'interests']
                    )
                    if _needs_norm:
                        try:
                            from ai_integration.utils import normalize_profile_fields
                            _norm_ok = await normalize_profile_fields(profile)
                            if _norm_ok:
                                session_db.commit()
                                logger.info(f"[SEARCH] Auto-normalized profile for user {user.id}")
                        except Exception as _ne:
                            logger.warning(f"[SEARCH] Auto-normalization failed for user {user.id}: {_ne}")

                contacts_data.append({
                    'username': user.username,
                    'first_name': user.first_name,
                    'telegram_id': user.telegram_id,
                    'photo_url': photo_url,
                    'city': _pick_search(profile, 'city'),
                    'company': _pick_search(profile, 'company'),
                    'position': _pick_search(profile, 'position'),
                    'interests': _pick_search(profile, 'interests'),
                    'bio': _pick_search(profile, 'bio'),
                    'average_rating': profile.average_rating if profile else 0,
                    'rating_count': profile.rating_count if profile else 0
                })

            # Добавить my_rating для результатов поиска
            current_user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if current_user:
                my_ratings_map = {}
                try:
                    my_ratings = session_db.query(UserRating).filter_by(rater_user_id=current_user.id).all()
                    for r in my_ratings:
                        rated_u = session_db.query(User).filter_by(id=r.rated_user_id).first()
                        if rated_u and rated_u.username:
                            my_ratings_map[rated_u.username.replace('@', '').lower()] = r.rating
                except Exception as e:
                    logger.error(f"Error fetching my ratings in search: {e}")

                for contact in contacts_data:
                    username = contact.get('username', '')
                    if username:
                        contact['my_rating'] = my_ratings_map.get(username.replace('@', '').lower(), None)
                    else:
                        contact['my_rating'] = None

            return web.json_response({'contacts': contacts_data})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error searching contacts: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


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
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if user:
                user.timezone = timezone
                session_db.commit()
                logger.info(f"Updated timezone for user {user_id} to {timezone}")
            else:
                logger.warning(f"User not found for telegram_id={user_id} in update_timezone")
                return web.json_response({'status': 'error', 'message': 'User not found'}, status=404)
        finally:
            session_db.close()

        return web.json_response({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error updating timezone: {e}")
        return web.json_response({'status': 'error', 'detail': 'Internal server error'}, status=500)


async def api_balance_handler(request):
    """API для получения баланса токенов"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            balance = user.token_balance if user else 0
            return web.json_response({'balance': balance or 0})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_balance: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_goals_handler(request):
    """API для получения целей пользователя с прогрессом"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            
            goals = session_db.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status.in_(['active', 'completed'])
            ).order_by(Goal.created_at.desc()).all()
            
            goals_data = []
            for g in goals:
                # Calculate progress: use metric if available, else progress_percentage
                progress = g.progress_percentage or 0
                if g.metric_target and g.metric_target > 0:
                    progress = min(100, int((g.metric_current or 0) / g.metric_target * 100))
                
                # Count linked tasks
                total_tasks = session_db.query(Task).filter(Task.goal_id == g.id).count()
                completed_tasks = session_db.query(Task).filter(Task.goal_id == g.id, Task.status == 'completed').count()
                
                goals_data.append({
                    'id': g.id,
                    'title': g.title,
                    'status': g.status,
                    'progress': progress,
                    'metric_current': g.metric_current,
                    'metric_target': g.metric_target,
                    'metric_unit': g.metric_unit,
                    'category': g.category,
                    'priority': g.priority,
                    'target_date': g.target_date.strftime('%d.%m.%Y') if g.target_date else None,
                    'total_tasks': total_tasks,
                    'completed_tasks': completed_tasks,
                })
            
            return web.json_response({'goals': goals_data})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_goals: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_notes_handler(request):
    """API for getting and creating notes"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            if request.method == 'POST':
                data = await request.json()
                content = (data.get('content') or '').strip()
                if not content:
                    return web.json_response({'error': 'Content required'}, status=400)
                if len(content) > 5000:
                    return web.json_response({'error': 'Note too long'}, status=400)
                source = data.get('source', 'manual')
                if source not in ('manual', 'chat'):
                    source = 'manual'
                title = (data.get('title') or '').strip() or None
                note = Note(user_id=user.id, title=title, content=content, source=source)
                session_db.add(note)
                session_db.commit()
                return web.json_response({'success': True, 'note': {
                    'id': note.id,
                    'title': note.title,
                    'content': note.content,
                    'source': note.source,
                    'created_at': (note.created_at.isoformat() + 'Z') if note.created_at else None,
                }})

            # GET
            notes = session_db.query(Note).filter_by(user_id=user.id).order_by(Note.created_at.desc()).limit(100).all()
            return web.json_response({'notes': [{
                'id': n.id,
                'title': n.title,
                'content': n.content,
                'source': n.source,
                'created_at': (n.created_at.isoformat() + 'Z') if n.created_at else None,
            } for n in notes]})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_notes: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_note_delete_handler(request):
    """API for deleting a note"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        note_id = int(request.match_info['note_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            note = session_db.query(Note).filter_by(id=note_id, user_id=user.id).first()
            if not note:
                return web.json_response({'error': 'Note not found'}, status=404)
            session_db.delete(note)
            session_db.commit()
            return web.json_response({'success': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error deleting note: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_note_edit_handler(request):
    """API for editing a note"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        note_id = int(request.match_info['note_id'])
        data = await request.json()
        content = (data.get('content') or '').strip()
        if not content:
            return web.json_response({'error': 'Content required'}, status=400)
        if len(content) > 5000:
            return web.json_response({'error': 'Note too long'}, status=400)
        title = (data.get('title') or '').strip() or None
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            note = session_db.query(Note).filter_by(id=note_id, user_id=user.id).first()
            if not note:
                return web.json_response({'error': 'Note not found'}, status=404)
            note.content = content
            note.title = title
            session_db.commit()
            return web.json_response({'success': True, 'note': {
                'id': note.id,
                'title': note.title,
                'content': note.content,
                'source': note.source,
                'created_at': (note.created_at.isoformat() + 'Z') if note.created_at else None,
            }})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error editing note: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_email_contacts_handler(request):
    """API for managing email contacts — GET (list), POST (create)."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            if request.method == 'GET':
                contacts = session_db.query(EmailContact).filter_by(
                    user_id=user.id
                ).order_by(EmailContact.created_at.desc()).limit(200).all()
                return web.json_response({'contacts': [{
                    'id': c.id,
                    'email': c.email,
                    'name': c.name,
                    'company': c.company,
                    'position': c.position,
                    'notes': c.notes,
                    'source': c.source,
                    'status': c.status,
                    'last_contacted_at': (c.last_contacted_at.isoformat() + 'Z') if c.last_contacted_at else None,
                    'created_at': (c.created_at.isoformat() + 'Z') if c.created_at else None,
                } for c in contacts]})

            elif request.method == 'POST':
                data = await request.json()
                email = (data.get('email') or '').strip().lower()
                if not email or '@' not in email:
                    return web.json_response({'error': 'Invalid email'}, status=400)
                # Check duplicate
                existing = session_db.query(EmailContact).filter_by(
                    user_id=user.id, email=email
                ).first()
                if existing:
                    return web.json_response({'error': 'Contact already exists'}, status=409)
                contact = EmailContact(
                    user_id=user.id,
                    email=email,
                    name=data.get('name', '').strip() or None,
                    company=data.get('company', '').strip() or None,
                    position=data.get('position', '').strip() or None,
                    notes=data.get('notes', '').strip() or None,
                    source=data.get('source', 'manual'),
                )
                session_db.add(contact)
                session_db.commit()
                return web.json_response({'ok': True, 'id': contact.id})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_email_contacts: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_email_contact_edit_handler(request):
    """Edit an email contact (PUT)."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        contact_id = int(request.match_info['contact_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            contact = session_db.query(EmailContact).filter_by(
                id=contact_id, user_id=user.id
            ).first()
            if not contact:
                return web.json_response({'error': 'Contact not found'}, status=404)
            data = await request.json()
            for field in ('name', 'company', 'position', 'notes', 'status'):
                if field in data:
                    setattr(contact, field, (data[field] or '').strip() or None)
            if 'email' in data:
                new_email = (data['email'] or '').strip().lower()
                if new_email and '@' in new_email:
                    contact.email = new_email
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_email_contact_edit: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_email_contact_delete_handler(request):
    """Delete an email contact."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        contact_id = int(request.match_info['contact_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            contact = session_db.query(EmailContact).filter_by(
                id=contact_id, user_id=user.id
            ).first()
            if not contact:
                return web.json_response({'error': 'Contact not found'}, status=404)
            session_db.delete(contact)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error deleting email contact: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_messages_handler(request):
    """GET /api/messages — get inbox/outbox messages for dashboard."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import UserMessage
            messages = session_db.query(UserMessage).filter(
                (UserMessage.sender_id == user.id) | (UserMessage.recipient_id == user.id)
            ).order_by(UserMessage.created_at.desc()).limit(50).all()
            
            result = []
            for msg in messages:
                sender = session_db.query(User).filter_by(id=msg.sender_id).first()
                recipient = session_db.query(User).filter_by(id=msg.recipient_id).first()
                is_incoming = msg.recipient_id == user.id
                result.append({
                    'id': msg.id,
                    'is_incoming': is_incoming,
                    'sender_username': sender.username if sender else None,
                    'sender_name': sender.first_name if sender else None,
                    'sender_photo': safe_avatar_url(sender.telegram_id) if sender and sender.telegram_id and sender.telegram_id > 0 else None,
                    'recipient_username': recipient.username if recipient else None,
                    'recipient_name': recipient.first_name if recipient else None,
                    'message_text': msg.message_text,
                    'intent': msg.intent,
                    'status': msg.status,
                    'reply_text': msg.reply_text,
                    'created_at': msg.created_at.isoformat() if msg.created_at else None,
                    'replied_at': msg.replied_at.isoformat() if msg.replied_at else None,
                })
            
            unread_count = session_db.query(UserMessage).filter(
                UserMessage.recipient_id == user.id,
                UserMessage.status.in_(['sent', 'delivered', 'pending_read'])
            ).count()
            
            return web.json_response({'messages': result, 'unread_count': unread_count})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_messages_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_messages_reply_handler(request):
    """POST /api/messages/reply — reply to inbox message."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        message_id = data.get('message_id')
        reply_text = data.get('reply_text', '').strip()
        if not message_id or not reply_text:
            return web.json_response({'error': 'message_id and reply_text required'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import UserMessage
            msg = session_db.query(UserMessage).filter_by(id=message_id, recipient_id=user.id).first()
            if not msg:
                return web.json_response({'error': 'Message not found'}, status=404)
            
            import datetime as _dt
            msg.reply_text = reply_text
            msg.replied_at = _dt.datetime.now(_dt.timezone.utc)
            msg.status = 'replied'
            session_db.commit()
            
            # Deliver reply via Telegram if sender has TG
            sender = session_db.query(User).filter_by(id=msg.sender_id).first()
            if sender and sender.telegram_id and sender.telegram_id > 0:
                try:
                    from config import TELEGRAM_TOKEN
                    import aiohttp
                    reply_user = user.first_name or user.username or 'Пользователь'
                    tg_text = f"💬 Ответ от @{user.username or reply_user}:\n\n{reply_text}"
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                    async with aiohttp.ClientSession() as http_session:
                        async with http_session.post(url, json={"chat_id": sender.telegram_id, "text": tg_text}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            pass
                except Exception as e:
                    logger.warning(f"Failed to deliver reply via TG: {e}")
            
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_messages_reply_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_messages_send_handler(request):
    """POST /api/messages/send — send a new internal message from dashboard."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        recipient_username = data.get('recipient_username', '').strip().lstrip('@')
        message_text = data.get('message_text', '').strip()
        if not recipient_username or not message_text:
            return web.json_response({'error': 'recipient_username and message_text required'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from sqlalchemy import func as sa_func
            recipient = session_db.query(User).filter(
                sa_func.lower(User.username) == recipient_username.lower()
            ).first()
            if not recipient:
                recipient = session_db.query(User).filter(
                    sa_func.lower(User.first_name) == recipient_username.lower()
                ).first()
            if not recipient:
                return web.json_response({'error': f'Пользователь @{recipient_username} не найден'}, status=404)
            if recipient.id == user.id:
                return web.json_response({'error': 'Нельзя отправить сообщение себе'}, status=400)
            
            from models import UserMessage
            import datetime as _dt
            msg = UserMessage(
                sender_id=user.id,
                recipient_id=recipient.id,
                message_text=message_text,
                intent='direct',
                status='sent',
                is_ai_generated=False,
            )
            session_db.add(msg)
            session_db.commit()
            
            # Try to deliver via Telegram
            if recipient.telegram_id and recipient.telegram_id > 0:
                try:
                    from config import TELEGRAM_TOKEN
                    import aiohttp
                    sender_uname = user.username or user.first_name or 'Пользователь'
                    tg_text = f"📩 Сообщение от @{sender_uname}:\n\n{message_text}\n\n💬 Чтобы ответить, напиши: «ответь @{sender_uname} [твой ответ]»"
                    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                    async with aiohttp.ClientSession() as http_session:
                        async with http_session.post(url, json={"chat_id": recipient.telegram_id, "text": tg_text}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                msg.status = 'delivered'
                                msg.delivered_at = _dt.datetime.now(_dt.timezone.utc)
                                session_db.commit()
                except Exception as e:
                    logger.warning(f"TG delivery failed for internal message: {e}")
            
            return web.json_response({'ok': True, 'message_id': msg.id})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_messages_send_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_messages_read_handler(request):
    """POST /api/messages/read — mark messages as read."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import UserMessage
            updated = session_db.query(UserMessage).filter(
                UserMessage.recipient_id == user.id,
                UserMessage.status.in_(['sent', 'delivered', 'pending_read'])
            ).update({UserMessage.status: 'read'}, synchronize_session='fetch')
            session_db.commit()
            return web.json_response({'ok': True, 'marked_read': updated})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_messages_read_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_messages_delete_handler(request):
    """POST /api/messages/delete — delete a message."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        message_id = data.get('message_id')
        if not message_id:
            return web.json_response({'error': 'message_id required'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import UserMessage
            msg = session_db.query(UserMessage).filter_by(id=message_id).first()
            if not msg:
                return web.json_response({'error': 'Message not found'}, status=404)
            # Only sender or recipient can delete
            if msg.sender_id != user.id and msg.recipient_id != user.id:
                return web.json_response({'error': 'Access denied'}, status=403)
            session_db.delete(msg)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_messages_delete_handler: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_outreach_delete_handler(request):
    """Delete an outreach record."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        outreach_id = int(request.match_info['outreach_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import EmailOutreach, EmailCampaign
            outreach = session_db.query(EmailOutreach).filter_by(
                id=outreach_id, user_id=user.id
            ).first()
            if not outreach:
                return web.json_response({'error': 'Outreach not found'}, status=404)
            # Update campaign counters
            if outreach.campaign_id:
                campaign = session_db.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
                if campaign:
                    if outreach.status not in ('draft',):
                        campaign.emails_sent = max(0, (campaign.emails_sent or 1) - 1)
                    if outreach.status == 'replied':
                        campaign.emails_replied = max(0, (campaign.emails_replied or 1) - 1)
            session_db.delete(outreach)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error deleting outreach: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_campaign_status_handler(request):
    """PATCH /api/campaigns/{campaign_id}/status — set status active or paused."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        campaign_id = int(request.match_info['campaign_id'])
        body = await request.json()
        new_status = body.get('status')
        if new_status not in ('active', 'paused'):
            return web.json_response({'error': 'Invalid status'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import EmailCampaign
            campaign = session_db.query(EmailCampaign).filter_by(id=campaign_id, user_id=user.id).first()
            if not campaign:
                return web.json_response({'error': 'Campaign not found'}, status=404)
            campaign.status = new_status
            session_db.commit()
            return web.json_response({'ok': True, 'status': new_status})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error updating campaign status: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_campaign_delete_handler(request):
    """DELETE /api/campaigns/{campaign_id} — delete campaign and its outreach."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        campaign_id = int(request.match_info['campaign_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import EmailCampaign, EmailOutreach
            campaign = session_db.query(EmailCampaign).filter_by(id=campaign_id, user_id=user.id).first()
            if not campaign:
                return web.json_response({'error': 'Campaign not found'}, status=404)
            session_db.query(EmailOutreach).filter_by(campaign_id=campaign_id).delete()
            session_db.delete(campaign)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error deleting campaign: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_activity_delete_handler(request):
    """DELETE /api/activities/{activity_id} — delete an agent activity log entry.
    For delegation: cancels the associated task. For posts: deletes the post."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        activity_id = int(request.match_info['activity_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            activity = session_db.query(AgentActivityLog).filter_by(id=activity_id, user_id=user.id).first()
            if not activity:
                return web.json_response({'error': 'Activity not found'}, status=404)
            # For delegation: cancel the task
            if activity.activity_type == 'delegation' and activity.ref_id:
                task = session_db.query(Task).filter_by(id=activity.ref_id).first()
                if task and task.delegation_status in ('pending',):
                    task.delegation_status = 'cancelled'
                    task.status = 'cancelled'
            # For newsfeed posts: delete the post + cascade (likes, comments, views)
            elif activity.activity_type == 'post_newsfeed' and activity.ref_id:
                post = session_db.query(Post).filter_by(id=activity.ref_id, user_id=user.id).first()
                if post:
                    try:
                        from models import PostLike, Comment, PostView
                        session_db.query(PostLike).filter_by(post_id=post.id).delete(synchronize_session=False)
                        session_db.query(Comment).filter_by(post_id=post.id).delete(synchronize_session=False)
                        session_db.query(PostView).filter_by(post_id=post.id).delete(synchronize_session=False)
                    except Exception:
                        pass
                    session_db.delete(post)
            # For content campaign posts: also cascade
            elif activity.activity_type in ('post_telegram', 'post_discord') and activity.ref_id:
                pass  # No linked Post object to delete, just remove the activity log
            # Delete the activity log entry
            session_db.delete(activity)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error deleting activity: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_activity_status_handler(request):
    """PATCH /api/activities/{activity_id}/status — update activity status (pause/resume delegation)."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        activity_id = int(request.match_info['activity_id'])
        data = await request.json()
        new_status = data.get('status', '')
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            activity = session_db.query(AgentActivityLog).filter_by(id=activity_id, user_id=user.id).first()
            if not activity:
                return web.json_response({'error': 'Activity not found'}, status=404)
            activity.status = new_status
            import datetime as _dt
            activity.updated_at = _dt.datetime.now(_dt.timezone.utc)
            # For delegation tasks: update underlying task too
            if activity.activity_type == 'delegation' and activity.ref_id:
                task = session_db.query(Task).filter_by(id=activity.ref_id).first()
                if task:
                    if new_status == 'cancelled':
                        task.delegation_status = 'cancelled'
                        task.status = 'cancelled'
            session_db.commit()
            return web.json_response({'ok': True, 'status': new_status})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error updating activity status: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_content_campaign_status_handler(request):
    """PATCH /api/content-campaigns/{campaign_id}/status"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        cid = int(request.match_info['campaign_id'])
        body = await request.json()
        new_status = body.get('status')
        if new_status not in ('active', 'paused'):
            return web.json_response({'error': 'Invalid status'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import ContentCampaign
            cc = session_db.query(ContentCampaign).filter_by(id=cid, user_id=user.id).first()
            if not cc:
                return web.json_response({'error': 'Not found'}, status=404)
            cc.status = new_status
            session_db.commit()
            return web.json_response({'ok': True, 'status': new_status})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"api_content_campaign_status_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_content_campaign_delete_handler(request):
    """DELETE /api/content-campaigns/{campaign_id}"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        cid = int(request.match_info['campaign_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import ContentCampaign
            cc = session_db.query(ContentCampaign).filter_by(id=cid, user_id=user.id).first()
            if not cc:
                return web.json_response({'error': 'Not found'}, status=404)
            session_db.delete(cc)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"api_content_campaign_delete_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_delegation_campaign_status_handler(request):
    """PATCH /api/delegation-campaigns/{campaign_id}/status"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        cid = int(request.match_info['campaign_id'])
        body = await request.json()
        new_status = body.get('status')
        if new_status not in ('active', 'paused'):
            return web.json_response({'error': 'Invalid status'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import DelegationCampaign
            dc = session_db.query(DelegationCampaign).filter_by(id=cid, user_id=user.id).first()
            if not dc:
                return web.json_response({'error': 'Not found'}, status=404)
            dc.status = new_status
            session_db.commit()
            return web.json_response({'ok': True, 'status': new_status})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"api_delegation_campaign_status_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_delegation_campaign_delete_handler(request):
    """DELETE /api/delegation-campaigns/{campaign_id}"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        cid = int(request.match_info['campaign_id'])
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import DelegationCampaign
            dc = session_db.query(DelegationCampaign).filter_by(id=cid, user_id=user.id).first()
            if not dc:
                return web.json_response({'error': 'Not found'}, status=404)
            session_db.delete(dc)
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"api_delegation_campaign_delete_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_activities_latest_handler(request):
    """Polling endpoint: GET /api/activities/latest?since=<iso_timestamp>
    Returns agent activities newer than `since`. Used for real-time timeline updates."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)

        since_str = request.rel_url.query.get('since')
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'activities': []})

            query = session_db.query(AgentActivityLog).filter_by(user_id=user.id)
            if since_str:
                try:
                    from datetime import timezone as _tz
                    since_dt = datetime.fromisoformat(since_str.replace('Z', '+00:00'))
                    # strip tz for naive datetime comparison
                    since_naive = since_dt.replace(tzinfo=None) if since_dt.tzinfo else since_dt
                    query = query.filter(AgentActivityLog.created_at > since_naive)
                except Exception:
                    pass

            activities = query.order_by(AgentActivityLog.created_at.desc()).limit(50).all()
            data = []
            for a in activities:
                data.append({
                    'id': a.id,
                    'activity_type': a.activity_type,
                    'title': a.title,
                    'content': a.content,
                    'target': a.target,
                    'status': a.status,
                    'ref_id': a.ref_id,
                    'result': a.result,
                    'created_at': (a.created_at.isoformat() + 'Z') if a.created_at else None,
                    'updated_at': (a.updated_at.isoformat() + 'Z') if a.updated_at else None,
                })
            return web.json_response({'activities': data})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"api_activities_latest_handler: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def sse_activities_handler(request):
    """Server-Sent Events stream: push new agent activities in real time."""
    try:
        session = await get_session(request)
        uid = session.get('user_id')
        if not uid:
            return web.Response(status=401)

        last_id = 0
        try:
            last_id = int(request.rel_url.query.get('last_id', '0'))
        except (ValueError, TypeError):
            last_id = 0

        response = web.StreamResponse(headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        })
        await response.prepare(request)

        poll_interval = 5   # seconds between DB polls
        hb_interval = 30    # seconds between heartbeats
        last_hb = asyncio.get_running_loop().time()

        while True:
            try:
                with Session() as sdb:
                    new_acts = sdb.query(AgentActivityLog).filter(
                        AgentActivityLog.user_id == uid,
                        AgentActivityLog.id > last_id
                    ).order_by(AgentActivityLog.id.asc()).limit(20).all()

                    if new_acts:
                        last_id = new_acts[-1].id
                        payload = json.dumps({'activities': [
                            {
                                'id': a.id,
                                'activity_type': a.activity_type,
                                'title': a.title,
                                'content': a.content,
                                'created_at': (a.created_at.isoformat() + 'Z') if a.created_at else None,
                            } for a in new_acts
                        ]})
                        await response.write(f'data: {payload}\n\n'.encode())
                        last_hb = asyncio.get_running_loop().time()
                    elif asyncio.get_running_loop().time() - last_hb >= hb_interval:
                        await response.write(b': heartbeat\n\n')
                        last_hb = asyncio.get_running_loop().time()
            except (ConnectionResetError, asyncio.CancelledError):
                break
            except Exception as _e:
                logger.warning(f"[SSE] query error: {_e}")
            await asyncio.sleep(poll_interval)

        return response
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.warning(f"[SSE] stream error: {e}")


def _categorize_token_action(action: str) -> str:
    """Categorize a token transaction action for 30-day expense stats."""
    a = action.lower()
    if 'proactive' in a or 'проактив' in a:
        return 'proactive'
    if 'message' in a or 'chat' in a:
        return 'chat'
    if 'anchor' in a or 'reminder' in a or 'напомин' in a:
        return 'reminders'
    if 'post' in a or 'пост' in a:
        return 'autoposts'
    if 'research' in a or 'исслед' in a:
        return 'research'
    return 'other'


async def api_reports_handler(request):
    """API for getting email reports — campaigns + standalone emails."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            # Campaigns with outreach
            campaigns_data = []
            try:
                campaigns = session_db.query(EmailCampaign).filter_by(
                    user_id=user.id
                ).order_by(EmailCampaign.created_at.desc()).limit(20).all()
                import pytz as _pytz_api
                from datetime import datetime as _dt_api, timezone as _tz_api
                _user_tz_api = _pytz_api.timezone(getattr(user, 'timezone', None) or 'Europe/Moscow')
                _user_now_api = _dt_api.now(_user_tz_api)
                _today_start_api = _user_now_api.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz_api.utc)

                for c in campaigns:
                    outreach = session_db.query(EmailOutreach).filter_by(
                        campaign_id=c.id
                    ).order_by(EmailOutreach.sent_at.desc()).limit(50).all()
                    opened_count = sum(1 for o in outreach if o.status in ('opened', 'replied'))
                    # Сколько отправлено сегодня
                    _sent_today = session_db.query(EmailOutreach).filter(
                        EmailOutreach.campaign_id == c.id,
                        EmailOutreach.sent_at >= _today_start_api,
                        EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
                    ).count()
                    _drafts_count = sum(1 for o in outreach if o.status == 'draft')
                    campaigns_data.append({
                        'id': c.id,
                        'name': c.name,
                        'goal': c.goal,
                        'status': c.status,
                        'emails_sent': c.emails_sent or 0,
                        'emails_replied': c.emails_replied or 0,
                        'emails_opened': opened_count,
                        'daily_limit': c.daily_limit or 20,
                        'sent_today': _sent_today,
                        'drafts_count': _drafts_count,
                        'max_emails': c.max_emails or 0,
                        'created_at': (c.created_at.isoformat() + 'Z') if c.created_at else None,
                        'outreach': [{
                            'id': o.id,
                            'recipient_email': o.recipient_email,
                            'recipient_name': o.recipient_name,
                            'subject': o.subject,
                            'body': o.body,
                            'status': o.status,
                            'sent_at': (o.sent_at.isoformat() + 'Z') if o.sent_at else None,
                            'reply_text': (o.reply_text[:200] + '...') if o.reply_text and len(o.reply_text) > 200 else o.reply_text,
                            'reply_at': (o.reply_at.isoformat() + 'Z') if o.reply_at else None,
                            'ai_reply_text': (o.ai_reply_text[:200] + '...') if o.ai_reply_text and len(o.ai_reply_text) > 200 else (o.ai_reply_text or None),
                            'ai_reply_sent_at': (o.ai_reply_sent_at.isoformat() + 'Z') if o.ai_reply_sent_at else None,
                        } for o in outreach],
                    })
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading campaigns: {e}")

            # Auto-sync outreach statuses from Resend API (non-blocking)
            try:
                from config import RESEND_API_KEY
                if RESEND_API_KEY:
                    import aiohttp as _aiohttp
                    synced = 0
                    for c_data in campaigns_data:
                        for o_data in c_data.get('outreach', []):
                            if o_data.get('status') in ('sent', 'delivered') and o_data.get('id'):
                                outreach_obj = session_db.query(EmailOutreach).filter_by(id=o_data['id']).first()
                                if outreach_obj and outreach_obj.resend_id:
                                    try:
                                        async with _aiohttp.ClientSession() as http:
                                            resp = await http.get(
                                                f'https://api.resend.com/emails/{outreach_obj.resend_id}',
                                                headers={'Authorization': f'Bearer {RESEND_API_KEY}'},
                                                timeout=_aiohttp.ClientTimeout(total=5),
                                            )
                                            if resp.status == 200:
                                                r_data = await resp.json()
                                                last_event = None
                                                for evt in r_data.get('events', []):
                                                    evt_type = evt.get('type', '')
                                                    if evt_type in ('email.opened',):
                                                        last_event = 'opened'
                                                    elif evt_type == 'email.delivered' and last_event != 'opened':
                                                        last_event = 'delivered'
                                                if last_event:
                                                    status_priority = {'draft': 0, 'sent': 1, 'delivered': 2, 'opened': 3, 'replied': 4}
                                                    if status_priority.get(last_event, 0) > status_priority.get(outreach_obj.status, 0):
                                                        outreach_obj.status = last_event
                                                        o_data['status'] = last_event
                                                        synced += 1
                                    except Exception:
                                        pass
                    if synced > 0:
                        session_db.commit()
                        logger.info(f"[API_REPORTS] Synced {synced} outreach statuses from Resend")
            except Exception as e:
                logger.warning(f"[API_REPORTS] Status sync error: {e}")

            # Agent activities (delegations, auto-posts, TG posts, etc.)
            agent_activities_data = []
            try:
                activities = session_db.query(AgentActivityLog).filter_by(
                    user_id=user.id
                ).order_by(AgentActivityLog.created_at.desc()).limit(100).all()
                for a in activities:
                    agent_activities_data.append({
                        'id': a.id,
                        'activity_type': a.activity_type,
                        'title': a.title,
                        'content': a.content,
                        'target': a.target,
                        'status': a.status,
                        'ref_id': a.ref_id,
                        'result': a.result,
                        'created_at': (a.created_at.isoformat() + 'Z') if a.created_at else None,
                        'updated_at': (a.updated_at.isoformat() + 'Z') if a.updated_at else None,
                    })
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading agent activities: {e}")

            # Post engagement stats (last 7 days)
            post_stats = {'total': 0, 'likes': 0, 'views': 0, 'comments': 0}
            try:
                from datetime import timedelta
                seven_days_ago_stats = datetime.now(dt_timezone.utc) - timedelta(days=7)
                user_posts = session_db.query(Post).filter(
                    Post.user_id == user.id,
                    Post.created_at >= seven_days_ago_stats
                ).all()
                post_stats['total'] = len(user_posts)
                if user_posts:
                    post_ids = [p.id for p in user_posts]
                    post_stats['likes'] = session_db.query(PostLike).filter(
                        PostLike.post_id.in_(post_ids)
                    ).count()
                    post_stats['views'] = session_db.query(PostView).filter(
                        PostView.post_id.in_(post_ids)
                    ).count()
                    post_stats['comments'] = session_db.query(Comment).filter(
                        Comment.post_id.in_(post_ids)
                    ).count()
                    today_start_p = datetime.now(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    today_posts = [p for p in user_posts if p.created_at and p.created_at.replace(tzinfo=dt_timezone.utc) >= today_start_p]
                    today_post_ids = [p.id for p in today_posts]
                    post_stats['total_today'] = len(today_posts)
                    if today_post_ids:
                        post_stats['likes_today'] = session_db.query(PostLike).filter(
                            PostLike.post_id.in_(today_post_ids)
                        ).count()
                        post_stats['views_today'] = session_db.query(PostView).filter(
                            PostView.post_id.in_(today_post_ids)
                        ).count()
                        post_stats['comments_today'] = session_db.query(Comment).filter(
                            Comment.post_id.in_(today_post_ids)
                        ).count()
                    # Also count likes/views/comments received today on ANY of user's posts
                    post_stats['likes_today'] = session_db.query(PostLike).filter(
                        PostLike.post_id.in_(post_ids),
                        PostLike.created_at >= today_start_p
                    ).count()
                    post_stats['views_today'] = session_db.query(PostView).filter(
                        PostView.post_id.in_(post_ids),
                        PostView.viewed_at >= today_start_p
                    ).count()
                    post_stats['comments_today'] = session_db.query(Comment).filter(
                        Comment.post_id.in_(post_ids),
                        Comment.created_at >= today_start_p
                    ).count()
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading post stats: {e}")

            # Task completion stats
            task_stats = {'completed_week': 0, 'total_active': 0, 'overdue': 0}
            try:
                seven_days_ago = datetime.now(dt_timezone.utc) - timedelta(days=7)
                task_stats['completed_week'] = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'completed',
                    Task.created_at >= seven_days_ago
                ).count()
                task_stats['total_active'] = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress'])
                ).count()
                now_utc = datetime.now(dt_timezone.utc)
                task_stats['overdue'] = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status.in_(['pending', 'in_progress']),
                    Task.due_date.isnot(None),
                    Task.due_date < now_utc
                ).count()
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading task stats: {e}")

            # Personal activity stats (last 7 days)
            personal_stats = {
                'tasks_completed': 0, 'tasks_completed_today': 0,
                'tasks_deleted': 0, 'tasks_deleted_today': 0,
                'goals_completed': 0, 'goals_completed_today': 0,
                'activity_index': 0, 'active_days': 0,
            }
            try:
                today_start_ps = datetime.now(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                # Tasks completed in 7d (prefer actual_completion_time, fallback to created_at)
                from sqlalchemy import or_, and_
                completed_tasks_30d = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'completed',
                    or_(
                        and_(Task.actual_completion_time.isnot(None), Task.actual_completion_time >= seven_days_ago_stats),
                        and_(Task.actual_completion_time.is_(None), Task.created_at >= seven_days_ago_stats)
                    )
                ).all()
                personal_stats['tasks_completed'] = len(completed_tasks_30d)
                personal_stats['tasks_completed_today'] = sum(
                    1 for t in completed_tasks_30d
                    if (t.actual_completion_time or t.created_at) and
                       (t.actual_completion_time or t.created_at).replace(tzinfo=dt_timezone.utc) >= today_start_ps
                )
                # Cancelled / deleted tasks in 7d
                personal_stats['tasks_deleted'] = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'cancelled',
                    or_(
                        and_(Task.actual_completion_time.isnot(None), Task.actual_completion_time >= seven_days_ago_stats),
                        and_(Task.actual_completion_time.is_(None), Task.created_at >= seven_days_ago_stats)
                    )
                ).count()
                personal_stats['tasks_deleted_today'] = session_db.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'cancelled',
                    or_(
                        and_(Task.actual_completion_time.isnot(None), Task.actual_completion_time >= today_start_ps),
                        and_(Task.actual_completion_time.is_(None), Task.created_at >= today_start_ps)
                    )
                ).count()
                # Goals completed in 7d
                from models import Goal as _Goal
                goals_30d = session_db.query(_Goal).filter(
                    _Goal.user_id == user.id,
                    _Goal.status == 'completed',
                    _Goal.completed_at >= seven_days_ago_stats
                ).all()
                personal_stats['goals_completed'] = len(goals_30d)
                personal_stats['goals_completed_today'] = sum(
                    1 for g in goals_30d
                    if g.completed_at and g.completed_at.replace(tzinfo=dt_timezone.utc) >= today_start_ps
                )
                # Active days (days with at least one interaction in 7d)
                interactions_30d = session_db.query(Interaction.created_at).filter(
                    Interaction.user_id == user.id,
                    Interaction.created_at >= seven_days_ago_stats
                ).all()
                active_days = len(set(i.created_at.date() for i in interactions_30d if i.created_at))
                personal_stats['active_days'] = active_days
                # Activity index: tasks*5 + goals*15 + posts*3 + active_days*0.67 → capped at 100
                idx = min(100, round(
                    personal_stats['tasks_completed'] * 5 +
                    personal_stats['goals_completed'] * 15 +
                    post_stats.get('total', 0) * 3 +
                    active_days * 0.67
                ))
                personal_stats['activity_index'] = idx
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading personal stats: {e}")

            # Tokens spent today
            tokens_today = 0
            try:
                today_start = datetime.now(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                result = session_db.query(TokenTransaction).filter(
                    TokenTransaction.user_id == user.id,
                    TokenTransaction.amount < 0,
                    TokenTransaction.created_at >= today_start
                ).all()
                tokens_today = sum(abs(t.amount) for t in result)
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading token stats: {e}")

            # Token spend breakdown for last 7 days
            token_stats_30d = {}
            try:
                tt_list = session_db.query(TokenTransaction).filter(
                    TokenTransaction.user_id == user.id,
                    TokenTransaction.amount < 0,
                    TokenTransaction.created_at >= seven_days_ago_stats
                ).all()
                total_30d = 0
                for tx in tt_list:
                    cat = _categorize_token_action(tx.action or '')
                    token_stats_30d[cat] = token_stats_30d.get(cat, 0) + abs(tx.amount)
                    total_30d += abs(tx.amount)
                token_stats_30d['_total'] = total_30d
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading token stats 30d: {e}")

            # Delegations TO me (tasks assigned to my username)
            delegated_to_me = 0
            delegated_to_me_today = 0
            try:
                my_username = user.username
                if my_username:
                    dm_q = session_db.query(Task).filter(
                        Task.delegated_to_username == my_username,
                        Task.delegation_status.in_(['pending', 'accepted', 'in_progress'])
                    )
                    delegated_to_me = dm_q.count()
                    today_start_d = datetime.now(dt_timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    delegated_to_me_today = session_db.query(Task).filter(
                        Task.delegated_to_username == my_username,
                        Task.created_at >= today_start_d
                    ).count()
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading delegated_to_me: {e}")

            # Content campaigns (auto-posting projects)
            content_campaigns_data = []
            try:
                from models import ContentCampaign
                cc_list = session_db.query(ContentCampaign).filter_by(
                    user_id=user.id
                ).order_by(ContentCampaign.created_at.desc()).limit(20).all()
                import json as _json_cc_api
                for cc in cc_list:
                    try:
                        _cc_platforms = _json_cc_api.loads(cc.platforms) if cc.platforms else ['feed']
                    except Exception:
                        _cc_platforms = ['feed']
                    content_campaigns_data.append({
                        'id': cc.id,
                        'name': cc.name,
                        'goal': cc.goal,
                        'topics': cc.topics,
                        'platforms': _cc_platforms,
                        'tone': cc.tone,
                        'frequency': cc.frequency,
                        'post_time': cc.post_time,
                        'daily_limit': cc.daily_limit or 1,
                        'max_posts': cc.max_posts or 0,
                        'posts_published': cc.posts_published or 0,
                        'status': cc.status,
                        'last_post_at': (cc.last_post_at.isoformat() + 'Z') if cc.last_post_at else None,
                        'created_at': (cc.created_at.isoformat() + 'Z') if cc.created_at else None,
                    })
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading content campaigns: {e}")

            # Delegation campaigns
            delegation_campaigns_data = []
            try:
                from models import DelegationCampaign
                dc_list = session_db.query(DelegationCampaign).filter_by(
                    user_id=user.id
                ).order_by(DelegationCampaign.created_at.desc()).limit(20).all()
                for dc in dc_list:
                    delegation_campaigns_data.append({
                        'id': dc.id,
                        'name': dc.name,
                        'goal': dc.goal,
                        'target_audience': dc.target_audience,
                        'task_template': dc.task_template,
                        'offer': dc.offer,
                        'status': dc.status,
                        'max_delegations': dc.max_delegations or 0,
                        'daily_limit': dc.daily_limit or 3,
                        'delegations_sent': dc.delegations_sent or 0,
                        'delegations_accepted': dc.delegations_accepted or 0,
                        'delegations_completed': dc.delegations_completed or 0,
                        'delegations_rejected': dc.delegations_rejected or 0,
                        'default_deadline_hours': dc.default_deadline_hours or 48,
                        'last_delegation_at': (dc.last_delegation_at.isoformat() + 'Z') if dc.last_delegation_at else None,
                        'created_at': (dc.created_at.isoformat() + 'Z') if dc.created_at else None,
                    })
            except Exception as e:
                logger.warning(f"[API_REPORTS] Error loading delegation campaigns: {e}")

            return web.json_response({
                'campaigns': campaigns_data,
                'emails': [],
                'agent_activities': agent_activities_data,
                'content_campaigns': content_campaigns_data,
                'delegation_campaigns': delegation_campaigns_data,
                'post_stats': post_stats,
                'task_stats': task_stats,
                'personal_stats': personal_stats,
                'tokens_today': tokens_today,
                'token_stats_30d': token_stats_30d,
                'delegated_to_me': delegated_to_me,
                'delegated_to_me_today': delegated_to_me_today,
            })
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_reports: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def api_outreach_reply_handler(request):
    """Manually mark an outreach as replied with reply text."""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        outreach_id = int(request.match_info['outreach_id'])
        reply_text = (data.get('reply_text', '') or '').strip()
        if not reply_text:
            return web.json_response({'error': 'reply_text is required'}, status=400)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            from models import EmailOutreach, EmailCampaign
            outreach = session_db.query(EmailOutreach).filter_by(id=outreach_id, user_id=user.id).first()
            if not outreach:
                return web.json_response({'error': 'Outreach not found'}, status=404)
            was_replied = outreach.status == 'replied'
            outreach.status = 'replied'
            if outreach.reply_text:
                outreach.reply_text = (outreach.reply_text + '\n\n--- ' + datetime.now(dt_timezone.utc).strftime('%d.%m.%Y %H:%M') + ' ---\n' + reply_text)[:5000]
            else:
                outreach.reply_text = reply_text[:5000]
            outreach.reply_at = datetime.now(dt_timezone.utc)
            if not was_replied:
                campaign = session_db.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
                if campaign:
                    campaign.emails_replied = (campaign.emails_replied or 0) + 1
            session_db.commit()
            return web.json_response({'status': 'ok'})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Error in api_outreach_reply: {e}", exc_info=True)
        return web.json_response({'error': 'Internal server error'}, status=500)


async def translate_note_handler(request):
    """Translate a note to the specified language using DeepSeek"""
    db_session = None
    try:
        user_session = await get_session(request)
        user_id = user_session.get('user_id')
        if not user_id:
            return web.json_response({'error': 'Unauthorized'}, status=401)

        note_id = int(request.match_info['note_id'])
        data = await request.json()
        target_lang = data.get('lang', 'en')

        db_session = Session()
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        note = db_session.query(Note).filter_by(id=note_id, user_id=user.id).first()
        if not note:
            return web.json_response({'error': 'Note not found'}, status=404)

        content = note.content
        if not content or len(content.strip()) < 2:
            return web.json_response({'error': 'Nothing to translate'}, status=400)

        lang_names = {
            'ru': 'Russian', 'en': 'English', 'es': 'Spanish', 'fr': 'French',
            'de': 'German', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
        }
        lang_name = lang_names.get(target_lang, target_lang)

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': DEEPSEEK_MODEL,
                    'messages': [
                        {'role': 'system', 'content': f'Translate the following text to {lang_name}. Return ONLY the translated text, nothing else. Preserve formatting and line breaks.'},
                        {'role': 'user', 'content': content},
                    ],
                    'max_tokens': 2000,
                    'temperature': 0.3,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
            result = await resp.json()

        translated = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        if not translated:
            return web.json_response({'error': 'Translation failed'}, status=500)

        return web.json_response({'success': True, 'translated': translated, 'lang': target_lang})

    except Exception as e:
        logger.error(f"Error translating note: {e}", exc_info=True)
        return web.json_response({'error': 'Translation error'}, status=500)
    finally:
        if db_session:
            db_session.close()


async def api_set_language_handler(request):
    """Save user language preference to database"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        lang = data.get('lang', 'ru')
        if lang not in ('ru', 'en'):
            lang = 'ru'
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if user:
                user.language = lang
                session_db.commit()
                logger.info(f"[SET_LANG] User {user_id} language set to {lang}")
            return web.json_response({'success': True, 'lang': lang})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[SET_LANG] Error: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def api_profile_handler(request):
    """API для получения и обновления профиля пользователя"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        logger.info(f"API profile: session exists={session is not None}, user_id={user_id}")
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
                if 'country' in data:
                    profile.country = data['country'].strip() if data['country'] and data['country'].strip() else None
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
                    import re as _re
                    new_goals_text = data['goals'].strip() if data['goals'] and data['goals'].strip() else None
                    # Parse old and new goal parts for diff-sync
                    old_goals_text = profile.goals or ''
                    _old_parts_lower = {g.strip().lower() for g in _re.split(r'[;,]', old_goals_text) if g.strip() and len(g.strip()) > 2}
                    _new_parts = [g.strip() for g in _re.split(r'[;,]', new_goals_text or '') if g.strip() and len(g.strip()) > 2]
                    _new_parts_lower = {g.lower() for g in _new_parts}
                    profile.goals = new_goals_text
                    # Sync goals text → Goal objects (create missing, delete removed)
                    _existing_goals = session_db.query(Goal).filter(
                        Goal.user_id == user.id,
                        Goal.status.in_(['active', 'paused', 'in_progress'])
                    ).all()
                    _existing_titles_lower = {g.title.lower(): g for g in _existing_goals}
                    # Mark as deleted: goals whose title was in OLD profile text but not in NEW
                    for _title_lower, _gobj in _existing_titles_lower.items():
                        if _title_lower in _old_parts_lower and _title_lower not in _new_parts_lower:
                            _gobj.status = 'deleted'
                            logger.info(f"[API PROFILE] Deleted goal '{_gobj.title}' (removed from profile text) for user {user_id}")
                    # Create new goals for titles added to profile text
                    for _gtitle in _new_parts:
                        if _gtitle.lower() not in _existing_titles_lower:
                            _new_goal = Goal(
                                user_id=user.id,
                                title=_gtitle[:255],
                                status='active',
                                priority='medium',
                                category='personal',
                                progress_percentage=0,
                                metric_current=0,
                            )
                            session_db.add(_new_goal)
                            logger.info(f"[API PROFILE] Auto-created goal '{_gtitle}' for user {user_id}")
                if 'status_text' in data:
                    profile.status_text = data['status_text'].strip()[:100] if data['status_text'] and data['status_text'].strip() else None
                if 'bio' in data:
                    profile.bio = data['bio'].strip() if data['bio'] and data['bio'].strip() else None

                # Update user fields
                if 'first_name' in data:
                    user.first_name = data['first_name'].strip() if data['first_name'] and data['first_name'].strip() else user.first_name
                if 'email' in data:
                    email_val = data['email'].strip().lower() if data['email'] and data['email'].strip() else None
                    if email_val and '@' in email_val:
                        existing_email = session_db.query(User).filter(User.email == email_val, User.id != user.id).first()
                        if not existing_email:
                            user.email = email_val
                    elif not email_val:
                        user.email = None
                if 'phone' in data:
                    user.phone = data['phone'].strip() if data['phone'] and data['phone'].strip() else None
                if 'telegram_channel' in data:
                    user.telegram_channel = data['telegram_channel'].strip() if data['telegram_channel'] and data['telegram_channel'].strip() else None
                if 'discord_webhook' in data:
                    webhook = data['discord_webhook'].strip() if data['discord_webhook'] and data['discord_webhook'].strip() else None
                    if webhook and not webhook.startswith('https://discord.com/api/webhooks/'):
                        return web.json_response({'error': 'Invalid Discord webhook URL'}, status=400)
                    user.discord_webhook = webhook
                    # Fetch server name from Discord webhook
                    if webhook:
                        try:
                            import aiohttp as _aiohttp
                            async with _aiohttp.ClientSession() as _ws:
                                async with _ws.get(webhook) as _wr:
                                    if _wr.status == 200:
                                        _wd = await _wr.json()
                                        guild_name = _wd.get('guild', {}).get('name') if isinstance(_wd.get('guild'), dict) else None
                                        channel_name = _wd.get('channel', {}).get('name') if isinstance(_wd.get('channel'), dict) else None
                                        # Try name from webhook response
                                        server_name = _wd.get('name', '')
                                        if guild_name:
                                            server_name = guild_name
                                        user.discord_server_name = server_name or 'Discord'
                                        # Save guild_id and channel_id for building links
                                        user.discord_guild_id = str(_wd.get('guild_id', '')) if _wd.get('guild_id') else (_wd.get('guild', {}).get('id') if isinstance(_wd.get('guild'), dict) else None)
                                        user.discord_channel_id = str(_wd.get('channel_id', '')) if _wd.get('channel_id') else (_wd.get('channel', {}).get('id') if isinstance(_wd.get('channel'), dict) else None)
                                    else:
                                        user.discord_server_name = 'Discord'
                                        user.discord_guild_id = None
                                        user.discord_channel_id = None
                        except Exception:
                            user.discord_server_name = 'Discord'
                            user.discord_guild_id = None
                            user.discord_channel_id = None
                    else:
                        user.discord_server_name = None
                        user.discord_guild_id = None
                        user.discord_channel_id = None

                session_db.commit()
                logger.info(f"[API PROFILE POST] Profile updated for user {user_id}")

                # Background normalization for cross-language matching (non-blocking)
                try:
                    from ai_integration.utils import normalize_profile_background
                    import asyncio as _asyncio
                    _asyncio.ensure_future(normalize_profile_background(user.id))
                    logger.info(f"[API PROFILE POST] Background normalization scheduled for user {user_id}")
                except Exception as norm_err:
                    logger.warning(f"[API PROFILE POST] Failed to schedule normalization: {norm_err}")

                return web.json_response({'success': True, 'message': 'Profile updated'})
            finally:
                session_db.close()
        except Exception as e:
            logger.error(f"Error updating profile: {e}", exc_info=True)
            return web.json_response({'error': 'Internal server error'}, status=500)

    # Get fresh data from database (убрали кеширование для мгновенного обновления)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)

        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()

        # Determine viewer language for field translation
        # Accept ?lang= query param from client-side language switch
        viewer_lang = request.query.get('lang') or (user.language if hasattr(user, 'language') and user.language else 'ru')
        if viewer_lang not in ('ru', 'en'):
            viewer_lang = 'ru'

        def _pick_own(field_name):
            """Pick translated or original profile field based on user language."""
            if not profile:
                return None
            original = getattr(profile, field_name, None)
            if not original:
                return None
            if viewer_lang == 'en':
                return getattr(profile, f'{field_name}_normalized', None) or original
            else:
                return getattr(profile, f'{field_name}_normalized_ru', None) or original

        profile_data = {
            'username': user.username,
            'telegram_channel': user.telegram_channel,
            'discord_webhook': user.discord_webhook if hasattr(user, 'discord_webhook') else None,
            'city': _pick_own('city'),
            'birthdate': profile.birthdate if profile else None,
            'zodiac_sign': profile.zodiac_sign if profile else None,
            'company': _pick_own('company'),
            'position': _pick_own('position'),
            'goals': _pick_own('goals'),
            'skills': _pick_own('skills'),
            'interests': _pick_own('interests'),
            'languages': profile.languages if profile else None,
            'bio': _pick_own('bio'),
            'status_text': _pick_own('status_text'),
            'average_rating': profile.average_rating if profile else 0,
            'rating_count': profile.rating_count if profile else 0,
            'country': _pick_own('country') if profile and hasattr(profile, 'country') else None,
            'email': user.email if hasattr(user, 'email') else None,
            'phone': user.phone if hasattr(user, 'phone') else None,
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
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
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
            'token_balance': user.token_balance or 0,
            'referral_balance': user.referral_balance,
            'timezone': user.timezone or 'UTC',
            'telegram_linked': user.telegram_id > 0,
            'telegram_username': user.username if user.telegram_id > 0 else '',
            'discord_linked': bool(user.discord_id),
            'discord_username': ('@' + (user.discord_username or user.username or user.first_name or '')) if user.discord_id else '',
            'telegram_channel': user.telegram_channel or None,
            'discord_server_name': user.discord_server_name if hasattr(user, 'discord_server_name') else None,
            'discord_guild_id': str(user.discord_guild_id) if hasattr(user, 'discord_guild_id') and user.discord_guild_id else None,
            'discord_channel_id': str(user.discord_channel_id) if hasattr(user, 'discord_channel_id') and user.discord_channel_id else None,
            'gmail_linked': bool(getattr(user, 'google_oauth_token', None)),
            'gmail_email': (lambda t: (json.loads(t).get('email','') if t else ''))(getattr(user,'google_oauth_token',None) or ''),
        }

        # Добавляем данные активного агента для аватара в чате (focused/first)
        # + список всех активных агентов для бара в чате
        try:
            from ai_integration.user_agents import get_user_active_agent as _gaa_p, get_user_active_agents as _gaas_p
            from models import UserAgent as _UA_p
            _active_aid = _gaa_p(user_id)  # fresh session — identity map кэш session_db стал бы устаревшим
            if _active_aid:
                _ag_p = session_db.query(_UA_p).filter_by(id=_active_aid).first()
                response_data['active_agent'] = {
                    'id': _ag_p.id,
                    'name': _ag_p.name or '',
                    'avatar_url': _ag_p.avatar_url or '',
                } if _ag_p else None
            else:
                response_data['active_agent'] = None
            # Все активные агенты
            _all_aids = _gaas_p(user_id)  # fresh session
            _agents_list = []
            for _aid in _all_aids:
                _ag = session_db.query(_UA_p).filter_by(id=_aid).first()
                if _ag:
                    _agents_list.append({
                        'id': _ag.id,
                        'name': _ag.name or '',
                        'avatar_url': _ag.avatar_url or '',
                        'job_title': _ag.job_title or '',
                    })
            response_data['active_agents'] = _agents_list
        except Exception:
            response_data['active_agent'] = None
            response_data['active_agents'] = []

        return web.json_response(response_data)
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)
    finally:
        session_db.close()


async def telegram_unlink_handler(request):
    """Отвязывает Telegram от текущего пользователя"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            if not user.discord_id:
                return web.json_response({'error': 'Нельзя отвязать единственный аккаунт'}, status=400)
            user.telegram_id = -user.discord_id  # Переводим на pseudo telegram_id
            user.username = None
            session_db.commit()
            session['user_id'] = user.telegram_id
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Telegram unlink error: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def discord_unlink_handler(request):
    """Отвязывает Discord аккаунт от текущего пользователя"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id') if session else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)
            if not user.discord_id:
                return web.json_response({'error': 'Discord not linked'}, status=400)
            user.discord_id = None
            user.discord_username = None
            session_db.commit()
            return web.json_response({'ok': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"Discord unlink error: {e}")
        return web.json_response({'error': 'Internal server error'}, status=500)


async def extend_subscription_handler(request):
    """Перенаправление на страницу пополнения токенов"""
    return web.HTTPFound('/subscription-tiers')


# ── Gmail OAuth2 handlers ────────────────────────────────────────────────────

async def gmail_oauth_redirect(request):
    """GET /oauth/gmail — редиректим пользователя на Google Consent Screen."""
    user_id = await get_user_id_from_request(request)
    if not user_id:
        return web.HTTPFound('/?next=/oauth/gmail')
    from config import GOOGLE_CLIENT_ID as _GCI, WEB_APP_URL as _WAU
    if not _GCI:
        return web.Response(text="Google OAuth not configured (GOOGLE_CLIENT_ID missing)", status=501)
    import urllib.parse as _up
    params = {
        'client_id': _GCI,
        'redirect_uri': f"{_WAU}/oauth/gmail/callback",
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/gmail.send email profile',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': str(user_id),
    }
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + _up.urlencode(params)
    return web.HTTPFound(url)


async def gmail_oauth_callback(request):
    """GET /oauth/gmail/callback — обмениваем code на токены и сохраняем."""
    code = request.rel_url.query.get('code')
    state = request.rel_url.query.get('state', '')
    error = request.rel_url.query.get('error', '')

    if error or not code:
        logger.warning(f"Gmail OAuth callback error: {error}")
        return web.HTTPFound('/dashboard?gmail_auth=error')

    try:
        from config import GOOGLE_CLIENT_ID as _GCI, GOOGLE_CLIENT_SECRET as _GCS, WEB_APP_URL as _WAU
        import json as _jsn

        async with aiohttp.ClientSession() as http:
            # Обмен code → tokens
            token_resp = await http.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': _GCI,
                    'client_secret': _GCS,
                    'redirect_uri': f"{_WAU}/oauth/gmail/callback",
                    'grant_type': 'authorization_code',
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
            token_data = await token_resp.json()
            if 'error' in token_data:
                logger.error(f"Gmail OAuth token error: {token_data}")
                return web.HTTPFound('/dashboard?gmail_auth=error')

            access_token = token_data.get('access_token', '')
            refresh_token = token_data.get('refresh_token', '')

            # Получаем email пользователя
            ui_resp = await http.get(
                'https://www.googleapis.com/oauth2/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            userinfo = await ui_resp.json()
            gmail_email = userinfo.get('email', '')

        # Сохраняем в БД
        session_db = Session()
        try:
            user = None
            if state and state.lstrip('-').isdigit():
                user = session_db.query(User).filter_by(telegram_id=int(state)).first()
            if not user:
                web_sess = await get_session(request)
                uid = web_sess.get('user_id')
                if uid:
                    user = session_db.query(User).filter_by(telegram_id=uid).first()
            if user:
                import datetime as _dt_oa
                token_json = _jsn.dumps({
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'email': gmail_email,
                    'saved_at': _dt_oa.datetime.utcnow().isoformat(),
                })
                user.google_oauth_token = token_json
                session_db.commit()
                logger.info(f"✅ Gmail OAuth saved for user {user.id}: {gmail_email}")
        finally:
            session_db.close()

        return web.HTTPFound('/dashboard?gmail_auth=ok')
    except Exception as e:
        logger.exception(f"Gmail OAuth callback exception: {e}")
        return web.HTTPFound('/dashboard?gmail_auth=error')


async def gmail_oauth_status(request):
    """GET /api/oauth/gmail/status — проверяем подключён ли Gmail."""
    user_id = await get_user_id_from_request(request)
    if not user_id:
        return web.json_response({'connected': False})
    import json as _jsn
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if user and user.google_oauth_token:
            td = _jsn.loads(user.google_oauth_token)
            return web.json_response({'connected': True, 'email': td.get('email', '')})
        return web.json_response({'connected': False})
    finally:
        session_db.close()


async def gmail_oauth_disconnect(request):
    """POST /api/oauth/gmail/disconnect — удаляем токены Gmail."""
    user_id = await get_user_id_from_request(request)
    if not user_id:
        return web.json_response({'ok': False}, status=401)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            user.google_oauth_token = None
            session_db.commit()
        return web.json_response({'ok': True})
    finally:
        session_db.close()


# ── Geo detection for payment method selection ──────────────────────────────
_geo_cache: dict = {}
CIS_COUNTRIES = {'RU', 'BY', 'KZ', 'UA', 'UZ', 'AZ', 'AM', 'GE', 'TJ', 'TM', 'KG', 'MD'}

async def get_country_by_ip(ip: str) -> str:
    if ip in _geo_cache:
        return _geo_cache[ip]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://ip-api.com/json/{ip}?fields=countryCode",
                timeout=aiohttp.ClientTimeout(total=2)
            ) as r:
                data = await r.json()
                code = data.get("countryCode", "XX")
    except Exception:
        code = "XX"
    _geo_cache[ip] = code
    return code


async def get_payment_flags(request) -> dict:
    """Return show_yookassa / show_crypto based on user geo"""
    ip = request.headers.get("X-Forwarded-For", request.remote or "")
    ip = ip.split(",")[0].strip()
    country = await get_country_by_ip(ip)
    is_cis = country in CIS_COUNTRIES or country == "XX"
    return {
        "show_yookassa": is_cis,
        "show_crypto": not is_cis and bool(NOWPAYMENTS_API_KEY),
    }


@aiohttp_jinja2.template('subscription_tiers.html')
async def subscription_tiers_handler(request):
    """Страца выбора тарифа подписки"""
    lang = request.match_info.get('lang', 'ru')
    if lang not in ('ru', 'en'):
        lang = 'ru'
    flags = await get_payment_flags(request)
    return {'lang': lang, **flags}


async def faq_handler(request):
    """FAQ страница для AI SEO — AI-поиск цитирует ответы"""
    lang = request.match_info.get('lang', 'ru')
    if lang not in ('ru', 'en'):
        lang = 'ru'
    return aiohttp_jinja2.render_template('faq.html', request, {'lang': lang})


async def arena_public_handler(request):
    """Публичная страница Арены AI — доступна без авторизации"""
    return aiohttp_jinja2.render_template('arena_public.html', request, {})


async def privacy_handler(request):
    """Страница соглашения об обработке персональных данных"""
    return aiohttp_jinja2.render_template('personal_data_consent.html', request, {})

async def terms_handler(request):
    """Страница правил использования и отказа от ответственности"""
    return aiohttp_jinja2.render_template('terms.html', request, {})


async def create_payment_handler(request):
    """Создает платеж для пакета токенов или тарифа"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    logger.info(f"Create payment handler called with user_id: {user_id}")

    if not user_id:
        session_obj['next_url'] = '/subscription-tiers'
        logger.warning("No user_id in session, saving next_url and redirecting to login")
        return web.HTTPFound('/')

    # Support both new token packs (?pack=small) and legacy tiers (?tier=light)
    pack = request.query.get('pack')
    tier = request.query.get('tier', 'light')

    try:
        from payments import create_payment, TOKEN_PACK_PRICES

        if pack and pack in ('small', 'medium', 'large'):
            # Token pack purchase
            pack_key = f'tokens_{pack}'
            pack_info = TOKEN_PACK_PRICES[pack_key]
            amount = pack_info['price']
            tokens = pack_info['tokens']
            description = f"Пополнение ASI Biont — {tokens} токенов"

            logger.info(f"Creating token pack payment: pack={pack}, amount={amount}, tokens={tokens}, user_id={user_id}")

            payment_url = create_payment(
                amount=str(amount),
                description=description,
                user_id=user_id,
                tier=pack_key
            )
        else:
            # Legacy tier payments removed — only token packs supported
            logger.warning(f"Legacy tier payment attempted: tier={tier}, user_id={user_id}. Redirecting to tokens.")
            # Fallback to small token pack
            pack_key = 'tokens_small'
            pack_info = TOKEN_PACK_PRICES[pack_key]
            amount = pack_info['price']
            tokens = pack_info['tokens']
            description = f"Пополнение ASI Biont — {tokens} токенов"

            payment_url = create_payment(
                amount=str(amount),
                description=description,
                user_id=user_id,
                tier=pack_key
            )

        logger.info(f"Payment URL created: {payment_url}")
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return web.Response(text='Ошибка создания платежа. Попробуйте позже.', status=500)


async def create_crypto_payment_handler(request):
    """Create NowPayments (USDT) invoice for international users"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')
    pack = request.query.get('pack')
    if pack not in ('small', 'medium', 'large'):
        return web.HTTPFound('/subscription-tiers')
    if not user_id:
        session_obj['next_url'] = '/subscription-tiers'
        return web.HTTPFound('/')
    if not NOWPAYMENTS_API_KEY:
        return web.Response(text='Crypto payments not configured.', status=503)
    try:
        from crypto_payments import create_crypto_payment
        from config import WEB_APP_URL
        payment_url = await create_crypto_payment(pack, int(user_id), NOWPAYMENTS_API_KEY, WEB_APP_URL)
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"[NOWPAYMENTS] Error creating payment: {e}")
        return web.Response(text='Payment error. Please try again later.', status=500)


async def nowpayments_webhook(request):
    """NowPayments IPN webhook — credits tokens on successful crypto payment"""
    try:
        payload_bytes = await request.read()
        signature = request.headers.get('x-nowpayments-sig', '')

        # Verify HMAC signature (mandatory when IPN secret is configured)
        data = json.loads(payload_bytes)
        if NOWPAYMENTS_IPN_SECRET:
            if not signature:
                logger.warning('[NOWPAYMENTS] Missing webhook signature')
                return web.Response(text='Missing signature', status=400)
            from crypto_payments import verify_nowpayments_signature
            sorted_payload = json.dumps(dict(sorted(data.items())), separators=(',', ':'))
            if not verify_nowpayments_signature(sorted_payload, signature, NOWPAYMENTS_IPN_SECRET):
                logger.warning('[NOWPAYMENTS] Invalid webhook signature')
                return web.Response(text='Invalid signature', status=400)

        status = data.get('payment_status', '')
        if status not in ('finished', 'confirmed'):
            return web.Response(text='OK')

        order_id = data.get('order_id', '')
        payment_id = str(data.get('payment_id', ''))
        parts = order_id.split('_')
        if len(parts) < 2:
            logger.warning(f'[NOWPAYMENTS] Bad order_id: {order_id}')
            return web.Response(text='OK')

        tg_user_id = int(parts[0])
        pack = parts[1]

        from crypto_payments import CRYPTO_PACK_PRICES
        pack_info = CRYPTO_PACK_PRICES.get(pack)
        if not pack_info:
            return web.Response(text='OK')
        tokens_to_add = pack_info['tokens']

        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=tg_user_id).first()
            if not user:
                return web.Response(text='OK')

            existing = session.query(PaymentHistory).filter_by(payment_id=payment_id).first()
            if existing:
                logger.info(f'[NOWPAYMENTS] Duplicate webhook for {payment_id}, skipping')
                return web.Response(text='OK')

            from token_service import add_tokens
            result = add_tokens(tg_user_id, tokens_to_add, reason='purchase', session=session)
            logger.info(f'[NOWPAYMENTS] Credited {tokens_to_add} tokens to user {tg_user_id}')

            history = PaymentHistory(
                user_id=user.id,
                telegram_username=user.username,
                action='token_purchase',
                tier='LIGHT',
                amount=str(data.get('price_amount', 0)),
                payment_id=payment_id,
                duration_days=0,
                start_date=datetime.now(pytz.UTC),
                end_date=datetime.now(pytz.UTC),
                details=json.dumps({
                    'type': 'crypto_purchase',
                    'pack': pack,
                    'tokens_added': tokens_to_add,
                    'balance_after': result.get('balance', 0),
                    'payment_method': 'nowpayments',
                    'currency': data.get('pay_currency'),
                    'status': status,
                })
            )
            session.add(history)
            session.commit()

            if bot:
                try:
                    await bot.send_message(
                        tg_user_id,
                        f"✅ Crypto payment confirmed!\n\n"
                        f"➕ Added: {tokens_to_add} tokens\n"
                        f"💰 Balance: {result.get('balance', 0)} tokens"
                    )
                except Exception as e:
                    logger.warning(f'[NOWPAYMENTS] Could not notify user: {e}')
        except Exception as e:
            session.rollback()
            logger.error(f'[NOWPAYMENTS] DB error: {e}')
        finally:
            session.close()

        return web.Response(text='OK')
    except Exception as e:
        logger.error(f'[NOWPAYMENTS] Webhook error: {e}')
        return web.Response(text='OK')


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
        return web.json_response({'error': 'Internal server error'}, status=500)


async def add_test_users_handler(request):
    """Add test users with different tiers and interests (admin only)"""
    try:
        # Security check
        admin_secret = request.query.get('secret', '')
        expected_secret = os.getenv('ADMIN_SECRET')
        
        if not admin_secret or admin_secret != expected_secret:
            return web.json_response({'error': 'Unauthorized'}, status=403)
        
        session = Session()
        
        # Данные пользователей (тарифы убраны, все на токенах)
        sport_users = [
            {'username': 'sport_alex', 'telegram_id': 1000001, 'interests': 'футбол, баскетбол, олейбол', 'tier': 'LIGHT'},
            {'username': 'sport_maria', 'telegram_id': 1000002, 'interests': 'бег, йога, пилатес', 'tier': 'LIGHT'},
            {'username': 'sport_ivan', 'telegram_id': 1000003, 'interests': 'теис, плаае, елоспорт', 'tier': 'LIGHT'},
            {'username': 'sport_olga', 'telegram_id': 1000004, 'interests': 'фитс, кроссфит, бодибилди', 'tier': 'LIGHT'},
            {'username': 'sport_dmitry', 'telegram_id': 1000005, 'interests': 'хоккей, биатлон, лыжи', 'tier': 'LIGHT'},
        ]
        
        business_users = [
            {'username': 'biz_anna', 'telegram_id': 2000001, 'interests': 'стартапы, маркети, продажи', 'tier': 'LIGHT'},
            {'username': 'biz_sergey', 'telegram_id': 2000002, 'interests': 'иестиции, финсы, криптоалюта', 'tier': 'LIGHT'},
            {'username': 'biz_elena', 'telegram_id': 2000003, 'interests': 'упралее проектами, agile, scrum', 'tier': 'LIGHT'},
            {'username': 'biz_maxim', 'telegram_id': 2000004, 'interests': 'e-commerce, оайн-торголя, логистика', 'tier': 'LIGHT'},
            {'username': 'biz_victoria', 'telegram_id': 2000005, 'interests': 'HR, рекрути, обучее персола', 'tier': 'LIGHT'},
        ]
        
        all_users = sport_users + business_users
        added = []
        skipped = []

        # Batch-load existing users to avoid N+1
        _seed_tids = [u['telegram_id'] for u in all_users]
        _seed_existing = {u.telegram_id: u for u in session.query(User).filter(User.telegram_id.in_(_seed_tids)).all()}
        # Batch-load existing subscriptions for those users
        _seed_existing_uids = [u.id for u in _seed_existing.values()]
        _seed_subs = {s.user_id: s for s in (
            session.query(Subscription).filter(Subscription.user_id.in_(_seed_existing_uids)).all()
            if _seed_existing_uids else []
        )}

        for user_data in all_users:
            existing_user = _seed_existing.get(user_data['telegram_id'])
            
            if existing_user:
                # Проверяем есть ли subscription (из batch-карты)
                existing_sub = _seed_subs.get(existing_user.id)
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
                    added.append(f"@{user_data['username']} (subscription only - {user_data['tier']})")
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
            
            added.append(f"@{user_data['username']} ({user_data['tier']})")
        
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
        return web.json_response({'error': 'Internal server error'}, status=500)


async def admin_invite_handler(request):
    """Send beta-invite email to a specified address (admin only).

    GET /admin/invite?secret=ADMIN_SECRET&email=user@example.com
    """
    try:
        admin_secret = request.query.get('secret', '')
        expected_secret = os.getenv('ADMIN_SECRET')
        if not admin_secret or admin_secret != expected_secret:
            return web.json_response({'error': 'Unauthorized'}, status=403)

        email = request.query.get('email', '').strip()
        if not email or '@' not in email:
            return web.json_response({'error': 'email parameter required'}, status=400)

        base_url = os.getenv('BASE_URL', 'https://asibiont.com')
        register_url = f"{base_url}/"

        subject = "Приглашение на тестирование ASI Biont"
        body = (
            f"Здравствуйте!\n\n"
            f"Вы приглашены для тестирования AI-агента ASI Biont.\n\n"
            f"ASI Biont — это персональный AI-ассистент для управления задачами, "
            f"целями и коммуникациями через Telegram-бота и веб-интерфейс.\n\n"
            f"Для начала работы:\n"
            f"1. Перейдите на {register_url}\n"
            f"2. Войдите через Telegram (@asibiont_bot) или создайте аккаунт по email\n"
            f"3. Напишите боту свои первые задачи или цели — агент разберётся сам\n\n"
            f"Ваши отзывы очень важны для нас. Буду рад любым комментариям в ответ "
            f"на это письмо.\n\n"
            f"С уважением,\n"
            f"Команда ASI Biont"
        )

        await send_email(email, subject, body)
        logger.info(f"[INVITE] Beta invite sent to {email}")
        return web.json_response({'success': True, 'sent_to': email})

    except Exception as e:
        logger.error(f"[INVITE] Failed to send invite to {request.query.get('email')}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def withdraw_handler(request):
    """Handle referral balance withdrawal request"""
    try:
        user_id = await get_user_id_from_request(request)
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        data = await request.json()
        card = data.get('card', '').strip()
        amount = data.get('amount')

        if not card or len(card) < 16:
            return web.json_response({'error': 'Некорректный номер карты'}, status=400)

        try:
            amount = int(amount)
        except (ValueError, TypeError):
            return web.json_response({'error': 'Некорректная сумма'}, status=400)

        if amount < 100:
            return web.json_response({'error': 'Минимальная сумма вывода: 100 токенов'}, status=400)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'Пользователь не найден'}, status=404)

            if (user.referral_balance or 0) < amount:
                return web.json_response({'error': 'Недостаточно средств'}, status=400)

            # Маскируем карту для логов
            masked_card = card[:4] + ' **** **** ' + card[-4:]

            # Уведомляем админа через Telegram
            from config import DEVELOPER_CHAT_ID
            if DEVELOPER_CHAT_ID and bot:
                admin_msg = (
                    f"💸 Заявка на вывод\n"
                    f"👤 @{user.username or user.telegram_id}\n"
                    f"💰 Сумма: {amount} токенов ({amount} руб)\n"
                    f"💳 Карта: {masked_card}\n"
                    f"📊 Баланс: {user.referral_balance or 0} токенов"
                )
                try:
                    await bot.send_message(int(DEVELOPER_CHAT_ID), admin_msg)
                except Exception as e:
                    logger.error(f"Failed to notify admin about withdraw: {e}")

            logger.info(f"[WITHDRAW] User @{user.username} requested {amount} tokens to card {masked_card}")

            return web.json_response({'success': True})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error in withdraw_handler: {e}")
        return web.json_response({'error': 'Ошибка сервера'}, status=500)


# ═══════════════════════════════════════════════════════════════════
# Resend Webhook — входящие ответы на email-кампании + статус-события
# ═══════════════════════════════════════════════════════════════════

async def resend_webhook_handler(request):
    """Обрабатывает webhooks от Resend API:
    - email.delivered — обновляем статус
    - email.opened — обновляем статус
    - email.bounced — помечаем ошибку
    - email.complained — помечаем жалобу
    - email.received (inbound reply) — сохраняем текст ответа, агент автономно отвечает через якорь
    """
    try:
        raw_body = await request.text()
        logger.info(f"[RESEND_WEBHOOK] Raw body (first 2000): {raw_body[:2000]}")
        import json as _json
        data = _json.loads(raw_body)
        event_type = data.get('type', '')
        payload = data.get('data', {})

        # For inbound emails that come without wrapper (no type/data nesting)
        if not event_type and 'from' in data:
            payload = data
            event_type = 'email.received'

        logger.info(f"[RESEND_WEBHOOK] Event: {event_type}, payload keys: {list(payload.keys())}, data keys: {list(data.keys())}")

        session_db = Session()
        try:
            # --- Tracking events (delivered, opened, bounced, complained) ---
            if event_type in ('email.delivered', 'email.opened', 'email.bounced', 'email.complained'):
                email_id = payload.get('email_id', '')
                if email_id:
                    from models import EmailOutreach, EmailCampaign
                    outreach = session_db.query(EmailOutreach).filter_by(resend_id=email_id).first()
                    if outreach:
                        status_map = {
                            'email.delivered': 'delivered',
                            'email.opened': 'opened',
                            'email.bounced': 'bounced',
                            'email.complained': 'failed',
                        }
                        new_status = status_map.get(event_type, outreach.status)
                        status_priority = {'draft': 0, 'sent': 1, 'delivered': 2, 'opened': 3, 'replied': 4, 'bounced': 5, 'failed': 5}
                        if status_priority.get(new_status, 0) > status_priority.get(outreach.status, 0):
                            outreach.status = new_status
                            session_db.commit()
                            logger.info(f"[RESEND_WEBHOOK] Updated outreach #{outreach.id} → {new_status}")

                        if event_type == 'email.bounced':
                            campaign = session_db.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
                            if campaign:
                                pass

            # --- Inbound email (reply) ---
            elif event_type == 'email.received' or 'from' in payload:
                # Resend webhook does NOT include email body — must fetch via API
                email_id = payload.get('email_id', '') or data.get('email_id', '')
                raw_from = payload.get('from', '') or data.get('from', '')
                subject = payload.get('subject', '') or data.get('subject', '')
                text_body = payload.get('text', '') or payload.get('html', '') or data.get('text', '') or data.get('html', '')
                
                logger.info(f"[RESEND_WEBHOOK] Inbound: email_id={email_id}, raw_from={raw_from}, subject={subject[:80] if subject else ''}, has_body={bool(text_body)}")

                # Always try to fetch body from Resend API for inbound emails
                if email_id:
                    try:
                        from config import RESEND_RECEIVING_API_KEY
                        if RESEND_RECEIVING_API_KEY:
                            import aiohttp as _aiohttp
                            async with _aiohttp.ClientSession() as http:
                                r = await http.get(
                                    f'https://api.resend.com/emails/receiving/{email_id}',
                                    headers={'Authorization': f'Bearer {RESEND_RECEIVING_API_KEY}'},
                                    timeout=_aiohttp.ClientTimeout(total=10),
                                )
                                resp_text = await r.text()
                                logger.info(f"[RESEND_WEBHOOK] Receiving API status={r.status}, body (first 500): {resp_text[:500]}")
                                if r.status == 200:
                                    import json as _json2
                                    rec = _json2.loads(resp_text)
                                    fetched_body = rec.get('text') or rec.get('html') or ''
                                    if fetched_body:
                                        text_body = fetched_body
                                    if not raw_from:
                                        raw_from = rec.get('from', '')
                                    if not subject:
                                        subject = rec.get('subject', '')
                                elif r.status == 401:
                                    logger.warning(f"[RESEND_WEBHOOK] Receiving API 401 — API key restricted to sending only. Set RESEND_RECEIVING_API_KEY env var with a full-access key.")
                                else:
                                    logger.warning(f"[RESEND_WEBHOOK] Receiving API returned {r.status}")
                    except Exception as e:
                        logger.warning(f"[RESEND_WEBHOOK] Failed to fetch received email body: {e}")

                if isinstance(raw_from, dict):
                    from_email = raw_from.get('email', '') or raw_from.get('address', '')
                elif isinstance(raw_from, list) and raw_from:
                    first = raw_from[0]
                    from_email = first.get('email', '') if isinstance(first, dict) else str(first)
                elif isinstance(raw_from, str) and '<' in raw_from and '>' in raw_from:
                    import re as _re
                    match = _re.search(r'<([^>]+)>', raw_from)
                    from_email = match.group(1) if match else raw_from
                else:
                    from_email = str(raw_from or '')
                from_email = from_email.strip().lower()

                if text_body and '<' in text_body and '>' in text_body:
                    import re as _re
                    text_body = _re.sub(r'<[^>]+>', '', text_body).strip()

                # --- Отрезаем цитату оригинального письма (строки «> ...» и заголовок «Чт, ...») ---
                if text_body:
                    import re as _re_q
                    lines = text_body.split('\n')
                    clean_lines = []
                    for _ln in lines:
                        if _ln.strip().startswith('>'):
                            break  # всё после первой цитируемой строки — мусор
                        clean_lines.append(_ln)
                    clean_body = '\n'.join(clean_lines).strip()
                    # Убираем финальный заголовок «Пн/Вт/.../Чт, DD мес YYYY г. в HH:MM, Имя:»
                    clean_body = _re_q.sub(
                        r'\n+(?:Пн|Вт|Ср|Чт|Пт|Сб|Вс|On\s)[^\n]{0,200}$',
                        '', clean_body, flags=_re_q.DOTALL
                    ).strip()
                    if clean_body:
                        text_body = clean_body

                to_raw = payload.get('to', '') or data.get('to', '')
                to_email = to_raw[0] if isinstance(to_raw, list) and to_raw else str(to_raw)
                logger.info(f"[RESEND_WEBHOOK] Parsed: from_email={from_email}, to={to_email}, subject={subject[:80] if subject else ''}, body_len={len(text_body or '')}")

                if from_email:
                    from models import EmailOutreach, EmailCampaign
                    from sqlalchemy import func
                    
                    # Проверим все outreach для этого email
                    all_outreach = session_db.query(EmailOutreach).filter(
                        func.lower(EmailOutreach.recipient_email) == from_email,
                    ).order_by(EmailOutreach.sent_at.desc()).all()
                    logger.info(f"[RESEND_WEBHOOK] Found {len(all_outreach)} total outreach records for {from_email}: {[(o.id, o.status, o.recipient_email) for o in all_outreach[:5]]}")
                    
                    outreach = session_db.query(EmailOutreach).filter(
                        func.lower(EmailOutreach.recipient_email) == from_email,
                        EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
                    ).order_by(EmailOutreach.sent_at.desc()).first()

                    if not outreach:
                        logger.warning(f"[RESEND_WEBHOOK] No outreach found for {from_email}, trying broader search...")
                        outreach = session_db.query(EmailOutreach).filter(
                            func.lower(EmailOutreach.recipient_email) == from_email,
                        ).order_by(EmailOutreach.sent_at.desc()).first()
                        if outreach:
                            logger.info(f"[RESEND_WEBHOOK] Found outreach #{outreach.id} with status={outreach.status} via broader search")

                    if outreach:
                        was_replied = outreach.status == 'replied'
                        outreach.status = 'replied'
                        if outreach.reply_text:
                            outreach.reply_text = (outreach.reply_text + '\n\n--- ' + datetime.now(dt_timezone.utc).strftime('%d.%m.%Y %H:%M') + ' ---\n' + (text_body or ''))[:5000]
                        else:
                            outreach.reply_text = (text_body or '') or None
                        outreach.reply_at = datetime.now(dt_timezone.utc)
                        if not was_replied:
                            campaign = session_db.query(EmailCampaign).filter_by(id=outreach.campaign_id).first()
                            if campaign:
                                campaign.emails_replied = (campaign.emails_replied or 0) + 1
                        session_db.commit()
                        logger.info(f"[RESEND_WEBHOOK] Reply saved for outreach #{outreach.id} from {from_email} (was_replied={was_replied})")

                        # Уведомим пользователя через TG если возможно
                        _reply_user = None
                        try:
                            _reply_user = session_db.query(User).filter_by(id=outreach.user_id).first()
                            if _reply_user and _reply_user.telegram_id:
                                tg_text = (
                                    f"📩 Новый ответ на email-кампанию!\n\n"
                                    f"От: {from_email}\n"
                                    f"Тема: {subject[:100]}\n"
                                    f"Текст: {text_body[:300]}{'...' if len(text_body) > 300 else ''}\n\n"
                                    f"Агент автоматически подготовит ответ в рамках цели кампании."
                                )
                                from config import BOT_TOKEN
                                if BOT_TOKEN:
                                    import aiohttp as _aiohttp
                                    async with _aiohttp.ClientSession() as http:
                                        await http.post(
                                            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
                                            json={'chat_id': _reply_user.telegram_id, 'text': tg_text, 'parse_mode': 'HTML'},
                                            timeout=_aiohttp.ClientTimeout(total=10),
                                        )
                        except Exception as e:
                            logger.warning(f"[RESEND_WEBHOOK] Failed to notify user via TG: {e}")

                        # Немедленно запускаем anchor engine — не ждём следующего цикла
                        try:
                            from anchor_engine import get_anchor_engine as _get_engine
                            _engine = _get_engine()
                            if _engine and _reply_user and _reply_user.telegram_id:
                                asyncio.create_task(_engine._process_user(_reply_user.telegram_id))
                                logger.info(f"[RESEND_WEBHOOK] Triggered anchor engine for user {_reply_user.telegram_id}")
                        except Exception as _ae:
                            logger.warning(f"[RESEND_WEBHOOK] Failed to trigger anchor engine: {_ae}")
                    else:
                        logger.info(f"[RESEND_WEBHOOK] No matching outreach for reply from {from_email}")

        finally:
            session_db.close()

        return web.json_response({'status': 'ok'})

    except Exception as e:
        logger.error(f"[RESEND_WEBHOOK] Error: {e}", exc_info=True)
        return web.json_response({'status': 'error', 'message': str(e)}, status=500)


# ═══════════════════════════════════════════════════════
# MARKETPLACE API
# ═══════════════════════════════════════════════════════

async def api_marketplace_agents_handler(request):
    """GET /api/marketplace/agents — список активных агентов"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            from models import UserAgent, AgentSubscription, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            category = request.rel_url.query.get('category')
            search = request.rel_url.query.get('search', '').strip()
            q = session_db.query(UserAgent).filter(
                UserAgent.status.in_(['active', 'paused']),
                UserAgent.is_private.isnot(True)
            )
            if category:
                q = q.filter(UserAgent.specialization == category)
            if search:
                q = q.filter(UserAgent.name.ilike(f'%{search}%'))
            agents = q.order_by(UserAgent.subscribers_count.desc()).limit(20).all()
            result = []
            for a in agents:
                rating = round(a.rating_sum / a.rating_count, 1) if a.rating_count else None
                is_subscribed = False
                user_rating = None
                if user_obj:
                    is_subscribed = bool(session_db.query(AgentSubscription).filter_by(
                        user_id=user_obj.id, agent_id=a.id).first())
                    from models import AgentRating as AgentRatingModel
                    ar = session_db.query(AgentRatingModel).filter_by(
                        rater_user_id=user_obj.id, agent_id=a.id).first()
                    if ar:
                        user_rating = ar.rating
                author_username = a.author.username if a.author and a.author.username else None
                result.append({
                    'id': a.id, 'name': a.name, 'slug': a.slug,
                    'description': a.description, 'specialization': a.specialization,
                    'job_title': a.job_title or '',
                    'avatar_url': a.avatar_url,
                    'price_per_message': a.price_per_message,
                    'trial_messages': a.trial_messages,
                    'subscribers_count': a.subscribers_count,
                    'messages_count': a.messages_count,
                    'rating': rating, 'user_rating': user_rating,
                    'is_subscribed': is_subscribed,
                    'is_adult': a.is_adult,
                    'author_username': author_username,
                    'is_owner': bool(user_obj and a.author_id == user_obj.id),
                })
            return web.json_response({'agents': result})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agents error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_agent_generate_code_handler(request):
    """POST /api/marketplace/agents/generate-code — генерация python_code агента через DeepSeek"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        description = (data.get('description') or '').strip()
        api_keys_raw = (data.get('api_keys') or '').strip()
        base_code = (data.get('base_code') or '').strip()
        if not description:
            return web.json_response({'error': 'description required'}, status=400)

        # Строим контекст доступных ключей (только имена, не значения)
        key_names = []
        for line in api_keys_raw.splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                kname = line.split('=', 1)[0].strip()
                if kname:
                    key_names.append(kname)

        keys_hint = ''
        if key_names:
            keys_hint = f'\nДоступные переменные окружения (os.environ): {", ".join(key_names)}'

        # Если передан базовый шаблон — просим ИИ модифицировать его, а не писать с нуля
        if base_code:
            prompt = f"""У тебя есть готовый рабочий Python-скрипт агента:

```python
{base_code}
```

Задача пользователя: {description}{keys_hint}

Модифицируй скрипт согласно задаче. Сохрани всю правильную структуру кода (IMAP логин, strip пробелов из пароля, try/except, reversed(), is_multipart и т.д.). Верни ТОЛЬКО итоговый Python-код без пояснений и без markdown-блоков."""
        else:
            prompt = f"""Напиши Python-скрипт для AI-агента. Скрипт выполняется перед каждым ответом агента и должен напечатать (print) актуальные данные в stdout — они попадут в контекст ИИ.

Задача агента: {description}{keys_hint}

СТРОГИЕ требования к коду:
- Только стандартная библиотека Python (os, imaplib, smtplib, json, datetime, re, math, random, collections, itertools, time, hashlib, base64, urllib.request, urllib.parse, email, email.header, email.message)
- НЕ используй: requests, httpx, aiohttp, subprocess, shutil, ctypes, pickle, eval, exec, open(), pathlib, glob, tempfile
- Для HTTP-запросов используй ТОЛЬКО urllib.request (не requests!)
- Ключи читать через os.environ.get('KEY_NAME', '')
- Вывод должен быть кратким и информативным (до 2000 символов)
- Обернуть в try/except, при ошибке напечатать сообщение об ошибке через print()
- Никаких интерактивных вводов, без бесконечных циклов
- Только код, без пояснений, без markdown-блоков
- НИКОГДА не используй строку 'undefined' — это JavaScript. В Python для отсутствующих значений используй '' или 'нет данных'
- Если переданы ключи нескольких сервисов — выбери ОДИН наиболее подходящий для задачи и пиши код только для него
- Для null/None значений из API всегда пиши: value or 'нет данных' (не value or 'undefined'!)

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ДЛЯ IMAP (работают с Gmail и любой почтой):

0. ЧИТАЙ ВСЕ ПАПКИ, А НЕ ТОЛЬКО INBOX. Агент должен видеть полную картину: входящие И отправленные.
   — Gmail: используй папку "[Gmail]/All Mail" — она содержит ВСЁ (входящие + отправленные + архив).
     Поиск в ней: mail.select('"[Gmail]/All Mail"', readonly=True)
     Фильтруй спам через X-GM-RAW: mail.search(None, f'(X-GM-RAW "newer_than:7d -in:spam -in:trash") SINCE {{week_ago}}')
   — Яндекс Почта: папка "INBOX" (входящие) + папка "Sent" (отправленные). Делай два прохода.
   — Mail.ru: папка "INBOX" + папка "Sent". Делай два прохода.
   — Общее правило: если папка недоступна — пропускай через try/except, не падай.

1. ПОСЛЕДНИЕ письма: mail.search возвращает id от старого к новому.
   ВСЕГДА используй: for eid in reversed(email_ids[-N:])
   НИКОГДА: email_ids[:N]  — это самые СТАРЫЕ письма!

2. Поиск SINCE (не UNSEEN): UNSEEN пропускает все прочитанные письма.

3. Subject МОЖЕТ быть None — всегда защищай:
   subj_raw = msg.get("Subject") or ""
   parts = decode_header(subj_raw)
   subject = "".join(
       p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else (p or "")
       for p, enc in parts
   )

4. Тело письма: большинство писем multipart — get_payload(decode=True) вернёт None!
   ПРАВИЛЬНЫЙ способ извлечения тела:
   body = ""
   if msg.is_multipart():
       for part in msg.walk():
           if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
               raw = part.get_payload(decode=True) or b""
               body = raw.decode(part.get_content_charset() or "utf-8", errors="replace")[:300]
               break
   else:
       raw = msg.get_payload(decode=True) or b""
       body = raw.decode(msg.get_content_charset() or "utf-8", errors="replace")[:300]

5. Каждое письмо в отдельном try/except чтобы одна ошибка не ломала весь вывод.

6. Помечай направление письма: для отправленных добавляй пометку [SENT] или «Исходящее:».

ПОЛНЫЙ ПРИМЕР правильного Gmail-агента (читает ВСЕ письма через All Mail):
import imaplib, os, datetime
from email import message_from_bytes
from email.header import decode_header

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
try:
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_PASS)
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%d-%b-%Y")
    # [Gmail]/All Mail — ВСЕ письма (входящие + отправленные + архив), без спама и корзины
    mail.select('"[Gmail]/All Mail"', readonly=True)
    _, data = mail.search(None, f'(X-GM-RAW "newer_than:7d -in:spam -in:trash") SINCE {{week_ago}}')
    email_ids = data[0].split()
    if not email_ids:
        print("Новых писем нет.")
    else:
        for eid in reversed(email_ids[-20:]):
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                msg = message_from_bytes(msg_data[0][1])
                subj_raw = msg.get("Subject") or ""
                parts = decode_header(subj_raw)
                subject = "".join(
                    p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else (p or "")
                    for p, enc in parts
                )
                direction = "[SENT]" if GMAIL_USER.lower() in (msg.get("From") or "").lower() else "[INBOX]"
                from_addr = msg.get("From", "")
                to_addr = msg.get("To", "")
                date_str = msg.get("Date", "")
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                            raw = part.get_payload(decode=True) or b""
                            body = raw.decode(part.get_content_charset() or "utf-8", errors="replace")[:200]
                            break
                else:
                    raw = msg.get_payload(decode=True) or b""
                    body = raw.decode(msg.get_content_charset() or "utf-8", errors="replace")[:200]
                print(f"{{direction}} От: {{from_addr}} Кому: {{to_addr}}\\nТема: {{subject}}\\nДата: {{date_str}}\\nТело: {{body[:200]}}\\n---")
            except Exception as e:
                print(f"Ошибка письма: {{e}}")
    mail.logout()
except Exception as e:
    print(f"Ошибка подключения: {{e}}")

Выведи ТОЛЬКО чистый Python-код, никаких комментариев до или после кода."""

        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp as _aio_h
        import json as _json_g
        async with _aio_h.ClientSession() as _sess:
            async with _sess.post(
                'https://api.deepseek.com/chat/completions',
                headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                json={'model': DEEPSEEK_MODEL, 'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': 1400, 'temperature': 0.1},
                timeout=_aio_h.ClientTimeout(total=50)
            ) as resp:
                result = await resp.json()
        code = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
        # Убираем markdown-блоки если DeepSeek их добавил
        import re as _re_gc
        code = _re_gc.sub(r'^```python\s*', '', code, flags=_re_gc.MULTILINE)
        code = _re_gc.sub(r'^```\s*', '', code, flags=_re_gc.MULTILINE).strip()
        return web.json_response({'code': code})
    except Exception as e:
        logger.error(f"[MARKETPLACE] generate-code error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_publish_agent_handler(request):
    """POST /api/marketplace/agents — создать/обновить агента"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()
        session_db = Session()
        try:
            from models import UserAgent, User as UserModel
            import re as _re
            import json as _json
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)

            agent_id = data.get('id')
            if agent_id:
                agent = session_db.query(UserAgent).filter_by(
                    id=agent_id, author_id=user_obj.id).first()
                if not agent:
                    return web.json_response({'error': 'Not found'}, status=404)
            else:
                agent = UserAgent(author_id=user_obj.id, status='review')
                session_db.add(agent)

            # Slug из имени если не задан
            name = (data.get('name') or '').strip()[:100]
            if not name:
                return web.json_response({'error': 'name required'}, status=400)
            slug = data.get('slug') or _re.sub(r'[^a-z0-9-]', '-', name.lower())[:100]
            slug = _re.sub(r'-+', '-', slug).strip('-')

            agent.name = name
            agent.slug = slug
            agent.description = (data.get('description') or '')[:1000]
            agent.specialization = data.get('specialization', 'misc')
            agent.job_title = (data.get('job_title') or '')[:200]
            agent.personality = (data.get('personality') or '')[:8000]
            agent.tools_allowed = _json.dumps(data.get('tools_allowed') or [])
            agent.knowledge_base = _json.dumps(data.get('knowledge_base') or [])
            agent.price_per_message = max(1, int(data.get('price_per_message') or 5))
            agent.trial_messages = 0  # пробные сообщения отключены
            agent.is_adult = bool(data.get('is_adult', False))
            agent.is_private = bool(data.get('is_private', False))
            agent.search_scope = (data.get('search_scope') or '').strip()[:500]
            # Новый агент создаётся в статусе 'paused' — активирует пользователь кнопкой «Запустить в чат»
            # При редактировании существующего агента статус не меняем
            if not agent_id:
                agent.status = 'paused'

            # Пользовательские API ключи
            agent.user_api_keys = (data.get('user_api_keys') or '').strip()

            # Частота проактивных уведомлений
            _notify_freq = int(data.get('notification_frequency') or 0)
            if _notify_freq > 0:
                import json as _jf
                agent.custom_anchors = _jf.dumps([{
                    'id': 'auto-notify',
                    'topic': f'Агент {agent.name or ""} пишет первым',
                    'priority': 'MEDIUM',
                    'cooldown_hours': _notify_freq,
                }], ensure_ascii=False)
            else:
                agent.custom_anchors = None

            # Python-код агента (выполняется перед генерацией ответа)
            _py_code_raw = (data.get('python_code') or '').strip()
            if _py_code_raw:
                import ast as _ast
                # ── AST-анализ: строковый blacklist тривиально обходится ──────────────
                _FORBIDDEN_MODULES = {
                    'subprocess', 'shutil', 'socket', 'requests', 'httpx', 'aiohttp',
                    'http', 'ftplib', 'telnetlib', 'asyncio', 'threading', 'multiprocessing',
                    'ctypes', 'pickle', 'marshal', 'importlib', 'runpy',
                    'pty', 'tty', 'termios', 'nis', 'pwd', 'grp', 'spwd',
                    'pathlib', 'tempfile', 'glob', 'fnmatch',
                }
                _FORBIDDEN_CALLS = {
                    'eval', 'exec', 'compile', '__import__', 'open',
                    'input', 'breakpoint', 'quit', 'exit',
                }
                _FORBIDDEN_ATTRS = {
                    '__class__', '__bases__', '__subclasses__', '__mro__',
                    '__builtins__', '__globals__', '__code__', '__closure__',
                    '__import__', '__loader__', '__spec__',
                    'system', 'popen', 'execv', 'execvp', 'execve',
                    'fork', 'forkpty', 'kill', 'killpg', 'remove', 'rmdir',
                }
                _AST_ERRORS = []
                try:
                    _tree = _ast.parse(_py_code_raw)
                    for _node in _ast.walk(_tree):
                        # Запрещённые импорты
                        if isinstance(_node, (_ast.Import, _ast.ImportFrom)):
                            if isinstance(_node, _ast.Import):
                                for _alias in _node.names:
                                    _mod = _alias.name.split('.')[0]
                                    if _mod in _FORBIDDEN_MODULES:
                                        _AST_ERRORS.append(f'import {_mod}')
                            if isinstance(_node, _ast.ImportFrom) and _node.module:
                                _mod = _node.module.split('.')[0]
                                if _mod in _FORBIDDEN_MODULES:
                                    _AST_ERRORS.append(f'import {_node.module}')
                        # Запрещённые имена функций и атрибутов
                        if isinstance(_node, _ast.Call):
                            if isinstance(_node.func, _ast.Name):
                                if _node.func.id in _FORBIDDEN_CALLS:
                                    _AST_ERRORS.append(f'вызов {_node.func.id}()')
                            if isinstance(_node.func, _ast.Attribute):
                                if _node.func.attr in _FORBIDDEN_ATTRS:
                                    _AST_ERRORS.append(f'атрибут .{_node.func.attr}')
                        # Доступ к dunders (__class__, __subclasses__ и т.д.)
                        if isinstance(_node, _ast.Attribute):
                            if _node.attr in _FORBIDDEN_ATTRS:
                                _AST_ERRORS.append(f'атрибут .{_node.attr}')
                        if isinstance(_node, _ast.Name):
                            if _node.id in _FORBIDDEN_CALLS:
                                _AST_ERRORS.append(f'имя {_node.id}')
                except SyntaxError as _se:
                    return web.json_response({'error': f'Синтаксическая ошибка в коде: {_se}'}, status=400)
                if _AST_ERRORS:
                    _uniq = list(dict.fromkeys(_AST_ERRORS))[:5]
                    return web.json_response(
                        {'error': f'Код содержит запрещённые операции: {", ".join(_uniq)}. '
                                  f'Используйте urllib.request для HTTP-запросов, os.environ для переменных среды.'},
                        status=400
                    )
            agent.python_code = _py_code_raw

            # Аватар из base64 data URL (сохраняем напрямую; в продакшене заменить на upload в CDN)
            avatar_data = (data.get('avatar_data_url') or '').strip()
            if avatar_data and avatar_data.startswith('data:image/'):
                # Обрезаем до 2МБ (safety limit)
                if len(avatar_data) < 2_800_000:
                    agent.avatar_url = avatar_data

            session_db.commit()
            session_db.refresh(agent)  # читаем актуальные данные из БД

            # Авто-подписка для нового агента (создаёт AgentSubscription + добавляет в сессию),
            # чтобы он сразу участвовал в директорском потоке без ручного «Активировать».
            if not agent_id:
                try:
                    from models import AgentSubscription as _AS_pub
                    _existing_pub = session_db.query(_AS_pub).filter_by(
                        user_id=user_obj.id, agent_id=agent.id).first()
                    if not _existing_pub:
                        session_db.add(_AS_pub(user_id=user_obj.id, agent_id=agent.id))
                        session_db.commit()
                    from ai_integration.user_agents import set_user_active_agent as _sua_pub
                    _sua_pub(user_id, agent.id)
                except Exception as _ae:
                    logger.debug("[MARKETPLACE] auto-subscribe on create error: %s", _ae)

            is_private_actual = bool(agent.is_private)
            is_private_requested = bool(data.get('is_private', False))
            privacy_warning = None
            if is_private_requested and not is_private_actual:
                privacy_warning = 'Не удалось сохранить приватность агента — пересохраните его ещё раз.'
                logger.error(f"[MARKETPLACE] is_private MISMATCH: agent {agent.id} requested private but saved as public!")
            return web.json_response({'success': True, 'id': agent.id, 'slug': agent.slug,
                                      'status': agent.status,
                                      'is_private': is_private_actual,
                                      'message': 'Агент сохранён. Нажмите «Запустить в чат» чтобы он начал писать в Арену.' if agent.status == 'paused' else 'Агент активен.',
                                      'warning': privacy_warning})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] publish agent error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_agent_test_code_handler(request):
    """POST /api/marketplace/agents/test-code — тестирует python_code агента и возвращает сырой вывод.
    Принимает либо agent_id (для сохранённых агентов), либо python_code + user_api_keys напрямую."""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        data = await request.json()

        # Режим 1: код и ключи переданы напрямую (новый агент не сохранён)
        if 'python_code' in data:
            py_code = (data.get('python_code') or '').strip()
            api_keys_raw = (data.get('user_api_keys') or '').strip()
            if not py_code:
                return web.json_response({'error': 'Нет кода для запуска'})
        else:
            # Режим 2: загружаем код из сохранённого агента по agent_id
            agent_id = data.get('agent_id')
            session_db = Session()
            try:
                from models import UserAgent, User as UserModel
                user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
                if not user_obj:
                    return web.json_response({'error': 'User not found'}, status=404)
                agent = session_db.query(UserAgent).filter_by(
                    id=agent_id, author_id=user_obj.id).first()
                if not agent:
                    return web.json_response({'error': 'Agent not found'}, status=404)
                py_code = (agent.python_code or '').strip()
                if not py_code:
                    return web.json_response({'error': 'Нет python_code у агента', 'code': ''})
                api_keys_raw = agent.user_api_keys or ''
            finally:
                session_db.close()

        # Строим env как в autonomous_agent
        import os as _os_t, sys as _sys_t, asyncio as _aio_t
        _env = {
            'PATH': _os_t.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': _os_t.environ.get('HOME', '/tmp'),
            'PYTHONIOENCODING': 'utf-8',
        }
        for _kline in api_keys_raw.splitlines():
            _kline = _kline.strip()
            if '=' in _kline and not _kline.startswith('#'):
                _k, _, _v = _kline.partition('=')
                _env[_k.strip()] = _v.strip()

        try:
            proc = await _aio_t.create_subprocess_exec(
                _sys_t.executable, '-c', py_code,
                stdout=_aio_t.subprocess.PIPE,
                stderr=_aio_t.subprocess.PIPE,
                env=_env,
            )
            stdout, stderr = await _aio_t.wait_for(proc.communicate(), timeout=15.0)
            out = stdout.decode('utf-8', errors='replace').strip()
            err = stderr.decode('utf-8', errors='replace').strip()
            # Строим маскированное резюме переменных для диагностики
            _safe_keys = {'EMAIL', 'USER', 'FROM', 'DOMAIN', 'HOST', 'URL', 'PORT', 'REGION', 'BUCKET', 'SHEET', 'ID'}
            def _mask_val(k, v):
                ku = k.upper()
                if any(s in ku for s in _safe_keys):
                    return v  # не секрет — показываем полностью
                if not v:
                    return '(не задан)'
                if len(v) <= 6:
                    return '***'
                return v[:4] + '***'
            _user_env = {k: v for k, v in _env.items() if k not in ('PATH', 'HOME', 'PYTHONIOENCODING')}
            env_summary = {k: _mask_val(k, v) for k, v in _user_env.items()}
            return web.json_response({
                'stdout': out[:3000],
                'stderr': err[:1000],
                'returncode': proc.returncode,
                'env_keys': list(_user_env.keys()),
                'env_summary': env_summary,
            })
        except _aio_t.TimeoutError:
            return web.json_response({'error': 'Тайм-аут (15 сек)', 'stdout': '', 'stderr': ''})
        except Exception as e:
            return web.json_response({'error': str(e), 'stdout': '', 'stderr': ''})
    except Exception as e:
        logger.error(f"[MARKETPLACE] test_code error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_office_activity_handler(request):
    """GET /api/office/activity — лента фоновой активности офиса (якоря agent_office_update + integration_alert)"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        # since= позволяет поллингу запрашивать только новые
        since_ts = request.rel_url.query.get('since')
        limit = min(int(request.rel_url.query.get('limit', 30)), 100)
        session_db = Session()
        try:
            import json as _json
            import datetime as _dt
            from models import Anchor, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'items': []})
            q = session_db.query(Anchor).filter(
                Anchor.user_id == user_obj.id,
                Anchor.anchor_type.in_(['agent_office_update', 'integration_alert']),
            )
            if since_ts:
                try:
                    since_dt = _dt.datetime.fromisoformat(since_ts.replace('Z', '+00:00'))
                    q = q.filter(Anchor.created_at > since_dt)
                except Exception:
                    pass
            else:
                since7 = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)
                q = q.filter(Anchor.created_at >= since7)
            rows = q.order_by(Anchor.created_at.desc()).limit(limit).all()
            items = []
            for a in rows:
                try:
                    d = _json.loads(a.data or '{}')
                except Exception:
                    d = {}
                items.append({
                    'id': a.id,
                    'type': a.anchor_type,
                    'topic': a.topic or '',
                    'priority': a.priority.value if a.priority else 'MEDIUM',
                    'created_at': a.created_at.isoformat() if a.created_at else '',
                    'data': d,
                })
            return web.json_response({'items': items})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[OFFICE ACTIVITY] error: {e}", exc_info=True)
        return web.json_response({'items': []})


async def api_agents_activity_handler(request):
    """GET /api/marketplace/agents/activity — социальная активность агентов за 30 дней"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            from models import UserAgent, ArenaPost, User as UserModel
            import datetime as _dt
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'agents': [], 'events': [], 'stats': {}})
            since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
            # Агенты пользователя
            agents = session_db.query(UserAgent).filter(
                UserAgent.author_id == user_obj.id,
                UserAgent.status.in_(['active', 'paused'])
            ).order_by(UserAgent.subscribers_count.desc()).limit(20).all()
            agent_mkt_ids = [f'mkt_{a.id}' for a in agents]
            agents_data = [{'id': a.id, 'name': a.name,
                            'subscribers_count': a.subscribers_count,
                            'messages_count': a.messages_count,
                            'avatar_url': a.avatar_url or ''} for a in agents]
            # Считаем из ArenaPost (арена пишет туда, не в AgentActivityLog)
            arena_rows = []
            if agent_mkt_ids:
                arena_rows = session_db.query(ArenaPost).filter(
                    ArenaPost.agent_id.in_(agent_mkt_ids),
                    ArenaPost.created_at >= since
                ).order_by(ArenaPost.created_at.desc()).limit(500).all()
            posts_count = sum(1 for p in arena_rows if not p.reply_to)
            comments_count = sum(1 for p in arena_rows if p.reply_to)
            # Считаем лайки/просмотры напрямую из хранимых счётчиков,
            # но только для агентов у которых есть посты в периоде
            agents_with_posts = {p.agent_id for p in arena_rows}
            likes_total = sum((a.arena_likes_count or 0) for a in agents if f'mkt_{a.id}' in agents_with_posts)
            views_total = sum((a.arena_views_count or 0) for a in agents if f'mkt_{a.id}' in agents_with_posts)
            stats = {
                'posts_feed': posts_count,
                'likes': likes_total,
                'views': views_total,
                'comments': comments_count,
                'total': len(arena_rows),
                'agents_count': len(agents_data),
            }
            return web.json_response({'agents': agents_data, 'events': [], 'stats': stats})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[AGENTS ACTIVITY] error: {e}", exc_info=True)
        return web.json_response({'agents': [], 'events': [], 'stats': {}})


async def api_marketplace_my_handler(request):
    """GET /api/marketplace/my — мои агенты и скрипты"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        session_db = Session()
        try:
            from models import UserAgent, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'Not found'}, status=404)
            # Авто-активация агентов на модерации (авто-одобрение)
            session_db.query(UserAgent).filter_by(
                author_id=user_obj.id, status='review').update({'status': 'active'})
            session_db.commit()
            agents = session_db.query(UserAgent).filter_by(author_id=user_obj.id).order_by(
                UserAgent.created_at.desc()).all()
            from models import AgentSubscription, ArenaPost
            from sqlalchemy import func
            # Считаем реальное количество постов из arena_posts
            arena_counts = {}
            for a in agents:
                agent_key = f'mkt_{a.id}'
                cnt = session_db.query(func.count(ArenaPost.id)).filter(
                    ArenaPost.agent_id == agent_key).scalar() or 0
                arena_counts[a.id] = cnt
            def _is_subscribed(agent):
                return bool(session_db.query(AgentSubscription).filter_by(
                    user_id=user_obj.id, agent_id=agent.id).first())
            def _parse_notify_freq(custom_anchors_json):
                import json as _j
                try:
                    lst = _j.loads(custom_anchors_json or '[]')
                    for e in lst:
                        if e.get('id') == 'auto-notify':
                            return int(e.get('cooldown_hours', 0))
                except Exception:
                    pass
                return 0
            # Подписанные агенты других пользователей (активированные чужие агенты)
            ext_subs = session_db.query(AgentSubscription).filter_by(user_id=user_obj.id).all()
            owned_ids = {a.id for a in agents}
            ext_agent_ids = [s.agent_id for s in ext_subs if s.agent_id not in owned_ids]
            ext_agents = []
            if ext_agent_ids:
                ext_objs = session_db.query(UserAgent).filter(UserAgent.id.in_(ext_agent_ids)).all()
                for ea in ext_objs:
                    ext_agents.append({
                        'id': ea.id, 'name': ea.name, 'slug': ea.slug,
                        'status': 'subscribed', 'subscribers_count': ea.subscribers_count,
                        'price_per_message': ea.price_per_message,
                        'trial_messages': ea.trial_messages,
                        'messages_count': 0,
                        'specialization': ea.specialization or '',
                        'job_title': ea.job_title or '',
                        'description': ea.description or '',
                        'personality': '',
                        'avatar_url': ea.avatar_url or '',
                        'is_private': bool(ea.is_private),
                        'user_api_keys': '', 'python_code': '', 'search_scope': '',
                        'is_subscribed': True, 'is_external': True,
                    })
            own_agents_list = [{'id': a.id, 'name': a.name, 'slug': a.slug,
                             'status': a.status, 'subscribers_count': a.subscribers_count,
                             'price_per_message': a.price_per_message,
                             'trial_messages': a.trial_messages,
                             'messages_count': arena_counts.get(a.id, 0),
                             'specialization': a.specialization or '',
                             'job_title': a.job_title or '',
                             'description': a.description or '',
                             'personality': a.personality or '',
                             'avatar_url': a.avatar_url or '',
                             'is_private': bool(a.is_private),
                             'user_api_keys': (a.user_api_keys or '') if a.author_id == user_obj.id else '',
                             'python_code': (a.python_code or '') if a.author_id == user_obj.id else '',
                             'search_scope': (a.search_scope or '') if a.author_id == user_obj.id else '',
                             'notification_frequency': _parse_notify_freq(a.custom_anchors) if a.author_id == user_obj.id else 0,
                             'is_subscribed': _is_subscribed(a),
                             'is_external': False} for a in agents]
            return web.json_response({
                'agents': own_agents_list + ext_agents,
                'scripts': [],
            })
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] my handler error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_agent_chat_handler(request):
    """POST /api/marketplace/agents/{id}/chat — чат с агентом, биллинг"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        data = await request.json()
        user_message = (data.get('message') or '').strip()
        history = data.get('history', [])
        if not user_message:
            return web.json_response({'error': 'message required'}, status=400)

        # ─── Billing and DB updates ──────────────────────────────────
        session_db = Session()
        try:
            from models import UserAgent, AgentSubscription, AgentRun, TokenTransaction, User as UserModel
            import datetime as _dt

            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)

            agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
            if not agent:
                return web.json_response({'error': 'Agent not found'}, status=404)
            if agent.status == 'disabled':
                return web.json_response({'error': 'Agent not available'}, status=400)
            # Приватные агенты — только владелец может с ними общаться
            is_owner_chat = bool(user_obj and agent.author_id == user_obj.id)
            if agent.is_private and not is_owner_chat:
                return web.json_response({'error': 'Agent not available'}, status=403)

            # Get or create subscription
            sub = session_db.query(AgentSubscription).filter_by(
                user_id=user_obj.id, agent_id=agent_id).first()
            if not sub:
                sub = AgentSubscription(user_id=user_obj.id, agent_id=agent_id)
                session_db.add(sub)
                session_db.flush()
                agent.subscribers_count = (agent.subscribers_count or 0) + 1

            # Billing
            is_owner = (agent.author_id == user_obj.id)
            tokens_charged = 0
            author_earnings = 0
            platform_earnings = 0

            if not is_owner:
                price = agent.price_per_message or 5
                if (user_obj.token_balance or 0) < price:
                    session_db.close()
                    return web.json_response({
                        'error': 'insufficient_balance',
                        'balance': user_obj.token_balance or 0,
                        'price': price
                    }, status=402)
                user_obj.token_balance = (user_obj.token_balance or 0) - price
                user_obj.tokens_spent = (user_obj.tokens_spent or 0) + price
                tokens_charged = price
                royalty_pct = agent.author_royalty_pct or 70
                author_earnings = price * royalty_pct // 100
                platform_earnings = price - author_earnings
                author = session_db.query(UserModel).filter_by(id=agent.author_id).first()
                if author and author.id != user_obj.id:
                    author.token_balance = (author.token_balance or 0) + author_earnings
                    author.referral_balance = (author.referral_balance or 0) + author_earnings
                    session_db.add(TokenTransaction(
                        user_id=author.id, amount=author_earnings,
                        action='agent_royalty',
                        description=f'Роялти за сообщение агенту «{agent.name}»',
                        balance_after=author.token_balance
                    ))
                session_db.add(TokenTransaction(
                    user_id=user_obj.id, amount=-price,
                    action='agent_message',
                    description=f'Сообщение агенту «{agent.name}»',
                    balance_after=user_obj.token_balance
                ))
            sub.messages_count = (sub.messages_count or 0) + 1
            sub.tokens_spent = (sub.tokens_spent or 0) + tokens_charged
            sub.last_message_at = _dt.datetime.now(_dt.timezone.utc)
            agent.messages_count = (agent.messages_count or 0) + 1
            session_db.add(AgentRun(
                user_id=user_obj.id, agent_id=agent_id,
                tokens_charged=tokens_charged, author_earnings=author_earnings,
                platform_earnings=platform_earnings, is_trial=False
            ))
            session_db.commit()

            # Capture values before session closes
            _agent_personality_raw = agent.personality or f'Ты полезный AI-ассистент по имени {agent.name}.'
            _agent_search_scope = (agent.search_scope or '').strip()
            user_balance = user_obj.token_balance or 0
        finally:
            session_db.close()

        # Inject search_scope hint into system prompt if configured
        if _agent_search_scope:
            agent_personality = (
                _agent_personality_raw +
                f"\n\nКогда используешь поиск в интернете — приоритизируй эти темы и источники: {_agent_search_scope}."
                f" При web-поиске формулируй запросы вокруг этих областей, если тема запроса не указана явно."
            )
        else:
            agent_personality = _agent_personality_raw

        # ─── DeepSeek call ───────────────────────────────────────────
        import aiohttp as _aio
        messages = [{'role': 'system', 'content': agent_personality}]
        for m in history[-10:]:
            if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and m.get('content'):
                messages.append({'role': m['role'], 'content': str(m['content'])})
        messages.append({'role': 'user', 'content': user_message})

        async with _aio.ClientSession() as sess:
            resp = await sess.post(
                'https://api.deepseek.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                json={'model': DEEPSEEK_MODEL, 'messages': messages,
                      'max_tokens': 1000, 'temperature': 0.7},
                timeout=_aio.ClientTimeout(total=60)
            )
            result = await resp.json()

        ai_reply = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip() or '...'
        return web.json_response({
            'reply': ai_reply,
            'tokens_charged': tokens_charged,
            'balance': user_balance
        })
    except web.HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AGENT CHAT] error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


# ═══════════════════════════════════════════════════════
# AGENT ARENA API
# ═══════════════════════════════════════════════════════

async def api_arena_state_handler(request):
    """GET /api/arena — глобальное состояние арены (без авторизации)"""
    try:
        from ai_integration.agent_arena import get_global_feed_state
        state = get_global_feed_state()
        return web.json_response(state)
    except Exception as e:
        logger.error(f"[ARENA] state error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_arena_stream_handler(request):
    """GET /api/arena/stream — SSE стрим глобальной ленты арены"""
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )
    await response.prepare(request)

    try:
        from ai_integration.agent_arena import global_feed_sse_generator
        async for chunk in global_feed_sse_generator():
            try:
                await response.write(chunk.encode('utf-8'))
            except (ConnectionResetError, asyncio.CancelledError):
                break
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.error(f"[ARENA] stream error: {e}", exc_info=True)

    return response


async def api_arena_comment_handler(request):
    """POST /api/arena/comment — пользователь оставил комментарий, агент отвечает"""
    try:
        data = await request.json()
        comment_text = (data.get('text') or '').strip()
        post_text = (data.get('context') or '').strip()
        post_key = (data.get('post_id') or '').strip()
        if not comment_text:
            return web.json_response({'error': 'empty text'}, status=400)
        from ai_integration.agent_arena import reply_to_comment
        agent_id = (data.get('agent_id') or '').strip()
        display_name = (data.get('display_name') or 'Участник').strip()[:50]
        avatar_url = (data.get('avatar_url') or '').strip()
        user_cmt_client_id = (data.get('user_cmt_id') or '').strip()
        reply = await reply_to_comment(comment_text, post_text, agent_id, post_key=post_key)

        # Сохраняем комментарий пользователя в _global_feed + ArenaPost
        user_cmt_id = None
        if post_key:
            try:
                import time as _time
                import datetime as _dt
                from ai_integration.agent_arena import _global_feed as _af, _db_save_post as _dsp
                user_cmt_id = f"ucmt_{post_key}_{int(_time.time()*1000)}"
                user_msg = {
                    'id': user_cmt_id,
                    'agent_id': 'user',
                    'agent_name': display_name,
                    'agent_title': 'Участник',
                    'color': '#068487',
                    'initials': display_name[0].upper() if display_name else 'У',
                    'text': comment_text,
                    'ts': _dt.datetime.utcnow().isoformat(),
                    'reply_to': post_key,
                    'avatar_url': avatar_url,
                }
                _af.append(user_msg)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _dsp, user_msg)
            except Exception as _upe:
                logger.warning(f"[ARENA] user comment persistence error: {_upe}")

        # Сохраняем ответ агента в _global_feed + ArenaPost (для persistence после деплоя)
        if post_key and reply.get('agent_id'):
            try:
                import time as _time
                import datetime as _dt
                from ai_integration.agent_arena import _global_feed as _af, _db_save_post as _dsp
                reply_msg = {
                    'id': f"reply_{post_key}_{int(_time.time())}",
                    'agent_id': reply.get('agent_id', ''),
                    'agent_name': reply.get('agent_name', ''),
                    'agent_title': reply.get('agent_title', ''),
                    'color': reply.get('color', ''),
                    'initials': reply.get('initials', ''),
                    'text': reply.get('text', ''),
                    'ts': _dt.datetime.utcnow().isoformat(),
                    'reply_to': post_key,
                    'avatar_url': '',
                }
                _af.append(reply_msg)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _dsp, reply_msg)
                reply['_reply_id'] = reply_msg['id']  # чтобы JS мог пометить как rendered
            except Exception as _pe:
                logger.warning(f"[ARENA] reply persistence error: {_pe}")

        # Сохраняем в ArenaComment для аналитики
        if post_key:
            try:
                from models import ArenaComment, UserAgent
                import datetime as _dt
                db_s = Session()
                try:
                    db_s.add(ArenaComment(
                        post_key=post_key,
                        user_text=comment_text,
                        agent_name=reply.get('agent_name', ''),
                        agent_title=reply.get('agent_title', ''),
                        color=reply.get('color', ''),
                        initials=reply.get('initials', ''),
                        agent_text=reply.get('text', ''),
                        ts=_dt.datetime.utcnow().isoformat(),
                    ))
                    # Обновляем messages_count у UserAgent
                    reply_agent_id = reply.get('agent_id', '') or agent_id
                    if reply_agent_id and reply_agent_id.startswith('mkt_'):
                        try:
                            numeric_id = int(reply_agent_id.split('_', 1)[1])
                            ua = db_s.query(UserAgent).filter_by(id=numeric_id).first()
                            if ua:
                                ua.messages_count = (ua.messages_count or 0) + 1
                        except (ValueError, IndexError):
                            pass
                    db_s.commit()
                finally:
                    db_s.close()
            except Exception as _ce:
                logger.warning(f"[ARENA] comment save error: {_ce}")

        if user_cmt_id:
            reply['_user_cmt_id'] = user_cmt_id
        return web.json_response(reply)
    except Exception as e:
        logger.error(f'[ARENA] comment handler error: {e}', exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_agent_status_handler(request):
    """PUT /api/marketplace/agents/{id}/status — пауза/запуск агента автором"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        data = await request.json()
        new_status = data.get('status', 'active')
        if new_status not in ('active', 'paused', 'disabled'):
            return web.json_response({'error': 'Invalid status'}, status=400)
        session_db = Session()
        try:
            from models import UserAgent, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            agent = session_db.query(UserAgent).filter_by(
                id=agent_id, author_id=user_obj.id).first()
            if not agent:
                return web.json_response({'error': 'Not found'}, status=404)
            agent.status = new_status
            session_db.commit()
            # Если агент активирован — сразу постим в арену (не ждём следующего цикла)
            if new_status == 'active':
                try:
                    from ai_integration.agent_arena import post_agent_immediately
                    asyncio.ensure_future(post_agent_immediately(agent.id))
                except Exception as _ae:
                    logger.warning(f"[ARENA] immediate post error: {_ae}")
            return web.json_response({'success': True, 'status': new_status})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent status error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_agent_delete_handler(request):
    """DELETE /api/marketplace/agents/{id} — удалить агента"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        session_db = Session()
        try:
            from models import (UserAgent, User as UserModel, ArenaPost, ArenaComment,
                                AgentSubscription, AgentRun)
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            agent = session_db.query(UserAgent).filter_by(
                id=agent_id, author_id=user_obj.id).first()
            if not agent:
                return web.json_response({'error': 'Not found'}, status=404)

            mkt_agent_id = f'mkt_{agent_id}'  # именно этот ключ хранится в arena_posts

            # Удаляем все посты агента из арены + комменты к ним + ответы агента
            arena_posts = session_db.query(ArenaPost).filter_by(agent_id=mkt_agent_id).all()
            post_keys = [ap.post_key for ap in arena_posts]
            for pk in post_keys:
                session_db.query(ArenaComment).filter_by(post_key=pk).delete(synchronize_session=False)
            if post_keys:
                session_db.query(ArenaPost).filter(
                    ArenaPost.post_key.in_(post_keys)).delete(synchronize_session=False)
            # Ответы агента на чужие посты (reply_to != None)
            session_db.query(ArenaPost).filter_by(agent_id=mkt_agent_id).delete(synchronize_session=False)

            # Удаляем подписчиков и запуски
            session_db.query(AgentSubscription).filter_by(agent_id=agent_id).delete(synchronize_session=False)
            session_db.query(AgentRun).filter_by(agent_id=agent_id).delete(synchronize_session=False)

            session_db.delete(agent)
            session_db.commit()

            # Чистим in-memory ленту от постов этого агента
            try:
                from ai_integration.agent_arena import _global_feed as _gf
                _gf[:] = [m for m in _gf if m.get('agent_id') != mkt_agent_id]
            except Exception as _fe:
                logger.debug(f"[ARENA] feed cleanup: {_fe}")

            return web.json_response({'success': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent delete error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_agent_get_handler(request):
    """GET /api/marketplace/agents/{id} — данные одного агента"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        session_db = Session()
        try:
            from models import UserAgent, AgentSubscription, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
            if not agent:
                return web.json_response({'error': 'Not found'}, status=404)
            # Приватные агенты доступны только владельцу
            is_owner_get = bool(user_obj and agent.author_id == user_obj.id)
            if agent.is_private and not is_owner_get:
                return web.json_response({'error': 'Not found'}, status=404)
            rating = round(agent.rating_sum / agent.rating_count, 1) if agent.rating_count else None
            is_subscribed = False
            if user_obj:
                is_subscribed = bool(session_db.query(AgentSubscription).filter_by(
                    user_id=user_obj.id, agent_id=agent.id).first())
            author_username = agent.author.username if agent.author and agent.author.username else None
            return web.json_response({'agent': {
                'id': agent.id, 'name': agent.name, 'slug': agent.slug,
                'description': agent.description or '',
                'specialization': agent.specialization or '',
                'job_title': agent.job_title or '',
                'avatar_url': agent.avatar_url or '',
                'price_per_message': agent.price_per_message,
                'trial_messages': agent.trial_messages,
                'subscribers_count': agent.subscribers_count,
                'messages_count': agent.messages_count,
                'author_royalty_pct': agent.author_royalty_pct or 30,
                'rating': rating,
                'is_subscribed': is_subscribed,
                'author_username': author_username,
                'is_owner': bool(user_obj and agent.author_id == user_obj.id),
                'user_api_keys': (agent.user_api_keys or '') if (user_obj and agent.author_id == user_obj.id) else '',
                'python_code': (agent.python_code or '') if (user_obj and agent.author_id == user_obj.id) else '',
            }})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent get error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


_ARENA_COLORS = [
    '#1a3a5c', '#2d5016', '#6b1a1a', '#4a1a6b', '#1a4a1a',
    '#5c3a1a', '#1a5c5c', '#4a3a1a', '#3a1a4a', '#1a4a3a',
]

async def _arena_intro_for_agent(agent_id: int, name: str, specialization: str,
                                  personality: str, description: str,
                                  author_username: str = ''):
    """Публикует первое вводное сообщение агента в глобальную ленту арены."""
    try:
        import time as _time
        from datetime import datetime as _dt
        from ai_integration.agent_arena import (
            _global_feed as _gf,
            _generate_agent_reply,
            _db_save_post,
        )
        color = _ARENA_COLORS[agent_id % len(_ARENA_COLORS)]
        initials = (name or '?')[:2].upper()
        agent_dict = {
            'id': f'mkt_{agent_id}',
            'name': name or 'Агент',
            'title': specialization or 'Агент',
            'color': color,
            'initials': initials,
            'system_prompt': personality or f"Ты — {name}. {description or ''}",
            '_is_marketplace': True,
            'author_username': author_username,
        }
        reply = await _generate_agent_reply(agent_dict, _gf[-10:])
        msg = {
            'id': f'mkt_{agent_id}_{int(_time.time())}',
            'agent_id': f'mkt_{agent_id}',
            'agent_name': name or 'Агент',
            'agent_title': specialization or 'Агент',
            'color': color,
            'initials': initials,
            'text': reply,
            'ts': _dt.utcnow().isoformat(),
            'author_username': author_username,
        }
        _gf.append(msg)
        if len(_gf) > 200:
            _gf[:] = _gf[-200:]
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _db_save_post, msg)
        logger.info(f"[ARENA] intro post published for agent mkt_{agent_id}")
    except Exception as e:
        logger.warning(f"[ARENA] intro post failed for agent {agent_id}: {e}")


async def api_marketplace_agent_activate_handler(request):
    """POST /api/marketplace/agents/{id}/activate — активировать (подписаться) на агента"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        session_db = Session()
        try:
            from models import UserAgent, AgentSubscription, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            # Для собственных приватных агентов пропускаем проверку status='active'
            agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
            if not agent:
                return web.json_response({'error': 'Agent not found'}, status=404)
            is_own = (agent.author_id == user_obj.id)
            if not is_own:
                # Приватные агенты нельзя активировать другим пользователям
                if agent.is_private:
                    return web.json_response({'error': 'Agent not found or not active'}, status=404)
                # Для чужих/публичных агентов требуем status='active' или 'paused' (видны в маркете)
                if agent.status not in ('active', 'paused'):
                    return web.json_response({'error': 'Agent not found or not active'}, status=404)
            existing = session_db.query(AgentSubscription).filter_by(
                user_id=user_obj.id, agent_id=agent_id).first()
            if not existing:
                # Добавляем подписку, не трогая другие
                sub = AgentSubscription(user_id=user_obj.id, agent_id=agent_id)
                session_db.add(sub)
                agent.subscribers_count = (agent.subscribers_count or 0) + 1
                session_db.commit()
            # Всегда ставим агента как активного (focused) — для корректного отображения в чате
            from ai_integration.user_agents import set_user_active_agent as _sua
            _sua(user_id, agent.id)
            _switched = True
            # Захватываем атрибуты до закрытия сессии
            _aid = agent.id
            _aname = agent.name or 'Агент'
            _aspec = agent.specialization or 'Агент'
            _apers = agent.personality or ''
            _adesc = agent.description or ''
            _price = agent.price_per_message
            _aavatar = agent.avatar_url or ''
            # Автор агента
            _author_user = session_db.query(UserModel).filter_by(id=agent.author_id).first()
            _author_uname = (_author_user.username or '') if _author_user else ''
            return web.json_response({'success': True,
                                      'price_per_message': _price, 'switched': _switched,
                                      'agent': {'id': _aid, 'name': _aname, 'avatar_url': _aavatar}})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent activate error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_agent_deactivate_handler(request):
    """DELETE /api/marketplace/agents/{id}/activate — деактивировать (отписаться) от агента"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        session_db = Session()
        try:
            from models import UserAgent, AgentSubscription, User as UserModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            sub = session_db.query(AgentSubscription).filter_by(
                user_id=user_obj.id, agent_id=agent_id).first()
            if sub:
                session_db.delete(sub)
                agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
                if agent and (agent.subscribers_count or 0) > 0:
                    agent.subscribers_count = agent.subscribers_count - 1
                session_db.commit()
            # Убираем агента из списка активных (не трогаем остальных)
            from ai_integration.user_agents import remove_user_active_agent as _rua
            _rua(user_id, agent_id)
            return web.json_response({'success': True})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent deactivate error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_marketplace_agent_use_handler(request):
    """Переключить активного агента в Telegram без повторной подписки"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        session_db = Session()
        try:
            from models import UserAgent, User as UserModel, AgentSubscription
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
            if not agent:
                return web.json_response({'error': 'Agent not found'}, status=404)
            # Разрешаем владельцу или подписчику
            is_owner = (agent.author_id == user_obj.id)
            if not is_owner:
                sub = session_db.query(AgentSubscription).filter_by(
                    user_id=user_obj.id, agent_id=agent_id).first()
                if not sub:
                    return web.json_response({'error': 'Subscribe first'}, status=403)
            from ai_integration.user_agents import set_user_active_agent as _sua
            _sua(user_id, agent_id)
            return web.json_response({'success': True, 'name': agent.name})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent use error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


async def api_agent_rate_handler(request):
    """POST /api/marketplace/agents/{id}/rate — оценить агента (1–10)"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        agent_id = int(request.match_info['id'])
        data = await request.json()
        rating_val = data.get('rating')
        try:
            rating_val = int(rating_val)
            if not 1 <= rating_val <= 10:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({'error': 'Rating must be 1–10'}, status=400)
        session_db = Session()
        try:
            from models import UserAgent, User as UserModel, AgentRating as AgentRatingModel
            user_obj = session_db.query(UserModel).filter_by(telegram_id=user_id).first()
            if not user_obj:
                return web.json_response({'error': 'User not found'}, status=404)
            agent = session_db.query(UserAgent).filter_by(id=agent_id).first()
            if not agent:
                return web.json_response({'error': 'Agent not found'}, status=404)
            if agent.author_id == user_obj.id:
                return web.json_response({'error': 'Cannot rate own agent'}, status=400)
            existing = session_db.query(AgentRatingModel).filter_by(
                rater_user_id=user_obj.id, agent_id=agent_id).first()
            if existing:
                old = existing.rating
                existing.rating = rating_val
                # Обновляем сумму
                agent.rating_sum = (agent.rating_sum or 0) - old + rating_val
            else:
                new_ar = AgentRatingModel(rater_user_id=user_obj.id, agent_id=agent_id, rating=rating_val)
                session_db.add(new_ar)
                agent.rating_sum = (agent.rating_sum or 0) + rating_val
                agent.rating_count = (agent.rating_count or 0) + 1
            session_db.commit()
            new_rating = round(agent.rating_sum / agent.rating_count, 1) if agent.rating_count else None
            return web.json_response({'success': True, 'rating': new_rating})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[MARKETPLACE] agent rate error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


# Routes
app.router.add_get('/health', health_handler)
app.router.add_get('/api/smtp-check', smtp_check_handler)
app.router.add_get('/', login_handler)
app.router.add_get('/admin/index.html', lambda r: web.HTTPFound('/dashboard'))  # Redirect old admin URL
app.router.add_get('/tg_auth', auth_handler)
app.router.add_get('/telegram_auth', auth_handler)  # Keep old route for compatibility
app.router.add_post('/api/register', email_register_handler)
app.router.add_post('/api/login/email', email_login_handler)
app.router.add_post('/api/password/reset', password_reset_handler)
app.router.add_post('/api/password/change', password_change_handler)
app.router.add_post('/api/account/delete', delete_account_handler)
app.router.add_post('/api/push/subscribe', push_subscribe_handler)
app.router.add_get('/api/push/vapid-key', push_vapid_key_handler)
app.router.add_get('/logout', logout_handler)
app.router.add_get('/dashboard', dashboard_handler)
app.router.add_get('/tasks', tasks_handler)
app.router.add_get('/profile', profile_handler)
app.router.add_post('/chat', chat_handler)
app.router.add_get('/chat/progress', chat_progress_handler)
app.router.add_post('/api/transcribe', transcribe_handler)
app.router.add_post('/api/send_message', api_send_message_handler)
app.router.add_post('/clear_history', clear_history_handler)

# Marketplace API
app.router.add_get('/api/marketplace/agents', api_marketplace_agents_handler)
app.router.add_post('/api/marketplace/agents', api_marketplace_publish_agent_handler)
app.router.add_post('/api/marketplace/agents/test-code', api_agent_test_code_handler)
app.router.add_post('/api/marketplace/agents/generate-code', api_agent_generate_code_handler)
app.router.add_get('/api/marketplace/my', api_marketplace_my_handler)
app.router.add_get('/api/marketplace/agents/activity', api_agents_activity_handler)
app.router.add_get('/api/office/activity', api_office_activity_handler)
app.router.add_get('/api/marketplace/agents/{id}', api_marketplace_agent_get_handler)
app.router.add_put('/api/marketplace/agents/{id}/status', api_marketplace_agent_status_handler)
app.router.add_delete('/api/marketplace/agents/{id}', api_marketplace_agent_delete_handler)
app.router.add_post('/api/marketplace/agents/{id}/chat', api_agent_chat_handler)
app.router.add_post('/api/marketplace/agents/{id}/activate', api_marketplace_agent_activate_handler)
app.router.add_delete('/api/marketplace/agents/{id}/activate', api_marketplace_agent_deactivate_handler)
app.router.add_post('/api/marketplace/agents/{id}/use', api_marketplace_agent_use_handler)
app.router.add_post('/api/marketplace/agents/{id}/rate', api_agent_rate_handler)
# Arena — глобальная лента (не требует авторизации)
app.router.add_get('/api/arena', api_arena_state_handler)
app.router.add_get('/api/arena/stream', api_arena_stream_handler)
app.router.add_post('/api/arena/comment', api_arena_comment_handler)

async def api_arena_agent_avatar_handler(request):
    """GET /api/arena/agent_avatar/{agent_id} — возвращает аватар агента из маркетплейса"""
    agent_id = request.match_info.get('agent_id', '')  # e.g. 'mkt_6'
    try:
        numeric_id = int(agent_id.replace('mkt_', ''))
    except ValueError:
        return web.Response(status=404)
    try:
        db_s = Session()
        try:
            from models import UserAgent
            agent = db_s.query(UserAgent).filter_by(id=numeric_id).first()
            if not agent or not agent.avatar_url:
                return web.Response(status=404)
            avatar_data = agent.avatar_url
        finally:
            db_s.close()
        import base64 as _b64
        if avatar_data.startswith('data:'):
            parts = avatar_data.split(',', 1)
            if len(parts) == 2:
                meta = parts[0]
                ct = meta.split(':')[1].split(';')[0] if ':' in meta else 'image/webp'
                img_bytes = _b64.b64decode(parts[1])
                return web.Response(
                    body=img_bytes,
                    content_type=ct,
                    headers={'Cache-Control': 'no-cache, no-store, must-revalidate'}
                )
        return web.Response(status=404)
    except Exception as e:
        logger.error(f'[ARENA] agent_avatar error: {e}')
        return web.Response(status=500)

app.router.add_get('/api/arena/agent_avatar/{agent_id}', api_arena_agent_avatar_handler)

async def api_arena_post_like_handler(request):
    """POST /api/arena/post/{post_key}/like — лайк/анлайк поста, обновляет счётчик у агента"""
    try:
        post_key = request.match_info.get('post_key', '')
        session_db = Session()
        try:
            from models import ArenaPost, UserAgent
            post = session_db.query(ArenaPost).filter_by(post_key=post_key).first()
            if not post:
                return web.json_response({'error': 'Not found'}, status=404)
            data = await request.json()
            liked = bool(data.get('liked', True))
            # Обновляем счётчик лайков поста
            post.likes_count = max(0, (post.likes_count or 0) + (1 if liked else -1))
            # Обновляем агрегированный счётчик агента (только при лайке)
            if liked and post.agent_id and post.agent_id.startswith('mkt_'):
                try:
                    numeric_id = int(post.agent_id.split('_', 1)[1])
                    ua = session_db.query(UserAgent).filter_by(id=numeric_id).first()
                    if ua:
                        ua.arena_likes_count = (ua.arena_likes_count or 0) + 1
                except (ValueError, IndexError):
                    pass
            elif not liked and post.agent_id and post.agent_id.startswith('mkt_'):
                try:
                    numeric_id = int(post.agent_id.split('_', 1)[1])
                    ua = session_db.query(UserAgent).filter_by(id=numeric_id).first()
                    if ua:
                        ua.arena_likes_count = max(0, (ua.arena_likes_count or 0) - 1)
                except (ValueError, IndexError):
                    pass
            session_db.commit()
            new_count = post.likes_count
            # Синхронизируем in-memory _global_feed чтобы SSE init не отдавал устаревший счётчик
            try:
                from ai_integration.agent_arena import update_post_likes_in_feed
                update_post_likes_in_feed(post_key, new_count)
            except Exception:
                pass
            return web.json_response({'ok': True, 'likes_count': new_count})
        finally:
            session_db.close()
    except Exception as e:
        logger.error(f"[ARENA] like error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

app.router.add_post('/api/arena/post/{post_key}/like', api_arena_post_like_handler)

async def api_arena_delete_entry_handler(request):
    """DELETE /api/arena/entry/{post_key} — удаляет пост пользователя или пост своего агента"""
    try:
        post_key = request.match_info.get('post_key', '').strip()
        if not post_key:
            return web.json_response({'error': 'missing post_key'}, status=400)
        session_web = await get_session(request)
        current_user_id = session_web.get('user_id') if session_web else None
        from models import ArenaPost, UserAgent, User as UserModel
        from ai_integration.agent_arena import _global_feed as _gf
        db_s = Session()
        try:
            row = db_s.query(ArenaPost).filter_by(post_key=post_key).first()
            if not row:
                return web.json_response({'error': 'not found'}, status=404)
            allowed = (row.agent_id == 'user')
            if not allowed and current_user_id and row.agent_id.startswith('mkt_'):
                try:
                    agent_num_id = int(row.agent_id[4:])
                    user_obj = db_s.query(UserModel).filter_by(telegram_id=current_user_id).first()
                    if user_obj:
                        agent_obj = db_s.query(UserAgent).filter_by(id=agent_num_id, author_id=user_obj.id).first()
                        if agent_obj:
                            allowed = True
                except Exception:
                    pass
            if not allowed:
                return web.json_response({'error': 'forbidden'}, status=403)
            db_s.delete(row)
            db_s.commit()
        finally:
            db_s.close()
        # Удаляем из in-memory feed
        to_remove = [m for m in _gf if m.get('id') == post_key]
        for m in to_remove:
            try:
                _gf.remove(m)
            except ValueError:
                pass
        return web.json_response({'ok': True})
    except Exception as e:
        logger.error(f'[ARENA] delete entry error: {e}', exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

app.router.add_delete('/api/arena/entry/{post_key}', api_arena_delete_entry_handler)

async def api_arena_user_post_handler(request):
    """POST /api/arena/user-post — пользователь публикует пост в ленту арены"""
    try:
        data = await request.json()
        text = (data.get('text') or '').strip()
        if not text:
            return web.json_response({'error': 'empty'}, status=400)
        display_name = (data.get('display_name') or 'Участник').strip()[:50]
        import time as _t
        import datetime as _dt
        from ai_integration.agent_arena import _global_feed as _gf, _db_save_post as _dsp
        msg = {
            'id': f'user_{int(_t.time()*1000)}',
            'agent_id': 'user',
            'agent_name': display_name,
            'agent_title': 'Участник',
            'color': '#068487',
            'initials': display_name[0].upper() if display_name else 'У',
            'text': text,
            'ts': _dt.datetime.utcnow().isoformat(),
            'author_username': (data.get('author_username') or '').strip()[:100],
            'avatar_url': data.get('avatar_url') or '',
        }
        _gf.append(msg)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _dsp, msg)
        # Запускаем волну обсуждения: агенты прокомментируют пост пользователя
        try:
            from ai_integration.agent_arena import _discussion_wave as _dw
            asyncio.ensure_future(_dw(msg))
        except Exception as _we:
            logger.warning(f'[ARENA] discussion_wave schedule error: {_we}')
        return web.json_response({'ok': True, 'id': msg['id']})
    except Exception as e:
        logger.error(f'[ARENA] user-post error: {e}', exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

app.router.add_post('/api/arena/user-post', api_arena_user_post_handler)

async def api_arena_force_post_handler(request):
    """POST /api/arena/force-post — принудительно создать один пост (для тестирования)"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        from ai_integration.agent_arena import (
            _load_marketplace_agents, _generate_agent_reply, _global_feed, _db_save_post
        )
        import time as _time
        loop = asyncio.get_running_loop()
        agents = await loop.run_in_executor(None, _load_marketplace_agents)
        if not agents:
            return web.json_response({'error': 'Нет активных агентов маркетплейса. Создайте агента сначала.'}, status=404)
        agent = random.choice(agents)
        reply = await _generate_agent_reply(agent, _global_feed[-10:])
        msg = {
            "id": f"{agent['id']}_{int(_time.time())}",
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "agent_title": agent["title"],
            "color": agent["color"],
            "initials": agent["initials"],
            "text": reply,
            "ts": datetime.utcnow().isoformat(),
            "author_username": agent.get("author_username", ""),
        }
        from ai_integration.agent_arena import _global_feed as gf
        gf.append(msg)
        loop.run_in_executor(None, _db_save_post, msg)
        return web.json_response({'success': True, 'agent': agent['name'], 'preview': reply[:80]})
    except Exception as e:
        logger.error(f"[ARENA] force-post error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

app.router.add_post('/api/arena/force-post', api_arena_force_post_handler)

async def api_arena_clear_all_handler(request):
    """POST /api/arena/clear-all — удаляет ВСЕ посты арены из БД и памяти, после чего агенты сгенерируют свежие"""
    try:
        session_web = await get_session(request)
        user_id = session_web.get('user_id') if session_web else None
        if not user_id:
            return web.json_response({'error': 'Not authenticated'}, status=401)
        import ai_integration.agent_arena as _arena_mod
        # Сбрасываем семафор seed чтобы новые SSE-подключения ждали seed
        _arena_mod._seed_done.clear()
        # Удаляем все посты из БД
        def _delete_all_posts():
            import psycopg2
            try:
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                cur.execute("DELETE FROM arena_posts")
                deleted = cur.rowcount
                # Also clear comments if table exists
                try:
                    cur.execute("DELETE FROM arena_comments")
                except Exception:
                    pass
                # Reset agent arena counters
                try:
                    cur.execute(
                        "UPDATE user_agents SET messages_count = 0, "
                        "arena_likes_count = 0, arena_views_count = 0"
                    )
                except Exception:
                    pass
                conn.commit()
                cur.close(); conn.close()
                return deleted
            except Exception as e:
                logger.error(f'[ARENA] DB delete error: {e}')
                return 0
        loop = asyncio.get_running_loop()
        deleted = await loop.run_in_executor(None, _delete_all_posts)
        # Очищаем in-memory ленту
        _arena_mod._global_feed.clear()
        # Запускаем пересев
        asyncio.ensure_future(_arena_mod.seed_global_feed_if_empty())
        return web.json_response({'success': True, 'deleted_from_db': deleted, 'feed_cleared': True})
    except Exception as e:
        logger.error(f'[ARENA] clear-all error: {e}', exc_info=True)
        return web.json_response({'error': str(e)}, status=500)

app.router.add_post('/api/arena/clear-all', api_arena_clear_all_handler)

app.router.add_post('/clear_user_tasks', clear_user_tasks_handler)
app.router.add_post('/clear_email_contacts', clear_email_contacts_handler)
app.router.add_post('/clear_single_task', clear_single_task_handler)
app.router.add_post('/complete_task', complete_task_handler)
app.router.add_post('/restore_task', restore_task_handler)
app.router.add_post('/skip_task', skip_task_handler)
app.router.add_post('/delete_task', delete_task_handler)
app.router.add_post('/api/edit_task', edit_task_handler)
app.router.add_post('/reschedule_task', reschedule_task_handler)
app.router.add_post('/update_timezone', update_timezone_handler)
app.router.add_get('/extend_subscription', extend_subscription_handler)
app.router.add_get('/subscription_tiers', subscription_tiers_handler)
app.router.add_get('/subscription-tiers', subscription_tiers_handler)  # Alias with dash
app.router.add_get('/create_payment', create_payment_handler)
app.router.add_get('/direct_login', direct_login_handler)
# SEO: verification files
app.router.add_get('/yandex_48ffa2026650f03f.html', lambda r: web.FileResponse('static/yandex_48ffa2026650f03f.html'))
app.router.add_get('/yandex_05efc1780770a3e1.html', lambda r: web.FileResponse('static/yandex_05efc1780770a3e1.html'))
app.router.add_get('/BingSiteAuth.xml', lambda r: web.FileResponse('static/BingSiteAuth.xml'))
# SEO: robots.txt и sitemap.xml в корне (поисковые ищут именно тут)
app.router.add_get('/robots.txt', lambda r: web.FileResponse('static/robots.txt', headers={'Content-Type': 'text/plain; charset=utf-8'}))
app.router.add_get('/sitemap.xml', lambda r: web.FileResponse('static/sitemap.xml', headers={'Content-Type': 'application/xml; charset=utf-8'}))
# PWA: Service Worker из корня с разрешённым scope /
async def sw_handler(request):
    return web.FileResponse('static/sw.js', headers={
        'Content-Type': 'application/javascript',
        'Service-Worker-Allowed': '/',
        'Cache-Control': 'no-cache',
    })
app.router.add_get('/sw.js', sw_handler)
# SEO: IndexNow key file
app.router.add_get('/d6193b04262141bba808b1279123715b.txt', lambda r: web.FileResponse('static/d6193b04262141bba808b1279123715b.txt'))
# AI SEO: llms.txt for AI crawlers (ChatGPT, Perplexity, Yandex GPT)
app.router.add_get('/llms.txt', lambda r: web.FileResponse('static/llms.txt', headers={'Content-Type': 'text/plain; charset=utf-8'}))
app.router.add_get('/llms-full.txt', lambda r: web.FileResponse('static/llms-full.txt', headers={'Content-Type': 'text/plain; charset=utf-8'}))
# AI SEO: FAQ page
app.router.add_get('/faq', faq_handler)
app.router.add_get('/arena', arena_public_handler)
# Privacy / personal data consent
app.router.add_get('/privacy', privacy_handler)
# Terms of use
app.router.add_get('/terms', terms_handler)
# i18n: English language routes (SEO — separate URLs per language)
async def login_handler_en(request):
    """English landing page"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if user_id:
        try:
            user_id = int(user_id)
            return web.HTTPFound('/dashboard')
        except (ValueError, TypeError):
            pass
    bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'asibiont_bot')
    base_url = str(request.url.origin())
    auth_url = f"{base_url}/tg_auth"
    return aiohttp_jinja2.render_template('index.html', request, {
        'logged_in': False, 'bot_username': bot_user, 'auth_url': auth_url,
        'lang': 'en', 'subscription_tier': 'Токены',
        'current_date': '', 'current_time': '', 'formatted_end_date': None,
        'timestamp': int(time.time()), 'user_timezone': 'UTC',
        'user': None, 'profile': None, 'tasks': [], 'messages': [], 'partners': [], 'subscription': None
    })

async def faq_handler_en(request):
    return aiohttp_jinja2.render_template('faq.html', request, {'lang': 'en'})

async def subscription_tiers_handler_en(request):
    flags = await get_payment_flags(request)
    return aiohttp_jinja2.render_template('subscription_tiers.html', request, {'lang': 'en', **flags})

app.router.add_get('/en', login_handler_en)
app.router.add_get('/en/', login_handler_en)
app.router.add_get('/en/faq', faq_handler_en)
app.router.add_get('/en/subscription-tiers', subscription_tiers_handler_en)
app.router.add_get('/en/subscription_tiers', subscription_tiers_handler_en)
app.router.add_static('/static', 'static')
app.router.add_post('/webhook/yookassa', yookassa_webhook)
app.router.add_get('/create_crypto_payment', create_crypto_payment_handler)
app.router.add_post('/webhook/nowpayments', nowpayments_webhook)
app.router.add_post('/webhook/resend', resend_webhook_handler)
# Gmail OAuth2
app.router.add_get('/oauth/gmail', gmail_oauth_redirect)
app.router.add_get('/oauth/gmail/callback', gmail_oauth_callback)
app.router.add_get('/api/oauth/gmail/status', gmail_oauth_status)
app.router.add_post('/api/oauth/gmail/disconnect', gmail_oauth_disconnect)
# Discord OAuth2 callback + login/link redirects
try:
    from discord_bot import discord_oauth_callback, discord_login_redirect, discord_link_redirect
    app.router.add_get('/auth/discord', discord_oauth_callback)
    app.router.add_get('/discord/login', discord_login_redirect)
    app.router.add_get('/discord/link', discord_link_redirect)
    logger.info("✅ Discord OAuth route registered")
except ImportError as e:
    logger.warning(f"Discord module not available: {e}")
app.router.add_post('/api/discord/unlink', discord_unlink_handler)
app.router.add_post('/api/telegram/unlink', telegram_unlink_handler)
# API routes for dynamic updates
app.router.add_get('/api/tasks', api_tasks_handler)
app.router.add_get('/api/partners', api_partners_handler)
app.router.add_post('/admin/clear_database', clear_database_handler)
app.router.add_get('/admin/add_test_users', add_test_users_handler)
app.router.add_get('/admin/invite', admin_invite_handler)
app.router.add_get('/api/elite_partners', api_elite_partners_handler)
app.router.add_get('/api/contact_profile', api_contact_profile_handler)
app.router.add_get('/api/favorite_contacts', api_favorite_contacts_handler)
app.router.add_post('/api/favorite_contacts', api_favorite_contacts_handler)
app.router.add_get('/api/blocked_contacts', api_blocked_contacts_handler)
app.router.add_post('/api/blocked_contacts', api_blocked_contacts_handler)
app.router.add_get('/api/avatar/{telegram_id}', api_avatar_handler)
app.router.add_post('/api/avatar/upload', api_avatar_upload_handler)
app.router.add_post('/api/avatar/delete', api_avatar_delete_handler)
app.router.add_post('/api/rate_user', rate_user_handler)
app.router.add_get('/api/get_user_rating', get_user_rating_handler)
app.router.add_post('/api/set_user_rating', set_user_rating_handler)
app.router.add_post('/api/posts', create_post_handler)
app.router.add_post('/api/update_profile', api_update_profile_handler)
app.router.add_post('/api/accept_delegated_task', api_accept_delegated_task_handler)
app.router.add_post('/api/reject_delegated_task', api_reject_delegated_task_handler)
app.router.add_post('/api/cancel_delegation', cancel_delegation_handler)
app.router.add_post('/api/withdraw', withdraw_handler)
app.router.add_get('/api/feed', get_feed_handler)
app.router.add_post('/api/feed/mark-viewed', mark_posts_viewed_handler)
app.router.add_put('/api/posts/{post_id}', edit_post_handler)
app.router.add_delete('/api/posts/{post_id}', delete_post_handler)
app.router.add_post('/api/comments', create_comment_handler)
app.router.add_get('/api/comments/{post_id}', get_comments_handler)
app.router.add_put('/api/comments/{comment_id}', edit_comment_handler)
app.router.add_delete('/api/comments/{comment_id}', delete_comment_handler)
app.router.add_post('/api/posts/{post_id}/like', toggle_like_handler)
app.router.add_post('/api/posts/{post_id}/translate', translate_post_handler)
app.router.add_post('/api/translate', translate_text_handler)
app.router.add_post('/api/comments/{comment_id}/translate', translate_comment_handler)
app.router.add_post('/api/notes/{note_id}/translate', translate_note_handler)
app.router.add_post('/api/hide_contact', hide_contact_handler)
app.router.add_get('/api/profile', api_profile_handler)
app.router.add_post('/api/profile', api_profile_handler)
app.router.add_post('/api/set_language', api_set_language_handler)
app.router.add_get('/api/reminders', api_reminders_handler)
app.router.add_get('/api/delegations', api_delegations_handler)
app.router.add_get('/api/interactions', api_interactions_handler)
app.router.add_get('/api/search_contacts', api_search_contacts_handler)
app.router.add_get('/api/balance', api_balance_handler)
app.router.add_get('/api/goals', api_goals_handler)
app.router.add_get('/api/notes', api_notes_handler)
app.router.add_post('/api/notes', api_notes_handler)
app.router.add_delete('/api/notes/{note_id}', api_note_delete_handler)
app.router.add_put('/api/notes/{note_id}', api_note_edit_handler)
app.router.add_get('/api/reports', api_reports_handler)
app.router.add_get('/api/activities/latest', api_activities_latest_handler)
app.router.add_get('/api/activities/stream', sse_activities_handler)
app.router.add_patch('/api/campaigns/{campaign_id}/status', api_campaign_status_handler)
app.router.add_delete('/api/campaigns/{campaign_id}', api_campaign_delete_handler)
app.router.add_patch('/api/content-campaigns/{campaign_id}/status', api_content_campaign_status_handler)
app.router.add_delete('/api/content-campaigns/{campaign_id}', api_content_campaign_delete_handler)
app.router.add_patch('/api/delegation-campaigns/{campaign_id}/status', api_delegation_campaign_status_handler)
app.router.add_delete('/api/delegation-campaigns/{campaign_id}', api_delegation_campaign_delete_handler)
app.router.add_post('/api/outreach/{outreach_id}/reply', api_outreach_reply_handler)
app.router.add_delete('/api/outreach/{outreach_id}', api_outreach_delete_handler)
app.router.add_delete('/api/activities/{activity_id}', api_activity_delete_handler)
app.router.add_patch('/api/activities/{activity_id}/status', api_activity_status_handler)
app.router.add_get('/api/email-contacts', api_email_contacts_handler)
app.router.add_post('/api/email-contacts', api_email_contacts_handler)
app.router.add_put('/api/email-contacts/{contact_id}', api_email_contact_edit_handler)
app.router.add_delete('/api/email-contacts/{contact_id}', api_email_contact_delete_handler)

# Internal messaging
app.router.add_get('/api/messages', api_messages_handler)
app.router.add_post('/api/messages/reply', api_messages_reply_handler)
app.router.add_post('/api/messages/send', api_messages_send_handler)
app.router.add_post('/api/messages/read', api_messages_read_handler)
app.router.add_post('/api/messages/delete', api_messages_delete_handler)


# Setup for production
# dp = Dispatcher()

# Include router from handlers
# dp.include_router(handlers_router)

# Session storage will be initialized in on_startup handler

# Initialize ReminderService
import reminder_service as reminder_service_module
reminder_service = ReminderService(bot=bot)
reminder_service_module.REMINDER_SERVICE = reminder_service  # Set global variable for use in handlers
logger.info(f"ReminderService initialized with bot={'present' if bot else 'None'} and set as global REMINDER_SERVICE")

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

        # Migrate users table: email, password_hash, phone
        user_columns = [col['name'] for col in inspector.get_columns('users')]
        for col_name, col_def in [
            ('email', 'VARCHAR(255) UNIQUE'),
            ('password_hash', 'VARCHAR(500)'),
            ('phone', 'VARCHAR(20)'),
        ]:
            if col_name not in user_columns:
                logger.info(f"Adding {col_name} column to users table...")
                try:
                    with engine.connect() as conn:
                        conn.execute(sql_text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                    logger.info(f"Added {col_name} column")
                except Exception as col_err:
                    logger.warning(f"Could not add {col_name}: {col_err}")

        # Create push_subscriptions table if not exists
        if 'push_subscriptions' not in inspector.get_table_names():
            logger.info("Creating push_subscriptions table...")
            try:
                with engine.connect() as conn:
                    conn.execute(sql_text("""
                        CREATE TABLE IF NOT EXISTS push_subscriptions (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            endpoint TEXT NOT NULL,
                            keys_p256dh VARCHAR(500) NOT NULL,
                            keys_auth VARCHAR(500) NOT NULL,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user_id ON push_subscriptions(user_id)"))
                    conn.commit()
                logger.info("Created push_subscriptions table")
            except Exception as tbl_err:
                logger.warning(f"Could not create push_subscriptions: {tbl_err}")
            
    except Exception as e:
        logger.error(f"Error during database schema check: {e}")


async def start_reminder_service(app):
    logger.info("Starting ReminderService...")
    await reminder_service.start()
    logger.info("ReminderService started successfully")

    # Пинг IndexNow при старте — уведомляем поисковики о всех страницах
    try:
        await notify_indexnow([
            "https://asibiont.com/",
            "https://asibiont.com/subscription-tiers",
            "https://asibiont.com/dashboard",
            "https://asibiont.com/faq",
            "https://asibiont.com/llms.txt",
            "https://asibiont.com/llms-full.txt"
        ])
        logger.info("[IndexNow] Pinged search engines about all pages")
    except Exception as e:
        logger.warning(f"[IndexNow] Startup ping failed: {e}")

    # Log existing jobs
    jobs = reminder_service.scheduler.get_jobs()
    logger.info(f"Scheduled jobs after start: {len(jobs)}")
    for job in jobs[:5]:  # Log first 5 jobs
        logger.info(f"Job: {job.id} at {job.next_run_time}")


async def start_auto_post_service(app):
    """Start background auto-post service (proactive newsfeed posts)"""
    try:
        asyncio.create_task(auto_post_run_service())
        logger.info("Auto-post service started as background task")
    except Exception as e:
        logger.warning(f"Auto-post service startup error: {e}")

app.on_startup.append(ensure_database_schema)  # Run migrations first
app.on_startup.append(start_reminder_service)
app.on_startup.append(on_startup)
# auto_post_service DISABLED — handled by AnchorEngine (post_opportunity anchors)
# app.on_startup.append(start_auto_post_service)
app.on_shutdown.append(on_shutdown)


async def start_discord(app):
    try:
        from discord_bot import start_discord_bot
        await start_discord_bot()
    except Exception as e:
        logger.warning(f"Discord bot startup error: {e}")


app.on_startup.append(start_discord)

if bot:
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET or None,
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

                # Auto-post service — DISABLED, handled by AnchorEngine (post_opportunity anchors)
                auto_post_task = None
                # AnchorEngine._scan_post_opportunities() создаёт посты в ленту (все тарифы)
                # AI сам решает что публиковать на основе всех данных пользователя
                logger.info("Auto-post service DISABLED — handled by AnchorEngine")

                # Auto-marketing service — DISABLED, handled by AnchorEngine (channel_post anchors)
                auto_marketing_task = None
                # AnchorEngine._scan_channel_post() постит в канал PREMIUM пользователей
                # AI сам генерирует контент на основе профиля и контент-стратегии
                logger.info("Auto-marketing service DISABLED — handled by AnchorEngine")

                # Start contact alerts service — DISABLED, handled by AnchorEngine (contact_match anchors)
                contact_alerts_task = None
                # ContactAlertsService дублирует AnchorEngine._scan_contacts()
                # AnchorEngine проверяет ContactAlert каждые 20 мин с DND, ночными часами, cooldown
                logger.info("Contact alerts service DISABLED — handled by AnchorEngine")

                # Start AnchorEngine — unified event-driven autonomous system
                anchor_engine_task = None
                try:
                    from anchor_engine import start_anchor_engine

                    async def _anchor_engine_supervisor():
                        """Супервизор: перезапускает AnchorEngine при сбое"""
                        restart_delay = 60  # первый перезапуск через 1 мин
                        while True:
                            try:
                                logger.info("AnchorEngine supervisor: starting engine...")
                                await start_anchor_engine(bot)
                            except asyncio.CancelledError:
                                logger.info("AnchorEngine supervisor: cancelled, stopping")
                                return
                            except Exception as _ae_err:
                                logger.error(f"AnchorEngine crashed: {_ae_err}, restarting in {restart_delay}s")
                                await asyncio.sleep(restart_delay)
                                restart_delay = min(restart_delay * 2, 600)  # max 10 мин
                            else:
                                # engine.start() завершился (running=False) — перезапустим через 30с
                                logger.warning("AnchorEngine exited normally, restarting in 30s")
                                await asyncio.sleep(30)
                                restart_delay = 60  # сбрасываем задержку

                    logger.info("Starting AnchorEngine in background...")
                    anchor_engine_task = asyncio.create_task(_anchor_engine_supervisor())
                    logger.info("AnchorEngine task created")
                except Exception as e:
                    logger.error(f"Failed to start AnchorEngine: {e}")

                # Start Living Office Engine — L1 мониторинг скриптов + L2 координатор
                try:
                    from ai_integration.office_engine import start_office_engine
                    start_office_engine()
                    logger.info("[OFFICE] Living Office Engine started")
                except Exception as _oe_err:
                    logger.warning(f"[OFFICE] Failed to start OfficeEngine: {_oe_err}")

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
                    if anchor_engine_task and not anchor_engine_task.done():
                        logger.info("Cancelling AnchorEngine...")
                        anchor_engine_task.cancel()
                    await runner.cleanup()
                    logger.info("Server shut down")

            asyncio.run(run_server())
        except Exception as serve_error:
            logger.error(f"Error in asyncio run: {serve_error}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Failed to start application: {e}", exc_info=True)
        raise
