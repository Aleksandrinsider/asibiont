#!/usr/bin/env python3
"""
Test script for AI agent in combat conditions
Tests the agent against the requirements from my file
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_agent():
    """Test the agent with various scenarios"""

    # Create test user if not exists
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=1001).first()
        if not user:
            user = User(telegram_id=1001, username="testuser")
            session.add(user)
            session.commit()
            logger.info("Created test user")

        # Create profile with some data
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                city="Москва",
                company="Тестовая компания",
                position="Разработчик",
                skills="программирование, Python",
                interests="спорт, чтение",
                goals="похудеть, научиться новому языку"
            )
            session.add(profile)
            session.commit()
            logger.info("Created test profile")

        user_id = user.telegram_id

        # Test scenarios
        test_cases = [
            "Привет! Расскажи о себе",
            "Создай задачу: подготовить презентацию к завтрашнему утру",
            "Покажи мои задачи",
            "Найди партнеров для спорта",
            "Обнови мой профиль: я из Санкт-Петербурга",
            "Как мне лучше организовать свое время?",
            "У меня есть свободное время вечером, что посоветуешь?",
            "Я сделал задачу о презентации",
            "Удалить все задачи",
            "Какой совет по питанию для похудения?"
        ]

        context = []

        for i, message in enumerate(test_cases, 1):
            logger.info(f"\n--- Test {i}: {message} ---")
            try:
                response = await chat_with_ai(message, context, user_id)
                logger.info(f"Response: {response}")

                # Add to context
                if response is not None:
                    context.append({"user": message, "agent": response})

                # Check against requirements (basic checks)
                if response is not None:
                    check_response_requirements(response, message)

            except Exception as e:
                logger.error(f"Error in test {i}: {e}")

    finally:
        session.close()

def check_response_requirements(response, user_message):
    """Basic checks against requirements"""

    issues = []

    # Check for forbidden formatting
    forbidden_patterns = ['1.', '2.', '3.', '-', '*', '**', '__']
    if any(pattern in response for pattern in forbidden_patterns):
        issues.append("Использует запрещенные форматы (нумерация, списки, жирный)")

    # Check length (should be 3-4 paragraphs max, but not too short)
    paragraphs = response.split('\n\n')
    if len(paragraphs) > 4:
        issues.append("Слишком много абзацев (>4)")
    elif len(response) < 50:
        issues.append("Ответ слишком короткий")

    # Check for generic phrases
    generic_phrases = [
        "разбить на этапы", "составить список", "начать с простого",
        "сделать шаг за шагом", "выделить время", "организовать/спланировать"
    ]
    for phrase in generic_phrases:
        if phrase.lower() in response.lower():
            issues.append(f"Использует банальную фразу: '{phrase}'")

    # Check for practical advice
    if "конкретн" not in response.lower() and "практич" not in response.lower() and len(response) > 100:
        # This is a rough check - responses should be practical
        pass

    if issues:
        logger.warning(f"Issues found: {', '.join(issues)}")
    else:
        logger.info("✓ Response looks good")

if __name__ == "__main__":
    asyncio.run(test_agent())