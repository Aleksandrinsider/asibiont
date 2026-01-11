import os
import asyncio
from dotenv import load_dotenv
from models import User, Task, UserProfile, Interaction, UserRating, Subscription
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

# Set up minimal environment for testing
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_dialogue.db')
os.environ.setdefault('LOCAL', '1')
os.environ.setdefault('FREE_ACCESS_MODE', '1')  # Enable free access for testing
os.environ.setdefault('DEEPSEEK_API_KEY', 'test-key')  # Will fail but won't crash
os.environ.setdefault('TELEGRAM_TOKEN', 'test-token')
os.environ.setdefault('WEBHOOK_URL', 'http://localhost:8080')
os.environ.setdefault('ENCRYPTION_KEY', 'test-encryption-key-32-chars-long')
os.environ.setdefault('SESSION_SECRET', 'test-session-secret-32-chars')

from ai_integration import chat_with_ai, classify_user_intent
from models import Base, engine, Session, Task, User, UserProfile, Interaction, UserRating, Subscription
from datetime import datetime

# Create tables
Base.metadata.create_all(engine)

async def test_ai_responses():
    """Test AI responses with various scenarios"""

    test_user_id = 123456789

    # Clear any existing data in correct order (reverse of dependencies)
    session = Session()
    session.query(UserRating).delete()
    session.query(Subscription).delete()
    session.query(Interaction).delete()
    session.query(Task).delete()
    session.query(UserProfile).delete()
    session.query(User).delete()
    session.commit()
    session.close()

    # Create test user with active subscription
    session = Session()
    test_user = User(telegram_id=test_user_id, username="testuser")
    session.add(test_user)
    session.commit()

    # Create active subscription
    subscription = Subscription(
        user_id=test_user.id,
        status="active",
        plan="monthly",
        start_date=datetime.now(),
        end_date=datetime.now() + timedelta(days=30),
        login_count=1
    )
    session.add(subscription)
    session.commit()
    session.close()

    test_scenarios = [
        "Привет! Расскажи о себе",
        "Добавь задачу позвонить другу",
        "Добавь задачу проверить почту завтра в 10:00",
        "Покажи список задач",
        "Сделал позвонить другу",
        "Удали все задачи",
        "Живу в Москве, работаю в IT",
        "Найди людей с похожими интересами",
        "Создай подписку",
        "Проверь статус подписки",
    ]

    print("🧪 Тестирование AI ответов\n")

    for i, message in enumerate(test_scenarios, 1):
        print(f"\n{i}. Тест: '{message}'")
        print("-" * 50)

        try:
            # Classify intent first
            intent = classify_user_intent(message, "")
            print(f"Распознанный intent: {intent['type']} (уверенность: {intent['confidence']:.2f})")

            # Get AI response
            response = await chat_with_ai(message, context=[], user_id=test_user_id)
            print(f"AI ответ: {response}")

        except Exception as e:
            print(f"❌ Ошибка: {str(e)}")

        print()

if __name__ == "__main__":
    asyncio.run(test_ai_responses())