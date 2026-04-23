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
        'website': 'ALTER TABLE user_profiles ADD COLUMN website VARCHAR(500)',
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
        'goal_autopilot_enabled': 'ALTER TABLE user_profiles ADD COLUMN goal_autopilot_enabled BOOLEAN DEFAULT FALSE',
        'gender': "ALTER TABLE user_profiles ADD COLUMN gender VARCHAR(10)",
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
        'tg_avatar_data': 'ALTER TABLE users ADD COLUMN tg_avatar_data TEXT',
        'google_oauth_token': 'ALTER TABLE users ADD COLUMN google_oauth_token TEXT',
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

        # Partial unique index: запрещает дублирующиеся pending-якоря с одинаковым
        # (user_id, anchor_type, source) при race condition (Railway multi-instance).
        # Код обрабатывает IntegrityError и молча пропускает дубли.
        try:
            session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS anchors_pending_unique_src "
                "ON anchors (user_id, anchor_type, source) "
                "WHERE delivered_at IS NULL AND source IS NOT NULL"
            ))
            session.commit()
            logger.info("Migration: anchors_pending_unique_src index created")
        except Exception as _idx_err:
            session.rollback()
            logger.debug(f"anchors_pending_unique_src: {_idx_err}")

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
            'slug': 'ALTER TABLE notes ADD COLUMN slug VARCHAR(300)',
            'title_en': 'ALTER TABLE notes ADD COLUMN title_en VARCHAR(500)',
            'content_en': 'ALTER TABLE notes ADD COLUMN content_en TEXT',
            'image_data': 'ALTER TABLE notes ADD COLUMN image_data BYTEA',
            'image_mime': "ALTER TABLE notes ADD COLUMN image_mime VARCHAR(20)",
        })
        # Index on slug for fast lookups
        if 'slug' not in cols:
            try:
                session.execute(text('CREATE INDEX IF NOT EXISTS ix_notes_slug ON notes(slug)'))
                session.commit()
            except Exception:
                session.rollback()
        # Backfill slug for existing blog posts that don't have one
        try:
            rows = session.execute(text(
                "SELECT id, title FROM notes WHERE source='blog' AND (slug IS NULL OR slug='')"
            )).fetchall()
            if rows:
                import re as _re_mig
                _TRANSLIT = {
                    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z',
                    'и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r',
                    'с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',
                    'щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
                }
                for row_id, row_title in rows:
                    s = (row_title or 'post').lower().strip()
                    r = ''
                    for ch in s:
                        r += _TRANSLIT.get(ch, ch)
                    r = _re_mig.sub(r'[^a-z0-9]+', '-', r).strip('-')[:60].rstrip('-') or 'post'
                    new_slug = f"{row_id}-{r}"
                    session.execute(text(
                        "UPDATE notes SET slug=:slug WHERE id=:id"
                    ), {'slug': new_slug, 'id': row_id})
                session.commit()
                logger.info(f"Migration: backfilled slug for {len(rows)} blog posts")
        except Exception as _e:
            logger.warning(f"Migration: slug backfill failed: {_e}")
            session.rollback()


def _migrate_email_campaigns(session, inspector):
    """Миграции email_campaigns + email_outreach (создание через Base.metadata.create_all, только добавляем колонки если нужно)"""
    # Таблицы создаются автоматически через Base.metadata.create_all в models.py
    # Здесь только миграции колонок для существующих таблиц
    if inspector.has_table('email_campaigns'):
        cols = [col['name'] for col in inspector.get_columns('email_campaigns')]
        _add_columns(session, 'email_campaigns', cols, {
            'max_follow_ups': 'ALTER TABLE email_campaigns ADD COLUMN max_follow_ups INTEGER DEFAULT 2',
            'landing_url': 'ALTER TABLE email_campaigns ADD COLUMN landing_url VARCHAR(500)',
        })
    if inspector.has_table('email_outreach'):
        cols = [col['name'] for col in inspector.get_columns('email_outreach')]
        _add_columns(session, 'email_outreach', cols, {
            'follow_up_count': 'ALTER TABLE email_outreach ADD COLUMN follow_up_count INTEGER DEFAULT 0',
            'last_follow_up_at': 'ALTER TABLE email_outreach ADD COLUMN last_follow_up_at TIMESTAMP',
            'next_follow_up_at': 'ALTER TABLE email_outreach ADD COLUMN next_follow_up_at TIMESTAMP',
            'ai_reply_text': 'ALTER TABLE email_outreach ADD COLUMN ai_reply_text TEXT',
            'ai_reply_sent_at': 'ALTER TABLE email_outreach ADD COLUMN ai_reply_sent_at TIMESTAMP',
            # Outcome Feedback Loop fields (#1)
            'reply_count': 'ALTER TABLE email_outreach ADD COLUMN reply_count INTEGER DEFAULT 0',
            'engagement_rating': 'ALTER TABLE email_outreach ADD COLUMN engagement_rating FLOAT',
            'ai_reply_count': 'ALTER TABLE email_outreach ADD COLUMN ai_reply_count INTEGER DEFAULT 0',
            'success': 'ALTER TABLE email_outreach ADD COLUMN success BOOLEAN',
            # Email Content Fingerprint fields (#2)
            'body_length': 'ALTER TABLE email_outreach ADD COLUMN body_length INTEGER',
            'has_personalization': 'ALTER TABLE email_outreach ADD COLUMN has_personalization BOOLEAN',
            'has_call_to_action': 'ALTER TABLE email_outreach ADD COLUMN has_call_to_action BOOLEAN',
            'tone_type': 'ALTER TABLE email_outreach ADD COLUMN tone_type VARCHAR(30)',
            'sent_at_hour_utc': 'ALTER TABLE email_outreach ADD COLUMN sent_at_hour_utc INTEGER',
            'sent_by_agent': 'ALTER TABLE email_outreach ADD COLUMN sent_by_agent VARCHAR(100)',
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
        if 'search_scope' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN search_scope TEXT"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.search_scope")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] search_scope add skipped: {e}")
        if 'last_office_run_at' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN last_office_run_at TIMESTAMP"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.last_office_run_at")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] last_office_run_at add skipped: {e}")
        if 'last_stdout_hash' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN last_stdout_hash VARCHAR(32)"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.last_stdout_hash")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] last_stdout_hash add skipped: {e}")
        if 'run_interval_minutes' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN run_interval_minutes INTEGER DEFAULT 60"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.run_interval_minutes")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] run_interval_minutes add skipped: {e}")
        if 'gender' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN gender VARCHAR(10) DEFAULT 'male'"))
                session.commit()
                logger.info("[MIGRATION] Added user_agents.gender")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] gender add skipped: {e}")
        if 'webhook_token' not in cols:
            try:
                session.execute(text("ALTER TABLE user_agents ADD COLUMN webhook_token VARCHAR(64)"))
                session.commit()
                # Уникальный индекс
                try:
                    session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_user_agents_webhook_token ON user_agents (webhook_token) WHERE webhook_token IS NOT NULL"))
                    session.commit()
                except Exception:
                    session.rollback()
                logger.info("[MIGRATION] Added user_agents.webhook_token")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] webhook_token add skipped: {e}")


def _migrate_activity_log_updated_at_index(session, inspector):
    """Добавляет индекс на agent_activity_log.updated_at для эффективного SSE-отслеживания обновлений."""
    if not inspector.has_table('agent_activity_log'):
        return
    try:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('agent_activity_log')}
        if 'ix_agent_activity_updated_at' not in existing_indexes:
            # NOTE: CONCURRENTLY cannot run inside a transaction block (SQLAlchemy uses implicit txn)
            session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_agent_activity_updated_at "
                "ON agent_activity_log(user_id, updated_at)"
            ))
            session.commit()
            logger.info("[MIGRATION] Created index ix_agent_activity_updated_at")
    except Exception as e:
        session.rollback()
        logger.debug(f"[MIGRATION] ix_agent_activity_updated_at skipped: {e}")


def _migrate_agent_activity_log_title_default(session, inspector):
    """Добавляет DEFAULT '' к agent_activity_log.title чтобы NOT NULL не падал при вставке без title."""
    if not inspector.has_table('agent_activity_log'):
        return
    try:
        session.execute(text(
            "ALTER TABLE agent_activity_log ALTER COLUMN title SET DEFAULT ''"
        ))
        session.commit()
        logger.info("[MIGRATION] Set DEFAULT '' on agent_activity_log.title")
    except Exception as e:
        session.rollback()
        logger.debug(f"[MIGRATION] agent_activity_log.title default skipped: {e}")


def _migrate_activity_log(session, inspector):
    """Создаёт таблицу agent_activity_log (идемпотентно)."""
    from models import Base, engine as _engine
    if not inspector.has_table('agent_activity_log'):
        try:
            Base.metadata.tables['agent_activity_log'].create(bind=_engine, checkfirst=True)
            logger.info("[MIGRATION] Created table agent_activity_log")
        except Exception as e:
            logger.warning(f"[MIGRATION] Could not create agent_activity_log: {e}")


def _migrate_task_agent_source(session, inspector):
    """Добавляет source + created_by_agent_id к таблице tasks (идемпотентно)."""
    if not inspector.has_table('tasks'):
        return
    cols = {c['name'] for c in inspector.get_columns('tasks')}
    if 'source' not in cols:
        try:
            session.execute(text("ALTER TABLE tasks ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"))
            session.commit()
            logger.info("[MIGRATION] Added tasks.source")
        except Exception as e:
            session.rollback()
            logger.debug(f"[MIGRATION] tasks.source skipped: {e}")
    if 'created_by_agent_id' not in cols:
        try:
            session.execute(text("ALTER TABLE tasks ADD COLUMN created_by_agent_id INTEGER REFERENCES user_agents(id)"))
            session.commit()
            logger.info("[MIGRATION] Added tasks.created_by_agent_id")
        except Exception as e:
            session.rollback()
            logger.debug(f"[MIGRATION] tasks.created_by_agent_id skipped: {e}")


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
        if 'likes_count' not in cols:
            try:
                session.execute(text("ALTER TABLE arena_posts ADD COLUMN likes_count INTEGER DEFAULT 0"))
                session.commit()
                logger.info("[MIGRATION] Added arena_posts.likes_count")
            except Exception as e:
                session.rollback()
                logger.debug(f"[MIGRATION] likes_count add skipped: {e}")


def _migrate_fix_agent_python_code(session):
    """Патч python_code агентов: 3 бага в старых шаблонах (IMAP, CDATA regex, RSS link regex)."""
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


def _migrate_payment_id_unique(session, inspector):
    """Добавляет уникальный индекс на payment_history.payment_id для защиты от дублей вебхуков."""
    if not inspector.has_table('payment_history'):
        return
    try:
        indexes = inspector.get_indexes('payment_history')
        if any(idx.get('name') == 'ix_payment_history_payment_id' for idx in indexes):
            return
        session.execute(text("CREATE UNIQUE INDEX ix_payment_history_payment_id ON payment_history (payment_id) WHERE payment_id IS NOT NULL"))
        session.commit()
        logger.info("[MIGRATION] Added unique index on payment_history.payment_id")
    except Exception as e:
        session.rollback()
        logger.debug(f"[MIGRATION] payment_history unique index skipped: {e}")


def _cleanup_junk_agent_tasks(session):
    """Удаляет мусорные задачи агентов, которые не нужны пользователю.

    - cancelled + 'Прервано: новый цикл агента' → внутренний перезапуск автопилота
    - cancelled + 'Агент вернул пустой результат' → агент не дал результата
    - in_progress + source='agent' старше 2ч → зависшие задачи координатора
    """
    try:
        result = session.execute(text(
            "DELETE FROM tasks WHERE source='agent' AND status='cancelled' "
            "AND completion_notes IN ('Прервано: новый цикл агента', 'Агент вернул пустой результат')"
        ))
        deleted_cancelled = result.rowcount if hasattr(result, 'rowcount') else 0

        result2 = session.execute(text(
            "DELETE FROM tasks WHERE source='agent' AND status='in_progress' "
            "AND created_at < NOW() - INTERVAL '2 hours'"
        ))
        deleted_stuck = result2.rowcount if hasattr(result2, 'rowcount') else 0

        # Переименовываем старые AAL-записи с техническим префиксом "L2 координация:"
        result3 = session.execute(text(
            "UPDATE agent_activity_log "
            "SET title = REGEXP_REPLACE(title, '^L2 координация: ', '') "
            "WHERE title LIKE 'L2 координация: %'"
        ))
        renamed_aal = result3.rowcount if hasattr(result3, 'rowcount') else 0

        session.commit()
        if deleted_cancelled or deleted_stuck or renamed_aal:
            logger.info(
                "Cleanup: removed %d cancelled+%d stuck junk agent tasks, renamed %d AAL titles",
                deleted_cancelled, deleted_stuck, renamed_aal,
            )
    except Exception as e:
        logger.warning("Cleanup junk agent tasks failed (non-fatal): %s", e)
        try:
            session.rollback()
        except Exception:
            pass


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
        _migrate_activity_log(session, inspector)
        _migrate_task_agent_source(session, inspector)
        _migrate_fix_agent_python_code(session)
        _migrate_payment_id_unique(session, inspector)
        _migrate_activity_log_updated_at_index(session, inspector)
        _migrate_agent_activity_log_title_default(session, inspector)
        _cleanup_junk_agent_tasks(session)
        _migrate_intelligence_tables(session, inspector)
        logger.info("✅ Database migrations completed")
    except Exception as e:
        logger.error(f"❌ Database migrations failed: {e}")
        raise
    finally:
        session.close()


def _migrate_intelligence_tables(session, inspector):
    """Создаём таблицы intelligence layer: decision_log, email_contact_preferences."""
    from models import Base, engine as _engine
    for tbl in ('decision_log', 'email_contact_preferences'):
        if not inspector.has_table(tbl):
            try:
                Base.metadata.tables[tbl].create(bind=_engine, checkfirst=True)
                logger.info(f"[MIGRATION] Created intelligence table: {tbl}")
            except Exception as e:
                logger.warning(f"[MIGRATION] Could not create {tbl}: {e}")


if __name__ == '__main__':
    run_migrations()
