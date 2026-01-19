#!/usr/bin/env python3
"""
Comprehensive test script for AI agent in combat conditions
Tests all agent capabilities including DB operations, Redis, and full dialogues
"""

import asyncio
import sys
import os
import json
import re
sys.path.append(os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task
from ai_integration.utils import redis_client
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def comprehensive_test():
    """Comprehensive test of all agent capabilities"""

    # Setup test user
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=1001).first()
        if not user:
            user = User(telegram_id=1001, username="testuser")
            session.add(user)
            session.commit()
            logger.info("Created test user")

        # Clear existing profile and tasks for clean test
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            session.delete(profile)
            session.commit()

        tasks = session.query(Task).filter_by(user_id=user.id).all()
        for task in tasks:
            session.delete(task)
        session.commit()

        # Create minimal profile
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            interests="спорт"
        )
        session.add(profile)
        session.commit()

        user_id = user.telegram_id

        # Clear Redis context
        if redis_client:
            await redis_client.delete(f"context:{user_id}")
            await redis_client.delete(f"last_task_id:{user_id}")

        logger.info("=== STARTING COMPREHENSIVE AGENT TEST ===")

        # Test scenarios with expected DB changes
        test_scenarios = [
            {
                "name": "Greeting and profile completion",
                "messages": ["Привет! Я разработчик из Москвы, люблю спорт и программирование"],
                "expected_db_changes": "profile_updated",
                "check_redis": True
            },
            {
                "name": "Task creation with clarification",
                "messages": ["Создай задачу на завтра", "В 10 утра подготовить презентацию"],
                "expected_db_changes": "task_created",
                "check_redis": True
            },
            {
                "name": "Task listing",
                "messages": ["Покажи мои задачи"],
                "expected_db_changes": None,
                "check_redis": False
            },
            {
                "name": "Task completion",
                "messages": ["Я выполнил задачу о презентации"],
                "expected_db_changes": "task_completed",
                "check_redis": False
            },
            {
                "name": "Partner search",
                "messages": ["Найди партнеров для спорта"],
                "expected_db_changes": None,
                "check_redis": False
            },
            {
                "name": "Profile update",
                "messages": ["Обнови мой профиль: я знаю Python и JavaScript"],
                "expected_db_changes": "profile_updated",
                "check_redis": False
            },
            {
                "name": "Advice request",
                "messages": ["Как лучше организовать свое время?"],
                "expected_db_changes": None,
                "check_redis": False
            },
            {
                "name": "Task deletion",
                "messages": ["Удалить все задачи"],
                "expected_db_changes": "tasks_deleted",
                "check_redis": False
            }
        ]

        context = []
        all_responses = []

        for scenario in test_scenarios:
            logger.info(f"\n=== Testing: {scenario['name']} ===")

            # Check DB state before
            db_state_before = check_db_state(session, user.id)

            for message in scenario["messages"]:
                logger.info(f"User: {message}")

                # Check Redis before
                redis_state_before = await check_redis_state(user_id) if scenario["check_redis"] else None

                response = await chat_with_ai(message, context.copy(), user_id)
                logger.info(f"Agent: {response[:200]}...")

                # Validate response against requirements
                issues = validate_response(response, message)
                if issues:
                    logger.warning(f"Issues: {issues}")

                # Check DB state after
                db_state_after = check_db_state(session, user.id)
                if scenario["expected_db_changes"]:
                    changes = compare_db_states(db_state_before, db_state_after, scenario["expected_db_changes"])
                    if changes:
                        logger.info(f"✓ DB changes detected: {changes}")
                    else:
                        logger.error(f"✗ Expected DB changes not found for {scenario['expected_db_changes']}")

                # Check Redis after
                if scenario["check_redis"]:
                    redis_state_after = await check_redis_state(user_id)
                    redis_changes = compare_redis_states(redis_state_before, redis_state_after)
                    if redis_changes:
                        logger.info(f"✓ Redis changes: {redis_changes}")

                # Add to context
                context.append({"user": message, "agent": response})
                all_responses.append({"scenario": scenario["name"], "user": message, "agent": response})

                # Small delay between messages
                await asyncio.sleep(0.5)

        # Final summary
        logger.info("\n=== TEST SUMMARY ===")
        logger.info(f"Total scenarios tested: {len(test_scenarios)}")
        logger.info(f"Total messages: {sum(len(s['messages']) for s in test_scenarios)}")

        # Check final DB state
        final_db_state = check_db_state(session, user.id)
        logger.info(f"Final DB state: {final_db_state}")

        # Save test results
        with open("test_results.json", "w", encoding="utf-8") as f:
            json.dump(all_responses, f, ensure_ascii=False, indent=2)

        logger.info("Test results saved to test_results.json")

    finally:
        session.close()

def check_db_state(session, user_id):
    """Check current DB state for user"""
    profile = session.query(UserProfile).filter_by(user_id=user_id).first()
    tasks = session.query(Task).filter_by(user_id=user_id).all()

    return {
        "profile": {
            "city": profile.city if profile else None,
            "company": profile.company if profile else None,
            "position": profile.position if profile else None,
            "skills": profile.skills if profile else None,
            "interests": profile.interests if profile else None,
            "goals": profile.goals if profile else None
        } if profile else None,
        "tasks_count": len(tasks),
        "tasks": [{"id": t.id, "title": t.title, "status": t.status, "reminder_time": str(t.reminder_time)} for t in tasks]
    }

async def check_redis_state(user_id):
    """Check Redis state for user"""
    if not redis_client:
        return None

    context = await redis_client.get(f"context:{user_id}")
    last_task = await redis_client.get(f"last_task_id:{user_id}")

    return {
        "context_exists": context is not None,
        "context_length": len(context) if context else 0,
        "last_task_exists": last_task is not None
    }

def compare_db_states(before, after, expected_change):
    """Compare DB states and return changes"""
    changes = []

    if expected_change == "profile_updated":
        if before["profile"] != after["profile"]:
            changes.append("profile_updated")
    elif expected_change == "task_created":
        if after["tasks_count"] > before["tasks_count"]:
            changes.append(f"task_created: {after['tasks_count'] - before['tasks_count']} new tasks")
    elif expected_change == "task_completed":
        completed_tasks = [t for t in after["tasks"] if t["status"] == "completed"]
        if completed_tasks:
            changes.append(f"tasks_completed: {len(completed_tasks)}")
    elif expected_change == "tasks_deleted":
        if after["tasks_count"] < before["tasks_count"]:
            changes.append(f"tasks_deleted: {before['tasks_count'] - after['tasks_count']} removed")

    return changes

def compare_redis_states(before, after):
    """Compare Redis states"""
    changes = []
    if before and after:
        if not before["context_exists"] and after["context_exists"]:
            changes.append("context_created")
        if after["context_length"] > before["context_length"]:
            changes.append(f"context_grew: +{after['context_length'] - before['context_length']}")
        if not before["last_task_exists"] and after["last_task_exists"]:
            changes.append("last_task_set")
    return changes

def validate_response(response, user_message):
    """Validate response against requirements"""
    issues = []

    # Check for forbidden formatting
    forbidden_patterns = [r'\d+\.', r'[-*•]', r'\*\*.*?\*\*', r'"[^"]*"']
    for pattern in forbidden_patterns:
        if re.search(pattern, response):
            issues.append(f"Forbidden pattern: {pattern}")

    # Check length
    paragraphs = response.split('\n\n')
    if len(paragraphs) > 4:
        issues.append("Too many paragraphs (>4)")
    if len(response) < 20:
        issues.append("Response too short")

    # Check for banned phrases
    banned_phrases = [
        "разбить на этапы", "составить список", "начать с простого",
        "сделать шаг за шагом", "выделить время", "организовать/спланировать"
    ]
    for phrase in banned_phrases:
        if phrase.lower() in response.lower():
            issues.append(f"Banned phrase: '{phrase}'")

    # Check for practical advice (basic check)
    if "как" in user_message.lower() and len(response.split()) < 10:
        issues.append("Advice response too short")

    return issues

if __name__ == "__main__":
    asyncio.run(comprehensive_test())