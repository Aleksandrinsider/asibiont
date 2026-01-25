from models import Base, engine, Session, Subscription, User, Task, UserProfile, Interaction, UserRating, SubscriptionTier, PromoCode, PaymentHistory, Post, PostLike, Comment, init_db
from reminder_service import ReminderService
from ai_integration import chat_with_ai, get_partners_list, decrypt_data, encrypt_data
from security_monitor import security_monitor
from datetime import datetime, timedelta, timezone as dt_timezone
from config import TELEGRAM_TOKEN, TELEGRAM_BOT_USERNAME, PORT, ADMIN_SECRET, CURRENT_DATE, DATABASE_URL, LOCAL
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

# Import handlers
if not LOCAL:
    from handlers import router as handlers_router
import hashlib
import hmac
import json
import warnings
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Aiogram imports
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
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

try:
    # Test database connection
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("✅ Database connection successful")

    # Initialize database tables
    init_db()
except Exception as e:
    logger.error(f"❌ Database connection failed: {e}")
    logger.error("Application may not work correctly without database connection")
    # Don't exit, let the app start anyway for webhook setup
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite database connection issues")

try:
    logger.info("Creating database tables...")
    Base.metadata.create_all(engine)
    logger.info("✅ Database tables created or already exist")
except Exception as e:
    logger.error(f"❌ Failed to create database tables: {e}")
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite table creation issues")

logger.info("Running database migrations...")
try:
    # Migration code is inline below
    """Run database migrations"""
    logger.info("Starting database migrations...")
    from sqlalchemy import text, inspect

    # Check database type
    is_sqlite = 'sqlite' in str(engine.url).lower()
    logger.info(f"Database type: {'SQLite' if is_sqlite else 'PostgreSQL'}")

    try:
        session = Session()
        logger.info("Migration session created")
        inspector = inspect(engine)

        if 'user_profiles' not in inspector.get_table_names():
            logger.info("user_profiles table does not exist, skipping migration")
            session.close()
            logger.info("Migration completed (skipped - no user_profiles table)")

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

        # Migration for birthdate column
        if 'birthdate' not in columns:
            try:
                logger.info("Adding birthdate column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN birthdate VARCHAR(10)'))
                session.commit()
                logger.info("Migration: birthdate column added successfully")
            except Exception as e:
                logger.error(f"Failed to add birthdate column: {e}")
                session.rollback()
        else:
            logger.info("Migration: birthdate column already exists")

        # Migration for zodiac_sign column
        if 'zodiac_sign' not in columns:
            try:
                logger.info("Adding zodiac_sign column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN zodiac_sign VARCHAR(20)'))
                session.commit()
                logger.info("Migration: zodiac_sign column added successfully")
            except Exception as e:
                logger.error(f"Failed to add zodiac_sign column: {e}")
                session.rollback()
        else:
            logger.info("Migration: zodiac_sign column already exists")

        # Migration for favorite_contacts column
        if 'favorite_contacts' not in columns:
            try:
                logger.info("Adding favorite_contacts column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN favorite_contacts TEXT'))
                session.commit()
                logger.info("Migration: favorite_contacts column added successfully")
            except Exception as e:
                logger.error(f"Failed to add favorite_contacts column: {e}")
                session.rollback()
        else:
            logger.info("Migration: favorite_contacts column already exists")

        # Migration for blocked_contacts column
        if 'blocked_contacts' not in columns:
            try:
                logger.info("Adding blocked_contacts column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN blocked_contacts TEXT'))
                session.commit()
                logger.info("Migration: blocked_contacts column added successfully")
            except Exception as e:
                logger.error(f"Failed to add blocked_contacts column: {e}")
                session.rollback()
        else:
            logger.info("Migration: blocked_contacts column already exists")

        # Migration for interaction_count column
        if 'interaction_count' not in columns:
            try:
                logger.info("Adding interaction_count column to user_profiles table")
                session.execute(text('ALTER TABLE user_profiles ADD COLUMN interaction_count INTEGER DEFAULT 0'))
                session.commit()
                logger.info("Migration: interaction_count column added successfully")
            except Exception as e:
                logger.error(f"Failed to add interaction_count column: {e}")
                session.rollback()
        else:
            logger.info("Migration: interaction_count column already exists")

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

            if 'conversation_state' not in user_columns:
                logger.info("Adding conversation_state column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN conversation_state VARCHAR(100) DEFAULT \'normal\''))
                session.commit()
                logger.info("Migration: conversation_state column added successfully")
            else:
                logger.info("Migration: conversation_state column already exists")

            if 'pending_task_data' not in user_columns:
                logger.info("Adding pending_task_data column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN pending_task_data TEXT'))
                session.commit()
                logger.info("Migration: pending_task_data column added successfully")
            else:
                logger.info("Migration: pending_task_data column already exists")

            if 'last_interaction_at' not in user_columns:
                logger.info("Adding last_interaction_at column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN last_interaction_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'))
                session.commit()
                logger.info("Migration: last_interaction_at column added successfully")
            else:
                logger.info("Migration: last_interaction_at column already exists")

            if 'conversation_context' not in user_columns:
                logger.info("Adding conversation_context column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN conversation_context TEXT'))
                session.commit()
                logger.info("Migration: conversation_context column added successfully")
            else:
                logger.info("Migration: conversation_context column already exists")

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

            if 'history_cleared_at' not in user_columns:
                logger.info("Adding history_cleared_at column to users table")
                session.execute(text('ALTER TABLE users ADD COLUMN history_cleared_at TIMESTAMP'))
                session.commit()
                logger.info("Migration: history_cleared_at column added successfully")
            else:
                logger.info("Migration: history_cleared_at column already exists")

            # Migration for subscription_tier column
            if is_sqlite:
                logger.info("Skipping subscription_tier enum migration for SQLite")
            else:
                # Check if subscription_tier column exists and has correct type
                user_columns = [col['name'] for col in inspector.get_columns('users')]
                recreate_needed = False

                if 'subscription_tier' in user_columns:
                    # Check column type
                    column_info = next((col for col in inspector.get_columns('users') if col['name'] == 'subscription_tier'), None)
                    if column_info:
                        column_type = str(column_info['type']).upper()
                        # If column exists but type is not our enum, need to recreate
                        if 'SUBSCRIPTION_TIER_ENUM' not in column_type:
                            recreate_needed = True
                            logger.info(f"subscription_tier column exists but has wrong type ({column_type}), recreating")
                        else:
                            logger.info("subscription_tier column already exists with correct enum type, skipping recreation")
                    else:
                        recreate_needed = True
                else:
                    recreate_needed = True
                    logger.info("subscription_tier column does not exist, creating")

                if recreate_needed:
                    # Migration for subscription_tier column - recreate only if needed
                    logger.info("Recreating subscription_tier column to fix enum values")

                    # First, change dependent columns to text to allow dropping the enum type
                    if 'subscriptions' in inspector.get_table_names():
                        sub_columns = [col['name'] for col in inspector.get_columns('subscriptions')]
                        if 'tier' in sub_columns:
                            logger.info("Temporarily changing subscriptions.tier to text to allow enum recreation")
                            session.execute(text("ALTER TABLE subscriptions ALTER COLUMN tier DROP DEFAULT"))
                            session.execute(text("ALTER TABLE subscriptions ALTER COLUMN tier TYPE TEXT"))

                    session.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS subscription_tier"))
                    session.execute(text("DROP TYPE IF EXISTS subscription_tier_enum"))
                    session.execute(text("CREATE TYPE subscription_tier_enum AS ENUM ('BRONZE', 'SILVER', 'GOLD')"))
                    session.execute(text('ALTER TABLE users ADD COLUMN subscription_tier subscription_tier_enum DEFAULT \'BRONZE\''))

                    # Update existing users data to match new enum values (after creating the enum)
                    session.execute(text("UPDATE users SET subscription_tier = CASE WHEN LOWER(subscription_tier::text) = 'bronze' THEN 'BRONZE'::subscription_tier_enum WHEN LOWER(subscription_tier::text) = 'silver' THEN 'SILVER'::subscription_tier_enum WHEN LOWER(subscription_tier::text) = 'gold' THEN 'GOLD'::subscription_tier_enum ELSE 'BRONZE'::subscription_tier_enum END"))

                    # Update subscriptions.tier back to enum type with correct values
                    if 'subscriptions' in inspector.get_table_names():
                        sub_columns = [col['name'] for col in inspector.get_columns('subscriptions')]
                        if 'tier' in sub_columns:
                            logger.info("Converting subscriptions.tier back to enum type")
                            # Update existing data to match new enum values (after creating the enum)
                            session.execute(text("UPDATE subscriptions SET tier = CASE WHEN LOWER(tier::text) = 'bronze' THEN 'BRONZE'::subscription_tier_enum WHEN LOWER(tier::text) = 'silver' THEN 'SILVER'::subscription_tier_enum WHEN LOWER(tier::text) = 'gold' THEN 'GOLD'::subscription_tier_enum ELSE 'BRONZE'::subscription_tier_enum END"))
                            session.execute(text("ALTER TABLE subscriptions ALTER COLUMN tier TYPE subscription_tier_enum USING tier::subscription_tier_enum"))
                            session.execute(text("ALTER TABLE subscriptions ALTER COLUMN tier SET DEFAULT 'BRONZE'"))

                    session.commit()
                    logger.info("Migration: subscription_tier column recreated successfully")
                else:
                    logger.info("Migration: subscription_tier column already correct, skipping recreation")

            # Migration for tier column in subscriptions table
            if 'subscriptions' in inspector.get_table_names():
                sub_columns = [col['name'] for col in inspector.get_columns('subscriptions')]
                if 'tier' not in sub_columns:
                    logger.info("Adding tier column to subscriptions table")
                    if is_sqlite:
                        session.execute(text('ALTER TABLE subscriptions ADD COLUMN tier TEXT DEFAULT \'BRONZE\''))
                    else:
                        session.execute(text('ALTER TABLE subscriptions ADD COLUMN tier subscription_tier_enum DEFAULT \'BRONZE\''))
                    session.commit()
                    logger.info("Migration: tier column added to subscriptions table successfully")
                else:
                    logger.info("Migration: tier column already exists in subscriptions table")

        # Migration for promo_codes table
        if 'promo_codes' not in inspector.get_table_names():
            logger.info("Creating promo_codes table")
            if is_sqlite:
                session.execute(text('''
                    CREATE TABLE promo_codes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code VARCHAR(50) UNIQUE NOT NULL,
                        tier TEXT DEFAULT 'BRONZE',
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
                        tier subscription_tier_enum DEFAULT 'BRONZE',
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

        session.close()
        logger.info("Migration session closed successfully")
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        session.close()
        raise

    logger.info("✅ Database migrations completed")
except Exception as e:
    logger.error(f"❌ Database migrations failed: {e}")
    if not LOCAL:
        raise  # Fail hard in production
    else:
        logger.warning("Continuing with local mode despite migration issues")

# Subscription restoration removed for production



"""
def check_database_connection():
    \"\"\"Добавляет тестовых пользователей с интересами 'спорт' если их еще нет\"\"\"
    try:
        session = Session()

        test_users_data = [{'telegram_id': 111111,
                            'username': 'sportfan1',
                            'first_name': 'Алексей',
                            'interests': 'спорт, бег, фитнес',
                            'city': 'Москва',
                            'company': 'Фитнес-клуб',
                            'position': 'Тренер',
                            'subscription_tier': SubscriptionTier.BRONZE},
                           {'telegram_id': 222222,
                            'username': 'sportfan2',
                            'first_name': 'Дмитрий',
                            'interests': 'спорт, футбол, плавание',
                            'city': 'Санкт-Петербург',
                            'company': 'Спортивная школа',
                            'position': 'Инструктор',
                            'subscription_tier': SubscriptionTier.SILVER},
                           {'telegram_id': 333333,
                            'username': 'sportfan3',
                            'first_name': 'Михаил',
                            'interests': 'спорт, теннис, йога',
                            'city': 'Москва',
                            'company': 'Теннисный центр',
                            'position': 'Спортсмен',
                            'subscription_tier': SubscriptionTier.GOLD},
                           {'telegram_id': 444444,
                            'username': 'sportfan4',
                            'first_name': 'Елена',
                            'interests': 'спорт, волейбол, танцы',
                            'city': 'Казань',
                            'company': 'Спортивный комплекс',
                            'position': 'Администратор',
                            'subscription_tier': SubscriptionTier.BRONZE},
                           {'telegram_id': 555555,
                            'username': 'sportfan5',
                            'first_name': 'Анна',
                            'interests': 'спорт, гимнастика, пилатес',
                            'city': 'Москва',
                            'company': 'Студия пилатес',
                            'position': 'Инструктор',
                            'subscription_tier': SubscriptionTier.SILVER}]

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
                    timezone='Europe/Moscow',
                    subscription_tier=user_data['subscription_tier']
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
\"\"\"

# All test functions below are disabled in production
\"\"\"
def ensure_sport_interest():
    \"\"\"Добавляет 'спорт' к интересам всех пользователей если его нет\"\"\"
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



#     try:
#         session = Session()

#         # Проверяем, есть ли уже тестовый промокод
#         existing_promo = session.query(PromoCode).filter_by(code='TESTBRONZE').first()
#         if existing_promo:
#             logger.info("Test promo code TESTBRONZE already exists")
#             session.close()
#             return

#         # Создаем тестовый промокод на бронзу на месяц, действующий год
#         expires_at = datetime.now(dt_timezone.utc) + timedelta(days=365)
#         test_promo = PromoCode(
#             code='TESTBRONZE',
#             tier=SubscriptionTier.BRONZE,
#             duration_days=30,
#             expires_at=expires_at
#         )
#         session.add(test_promo)
#         session.commit()
#         logger.info("Created test promo code: TESTBRONZE (Bronze for 30 days, expires in 1 year)")
#         session.close()
#     except Exception as e:
#         logger.error(f"Failed to create test promo codes: {e}")
# # """

# Test database connection before starting
try:
    test_session = Session()
    test_session.execute(text('SELECT 1'))
    test_session.close()
    logger.info("✅ Database connection successful")
except Exception as e:
    logger.error(f"❌ CRITICAL: Cannot connect to database: {e}", exc_info=True)
    logger.error(f"DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "DATABASE_URL not set")
    # Don't exit, let Railway restart the app

try:
    # Migrations are already run above
    logger.info("Database migrations completed")
    # Production mode: Test users and promo codes disabled
    logger.info("Production mode: Test data creation disabled")

    # Create test users with different tiers and sport interests (only in local mode)
    if os.getenv('LOCAL') == '1':
        try:
            session_db = Session()
            logger.info("Creating test users with different subscription tiers")

            test_users_data = [
                {'telegram_id': 1001, 'tier': 'BRONZE', 'name': 'Test User Bronze'},
                {'telegram_id': 1002, 'tier': 'SILVER', 'name': 'Test User Silver'},
                {'telegram_id': 1003, 'tier': 'GOLD', 'name': 'Test User Gold'},
                {'telegram_id': 1004, 'tier': 'BRONZE', 'name': 'Test User Bronze 2'},
                {'telegram_id': 1005, 'tier': 'SILVER', 'name': 'Test User Silver 2'},
                {'telegram_id': 1006, 'tier': 'SILVER', 'name': 'Test User Silver 3'},
                {'telegram_id': 1007, 'tier': 'GOLD', 'name': 'Test User Gold 2'},
                {'telegram_id': 1008, 'tier': 'SILVER', 'name': 'Test User Silver 4'},
                {'telegram_id': 1009, 'tier': 'GOLD', 'name': 'Test User Gold 3'},
            ]

            now = datetime.now()

            added_count = 0
            for user_data in test_users_data:
                # Check if user already exists
                existing_user = session_db.query(User).filter(User.telegram_id == user_data['telegram_id']).first()
                if existing_user:
                    logger.info(f"Test user {user_data['telegram_id']} already exists")
                    continue

                # Create user
                user = User(
                    telegram_id=user_data['telegram_id'],
                    first_name=user_data['name'],
                    subscription_tier=user_data['tier'],  # Set subscription tier
                    created_at=now
                )
                session_db.add(user)
                session_db.flush()  # Get user.id

                # Create profile with sport interests
                profile = UserProfile(
                    user_id=user.id,
                    interests='спорт, фитнес, здоровый образ жизни',
                    city='Москва',
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
                        title="Подготовить презентацию для клиента",
                        description="Создать презентацию о наших услугах",
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
                        title="Проверить код на ошибки",
                        description="Ревью кода для нового модуля",
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
                        title="Организовать встречу с командой",
                        description="Запланировать еженедельную встречу",
                        status="pending",
                        created_at=now,
                        delegated_to_username=user_1002.username,
                        delegation_status="accepted"
                    )
                    session_db.add(task3)
                    logger.info("Created delegated task from user 1001 to user 1002")

            if added_count > 0:
                session_db.commit()
                logger.info(f"Successfully added {added_count} test users")
            else:
                logger.info("All test users already exist")
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


def check_duplicate_message(user_id, message_text):
    """Check for duplicate message in last 30 seconds"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        
        # Check if same message exists in last 30 seconds
        threshold = datetime.now(dt_timezone.utc) - timedelta(seconds=30)
        duplicate = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user',
            Interaction.content == message_text,
            Interaction.created_at > threshold
        ).first()
        
        return duplicate is not None
    finally:
        session.close()


async def get_timezone_from_ip(ip_address):
    """Определяет timezone по IP адресу через ipapi.co"""
    # Маппинг английских названий городов на русские
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
        # Игнорируем локальные IP
        if ip_address.startswith(('127.', '192.168.', '10.', '172.')):
            return 'Europe/Moscow', 'Москва'  # По умолчанию для локальных

        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://ipapi.co/{ip_address}/json/', timeout=aiohttp.ClientTimeout(total=3)) as response:
                if response.status == 200:
                    data = await response.json()
                    timezone = data.get('timezone')
                    city = data.get('city')

                    # Преобразуем английское название города в русское, если есть в маппинге
                    if city and city in city_mapping:
                        city = city_mapping[city]

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
        error_str = str(e).lower()
        if "user not found" in error_str or "bad request" in error_str:
            # Для тестовых пользователей или несуществующих пользователей не логируем ошибку
            logger.debug(f"User {user_id} not found or has no avatar (expected for test users)")
        else:
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
    """Страница авторизации"""
    session = await get_session(request)
    user_id = session.get('user_id')

    # Check for logout parameter
    if request.query.get('logout') == '1':
        session.pop('user_id', None)
        session.pop('history_cleared_timestamp', None)
        user_id = None

    # Если пользователь уже залогинен, редиректим на dashboard
    if user_id:
        try:
            user_id = int(user_id)
            return web.HTTPFound('/dashboard')
        except (ValueError, TypeError):
            pass

    # Показываем страницу авторизации
    bot_user = TELEGRAM_BOT_USERNAME.replace(
        '@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'Asibiont_bot')
    return aiohttp_jinja2.render_template('dashboard_new.html', request, {
        'logged_in': False,
        'bot_username': bot_user,
        'subscription_tier': 'BRONZE',
        'current_date': '',
        'current_time': '',
        'formatted_end_date': None,
        'timestamp': 1768857743,
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
                            avatar_url = await get_user_avatar_url(request.app['bot'], user_id)
                            logger.info(f"Got avatar URL for new user {user_id}: {avatar_url}")
                        except Exception as e:
                            logger.error(f"Error getting avatar for new user {user_id}: {e}")

                    user = User(
                        telegram_id=user_id,
                        username=data.get('username'),
                        first_name=data.get('first_name'),
                        photo_url=avatar_url,
                        timezone=timezone)
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
            except Exception as e:
                logger.error(f"Database error in auth_handler: {e}", exc_info=True)
                if session_db:
                    session_db.rollback()
                return web.Response(text=f'Ошибка подключения к базе данных. Попробуйте позже.', status=500)
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
            logger.info(f"Session set with user_id: {user_id}")

            response = web.HTTPFound('/dashboard')
            logger.info("Redirecting to /dashboard after auth")
            return response
        else:
            logger.error(f"Authentication failed for data: {data}")
            return web.Response(text='Authentication failed', status=401)
    except Exception as e:
        logger.error(f"CRITICAL ERROR in auth_handler: {e}", exc_info=True)
        return web.Response(text=f'Internal server error: {str(e)}', status=500)
        return web.Response(text='Authentication failed', status=401)


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

        if not logged_in:
            # Show login page in dashboard
            bot_user = TELEGRAM_BOT_USERNAME.replace(
                '@', '') if TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_USERNAME.startswith('@') else (TELEGRAM_BOT_USERNAME or 'Asibiont_bot')
            logger.info(f"Rendering login page with bot_username: {bot_user}, original: {TELEGRAM_BOT_USERNAME}")
            return aiohttp_jinja2.render_template('dashboard_new.html', request, {
                'logged_in': False,
                'bot_username': bot_user,
                'subscription_tier': 'BRONZE',
                'current_date': '',
                'current_time': '',
                'formatted_end_date': None,
                'timestamp': 1768857743,
                'user_timezone': 'UTC',
                'user': None,
                'profile': None,
                'tasks': [],
                'messages': [],
                'partners': [],
                'subscription': None
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
                    'subscription_tier': 'BRONZE',
                    'current_date': '',
                    'current_time': '',
                    'formatted_end_date': None,
                    'timestamp': 1768857743,
                    'user_timezone': 'UTC',
                    'user': None,
                    'profile': None,
                    'tasks': [],
                    'messages': [],
                    'partners': [],
                    'subscription': None
                })

            logger.info(f"User found: {user.id}, telegram_id: {user.telegram_id}")

            # Проверить подписку
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()

            # Проверить и обновить статус истекших подписок
            if subscription and subscription.status == 'active' and subscription.end_date:
                now = datetime.now(pytz.UTC)
                if subscription.end_date.tzinfo is None:
                    subscription.end_date = subscription.end_date.replace(tzinfo=pytz.UTC)
                if subscription.end_date < now:
                    subscription.status = 'expired'
                    # user.subscription_tier = SubscriptionTier.BRONZE  # Сбросить тариф на бронзу при истечении - убрано по просьбе пользователя
                    session_db.commit()
                    logger.info(f"Subscription {subscription.id} expired, status set to 'expired'")

            # Синхронизировать тариф пользователя с активной подпиской
            if subscription and subscription.status == 'active' and subscription.tier:
                sub_tier = subscription.tier.value if hasattr(subscription.tier, 'value') else str(subscription.tier).upper()
                user_tier = user.subscription_tier.value if user.subscription_tier else None

                if sub_tier != user_tier:
                    logger.info(f"Syncing user tier: {user_tier} -> {sub_tier}")
                    if sub_tier == 'BRONZE':
                        user.subscription_tier = SubscriptionTier.BRONZE
                    elif sub_tier == 'SILVER':
                        user.subscription_tier = SubscriptionTier.SILVER
                    elif sub_tier == 'GOLD':
                        user.subscription_tier = SubscriptionTier.GOLD
                    session_db.commit()
                    logger.info(f"User {user.username} tier synced to {sub_tier}")

            logger.info(
                f"Subscription found: {subscription.id if subscription else None}, status: {subscription.status if subscription else None}, end_date: {subscription.end_date if subscription else None}, tier: {subscription.tier if subscription else None}, user_tier: {user.subscription_tier.value if user.subscription_tier else None}")

            # В FREE_ACCESS_MODE не требуется активная подписка
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
                logger.info("No active subscription, redirecting to subscription_tiers")
                return web.HTTPFound('/subscription_tiers')

            tasks = session_db.query(Task).filter(
                or_(
                    Task.user_id == user.id,
                    Task.delegated_to_username.ilike(user.username)
                )
            ).all()
            logger.info(f"Found {len(tasks)} tasks for user {user.id} (telegram_id: {user.telegram_id})")
            for task in tasks:
                logger.info(f"Task {task.id}: {task.title} (user_id: {task.user_id})")
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first() if user else None

            # Проверяем timestamp очистки истории из БД
            history_cleared_timestamp = None
            if user.history_cleared_at:
                history_cleared_timestamp = user.history_cleared_at.timestamp()
                logger.info(f"History cleared timestamp from DB: {history_cleared_timestamp}")

            # Берем последние 50 сообщений, но фильтруем по timestamp очистки
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
                                # Naive datetime - интерпретируем как UTC напрямую через replace
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

            # Store subscription tier before closing session
            user_subscription_tier = user.subscription_tier if user and user.subscription_tier else SubscriptionTier.BRONZE

            # Получить контакты по делегированию
            delegating_to_me = []  # Люди, которые делегировали мне задачи
            delegating_by_me = []  # Люди, которым я делегировал задачи

            try:
                # Получить список избранных контактов
                favorite_contacts = []
                if profile and profile.favorite_contacts:
                    try:
                        raw_favorites = json.loads(profile.favorite_contacts)
                        favorite_contacts = [str(c).lower().replace('@', '') if isinstance(c, str) else str(c) for c in raw_favorites]
                    except json.JSONDecodeError:
                        favorite_contacts = []

                # Люди, которые делегировали мне задачи (я получаю задачи от них)
                delegated_tasks = session_db.query(Task).filter(
                    Task.delegated_to_username.ilike(user.username.replace('@', '')),
                    Task.delegation_status.in_(['pending', 'accepted']),
                    Task.status != 'deleted',
                    Task.status != 'rejected'
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

                # Добавить избранные контакты, у которых все задачи отклонены, но контакт в избранном
                for favorite_username in favorite_contacts:
                    favorite_user = session_db.query(User).filter(
                        User.username.ilike(favorite_username)
                    ).first()
                    
                    if favorite_user and favorite_user.id != user.id and favorite_user.id not in delegator_ids:
                        # Проверить, были ли у этого контакта задачи (включая отклоненные)
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
                                    'reason': f'в избранном',
                                    'tasks': [],
                                    'task_count': 0
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
                                'reason': f'я делегировал {task_count} задач',
                                'tasks': task_titles,
                                'task_count': task_count
                            })

                # Для премиум пользователей (Gold) добавляем все рекомендованные контакты
                if user_subscription_tier and user_subscription_tier.value == 'GOLD':
                    # Получаем все рекомендованные контакты
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
                            if hasattr(partner, 'task_relevance') and partner.task_relevance:
                                reason_parts.append(partner.task_relevance)
                            
                            reason = ', '.join(reason_parts) if reason_parts else 'рекомендован системой'
                            
                            # Добавляем в delegating_to_me как рекомендованный контакт
                            delegating_to_me.append({
                                'id': partner_user.id,
                                'username': partner_user.username,
                                'first_name': partner_user.first_name,
                                'reason': reason,
                                'tasks': [],
                                'task_count': 0
                            })

            except Exception as e:
                logger.error(f"Error getting delegation contacts: {e}")
                delegating_to_me = []
                delegating_by_me = []

            # Получить заблокированные контакты
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
                                'reason': 'заблокированный контакт'
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
                if tier == 'BRONZE':
                    partners = partners[:1]  # Bronze: 1 contact
                elif tier == 'SILVER':
                    partners = partners[:5]  # Silver: 5 contacts
                # Gold: unlimited (already limited to 20 in get_partners_list)

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

            # Получаем список контактов, с которыми уже общались
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

        logger.info(f"Rendering dashboard for user {user.id} with subscription_tier: {user_subscription_tier.value if user_subscription_tier else 'BRONZE'}")

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
            'subscription_tier': user_subscription_tier.value if user_subscription_tier else 'BRONZE',
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'pending_tasks': pending_tasks,
            'skipped_tasks': skipped_tasks,
            'current_date': current_date,
            'current_time': current_time,
            'user_timezone': user.timezone if user and user.timezone else 'UTC',
            'formatted_end_date': formatted_end_date,
            'upcoming_reminders': upcoming_reminders[:5],  # Limit to 5
            'timestamp': 1768857743,
            'bot_username': TELEGRAM_BOT_USERNAME.replace('@', ''),
            'user_avatar_url': user_avatar_url
        })
    except Exception as e:
        logger.error(f"Unexpected error in dashboard_handler: {e}", exc_info=True)
        bot_user = TELEGRAM_BOT_USERNAME.replace('@', '') if TELEGRAM_BOT_USERNAME else 'Asibiont_bot'
        return aiohttp_jinja2.render_template('dashboard_new.html', request, {
            'logged_in': False,
            'bot_username': bot_user,
            'subscription_tier': 'BRONZE',
            'current_date': '',
            'current_time': '',
            'formatted_end_date': None,
            'timestamp': 1768857743
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

        # Save user message WITH PRECISE TIMESTAMP before AI call
        user_message_timestamp = datetime.now(dt_timezone.utc)

        # Check for duplicate via DB (web chat duplicate protection)
        is_duplicate = check_duplicate_message(user_id, message)
        if is_duplicate:
            logger.warning(f"[WEB DUPLICATE] Message from user {user_id} IGNORED (already processed): '{message[:100]}...'")
            return web.json_response({'duplicate': True, 'message': 'Message already processed'})
        
        logger.info(f"[WEB CHAT] New message from user {user_id}: '{message[:100]}...'")

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            logger.info(f"User found: {user is not None}")

            # Get AI response (will take time, so agent timestamp will be later)
            try:
                logger.info(f"Calling chat_with_ai with user_id: {user_id}")
                response = await chat_with_ai(message, context, user_id, file_content)
                logger.info("AI response: %s...", response[:100])
            except Exception as e:
                logger.error(f"Error getting AI response: {e}", exc_info=True)
                response = f"Ошибка: {str(e)}"

            # Save interaction to DB (only user message here, AI response saved separately)
            save_context_to_db(user_id, message, None)  # Don't save AI response here
            logger.info("User message saved to DB")

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
        logger.info(f"API send_message handler called, session user_id: {user_id}")

        if not user_id:
            logger.warning("No user_id in session for api_send_message")
            return web.json_response({'error': 'Not authenticated'}, status=401)

        data = await request.json()
        message = data.get('message', '')
        logger.info(f"API Message received: {message}")

        # Load context from DB
        context = get_context_from_db(user_id, limit=20)

        # Import chat function
        from ai_integration.chat import chat_with_ai as chat

        # Get user from database
        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return web.json_response({'error': 'User not found'}, status=404)

            # Call AI chat
            try:
                response = await chat(message, context=context, user_id=user_id, file_content=None, db_session=session_db)
                logger.info("AI response: %s...", response[:100])
            except Exception as e:
                logger.error(f"Error calling AI chat: {e}", exc_info=True)
                return web.json_response({'error': 'AI service error'}, status=500)

            # Check if response contains tier restriction error
            if "Делегирование задач доступно только на тарифах" in response:
                return web.json_response({
                    'error': 'tier_restriction',
                    'message': '🥉 Делегирование задач доступно только на тарифах Серебро и Золото',
                    'tier': 'BRONZE',
                    'upgrade_url': '/subscription_tiers'
                }, status=403)

            # Save context to DB
            save_context_to_db(user_id, message, response)
            logger.info("Context saved to DB")
        finally:
            session_db.close()

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

    # Обновляем history_cleared_at в БД
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

        # Ищем задачу либо среди своих, либо среди делегированных мне
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
        result = await complete_task(task_id=task_id, user_id=user_id)
        logger.info(f"Task {task_id} completed by user {user_id}: {result}")
        
        # Отправляем уведомление в Telegram через AI обработку, как будто пользователь написал о выполнении
        try:
            if 'bot' in request.app:
                from models import Session as DBSession, Task, User
                from ai_integration.chat import chat_with_ai
                db_session = DBSession()
                try:
                    # Находим пользователя по user_id (это telegram_id)
                    user = db_session.query(User).filter_by(telegram_id=user_id).first()
                    if user:
                        task = db_session.query(Task).filter_by(id=task_id, user_id=user.id).first()
                        if task:
                            # Отправляем сообщение через AI, как будто пользователь написал о выполнении
                            ai_message = f"я выполнил задачу '{task.title}'"
                            try:
                                ai_response = await chat_with_ai(ai_message, user_id=user_id)
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
    """Восстанавливает задачу в работу"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration import restore_task
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

    from ai_integration import skip_task
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

    from ai_integration import delete_task
    try:
        # Передаём confirmed=True, поскольку пользователь уже подтвердил удаление в UI
        result = await delete_task(task_id=task_id, user_id=user_id, confirmed=True)
        logger.info(f"Task {task_id} deleted by user {user_id}: {result}")
        
        # Если результат содержит флаг, обработаем через AI и отправим в Telegram
        if result.startswith('TASK_COMPLETED_ASK_RESULT:') or result.startswith('TASK_UPDATED:') or result.startswith('TASK_DELETED_ASK_REASON:'):
            try:
                from ai_integration.chat import chat_with_ai
                from models import Session as DBSession, User
                db_session = DBSession()
                try:
                    # Обработка через AI для генерации естественного ответа
                    ai_response = await chat_with_ai(result, user_id=user_id, db_session=db_session)
                    
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


async def reschedule_task_handler(request):
    """Переносит задачу на новую дату"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    new_date = data.get('new_date')
    if not task_id or not new_date:
        return web.json_response({'error': 'Task ID and new date required'}, status=400)

    from ai_integration import reschedule_task
    try:
        result = await reschedule_task(task_id=task_id, new_date=new_date, user_id=user_id)
        logger.info(f"Task {task_id} rescheduled by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error rescheduling task {task_id}: {e}")
        return web.json_response({'error': str(e)}, status=500)


async def get_task_advice_handler(request):
    """Получает совет по задаче от AI"""
    session = await get_session(request)
    user_id = session.get('user_id')
    if not user_id:
        return web.json_response({'error': 'Not authenticated'}, status=401)

    data = await request.json()
    task_id = data.get('task_id')
    if not task_id:
        return web.json_response({'error': 'Task ID required'}, status=400)

    from ai_integration import get_task_advice
    try:
        result = await get_task_advice(task_id=task_id, user_id=user_id)
        logger.info(f"Task advice requested for task {task_id} by user {user_id}: {result}")
        return web.json_response({'message': result})
    except Exception as e:
        logger.error(f"Error getting advice for task {task_id}: {e}")
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


async def admin_users_handler(request):
    """Admin endpoint to view all users in database"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)

    session_db = Session()
    try:
        users = session_db.query(User).all()
        users_data = []
        for user in users:
            profile = session_db.query(UserProfile).filter_by(user_id=user.id).first()
            subscription = session_db.query(Subscription).filter_by(user_id=user.id).first()

            user_data = {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'first_name': user.first_name,
                'subscription_tier': user.subscription_tier.value if user.subscription_tier else None,
                'premium_status': (subscription and subscription.status == 'active') or (user.subscription_tier and user.subscription_tier != SubscriptionTier.BRONZE),
                'timezone': user.timezone,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'photo_url': user.photo_url,
                'profile': {
                    'city': profile.city if profile else None,
                    'company': profile.company if profile else None,
                    'position': profile.position if profile else None,
                    'interests': profile.interests if profile else None,
                    'average_rating': profile.average_rating if profile else 0,
                    'rating_count': profile.rating_count if profile else 0
                } if profile else None,
                'subscription': {
                    'status': subscription.status if subscription else None,
                    'tier': subscription.tier.value if subscription and subscription.tier else None,
                    'start_date': subscription.start_date.isoformat() if subscription and subscription.start_date else None,
                    'end_date': subscription.end_date.isoformat() if subscription and subscription.end_date else None
                } if subscription else None
            }
            users_data.append(user_data)

        return web.json_response({
            'total_users': len(users_data),
            'users': users_data
        })
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def admin_security_handler(request):
    """Admin endpoint to view security statistics"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)
    
    # Get IP info if requested
    ip = request.query.get('ip')
    if ip:
        ip_info = security_monitor.get_ip_info(ip)
        return web.json_response(ip_info)
    
    # Get security statistics
    stats = security_monitor.get_statistics()
    
    # Get user statistics from database
    session_db = Session()
    try:
        from sqlalchemy import func, case
        from datetime import datetime, timedelta
        
        # Общее количество пользователей
        total_users = session_db.query(User).count()
        
        # Пользователи за последние 24 часа
        last_24h = datetime.now() - timedelta(hours=24)
        new_users_24h = session_db.query(User).filter(User.created_at >= last_24h).count()
        
        # Пользователи за последнюю неделю
        last_week = datetime.now() - timedelta(days=7)
        new_users_week = session_db.query(User).filter(User.created_at >= last_week).count()
        
        # Активные подписки (включая пользователей с Silver/Gold тарифами)
        active_subscriptions_from_table = session_db.query(Subscription).filter(
            Subscription.status == 'active'
        ).count()
        
        # Пользователи с платными тарифами (могут не иметь записи в Subscription)
        users_with_paid_tiers = session_db.query(User).filter(
            or_(
                User.subscription_tier == SubscriptionTier.SILVER,
                User.subscription_tier == SubscriptionTier.GOLD
            )
        ).count()
        
        # Общее количество активных подписок
        active_subscriptions = max(active_subscriptions_from_table, users_with_paid_tiers)
        
        # Количество пользователей по тарифам
        tier_stats = session_db.query(
            User.subscription_tier,
            func.count(User.id).label('count')
        ).group_by(User.subscription_tier).all()
        
        tier_distribution = {}
        for tier, count in tier_stats:
            if tier:
                tier_distribution[tier.value] = count
            else:
                tier_distribution['bronze'] = count
        
        # Пользователи с заполненным профилем
        profiles_filled = session_db.query(UserProfile).filter(
            UserProfile.city.isnot(None),
            UserProfile.interests.isnot(None)
        ).count()
        
        # Количество задач
        total_tasks = session_db.query(Task).count()
        completed_tasks = session_db.query(Task).filter(Task.status == 'completed').count()
        
        # Количество взаимодействий (сообщений в чате)
        total_interactions = session_db.query(Interaction).count()
        
        # Пользователи онлайн (с активностью за последние 15 минут)
        last_15min = datetime.now() - timedelta(minutes=15)
        users_online = session_db.query(User.id).join(Interaction).filter(
            Interaction.created_at >= last_15min
        ).distinct().count()
        
        # Последние зарегистрированные пользователи
        recent_users = session_db.query(User).order_by(User.created_at.desc()).limit(10).all()
        recent_users_list = [{
            'id': u.id,
            'username': u.username,
            'first_name': u.first_name,
            'created_at': u.created_at.strftime('%Y-%m-%d %H:%M:%S') if u.created_at else 'N/A',
            'tier': u.subscription_tier.value if u.subscription_tier else 'bronze'
        } for u in recent_users]
        
        # Активность пользователей (топ-10 по количеству взаимодействий)
        top_active_users = session_db.query(
            User.username,
            User.first_name,
            func.count(Interaction.id).label('interaction_count')
        ).join(Interaction, User.id == Interaction.user_id)\
         .group_by(User.id, User.username, User.first_name)\
         .order_by(func.count(Interaction.id).desc())\
         .limit(10).all()
        
        active_users_list = [{
            'username': u[0],
            'first_name': u[1],
            'interactions': u[2]
        } for u in top_active_users]
        
        user_stats = {
            'total_users': total_users,
            'users_online': users_online,
            'new_users_24h': new_users_24h,
            'new_users_week': new_users_week,
            'active_subscriptions': active_subscriptions,
            'tier_distribution': tier_distribution,
            'profiles_filled': profiles_filled,
            'profile_completion_rate': round((profiles_filled / total_users * 100) if total_users > 0 else 0, 1),
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'task_completion_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 1),
            'total_interactions': total_interactions,
            'recent_users': recent_users_list,
            'top_active_users': active_users_list
        }
        
        stats['user_stats'] = user_stats
        
        # Debug logging
        logger.info(f"User statistics collected: total_users={total_users}, users_online={users_online}, new_24h={new_users_24h}")
        
    except Exception as e:
        logger.error(f"Error getting user statistics: {e}")
        import traceback
        logger.error(traceback.format_exc())
        stats['user_stats'] = {
            'total_users': 0,
            'users_online': 0,
            'new_users_24h': 0,
            'new_users_week': 0,
            'active_subscriptions': 0,
            'tier_distribution': {},
            'profiles_filled': 0,
            'profile_completion_rate': 0,
            'total_tasks': 0,
            'completed_tasks': 0,
            'task_completion_rate': 0,
            'total_interactions': 0,
            'recent_users': [],
            'top_active_users': [],
            'error': str(e)
        }
    finally:
        session_db.close()
    
    # Return JSON or HTML based on accept header
    if 'text/html' in request.headers.get('Accept', ''):
        logger.info(f"Rendering template with stats: {stats.get('user_stats', {}).get('total_users', 'N/A')} users")
        return aiohttp_jinja2.render_template('security_dashboard.html', request, {
            'stats': stats,
            'current_time': datetime.now().isoformat()
        })
    
    return web.json_response(stats)


async def check_sportfan3_handler(request):
    """Check and fix @sportfan3 subscription"""
    # Check admin secret
    secret = request.query.get('secret')
    if secret != ADMIN_SECRET:
        return web.json_response({'error': 'Unauthorized'}, status=403)

    session_db = Session()
    try:
        logger.info("=== Проверка подписки @sportfan3 ===")

        # Найдем пользователя
        user = session_db.query(User).filter(User.username == 'sportfan3').first()
        if not user:
            return web.json_response({'error': 'User sportfan3 not found'}, status=404)

        result = {
            'user_id': user.id,
            'username': user.username,
            'current_tier': user.subscription_tier.value if user.subscription_tier else None
        }

        # Проверим активные подписки
        subscriptions = session_db.query(Subscription).filter(
            Subscription.user_id == user.id,
            Subscription.active == True
        ).all()
        result['active_subscriptions'] = len(subscriptions)
        result['subscriptions'] = []
        for sub in subscriptions:
            result['subscriptions'].append({
                'id': sub.id,
                'tier': sub.tier.value if sub.tier else None,
                'active': sub.active,
                'start_date': sub.start_date.isoformat() if sub.start_date else None,
                'end_date': sub.end_date.isoformat() if sub.end_date else None
            })

        # Проверим payment_history
        payments = session_db.query(PaymentHistory).filter(
            PaymentHistory.user_id == user.id
        ).order_by(PaymentHistory.created_at.desc()).all()
        result['payment_history_count'] = len(payments)
        result['payments'] = []
        for payment in payments:
            result['payments'].append({
                'id': payment.id,
                'tier': payment.tier,
                'action': payment.action,
                'start_date': payment.start_date.isoformat() if payment.start_date else None,
                'end_date': payment.end_date.isoformat() if payment.end_date else None,
                'created_at': payment.created_at.isoformat() if payment.created_at else None
            })

        # Проверим нужно ли восстановление
        now = datetime.now(dt_timezone.utc)
        has_active_gold = any(
            p.tier == 'gold' and p.end_date and p.end_date > now 
            for p in payments if p.action in ['subscription_activated', 'subscription_upgraded']
        )

        result['has_active_gold_payment'] = has_active_gold
        result['needs_fix'] = has_active_gold and user.subscription_tier != SubscriptionTier.GOLD

        if result['needs_fix']:
            logger.info(f"❌ НАЙДЕНА ПРОБЛЕМА: Пользователь должен иметь GOLD, но имеет {user.subscription_tier}")
            # Восстанавливаем подписку В ОБЕИХ ТАБЛИЦАХ
            user.subscription_tier = SubscriptionTier.GOLD
            
            # Обновляем subscriptions.tier тоже
            subscription = session_db.query(Subscription).filter_by(user_id=user.id, status='active').first()
            if subscription:
                subscription.tier = SubscriptionTier.GOLD
            
            session_db.commit()
            result['fixed'] = True
            result['new_tier'] = 'gold'
            logger.info("✅ Подписка восстановлена в users и subscriptions!")
        else:
            result['fixed'] = False

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error checking sportfan3 subscription: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


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
    if not LOCAL:
        dp.include_router(handlers_router)
    if not LOCAL:
        app.router.add_post('/webhook', SimpleRequestHandler(dp, bot))

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
async def security_middleware(request, handler):
    """Security monitoring and rate limiting"""
    remote = request.remote or 'unknown'
    
    # Check if IP is blocked
    if security_monitor.is_blocked(remote):
        logger.warning(f"Blocked IP attempted access: {remote}")
        return web.json_response(
            {'error': 'Too many requests. Your IP has been temporarily blocked.'},
            status=429
        )
    
    # Check rate limits
    is_limited, reason = security_monitor.is_rate_limited(remote)
    if is_limited:
        logger.warning(f"Rate limit exceeded for {remote}: {reason}")
        return web.json_response(
            {'error': f'Rate limit exceeded: {reason}'},
            status=429
        )
    
    # Get user_id from session if available
    user_id = None
    try:
        session = await get_session(request)
        user_id = session.get('user_id')
    except:
        pass
    
    # Log request
    user_agent = request.headers.get('User-Agent', 'unknown')
    security_monitor.log_request(
        ip=remote,
        user_agent=user_agent,
        path=request.path,
        method=request.method,
        user_id=user_id
    )
    
    # Process request
    try:
        response = await handler(request)
        # Log response status
        security_monitor.request_logs[-1].status_code = response.status
        return response
    except Exception as e:
        # Log error
        security_monitor.request_logs[-1].status_code = 500
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

app.middlewares.append(security_middleware)
app.middlewares.append(redirect_to_root_middleware)
app.middlewares.append(session_error_middleware)
app.middlewares.append(logging_middleware)
app.middlewares.append(csp_middleware)

aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader('templates'))


async def yookassa_webhook(request):
    data = await request.json()
    if data.get('event') == 'payment.succeeded':
        payment = data['object']
        user_id = payment['metadata']['user_id']
        tier = payment['metadata'].get('tier', 'bronze')  # Get tier from payment metadata

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

            # Update tier
            tier_enum = SubscriptionTier.BRONZE
            if tier == 'bronze':
                subscription.tier = SubscriptionTier.BRONZE
                user.subscription_tier = SubscriptionTier.BRONZE
                tier_enum = SubscriptionTier.BRONZE
            elif tier == 'silver':
                subscription.tier = SubscriptionTier.SILVER
                user.subscription_tier = SubscriptionTier.SILVER
                tier_enum = SubscriptionTier.SILVER
            elif tier == 'gold':
                subscription.tier = SubscriptionTier.GOLD
                user.subscription_tier = SubscriptionTier.GOLD
                tier_enum = SubscriptionTier.GOLD

            # Если подписка еще активна, продлеваем от end_date, иначе от текущей даты
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
                    details=json.dumps({'payment_method': payment.get('payment_method', {}).get('type'), 'status': payment.get('status')})
                )
                session.add(payment_history)
                session.commit()
                logger.info(f"💾 Payment logged to history: user={user.username}, tier={tier}, payment_id={payment['id']}")
            except Exception as e:
                logger.error(f"❌ Failed to log payment to history: {e}")
                # Не падаем, платеж уже обработан

            from payments import get_tier_name
            tier_name = get_tier_name(tier)
            await bot.send_message(int(user_id), f"Подписка {tier_name} активирована! Теперь у вас доступ ко всем премиум-функциям.")
        session.close()
    return web.Response(text="OK")


async def get_user_id_from_request(request):
    """Helper function to get user_id from session or query parameters"""
    session_req = await get_session(request)
    user_id = session_req.get('user_id')
    
    # Check for telegram_id in query parameters (for local testing)
    if not user_id:
        telegram_id_param = request.query.get('telegram_id')
        if telegram_id_param:
            try:
                user_id = int(telegram_id_param)
                logger.info(f"Set user_id from query parameter: {user_id}")
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
                partners = get_partners_list(user_id=user.id)  # Передаем user.id (базовый ID), а не telegram_id
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

            # Получить контакты по делегированию
            delegating_to_me = []  # Люди, которые делегировали мне задачи
            delegating_by_me = []  # Люди, которым я делегировал задачи

            try:
                # Люди, которые делегировали мне задачи (я получаю задачи от них)
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
                                'reason': f'я делегировал {len(task_titles)} {pluralize_task(len(task_titles))}'
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

            # Получаем список контактов, с которыми уже общались
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
                    reasons.append('общие навыки')
                if p.common_interests:
                    reasons.append('общие интересы')
                if p.common_goals:
                    reasons.append('общие цели')
                if p.city and profile.city and p.city.lower() == profile.city.lower():
                    reasons.append('из вашего города')
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
                                    # Если совпадает >= 2 слов
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

                # Check tier access - use user.subscription_tier for now since update script uses it
                user_tier = user.subscription_tier if user and hasattr(user, 'subscription_tier') and user.subscription_tier else SubscriptionTier.BRONZE
                partner_tier = partner_user.subscription_tier if partner_user and hasattr(partner_user, 'subscription_tier') and partner_user.subscription_tier else SubscriptionTier.BRONZE

                # Ensure tiers are proper enum values
                if not hasattr(user_tier, 'value'):
                    user_tier = SubscriptionTier.BRONZE
                if not hasattr(partner_tier, 'value'):
                    partner_tier = SubscriptionTier.BRONZE

                # Convert to string for comparison if needed
                user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
                partner_tier_str = partner_tier.value if hasattr(partner_tier, 'value') else str(partner_tier).lower()

                logger.info(f"User {user.username} (id:{user.telegram_id}) has tier {user_tier} ({user_tier_str}), partner {partner_user.username if partner_user else 'unknown'} has tier {partner_tier} ({partner_tier_str})")

                # Determine if user can access this contact
                # Bronze и Silver видят друг друга (Bronze видит Bronze+Silver, Silver видит Bronze+Silver)
                # Gold видит всех (Bronze, Silver, Gold)
                can_access = False
                required_tier = None

                if user_tier_str.lower() in ['bronze', 'silver']:
                    # Bronze и Silver видят только Bronze и Silver контакты
                    can_access = (partner_tier_str.lower() in ['bronze', 'silver'])
                    logger.info(f"User {user_tier_str} checking partner {partner_tier_str}: can_access = {can_access}")
                    if not can_access:
                        required_tier = 'gold'
                elif user_tier_str.lower() == 'gold':
                    # Gold видит всех
                    can_access = True
                    logger.info(f"User {user_tier_str} can access all partners")

                # Add ALL contacts, including those user cannot access yet (for "Premium status" filter)
                if partner_user:
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
                            'required_tier': required_tier,
                            'subscription_tier': (partner_tier.value if partner_tier and hasattr(partner_tier, 'value') else 'bronze').lower(),
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
                
            # Получить профиль делегатора для расчета общих интересов/навыков/целей
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
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegator.telegram_id)
                    if updated_avatar and updated_avatar != delegator.photo_url:
                        delegator.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegator avatar for {delegator.telegram_id}: {e}")

            # Для контактов "Делегирует мне" НЕ применяем фильтр по тарифу
            # Делегированные задачи должны всегда отображаться независимо от тарифа делегатора
            delegator_tier = delegator.subscription_tier if delegator and delegator.subscription_tier else SubscriptionTier.BRONZE
            
            # Ensure tier is proper enum value
            if not hasattr(delegator_tier, 'value'):
                delegator_tier = SubscriptionTier.BRONZE
            
            delegator_tier_str = delegator_tier.value if hasattr(delegator_tier, 'value') else str(delegator_tier).lower()
            
            logger.info(f"Adding delegating contact {contact['username']} with tier {delegator_tier_str} for user {user.username} (no tier restrictions)")
            delegator_profile = session_db.query(UserProfile).filter_by(user_id=delegator.id).first() if delegator else None
            partners_data.append({
                'contact_info': contact['username'],
                'telegram_id': delegator.telegram_id if delegator else None,
                'can_access': True,  # Всегда доступен
                'required_tier': None,  # Нет ограничений
                'subscription_tier': delegator_tier.value if delegator_tier else 'bronze',
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
            
            # Получить профиль делегата для расчета общих интересов/навыков/целей
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
                    updated_avatar = await get_user_avatar_url(request.app['bot'], delegatee.telegram_id)
                    if updated_avatar and updated_avatar != delegatee.photo_url:
                        delegatee.photo_url = updated_avatar
                        session_db.commit()
                        photo_url = updated_avatar
                except Exception as e:
                    logger.error(f"Error updating delegatee avatar for {delegatee.telegram_id}: {e}")

            # Check tier access
            user_tier = user.subscription_tier if user else SubscriptionTier.BRONZE
            delegatee_tier = delegatee.subscription_tier if delegatee and delegatee.subscription_tier else SubscriptionTier.BRONZE

            # Ensure tiers are proper enum values
            if not hasattr(user_tier, 'value'):
                user_tier = SubscriptionTier.BRONZE
            if not hasattr(delegatee_tier, 'value'):
                delegatee_tier = SubscriptionTier.BRONZE

            # Convert to string for comparison
            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
            delegatee_tier_str = delegatee_tier.value if hasattr(delegatee_tier, 'value') else str(delegatee_tier).lower()

            can_access = False
            required_tier = None

            if user_tier_str.lower() in ['bronze', 'silver']:
                # Bronze и Silver видят только Bronze и Silver контакты
                can_access = (delegatee_tier_str.lower() in ['bronze', 'silver'])
                logger.info(f"Delegatee check: User {user_tier_str} checking delegatee {delegatee_tier_str}: can_access = {can_access}")
                if not can_access:
                    required_tier = 'gold'
            elif user_tier_str.lower() == 'gold':
                can_access = True
                logger.info(f"Delegatee check: User {user_tier_str} can access all delegatees")

            # Only add contact if user can access it
            if can_access:
                logger.info(f"Adding delegating_by_me contact {contact['username']} with tier {delegatee_tier_str} for user {user.username} with tier {user_tier_str}")
                delegatee_profile = session_db.query(UserProfile).filter_by(user_id=delegatee.id).first() if delegatee else None
                partners_data.append({
                    'contact_info': contact['username'] if can_access else None,
                    'telegram_id': delegatee.telegram_id if delegatee else None,
                    'can_access': can_access,
                    'required_tier': required_tier,
                    'subscription_tier': delegatee_tier.value if delegatee_tier else 'bronze',
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

        # Сортируем partners_data: сначала по городу (совпадение с пользователем), потом по рейтингу
        user_city = profile.city.lower() if profile and profile.city else None

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

        # Add favorite contacts
        if profile and profile.favorite_contacts:
            try:
                favorite_data = json.loads(profile.favorite_contacts)
                for item in favorite_data:
                    favorite_username = None
                    # Определить username по ID или использовать напрямую
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
                            user_tier = user.subscription_tier if user else SubscriptionTier.BRONZE
                            favorite_tier = favorite_user.subscription_tier if favorite_user.subscription_tier else SubscriptionTier.BRONZE

                            # Ensure tiers are proper enum values
                            if not hasattr(user_tier, 'value'):
                                user_tier = SubscriptionTier.BRONZE
                            if not hasattr(favorite_tier, 'value'):
                                favorite_tier = SubscriptionTier.BRONZE

                            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
                            favorite_tier_str = favorite_tier.value if hasattr(favorite_tier, 'value') else str(favorite_tier).lower()

                            # Избранные контакты всегда доступны независимо от тарифа
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
                                'subscription_tier': favorite_tier.value if favorite_tier else 'bronze',
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
                                'reason': 'избранный контакт',
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
        blocked_partners_data = []  # Сохраняем информацию о заблокированных
        for partner in partners_data:
            partner_username = (partner.get('contact_info') or '').replace('@', '')
            if partner_username in blocked_by_me or partner_username in blocked_me:
                blocked_partners_data.append(partner)  # Сохраняем заблокированные
                continue  # Skip blocked contacts from main list
            filtered_partners_data.append(partner)

        partners_data = filtered_partners_data
        partners_data.sort(key=sort_key)

        # Добавить флаг is_favorite для всех контактов
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

        # Установить флаг is_favorite для всех контактов
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
        # На случай ранних ошибок закрываем сессию, если она еще открыта
        try:
            if 'session_db' in locals():
                session_db.close()
        except Exception:
            pass


async def api_elite_partners_handler(request):
    """Get ALL Gold partners for Gold users (Premium status filter)"""
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
        logger.info(f"API elite partners handler called for user_id: {user_id}")
        if not user_id:
            return web.json_response({'error': 'Not logged in'}, status=401)

        session_db = Session()
        try:
            user = session_db.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.warning(f"User not found for telegram_id: {user_id}")
                return web.json_response({'error': 'User not found'}, status=404)

            # Check if user has Gold tier
            user_tier = user.subscription_tier if user and hasattr(user, 'subscription_tier') else SubscriptionTier.BRONZE
            user_tier_str = user_tier.value if hasattr(user_tier, 'value') else str(user_tier).lower()
            
            logger.info(f"User {user.username} has tier: {user_tier_str}")
            
            if user_tier_str.lower() != 'gold':
                # Only Gold users can access elite partners
                logger.info(f"User {user.username} does not have Gold tier, returning empty partners list")
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

            # Get all Gold users (except self)
            gold_users = session_db.query(User).filter(
                User.subscription_tier == SubscriptionTier.GOLD,
                User.id != user.id
            ).all()
            
            logger.info(f"Found {len(gold_users)} other Gold users for user {user.username}")

            partners_data = []
            for gold_user in gold_users:
                # Skip hidden and blocked contacts
                username_clean = gold_user.username.replace('@', '').lower() if gold_user.username else ''
                if username_clean in hidden_contacts or gold_user.username in blocked_by_me:
                    logger.info(f"Skipping Gold user {gold_user.username} - hidden or blocked")
                    continue

                gold_profile = session_db.query(UserProfile).filter_by(user_id=gold_user.id).first()
                
                logger.info(f"Adding Gold user to elite partners: {gold_user.username}")

                # Update avatar from Telegram if available
                photo_url = gold_user.photo_url if gold_user.photo_url else None
                if gold_user.telegram_id and 'bot' in request.app:
                    try:
                        updated_avatar = await get_user_avatar_url(request.app['bot'], gold_user.telegram_id)
                        if updated_avatar and updated_avatar != gold_user.photo_url:
                            gold_user.photo_url = updated_avatar
                            session_db.commit()
                            photo_url = updated_avatar
                    except Exception as e:
                        logger.error(f"Error updating Gold user avatar for {gold_user.telegram_id}: {e}")

                # Calculate common interests/skills/goals/tasks
                common_interests = None
                common_skills = None
                common_goals = None
                common_tasks = None

                if gold_profile:
                    # Common interests
                    if gold_profile.interests and user_profile.interests:
                        user_interests = set(i.strip().lower() for i in user_profile.interests.split(','))
                        gold_interests = set(i.strip().lower() for i in gold_profile.interests.split(','))
                        common = user_interests & gold_interests
                        common_interests = ', '.join(common) if common else None

                    # Common skills
                    if gold_profile.skills and user_profile.skills:
                        user_skills = set(s.strip().lower() for s in user_profile.skills.split(','))
                        gold_skills = set(s.strip().lower() for s in gold_profile.skills.split(','))
                        common_sk = user_skills & gold_skills
                        common_skills = ', '.join(common_sk) if common_sk else None

                    # Common goals
                    if gold_profile.goals and user_profile.goals:
                        user_goals = set(g.strip().lower() for g in user_profile.goals.split(','))
                        gold_goals = set(g.strip().lower() for g in gold_profile.goals.split(','))
                        common_g = user_goals & gold_goals
                        common_goals = ', '.join(common_g) if common_g else None

                    # Common tasks
                    user_tasks = session_db.query(Task).filter_by(user_id=user.id).all()
                    gold_tasks = session_db.query(Task).filter_by(user_id=gold_user.id).all()
                    
                    user_task_titles = set(t.title.lower().strip() for t in user_tasks if t.title)
                    gold_task_titles = set(t.title.lower().strip() for t in gold_tasks if t.title)
                    
                    common_task_titles = user_task_titles & gold_task_titles
                    if not common_task_titles:
                        partial_matches = set()
                        for user_task in user_task_titles:
                            user_words = set(user_task.split())
                            if len(user_words) < 2:
                                continue
                            for gold_task in gold_task_titles:
                                gold_words = set(gold_task.split())
                                common_words = user_words & gold_words
                                if len(common_words) >= 2:
                                    partial_matches.add(user_task)
                        if partial_matches:
                            common_task_titles = partial_matches
                    
                    common_tasks = ', '.join(list(common_task_titles)[:5]) if common_task_titles else None

                partners_data.append({
                    'contact_info': gold_user.username if gold_user.username else None,
                    'telegram_id': gold_user.telegram_id,
                    'photo_url': photo_url,
                    'can_access': True,  # Gold users can access all Gold users
                    'required_tier': None,
                    'subscription_tier': 'gold',
                    'first_name': gold_user.first_name,
                    'city': gold_profile.city if gold_profile else None,
                    'company': gold_profile.company if gold_profile else None,
                    'position': gold_profile.position if gold_profile else None,
                    'interests': gold_profile.interests if gold_profile else None,
                    'skills': gold_profile.skills if gold_profile else None,
                    'goals': gold_profile.goals if gold_profile else None,
                    'common_interests': common_interests,
                    'common_skills': common_skills,
                    'common_goals': common_goals,
                    'common_tasks': common_tasks,
                    'average_rating': gold_profile.average_rating if gold_profile else 0,
                    'rating_count': gold_profile.rating_count if gold_profile else 0,
                    'type': 'elite'
                })

            # Add delegation contacts for Gold users
            delegating_to_me = []
            delegating_by_me = []
            
            try:
                # Люди, которые делегировали задачи мне (accepted delegation)
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
                                    'subscription_tier': delegator.subscription_tier.value if delegator.subscription_tier else 'bronze',
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
                                    'reason': f'делегировал {len(task_titles)} {pluralize_task(len(task_titles))}',
                                    'type': 'delegation'
                                })

                # Люди, которым я делегировал задачи
                my_delegated_tasks = session_db.query(Task).filter(
                    Task.user_id == user.id,
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
                                'subscription_tier': delegatee.subscription_tier.value if delegatee.subscription_tier else 'bronze',
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
                                'reason': f'я делегировал {len(task_titles)} {pluralize_task(len(task_titles))}',
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

            logger.info(f"Returning {len(partners_data)} elite (Gold) partners for user {user_id}")
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
                    'subscription_tier': contact_user.subscription_tier.value if hasattr(contact_user, 'subscription_tier') and contact_user.subscription_tier else 'bronze'
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
                    'subscription_tier': 'bronze'
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
                                        message = f"@{user.username} не готов принимать задачи от вас. Ваши делегированные задачи были отклонены."
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
            success_message = f'Оценка {rating}/10 для @{rated_username} сохранена'

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

            # Update task status to accepted
            task.status = 'accepted'
            task.delegation_status = 'accepted'
            task.updated_at = datetime.now(pytz.UTC)

            # Create interaction record
            interaction = Interaction(
                user_id=user.id,
                message_type='ai',
                content=f'Задача "{task.title}" принята'
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
        bio = data.get('bio')
        skills = data.get('skills')
        interests = data.get('interests')
        goals = data.get('goals')
        languages = data.get('languages')
        timezone = data.get('timezone')

        session_db = Session()
        try:
            # Import the update_profile function
            from ai_integration.handlers import update_profile

            # Call the update_profile function
            update_profile(
                city=city,
                company=company,
                position=position,
                bio=bio,
                skills=skills,
                interests=interests,
                goals=goals,
                languages=languages,
                timezone=timezone,
                user_id=user_id,
                session=session_db
            )

            return web.json_response({
                'success': True,
                'message': 'Профиль обновлен'
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
                            # Это username - найти user_id
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
                    'subscription_tier': u.subscription_tier.value if u.subscription_tier else 'BRONZE'
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
                            'subscription_tier': author.get('subscription_tier', 'BRONZE'),
                            'is_current_user': post.user_id == user.id
                        }
                    })
                except Exception as post_error:
                    logger.error(f"Error processing post {post.id}: {post_error}")
                    continue

            return web.json_response({'success': True, 'posts': feed})

        finally:
            session_db.close()

    except Exception as e:
        logger.error(f"Error getting feed: {e}")
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
        post_id = comment.post_id
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
    storage = SimpleCookieStorage()
    logger.info("Using SimpleCookieStorage for sessions")

    # Setup session middleware
    aiohttp_session.setup(app, storage)
    logger.info("Session middleware set up")
    
    # Синхронизируем users.subscription_tier с subscriptions.tier при старте
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
            
            # Проверяем, не истекла ли подписка
            now = datetime.now(pytz.UTC)
            if sub.end_date and sub.end_date.tzinfo is None:
                sub.end_date = sub.end_date.replace(tzinfo=pytz.UTC)
            
            if sub.end_date and sub.end_date < now:
                continue
            
            # Синхронизируем тарифы
            user_tier_str = str(user.subscription_tier).split('.')[-1] if user.subscription_tier else None
            sub_tier_str = str(sub.tier).split('.')[-1] if sub.tier else None
            
            if user_tier_str != sub_tier_str:
                logger.info(f"Syncing tier for @{user.username}: users.{user_tier_str} -> subscriptions.{sub_tier_str}")
                user.subscription_tier = sub.tier
                synced_count += 1
        
        if synced_count > 0:
            session_db.commit()
            logger.info(f"✅ Synced {synced_count} user tiers with subscriptions on startup")
        
        # Синхронизируем users.average_rating с user_profiles.average_rating
        all_profiles = session_db.query(UserProfile).all()
        rating_synced_count = 0
        
        for profile in all_profiles:
            user = session_db.query(User).filter_by(id=profile.user_id).first()
            if not user:
                continue
            
            # Синхронизируем рейтинг
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


async def on_shutdown(app):
    """Cleanup on application shutdown"""
    # Set webhook - используем Railway subdomain т.к. Telegram требует HTTPS
    if bot and not LOCAL:
        # Get webhook URL from environment variable or construct from Railway variables
        webhook_url = os.getenv('WEBHOOK_URL')
        if not webhook_url:
            # Try to construct from Railway environment variables
            railway_project_id = os.getenv('RAILWAY_PROJECT_ID')
            if railway_project_id:
                webhook_url = f"https://{railway_project_id}.up.railway.app/webhook"
            else:
                # Fallback to hardcoded but log warning
                webhook_url = "https://task-production-1d10.up.railway.app/webhook"
                logger.warning("WEBHOOK_URL not set and RAILWAY_PROJECT_ID not found, using hardcoded URL")

        try:
            await bot.set_webhook(webhook_url)
            logger.info(f"Webhook set to: {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to set webhook to {webhook_url}: {e}")
            logger.warning("Continuing without webhook setup - bot may not receive updates")
    else:
        logger.warning("Bot not created or local mode, skipping webhook setup")

    # Redis was removed from the project - using in-memory cache only
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
        query_conditions = [Task.user_id == user.id]
        if user.username:
            # Compare without @ symbol to handle both @username and username formats
            username_clean = user.username.replace('@', '')
            query_conditions.append(or_(
                Task.delegated_to_username.ilike(username_clean),
                Task.delegated_to_username.ilike(f'@{username_clean}')
            ))
        
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
                title = re.sub(r' - [Дд]елегирована (от|на) @\w+$', '', title)

                # Check if task is delegated TO me or BY me
                if user.username and (task.delegated_to_username.lower() == user.username.lower(
                ) or task.delegated_to_username.lower() == f"@{user.username.lower()}"):
                    # Task delegated TO me
                    creator = session_db.query(User).filter_by(id=task.user_id).first()
                    if creator:
                        title = f"{title} - Делегирована от @{creator.username}"
                elif task.user_id == user.id:
                    # Task delegated BY me to someone else
                    title = f"{title} - Делегирована на @{delegated_username}"

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
                'delegated_by': None  # Будет установлено ниже если задача делегирована мне
            }
            
            # Определяем delegated_by если задача делегирована мне
            if task.delegated_to_username and user.username:
                if task.delegated_to_username.lower().strip('@') == user.username.lower():
                    creator = session_db.query(User).filter_by(id=task.user_id).first()
                    if creator and creator.username:
                        task_data['delegated_by'] = creator.username
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

        interactions = session_db.query(Interaction).filter_by(
            user_id=user.id).order_by(
            Interaction.created_at.asc()).all()
        
        logger.info(f"Found {len(interactions)} total interactions for user {user.id}")

        # Get history cleared timestamp from DB
        history_cleared_timestamp = 0
        if user.history_cleared_at:
            history_cleared_timestamp = user.history_cleared_at.timestamp()

        # Filter interactions based on cleared timestamp
        filtered_interactions = [
            i for i in interactions
            if i.created_at.replace(tzinfo=dt_timezone.utc).timestamp() > history_cleared_timestamp
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
            'first_name': user.first_name
        }

        return web.json_response(response_data)
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        return web.json_response({'error': str(e)}, status=500)
    finally:
        session_db.close()


async def extend_subscription_handler(request):
    """Перенаправление на страницу выбора тарифа"""
    return web.HTTPFound('/subscription_tiers')


@aiohttp_jinja2.template('subscription_tiers.html')
async def subscription_tiers_handler(request):
    """Страница выбора тарифа подписки"""
    return {}


async def apply_promo_code_handler(request):
    """Применяет промокод и активирует подписку"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    if not user_id:
        return web.json_response({'success': False, 'message': 'Не авторизован'}, status=401)

    data = await request.post()
    promo_code = data.get('promo_code', '').strip().upper()

    if not promo_code:
        return web.json_response({'success': False, 'message': 'Введите промокод'})

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return web.json_response({'success': False, 'message': 'Пользователь не найден'})

        # Проверяем промокод
        promo = session.query(PromoCode).filter_by(code=promo_code).first()
        if not promo:
            return web.json_response({'success': False, 'message': 'Неверный промокод'})

        # Проверяем срок действия - приводим обе даты к одному формату
        now = datetime.now(dt_timezone.utc)
        expires_at = promo.expires_at.replace(tzinfo=dt_timezone.utc) if promo.expires_at.tzinfo is None else promo.expires_at
        if expires_at < now:
            return web.json_response({'success': False, 'message': 'Срок действия промокода истек'})

        # Проверяем лимит использований
        if promo.max_uses is not None and promo.used_count >= promo.max_uses:
            return web.json_response({'success': False, 'message': 'Промокод достиг лимита использований'})

        # Проверяем, использовал ли уже этот пользователь этот промокод
        import json
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id in used_by_users:
            return web.json_response({'success': False, 'message': 'Вы уже использовали этот промокод'})

        # Активируем подписку
        start_date = now
        end_date = start_date + timedelta(days=promo.duration_days)

        # Ищем существующую подписку или создаем новую
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

        # Обновляем user.subscription_tier для синхронизации
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

        # Устаревшие поля для совместимости
        promo.used_by_user_id = user.id
        promo.used_at = now

        session.commit()
        logger.info(f"Promo code {promo_code} activated for user {user.id}, subscription created/updated with tier {subscription.tier}")

        # Сохраняем значения до закрытия сессии
        tier_name = promo.tier.value if hasattr(promo.tier, 'value') else str(promo.tier)
        duration = promo.duration_days
        end_date_str = end_date.strftime("%d.%m.%Y")

        return web.json_response({
            'success': True,
            'message': f'Промокод активирован! Подписка {tier_name} на {duration} дней до {end_date_str}'
        })

    except Exception as e:
        logger.error(f"Error applying promo code: {e}", exc_info=True)
        session.rollback()
        return web.json_response({'success': False, 'message': f'Ошибка активации промокода: {str(e)}'}, status=500)
    finally:
        session.close()


async def create_payment_handler(request):
    """Создает платеж для выбранного тарифа"""
    session_obj = await get_session(request)
    user_id = session_obj.get('user_id')

    logger.info(f"Create payment handler called with user_id: {user_id}")

    if not user_id:
        logger.warning("No user_id in session, redirecting to login")
        return web.HTTPFound('/')

    tier = request.query.get('tier', 'bronze')
    logger.info(f"Creating payment for tier: {tier}")

    if tier not in ['bronze', 'silver', 'gold']:
        tier = 'bronze'

    try:
        from payments import create_payment, get_tier_price, get_tier_name

        amount = get_tier_price(tier)
        tier_name = get_tier_name(tier)

        logger.info(f"Creating payment: amount={amount}, tier={tier}, user_id={user_id}")

        payment_url = create_payment(
            amount=str(amount),
            description=f"Подписка ASI Biont - {tier_name} на 30 дней",
            user_id=user_id,
            tier=tier
        )

        logger.info(f"Payment URL created: {payment_url}")
        return web.HTTPFound(payment_url)
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
        return web.Response(text=f'Ошибка создания платежа: {str(e)}', status=500)


async def setup_production_feed_handler(request):
    """Временный админ-эндпоинт для настройки production ленты"""
    try:
        import json
        from datetime import timezone
        
        session = Session()
        results = []
        
        # Находим пользователей
        aleksandr = session.query(User).filter_by(username='aleksandrinsider').first()
        test_sport = session.query(User).filter_by(username='test_sport_10').first()
        
        if not aleksandr:
            return web.json_response({'error': 'aleksandrinsider не найден'}, status=404)
        
        if not test_sport:
            return web.json_response({'error': 'test_sport_10 не найден'}, status=404)
        
        results.append(f"aleksandrinsider ID: {aleksandr.id}")
        results.append(f"test_sport_10 ID: {test_sport.id}")
        
        # Получаем/создаем профиль aleksandrinsider
        profile = session.query(UserProfile).filter_by(user_id=aleksandr.id).first()
        if not profile:
            profile = UserProfile(user_id=aleksandr.id)
            session.add(profile)
            session.flush()
        
        # Добавляем test_sport_10 в избранное
        current_favorites = json.loads(profile.favorite_contacts or '[]')
        if 'test_sport_10' not in current_favorites:
            current_favorites.append('test_sport_10')
            profile.favorite_contacts = json.dumps(current_favorites)
            session.commit()
            results.append("test_sport_10 добавлен в избранное aleksandrinsider")
        else:
            results.append("test_sport_10 уже в избранном")
        
        # Проверяем существующие посты
        existing_posts = session.query(Post).filter_by(user_id=test_sport.id).count()
        results.append(f"Текущее количество постов test_sport_10: {existing_posts}")
        
        # Создаем посты если их меньше 9
        posts_to_create = [
            '🎯 Сегодня пробежал личный рекорд - 10км за 45 минут! Чувствую себя отлично, тренировки дают результат. #бег #спорт #здоровье',
            '☕️ Утренняя пробежка + кофе = идеальное начало дня. Кто со мной на завтра в 7:00? #утро #пробежка #мотивация'
        ]
        
        created_count = 0
        for content in posts_to_create:
            if existing_posts + created_count >= 9:
                break
            
            new_post = Post(
                user_id=test_sport.id,
                content=content,
                created_at=datetime.now(timezone.utc)
            )
            session.add(new_post)
            created_count += 1
        
        if created_count > 0:
            session.commit()
            results.append(f"Создано {created_count} постов от test_sport_10")
        else:
            results.append("Посты не созданы (уже достаточно)")
        
        # Итоговая статистика
        total_posts = session.query(Post).filter_by(user_id=test_sport.id).count()
        results.append(f"Итого постов test_sport_10: {total_posts}")
        results.append(f"Избранные aleksandrinsider: {profile.favorite_contacts}")
        
        session.close()
        return web.json_response({'success': True, 'results': results})
        
    except Exception as e:
        logger.error(f"Error in setup_production_feed_handler: {e}", exc_info=True)
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
app.router.add_post('/get_task_advice', get_task_advice_handler)
app.router.add_post('/update_timezone', update_timezone_handler)
app.router.add_get('/extend_subscription', extend_subscription_handler)
app.router.add_get('/subscription_tiers', subscription_tiers_handler)
app.router.add_post('/apply_promo_code', apply_promo_code_handler)
app.router.add_get('/create_payment', create_payment_handler)
app.router.add_get('/clear_old_tasks', clear_old_tasks_handler)
app.router.add_get('/clear_database', clear_database_handler)
app.router.add_get('/admin/users', admin_users_handler)
app.router.add_get('/admin/security', admin_security_handler)
# app.router.add_get('/check_sportfan3', check_sportfan3_handler)  # Disabled - user deleted from production
app.router.add_get('/direct_login', direct_login_handler)
app.router.add_static('/static', 'static')
app.router.add_post('/webhook/yookassa', yookassa_webhook)
# API routes for dynamic updates
app.router.add_get('/api/tasks', api_tasks_handler)
app.router.add_get('/api/partners', api_partners_handler)
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
app.router.add_get('/api/feed', get_feed_handler)
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
app.router.add_get('/admin/setup_feed', setup_production_feed_handler)

# Setup for production
# dp = Dispatcher()

# Include router from handlers
# dp.include_router(handlers_router)

# Session storage will be initialized in on_startup handler

# Initialize ReminderService
reminder_service = ReminderService(bot=bot if not LOCAL else None)
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
app.on_shutdown.append(on_shutdown)

if bot:
    # webhook_requests_handler = SimpleRequestHandler(
    #     dispatcher=dp,
    #     bot=bot,
    # )
    # webhook_requests_handler.register(app, path="/webhook")
    # setup_application(app, dp, bot=bot)
    logger.info("Bot created, but webhook setup disabled for local mode")
else:
    logger.warning("Bot not created or local mode, skipping webhook setup")

logger.info("App created successfully")

if __name__ == "__main__":
    from config import LOCAL
    if LOCAL:  # Enabled web server for local testing
        logger.info("Running in local mode with web server only")
        # Production mode or local web mode: run web server
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
    else:
        # Production mode or local web mode: run web server
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
