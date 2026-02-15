"""
Упрощенный интерфейс для промптов
Использует отдельные модули: context_builder и system_prompt
"""

import logging
import pytz
import json
from datetime import datetime, timedelta, timezone

from .context_builder import context_builder
from .system_prompt import select_prompt_version

logger = logging.getLogger(__name__)

def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None, message_type=None, weather_info=None, news_info=None, profile_data=None, proactive_context=None, current_task_info=None, user_id_param=None):
    """Упрощенный промпт - использует отдельные модули"""

    # Subscription info
    tier_value = subscription_tier.value if hasattr(subscription_tier, 'value') else str(subscription_tier)
    if tier_value == 'LIGHT':
        tier_info = "\nТариф LIGHT: базовые функции, задачи, поиск партнеров, research_topic"
    elif tier_value == 'STANDARD':
        tier_info = "\nТариф STANDARD: +marketing, delegation, полный research_topic"
    elif tier_value == 'PREMIUM':
        tier_info = "\nТариф PREMIUM: все функции, алерты, автономность"
    else:
        tier_info = f"\nТариф: {tier_value}"

    # Context data
    weather = f"\nПогода: {weather_info}" if weather_info else ""
    news = f"\nНовости: {news_info}" if news_info else ""

    # Profile completeness check
    profile_complete = False
    profile_missing = []
    if profile_data:
        if not profile_data.get('goals'):
            profile_missing.append('цели')
        if not profile_data.get('skills'):
            profile_missing.append('навыки')
        if not profile_data.get('interests'):
            profile_missing.append('интересы')
        if len(profile_missing) <= 1:  # If only one thing missing, consider complete
            profile_complete = True
    else:
        profile_missing = ['цели', 'навыки', 'интересы']

    # Profile
    profile = ""
    if profile_data:
        parts = []
        for k in ['city', 'company', 'position', 'goals', 'skills', 'interests', 'telegram_channel']:
            if profile_data.get(k):
                label = 'Telegram' if k == 'telegram_channel' else k.title()
                parts.append(f"{label}: {profile_data[k]}")
        if parts:
            profile = "\nПРОФИЛЬ:\n" + "\n".join(parts[:7])

    # Search history
    search_context = ""
    if user_id_param:
        try:
            from .utils import generate_unified_recommendations
            recommendations = generate_unified_recommendations('personalized', user_id=user_id_param)
            if recommendations:
                search_context = "\nИСТОРИЯ ПОИСКОВ:\n" + "\n".join(f"• {rec}" for rec in recommendations[:3])
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to get search context: {e}")

    # User memory
    memory_section = ""
    if user_memory:
        try:
            decrypted_memory = user_memory  # Assuming it's already decrypted
            if decrypted_memory and len(decrypted_memory.strip()) > 0:
                memory_section = f"\nПАМЯТЬ ПОЛЬЗОВАТЕЛЯ:\n{decrypted_memory[:500]}"  # Limit to 500 chars
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to process user memory: {e}")

    # Current task
    task_section = ""
    if current_task_info:
        task_section = f"""
АКТИВНАЯ ЗАДАЧА: "{current_task_info['title']}" (ID: {current_task_info['id']})
Если пользователь говорит "сделал/готово/выполнил" → complete_task()"""

    # Proactive context - теперь из context_builder
    if proactive_context is None and user_id_param:
        try:
            from models import Session
            session = Session()
            proactive_context = context_builder.build_proactive_context(user_id_param, session, profile_complete)
            session.close()
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to build proactive context: {e}")
            proactive_context = ""

    # Profile completeness instruction
    profile_instruction = ""
    if not profile_complete and profile_missing:
        missing_str = ', '.join(profile_missing)
        profile_instruction = f"""

ПРОФИЛЬ НЕПОЛНЫЙ (нет: {missing_str}).
Если к месту — спроси ненавязчиво, вплетая в разговор.
НЕ превращай это в допрос. Не спрашивай при каждом сообщении.
"""

    # Выбираем версию промпта
    complexity = "medium"  # Можно определить на основе контекста
    base_prompt = select_prompt_version(subscription_tier, complexity)

    # Заполняем шаблон
    prompt = base_prompt.format(
        tier_info=tier_info,
        user_username=user_username,
        current_time_str=current_time_str,
        current_date_str=current_date_str,
        tier_value=tier_value,
        profile=profile,
        search_context=search_context,
        memory_section=memory_section,
        weather=weather,
        news=news,
        proactive_context=proactive_context or "",
        task_section=task_section
    )

    # Добавляем инструкцию по профилю если нужно
    if profile_instruction:
        prompt += profile_instruction

    return prompt