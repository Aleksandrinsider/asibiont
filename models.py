import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from config import DATABASE_URL

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    memory = Column(Text)  # Long-term memory for user info
    timezone = Column(String(50), default='UTC')
    do_not_disturb_until = Column(DateTime)
    pending_action = Column(Text)  # JSON for pending interactions
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

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
    priority = Column(String(20), default='medium')  # high, medium, low
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="tasks")

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
    current_plans = Column(Text)  # Current plans or events, e.g., "Сегодня иду в кино, завтра на выставку"
    current_time = Column(String(10))  # User's current time in HH:MM format, for relative time calculations
    total_tasks_created = Column(Integer, default=0)  # Total tasks created
    completed_tasks = Column(Integer, default=0)  # Total completed tasks
    skipped_tasks = Column(Integer, default=0)  # Tasks marked as skipped or overdue
    average_completion_time = Column(Integer, default=0)  # Average time to complete tasks in minutes
    last_activity = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))  # Last interaction time
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="profile")

class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    status = Column(String(50), default='inactive')  # active, inactive, expired
    plan = Column(String(50), default='monthly')  # monthly, yearly, etc.
    start_date = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    end_date = Column(DateTime)
    login_count = Column(Integer, default=0)  # Number of logins
    subscriber_number = Column(Integer, unique=True)  # Subscriber number
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="subscription")

# Fix DATABASE_URL for psycopg2 compatibility
db_url = DATABASE_URL
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

# Increase connection pool size to handle more concurrent requests
engine = create_engine(
    db_url,
    pool_size=20,           # Increased from default 5
    max_overflow=30,        # Increased from default 10
    pool_timeout=60,        # Increased from default 30
    pool_recycle=3600,      # Recycle connections after 1 hour
    pool_pre_ping=True      # Check connections before using
)
try:
    Base.metadata.create_all(engine)
    print("Database tables created successfully")
except Exception as e:
    print(f"Failed to create database tables: {e}")
    raise

Session = sessionmaker(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)