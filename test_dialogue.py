import asyncio
import aiohttp
import json
import logging
from datetime import datetime
import os
import sys

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile

# Enable free access for testing
import config
config.FREE_ACCESS_MODE = True

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def generate_user_response(agent_response, conversation_history, user_profile):
    """Generate next user message based on agent response using AI"""
    try:
        system_prompt = f"""Ты - пользователь чат-бота для управления задачами. Твоя роль - вести естественный диалог с агентом.

Твой профиль:
- Имя: {user_profile.get('name', 'Пользователь')}
- Интересы: {user_profile.get('interests', 'работа, спорт, здоровье')}
- Город: {user_profile.get('city', 'Москва')}

Правила поведения:
1. Веди естественный разговор, как реальный пользователь
2. Создавай задачи с различными временными указаниями (абсолютное время, относительное, без времени)
3. Отвечай на вопросы агента о времени, если он уточняет
4. Иногда выполняй задачи, проверяй список задач
5. Задавай вопросы о функциях бота
6. Иногда повторяй сообщения, чтобы проверить дублирование
7. Проявляй интерес к социальным функциям (партнеры, профиль)

История разговора:
{conversation_history}

Последний ответ агента: {agent_response}

Сгенерируй следующее сообщение пользователя. Будь естественным, используй русский язык.
Если агент уточняет время - укажи его.
Если разговор подходит к концу - можешь завершить или продолжить."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"На основе ответа агента '{agent_response}' сгенерируй следующее сообщение пользователя в диалоге."}
        ]

        async with aiohttp.ClientSession() as session:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 200
            }

            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    result = await response.json()
                    user_message = result["choices"][0]["message"]["content"].strip()
                    # Remove quotes if present
                    user_message = user_message.strip('"')
                    return user_message
                else:
                    logger.error(f"AI response failed: {response.status}")
                    return "расскажи о функциях бота"

    except Exception as e:
        logger.error(f"Error generating user response: {e}")
        return "что ты умеешь?"

async def run_dialogue_test():
    """Run dialogue test between agent and simulated user"""
    logger.info("Starting dialogue test...")

    # Test user profile
    user_profile = {
        'name': 'Тестовый Пользователь',
        'interests': 'работа, спорт, путешествия, здоровье',
        'city': 'Москва'
    }

    # Initialize user in database
    session = Session()
    try:
        test_user = session.query(User).filter_by(telegram_id=999999).first()
        if not test_user:
            test_user = User(telegram_id=999999, timezone="Europe/Moscow")
            session.add(test_user)
            session.commit()

        # Create or update profile
        profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
        if not profile:
            profile = UserProfile(user_id=test_user.id)
            session.add(profile)

        profile.interests = user_profile['interests']
        profile.city = user_profile['city']
        session.commit()

        user_id = test_user.telegram_id

    finally:
        session.close()

    # Initial user messages to start conversation
    initial_messages = [
        "привет, мне нужно провести встречу через 15 минут",
        "напомни мне проверить почту в 10 утра завтра",
        "нужно сходить в магазин",
        "что ты умеешь?",
        "покажи мои задачи"
    ]

    conversation_history = []
    max_turns = 10

    for turn in range(max_turns):
        logger.info(f"\n--- Turn {turn + 1} ---")

        # Get user message
        if turn < len(initial_messages):
            user_message = initial_messages[turn]
        else:
            # Generate user message using AI
            history_text = "\n".join([f"{'Пользователь' if i%2==0 else 'Агент'}: {msg}" for i, msg in enumerate(conversation_history[-6:])])
            user_message = await generate_user_response(
                conversation_history[-1] if conversation_history else "Начнем разговор",
                history_text,
                user_profile
            )

        logger.info(f"User: {user_message}")
        conversation_history.append(f"User: {user_message}")

        # Process message with agent
        try:
            agent_response = await chat_with_ai(user_message, context=None, user_id=user_id)
            logger.info(f"Agent: {agent_response}")
            conversation_history.append(f"Agent: {agent_response}")

            # Check for duplicates in response
            if conversation_history.count(f"Agent: {agent_response}") > 1:
                logger.warning(f"DUPLICATE DETECTED: Agent sent same response twice: {agent_response}")

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            break

        # Small delay between turns
        await asyncio.sleep(1)

        # Check tasks created
        session = Session()
        try:
            user_obj = session.query(User).filter_by(telegram_id=user_id).first()
            if user_obj:
                tasks = session.query(Task).filter_by(user_id=user_obj.id).all()
                logger.info(f"Current tasks count: {len(tasks)}")
                for task in tasks[-3:]:  # Show last 3 tasks
                    logger.info(f"  - {task.title} (ID: {task.id}, status: {task.status})")
        finally:
            session.close()

    # Final check
    logger.info("\n--- Final State ---")
    session = Session()
    try:
        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        if user_obj:
            tasks = session.query(Task).filter_by(user_id=user_obj.id).all()
            logger.info(f"Total tasks created: {len(tasks)}")
            for task in tasks:
                logger.info(f"  - {task.title} (reminder: {task.reminder_time}, status: {task.status})")

            # Check for duplicates
            task_titles = [t.title for t in tasks]
            duplicates = set([x for x in task_titles if task_titles.count(x) > 1])
            if duplicates:
                logger.warning(f"DUPLICATE TASKS FOUND: {duplicates}")

    finally:
        session.close()

    logger.info("Dialogue test completed.")

if __name__ == "__main__":
    asyncio.run(run_dialogue_test())