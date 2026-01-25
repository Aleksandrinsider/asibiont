"""
Unit tests for models.py
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, UserProfile, SubscriptionTier
import datetime


@pytest.fixture(scope="function")
def test_db():
    """Create in-memory SQLite database for testing"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_user_creation(test_db):
    """Test User model creation"""
    user = User(
        telegram_id=123456789,
        username="testuser",
        first_name="Test User"
    )
    test_db.add(user)
    test_db.commit()

    # Verify user was created
    assert user.id is not None
    assert user.telegram_id == 123456789
    assert user.username == "testuser"
    assert user.subscription_tier == SubscriptionTier.BRONZE


def test_task_creation(test_db):
    """Test Task model creation"""
    # Create user first
    user = User(telegram_id=123456789, username="testuser")
    test_db.add(user)
    test_db.commit()

    # Create task
    task = Task(
        user_id=user.id,
        title="Test Task",
        description="This is a test task",
        status="pending"
    )
    test_db.add(task)
    test_db.commit()

    # Verify task was created
    assert task.id is not None
    assert task.user_id == user.id
    assert task.title == "Test Task"
    assert task.status == "pending"


def test_user_profile_creation(test_db):
    """Test UserProfile model creation"""
    # Create user first
    user = User(telegram_id=123456789, username="testuser")
    test_db.add(user)
    test_db.commit()

    # Create profile
    profile = UserProfile(
        user_id=user.id,
        skills="Python, SQL",
        interests="AI, Programming",
        city="Moscow"
    )
    test_db.add(profile)
    test_db.commit()

    # Verify profile was created
    assert profile.id is not None
    assert profile.user_id == user.id
    assert profile.skills == "Python, SQL"
    assert profile.city == "Moscow"


def test_task_relationship(test_db):
    """Test relationship between User and Task"""
    # Create user
    user = User(telegram_id=123456789, username="testuser")
    test_db.add(user)
    test_db.commit()

    # Create task
    task = Task(
        user_id=user.id,
        title="Test Task",
        description="Test description"
    )
    test_db.add(task)
    test_db.commit()

    # Test relationship
    retrieved_user = test_db.query(User).filter_by(telegram_id=123456789).first()
    assert len(retrieved_user.tasks) == 1
    assert retrieved_user.tasks[0].title == "Test Task"


def test_subscription_tier_enum(test_db):
    """Test SubscriptionTier enum values"""
    assert SubscriptionTier.BRONZE.value == 'BRONZE'
    assert SubscriptionTier.SILVER.value == 'SILVER'
    assert SubscriptionTier.GOLD.value == 'GOLD'

    # Test default value in database
    user = User(telegram_id=123456789)
    test_db.add(user)
    test_db.commit()

    # Reload from database to get default values
    test_db.refresh(user)
    assert user.subscription_tier == SubscriptionTier.BRONZE