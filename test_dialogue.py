import asyncio
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Force local mode for testing
os.environ['LOCAL'] = '1'

from ai_integration import chat_with_ai, set_redis_client
from models import Base, engine, Session, User, Task
from config import DATABASE_URL, REDIS_URL, FREE_ACCESS_MODE
import redis.asyncio as redis

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Create database tables
try:
    Base.metadata.create_all(engine)
    logger.info("Database tables created or already exist")
except Exception as e:
    logger.error(f"Failed to create database tables: {e}")

async def test_dialogue():
    """Test various dialogue scenarios with the AI agent"""

    # Set up Redis client
    redis_client = redis.from_url(REDIS_URL)
    set_redis_client(redis_client)

    # Test user ID
    test_user_id = 123456789

    # Create test user with profile and tasks for more realistic testing
    session = Session()
    user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not user:
        user = User(telegram_id=test_user_id, username="testuser", first_name="Test User", timezone="Europe/Moscow")
        session.add(user)
        session.commit()
        logger.info(f"Created test user with telegram_id: {test_user_id}")
    
    # Create or update user profile for realistic context
    from models import UserProfile
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            company="Тестовая компания",
            position="Разработчик",
            skills="Python, AI, разработка",
            interests="технологии, спорт",
            goals="улучшить навыки программирования"
        )
        session.add(profile)
        session.commit()
        logger.info("Created test user profile")
    
    # Create some test tasks
    existing_tasks = session.query(Task).filter_by(user_id=user.id).count()
    if existing_tasks == 0:
        from datetime import datetime, timedelta
        now = datetime.now()
        tasks_data = [
            ("Проверить почту", now + timedelta(hours=1), "pending"),
            ("Подготовить отчет", now + timedelta(days=1), "pending"),
            ("Позвонить клиенту", now - timedelta(hours=2), "pending"),  # overdue
        ]
        for title, reminder, status in tasks_data:
            task = Task(
                user_id=user.id,
                title=title,
                reminder_time=reminder,
                status=status
            )
            session.add(task)
        session.commit()
        logger.info("Created test tasks")
    
    session.close()

    # Test scenarios
    scenarios = [
        "Привет",
        "Добавь задачу: купить молоко завтра в 10 утра",
        "Покажи мои задачи",
        "Заверши задачу купить молоко",
        "Делегируй задачу купить молоко @testdelegate",
        "Что ты умеешь делать?",
        "Добавь задачу без уточнения времени"
    ]

    context = []  # Conversation history

    print("=== Starting Dialogue Test ===\n")

    for i, user_message in enumerate(scenarios, 1):
        print(f"Test Case {i}: {user_message}")
        print("-" * 50)

        try:
            # Call the AI function
            ai_response = await chat_with_ai(
                message=user_message,
                context=context,
                user_id=test_user_id
            )

            print(f"User: {user_message}")
            print(f"AI: {ai_response}")

            # Add to context for conversation continuity
            context.append({"role": "user", "content": user_message})
            context.append({"role": "assistant", "content": ai_response})

            # Verify AI follows prompts (basic check)
            if "привет" in user_message.lower() or "здравствуй" in user_message.lower():
                if "привет" in ai_response.lower() and ("задач" in ai_response.lower() or "помочь" in ai_response.lower()):
                    print("✓ AI correctly responded to greeting with task overview")
                else:
                    print("⚠ AI may not have responded to greeting properly")

            elif "задача" in user_message.lower() and "добав" in user_message.lower():
                if "добавил" in ai_response.lower() or "создал" in ai_response.lower():
                    print("✓ AI correctly added task")
                else:
                    print("⚠ AI may not have added task properly")

            elif "покажи" in user_message.lower() and "задач" in user_message.lower():
                if "задач" in ai_response.lower() or "список" in ai_response.lower():
                    print("✓ AI correctly listed tasks")
                else:
                    print("⚠ AI may not have listed tasks properly")

            elif "заверши" in user_message.lower():
                if "заверш" in ai_response.lower() or "выполн" in ai_response.lower():
                    print("✓ AI correctly completed task")
                else:
                    print("⚠ AI may not have completed task properly")

            elif "делегируй" in user_message.lower():
                if "делегир" in ai_response.lower() or "@" in ai_response:
                    print("✓ AI correctly delegated task")
                else:
                    print("⚠ AI may not have delegated task properly")

        except Exception as e:
            print(f"Error in test case {i}: {e}")
            logger.error(f"Error in dialogue test: {e}")

        print("\n" + "="*50 + "\n")

    # Clean up
    await redis_client.aclose()

if __name__ == "__main__":
    asyncio.run(test_dialogue())