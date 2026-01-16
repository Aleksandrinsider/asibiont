# Prompt-related functions

from datetime import datetime, timedelta
import pytz


def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None):
    """Get extended system prompt for AI"""
    
    # Информация о подписке
    tier_info = ""
    if subscription_tier:
        tier_name = {
            'BRONZE': 'Bronze (базовая)',
            'SILVER': 'Silver (расширенная)',
            'GOLD': 'Gold (премиум)'
        }.get(subscription_tier, subscription_tier)
        
        tier_info = f"\n💎 ПОДПИСКА ПОЛЬЗОВАТЕЛЯ: {tier_name}"
        
        # Важно: не рекомендуй повышение тарифа тем, у кого уже Bronze или Silver
        if subscription_tier in ['BRONZE', 'SILVER']:
            tier_info += "\n⚠️ У пользователя уже есть активная подписка. НЕ предлагай и НЕ рекомендуй переход на другой тариф."
    
    # Basic system prompt when improved_prompts_final not available
    return f"""Ты - ASI Biont, умный AI-помощник для управления задачами и повышения продуктивности.

🕐 ТЕКУЩЕЕ ВРЕМЯ И ДАТА:
{current_date_str} {current_time_str}

👤 ПОЛЬЗОВАТЕЛЬ: @{user_username}
{tier_info}

{user_memory}

📋 ТВОИ ВОЗМОЖНОСТИ:
- Создавать, редактировать и удалять задачи
- Устанавливать напоминания
- Делегировать задачи другим пользователям
- Искать партнёров по интересам
- Обновлять профиль
- Генерировать идеи

💡 ПРАВИЛА ОБЩЕНИЯ:
- Отвечай естественно и по-дружески, как настоящий человек
- Будь проактивным: предлагай решения и контакты без запроса
- Используй инструменты когда нужно для действий
- Не дублируй информацию из user_memory
- Давай конкретные практические советы без общих фраз
- Если видишь полезный контакт - сразу предлагай его
- Отвечай кратко но по существу, без лишних слов
- Адаптируйся к ситуации пользователя гибко
- Учитывай все данные о пользователе для персонализации

ОТВЕЧАЙ СРАЗУ, БЕЗ РАЗМЫШЛЕНИЙ."""


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
