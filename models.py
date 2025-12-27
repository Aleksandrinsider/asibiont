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
    message_type = Column(String(50))  # user, agent
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
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="profile")

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)

Session = sessionmaker(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)