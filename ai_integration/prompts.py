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
        tier_info = "\nТариф LIGHT (3000₽): Все инструменты — задачи, поиск партнёров, исследования, котировки, новости, погода, маркетинг, публикации, проактивные рекомендации."
    elif tier_value == 'STANDARD':
        tier_info = "\nТариф STANDARD (9000₽): Всё из Лайт + делегирование задач. AI находит исполнителя из сети, передаёт задачу, следит за дедлайнами."
    elif tier_value == 'PREMIUM':
        tier_info = "\nТариф PREMIUM (27000₽): Всё из Стандарт + автономное ведение канала. Ежедневный автопостинг в указанное время + контент-стратегия."
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

    # Profile — с аналитикой что есть и чего нет
    profile = ""
    if profile_data:
        FIELD_LABELS = {
            'city': 'Город', 'company': 'Компания', 'position': 'Должность',
            'goals': 'Цели', 'skills': 'Навыки', 'interests': 'Интересы',
            'telegram_channel': 'Telegram'
        }
        filled_parts = []
        empty_fields = []
        for k, label in FIELD_LABELS.items():
            if profile_data.get(k):
                filled_parts.append(f"{label}: {profile_data[k]}")
            elif k != 'telegram_channel':  # telegram_channel необязателен
                empty_fields.append(label.lower())
        if filled_parts:
            profile = "\nПРОФИЛЬ (заполнено):\n" + "\n".join(filled_parts[:7])
        if empty_fields:
            profile += f"\nПРОФИЛЬ (не заполнено): {', '.join(empty_fields)}"
    else:
        profile = "\nПРОФИЛЬ: пустой (ничего не известно о пользователе)"

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
    else:
        task_section = "\nАКТИВНАЯ ЗАДАЧА: нет"

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
        if len(profile_missing) >= 3:
            profile_instruction = f"\n\n⚠️ ПРОФИЛЬ ПУСТОЙ (нет: {missing_str}). ЭТО ПРИОРИТЕТ №1 — узнай о человеке через живой вопрос. Не \"заполни профиль\", а естественный вопрос: \"Чем занимаешься?\", \"Какие планы сейчас?\" Каждый ответ сохраняй через update_profile.\n"
        else:
            profile_instruction = f"\n\n⚠️ ПРОФИЛЬ НЕПОЛНЫЙ (нет: {missing_str}). При случае узнай естественно и сохрани через update_profile.\n"

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

    # Добавляем инструкцию по профилю ПЕРЕД основным промптом — она должна перебивать всё
    if profile_instruction and len(profile_missing) >= 3:
        prompt = profile_instruction + "\n" + prompt
    elif profile_instruction:
        prompt += profile_instruction

    return prompt