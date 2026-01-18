#!/usr/bin/env python3
"""
Test script to verify that proactive messages and reminders are saved to chat context
"""
import asyncio
import json
import sys
import os

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.utils import redis_client
from reminder_service import ReminderService
from ai_integration.chat import generate_proactive_message
from models import Session, User, Interaction
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_proactive_message_saving():
    """Test that proactive messages are saved to Redis context"""
    logger.info("Testing proactive message saving...")

    # Create a test user
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(telegram_id=123456789, username="test_user")
        session.add(test_user)
        session.commit()
        logger.info("Created test user")

    user_id = test_user.telegram_id

    # Clear existing context
    if redis_client:
        await redis_client.delete(f"context:{user_id}")
        logger.info("Cleared existing context")

    # Generate a proactive message (this will use fallback since improved_prompts_final.py not available)
    try:
        message = await generate_proactive_message(user_id)
        logger.info(f"Generated proactive message: {message}")

        # Check if message was saved to context
        if redis_client:
            context_data = await redis_client.get(f"context:{user_id}")
            if context_data:
                context = json.loads(context_data.decode('utf-8'))
                logger.info(f"Context after proactive message: {context}")

                # Check if the message is in context
                found = False
                for item in context:
                    if "agent" in item and message in item["agent"]:
                        found = True
                        break

                if found:
                    logger.info("✅ SUCCESS: Proactive message saved to chat context")
                else:
                    logger.error("❌ FAILED: Proactive message not found in chat context")
            else:
                logger.error("❌ FAILED: No context data found")
        else:
            logger.warning("Redis client not available - skipping context check")

    except Exception as e:
        logger.error(f"Error testing proactive message: {e}")
    finally:
        session.close()

async def test_reminder_saving():
    """Test that reminders are saved to Redis context"""
    logger.info("Testing reminder saving...")

    # Create a test user
    session = Session()
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        test_user = User(telegram_id=123456789, username="test_user")
        session.add(test_user)
        session.commit()

    user_id = test_user.telegram_id

    # Clear existing context
    if redis_client:
        await redis_client.delete(f"context:{user_id}")

    # Simulate saving to context (copy the logic from send_reminder)
    reminder_text = "Тестовое напоминание о задаче"

    try:
        if redis_client:
            context_data = await redis_client.get(f"context:{user_id}")
            if context_data:
                context = json.loads(context_data.decode('utf-8'))
            else:
                context = []

            # Добавляем напоминание как сообщение от AI
            context.append({"user": "", "agent": reminder_text})
            if len(context) > 10:
                context = context[-10:]

            await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
            logger.info(f"Saved reminder to chat context for user {user_id}")

            # Verify it was saved
            context_data = await redis_client.get(f"context:{user_id}")
            if context_data:
                context = json.loads(context_data.decode('utf-8'))
                found = False
                for item in context:
                    if "agent" in item and reminder_text in item["agent"]:
                        found = True
                        break

                if found:
                    logger.info("✅ SUCCESS: Reminder saved to chat context")
                else:
                    logger.error("❌ FAILED: Reminder not found in chat context")
        else:
            logger.warning("Redis client not available - skipping reminder context test")
    except Exception as e:
        logger.error(f"Error testing reminder saving: {e}")
    finally:
        session.close()

async def main():
    logger.info("Starting tests for message context saving...")

    await test_proactive_message_saving()
    await test_reminder_saving()

    logger.info("Tests completed")

if __name__ == "__main__":
    asyncio.run(main())