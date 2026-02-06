import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_integration.chat import chat_with_ai
from models import init_db, Session, User, UserProfile, Base, engine
from config import DATABASE_URL

async def test_agent_scenarios():
    """Test agent with various scenarios including confirmations"""

    print(f"DATABASE_URL: {DATABASE_URL}")

    # Mock user_id for testing
    user_id = 123456789

    # Initialize database
    try:
        Base.metadata.create_all(engine)
        print("✅ Database tables created successfully")
    except Exception as e:
        print(f"❌ Failed to create database tables: {e}")
        return

    # Create mock user if not exists
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(
            telegram_id=user_id,
            username="testuser",
            first_name="Test",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()

        # Create profile
        profile = UserProfile(
            user_id=user.id,
            interests="программирование, спорт, чтение",
            goals="улучшить навыки Python, бегать марафон",
            city="Москва"
        )
        session.add(profile)
        session.commit()

    # Don't close the session yet, pass it to chat_with_ai

    # Test scenarios (reduced for testing)
    scenarios = [
        # Basic task creation
        "Создай задачу купить молоко завтра в 9 утра",

        # Task listing
        "Покажи мои задачи",

        # Confirmation of agent's suggestion
        "Да, звучит хорошо",

        # Task completion
        "Я купил молоко, готово",

        # Finding partners
        "Найди единомышленников для спорта",

        # Profile update
        "Обнови мой профиль: люблю бег и программирование",

        # Random confirmation
        "Окей, согласен",

        # Task details
        "Расскажи подробнее о задаче с молоком",
    ]

    print("🧪 Starting agent testing with various scenarios...\n")

    context = []  # To maintain conversation context

    for i, user_message in enumerate(scenarios, 1):
        print(f"🔹 Test {i}: '{user_message}'")
        try:
            response = await chat_with_ai(
                message=user_message,
                user_id=user_id,
                context=context,
                db_session=session
            )

            agent_response = response.get('response', 'No response')
            print(f"🤖 Agent: {agent_response[:200]}..." if len(agent_response) > 200 else f"🤖 Agent: {agent_response}")

            # Add to context for next message
            context.append({"role": "user", "content": user_message})
            context.append({"role": "assistant", "content": agent_response})

            # Keep context manageable
            if len(context) > 20:
                context = context[-20:]

            print("✅ Success\n")

        except Exception as e:
            print(f"❌ Error: {e}\n")

        # Small delay between tests
        await asyncio.sleep(0.5)

    session.close()
    print("🎉 Testing completed!")

if __name__ == "__main__":
    asyncio.run(test_agent_scenarios())