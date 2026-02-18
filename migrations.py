"""
Database migrations — вынесены из main.py для чистоты кода.
Каждая миграция идемпотентна (проверяет существование перед добавлением).
"""
import logging
from sqlalchemy import text, inspect as sa_inspect
from models import Session, engine
from config import LOCAL

logger = logging.getLogger(__name__)


def _add_columns(session, table, columns_exist, migrations):
    """Добавляет несколько колонок в таблицу если их нет"""
    for col_name, sql in migrations.items():
        if col_name not in columns_exist:
            try:
                session.execute(text(sql))
                session.commit()
                logger.info(f"Migration: {col_name} added to {table}")
            except Exception as e:
                logger.error(f"Failed to add {col_name} to {table}: {e}")
                session.rollback()


def _migrate_user_profiles(session, inspector):
    """Миграции таблицы user_profiles"""
    if not inspector.has_table('user_profiles'):
        return
    cols = [col['name'] for col in inspector.get_columns('user_profiles')]

    _add_columns(session, 'user_profiles', cols, {
        'activity_streak': 'ALTER TABLE user_profiles ADD COLUMN activity_streak INTEGER DEFAULT 0',
        'bio': 'ALTER TABLE user_profiles ADD COLUMN bio TEXT',
        'birthdate': "ALTER TABLE user_profiles ADD COLUMN birthdate VARCHAR(10)",
        'interests': 'ALTER TABLE user_profiles ADD COLUMN interests TEXT',
        'city': 'ALTER TABLE user_profiles ADD COLUMN city VARCHAR(100)',
        'company': 'ALTER TABLE user_profiles ADD COLUMN company VARCHAR(200)',
        'position': 'ALTER TABLE user_profiles ADD COLUMN position VARCHAR(200)',
        'timezone': "ALTER TABLE user_profiles ADD COLUMN timezone VARCHAR(50) DEFAULT 'UTC'",
        'subscription_expires_at': 'ALTER TABLE user_profiles ADD COLUMN subscription_expires_at TIMESTAMP',
        'subscription_renewal_date': 'ALTER TABLE user_profiles ADD COLUMN subscription_renewal_date TIMESTAMP',
        'pending_premium_recommendations': 'ALTER TABLE user_profiles ADD COLUMN pending_premium_recommendations TEXT',
        'content_strategy': 'ALTER TABLE user_profiles ADD COLUMN content_strategy TEXT',
        'auto_marketing_enabled': 'ALTER TABLE user_profiles ADD COLUMN auto_marketing_enabled BOOLEAN DEFAULT TRUE',
        'auto_delegation_enabled': 'ALTER TABLE user_profiles ADD COLUMN auto_delegation_enabled BOOLEAN DEFAULT TRUE',
        'auto_post_time': "ALTER TABLE user_profiles ADD COLUMN auto_post_time VARCHAR(5) DEFAULT '12:00'",
    })

    # subscription_tier — особая обработка для PostgreSQL enum
    if 'subscription_tier' not in cols:
        if LOCAL:
            session.execute(text("ALTER TABLE user_profiles ADD COLUMN subscription_tier TEXT DEFAULT 'LIGHT'"))
        else:
            try:
                session.execute(text("CREATE TYPE subscription_tier_enum AS ENUM ('LIGHT', 'STANDARD', 'PREMIUM')"))
                session.commit()
            except Exception:
                session.rollback()
            session.execute(text("ALTER TABLE user_profiles ADD COLUMN subscription_tier subscription_tier_enum DEFAULT 'LIGHT'"))
        session.commit()
        logger.info("Migration: subscription_tier added to user_profiles")


def _migrate_users(session, inspector):
    """Миграции таблицы users"""
    if not inspector.has_table('users'):
        return
    cols = [col['name'] for col in inspector.get_columns('users')]
    _add_columns(session, 'users', cols, {
        'referral_balance': 'ALTER TABLE users ADD COLUMN referral_balance INTEGER DEFAULT 0',
        'referrer_id': 'ALTER TABLE users ADD COLUMN referrer_id INTEGER REFERENCES users(id)',
        'telegram_channel': 'ALTER TABLE users ADD COLUMN telegram_channel VARCHAR(255)',
        'current_task_id': 'ALTER TABLE users ADD COLUMN current_task_id INTEGER REFERENCES tasks(id)',
        'token_balance': 'ALTER TABLE users ADD COLUMN token_balance INTEGER DEFAULT 0',
        'tokens_spent': 'ALTER TABLE users ADD COLUMN tokens_spent INTEGER DEFAULT 0',
    })


def _migrate_tasks(session, inspector):
    """Создание и миграции таблицы tasks"""
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
        logger.info("Migration: tasks table created")
    else:
        cols = [col['name'] for col in inspector.get_columns('tasks')]
        _add_columns(session, 'tasks', cols, {
            'is_recurring': 'ALTER TABLE tasks ADD COLUMN is_recurring BOOLEAN DEFAULT FALSE',
            'recurrence_pattern': 'ALTER TABLE tasks ADD COLUMN recurrence_pattern VARCHAR(50)',
            'recurrence_interval': 'ALTER TABLE tasks ADD COLUMN recurrence_interval INTEGER DEFAULT 1',
            'recurrence_end_date': 'ALTER TABLE tasks ADD COLUMN recurrence_end_date TIMESTAMP',
            'parent_task_id': 'ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER REFERENCES tasks(id)',
            'followup_reminder_sent': 'ALTER TABLE tasks ADD COLUMN followup_reminder_sent BOOLEAN DEFAULT FALSE',
        })


def _migrate_posts(session, inspector):
    """Создание таблицы posts"""
    if inspector.has_table('posts'):
        return
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
    logger.info("Migration: posts table created")


def _migrate_subscriptions(session, inspector):
    """Создание таблицы subscriptions"""
    if inspector.has_table('subscriptions'):
        return
    logger.info("Creating subscriptions table")
    if LOCAL:
        session.execute(text('''
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tier TEXT DEFAULT 'LIGHT',
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
                tier subscription_tier_enum DEFAULT 'LIGHT',
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))
    session.commit()
    logger.info("Migration: subscriptions table created")


def _migrate_payments(session, inspector):
    """Создание таблицы payments"""
    if inspector.has_table('payments'):
        return
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
    logger.info("Migration: payments table created")


def _migrate_promo_codes(session, inspector):
    """Создание и миграции таблицы promo_codes"""
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
        logger.info("Migration: promo_codes table created")
    else:
        cols = [col['name'] for col in inspector.get_columns('promo_codes')]
        _add_columns(session, 'promo_codes', cols, {
            'discount_percent': 'ALTER TABLE promo_codes ADD COLUMN discount_percent INTEGER DEFAULT 0',
            'max_uses': 'ALTER TABLE promo_codes ADD COLUMN max_uses INTEGER',
            'used_count': 'ALTER TABLE promo_codes ADD COLUMN used_count INTEGER DEFAULT 0',
            'used_by_users': "ALTER TABLE promo_codes ADD COLUMN used_by_users TEXT DEFAULT '[]'",
            'used_by_user_id': 'ALTER TABLE promo_codes ADD COLUMN used_by_user_id INTEGER',
            'used_at': 'ALTER TABLE promo_codes ADD COLUMN used_at TIMESTAMP',
        })


def _migrate_anchors(session, inspector):
    """Миграции таблиц anchors и anchor_delivery_log"""
    # Таблица anchors — создаётся автоматически через Base.metadata.create_all
    # но добавляем миграцию на случай нового поля в будущем
    if inspector.has_table('anchors'):
        cols = [col['name'] for col in inspector.get_columns('anchors')]
        _add_columns(session, 'anchors', cols, {
            # Будущие миграции сюда
        })

    if inspector.has_table('anchor_delivery_log'):
        cols = [col['name'] for col in inspector.get_columns('anchor_delivery_log')]
        _add_columns(session, 'anchor_delivery_log', cols, {
            # Будущие миграции сюда
        })


def _migrate_token_transactions(session, inspector):
    """Создание таблицы token_transactions"""
    # Создаётся автоматически через Base.metadata.create_all
    # Миграции для будущих полей:
    if inspector.has_table('token_transactions'):
        cols = [col['name'] for col in inspector.get_columns('token_transactions')]
        _add_columns(session, 'token_transactions', cols, {
            # Будущие миграции сюда
        })


def run_migrations():
    """Запускает все миграции базы данных"""
    logger.info("Running database migrations...")
    session = Session()
    inspector = sa_inspect(engine)

    try:
        _migrate_user_profiles(session, inspector)
        _migrate_users(session, inspector)
        _migrate_tasks(session, inspector)
        _migrate_posts(session, inspector)
        _migrate_subscriptions(session, inspector)
        _migrate_payments(session, inspector)
        _migrate_promo_codes(session, inspector)
        _migrate_anchors(session, inspector)
        _migrate_token_transactions(session, inspector)
        logger.info("✅ Database migrations completed")
    except Exception as e:
        logger.error(f"❌ Database migrations failed: {e}")
        raise
    finally:
        session.close()
