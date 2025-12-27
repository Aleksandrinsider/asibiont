from models import Base, engine, Session, User, Task, UserProfile, Interaction, Subscription
from datetime import datetime, timedelta
import pytz

# Create tables
Base.metadata.create_all(engine)

session = Session()

# Create test user
user = User(
    telegram_id=12345,
    username="testuser",
    first_name="Test User",
    timezone="Europe/Moscow"
)
session.add(user)
session.commit()

# Create user profile
profile = UserProfile(
    user_id=user.id,
    skills="Python, Web Development",
    interests="AI, Programming",
    total_tasks_created=5,
    completed_tasks=3,
    skipped_tasks=1,
    average_completion_time=45
)
session.add(profile)

# Create tasks
task1 = Task(
    user_id=user.id,
    title="Подготовить презентацию",
    description="Создать слайды для встречи",
    status="completed",
    priority="high",
    created_at=datetime.now(pytz.UTC) - timedelta(days=2)
)
task2 = Task(
    user_id=user.id,
    title="Завершить проект",
    description="Доделать веб-приложение",
    status="pending",
    priority="medium",
    created_at=datetime.now(pytz.UTC) - timedelta(days=1)
)
session.add(task1)
session.add(task2)

# Create interactions
interaction1 = Interaction(
    user_id=user.id,
    message_type="user",
    content="Добавь задачу на завтра",
    created_at=datetime.now(pytz.UTC) - timedelta(hours=5)
)
interaction2 = Interaction(
    user_id=user.id,
    message_type="agent",
    content="Задача добавлена успешно",
    created_at=datetime.now(pytz.UTC) - timedelta(hours=5)
)
session.add(interaction1)
session.add(interaction2)

# Create subscription
subscription = Subscription(
    user_id=user.id,
    status="active",
    plan="monthly",
    start_date=datetime.now(pytz.UTC) - timedelta(days=10),
    end_date=datetime.now(pytz.UTC) + timedelta(days=20)
)
session.add(subscription)

session.commit()
user_id = user.id
session.close()

print("Test user created with ID:", user_id)