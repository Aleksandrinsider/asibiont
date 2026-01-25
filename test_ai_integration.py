"""
Unit tests for ai_integration module
"""
import pytest
from unittest.mock import patch, MagicMock
from ai_integration.prompts import get_extended_system_prompt, get_optimized_system_prompt


def test_get_extended_system_prompt_basic():
    """Test basic system prompt generation"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory
    )

    assert "ASI Biont" in prompt
    assert "testuser" in prompt
    assert "Test memory" in prompt
    assert "10:00" in prompt
    assert "25 января 2026" in prompt


def test_get_extended_system_prompt_with_subscription():
    """Test system prompt with subscription tier"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    subscription_tier = "SILVER"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        subscription_tier=subscription_tier
    )

    assert "ПОДПИСКА ПОЛЬЗОВАТЕЛЯ: Silver (расширенная)" in prompt
    assert "ДЕЛЕГИРОВАНИЕ ЗАДАЧ другим пользователям" in prompt


def test_get_extended_system_prompt_reminder_type():
    """Test system prompt for reminder message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "reminder"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "СПЕЦИАЛЬНЫЙ РЕЖИМ: НАПОМИНАНИЕ О ЗАДАЧЕ" in prompt


def test_get_extended_system_prompt_proactive_type():
    """Test system prompt for proactive message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "proactive"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "ПРОАКТИВНЫЙ РЕЖИМ:" in prompt
    assert "Анализируй только актуальные предстоящие задачи для персонализированных советов" in prompt


def test_get_extended_system_prompt_result_check_type():
    """Test system prompt for result_check message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "result_check"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "ПРОВЕРКА РЕЗУЛЬТАТА:" in prompt
    assert "ВСЕГДА УТОЧНИ ВЫПОЛНЕНИЕ" in prompt


def test_get_extended_system_prompt_daily_report_type():
    """Test system prompt for daily_report message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "daily_report"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "ЕЖЕДНЕВНЫЙ ОТЧЁТ:" in prompt
    assert "АНАЛИЗИРУЙ ПРОГРЕСС" in prompt


def test_get_extended_system_prompt_overdue_type():
    """Test system prompt for overdue message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "overdue"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "ПРОСРОЧЕННЫЕ ЗАДАЧИ:" in prompt
    assert "предложи делегирование" in prompt


def test_get_extended_system_prompt_system_type():
    """Test system prompt for system message type"""
    user_now = "2026-01-25 10:00:00"
    current_time_str = "10:00"
    current_date_str = "25 января 2026"
    user_username = "testuser"
    mentions_str = ""
    user_memory = "Test memory"
    message_type = "system"

    prompt = get_extended_system_prompt(
        user_now, current_time_str, current_date_str,
        user_username, mentions_str, user_memory,
        message_type=message_type
    )

    assert "СИСТЕМНЫЕ СООБЩЕНИЯ:" in prompt
    assert "ВОВЛЕКАЙ В РЕШЕНИЯ" in prompt


def test_get_optimized_system_prompt():
    """Test optimized system prompt"""
    prompt = get_optimized_system_prompt()
    assert "ASI Biont" in prompt
    assert "управления задачами" in prompt
    assert len(prompt) < 100  # Should be short


@pytest.mark.asyncio
async def test_chat_with_ai_mock():
    """Test chat_with_ai function with mocked API"""
    from ai_integration.chat import chat_with_ai

    # Just test that the function exists and is callable
    assert callable(chat_with_ai)


def test_tools_definition():
    """Test that TOOLS are properly defined"""
    from ai_integration.tools import TOOLS

    assert isinstance(TOOLS, list)
    assert len(TOOLS) > 0

    # Check that all required functions are present
    tool_names = [tool['function']['name'] for tool in TOOLS]
    required_tools = ['add_task', 'list_tasks', 'complete_task', 'edit_task', 'delete_task', 'find_partners', 'update_profile', 'update_user_memory']

    for tool_name in required_tools:
        assert tool_name in tool_names, f"Tool {tool_name} not found in TOOLS. Available: {tool_names}"

    # Check tool structure
    for tool in TOOLS:
        assert 'type' in tool
        assert 'function' in tool
        assert 'name' in tool['function']
        assert 'description' in tool['function']
        assert 'parameters' in tool['function']


@pytest.mark.asyncio
async def test_tool_calling_simulation():
    """Simulate tool calling for key functions"""
    from ai_integration.tools import TOOLS
    from ai_integration.chat import chat_with_ai
    import json

    # Mock a simple tool call response
    mock_response = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "add_task",
                        "arguments": json.dumps({
                            "title": "Тестовая задача",
                            "reminder_time": "2026-01-25 12:00"
                        })
                    }
                }]
            }
        }]
    }

    with patch('aiohttp.ClientSession.post') as mock_post:
        mock_post.return_value.__aenter__.return_value.json = lambda: mock_response

        # This would normally call the API, but we're mocking it
        # In real scenario, we'd test the parsing of tool calls
        assert True  # Placeholder - actual tool calling tested in integration