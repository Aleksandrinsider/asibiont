# Prompt-related functions

from datetime import datetime, timedelta
import pytz


def get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory, context=None, intent=None):
    """Get extended system prompt for AI"""
    from improved_prompts_final import get_optimized_prompt_final
    return get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)


def replace_placeholders(content, user_now=None, current_time_str=None):
    """Replace placeholders like {{current_time}} with real values"""
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("Content must be a string")

    if not user_now:
        user_now = datetime.now(pytz.UTC)
    if not current_time_str:
        current_time_str = user_now.strftime("%H:%M")

    # Format date in Russian
    months = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

    content = content.replace("{{current_time}}", current_time_str)
    content = content.replace("{{current_date}}", current_date_str)
    content = content.replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d"))
    content = content.replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))

    return content


def get_optimized_system_prompt():
    """Get optimized system prompt (legacy fallback)"""
    return """Ты - личный ИИ-помощник и друг для управления жизнью. Веди живой, естественный диалог как настоящий человек."""
