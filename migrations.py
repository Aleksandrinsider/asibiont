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
        'status_text': 'ALTER TABLE user_profiles ADD COLUMN status_text VARCHAR(100)',
        # Normalized (English) fields for cross-language contact matching
        'skills_normalized': 'ALTER TABLE user_profiles ADD COLUMN skills_normalized TEXT',
        'interests_normalized': 'ALTER TABLE user_profiles ADD COLUMN interests_normalized TEXT',
        'goals_normalized': 'ALTER TABLE user_profiles ADD COLUMN goals_normalized TEXT',
        'city_normalized': 'ALTER TABLE user_profiles ADD COLUMN city_normalized VARCHAR(100)',
        'company_normalized': 'ALTER TABLE user_profiles ADD COLUMN company_normalized VARCHAR(255)',
        'position_normalized': 'ALTER TABLE user_profiles ADD COLUMN position_normalized VARCHAR(255)',
        'bio_normalized': 'ALTER TABLE user_profiles ADD COLUMN bio_normalized TEXT',
        'status_text_normalized': 'ALTER TABLE user_profiles ADD COLUMN status_text_normalized VARCHAR(100)',
        'current_plans_normalized': 'ALTER TABLE user_profiles ADD COLUMN current_plans_normalized TEXT',
        # Normalized (Russian) fields for displaying to RU users
        'skills_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN skills_normalized_ru TEXT',
        'interests_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN interests_normalized_ru TEXT',
        'goals_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN goals_normalized_ru TEXT',
        'city_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN city_normalized_ru VARCHAR(100)',
        'company_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN company_normalized_ru VARCHAR(255)',
        'position_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN position_normalized_ru VARCHAR(255)',
        'bio_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN bio_normalized_ru TEXT',
        'status_text_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN status_text_normalized_ru VARCHAR(100)',
        'current_plans_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN current_plans_normalized_ru TEXT',
        # Country field
        'country': 'ALTER TABLE user_profiles ADD COLUMN country VARCHAR(100)',
        'country_normalized': 'ALTER TABLE user_profiles ADD COLUMN country_normalized VARCHAR(100)',
        'country_normalized_ru': 'ALTER TABLE user_profiles ADD COLUMN country_normalized_ru VARCHAR(100)',
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
        'language': "ALTER TABLE users ADD COLUMN language VARCHAR(5) DEFAULT 'ru'",
        'platform': "ALTER TABLE users ADD COLUMN platform VARCHAR(20) DEFAULT 'telegram'",
        'discord_id': "ALTER TABLE users ADD COLUMN discord_id BIGINT",
        'discord_username': "ALTER TABLE users ADD COLUMN discord_username VARCHAR(255)",
        'discord_webhook': "ALTER TABLE users ADD COLUMN discord_webhook VARCHAR(500)",
        'discord_server_name': "ALTER TABLE users ADD COLUMN discord_server_name VARCHAR(255)",
        'discord_guild_id': "ALTER TABLE users ADD COLUMN discord_guild_id VARCHAR(64)",
        'discord_channel_id': "ALTER TABLE users ADD COLUMN discord_channel_id VARCHAR(64)",
        'custom_avatar': 'ALTER TABLE users ADD COLUMN custom_avatar TEXT',
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
            'delegation_campaign_id': 'ALTER TABLE tasks ADD COLUMN delegation_campaign_id INTEGER',
        })


def _migrate_posts(session, inspector):
    """Создание таблицы posts + image_url колонка"""
    if inspector.has_table('posts'):
        # Добавляем image_url если нет
        cols = [c['name'] for c in inspector.get_columns('posts')]
        _add_columns(session, 'posts', cols, {
            'image_url': 'ALTER TABLE posts ADD COLUMN image_url TEXT',
        })
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


def _migrate_anchors(session, inspector):
    """Миграции таблиц anchors и anchor_delivery_log"""
    if inspector.has_table('anchors'):
        cols = [col['name'] for col in inspector.get_columns('anchors')]
        _add_columns(session, 'anchors', cols, {
            # Будущие миграции сюда
        })
        # Migrate cooldown_hours from INTEGER to FLOAT (0.3 was truncated to 0)
        try:
            session.execute(text('ALTER TABLE anchors ALTER COLUMN cooldown_hours TYPE DOUBLE PRECISION USING cooldown_hours::double precision'))
            session.commit()
            logger.info('Migrated anchors.cooldown_hours to FLOAT')
        except Exception:
            session.rollback()

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


def _migrate_goals(session, inspector):
    """Миграции таблицы goals — добавление полей метрик"""
    if not inspector.has_table('goals'):
        return
    cols = [col['name'] for col in inspector.get_columns('goals')]
    _add_columns(session, 'goals', cols, {
        'metric_unit': 'ALTER TABLE goals ADD COLUMN metric_unit VARCHAR(100)',
        'metric_target': 'ALTER TABLE goals ADD COLUMN metric_target FLOAT',
        'metric_current': 'ALTER TABLE goals ADD COLUMN metric_current FLOAT DEFAULT 0',
    })


def _migrate_notes(session, inspector):
    """Create notes table if not exists"""
    if not inspector.has_table('notes'):
        try:
            session.execute(text("""
                CREATE TABLE notes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    title VARCHAR(200),
                    content TEXT NOT NULL,
                    source VARCHAR(20) DEFAULT 'manual',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            session.execute(text("CREATE INDEX ix_notes_user_id ON notes(user_id)"))
            session.execute(text("CREATE INDEX ix_notes_created_at ON notes(created_at)"))
            session.commit()
            logger.info("Migration: notes table created")
        except Exception as e:
            logger.error(f"Failed to create notes table: {e}")
            session.rollback()
    else:
        # Add title column if it doesn't exist
        cols = [col['name'] for col in inspector.get_columns('notes')]
        _add_columns(session, 'notes', cols, {
            'title': 'ALTER TABLE notes ADD COLUMN title VARCHAR(200)',
        })


def _migrate_email_campaigns(session, inspector):
    """Миграции email_campaigns + email_outreach (создание через Base.metadata.create_all, только добавляем колонки если нужно)"""
    # Таблицы создаются автоматически через Base.metadata.create_all в models.py
    # Здесь только миграции колонок для существующих таблиц
    if inspector.has_table('email_campaigns'):
        cols = [col['name'] for col in inspector.get_columns('email_campaigns')]
        _add_columns(session, 'email_campaigns', cols, {
            'max_follow_ups': 'ALTER TABLE email_campaigns ADD COLUMN max_follow_ups INTEGER DEFAULT 2',
        })
    if inspector.has_table('email_outreach'):
        cols = [col['name'] for col in inspector.get_columns('email_outreach')]
        _add_columns(session, 'email_outreach', cols, {
            'follow_up_count': 'ALTER TABLE email_outreach ADD COLUMN follow_up_count INTEGER DEFAULT 0',
            'last_follow_up_at': 'ALTER TABLE email_outreach ADD COLUMN last_follow_up_at TIMESTAMP',
            'next_follow_up_at': 'ALTER TABLE email_outreach ADD COLUMN next_follow_up_at TIMESTAMP',
            'ai_reply_text': 'ALTER TABLE email_outreach ADD COLUMN ai_reply_text TEXT',
            'ai_reply_sent_at': 'ALTER TABLE email_outreach ADD COLUMN ai_reply_sent_at TIMESTAMP',
        })
        # Unique index на (campaign_id, recipient_email) — защита от дублей при race condition
        try:
            existing_indexes = [idx['name'] for idx in inspector.get_indexes('email_outreach')]
            if 'ix_email_outreach_campaign_recipient' not in existing_indexes:
                session.execute(text(
                    'CREATE UNIQUE INDEX ix_email_outreach_campaign_recipient '
                    'ON email_outreach (campaign_id, recipient_email)'
                ))
                session.commit()
                logger.info("[MIGRATION] Created unique index ix_email_outreach_campaign_recipient")
        except Exception as e:
            session.rollback()
            logger.warning(f"[MIGRATION] Unique index email_outreach skipped: {e}")


def _migrate_email_contacts(session, inspector):
    """Миграции email_contacts (создание через Base.metadata.create_all, только добавляем колонки если нужно)"""
    if inspector.has_table('email_contacts'):
        cols = [col['name'] for col in inspector.get_columns('email_contacts')]
        _add_columns(session, 'email_contacts', cols, {
            'position': 'ALTER TABLE email_contacts ADD COLUMN position VARCHAR(200)',
            'updated_at': 'ALTER TABLE email_contacts ADD COLUMN updated_at TIMESTAMP',
        })


def _migrate_marketplace(session, inspector):
    """Создаёт таблицы маркетплейса агентов и скриптов (идемпотентно)."""
    from models import Base, engine as _engine
    # Создаём таблицы через metadata (create_all пропускает существующие)
    tables_to_create = ['user_agents', 'agent_subscriptions', 'agent_runs', 'agent_ratings',
                        'user_scripts', 'script_installs', 'script_runs']
    for tbl in tables_to_create:
        if not inspector.has_table(tbl):
            try:
                Base.metadata.tables[tbl].create(bind=_engine, checkfirst=True)
                logger.info(f"[MIGRATION] Created table {tbl}")
            except Exception as e:
                logger.warning(f"[MIGRATION] Could not create {tbl}: {e}")

    # Расширяем avatar_url до TEXT (было VARCHAR(500) — не влезает base64)
    if inspector.has_table('user_agents'):
        try:
            session.execute(text(
                "ALTER TABLE user_agents ALTER COLUMN avatar_url TYPE TEXT"
            ))
            session.commit()
            logger.info("[MIGRATION] user_agents.avatar_url changed to TEXT")
        except Exception as e:
            session.rollback()
            logger.debug(f"[MIGRATION] avatar_url type change skipped: {e}")

    # Добавляем user_api_keys
    if inspector.has_table('user_agents'):
        cols = [c['name'] for c in inspector.get_columns('user_agents')]
        if 'user_api_keys' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN user_api_keys TEXT"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.user_api_keys")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] user_api_keys add skipped: {e}")
        if 'python_code' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN python_code TEXT"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.python_code")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] python_code add skipped: {e}")
        if 'arena_likes_count' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN arena_likes_count INTEGER DEFAULT 0"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.arena_likes_count")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] arena_likes_count add skipped: {e}")
        if 'arena_views_count' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN arena_views_count INTEGER DEFAULT 0"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.arena_views_count")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] arena_views_count add skipped: {e}")
        if 'is_private' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN is_private BOOLEAN DEFAULT FALSE"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.is_private")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] is_private add skipped: {e}")
        if 'job_title' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN job_title VARCHAR(200)"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.job_title")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] job_title add skipped: {e}")


def _migrate_arena(session, inspector):
    """Создаёт таблицы для постов и комментариев арены (идемпотентно)."""
    from models import Base, engine as _engine
    for tbl in ['arena_posts', 'arena_comments']:
        if not inspector.has_table(tbl):
            try:
                Base.metadata.tables[tbl].create(bind=_engine, checkfirst=True)
                logger.info(f"[MIGRATION] Created table {tbl}")
            except Exception as e:
                logger.warning(f"[MIGRATION] Could not create {tbl}: {e}")
    # Добавить reply_to если нет
    if inspector.has_table('arena_posts'):
        cols = [c['name'] for c in inspector.get_columns('arena_posts')]
        if 'reply_to' not in cols:
            try:
                session.execute(text("ALTER TABLE arena_posts ADD COLUMN reply_to VARCHAR(100)"))
                session.commit()
                logger.info("[MIGRATION] Added arena_posts.reply_to")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] reply_to add skipped: {e}")
        if 'avatar_url' not in cols:
            try:
                session.execute(text("ALTER TABLE arena_posts ADD COLUMN avatar_url TEXT"))
                session.commit()
                logger.info("[MIGRATION] Added arena_posts.avatar_url")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] avatar_url add skipped: {e}")
        if 'author_username' not in cols:
            try:
                session.execute(text("ALTER TABLE arena_posts ADD COLUMN author_username VARCHAR(100)"))
                session.commit()
                logger.info("[MIGRATION] Added arena_posts.author_username")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] author_username add skipped: {e}")


def _migrate_fix_agent_python_code(session):
    """Патчит python_code у существующих агентов: исправляет 3 бага в старых шаблонах.

    1. mail.login(USER, PASS) → mail.login(USER, PASS.replace(' ', ''))
       (IMAP не принимает app-пароли с пробелами)
    2. r'<![CDATA[(.*?)]]>' → r'<!\[CDATA\[(.*?)\]\]>'
       (скобки в regex нужно экранировать)
    3. r'<link>s*(.*?)s*</link>' → r'<link>\s*(.*?)\s*</link>'
       (пропущен backslash перед \s)
    """
    try:
        from models import UserAgent
        agents = session.query(UserAgent).filter(
            UserAgent.python_code != None,
            UserAgent.python_code != '',
        ).all()
        patched = 0
        for agent in agents:
            code = agent.python_code or ''
            new_code = code
            # Fix 1: IMAP login without .replace
            new_code = new_code.replace(
                'mail.login(USER, PASS)\n',
                'mail.login(USER, PASS.replace(\' \', \'\'))\n',
            )
            # Fix 2: CDATA regex — unescaped brackets
            new_code = new_code.replace(
                r"r'<![CDATA[(.*?)]]>'",
                r"r'<!\[CDATA\[(.*?)\]\]>'",
            )
            # Fix 3: RSS link regex — missing backslash
            new_code = new_code.replace(
                r"r'<link>s*(.*?)s*</link>'",
                r"r'<link>\s*(.*?)\s*</link>'",
            )
            if new_code != code:
                agent.python_code = new_code
                patched += 1
        if patched:
            session.commit()
            logger.info(f"[MIGRATION] Fixed python_code bugs in {patched} agents")
        else:
            logger.debug("[MIGRATION] No agents needed python_code fixes")
    except Exception as e:
        session.rollback()
        logger.warning(f"[MIGRATION] _migrate_fix_agent_python_code skipped: {e}")


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
        _migrate_anchors(session, inspector)
        _migrate_token_transactions(session, inspector)
        _migrate_goals(session, inspector)
        _migrate_notes(session, inspector)
        _migrate_email_campaigns(session, inspector)
        _migrate_email_contacts(session, inspector)
        _migrate_marketplace(session, inspector)
        _migrate_arena(session, inspector)
        _migrate_fix_agent_python_code(session)
        logger.info("✅ Database migrations completed")
    except Exception as e:
        logger.error(f"❌ Database migrations failed: {e}")
        raise
    finally:
        session.close()
