"""
AI-powered testing of complex agent requests
Simulates real user interactions with AI responses
"""
import asyncio
import json
from unittest.mock import patch, MagicMock
from ai_integration.chat import chat_with_ai
from ai_integration.prompts import get_extended_system_prompt


async def simulate_ai_response(user_message, context=None, message_type=None, subscription_tier="SILVER"):
    """Simulate AI response for complex requests"""

    # Generate prompt to understand what AI would see
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="Пользователь активный, любит структурированные задачи",
        message_type=message_type,
        subscription_tier=subscription_tier
    )

    print(f"\n=== ТЕСТИРОВАНИЕ ЗАПРОСА ===")
    print(f"Пользователь: {user_message}")
    print(f"Контекст: {context}")
    print(f"Тип сообщения: {message_type}")
    print(f"Подписка: {subscription_tier}")

    # Simulate different AI responses based on request type
    if "удали все" in user_message.lower():
        # Delete all tasks - requires confirmation
        mock_response = {
            "choices": [{
                "message": {
                    "content": "⚠️ ВНИМАНИЕ! Ты действительно хочешь удалить ВСЕ свои задачи? Это действие НЕЛЬЗЯ отменить!\n\nПодтверди, пожалуйста, написав 'ДА, УДАЛИТЬ ВСЕ ЗАДАЧИ' или 'НЕТ'.",
                    "tool_calls": []
                }
            }]
        }
    elif "делегируй" in user_message.lower() or "delegate" in user_message.lower():
        # Delegation request
        if subscription_tier in ["BRONZE", "bronze"]:
            mock_response = {
                "choices": [{
                    "message": {
                        "content": "Для делегирования задач нужна подписка Silver или Gold. У тебя Bronze тариф. Хочешь узнать подробнее о тарифах?",
                        "tool_calls": []
                    }
                }]
            }
        else:
            mock_response = {
                "choices": [{
                    "message": {
                        "content": "Хорошо, делегирую задачу. Кому именно и с каким дедлайном?",
                        "tool_calls": [{
                            "function": {
                                "name": "delegate_task",
                                "arguments": json.dumps({
                                    "title": "Тестовая задача",
                                    "delegated_to_username": "testuser2",
                                    "reminder_time": "2026-01-26 12:00"
                                })
                            }
                        }]
                    }
                }]
            }
    elif "создай задачу" in user_message.lower():
        # Task creation
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Создаю задачу! Когда она должна быть выполнена?",
                    "tool_calls": [{
                        "function": {
                            "name": "add_task",
                            "arguments": json.dumps({
                                "title": "Новая задача",
                                "reminder_time": "2026-01-26 10:00"
                            })
                        }
                    }]
                }
            }]
        }
    elif "найди контакты" in user_message.lower() or "find partners" in user_message.lower():
        # Find partners
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Ищу подходящих людей для сотрудничества!",
                    "tool_calls": [{
                        "function": {
                            "name": "find_partners",
                            "arguments": json.dumps({
                                "skill": "программирование"
                            })
                        }]
                    }]
                }
            }]
        }
    elif "обнови профиль" in user_message.lower() or "update profile" in user_message.lower():
        # Profile update
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Обновляю твой профиль с новыми навыками!",
                    "tool_calls": [{
                        "function": {
                            "name": "update_profile",
                            "arguments": json.dumps({
                                "skills": "Python, AI",
                                "interests": "технологии"
                            })
                        }
                    }]
                }
            }]
        }
    else:
        # General response
        mock_response = {
            "choices": [{
                "message": {
                    "content": "Понял твой запрос. Что именно ты хочешь сделать?",
                    "tool_calls": []
                }
            }]
        }

    # Mock the API call
    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        try:
            result = await chat_with_ai(
                user_id=123,
                message=user_message,
                context=context or {"current_time": "2026-01-25 10:00:00"},
                message_type=message_type
            )

            print(f"Ответ ИИ: {result}")
            return result

        except Exception as e:
            print(f"Ошибка: {e}")
            return f"Ошибка: {e}"


async def run_complex_tests():
    """Run complex AI-powered tests"""

    test_cases = [
        # Confirmation-required actions
        ("Удали все мои задачи", None, None, "SILVER"),
        ("Да, удалить все задачи", None, None, "SILVER"),

        # Delegation with different subscriptions
        ("Делегируй задачу 'Тест' пользователю @john", None, None, "BRONZE"),
        ("Делегируй задачу 'Тест' пользователю @john", None, None, "SILVER"),

        # Complex multi-action requests
        ("Создай задачу 'Встреча с клиентом' и найди контакты по маркетингу", None, None, "GOLD"),
        ("Обнови мой профиль: навыки Python, интерес - спорт", None, None, "SILVER"),

        # Different message types
        ("Напоминание о задаче", None, "reminder", "SILVER"),
        ("Ежедневный отчет", None, "daily_report", "GOLD"),
        ("Просроченные задачи", None, "overdue", "SILVER"),
    ]

    for user_msg, ctx, msg_type, tier in test_cases:
        await simulate_ai_response(user_msg, ctx, msg_type, tier)
        print("-" * 50)


if __name__ == "__main__":
    asyncio.run(run_complex_tests())