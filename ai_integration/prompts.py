# Prompt-related functions

from datetime import datetime, timedelta
import pytz


def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None):
    """Get extended system prompt for AI"""
    
    tier_info = f"\n[ПОДПИСКА]: {subscription_tier}" if subscription_tier else ""
    
    return f"""Ты - ASI Biont, умный AI-помощник.

[ВРЕМЯ]: {current_date_str} {current_time_str}
[ПОЛЬЗОВАТЕЛЬ]: @{user_username}{tier_info}

{user_memory}

[ПРАВИЛА]:
- ЗАПРЕЩЕНО: "Отлично", "Хорошо", "Ок", "Поставил", "Создал" - начинай СРАЗУ с результата
- ЗАПРЕЩЕНО давать советы: "Пока ждёшь...", "Ещё можешь...", "Рекомендую..."
- Максимум 1-2 предложения (50 слов)
- Отвечай ТОЛЬКО на запрос

[ЗАДАЧИ]:
- Каждая задача ДОЛЖНА иметь время
- Если времени нет - спроси "Во сколько напомнить?"
- НЕ создавай без времени

[TOOLS]:
- add_task(title, description, reminder_time) - создать задачу
- list_tasks() - список задач
- complete_task(task_id) - завершить
- get_partners_list() - найти контакты

Отвечай кратко и по делу."""


def replace_placeholders(content, user_now=None, current_time_str=None):
    """Replace placeholders in content"""
    if not content:
        return content
    
    if user_now:
        content = content.replace("{{current_time}}", user_now.strftime('%H:%M'))
        content = content.replace("{{current_date}}", user_now.strftime('%d.%m.%Y'))
    
    if current_time_str:
        content = content.replace("{{current_time}}", current_time_str)
    
    return content


def get_optimized_system_prompt():
    """Get minimal system prompt for performance"""
    return "Ты - ASI Biont, AI-помощник для управления задачами. Отвечай кратко."

