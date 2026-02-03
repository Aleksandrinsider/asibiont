from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction, UserRating, SubscriptionTier, PromoCode, PaymentHistory, Post, PostLike, Comment, PostView, init_db
from reminder_service import ReminderService
from ai_integration import chat_with_ai, get_partners_list, decrypt_data, encrypt_data
from datetime import datetime, timedelta, timezone as dt_timezone
from config import TELEGRAM_TOKEN, TELEGRAM_BOT_USERNAME, PORT, CURRENT_DATE, DATABASE_URL, LOCAL
from aiohttp_session import SimpleCookieStorage
from aiohttp_session import get_session
import aiohttp_session
import os
from sqlalchemy import text, or_, and_, inspect
import re
import jinja2
import aiohttp_jinja2
from aiohttp import web
import aiohttp
import asyncio
import logging
import pytz

# Import handlers
from handlers import router as handlers_router
import hashlib
import hmac
import json
import warnings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
        'СЃРїР±': 'saint petersburg',
        'екатеринбург': 'yekaterinburg',
        'новосибирск': 'novosibirsk',
        'казань': 'kazan'
    }
    return city_map.get(city, city)


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info("Database Connection")
logger.info("Attempting to connect to the database...")

try:
    # Test database connection
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("вњ… Database connection successful")

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
except Exception as e:
    logger.error(f"вќЊ Database connection failed: {e}")
    logger.error("Application may not work correctly without database connection")
    # Don't exit, let the app start anyway for webhook setup
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite database connection issues")

try:
    logger.info("Creating database tables...")
    Base.metadata.create_all(engine)
    logger.info("вњ… Database tables created or already exist")
except Exception as e:
    logger.error(f"вќЊ Failed to create database tables: {e}")
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite table creation issues")

logger.info("Running database migrations...")
try:
    try:
        session = Session()
        inspector = inspect(engine)

        # Migration for user_profiles table columns
        if inspector.has_table('user_profiles'):
            columns = [col['name'] for col in inspector.get_columns('user_profiles')]

            # Migration for activity_streak column
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

            # Migration for birthdate column
            if 'birthdate' not in columns:
                logger.info("Adding birthdate column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN birthdate VARCHAR(10)'))
                session.commit()
                logger.info("Migration: birthdate column added successfully")
            else:
                logger.info("Migration: birthdate column already exists")

            # Migration for interests column
            if 'interests' not in columns:
                logger.info("Adding interests column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN interests TEXT'))
                session.commit()
                logger.info("Migration: interests column added successfully")
            else:
                logger.info("Migration: interests column already exists")

            # Migration for city column
            if 'city' not in columns:
                logger.info("Adding city column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN city VARCHAR(100)'))
                session.commit()
                logger.info("Migration: city column added successfully")
            else:
                logger.info("Migration: city column already exists")

            # Migration for company column
            if 'company' not in columns:
                logger.info("Adding company column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN company VARCHAR(200)'))
                session.commit()
                logger.info("Migration: company column added successfully")
            else:
                logger.info("Migration: company column already exists")

            # Migration for position column
            if 'position' not in columns:
                logger.info("Adding position column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN position VARCHAR(200)'))
                session.commit()
                logger.info("Migration: position column added successfully")
            else:
                logger.info("Migration: position column already exists")

            # Migration for timezone column
            if 'timezone' not in columns:
                logger.info("Adding timezone column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN timezone VARCHAR(50) DEFAULT \'UTC\''))
                session.commit()
                logger.info("Migration: timezone column added successfully")
            else:
                logger.info("Migration: timezone column already exists")

            # Migration for subscription_tier column
            if 'subscription_tier' not in columns:
                logger.info("Adding subscription_tier column to user_profiles table")
                if LOCAL:
                    session.execute(text('ALTER TABLE user_profiles ADD COLUMN subscription_tier TEXT DEFAULT \'LIGHT\''))
                else:
                    # First, create the enum type if it doesn't exist
                    try:
                        session.execute(text('CREATE TYPE subscription_tier_enum AS ENUM (\'LIGHT\', \'STANDARD\', \'PREMIUM\')'))
                        session.commit()
                        logger.info("Migration: subscription_tier_enum type created")
                    except Exception as e:
                        logger.info(f"Migration: subscription_tier_enum type already exists or error: {e}")
                        session.rollback()
                    
                    # Now add the column
                    session.execute(text('ALTER TABLE user_profiles ADD COLUMN subscription_tier subscription_tier_enum DEFAULT \'LIGHT\''))
                session.commit()
                logger.info("Migration: subscription_tier column added successfully")
            else:
                logger.info("Migration: subscription_tier column already exists")

            # Migration for subscription_expires_at column
            if 'subscription_expires_at' not in columns:
                logger.info("Adding subscription_expires_at column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN subscription_expires_at TIMESTAMP'))
                session.commit()
                logger.info("Migration: subscription_expires_at column added successfully")
            else:
                logger.info("Migration: subscription_expires_at column already exists")

            # Migration for subscription_renewal_date column
            if 'subscription_renewal_date' not in columns:
                logger.info("Adding subscription_renewal_date column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN subscription_renewal_date TIMESTAMP'))
                session.commit()
                logger.info("Migration: subscription_renewal_date column added successfully")
            else:
                logger.info("Migration: subscription_renewal_date column already exists")

        # Migration for users table columns
        if inspector.has_table('users'):
            user_columns = [col['name'] for col in inspector.get_columns('users')]

            # Migration for referral_balance column
            if 'referral_balance' not in user_columns:
                logger.info("Adding referral_balance column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN referral_balance INTEGER DEFAULT 0'))
                session.commit()
                logger.info("Migration: referral_balance column added successfully")
            else:
                logger.info("Migration: referral_balance column already exists")

            # Migration for referrer_id column
            if 'referrer_id' not in user_columns:
                logger.info("Adding referrer_id column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id)'))
                session.commit()
                logger.info("Migration: referrer_id column added successfully")
            else:
                logger.info("Migration: referrer_id column already exists")

        # Migration for tasks table
        if not inspector.has_table('tasks'):
            logger.info("Creating tasks table")
            if LOCAL:
                session.execute(text('''
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        title VARCHAR(500) NOT NULL,
                        description TEXT,
                        status VARCHAR(20) DEFAULT 'pending',
                        priority VARCHAR(20) DEFAULT 'medium',
                        reminder_time TIMESTAMP,
                        actual_completion_time TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                '''))
            else:
                session.execute(text('''
                    CREATE TABLE tasks (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        title VARCHAR(500) NOT NULL,
                        description TEXT,
                        status VARCHAR(20) DEFAULT 'pending',
                        priority VARCHAR(20) DEFAULT 'medium',
                        reminder_time TIMESTAMP,
                        actual_completion_time TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
            session.commit()
            logger.info("Migration: tasks table created successfully")
        else:
            logger.info("Migration: tasks table already exists")

            # Migration for recurring task columns in tasks table
            task_columns = [col['name'] for col in inspector.get_columns('tasks')]
            
            # Migration for is_recurring column
            if 'is_recurring' not in task_columns:
                logger.info("Adding is_recurring column to tasks table")
                session.execute(text('ALTER TABLE tasks ADD COLUMN is_recurring BOOLEAN DEFAULT FALSE'))
                session.commit()
                logger.info("Migration: is_recurring column added successfully")
            else:
                logger.info("Migration: is_recurring column already exists")

            # Migration for recurrence_pattern column
            if 'recurrence_pattern' not in task_columns:
                logger.info("Adding recurrence_pattern column to tasks table")
                session.execute(text('ALTER TABLE tasks ADD COLUMN recurrence_pattern VARCHAR(50)'))
                session.commit()
                logger.info("Migration: recurrence_pattern column added successfully")
            else:
                logger.info("Migration: recurrence_pattern column already exists")

            # Migration for recurrence_interval column
            if 'recurrence_interval' not in task_columns:
                logger.info("Adding recurrence_interval column to tasks table")
                session.execute(text('ALTER TABLE tasks ADD COLUMN recurrence_interval INTEGER DEFAULT 1'))
                session.commit()
                logger.info("Migration: recurrence_interval column added successfully")
            else:
                logger.info("Migration: recurrence_interval column already exists")

            # Migration for recurrence_end_date column
            if 'recurrence_end_date' not in task_columns:
                logger.info("Adding recurrence_end_date column to tasks table")
                session.execute(text('ALTER TABLE tasks ADD COLUMN recurrence_end_date TIMESTAMP'))
                session.commit()
                logger.info("Migration: recurrence_end_date column added successfully")
            else:
                logger.info("Migration: recurrence_end_date column already exists")

            # Migration for parent_task_id column
            if 'parent_task_id' not in task_columns:
                logger.info("Adding parent_task_id column to tasks table")
                session.execute(text('ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)'))
                session.commit()
                logger.info("Migration: parent_task_id column added successfully")
            else:
                logger.info("Migration: parent_task_id column already exists")

        # Migration for posts table
        if not inspector.has_table('posts'):
            logger.info("Creating posts table")
            if LOCAL:
                session.execute(text('''
                    CREATE TABLE posts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        username VARCHAR(100) NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                '''))
            else:
                session.execute(text('''
                    CREATE TABLE posts (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        username VARCHAR(100) NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
            session.commit()
            logger.info("Migration: posts table created successfully")
        else:
            logger.info("Migration: posts table already exists")

        # Migration for subscriptions table
        if not inspector.has_table('subscriptions'):
            logger.info("Creating subscriptions table")
            if LOCAL:
                session.execute(text('''
                    CREATE TABLE subscriptions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        tier TEXT DEFAULT 'FREE',
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                '''))
            else:
                session.execute(text('''
                    CREATE TABLE subscriptions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        tier subscription_tier_enum DEFAULT 'FREE',
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
            session.commit()
            logger.info("Migration: subscriptions table created successfully")
        else:
            logger.info("Migration: subscriptions table already exists")

        # Migration for payments table
        if not inspector.has_table('payments'):
            logger.info("Creating payments table")
            if LOCAL:
                session.execute(text('''
                    CREATE TABLE payments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        amount DECIMAL(10,2) NOT NULL,
                        currency VARCHAR(3) DEFAULT 'RUB',
                        status VARCHAR(20) DEFAULT 'pending',
                        payment_id VARCHAR(100),
                        tier TEXT DEFAULT 'LIGHT',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (id)
                    )
                '''))
            else:
                session.execute(text('''
                    CREATE TABLE payments (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id),
                        amount DECIMAL(10,2) NOT NULL,
                        currency VARCHAR(3) DEFAULT 'RUB',
                        status VARCHAR(20) DEFAULT 'pending',
                        payment_id VARCHAR(100),
                        tier subscription_tier_enum DEFAULT 'LIGHT',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
            session.commit()
            logger.info("Migration: payments table created successfully")
        else:
            logger.info("Migration: payments table already exists")

        # Migration for promo_codes table
        if not inspector.has_table('promo_codes'):
            logger.info("Creating promo_codes table")
            if LOCAL:
                session.execute(text('''
                    CREATE TABLE promo_codes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code VARCHAR(50) UNIQUE NOT NULL,
                        tier TEXT DEFAULT 'LIGHT',
                        duration_days INTEGER DEFAULT 30,
                        expires_at TIMESTAMP NOT NULL,
                        is_used BOOLEAN DEFAULT FALSE,
                        used_by_user_id INTEGER,
                        used_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (used_by_user_id) REFERENCES users (id)
                    )
                '''))
            else:
                session.execute(text('''
                    CREATE TABLE promo_codes (
                        id SERIAL PRIMARY KEY,
                        code VARCHAR(50) UNIQUE NOT NULL,
                        tier subscription_tier_enum DEFAULT 'LIGHT',
                        duration_days INTEGER DEFAULT 30,
                        expires_at TIMESTAMP NOT NULL,
                        is_used BOOLEAN DEFAULT FALSE,
                        used_by_user_id INTEGER REFERENCES users(id),
                        used_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
            session.commit()
            logger.info("Migration: promo_codes table created successfully")
        else:
            logger.info("Migration: promo_codes table already exists")
            # Add missing columns if they don't exist
            inspector = inspect(engine)
            columns = [col['name'] for col in inspector.get_columns('promo_codes')]
            if 'discount_percent' not in columns:
                logger.info("Adding discount_percent column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN discount_percent INTEGER DEFAULT 0"))
            if 'max_uses' not in columns:
                logger.info("Adding max_uses column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN max_uses INTEGER"))
            if 'used_count' not in columns:
                logger.info("Adding used_count column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN used_count INTEGER DEFAULT 0"))
            if 'used_by_users' not in columns:
                logger.info("Adding used_by_users column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN used_by_users TEXT DEFAULT '[]'"))
            if 'used_by_user_id' not in columns:
                logger.info("Adding used_by_user_id column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN used_by_user_id INTEGER"))
            if 'used_at' not in columns:
                logger.info("Adding used_at column to promo_codes")
                session.execute(text("ALTER TABLE promo_codes ADD COLUMN used_at TIMESTAMP"))
            session.commit()

        # Migration for users table - current_task_id column
        if inspector.has_table('users'):
            user_columns = [col['name'] for col in inspector.get_columns('users')]
            if 'current_task_id' not in user_columns:
                logger.info("Adding current_task_id column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN current_task_id INTEGER REFERENCES tasks(id)'))
                session.commit()
                logger.info("Migration: current_task_id column added successfully")
            else:
                logger.info("Migration: current_task_id column already exists")

            # Migration for users table - referral_balance and referrer_id columns
            if 'referral_balance' not in user_columns:
                logger.info("Adding referral_balance column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN referral_balance INTEGER DEFAULT 0'))
                session.commit()
                logger.info("Migration: referral_balance column added successfully")
            else:
                logger.info("Migration: referral_balance column already exists")

            if 'referrer_id' not in user_columns:
                logger.info("Adding referrer_id column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id)'))
                session.commit()
                logger.info("Migration: referrer_id column added successfully")
            else:
                logger.info("Migration: referrer_id column already exists")

        session.close()
        logger.info("Migration session closed successfully")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        session.close()
        raise

    logger.info("вњ… Database migrations completed")
except Exception as e:
    logger.error(f"вќЊ Database migrations failed: {e}")
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite migration issues")

# Test data creation removed for production

# Create test promo codes (3 for each tier, valid until Feb 1, 2026, unlimited uses)
# COMMENTED OUT FOR PRODUCTION - Remove this block before deploying
# try:
#     session_db = Session()

#     # Check if promo codes already exist
#     existing_promos = session_db.query(PromoCode).filter(PromoCode.code.like('SPORT%')).all()
#     if existing_promos:
#         logger.info(f"Test promo codes already exist: {[p.code for p in existing_promos]}")
#     else:
#         # Create promo codes valid until February 1, 2026
#         expires_at = datetime(2026, 2, 1, tzinfo=dt_timezone.utc)

#         # LIGHT tier promo codes
#         light_promos = [
#             PromoCode(code='SPORTLIGHT1', tier=SubscriptionTier.LIGHT, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTLIGHT2', tier=SubscriptionTier.LIGHT, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTLIGHT3', tier=SubscriptionTier.LIGHT, duration_days=30, max_uses=None, expires_at=expires_at),
#         ]

#         # STANDARD tier promo codes
#         standard_promos = [
#             PromoCode(code='SPORTSTAND1', tier=SubscriptionTier.STANDARD, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTSTAND2', tier=SubscriptionTier.STANDARD, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTSTAND3', tier=SubscriptionTier.STANDARD, duration_days=30, max_uses=None, expires_at=expires_at),
#         ]

#         # PREMIUM tier promo codes
#         premium_promos = [
#             PromoCode(code='SPORTPREM1', tier=SubscriptionTier.PREMIUM, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTPREM2', tier=SubscriptionTier.PREMIUM, duration_days=30, max_uses=None, expires_at=expires_at),
#             PromoCode(code='SPORTPREM3', tier=SubscriptionTier.PREMIUM, duration_days=30, max_uses=None, expires_at=expires_at),
#         ]

#         # Add all promo codes
#         all_promos = light_promos + standard_promos + premium_promos
#         for promo in all_promos:
#             session_db.add(promo)

#         session_db.commit()
#         logger.info(f"Created {len(all_promos)} test promo codes valid until {expires_at.date()}")
#         for promo in all_promos:
#             logger.info(f"  - {promo.code}: {promo.tier.value} tier for {promo.duration_days} days")

#     session_db.close()
# except Exception as e:
#     logger.error(f"Failed to create test promo codes: {e}")

# Test database connection before starting
try:
    test_session = Session()
    test_session.execute(text('SELECT 1'))
    test_session.close()
    logger.info("вњ… Database connection successful")
except Exception as e:
    logger.error(f"вќЊ CRITICAL: Cannot connect to database: {e}", exc_info=True)
    logger.error(f"DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "DATABASE_URL not set")
    # Don't exit, let Railway restart the app

try:
    # Migrations are already run above
    logger.info("Database migrations completed")
    # Production mode: Test users and promo codes disabled
    logger.info("Production mode: Test data creation disabled")

    # Create test users ONLY when explicitly enabled with CREATE_TEST_USERS=1
    if os.getenv('CREATE_TEST_USERS') == '1':
        try:
            session_db = Session()
            logger.info("Creating test users with different subscription tiers")

            test_users_data = [
                # LIGHT tier users
                {'telegram_id': 1001, 'tier': 'LIGHT', 'name': 'Test User 1', 'city': 'РњРѕСЃРєвР°', 'username': 'test1'},
                {'telegram_id': 1002, 'tier': 'LIGHT', 'name': 'Test User 2', 'city': 'РЎР°наєС‚-РџРµС‚РµСЂР±СѓСЂРі', 'username': 'test2'},
                {'telegram_id': 1003, 'tier': 'LIGHT', 'name': 'Test User 3', 'city': 'Р•РєР°С‚РµСЂРёна±СѓСЂРі', 'username': 'test3'},
                {'telegram_id': 1004, 'tier': 'LIGHT', 'name': 'Test User 4', 'city': 'РќРѕвРѕСЃРёР±РёСЂСЃРє', 'username': 'test4'},
                {'telegram_id': 1005, 'tier': 'LIGHT', 'name': 'Test User 5', 'city': 'РљР°Р·Р°РЅСЊ', 'username': 'test5'},
                {'telegram_id': 1006, 'tier': 'LIGHT', 'name': 'Test User 6', 'city': 'РќРёР¶наёР№ РќРѕвРіРѕСЂРѕРґ', 'username': 'test6'},
                {'telegram_id': 1007, 'tier': 'LIGHT', 'name': 'Test User 7', 'city': 'Р§РµР»СЏР±РёРЅСЃРє', 'username': 'test7'},
                {'telegram_id': 1008, 'tier': 'LIGHT', 'name': 'Test User 8', 'city': 'РћРјСЃРє', 'username': 'test8'},

                # STANDARD tier users
                {'telegram_id': 1009, 'tier': 'STANDARD', 'name': 'Test User 9', 'city': 'Р РѕСЃС‚Рѕв-на°-Р”РѕРЅСѓ', 'username': 'test9'},
                {'telegram_id': 1010, 'tier': 'STANDARD', 'name': 'Test User 10', 'city': 'РЈС„Р°', 'username': 'test10'},
                {'telegram_id': 1011, 'tier': 'STANDARD', 'name': 'Test User 11', 'city': 'Р’РѕР»РіРѕРіСЂР°Рґ', 'username': 'test11'},
                {'telegram_id': 1012, 'tier': 'STANDARD', 'name': 'Test User 12', 'city': 'РљСЂР°СЃнаѕСЏСЂСЃРє', 'username': 'test12'},
                {'telegram_id': 1013, 'tier': 'STANDARD', 'name': 'Test User 13', 'city': 'Р’РѕСЂРѕнаµР¶', 'username': 'test13'},
                {'telegram_id': 1014, 'tier': 'STANDARD', 'name': 'Test User 14', 'city': 'РџРµСЂРјСЊ', 'username': 'test14'},

                # PREMIUM tier users
                {'telegram_id': 1015, 'tier': 'PREMIUM', 'name': 'Test User 15', 'city': 'РљСЂР°СЃнаѕРґР°СЂ', 'username': 'test15'},
                {'telegram_id': 1016, 'tier': 'PREMIUM', 'name': 'Test User 16', 'city': 'РўСЋРјРµРЅСЊ', 'username': 'test16'},
                {'telegram_id': 1017, 'tier': 'PREMIUM', 'name': 'Test User 17', 'city': 'Р‘Р°СЂна°СѓР»', 'username': 'test17'},
                {'telegram_id': 1018, 'tier': 'PREMIUM', 'name': 'Test User 18', 'city': 'РР¶РµвСЃРє', 'username': 'test18'},
                {'telegram_id': 1019, 'tier': 'PREMIUM', 'name': 'Test User 19', 'city': 'Р’Р»Р°РґРёвРѕСЃС‚РѕРє', 'username': 'test19'},
                {'telegram_id': 1020, 'tier': 'PREMIUM', 'name': 'Test User 20', 'city': 'РЇСЂРѕСЃР»Р°вР»СЊ', 'username': 'test20'},
            ]

            now = datetime.now()

            added_count = 0
            updated_count = 0
            for user_data in test_users_data:
                # Check if user already exists
                existing_user = session_db.query(User).filter(User.telegram_id == user_data['telegram_id']).first()
                if existing_user:
                    logger.info(f"Test user {user_data['telegram_id']} already exists, updating profile interests")
                    # Update existing profile interests to 'СЃРїРѕСЂС‚'
                    existing_profile = session_db.query(UserProfile).filter_by(user_id=existing_user.id).first()
                    if existing_profile:
                        existing_profile.interests = 'СЃРїРѕСЂС‚'
                        updated_count += 1
                        logger.info(f"Updated interests for test user {user_data['telegram_id']}")
                    continue

                # Create user
                user = User(
                    telegram_id=user_data['telegram_id'],
                    username=user_data['username'],
                    first_name=user_data['name'],
                    photo_url=f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN or 'fake_token'}/photos/file_test_{user_data['telegram_id']}.jpg?r={user_data['telegram_id']}",  # Fake avatar URL for testing
                    subscription_tier=user_data['tier'],  # Set subscription tier
                    created_at=now
                )
                session_db.add(user)
                session_db.flush()  # Get user.id

                # Create profile with sport interests
                profile = UserProfile(
                    user_id=user.id,
                    interests='СЃРїРѕСЂС‚',  # All users have 'sport' interest
                    city=user_data['city'],  # Use city from user data
                    contact_info=f'user{user_data["telegram_id"]}@test.com'
                )
                session_db.add(profile)

                # Create active subscription
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

                logger.info(f"Created test user {user_data['telegram_id']} with {user_data['tier']} tier")
                added_count += 1

            # Create test tasks for delegation testing
            if added_count > 0:
                # Create tasks delegated to user 1001 from other users
                user_1001 = session_db.query(User).filter_by(telegram_id=1001).first()
                user_1002 = session_db.query(User).filter_by(telegram_id=1002).first()
                user_1003 = session_db.query(User).filter_by(telegram_id=1003).first()

                if user_1001 and user_1002:
                    # Task from user 1002 delegated to user 1001
                    task1 = Task(
                        user_id=user_1002.id,
                        title="РџРѕРґРіРѕС‚РѕвРёС‚СЊ РїСЂРµР·РµРЅС‚Р°С†РёСЋ РґР»СЏ РєР»РёРµРЅС‚Р°",
                        description="РЎРѕР·РґР°С‚СЊ РїСЂРµР·РµРЅС‚Р°С†РёСЋ Рѕ на°С€РёС… СѓСЃР»СѓРіР°С…",
                        status="pending",
                        created_at=now,
                        delegated_to_username=user_1001.username,
                        delegation_status="accepted"
                    )
                    session_db.add(task1)
                    logger.info("Created delegated task from user 1002 to user 1001")

                if user_1001 and user_1003:
                    # Task from user 1003 delegated to user 1001
                    task2 = Task(
                        user_id=user_1003.id,
                        title="РџСЂРѕвРµСЂРёС‚СЊ РєРѕРґ на° РѕС€РёР±РєРё",
                        description="Р РµвСЊСЋ РєРѕРґР° РґР»СЏ наѕвРѕРіРѕ РјРѕРґСѓР»СЏ",
                        status="pending",
                        created_at=now,
                        delegated_to_username=user_1001.username,
                        delegation_status="accepted"
                    )
                    session_db.add(task2)
                    logger.info("Created delegated task from user 1003 to user 1001")

                # Create task delegated by user 1001 to user 1002
                if user_1001 and user_1002:
                    task3 = Task(
                        user_id=user_1001.id,
                        title="РћСЂРіР°наёР·РѕвР°С‚СЊ вСЃС‚СЂРµС‡Сѓ СЃ РєРѕРјР°наґРѕР№",
                        description="Р—Р°РїР»Р°наёСЂРѕвР°С‚СЊ РµР¶РµнаµРґРµР»СЊРЅСѓСЋ вСЃС‚СЂРµС‡Сѓ",
                        status="pending",
                        created_at=now,
                        delegated_to_username=user_1002.username,
                        delegation_status="accepted"
                    )
                    session_db.add(task3)
                    logger.info("Created delegated task from user 1001 to user 1002")

            if added_count > 0 or updated_count > 0:
                session_db.commit()
                if added_count > 0:
                    logger.info(f"Successfully added {added_count} test users")
                if updated_count > 0:
                    logger.info(f"Successfully updated {updated_count} test user profiles")
            else:
                logger.info("All test users already exist and up to date")
        except Exception as e:
            logger.error(f"Error creating test users: {e}")
            session_db.rollback()
        finally:
            if 'session_db' in locals():
                session_db.close()
    else:
        logger.info("Skipping test user creation (not in local mode)")

except Exception as e:
    logger.error(f"Failed to run migrations: {e}", exc_info=True)


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
        
        interactions = query.order_by(Interaction.created_at.desc()).limit(limit * 2).all()
        interactions.reverse()  # Oldest first
        
        # Convert to context format
        context = []
        for i in range(0, len(interactions), 2):
            if i + 1 < len(interactions):
                user_msg = interactions[i]
                ai_msg = interactions[i + 1]
                if user_msg.message_type == 'user' and ai_msg.message_type == 'ai':
                    context.append({
                        'user': user_msg.content,
                        'agent': ai_msg.content
                    })
        
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
    """РћРїСЂРµРґРµР»СЏРµС‚ timezone РїРѕ IP Р°РґСЂРµСЃСѓ С‡РµСЂРµР· ipapi.co"""
    # РњР°РїРїРёнаі Р°наіР»РёР№СЃРєРёС… на°Р·вР°наёР№ РіРѕСЂРѕРґРѕв на° СЂСѓСЃСЃРєРёРµ
    city_mapping = {
        'Moscow': 'РњРѕСЃРєвР°',
        'Saint Petersburg': 'РЎР°наєС‚-РџРµС‚РµСЂР±СѓСЂРі',
        'Kazan': 'РљР°Р·Р°РЅСЊ',
        'Novosibirsk': 'РќРѕвРѕСЃРёР±РёСЂСЃРє',
        'Yekaterinburg': 'Р•РєР°С‚РµСЂРёна±СѓСЂРі',
        'Nizhny Novgorod': 'РќРёР¶наёР№ РќРѕвРіРѕСЂРѕРґ',
        'Chelyabinsk': 'Р§РµР»СЏР±РёРЅСЃРє',
        'Omsk': 'РћРјСЃРє',
        'Samara': 'РЎР°РјР°СЂР°',
        'Rostov-on-Don': 'Р РѕСЃС‚Рѕв-на°-Р”РѕРЅСѓ',
        'Ufa': 'РЈС„Р°',
        'Krasnoyarsk': 'РљСЂР°СЃнаѕСЏСЂСЃРє',
        'Voronezh': 'Р’РѕСЂРѕнаµР¶',
        'Perm': 'РџРµСЂРјСЊ',
        'Volgograd': 'Р’РѕР»РіРѕРіСЂР°Рґ',
        'Krasnodar': 'РљСЂР°СЃнаѕРґР°СЂ',
        'Saratov': 'РЎР°СЂР°С‚Рѕв',
        'Tyumen': 'РўСЋРјРµРЅСЊ',
        'Tolyatti': 'РўРѕР»СЊСЏС‚С‚Рё',
        'Izhevsk': 'РР¶РµвСЃРє',
        'Barnaul': 'Р‘Р°СЂна°СѓР»',
        'Ulyanovsk': 'РЈР»СЊСЏнаѕвСЃРє',
        'Irkutsk': 'РСЂРєСѓС‚СЃРє',
        'Khabarovsk': 'РҐР°Р±Р°СЂРѕвСЃРє',
        'Vladivostok': 'Р’Р»Р°РґРёвРѕСЃС‚РѕРє',
        'Yaroslavl': 'РЇСЂРѕСЃР»Р°вР»СЊ',
        'Vladimir': 'Р’Р»Р°РґРёРјРёСЂ',
        'Ivanovo': 'РвР°наѕвРѕ',
        'Bryansk': 'Р‘СЂСЏРЅСЃРє',
        'Smolensk': 'РЎРјРѕР»РµРЅСЃРє',
        'Kaluga': 'РљР°Р»СѓРіР°',
        'Tula': 'РўСѓР»Р°',
        'Ryazan': 'Р СЏР·Р°РЅСЊ',
        'Moscow Oblast': 'РњРѕСЃРєРѕвСЃРєР°СЏ РѕР±Р»Р°СЃС‚СЊ',
        'Leningrad Oblast': 'Р›РµнаёнаіСЂР°РґСЃРєР°СЏ РѕР±Р»Р°СЃС‚СЊ'
    }

    try:
        # РРінаѕСЂРёСЂСѓРµРј Р»РѕРєР°Р»СЊРЅС‹Рµ IP
        if ip_address.startswith(('127.', '192.168.', '10.', '172.')):
            return 'Europe/Moscow', 'РњРѕСЃРєвР°'  # РџРѕ СѓРјРѕР»С‡Р°наёСЋ РґР»СЏ Р»РѕРєР°Р»СЊРЅС‹С…

        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://ipapi.co/{ip_address}/json/', timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    timezone = data.get('timezone')
                    city = data.get('city')

                    # РџСЂРµРѕР±СЂР°Р·СѓРµРј Р°наіР»РёР№СЃРєРѕРµ на°Р·вР°наёРµ РіРѕСЂРѕРґР° в СЂСѓСЃСЃРєРѕРµ, РµСЃР»Рё РµСЃС‚СЊ в РјР°РїРїРёнаіРµ
                    if city and city in city_mapping:
                        city = city_mapping[city]

                    logger.info(f"Detected timezone: {timezone}, city: {city} for IP: {ip_address}")
                    return timezone if timezone else 'UTC', city
    except Exception as e:
        logger.error(f"Error getting timezone from IP {ip_address}: {e}")
    return 'UTC', None


async def get_user_avatar_url(bot, user_id, force_refresh=False):
    """РџРѕР»СѓС‡Р°РµС‚ URL Р°вР°С‚Р°СЂР° РїРѕР»СЊР·РѕвР°С‚РµР»СЏ РёР· Telegram РёР»Рё Р‘Р”
    
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
            
            # Р•СЃР»Рё наµ С‚СЂРµР±СѓРµС‚СЃСЏ РїСЂРёРЅСѓРґРёС‚РµР»СЊнаѕРµ РѕР±наѕвР»РµнаёРµ Рё РµСЃС‚СЊ РєСЌС€РёСЂРѕвР°наЅС‹Р№ Р°вР°С‚Р°СЂ, вРѕР·вСЂР°С‰Р°РµРј РµРіРѕ
            if not force_refresh and user and user.photo_url:
                logger.debug(f"Returning cached avatar for user {user_id}")
                return user.photo_url
            
            # Р—Р°РіСЂСѓР¶Р°РµРј СЃвРµР¶РёР№ Р°вР°С‚Р°СЂ РёР· Telegram
            if bot:
                try:
                    photos = await bot.get_user_profile_photos(user_id, limit=1)
                    if photos.total_count > 0:
                        file = await bot.get_file(photos.photos[0][-1].file_id)
                        avatar_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
                        
                        # РЎРѕС…СЂР°РЅСЏРµРј в Р‘Р” РґР»СЏ РєСЌС€РёСЂРѕвР°наёСЏ
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
    # РџСЂРѕвРµСЂРєР° Р°вС‚РѕСЂРёР·Р°С†РёРё РѕС‚ Telegram
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
    """РЎС‚СЂР°наёС†Р° Р°вС‚РѕСЂРёР·Р°С†РёРё"""
    session = await get_session(request)
    user_id = session.get('user_id')

    # Check for logout parameter
    if request.query.get('logout') == '1':
        session.pop('user_id', None)
        session.pop('history_cleared_timestamp', None)
        user_id = None

    # Р•СЃР»Рё РїРѕР»СЊР·РѕвР°С‚РµР»СЊ СѓР¶Рµ Р·Р°Р»РѕРіРёнаµРЅ, СЂРµРґРёСЂРµРєС‚РёРј на° dashboard
    if user_id:
        try:
            user_id = int(user_id)
            return web.HTTPFound('/dashboard')
        except (ValueError, TypeError):
            pass

    # РџРѕРєР°Р·С‹вР°РµРј СЃС‚СЂР°наёС†Сѓ Р°вС‚РѕСЂРёР·Р°С†РёРё
    bot_user = TELEGRAM_BOT_USERNAME.replace(
        '@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'asibiont_bot')
    
    # Р¤РѕСЂРјРёСЂСѓРµРј auth_url РґР»СЏ вРёРґР¶РµС‚Р° Telegram
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

                    # РћРїСЂРµРґРµР»СЏРµРј timezone РїРѕ IP
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

                    # РЎРѕР·РґР°РµРј РїСЂРѕС„РёР»СЊ СЃ РіРѕСЂРѕРґРѕРј, РµСЃР»Рё РѕРїСЂРµРґРµР»РёР»Рё
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
                return web.Response(text='РћС€РёР±РєР° РїРѕРґРєР»СЋС‡РµнаёСЏ Рє Р±Р°Р·Рµ РґР°наЅС‹С…. РџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.', status=500)
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

        # РџРѕР»СѓС‡РёС‚СЊ Р·Р°РґР°С‡Рё РїРѕР»СЊР·РѕвР°С‚РµР»СЏ
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                # Redirect to login page if user not found
                return web.HTTPFound('/')

            logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")
            
            # РџСЂРѕвРµСЂРёС‚СЊ РїРѕРґРїРёСЃРєСѓ
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()

            # РџСЂРѕвРµСЂРёС‚СЊ Рё РѕР±наѕвРёС‚СЊ СЃС‚Р°С‚СѓСЃ РёСЃС‚РµРєС€РёС… РїРѕРґРїРёСЃРѕРє
            if subscription and subscription.status == 'active' and subscription.end_date:
                now = datetime.now(pytz.UTC)
                if subscription.end_date.tzinfo is None:
                    subscription.end_date = subscription.end_date.replace(tzinfo=pytz.UTC)
                if subscription.end_date < now:
                    subscription.status = 'expired'
                    # user.subscription_tier = SubscriptionTier.LIGHT  # РЎР±СЂРѕСЃРёС‚СЊ С‚Р°СЂРёС„ на° Р±СЂРѕна·Сѓ РїСЂРё РёСЃС‚РµС‡РµнаёРё - СѓР±СЂР°наѕ РїРѕ РїСЂРѕСЃСЊР±Рµ РїРѕР»СЊР·РѕвР°С‚РµР»СЏ
                    session_db.commit()
                    logger.info(f"Subscription {subscription.id} expired, status set to 'expired'")

            # РЎРёРЅС…СЂРѕнаёР·РёСЂРѕвР°С‚СЊ С‚Р°СЂРёС„ РїРѕР»СЊР·РѕвР°С‚РµР»СЏ СЃ Р°РєС‚РёвнаѕР№ РїРѕРґРїРёСЃРєРѕР№
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

            # Р’ FREE_ACCESS_MODE наµ С‚СЂРµР±СѓРµС‚СЃСЏ Р°РєС‚Рёвна°СЏ РїРѕРґРїРёСЃРєР°
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

            # РџСЂРѕвРµСЂСЏРµРј timestamp РѕС‡РёСЃС‚РєРё РёСЃС‚РѕСЂРёРё РёР· Р‘Р”
            history_cleared_timestamp = None
            if user.history_cleared_at:
                history_cleared_timestamp = user.history_cleared_at.timestamp()
                logger.info(f"History cleared timestamp from DB: {history_cleared_timestamp}")

            # Р‘РµСЂРµРј РїРѕСЃР»РµРґнаёРµ 50 СЃРѕРѕР±С‰РµнаёР№, наѕ С„РёР»СЊС‚СЂСѓРµРј РїРѕ timestamp РѕС‡РёСЃС‚РєРё
            if user:
                all_interactions = list(
                    reversed(
                        session_db.query(Interaction).filter_by(
                            user_id=user.id).order_by(
                            Interaction.id.desc()).limit(50).all()))
                if history_cleared_timestamp:
                    # Р¤РёР»СЊС‚СЂСѓРµРј С‚РѕР»СЊРєРѕ СЃРѕРѕР±С‰РµнаёСЏ РїРѕСЃР»Рµ РѕС‡РёСЃС‚РєРё
                    filtered_interactions = []
                    for i in all_interactions:
                        try:
                            # Р•СЃР»Рё created_at naive (Р±РµР· tzinfo), СЃС‡РёС‚Р°РµРј РµРіРѕ UTC Рё РїСЂРѕСЃС‚Рѕ Р±РµСЂРµРј timestamp
                            # Р•СЃР»Рё СЃ tzinfo, РёСЃРїРѕР»СЊР·СѓРµРј РµРіРѕ timestamp
                            if i.created_at.tzinfo is None:
                                # Naive datetime - РёРЅС‚РµСЂРїСЂРµС‚РёСЂСѓРµРј РєР°Рє UTC на°РїСЂСЏРјСѓСЋ С‡РµСЂРµР· replace
                                interaction_ts = i.created_at.replace(tzinfo=dt_timezone.utc).timestamp()
                            else:
                                interaction_ts = i.created_at.timestamp()

                            logger.info(
                                f"Interaction ID {i.id}: created_at={i.created_at}, timestamp={interaction_ts}, clear_timestamp={history_cleared_timestamp}, include={interaction_ts > history_cleared_timestamp}")

                            if interaction_ts > history_cleared_timestamp:
                                filtered_interactions.append(i)
                        except Exception as e:
                            logger.error(f"Error processing interaction {i.id} timestamp: {e}")
                            # Р’ СЃР»СѓС‡Р°Рµ РѕС€РёР±РєРё РќР• вРєР»СЋС‡Р°РµРј СЃРѕРѕР±С‰РµнаёРµ (Р±РµР·РѕРїР°СЃнаµРµ СЃРєСЂС‹С‚СЊ)

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

            # РџРѕР»СѓС‡РёС‚СЊ РєРѕРЅС‚Р°РєС‚С‹ РїРѕ РґРµР»РµРіРёСЂРѕвР°наёСЋ
            delegating_to_me = []  # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рµ РґРµР»РµРіРёСЂРѕвР°Р»Рё Рјнаµ Р·Р°РґР°С‡Рё
            delegating_by_me = []  # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рј СЏ РґРµР»РµРіРёСЂРѕвР°Р» Р·Р°РґР°С‡Рё

            try:
                # РџРѕР»СѓС‡РёС‚СЊ СЃРїРёСЃРѕРє РёР·Р±СЂР°наЅС‹С… РєРѕРЅС‚Р°РєС‚Рѕв
                favorite_contacts = []
                if profile and profile.favorite_contacts:
                    try:
                        raw_favorites = json.loads(profile.favorite_contacts)
                        favorite_contacts = [str(c).lower().replace('@', '') if isinstance(c, str) else str(c) for c in raw_favorites]
                    except json.JSONDecodeError:
                        favorite_contacts = []

                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рµ РґРµР»РµРіРёСЂРѕвР°Р»Рё Рјнаµ Р·Р°РґР°С‡Рё (СЏ РїРѕР»СѓС‡Р°СЋ Р·Р°РґР°С‡Рё РѕС‚ наёС…)
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
                                'reason': f'РґРµР»РµРіРёСЂРѕвР°Р» {task_count} Р·Р°РґР°С‡',
                                'tasks': task_titles,
                                'task_count': task_count
                            })

                # Р”РѕР±Р°вРёС‚СЊ РёР·Р±СЂР°наЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹, Сѓ РєРѕС‚РѕСЂС‹С… вСЃРµ Р·Р°РґР°С‡Рё РѕС‚РєР»РѕнаµРЅС‹, наѕ РєРѕРЅС‚Р°РєС‚ в РёР·Р±СЂР°наЅРѕРј
                for favorite_username in favorite_contacts:
                    favorite_user = session_db.query(User).filter(
                        User.username.ilike(favorite_username)
                    ).first()
                    
                    if favorite_user and favorite_user.id != user.id and favorite_user.id not in delegator_ids:
                        # РџСЂРѕвРµСЂРёС‚СЊ, Р±С‹Р»Рё Р»Рё Сѓ СЌС‚РѕРіРѕ РєРѕРЅС‚Р°РєС‚Р° Р·Р°РґР°С‡Рё (вРєР»СЋС‡Р°СЏ РѕС‚РєР»РѕнаµнаЅС‹Рµ)
                        all_tasks_from_favorite = session_db.query(Task).filter(
                            Task.user_id == favorite_user.id,
                            Task.delegated_to_username.ilike(user.username.replace('@', ''))
                        ).all()
                        
                        if all_tasks_from_favorite:
                            # Р•СЃС‚СЊ РёСЃС‚РѕСЂРёСЏ РґРµР»РµРіРёСЂРѕвР°наёСЏ - РґРѕР±Р°вР»СЏРµРј в СЃРїРёСЃРѕРє
                            rejected_count = sum(1 for t in all_tasks_from_favorite if t.status == 'rejected')
                            if rejected_count > 0:
                                delegating_to_me.append({
                                    'id': favorite_user.id,
                                    'username': favorite_user.username,
                                    'first_name': favorite_user.first_name,
                                    'reason': 'в РёР·Р±СЂР°наЅРѕРј',
                                    'tasks': [],
                                    'task_count': 0
                                })

                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рј СЏ РґРµР»РµРіРёСЂРѕвР°Р» Р·Р°РґР°С‡Рё
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
                                'reason': f'СЏ РґРµР»РµРіРёСЂРѕвР°Р» {task_count} Р·Р°РґР°С‡',
                                'tasks': task_titles,
                                'task_count': task_count
                            })

                # Р”РѕР±Р°вР»СЏРµРј СЂРµРєРѕРјРµнаґРѕвР°наЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹ РґР»СЏ вСЃРµС… РїРѕР»СЊР·РѕвР°С‚РµР»РµР№
                # РџРѕР»СѓС‡Р°РµРј вСЃРµ СЂРµРєРѕРјРµнаґРѕвР°наЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹
                all_partners = get_partners_list(user.id, session_db)
                
                # Р”РѕР±Р°вР»СЏРµРј РєРѕРЅС‚Р°РєС‚С‹, РєРѕС‚РѕСЂС‹Рµ РµС‰Рµ наµ в СЃРїРёСЃРєР°С… РґРµР»РµРіРёСЂРѕвР°наёСЏ
                existing_contact_ids = set()
                for contact in delegating_to_me + delegating_by_me:
                    existing_contact_ids.add(contact['id'])
                
                for partner in all_partners:
                    partner_user = session_db.query(User).filter_by(id=partner.user_id).first()
                    if partner_user and partner_user.id not in existing_contact_ids and partner_user.id != user.id:
                        # РћРїСЂРµРґРµР»СЏРµРј РїСЂРёС‡РёРЅСѓ СЂРµРєРѕРјРµнаґР°С†РёРё
                        reason_parts = []
                        if hasattr(partner, 'common_interests') and partner.common_interests:
                            reason_parts.append(f"РѕР±С‰РёРµ РёРЅС‚РµСЂРµСЃС‹: {partner.common_interests}")
                        if hasattr(partner, 'common_skills') and partner.common_skills:
                            reason_parts.append(f"РѕР±С‰РёРµ на°вС‹РєРё: {partner.common_skills}")
                        if hasattr(partner, 'common_goals') and partner.common_goals:
                            reason_parts.append(f"РѕР±С‰РёРµ С†РµР»Рё: {partner.common_goals}")
                        if hasattr(partner, 'task_relevance') and partner.task_relevance:
                            reason_parts.append(partner.task_relevance)
                        
                        reason = ', '.join(reason_parts) if reason_parts else 'СЂРµРєРѕРјРµнаґРѕвР°РЅ СЃРёСЃС‚РµРјРѕР№'
                        
                        # Р”РѕР±Р°вР»СЏРµРј в delegating_to_me РєР°Рє СЂРµРєРѕРјРµнаґРѕвР°наЅС‹Р№ РєРѕРЅС‚Р°РєС‚
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

            # РџРѕР»СѓС‡РёС‚СЊ Р·Р°Р±Р»РѕРєРёСЂРѕвР°наЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹
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
                                'reason': 'Р·Р°Р±Р»РѕРєРёСЂРѕвР°наЅС‹Р№ РєРѕРЅС‚Р°РєС‚'
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

            # РџРѕР»СѓС‡Р°РµРј СЃРїРёСЃРѕРє РєРѕРЅС‚Р°РєС‚Рѕв, СЃ РєРѕС‚РѕСЂС‹РјРё СѓР¶Рµ РѕР±С‰Р°Р»РёСЃСЊ
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - improved matching with partial string matching
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(',') if i.strip())
                    common = user_interests & partner_interests
                    # Also check for partial matches (e.g., "СЃРїРѕСЂС‚" matches "СЃРїРѕСЂС‚, С„СѓС‚Р±РѕР»")
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
                        reasons.append('СѓР¶Рµ РѕР±С‰Р°Р»РёСЃСЊ')
                if p.common_skills:
                    reasons.append('РѕР±С‰РёРµ на°вС‹РєРё')
                if p.common_interests:
                    reasons.append('РѕР±С‰РёРµ РёРЅС‚РµСЂРµСЃС‹')
                if p.common_goals:
                    reasons.append('РѕР±С‰РёРµ С†РµР»Рё')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('РёР· вР°С€РµРіРѕ РіРѕСЂРѕРґР°')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'РїРѕРґС…РѕРґСЏС‰РёР№ РєРѕРЅС‚Р°РєС‚'

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
            'СЏнаІР°СЂСЏ',
            'С„РµвСЂР°Р»СЏ',
            'РјР°СЂС‚Р°',
            'Р°РїСЂРµР»СЏ',
            'РјР°СЏ',
            'РёСЋРЅСЏ',
            'РёСЋР»СЏ',
            'Р°вРіСѓСЃС‚Р°',
            'СЃРµРЅС‚СЏР±СЂСЏ',
            'РѕРєС‚СЏР±СЂСЏ',
            'наѕСЏР±СЂСЏ',
            'РґРµРєР°Р±СЂСЏ']
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
                        task.overdue_text = f"на {days} дн."
                    elif hours > 0:
                        task.overdue_text = f"на {hours} ч."
                    elif minutes > 0:
                        task.overdue_text = f"на {minutes} мин."
                    else:
                        task.overdue_text = ""
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
                    if task.reminder_time.astimezone(
                            user_tz if user.timezone else pytz.UTC) > user_now and task.status == 'pending':
                        reminder_time_local = task.reminder_time.astimezone(
                            user_tz if user.timezone else pytz.UTC).strftime("%H:%M")
                        upcoming_reminders.append(f"{task.title} в {reminder_time_local}")

        # Преобразуем задачи в словари для JSON сериализации
        tasks_dict = []
        for task in tasks:
            # РџРѕРґРіРѕС‚РѕвРёРј reminder_time в ISO С„РѕСЂРјР°С‚Рµ РґР»СЏ JavaScript
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

        logger.info(f"Rendering dashboard for user {user.id} with subscription_tier: {display_tier}")

        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': True,
            'tasks': tasks_dict,
            'user': user,
            'profile': profile,
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

            # РљР РРўРР§РќРћ: РЎРѕС…СЂР°РЅСЏРµРј СЃРѕРѕР±С‰РµнаёРµ РїРѕР»СЊР·РѕвР°С‚РµР»СЏ Р”Рћ вС‹Р·РѕвР° AI
            # Р­С‚Рѕ РіР°СЂР°РЅС‚РёСЂСѓРµС‚, С‡С‚Рѕ СЃРѕРѕР±С‰РµнаёРµ РїРѕСЏвРёС‚СЃСЏ в РёСЃС‚РѕСЂРёРё РґР°Р¶Рµ РµСЃР»Рё AI СѓРїР°РґРµС‚
            # Р РїСЂРµРґРѕС‚вСЂР°С‰Р°РµС‚ race condition СЃ РґСѓР±Р»РёРєР°С‚Р°РјРё
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
                response = f"РћС€РёР±РєР°: {str(e)}"

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
                if response is None or response == '':
                    logger.error("[API_SEND_MESSAGE] AI response is empty!")
                    response = "РР·вРёнаёС‚Рµ, РїСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ вР°С€РµРіРѕ Р·Р°РїСЂРѕСЃР°. РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰Рµ СЂР°Р·."
            except Exception as e:
                logger.error(f"[API_SEND_MESSAGE] Error calling AI chat: {e}", exc_info=True)
                return web.json_response({'error': 'AI service error'}, status=500)

            # Check if response contains tier restriction error
            if "Р”РµР»РµРіРёСЂРѕвР°наёРµ Р·Р°РґР°С‡ РґРѕСЃС‚СѓРїнаѕ С‚РѕР»СЊРєРѕ на° С‚Р°СЂРёС„Р°С…" in response:
                logger.info(f"[API_SEND_MESSAGE] Tier restriction detected for user {user_id}")
                return web.json_response({
                    'error': 'tier_restriction',
                    'message': 'рџҐ‰ Р”РµР»РµРіРёСЂРѕвР°наёРµ Р·Р°РґР°С‡ РґРѕСЃС‚СѓРїнаѕ С‚РѕР»СЊРєРѕ на° С‚Р°СЂРёС„Р°С… РЎС‚Р°наґР°СЂС‚ Рё РџСЂРµРјРёСѓРј',
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

    # РћР±наѕвР»СЏРµРј history_cleared_at в Р‘Р”
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

        # РС‰РµРј Р·Р°РґР°С‡Сѓ Р»РёР±Рѕ СЃСЂРµРґРё СЃвРѕРёС…, Р»РёР±Рѕ СЃСЂРµРґРё РґРµР»РµРіРёСЂРѕвР°наЅС‹С… Рјнаµ
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
    """Р—Р°вРµСЂС€Р°РµС‚ Р·Р°РґР°С‡Сѓ РїРѕ ID"""
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
        
        # РџСЂРѕвРµСЂСЏРµРј СЃС‚Р°С‚СѓСЃ Р·Р°РґР°С‡Рё РїРѕСЃР»Рµ Р·Р°вРµСЂС€РµнаёСЏ
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
        
        # РћС‚РїСЂР°вР»СЏРµРј СѓвРµРґРѕРјР»РµнаёРµ в Telegram С‡РµСЂРµР· AI РѕР±СЂР°Р±РѕС‚РєСѓ, РєР°Рє Р±СѓРґС‚Рѕ РїРѕР»СЊР·РѕвР°С‚РµР»СЊ на°РїРёСЃР°Р» Рѕ вС‹РїРѕР»наµнаёРё
        try:
            if 'bot' in request.app:
                from models import Session as DBSession, User
                from ai_integration.chat import chat_with_ai
                db_session = DBSession()
                try:
                    # РќР°С…РѕРґРёРј РїРѕР»СЊР·РѕвР°С‚РµР»СЏ РїРѕ user_id (СЌС‚Рѕ telegram_id)
                    user = db_session.query(User).filter_by(telegram_id=user_id).first()
                    if user:
                        from models import Task
                        task = db_session.query(Task).filter_by(id=task_id, user_id=user.id).first()
                        if task:
                            # РћС‚РїСЂР°вР»СЏРµРј СЃРѕРѕР±С‰РµнаёРµ С‡РµСЂРµР· AI, РєР°Рє Р±СѓРґС‚Рѕ РїРѕР»СЊР·РѕвР°С‚РµР»СЊ на°РїРёСЃР°Р» Рѕ вС‹РїРѕР»наµнаёРё
                            ai_message = f"СЏ вС‹РїРѕР»наёР» Р·Р°РґР°С‡Сѓ '{task.title}'"
                            try:
                                ai_result = await chat_with_ai(ai_message, user_id=user_id)
                                ai_response = ai_result['response']
                                await request.app['bot'].send_message(chat_id=user_id, text=ai_response)
                                
                                # РЎРѕС…СЂР°РЅСЏРµРј вР·Р°РёРјРѕРґРµР№СЃС‚вРёРµ в Р±Р°Р·Сѓ РґР°наЅС‹С… РґР»СЏ РѕС‚РѕР±СЂР°Р¶РµнаёСЏ в вРµР±-РїР°наµР»Рё
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
                                # Fallback на° РїСЂРѕСЃС‚РѕРµ СѓвРµРґРѕРјР»РµнаёРµ, РµСЃР»Рё AI наµ СЃСЂР°Р±РѕС‚Р°Р»
                                logger.warning(f"AI processing failed, using fallback: {ai_error}")
                                notification_text = f"вњ… Р—Р°РґР°С‡Р° вС‹РїРѕР»наµна°: {task.title}"
                                await request.app['bot'].send_message(chat_id=user_id, text=notification_text)
                                
                                # РЎРѕС…СЂР°РЅСЏРµРј fallback вР·Р°РёРјРѕРґРµР№СЃС‚вРёРµ в Р±Р°Р·Сѓ РґР°наЅС‹С…
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
    """Р’РѕСЃСЃС‚Р°на°вР»РёвР°РµС‚ Р·Р°РґР°С‡Сѓ в СЂР°Р±РѕС‚Сѓ"""
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
    """РџСЂРѕРїСѓСЃРєР°РµС‚ Р·Р°РґР°С‡Сѓ"""
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
    """РЈРґР°Р»СЏРµС‚ Р·Р°РґР°С‡Сѓ"""
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
        # РџРµСЂРµРґР°С‘Рј confirmed=True, РїРѕСЃРєРѕР»СЊРєСѓ РїРѕР»СЊР·РѕвР°С‚РµР»СЊ СѓР¶Рµ РїРѕРґС‚вРµСЂРґРёР» СѓРґР°Р»РµнаёРµ в UI
        result = await delete_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} deleted by user {user_id}: {result}")
        
        # Р•СЃР»Рё СЂРµР·СѓР»СЊС‚Р°С‚ СЃРѕРґРµСЂР¶РёС‚ С„Р»Р°Рі, РѕР±СЂР°Р±РѕС‚Р°РµРј С‡РµСЂРµР· AI Рё РѕС‚РїСЂР°вРёРј в Telegram
        if result.startswith('TASK_COMPLETED_ASK_RESULT:') or result.startswith('TASK_UPDATED:') or result.startswith('TASK_DELETED_ASK_REASON:'):
            try:
                from ai_integration.chat import chat_with_ai
                from models import Session as DBSession, User
                db_session = DBSession()
                try:
                    # РћР±СЂР°Р±РѕС‚РєР° С‡РµСЂРµР· AI РґР»СЏ РіРµнаµСЂР°С†РёРё РµСЃС‚РµСЃС‚вРµнаЅРѕРіРѕ РѕС‚вРµС‚Р°
                    ai_result = await chat_with_ai(result, user_id=user_id, db_session=db_session)
                    ai_response = ai_result['response']
                    
                    # РћС‚РїСЂР°вР»СЏРµРј AI РѕС‚вРµС‚ в Telegram РµСЃР»Рё Р±РѕС‚ РґРѕСЃС‚СѓРїРµРЅ
                    if 'bot' in request.app and ai_response:
                        await request.app['bot'].send_message(chat_id=user_id, text=ai_response)
                        
                        # РЎРѕС…СЂР°РЅСЏРµРј вР·Р°РёРјРѕРґРµР№СЃС‚вРёРµ в Р±Р°Р·Сѓ РґР°наЅС‹С… РґР»СЏ РѕС‚РѕР±СЂР°Р¶РµнаёСЏ в вРµР±-РїР°наµР»Рё
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
    """РћС‚РјРµРЅСЏРµС‚ РґРµР»РµРіРёСЂРѕвР°наёРµ Р·Р°РґР°С‡Рё"""
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
    """РџРµСЂРµнаѕСЃРёС‚ Р·Р°РґР°С‡Сѓ на° наѕвРѕРµ вСЂРµРјСЏ"""
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
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://telegram.org https://fonts.googleapis.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: https:; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.deepseek.com; frame-src https://oauth.telegram.org;"
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# app.middlewares.append(security_middleware)
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

            # Р•СЃР»Рё РїРѕРґРїРёСЃРєР° РµС‰Рµ Р°РєС‚Рёвна°, РїСЂРѕРґР»РµвР°РµРј РѕС‚ end_date, Рёна°С‡Рµ РѕС‚ С‚РµРєСѓС‰РµР№ РґР°С‚С‹
            now = datetime.now(pytz.UTC)
            if subscription.end_date and subscription.end_date > now:
                subscription.end_date = subscription.end_date + timedelta(days=30)
            else:
                subscription.end_date = now + timedelta(days=30)

            session.commit()

            # Р›РѕРіРёСЂСѓРµРј РїР»Р°С‚РµР¶ в payment_history РґР»СЏ Р·Р°С‰РёС‚С‹ РѕС‚ РїРѕС‚РµСЂРё РґР°наЅС‹С…
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
                logger.info(f"рџ’ѕ Payment logged to history: user={user.username}, tier={tier}, payment_id={payment['id']}, promo_code={promo_code}")
            except Exception as e:
                logger.error(f"вќЊ Failed to log payment to history: {e}")
                # РќРµ РїР°РґР°РµРј, РїР»Р°С‚РµР¶ СѓР¶Рµ РѕР±СЂР°Р±РѕС‚Р°РЅ

            from payments import get_tier_name, TIER_PRICES
            tier_name = get_tier_name(tier)
            promo_msg = f" СЃ РїСЂРѕРјРѕРєРѕРґРѕРј {promo_code}" if promo_code else ""
            await bot.send_message(int(user_id), f"РџРѕРґРїРёСЃРєР° {tier_name} Р°РєС‚РёвРёСЂРѕвР°на°{promo_msg}! РўРµРїРµСЂСЊ Сѓ вР°СЃ РґРѕСЃС‚СѓРї РєРѕ вСЃРµРј РїСЂРµРјРёСѓРј-С„СѓнаєС†РёСЏРј.")

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
                                f"рџ’° Р’Р°С€ СЂРµС„РµСЂР°Р» РѕРїР»Р°С‚РёР» РїРѕРґРїРёСЃРєСѓ! Р’С‹ РїРѕР»СѓС‡РёР»Рё {commission_amount} СЂСѓР±Р»РµР№ РєРѕРјРёСЃСЃРёРё. РўРµРєСѓС‰РёР№ Р±Р°Р»Р°РЅСЃ: {referrer.referral_balance} СЂСѓР±Р»РµР№."
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
        """РЎРєР»РѕнаµнаёРµ СЃР»РѕвР° 'Р·Р°РґР°С‡Р°' РїРѕ С‡РёСЃР»Сѓ"""
        last_digit = count % 10
        last_two_digits = count % 100

        if 11 <= last_two_digits <= 19:
            return 'Р·Р°РґР°С‡'
        if last_digit == 1:
            return 'Р·Р°РґР°С‡Сѓ'
        if 2 <= last_digit <= 4:
            return 'Р·Р°РґР°С‡Рё'
        return 'Р·Р°РґР°С‡'

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
                partners = get_partners_list(user_id=user.id)  # РџРµСЂРµРґР°РµРј user.id (Р±Р°Р·РѕвС‹Р№ ID), Р° наµ telegram_id
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

            # РџРѕР»СѓС‡РёС‚СЊ РєРѕРЅС‚Р°РєС‚С‹ РїРѕ РґРµР»РµРіРёСЂРѕвР°наёСЋ
            delegating_to_me = []  # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рµ РґРµР»РµРіРёСЂРѕвР°Р»Рё Рјнаµ Р·Р°РґР°С‡Рё
            delegating_by_me = []  # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рј СЏ РґРµР»РµРіРёСЂРѕвР°Р» Р·Р°РґР°С‡Рё

            try:
                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рµ РґРµР»РµРіРёСЂРѕвР°Р»Рё Рјнаµ Р·Р°РґР°С‡Рё (СЏ РїРѕР»СѓС‡Р°СЋ Р·Р°РґР°С‡Рё РѕС‚ наёС…)
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
                                'reason': f'РґРµР»РµРіРёСЂРѕвР°Р» {len(task_titles)} {pluralize_task(len(task_titles))}'
                            })

                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рј СЏ РґРµР»РµРіРёСЂРѕвР°Р» Р·Р°РґР°С‡Рё
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
                                'reason': f'СЏ РґРµР»РµРіРёСЂРѕвР°Р» {len(task_titles)} {pluralize_task(len(task_titles))}'
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

            # РџРѕР»СѓС‡Р°РµРј СЃРїРёСЃРѕРє РєРѕРЅС‚Р°РєС‚Рѕв, СЃ РєРѕС‚РѕСЂС‹РјРё СѓР¶Рµ РѕР±С‰Р°Р»РёСЃСЊ
            contacted_usernames = set()
            for interaction in interactions:
                mentions = re.findall(r'@(\w+)', interaction.content)
                contacted_usernames.update(mentions)

            for p in partners:
                # Common interests - improved matching with partial string matching
                if p.interests:
                    partner_interests = set(i.strip().lower() for i in p.interests.split(',') if i.strip())
                    common = user_interests & partner_interests
                    # Also check for partial matches (e.g., "СЃРїРѕСЂС‚" matches "СЃРїРѕСЂС‚, С„СѓС‚Р±РѕР»")
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
                        reasons.append('СѓР¶Рµ РѕР±С‰Р°Р»РёСЃСЊ')
                if p.common_skills:
                    reasons.append('РѕР±С‰РёРµ на°вС‹РєРё')
                if p.common_interests:
                    reasons.append('РѕР±С‰РёРµ РёРЅС‚РµСЂРµСЃС‹')
                if p.common_goals:
                    reasons.append('РѕР±С‰РёРµ С†РµР»Рё')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('РёР· вР°С€РµРіРѕ РіРѕСЂРѕРґР°')
                p.recommendation_reason = ', '.join(reasons) if reasons else 'РїРѕРґС…РѕРґСЏС‰РёР№ РєРѕРЅС‚Р°РєС‚'

        # Calculate common_tasks for regular partners - СѓР»СѓС‡С€РµнаЅР°СЏ Р»РѕРіРёРєР° СЃ С‡Р°СЃС‚РёС‡РЅС‹Рј СЃРѕвРїР°РґРµнаёРµРј
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

                        # РўРѕС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ Р·Р°РґР°С‡
                        common_task_titles = user_task_titles & partner_task_titles
                        
                        # Р§Р°СЃС‚РёС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ - РµСЃР»Рё С…РѕС‚СЏ Р±С‹ 2 СЃР»РѕвР° СЃРѕвРїР°РґР°СЋС‚
                        if not common_task_titles:
                            partial_matches = set()
                            for user_task in user_task_titles:
                                user_words = set(user_task.split())
                                if len(user_words) < 2:  # РџСЂРѕРїСѓСЃРєР°РµРј СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРёРµ
                                    continue
                                for partner_task in partner_task_titles:
                                    partner_words = set(partner_task.split())
                                    # Р•СЃР»Рё СЃРѕвРїР°РґР°РµС‚ >= 2 СЃР»Рѕв
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
                # РџРѕР»СѓС‡Р°РµРј telegram_id РїРѕР»СЊР·РѕвР°С‚РµР»СЏ РёР· Р±Р°Р·С‹
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
                # LIGHT вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
                # STANDARD вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
                # PREMIUM вРёРґРёС‚ вСЃРµ РєРѕРЅС‚Р°РєС‚С‹ (LIGHT, STANDARD, PREMIUM)
                can_access = False

                if user_tier_str.lower() == 'light':
                    # LIGHT вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
                    can_access = (partner_tier_str.lower() in ['light', 'standard'])
                    logger.info(f"User {user_tier_str} checking partner {partner_tier_str}: can_access = {can_access}")
                elif user_tier_str.lower() == 'standard':
                    # STANDARD вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
                    can_access = (partner_tier_str.lower() in ['light', 'standard'])
                    logger.info(f"User {user_tier_str} checking partner {partner_tier_str}: can_access = {can_access}")
                elif user_tier_str.lower() == 'premium':
                    # PREMIUM вРёРґРёС‚ вСЃРµС…
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
                                'РїРѕРґС…РѕРґСЏС‰РёР№ РєРѕРЅС‚Р°РєС‚'),
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
                
            # РџРѕР»СѓС‡РёС‚СЊ РїСЂРѕС„РёР»СЊ РґРµР»РµРіР°С‚РѕСЂР° РґР»СЏ СЂР°СЃС‡РµС‚Р° РѕР±С‰РёС… РёРЅС‚РµСЂРµСЃРѕв/на°вС‹РєРѕв/С†РµР»РµР№
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

                    # РўРѕС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ Р·Р°РґР°С‡
                    common_task_titles = user_task_titles & delegator_task_titles
                    
                    # Р§Р°СЃС‚РёС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ - РµСЃР»Рё С…РѕС‚СЏ Р±С‹ 2 СЃР»РѕвР° СЃРѕвРїР°РґР°СЋС‚
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
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id)
                    if updated_avatar and updated_avatar != delegator.photo_url:
                        delegator.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")

            # Для контактов "Делегирует мне" НЕ применяем фильтр по тарифу
            # Делегированные задачи должны всегда отображаться независимо от тарифа делегатора
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
                'can_access': True,  # Р’СЃРµРіРґР° РґРѕСЃС‚СѓРїРµРЅ
                'required_tier': None,  # РќРµС‚ РѕРіСЂР°наёС‡РµнаёР№
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
            
            # РџРѕР»СѓС‡РёС‚СЊ РїСЂРѕС„РёР»СЊ РґРµР»РµРіР°С‚Р° РґР»СЏ СЂР°СЃС‡РµС‚Р° РѕР±С‰РёС… РёРЅС‚РµСЂРµСЃРѕв/на°вС‹РєРѕв/С†РµР»РµР№
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

                    # РўРѕС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ Р·Р°РґР°С‡
                    common_task_titles = user_task_titles & delegatee_task_titles
                    
                    # Р§Р°СЃС‚РёС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ - РµСЃР»Рё С…РѕС‚СЏ Р±С‹ 2 СЃР»РѕвР° СЃРѕвРїР°РґР°СЋС‚
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
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id)
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
                # LIGHT вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
                can_access = (delegatee_tier_str.lower() in ['light', 'standard'])
                logger.info(f"Delegatee check: User {user_tier_str} checking delegatee {delegatee_tier_str}: can_access = {can_access}")
            elif user_tier_str.lower() == 'standard':
                # STANDARD вРёРґРёС‚ LIGHT Рё STANDARD РєРѕРЅС‚Р°РєС‚С‹
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

        # РЎРѕСЂС‚РёСЂСѓРµРј partners_data: СЃна°С‡Р°Р»Р° РїРѕ РіРѕСЂРѕРґСѓ (СЃРѕвРїР°РґРµнаёРµ СЃ РїРѕР»СЊР·РѕвР°С‚РµР»РµРј), РїРѕС‚РѕРј РїРѕ СЂРµР№С‚РёнаіСѓ

        # РЎРѕСЂС‚РёСЂСѓРµРј partners_data: СЃна°С‡Р°Р»Р° РїРѕ РіРѕСЂРѕРґСѓ (СЃРѕвРїР°РґРµнаёРµ СЃ РїРѕР»СЊР·РѕвР°С‚РµР»РµРј), РїРѕС‚РѕРј РїРѕ СЂРµР№С‚РёнаіСѓ
        user_city = profile.city.lower() if profile and profile.city else None

        normalized_user_city = normalize_city(user_city)

        def sort_key(partner):
            partner_city = normalize_city(partner.get('city', ''))
            same_city = 0 if (normalized_user_city and partner_city == normalized_user_city) else 1

            rating = partner.get('average_rating', 0) or 0
            # Р“СЂСѓРїРїС‹ СЂРµР№С‚РёнаіР°:
            # 1. Р’С‹СЃРѕРєРёР№ СЂРµР№С‚Рёнаі (>= 5): СЃРѕСЂС‚РёСЂСѓРµРј РїРѕ СѓР±С‹вР°наёСЋ
            # 2. РќРµС‚ СЂРµР№С‚РёнаіР° (0): наµР№С‚СЂР°Р»СЊнаѕ, вС‹С€Рµ РїР»РѕС…РёС…
            # 3. РќРёР·РєРёР№ СЂРµР№С‚Рёнаі (< 5): СЃРѕСЂС‚РёСЂСѓРµРј РїРѕ СѓР±С‹вР°наёСЋ
            if rating >= 5:
                rating_group = 0  # Р›СѓС‡С€Р°СЏ РіСЂСѓРїРїР°
                rating_value = -rating  # Р’РЅСѓС‚СЂРё РіСЂСѓРїРїС‹ РїРѕ СѓР±С‹вР°наёСЋ
            elif rating == 0:
                rating_group = 1  # РЎСЂРµРґРЅСЏСЏ РіСЂСѓРїРїР° (наµС‚ РґР°наЅС‹С…)
                rating_value = 0
            else:  # rating < 5
                rating_group = 2  # РҐСѓРґС€Р°СЏ РіСЂСѓРїРїР°
                rating_value = -rating  # Р’РЅСѓС‚СЂРё РіСЂСѓРїРїС‹ РїРѕ СѓР±С‹вР°наёСЋ

            return (same_city, rating_group, rating_value)

        # Add favorite contacts
        if profile and profile.favorite_contacts:
            try:
                favorite_data = json.loads(profile.favorite_contacts)
                for item in favorite_data:
                    favorite_username = None
                    # РћРїСЂРµРґРµР»РёС‚СЊ username РїРѕ ID РёР»Рё РёСЃРїРѕР»СЊР·РѕвР°С‚СЊ на°РїСЂСЏРјСѓСЋ
                    if isinstance(item, int):
                        # Р­С‚Рѕ user_id
                        fav_user = session_db.query(User).filter_by(id=item).first()
                        if fav_user:
                            favorite_username = fav_user.username
                    elif isinstance(item, str):
                        # Р­С‚Рѕ username
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

                            # РР·Р±СЂР°наЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹ вСЃРµРіРґР° РґРѕСЃС‚СѓРїРЅС‹ наµР·Р°вРёСЃРёРјРѕ РѕС‚ С‚Р°СЂРёС„Р°
                            can_access = True
                            required_tier = None

                            # Update avatar from Telegram if available
                            photo_url = favorite_user.photo_url if favorite_user.photo_url else None
                            if favorite_user.telegram_id and 'bot' in request.app:
                                try:
                                    updated_avatar = await get_user_avatar_url(request.app['bot'], favorite_user.telegram_id)
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
                                'reason': 'РёР·Р±СЂР°наЅС‹Р№ РєРѕРЅС‚Р°РєС‚',
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
        blocked_partners_data = []  # РЎРѕС…СЂР°РЅСЏРµРј РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ Р·Р°Р±Р»РѕРєРёСЂРѕвР°наЅС‹С…
        for partner in partners_data:
            partner_username = (partner.get('contact_info') or '').replace('@', '')
            if partner_username in blocked_by_me or partner_username in blocked_me:
                blocked_partners_data.append(partner)  # РЎРѕС…СЂР°РЅСЏРµРј Р·Р°Р±Р»РѕРєРёСЂРѕвР°наЅС‹Рµ
                continue  # Skip blocked contacts from main list
            filtered_partners_data.append(partner)

        partners_data = filtered_partners_data
        partners_data.sort(key=sort_key)

        # Р”РѕР±Р°вРёС‚СЊ С„Р»Р°Рі is_favorite РґР»СЏ вСЃРµС… РєРѕРЅС‚Р°РєС‚Рѕв
        favorite_usernames = set()
        if profile and profile.favorite_contacts:
            try:
                favorite_data = json.loads(profile.favorite_contacts)
                for item in favorite_data:
                    if isinstance(item, int):
                        # Р­С‚Рѕ user_id
                        fav_user = session_db.query(User).filter_by(id=item).first()
                        if fav_user and fav_user.username:
                            favorite_usernames.add(fav_user.username.replace('@', '').lower())
                    elif isinstance(item, str):
                        # Р­С‚Рѕ username
                        favorite_usernames.add(item.replace('@', '').lower())
            except json.JSONDecodeError:
                pass

        # РЈСЃС‚Р°наѕвРёС‚СЊ С„Р»Р°Рі is_favorite РґР»СЏ вСЃРµС… РєРѕРЅС‚Р°РєС‚Рѕв
        for partner in partners_data:
            contact_info = partner.get('contact_info')
            if contact_info is None:
                contact_info = ''
            contact_username = contact_info.replace('@', '').lower()
            partner['is_favorite'] = contact_username in favorite_usernames

        logger.info(f"Returning {len(partners_data)} partners for user {user_id}")
        return web.json_response({
            'partners': partners_data,
            'blocked_partners_info': blocked_partners_data  # Р”РѕР±Р°вР»СЏРµРј РёРЅС„РѕСЂРјР°С†РёСЋ Рѕ Р·Р°Р±Р»РѕРєРёСЂРѕвР°наЅС‹С…
        })
    except Exception as e:
        logger.error(f"Unexpected error in api_partners_handler: {e}", exc_info=True)
        return web.json_response({'partners': []}, status=200)
    finally:
        # РќР° СЃР»СѓС‡Р°Р№ СЂР°наЅРёС… РѕС€РёР±РѕРє Р·Р°РєСЂС‹вР°РµРј СЃРµСЃСЃРёСЋ, РµСЃР»Рё Рѕна° РµС‰Рµ РѕС‚РєСЂС‹С‚Р°
        try:
            if 'session_db' in locals():
                session_db.close()
        except Exception:
            pass


async def api_elite_partners_handler(request):
    """Get ALL Premium partners for Premium users (Premium status filter)"""
    def pluralize_task(count):
        """РЎРєР»РѕнаµнаёРµ СЃР»РѕвР° 'Р·Р°РґР°С‡Р°' РїРѕ С‡РёСЃР»Сѓ"""
        last_digit = count % 10
        last_two_digits = count % 100

        if 11 <= last_two_digits <= 19:
            return 'Р·Р°РґР°С‡'
        if last_digit == 1:
            return 'Р·Р°РґР°С‡Сѓ'
        if 2 <= last_digit <= 4:
            return 'Р·Р°РґР°С‡Рё'
        return 'Р·Р°РґР°С‡'

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
                        updated_avatar = await get_user_avatar_url(request.app['bot'], premium_user.telegram_id)
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
                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рµ РґРµР»РµРіРёСЂРѕвР°Р»Рё Р·Р°РґР°С‡Рё Рјнаµ (accepted delegation)
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
                                        updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id)
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
                                    'reason': f'РґРµР»РµРіРёСЂРѕвР°Р» {len(task_titles)} {pluralize_task(len(task_titles))}',
                                    'type': 'delegation'
                                })

                # Р›СЋРґРё, РєРѕС‚РѕСЂС‹Рј СЏ РґРµР»РµРіРёСЂРѕвР°Р» Р·Р°РґР°С‡Рё
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
                                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id)
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
                                'reason': f'СЏ РґРµР»РµРіРёСЂРѕвР°Р» {len(task_titles)} {pluralize_task(len(task_titles))}',
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
                # Р“СЂСѓРїРїС‹ СЂРµР№С‚РёнаіР°:
                # 1. Р’С‹СЃРѕРєРёР№ СЂРµР№С‚Рёнаі (>= 5): СЃРѕСЂС‚РёСЂСѓРµРј РїРѕ СѓР±С‹вР°наёСЋ
                # 2. РќРµС‚ СЂРµР№С‚РёнаіР° (0): наµР№С‚СЂР°Р»СЊнаѕ, вС‹С€Рµ РїР»РѕС…РёС…
                # 3. РќРёР·РєРёР№ СЂРµР№С‚Рёнаі (< 5): СЃРѕСЂС‚РёСЂСѓРµРј РїРѕ СѓР±С‹вР°наёСЋ
                if rating >= 5:
                    rating_group = 0  # Р›СѓС‡С€Р°СЏ РіСЂСѓРїРїР°
                    rating_value = -rating  # Р’РЅСѓС‚СЂРё РіСЂСѓРїРїС‹ РїРѕ СѓР±С‹вР°наёСЋ
                elif rating == 0:
                    rating_group = 1  # РЎСЂРµРґРЅСЏСЏ РіСЂСѓРїРїР° (наµС‚ РґР°наЅС‹С…)
                    rating_value = 0
                else:  # rating < 5
                    rating_group = 2  # РҐСѓРґС€Р°СЏ РіСЂСѓРїРїР°
                    rating_value = -rating  # Р’РЅСѓС‚СЂРё РіСЂСѓРїРїС‹ РїРѕ СѓР±С‹вР°наёСЋ

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
                    updated_avatar = await get_user_avatar_url(request.app['bot'], contact_user.telegram_id)
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
                                        message = f"@{user.username} наµ РіРѕС‚Рѕв РїСЂРёнаёРјР°С‚СЊ Р·Р°РґР°С‡Рё РѕС‚ вР°СЃ. Р’Р°С€Рё РґРµР»РµРіРёСЂРѕвР°наЅС‹Рµ Р·Р°РґР°С‡Рё Р±С‹Р»Рё РѕС‚РєР»РѕнаµРЅС‹."
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
            success_message = f'РћС†РµнаєР° {rating}/10 РґР»СЏ @{rated_username} СЃРѕС…СЂР°наµна°'

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

            # РЎРѕС…СЂР°наёС‚СЊ СЃРѕРѕР±С‰РµнаёРµ в РёСЃС‚РѕСЂРёСЋ вР·Р°РёРјРѕРґРµР№СЃС‚вРёР№
            success_message = f'@{username} СЃРєСЂС‹С‚ на° {days} РґнаµР№'
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
                content=f'Р—Р°РґР°С‡Р° "{task.title}" РїСЂРёРЅСЏС‚Р° Рё вР·СЏС‚Р° в СЂР°Р±РѕС‚Сѓ'
            )
            session_db.add(interaction)

            session_db.commit()

            return web.json_response({
                'success': True,
                'message': f'Р—Р°РґР°С‡Р° "{task.title}" РїСЂРёРЅСЏС‚Р°'
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
                content=f'Р—Р°РґР°С‡Р° "{task.title}" РѕС‚РєР»Рѕнаµна°'
            )
            session_db.add(interaction)

            session_db.commit()

            return web.json_response({
                'success': True,
                'message': f'Р—Р°РґР°С‡Р° "{task.title}" РѕС‚РєР»Рѕнаµна°'
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
                'message': 'РџСЂРѕС„РёР»СЊ РѕР±наѕвР»РµРЅ'
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
                    # favorite_contacts РјРѕР¶РµС‚ СЃРѕРґРµСЂР¶Р°С‚СЊ РєР°Рє ID, С‚Р°Рє Рё usernames
                    for item in favorite_data:
                        if isinstance(item, int):
                            # Р­С‚Рѕ user_id
                            favorite_user_ids.append(item)
                            logger.info(f"Feed: Added favorite user_id: {item}")
                        elif isinstance(item, str):
                            # Р­С‚Рѕ username - на°Р№С‚Рё user_id
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
            blocked_by_users = set()
            all_profiles = session_db.query(UserProfile).filter(
                UserProfile.blocked_contacts.isnot(None)
            ).all()
            
            import json
            for profile in all_profiles:
                try:
                    blocked_list = json.loads(profile.blocked_contacts)
                    if user.id in blocked_list:
                        blocked_by_users.add(profile.user_id)
                except:
                    pass

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
                        updated_avatar = await get_user_avatar_url(request.app['bot'], u.telegram_id)
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

            # РџСЂРѕвРµСЂРёС‚СЊ, РµСЃС‚СЊ Р»Рё наµРїСЂРѕС‡РёС‚Р°наЅС‹Рµ РїРѕСЃС‚С‹
            has_unread_posts = False
            if posts:
                # РџРѕР»СѓС‡РёС‚СЊ ID вСЃРµС… РїРѕСЃС‚Рѕв
                post_ids = [p.id for p in posts]
                # РџСЂРѕвРµСЂРёС‚СЊ, СЃРєРѕР»СЊРєРѕ РёР· наёС… РїРѕР»СЊР·РѕвР°С‚РµР»СЊ СѓР¶Рµ вРёРґРµР»
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

            # РћС‚РјРµС‚РёС‚СЊ РїРѕСЃС‚С‹ РєР°Рє РїСЂРѕСЃРјРѕС‚СЂРµнаЅС‹Рµ (РёСЃРїРѕР»СЊР·СѓРµРј on_conflict_do_nothing РґР»СЏ РёР·Р±РµР¶Р°наёСЏ РґСѓР±Р»РёРєР°С‚Рѕв)
            for post_id in post_ids:
                try:
                    # РџСЂРѕвРµСЂСЏРµРј, СЃСѓС‰РµСЃС‚вСѓРµС‚ Р»Рё РїРѕСЃС‚
                    post = session_db.query(Post).filter_by(id=post_id).first()
                    if post:
                        # РЎРѕР·РґР°РµРј Р·Р°РїРёСЃСЊ Рѕ РїСЂРѕСЃРјРѕС‚СЂРµ (РµСЃР»Рё наµ СЃСѓС‰РµСЃС‚вСѓРµС‚)
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
                        updated_avatar = await get_user_avatar_url(request.app['bot'], u.telegram_id)
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
                upcoming_reminders.append(f"{task.title} в {reminder_time_local}")

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
    
    # РЎРёРЅС…СЂРѕнаёР·РёСЂСѓРµРј users.subscription_tier СЃ subscriptions.tier РїСЂРё СЃС‚Р°СЂС‚Рµ
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
            
            # РџСЂРѕвРµСЂСЏРµРј, наµ РёСЃС‚РµРєР»Р° Р»Рё РїРѕРґРїРёСЃРєР°
            now = datetime.now(pytz.UTC)
            if sub.end_date and sub.end_date.tzinfo is None:
                sub.end_date = sub.end_date.replace(tzinfo=pytz.UTC)
            
            if sub.end_date and sub.end_date < now:
                continue
            
            # РЎРёРЅС…СЂРѕнаёР·РёСЂСѓРµРј С‚Р°СЂРёС„С‹
            user_tier_str = str(user.subscription_tier).split('.')[-1] if user.subscription_tier else None
            sub_tier_str = str(sub.tier).split('.')[-1] if sub.tier else None
            
            if user_tier_str != sub_tier_str:
                logger.info(f"Syncing tier for @{user.username}: users.{user_tier_str} -> subscriptions.{sub_tier_str}")
                user.subscription_tier = sub.tier
                synced_count += 1
        
        if synced_count > 0:
            session_db.commit()
            logger.info(f"вњ… Synced {synced_count} user tiers with subscriptions on startup")
        
        # РЎРёРЅС…СЂРѕнаёР·РёСЂСѓРµРј users.average_rating СЃ user_profiles.average_rating
        all_profiles = session_db.query(UserProfile).all()
        rating_synced_count = 0
        
        for profile in all_profiles:
            user = session_db.query(User).filter_by(id=profile.user_id).first()
            if not user:
                continue
            
            # РЎРёРЅС…СЂРѕнаёР·РёСЂСѓРµРј СЂРµР№С‚Рёнаі
            if user.average_rating != profile.average_rating or user.rating_count != profile.rating_count:
                logger.info(f"Syncing rating for @{user.username}: users.{user.average_rating} -> profile.{profile.average_rating}")
                user.average_rating = profile.average_rating
                user.rating_count = profile.rating_count
                rating_synced_count += 1
        
        if rating_synced_count > 0:
            session_db.commit()
            logger.info(f"вњ… Synced {rating_synced_count} user ratings with profiles on startup")
        
        session_db.close()
    except Exception as e:
        logger.error(f"вќЊ Error syncing subscription tiers on startup: {e}")

    # Set webhook for production mode
    if bot and not LOCAL:
        webhook_url = os.getenv('WEBHOOK_URL', 'https://asibiont.ru/webhook')
        try:
            await bot.set_webhook(webhook_url)
            logger.info(f"вњ… Webhook set to: {webhook_url}")
        except Exception as e:
            logger.error(f"вќЊ Failed to set webhook: {e}")


async def on_shutdown(app):
    """Cleanup on application shutdown"""
    logger.info("Application shutting down...")
    if bot and not LOCAL:
        try:
            await bot.delete_webhook()
            logger.info("вњ… Webhook deleted on shutdown")
        except Exception as e:
            logger.error(f"вќЊ Failed to delete webhook: {e}")


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
                title = re.sub(r' - [Р”Рґ]РµР»РµРіРёСЂРѕвР°на° (РѕС‚|на°) @\w+$', '', title)

                # Check if task is delegated TO me or BY me
                if user.username and (task.delegated_to_username.lower() == user.username.lower(
                ) or task.delegated_to_username.lower() == f"@{user.username.lower()}"):
                    # Task delegated TO me
                    creator = session_db.query(User).filter_by(id=task.delegated_by).first()
                    if creator:
                        title = f"{title} - Р”РµР»РµРіРёСЂРѕвР°на° РѕС‚ @{creator.username}"
                elif task.user_id == user.id:
                    # Task delegated BY me to someone else
                    title = f"{title} - Р”РµР»РµРіРёСЂРѕвР°на° на° @{delegated_username}"

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
                'delegated_to_username': task.delegated_to_username,  # Р”СѓР±Р»РёСЂСѓРµРј РґР»СЏ СѓРґРѕР±СЃС‚вР°
                'delegated_by': None,  # Р‘СѓРґРµС‚ СѓСЃС‚Р°наѕвР»Рµнаѕ наёР¶Рµ
                'delegated_by_username': None,  # Username С‚РѕРіРѕ РєС‚Рѕ РїРѕСЂСѓС‡РёР»
                'delegated_by_me': task.delegated_by == user.id  # True РµСЃР»Рё СЏ РґРµР»РµРіРёСЂРѕвР°Р» СЌС‚Сѓ Р·Р°РґР°С‡Сѓ
            }
            
            # РћРїСЂРµРґРµР»СЏРµРј delegated_by Рё delegated_by_username
            if task.delegated_by and task.delegated_by != user.id:
                # Р—Р°РґР°С‡Р° Р±С‹Р»Р° РґРµР»РµРіРёСЂРѕвР°на° Рјнаµ РєРµРј-С‚Рѕ
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
                # РџСЂРѕСЃСЂРѕС‡РєР° РґР»СЏ наµР·Р°вРµСЂС€РµнаЅС‹С… Р·Р°РґР°С‡ (pending РёР»Рё in_progress)
                task_data['overdue'] = local_reminder < user_now and task.status in ['pending', 'in_progress']
                if task_data['overdue']:
                    delta = user_now - local_reminder
                    total_seconds = int(delta.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    if days > 0:
                        task_data['overdue_text'] = f'на {days} дн.'
                    elif hours > 0:
                        task_data['overdue_text'] = f'на {hours} ч.'
                    else:
                        task_data['overdue_text'] = f'на {minutes} мин.'
            tasks_data.append(task_data)

        return web.json_response({'tasks': tasks_data})
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def api_delegations_handler(request):
    """API РґР»СЏ РїРѕР»СѓС‡РµнаёСЏ РґРµР»РµРіРёСЂРѕвР°наЅС‹С… Р·Р°РґР°С‡"""
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
    """API РґР»СЏ РїРѕР»СѓС‡РµнаёСЏ РёСЃС‚РѕСЂРёРё С‡Р°С‚Р°"""
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

        interactions = session_db.query(Interaction).filter_by(
            user_id=user.id).order_by(
            Interaction.created_at.asc()).all()
        
        logger.info(f"Found {len(interactions)} total interactions for user {user.id}")

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
    """API РґР»СЏ РїРѕРёСЃРєР° РєРѕРЅС‚Р°РєС‚Рѕв РїРѕ username"""
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
            # РџРѕРёСЃРє РїРѕР»СЊР·РѕвР°С‚РµР»РµР№ РїРѕ username (С‡Р°СЃС‚РёС‡наѕРµ СЃРѕвРїР°РґРµнаёРµ)
            users = session_db.query(User).filter(
                User.username.ilike(f'%{query}%')
            ).limit(20).all()

            contacts_data = []
            for user in users:
                # РџСЂРѕРїСѓСЃРєР°РµРј С‚РµРєСѓС‰РµРіРѕ РїРѕР»СЊР·РѕвР°С‚РµР»СЏ
                if user.telegram_id == user_id:
                    continue

                profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()

                # РћР±наѕвР»СЏРµРј Р°вР°С‚Р°СЂ РµСЃР»Рё РЅСѓР¶наѕ
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
    """РћР±наѕвР»СЏРµС‚ timezone РїРѕР»СЊР·РѕвР°С‚РµР»СЏ С‡РµСЂРµР· вРµР±-РїР°наµР»СЊ"""
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
        if not user_id:
            return web.json_response({'status': 'error', 'message': 'Not authenticated'}, status=401)

        data = await request.json()
        timezone = data.get('timezone')

        if not timezone:
            return web.json_response({'status': 'error', 'message': 'Timezone required'}, status=400)

        # РџСЂРѕвРµСЂРєР° вР°Р»РёРґнаѕСЃС‚Рё timezone
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
    """API РґР»СЏ РїРѕР»СѓС‡РµнаёСЏ Рё РѕР±наѕвР»РµнаёСЏ РїСЂРѕС„РёР»СЏ РїРѕР»СЊР·РѕвР°С‚РµР»СЏ"""
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

                # Update profile fields (РїСѓСЃС‚С‹Рµ СЃС‚СЂРѕРєРё СѓРґР°Р»СЏСЋС‚ РґР°наЅС‹Рµ)
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

                session_db.commit()
                logger.info(f"[API PROFILE POST] Profile updated for user {user_id}")
                
                return web.json_response({'success': True, 'message': 'Profile updated'})
            finally:
                session_db.close()
        except Exception as e:
            logger.error(f"Error updating profile: {e}", exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    # Get fresh data from database (СѓР±СЂР°Р»Рё РєРµС€РёСЂРѕвР°наёРµ РґР»СЏ РјРінаѕвРµнаЅРѕРіРѕ РѕР±наѕвР»РµнаёСЏ)
    session_db = Session()
    try:
        user = session_db.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)

        profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()

        profile_data = {
            'username': user.username,
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
            'СЏнаІР°СЂСЏ', 'С„РµвСЂР°Р»СЏ', 'РјР°СЂС‚Р°', 'Р°РїСЂРµР»СЏ', 'РјР°СЏ', 'РёСЋРЅСЏ',
            'РёСЋР»СЏ', 'Р°вРіСѓСЃС‚Р°', 'СЃРµРЅС‚СЏР±СЂСЏ', 'РѕРєС‚СЏР±СЂСЏ', 'наѕСЏР±СЂСЏ', 'РґРµРєР°Р±СЂСЏ'
        ]
        current_time = user_now.strftime('%H:%M')
        current_date = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

        # Format subscription end date
        formatted_end_date = None
        if subscription and subscription.end_date:
            end_dt = subscription.end_date
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=pytz.UTC)
            end_local = end_dt.astimezone(user_tz if user.timezone else pytz.UTC)
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
            'referral_balance': user.referral_balance
        }

        return web.json_response(response_data)
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def extend_subscription_handler(request):
    """РџРµСЂРµна°РїСЂР°вР»РµнаёРµ на° СЃС‚СЂР°наёС†Сѓ вС‹Р±РѕСЂР° С‚Р°СЂРёС„Р°"""
    return web.HTTPFound('/subscription_tiers')


@aiohttp_jinja2.template('subscription_tiers.html')
async def subscription_tiers_handler(request):
    """РЎС‚СЂР°наёС†Р° вС‹Р±РѕСЂР° С‚Р°СЂРёС„Р° РїРѕРґРїРёСЃРєРё"""
    return {}


async def apply_promo_code_handler(request):
    """РџСЂРёРјРµРЅСЏРµС‚ РїСЂРѕРјРѕРєРѕРґ Рё Р°РєС‚РёвРёСЂСѓРµС‚ РїРѕРґРїРёСЃРєСѓ"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    if not user_id:
        return web.json_response({'success': False, 'message': 'РќРµ Р°вС‚РѕСЂРёР·РѕвР°РЅ'}, status=401)

    data = await request.post()
    promo_code = data.get('promo_code', '').strip().upper()

    if not promo_code:
        return web.json_response({'success': False, 'message': 'Р’вРµРґРёС‚Рµ РїСЂРѕРјРѕРєРѕРґ'})

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'success': False, 'message': 'РџРѕР»СЊР·РѕвР°С‚РµР»СЊ наµ на°Р№РґРµРЅ'})

        # РџСЂРѕвРµСЂСЏРµРј РїСЂРѕРјРѕРєРѕРґ
        promo = session.query(PromoCode).filter_by(code=promo_code).first()
        if not promo:
            return web.json_response({'success': False, 'message': 'РќРµвРµСЂРЅС‹Р№ РїСЂРѕРјРѕРєРѕРґ'})

        # РџСЂРѕвРµСЂСЏРµРј СЃСЂРѕРє РґРµР№СЃС‚вРёСЏ - РїСЂРёвРѕРґРёРј РѕР±Рµ РґР°С‚С‹ Рє РѕРґнаѕРјСѓ С„РѕСЂРјР°С‚Сѓ
        now = datetime.now(dt_timezone.utc)
        expires_at = promo.expires_at.replace(tzinfo=dt_timezone.utc) if promo.expires_at.tzinfo is None else promo.expires_at
        if expires_at < now:
            return web.json_response({'success': False, 'message': 'РЎСЂРѕРє РґРµР№СЃС‚вРёСЏ РїСЂРѕРјРѕРєРѕРґР° РёСЃС‚РµРє'})

        # РџСЂРѕвРµСЂСЏРµРј Р»РёРјРёС‚ РёСЃРїРѕР»СЊР·РѕвР°наёР№
        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            return web.json_response({'success': False, 'message': 'РџСЂРѕРјРѕРєРѕРґ РґРѕСЃС‚РёРі Р»РёРјРёС‚Р° РёСЃРїРѕР»СЊР·РѕвР°наёР№'})

        # РџСЂРѕвРµСЂСЏРµРј, РёСЃРїРѕР»СЊР·РѕвР°Р» Р»Рё СѓР¶Рµ СЌС‚РѕС‚ РїРѕР»СЊР·РѕвР°С‚РµР»СЊ СЌС‚РѕС‚ РїСЂРѕРјРѕРєРѕРґ
        import json
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id in used_by_users:
            return web.json_response({'success': False, 'message': 'Р’С‹ СѓР¶Рµ РёСЃРїРѕР»СЊР·РѕвР°Р»Рё СЌС‚РѕС‚ РїСЂРѕРјРѕРєРѕРґ'})

        # РђРєС‚РёвРёСЂСѓРµРј РїРѕРґРїРёСЃРєСѓ
        start_date = now
        end_date = start_date + timedelta(days=promo.duration_days)

        # РС‰РµРј СЃСѓС‰РµСЃС‚вСѓСЋС‰СѓСЋ РїРѕРґРїРёСЃРєСѓ РёР»Рё СЃРѕР·РґР°РµРј наѕвСѓСЋ
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

        # РћР±наѕвР»СЏРµРј user.subscription_tier РґР»СЏ СЃРёРЅС…СЂРѕнаёР·Р°С†РёРё
        user.subscription_tier = promo.tier

        # РћР±наѕвР»СЏРµРј СЃС‡РµС‚С‡РёРє РёСЃРїРѕР»СЊР·РѕвР°наёР№
        promo.used_count += 1
        if promo.max_uses is None or promo.used_count >= promo.max_uses:
            promo.is_used = True

        # Р”РѕР±Р°вР»СЏРµРј РїРѕР»СЊР·РѕвР°С‚РµР»СЏ в СЃРїРёСЃРѕРє РёСЃРїРѕР»СЊР·РѕвР°вС€РёС…
        import json
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id not in used_by_users:
            used_by_users.append(user.id)
        promo.used_by_users = json.dumps(used_by_users)

        # РЈСЃС‚Р°СЂРµвС€РёРµ РїРѕР»СЏ РґР»СЏ СЃРѕвРјРµСЃС‚РёРјРѕСЃС‚Рё
        promo.used_by_user_id = user.id
        promo.used_at = now

        session.commit()
        logger.info(f"Promo code {promo_code} activated for user {user.id}, subscription created/updated with tier {subscription.tier}")

        # РЎРѕС…СЂР°РЅСЏРµРј Р·на°С‡РµнаёСЏ РґРѕ Р·Р°РєСЂС‹С‚РёСЏ СЃРµСЃСЃРёРё
        tier_name = promo.tier.value if hasattr(promo.tier, 'value') else str(promo.tier)
        duration = promo.duration_days
        end_date_str = end_date.strftime("%d.%m.%Y")

        return web.json_response({
            'success': True,
            'message': f'РџСЂРѕРјРѕРєРѕРґ Р°РєС‚РёвРёСЂРѕвР°РЅ! РџРѕРґРїРёСЃРєР° {tier_name} на° {duration} РґнаµР№ РґРѕ {end_date_str}'
        })

    except Exception as e:
        logger.error(f"Error applying promo code: {e}", exc_info=True)
        session.rollback()
        return web.json_response({'success': False, 'message': f'РћС€РёР±РєР° Р°РєС‚РёвР°С†РёРё РїСЂРѕРјРѕРєРѕРґР°: {str(e)}'}, status=500)
    finally:
        session.close()


async def create_payment_handler(request):
    """РЎРѕР·РґР°РµС‚ РїР»Р°С‚РµР¶ РґР»СЏ вС‹Р±СЂР°наЅРѕРіРѕ С‚Р°СЂРёС„Р°"""
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
            description=f"РџРѕРґРїРёСЃРєР° ASI Biont - {tier_name} на° 30 РґнаµР№",
            user_id=user_id,
            tier=tier
        )

        logger.info(f"Payment URL created: {payment_url}")
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return web.Response(text=f'РћС€РёР±РєР° СЃРѕР·РґР°наёСЏ РїР»Р°С‚РµР¶Р°: {str(e)}', status=500)


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
        
        # Р”Р°наЅС‹Рµ РїРѕР»СЊР·РѕвР°С‚РµР»РµР№
        sport_users = [
            {'username': 'sport_alex', 'telegram_id': 1000001, 'interests': 'С„СѓС‚Р±РѕР», Р±Р°СЃРєРµС‚Р±РѕР», вРѕР»РµР№Р±РѕР»', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_maria', 'telegram_id': 1000002, 'interests': 'Р±РµРі, Р№РѕРіР°, РїРёР»Р°С‚РµСЃ', 'tier': SubscriptionTier.STANDARD},
            {'username': 'sport_ivan', 'telegram_id': 1000003, 'interests': 'С‚РµнаЅРёСЃ, РїР»Р°вР°наёРµ, вРµР»РѕСЃРїРѕСЂС‚', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'sport_olga', 'telegram_id': 1000004, 'interests': 'С„РёС‚наµСЃ, РєСЂРѕСЃСЃС„РёС‚, Р±РѕРґРёР±РёР»РґРёнаі', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_dmitry', 'telegram_id': 1000005, 'interests': 'С…РѕРєРєРµР№, Р±РёР°С‚Р»РѕРЅ, Р»С‹Р¶Рё', 'tier': SubscriptionTier.STANDARD},
        ]
        
        business_users = [
            {'username': 'biz_anna', 'telegram_id': 2000001, 'interests': 'СЃС‚Р°СЂС‚Р°РїС‹, РјР°СЂРєРµС‚Рёнаі, РїСЂРѕРґР°Р¶Рё', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_sergey', 'telegram_id': 2000002, 'interests': 'РёнаІРµСЃС‚РёС†РёРё, С„Рёна°РЅСЃС‹, РєСЂРёРїС‚РѕвР°Р»СЋС‚Р°', 'tier': SubscriptionTier.LIGHT},
            {'username': 'biz_elena', 'telegram_id': 2000003, 'interests': 'СѓРїСЂР°вР»РµнаёРµ РїСЂРѕРµРєС‚Р°РјРё, agile, scrum', 'tier': SubscriptionTier.STANDARD},
            {'username': 'biz_maxim', 'telegram_id': 2000004, 'interests': 'e-commerce, Рѕна»Р°Р№РЅ-С‚РѕСЂРіРѕвР»СЏ, Р»РѕРіРёСЃС‚РёРєР°', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_victoria', 'telegram_id': 2000005, 'interests': 'HR, СЂРµРєСЂСѓС‚Рёнаі, РѕР±СѓС‡РµнаёРµ РїРµСЂСЃРѕна°Р»Р°', 'tier': SubscriptionTier.LIGHT},
        ]
        
        all_users = sport_users + business_users
        added = []
        skipped = []
        
        for user_data in all_users:
            existing_user = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            
            if existing_user:
                # РџСЂРѕвРµСЂСЏРµРј РµСЃС‚СЊ Р»Рё subscription
                existing_sub = session.query(Subscription).filter_by(user_id=existing_user.id).first()
                if existing_sub:
                    skipped.append(user_data['username'])
                    continue
                else:
                    # User РµСЃС‚СЊ, наѕ subscription наµС‚ - РґРѕР±Р°вР»СЏРµРј С‚РѕР»СЊРєРѕ subscription
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

# Предварительная загрузка кэша погоды и новостей
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
            logger.info("вњ… Successfully added pending_delegator_report column")
        else:
            logger.info("вњ… pending_delegator_report column already exists")
            
    except Exception as e:
        logger.error(f"Error during database schema check: {e}")


async def start_reminder_service(app):
    logger.info("Starting ReminderService...")
    await reminder_service.start()
    logger.info("ReminderService started successfully")

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
                    await runner.cleanup()
                    logger.info("Server shut down")

            asyncio.run(run_server())
        except Exception as serve_error:
            logger.error(f"Error in asyncio run: {serve_error}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Failed to start application: {e}", exc_info=True)
        raise
