"""
Test agent dialogue with real requests
"""
import os
import sys
import asyncio
import logging
from datetime import datetime
import pytz

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import from project
from models import Session, User, Task
from ai_integration.chat import chat_with_ai

# Test user ID
TEST_USER_ID = 1239774408  # aleksandrinsider

async def test_dialogue():
    """Test agent with real dialogue"""
    
    test_cases = [
        "напомни через 5 минут заказать продукты",
        "напомни через 5 минут заказать продукты",  # Повторный запрос
        "покажи мои задачи",
        "создай задачу позвонить маме завтра в 10:00",
        "покажи мои контакты",
    ]
    
    logger.info("=" * 80)
    logger.info("TESTING AGENT DIALOGUE")
    logger.info("=" * 80)
    
    for i, message in enumerate(test_cases, 1):
        logger.info(f"\n{'='*80}")
        logger.info(f"TEST CASE {i}: {message}")
        logger.info(f"{'='*80}")
        
        try:
            response = await chat_with_ai(message, TEST_USER_ID)
            
            logger.info(f"\n✅ RESPONSE {i}:")
            logger.info(f"{response}")
            logger.info(f"\n{'='*80}")
            
            # Check for forbidden phrases
            forbidden = [
                "Отлично,",
                "Хорошо,",
                "Okay,",
                "Давай",
                "Сейчас посмотрю",
                "Пока ждёшь",
                "можешь принять",
                "или посмотреть",
            ]
            
            violations = [f for f in forbidden if f.lower() in response.lower()]
            if violations:
                logger.warning(f"⚠️  PROMPT VIOLATIONS DETECTED: {violations}")
            
            # Check for time truncation
            if "в 21:" in response and "в 21:0" not in response and "в 21:1" not in response:
                logger.warning(f"⚠️  TIME TRUNCATION DETECTED: 'в 21:'")
            
            # Wait between requests
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ ERROR in test case {i}: {e}", exc_info=True)
    
    logger.info(f"\n{'='*80}")
    logger.info("CHECKING DATABASE AFTER TESTS")
    logger.info(f"{'='*80}")
    
    # Check database
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.created_at.desc()).limit(10).all()
            logger.info(f"\n📊 Latest tasks for user:")
            for task in tasks:
                tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                reminder = task.reminder_time.astimezone(tz).strftime('%d.%m.%Y %H:%M') if task.reminder_time else "No reminder"
                logger.info(f"  - {task.title} | Reminder: {reminder} | Status: {task.status}")
        else:
            logger.warning("User not found in database")
    except Exception as e:
        logger.error(f"Error checking database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    # Check environment
    if os.getenv("LOCAL") == "1":
        logger.info("Running in LOCAL mode")
    else:
        logger.info("Running in PRODUCTION mode")
    
    # Run tests
    asyncio.run(test_dialogue())
