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

    # Token system — все функции открыты, ограничение только баланс
    tier_value = subscription_tier.value if hasattr(subscription_tier, 'value') else str(subscription_tier)
    
    # Получаем баланс токенов для контекста AI
    token_balance_info = ""
    if user_id_param:
        try:
            from token_service import get_balance
            balance = get_balance(user_id_param)
            token_balance_info = f"\nБаланс токенов: {balance} (1 токен = 1₽). Каждое действие стоит токены."
            if balance < 100:
                token_balance_info += " ⚠️ У пользователя мало токенов — будь лаконичен, экономь ресурс."
        except Exception:
            pass

    tier_info = f"""\n## СИСТЕМА ТОКЕНОВ
Все функции открыты. Пользователь платит токенами за каждое действие.{token_balance_info}

Доступные инструменты:
- Задачи: list_tasks, add_task, complete_task, edit_task, delete_task, check_time_conflicts, reschedule_task, restore_task
- Цели: create_goal, update_goal_progress, list_goals
- Делегирование: delegate_task, get_delegation_progress
- Контакты: find_relevant_contacts_for_task
- Профиль: update_profile, show_profile
- Исследования: research_topic, get_news_trends, get_weather_info, quick_topic_search
- Маркетинг: generate_marketing_content, publish_to_telegram, set_content_strategy
- Автономность: set_auto_post_time, toggle_autonomous_feature

Стоимость действий для пользователя:
• Сообщение: 20₽ • Задача: 5-15₽ • Делегирование: 40₽ • Маркетинг: 60₽
Если баланс низкий — предупреди и предложи /buy."""

    # Context data
    weather = f"\nПогода: {weather_info}" if weather_info else ""
    news = f"\nНовости: {news_info}" if news_info else ""

    # Profile completeness check
    profile_complete = False
    profile_missing = []
    if profile_data:
        if not profile_data.get('city'):
            profile_missing.append('город')
        if not profile_data.get('goals'):
            profile_missing.append('цели')
        if not profile_data.get('skills'):
            profile_missing.append('навыки')
        if not profile_data.get('interests'):
            profile_missing.append('интересы')
        if len(profile_missing) <= 1:  # If only one thing missing, consider complete
            profile_complete = True
    else:
        profile_missing = ['город', 'цели', 'навыки', 'интересы']

    # Profile — с аналитикой что есть и чего нет
    profile = ""
    if profile_data:
        # Формат профиля как внутренние заметки — не для пересказа пользователю
        FIELD_KEYS = {
            'city': 'geo', 'company': 'org', 'position': 'role',
            'goals': 'aim', 'skills': 'can', 'interests': 'into',
            'telegram_channel': 'tg'
        }
        filled_parts = []
        empty_fields = []
        for k, short in FIELD_KEYS.items():
            if profile_data.get(k):
                filled_parts.append(f"{short}={profile_data[k]}")
            elif k != 'telegram_channel':  # telegram_channel необязателен
                empty_fields.append(short)
        if filled_parts:
            profile = "\n[internal_notes: " + " | ".join(filled_parts[:7]) + "]"
        if empty_fields:
            profile += f"\n[❓ не знаешь: {', '.join(empty_fields)} — узнай через живой вопрос]"
    else:
        profile = "\n[❗ профиль пуст: НИЧЕГО не знаешь о человеке — узнай через живой разговор]"

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
            profile_instruction = (
                f"\n\nКРИТИЧНО — ПРОФИЛЬ ПУСТОЙ (нет: {missing_str}). "
                f"Человек пишет из Telegram, у него нет попапа профиля как на сайте. "
                f"ТЫ — единственный способ узнать о нём. Без профиля ты бесполезен. "
                f"В КАЖДОМ ответе задавай ОДИН конкретный вопрос пока не заполнишь все поля. "
                f"Порядок: 1) Чем занимается (сфера/должность) 2) Город 3) Главная цель сейчас 4) Ключевые навыки/интересы. "
                f"Вопрос должен быть естественным, не анкетным. Вплетай в разговор. "
                f"Каждый ответ пользователя — сразу update_profile. "
                f"НЕ ВЫЗЫВАЙ research_topic, get_news_trends пока не знаешь хотя бы сферу деятельности.\n"
            )
        elif len(profile_missing) >= 2:
            profile_instruction = (
                f"\n\nПРОФИЛЬ НЕПОЛНЫЙ (нет: {missing_str}). "
                f"ОБЯЗАТЕЛЬНО В КОНЦЕ ОТВЕТА задай живой вопрос о недостающем. "
                f"Можешь дать ценность по теме + вопрос. Когда ответит — сразу update_profile.\n"
            )
        else:
            profile_instruction = f"\n\n💡 Профиль почти полный (нет: {missing_str}). При случае узнай естественно и сохрани через update_profile.\n"

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

    # Добавляем инструкцию по профилю — ПЕРЕД промптом для 2+ пустых полей (чтобы перебивала всё)
    if profile_instruction and len(profile_missing) >= 2:
        prompt = profile_instruction + "\n" + prompt
    elif profile_instruction:
        prompt += profile_instruction

    return prompt