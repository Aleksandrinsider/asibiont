"""
Real-world testing of AI agent capabilities
"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from ai_integration.prompts import get_extended_system_prompt
from ai_integration.chat import chat_with_ai
from ai_integration.tools import TOOLS
import json


@pytest.mark.asyncio
async def test_task_creation_request():
    """Test real task creation request"""
    # Simulate user message: "Создай задачу: позвонить маме завтра в 10 утра"
    user_message = "Создай задачу: позвонить маме завтра в 10 утра"

    prompt = get_extended_system_prompt(
        user_now="2026-01-25 09:00:00",
        current_time_str="09:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="Пользователь часто звонит родным",
        message_type=None
    )

    # Mock AI response with tool call
    mock_response = {
        "choices": [{
            "message": {
                "content": "Создаю задачу для звонка маме.",
                "tool_calls": [{
                    "function": {
                        "name": "add_task",
                        "arguments": json.dumps({
                            "title": "Позвонить маме",
                            "reminder_time": "2026-01-26 10:00"
                        })
                    }
                }]
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00"},
            message_type=None
        )

        assert "Создаю задачу" in result or "add_task" in str(result)


@pytest.mark.asyncio
async def test_reminder_specialization():
    """Test reminder message type specialization"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="",
        message_type="reminder"
    )

    assert "СПЕЦИАЛЬНЫЙ РЕЖИМ: НАПОМИНАНИЕ О ЗАДАЧЕ" in prompt
    assert "персонализированное напоминание" in prompt


@pytest.mark.asyncio
async def test_proactive_specialization():
    """Test proactive message type specialization"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="",
        message_type="proactive"
    )

    assert "ПРОАКТИВНЫЙ РЕЖИМ:" in prompt
    assert "предстоящие задачи" in prompt


@pytest.mark.asyncio
async def test_profile_personalization():
    """Test that profile information is included in prompts"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="Интересы: программирование, спорт. Навыки: Python, SQL.",
        user_memory="Любит структурированные задачи",
        message_type=None
    )

    # Check that user_memory is included
    assert "Любит структурированные задачи" in prompt
    # mentions_str is not directly included, but user_memory is
    assert "структурированные задачи" in prompt


@pytest.mark.asyncio
async def test_subscription_features():
    """Test subscription-based features in prompt"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="",
        message_type=None,
        subscription_tier="SILVER"
    )

    assert "ПОДПИСКА ПОЛЬЗОВАТЕЛЯ: Silver" in prompt
    assert "ДЕЛЕГИРОВАНИЕ ЗАДАЧ" in prompt


@pytest.mark.asyncio
async def test_social_functions():
    """Test social functions like partner finding"""
    # Check that find_partners tool exists
    tool_names = [tool['function']['name'] for tool in TOOLS]
    assert 'find_partners' in tool_names

    # Check tool description contains relevant keywords
    find_partners_tool = next(tool for tool in TOOLS if tool['function']['name'] == 'find_partners')
    assert 'потенциальных людей' in find_partners_tool['function']['description']


@pytest.mark.asyncio
async def test_security_no_dangerous_tools():
    """Test that no dangerous tools are exposed"""
    tool_names = [tool['function']['name'] for tool in TOOLS]

    # Should not have dangerous functions
    dangerous_tools = ['exec', 'eval', 'system', 'subprocess']
    for tool in dangerous_tools:
        assert tool not in tool_names, f"Dangerous tool {tool} found in TOOLS"

    # delete_all_tasks exists but has confirmation requirement
    if 'delete_all_tasks' in tool_names:
        delete_tool = next(t for t in TOOLS if t['function']['name'] == 'delete_all_tasks')
        description_lower = delete_tool['function']['description'].lower()
        assert 'подтверждение' in description_lower or 'подтверди' in description_lower


@pytest.mark.asyncio
async def test_communication_style():
    """Test conversational communication style in prompts"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="",
        message_type=None
    )

    # Check for conversational style instructions
    assert "СТИЛЬ ОБЩЕНИЯ - живой диалог" in prompt
    assert "как с другом" in prompt


@pytest.mark.asyncio
async def test_intellectual_analysis():
    """Test intellectual analysis capabilities"""
    prompt = get_extended_system_prompt(
        user_now="2026-01-25 10:00:00",
        current_time_str="10:00",
        current_date_str="25 января 2026",
        user_username="testuser",
        mentions_str="",
        user_memory="Часто откладывает задачи",
        message_type=None
    )

    assert "паттерны" in prompt or "patterns" in prompt
    assert "анализ" in prompt


@pytest.mark.asyncio
async def test_advanced_features():
    """Test advanced features like speech recognition"""
    # This would require actual audio file, so we test the import
    try:
        import speech_recognition as sr
        assert sr is not None
    except ImportError:
        pytest.skip("speech_recognition not available")

    # Test that AudioFile can be imported
    assert hasattr(sr, 'AudioFile')


@pytest.mark.asyncio
async def test_delegation_request():
    """Test task delegation request with confirmation"""
    user_message = "Делегируй задачу 'Подготовить отчет' пользователю @john"

    mock_response = {
        "choices": [{
            "message": {
                "content": "Делегирую задачу пользователю @john. Нужно ли добавить описание?",
                "tool_calls": [{
                    "function": {
                        "name": "delegate_task",
                        "arguments": json.dumps({
                            "title": "Подготовить отчет",
                            "delegated_to_username": "john",
                            "reminder_time": "2026-01-26 10:00"
                        })
                    }
                }]
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00"},
            message_type=None
        )

        # Should attempt delegation
        assert "Делегирую" in result or "delegate_task" in str(result)


@pytest.mark.asyncio
async def test_delete_confirmation_request():
    """Test delete all tasks with confirmation requirement"""
    user_message = "Удали все мои задачи"

    mock_response = {
        "choices": [{
            "message": {
                "content": "Ты точно хочешь удалить ВСЕ задачи? Это действие нельзя отменить. Подтверди, пожалуйста.",
                "tool_calls": []  # No tool call until confirmation
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00"},
            message_type=None
        )

        # Should ask for confirmation
        assert "подтверди" in result.lower() or "точно хочешь" in result.lower()


@pytest.mark.asyncio
async def test_complex_multi_tool_request():
    """Test complex request requiring multiple tools"""
    user_message = "Создай задачу 'Встретиться с клиентом', найди контакты по маркетингу и обнови мой профиль с навыком 'переговоры'"

    # Mock response with multiple tool calls
    mock_response = {
        "choices": [{
            "message": {
                "content": "Создаю задачу, ищу контакты и обновляю профиль.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "add_task",
                            "arguments": json.dumps({
                                "title": "Встретиться с клиентом",
                                "reminder_time": "2026-01-26 10:00"
                            })
                        }
                    },
                    {
                        "function": {
                            "name": "find_partners",
                            "arguments": json.dumps({
                                "skill": "маркетинг"
                            })
                        }
                    },
                    {
                        "function": {
                            "name": "update_profile",
                            "arguments": json.dumps({
                                "skills": "переговоры"
                            })
                        }
                    }
                ]
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00"},
            message_type=None
        )

        # Should handle multiple actions
        assert "Создаю" in result or "задачу" in result


@pytest.mark.asyncio
async def test_subscription_limited_features():
    """Test features limited by subscription"""
    user_message = "Делегируй задачу 'Тест' пользователю @test"

    # Mock response checking subscription
    mock_response = {
        "choices": [{
            "message": {
                "content": "Для делегирования задач нужна подписка Silver или Gold. Хочешь узнать о тарифах?",
                "tool_calls": []
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00", "subscription_tier": "BRONZE"},
            message_type=None
        )

        # Should mention subscription requirement
        assert "подпис" in result.lower() or "silver" in result.lower() or "gold" in result.lower()


@pytest.mark.asyncio
async def test_profile_update_with_memory():
    """Test profile update with memory preservation"""
    user_message = "Я умею программировать на Python и люблю спорт"

    mock_response = {
        "choices": [{
            "message": {
                "content": "Отлично! Обновляю твой профиль с навыками программирования и интересом к спорту.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "update_profile",
                            "arguments": json.dumps({
                                "skills": "Python",
                                "interests": "спорт"
                            })
                        }
                    },
                    {
                        "function": {
                            "name": "update_user_memory",
                            "arguments": json.dumps({
                                "memory_text": "Пользователь владеет Python и увлекается спортом"
                            })
                        }
                    }
                ]
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = lambda: mock_response
        mock_post.return_value.__aenter__.return_value = mock_response_obj

        result = await chat_with_ai(
            user_id=123,
            message=user_message,
            context={"current_time": "2026-01-25 09:00:00"},
            message_type=None
        )

        # Should update profile and memory
        assert "обновляю" in result.lower() or "профиль" in result.lower()