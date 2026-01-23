import datetime
import logging
import enum
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

logger = logging.getLogger(__name__)
Base = declarative_base()


class SubscriptionTier(enum.Enum):
    BRONZE = 'BRONZE'  # 3000 RUB/month
    SILVER = 'SILVER'  # 9000 RUB/month
    GOLD = 'GOLD'      # 27000 RUB/month


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    photo_url = Column(String(500))  # Telegram profile photo URL
    memory = Column(Text)  # Long-term memory for user info
    timezone = Column(String(50), default='UTC')
    do_not_disturb_until = Column(DateTime)
    pending_action = Column(Text)  # JSON for pending interactions
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(
        DateTime, default=datetime.datetime.now(
            datetime.timezone.utc), onupdate=datetime.datetime.now(
            datetime.timezone.utc))
    invalid_chat = Column(Boolean, default=False)  # Flag set when Telegram chat is invalid (chat not found)
    subscription_tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.BRONZE)  # User's subscription tier
    average_rating = Column(Integer, default=0)  # Average rating from other users (synced from UserProfile)
    rating_count = Column(Integer, default=0)  # Number of ratings received (synced from UserProfile)
    history_cleared_at = Column(DateTime)  # When user cleared chat history


class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime)
    status = Column(String(50), default='pending')  # pending, completed, etc.
    reminder_time = Column(DateTime)
    reminder_sent = Column(Boolean, default=False)
    result_check_sent = Column(Boolean, default=False)
    estimated_duration = Column(Integer)  # in minutes
    delegated_by = Column(Integer, ForeignKey('users.id'))  # User who delegated the task
    delegated_to_username = Column(String(255))  # Username of the person who should do it
    delegation_status = Column(String(50), default=None)  # None, pending, accepted, rejected
    delegation_details = Column(Text)  # Additional details about delegation
    completion_notes = Column(Text)  # Notes about task completion/result
    actual_completion_time = Column(DateTime)  # When task was actually completed
    skipped_reason = Column(String(255))  # Reason if task was skipped/cancelled
    overdue_reminders_sent = Column(Integer, default=0)  # Number of overdue reminders sent
    recommendations = Column(Text)  # JSON array of AI-generated recommendations
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="tasks", foreign_keys=[user_id])


class Interaction(Base):
    __tablename__ = 'interactions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    message_type = Column(String(50))  # user, ai
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="interactions")


class UserProfile(Base):
    __tablename__ = 'user_profiles'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    skills = Column(Text)  # JSON or comma-separated skills
    interests = Column(Text)  # JSON or comma-separated interests
    goals = Column(Text)  # User's goals
    contact_info = Column(String(255))  # Telegram username or other contact
    city = Column(String(100))  # City for location-based matching
    birthdate = Column(String(10))  # Date of birth in DD.MM.YYYY format
    zodiac_sign = Column(String(20))  # Zodiac sign (auto-calculated)
    company = Column(String(255))  # Company name
    position = Column(String(255))  # Job position
    bio = Column(Text)  # Short bio/description (2-3 sentences about user)
    languages = Column(String(500))  # Languages: Русский (родной), English (C1), Español (A2)
    current_plans = Column(Text)  # Current plans or events, e.g., "Сегодня иду в кино, завтра на выставку"
    current_time = Column(String(10))  # User's current time in HH:MM format, for relative time calculations
    total_tasks_created = Column(Integer, default=0)  # Total tasks created
    completed_tasks = Column(Integer, default=0)  # Total completed tasks
    skipped_tasks = Column(Integer, default=0)  # Tasks marked as skipped or overdue
    average_completion_time = Column(Integer, default=0)  # Average time to complete tasks in minutes
    last_activity = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))  # Last interaction time
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    average_rating = Column(Integer, default=0)  # Average rating from other users (0-10)
    rating_count = Column(Integer, default=0)  # Number of ratings received
    favorite_contacts = Column(Text)  # JSON array of favorite contact usernames
    blocked_contacts = Column(Text)  # JSON array of blocked contact usernames
    interaction_count = Column(Integer, default=0)  # Total interactions with AI

    user = relationship("User", backref="profile")


class UserRating(Base):
    __tablename__ = 'user_ratings'

    id = Column(Integer, primary_key=True)
    rater_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)  # User who gives the rating
    rated_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)  # User who receives the rating
    rating = Column(Integer, nullable=False)  # Rating value 1-10
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    rater = relationship("User", foreign_keys=[rater_user_id])
    rated_user = relationship("User", foreign_keys=[rated_user_id])


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    telegram_id = Column(Integer, nullable=False)  # Telegram ID for quick access
    telegram_username = Column(String(100))  # Telegram username for identification
    username = Column(String(255))  # Username for quick access
    status = Column(String(50), default='inactive')  # active, inactive, expired
    plan = Column(String(50), default='monthly')  # monthly, yearly, etc.
    tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.BRONZE)  # Subscription tier
    start_date = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    end_date = Column(DateTime)
    login_count = Column(Integer, default=0)  # Number of logins
    subscriber_number = Column(Integer, unique=True)  # Subscriber number
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="subscription")


class PromoCode(Base):
    __tablename__ = 'promo_codes'

    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)  # Promo code string
    tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.BRONZE)  # Tier to grant
    discount_percent = Column(Integer, default=0)  # Discount percentage (0-100)
    max_uses = Column(Integer, nullable=True)  # Maximum uses (None = unlimited)
    duration_days = Column(Integer, default=30)  # Duration in days
    expires_at = Column(DateTime, nullable=False)  # Expiration date
    is_used = Column(Boolean, default=False)  # Whether the code has been used (for single-use codes)
    used_count = Column(Integer, default=0)  # Number of times used
    used_by_users = Column(Text, default='[]')  # JSON list of user IDs who used this code
    used_by_user_id = Column(Integer, ForeignKey('users.id'))  # User who used it (for single-use) - deprecated
    used_at = Column(DateTime)  # When it was used (for single-use) - deprecated
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    used_by_user = relationship("User", backref="used_promo_codes")


class PaymentHistory(Base):
    """История всех изменений подписок и платежей для защиты от потери данных"""
    __tablename__ = 'payment_history'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    telegram_username = Column(String(100))
    action = Column(String(50), nullable=False)  # payment, tier_change, subscription_activated, promo_used, etc.
    tier = Column(Enum(SubscriptionTier), nullable=False)  # Tier at the time of action
    amount = Column(String(20))  # Payment amount if applicable
    payment_id = Column(String(100))  # External payment system ID (Yookassa, etc.)
    duration_days = Column(Integer)  # Duration of subscription
    start_date = Column(DateTime)  # Subscription start date
    end_date = Column(DateTime)  # Subscription end date
    details = Column(Text)  # JSON with additional details
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="payment_history")


class Post(Base):
    """User posts for news feed"""
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    username = Column(String(255))  # Denormalized username for easy viewing
    content = Column(Text, nullable=False)  # Post content
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="posts")


class Comment(Base):
    """Comments on posts"""
    __tablename__ = 'comments'

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    username = Column(String(255))  # Denormalized username for easy viewing
    content = Column(Text, nullable=False)  # Comment content
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    post = relationship("Post", backref="comments")
    user = relationship("User", backref="comments")


# Fix DATABASE_URL for psycopg2 compatibility
db_url = DATABASE_URL
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

# Import psycopg2 to ensure the driver is available
try:
    import psycopg2
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)
except ImportError:
    pass  # psycopg2 not available, perhaps using SQLite

# Increase connection pool size to handle more concurrent requests
connect_args = {}
if db_url and db_url.startswith('postgresql'):
    connect_args = {
        "connect_timeout": 10,  # 10 seconds timeout for PostgreSQL
        "options": "-c statement_timeout=10000"  # 10 seconds statement timeout
    }

engine = create_engine(
    db_url,
    pool_size=50,           # Increased from 20
    max_overflow=50,        # Increased from 30
    pool_timeout=60,        # Increased from default 30
    pool_recycle=3600,      # Recycle connections after 1 hour
    pool_pre_ping=True,     # Check connections before using
    connect_args=connect_args
)

def init_db():
    """Initialize database tables. Call this after ensuring DB is accessible."""
    try:
        Base.metadata.create_all(engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise

Session = sessionmaker(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
