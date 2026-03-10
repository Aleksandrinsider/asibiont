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

def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None, message_type=None, weather_info=None, news_info=None, profile_data=None, proactive_context=None, current_task_info=None, user_id_param=None, lang='ru'):
    """Упрощенный промпт - использует отдельные модули"""

    # Token system — все функции открыты, ограничение только баланс
    tier_value = 'Токены'  # Унифицированная модель
    
    # Получаем баланс токенов для контекста AI
    token_balance_info = ""
    if user_id_param:
        try:
            from token_service import get_balance
            balance = get_balance(user_id_param)
            if lang == 'en':
                token_balance_info = f"\nToken balance: {balance} (1 token = 1₽). Each action costs tokens."
                if balance < 500:
                    token_balance_info += " User has low tokens (less than 1 day left) — warn about balance, suggest /buy, but DON'T reduce response quality."
                elif balance < 1500:
                    token_balance_info += " Balance is getting low — mention /buy naturally if relevant."
                else:
                    token_balance_info += " DO NOT mention token balance in your response unless user asks about it."
            else:
                token_balance_info = f"\nБаланс токенов: {balance} (1 токен = 1₽). Каждое действие стоит токены."
                if balance < 500:
                    token_balance_info += " У пользователя мало токенов (менее суток) — предупреди, предложи /buy, но НЕ снижай качество ответа."
                elif balance < 1500:
                    token_balance_info += " Токены на исходе — при случае естественно упомяни /buy."
                else:
                    token_balance_info += " НЕ упоминай баланс токенов в ответе, если пользователь не спрашивает."
        except Exception:
            pass

    # Динамическая стоимость из token_service
    try:
        from token_service import ACTION_COSTS
        msg_cost = ACTION_COSTS.get('message', 20)
        task_cost_min = min(ACTION_COSTS.get('complete_task', 5), ACTION_COSTS.get('delete_task', 5))
        task_cost_max = max(ACTION_COSTS.get('add_task', 15), ACTION_COSTS.get('edit_task', 10))
        delegate_cost = ACTION_COSTS.get('delegate_task', 40)
        research_cost = ACTION_COSTS.get('research_topic', 20)
    except Exception:
        # Дефолты соответствуют текущей token_service.ACTION_COSTS
        msg_cost, task_cost_min, task_cost_max, delegate_cost, research_cost = 10, 2, 7, 20, 10

    if lang == 'en':
        tier_info = f"""\n## TOKEN SYSTEM
All features are available. User pays tokens for each action.{token_balance_info}

Costs (1 token = 1₽):
• Message: {msg_cost}₽ • Task: {task_cost_min}-{task_cost_max}₽ • Delegation: {delegate_cost}₽ • Research: {research_cost}₽
If balance is low — warn and suggest /buy."""
    else:
        tier_info = f"""\n## СИСТЕМА ТОКЕНОВ
Все функции открыты. Пользователь платит токенами за каждое действие.{token_balance_info}

Стоимость (1 токен = 1₽):
• Сообщение: {msg_cost}₽ • Задача: {task_cost_min}-{task_cost_max}₽ • Делегирование: {delegate_cost}₽ • Исследование: {research_cost}₽
Если баланс низкий — предупреди и предложи /buy."""

    # Context data
    weather = f"\n{'Weather' if lang == 'en' else 'Погода'}: {weather_info}" if weather_info else ""
    news = f"\n{'News' if lang == 'en' else 'Новости'}: {news_info}" if news_info else ""

    # Profile completeness check
    profile_complete = False
    profile_missing = []
    if profile_data:
        if not profile_data.get('city'):
            profile_missing.append('city' if lang == 'en' else 'город')
        if not profile_data.get('goals'):
            profile_missing.append('goals' if lang == 'en' else 'цели')
        if not profile_data.get('skills'):
            profile_missing.append('skills' if lang == 'en' else 'навыки')
        if not profile_data.get('interests'):
            profile_missing.append('interests' if lang == 'en' else 'интересы')
        if len(profile_missing) <= 1:  # If only one thing missing, consider complete
            profile_complete = True
    else:
        profile_missing = ['city', 'goals', 'skills', 'interests'] if lang == 'en' else ['город', 'цели', 'навыки', 'интересы']

    # Profile — явный формат чтобы AI видел данные и НЕ переспрашивал
    profile = ""
    if profile_data:
        if lang == 'en':
            FIELD_LABELS = {
                'city': 'City', 'company': 'Company', 'position': 'Position',
                'goals': 'Goals', 'skills': 'Skills', 'interests': 'Interests',
                'telegram_channel': 'TG channel', 'status_text': 'Status', 'bio': 'Bio'
            }
        else:
            FIELD_LABELS = {
                'city': 'Город', 'company': 'Компания', 'position': 'Должность',
                'goals': 'Цели', 'skills': 'Навыки', 'interests': 'Интересы',
                'telegram_channel': 'TG-канал', 'status_text': 'Статус', 'bio': 'О себе'
            }
        filled_parts = []
        empty_fields = []
        _optional_fields = ('telegram_channel', 'status_text', 'bio')  # необязательные
        # Добавляем Email/Phone рядом с основными полями (user.email / user.phone, если пассированы)
        if profile_data.get('email'):
            FIELD_LABELS['email'] = 'Email' if lang != 'en' else 'Email'
            _optional_fields = _optional_fields + ('email',)  # type: ignore
        if profile_data.get('phone'):
            FIELD_LABELS['phone'] = 'Телефон' if lang != 'en' else 'Phone'
            _optional_fields = _optional_fields + ('phone',)  # type: ignore
        for k, label in FIELD_LABELS.items():
            if profile_data.get(k):
                filled_parts.append(f"{label}: {profile_data[k]}")
            elif k not in _optional_fields:
                empty_fields.append(label)
        if filled_parts:
            header = "USER PROFILE (already known, DON'T re-ask):" if lang == 'en' else "ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ (уже известно, НЕ переспрашивай):"
            profile = "\n" + header + "\n" + "\n".join(filled_parts)
        if empty_fields:
            ask = "find out through a natural question" if lang == 'en' else "узнай через живой вопрос"
            not_filled = "Not filled" if lang == 'en' else "Не заполнено"
            profile += f"\n[{not_filled}: {', '.join(empty_fields)} — {ask}]"
    else:
        if lang == 'en':
            profile = "\n[ profile is empty: you know NOTHING about this person — find out through conversation]"
        else:
            profile = "\n[ профиль пуст: НИЧЕГО не знаешь о человеке — узнай через живой разговор]"

    # Search history
    search_context = ""
    if user_id_param:
        try:
            from .utils import generate_unified_recommendations
            recommendations = generate_unified_recommendations('personalized', user_id=user_id_param)
            if recommendations:
                header = "SEARCH HISTORY:" if lang == 'en' else "ИСТОРИЯ ПОИСКОВ:"
                search_context = "\n" + header + "\n" + "\n".join(f"• {rec}" for rec in recommendations[:3])
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to get search context: {e}")

    # User memory — извлекаем rules отдельно (ПРИОРИТЕТ), остальное — исторические заметки
    memory_section = ""
    rules_section = ""  # Будет добавлен В НАЧАЛО промпта
    if user_memory:
        try:
            import re as _re
            decrypted_memory = user_memory  # Assuming it's already decrypted
            if decrypted_memory and decrypted_memory.strip():
                # Пытаемся распарсить JSON — в нём хранятся rules и другие структурированные данные
                _mem_json = None
                _stripped = decrypted_memory.strip()
                if _stripped.startswith('{'):
                    try:
                        _mem_json = json.loads(_stripped)
                    except Exception:
                        pass

                # ── ПРАВИЛА ПОЛЬЗОВАТЕЛЯ — выносим отдельно и приоритетно ──
                _rules = []
                if _mem_json:
                    _rules = _mem_json.get('rules', [])
                if _rules:
                    _rules_lines = '\n'.join(f"  {i+1}. {r}" for i, r in enumerate(_rules))
                    if lang == 'en':
                        rules_section = (
                            f"\n🔴 MANDATORY USER RULES (stored preferences — ALWAYS follow, in every response and action):\n"
                            f"{_rules_lines}\n"
                            f"These rules override any default behavior. Violation = failure."
                        )
                    else:
                        rules_section = (
                            f"\n🔴 ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ПОЛЬЗОВАТЕЛЯ (сохранённые предпочтения — соблюдай ВСЕГДА, в каждом ответе и действии):\n"
                            f"{_rules_lines}\n"
                            f"Эти правила отменяют любое поведение по умолчанию. Нарушение = провал."
                        )

                # Убираем строки с результатами tool-вызовов — они НЕ факты
                _TOOL_JUNK_RE = _re.compile(
                    r'^(Искал:.*|create_goal:.*|update_goal_progress:.*|'
                    r'set_content_strategy:.*|set_contact_alert:.*|'
                    r'research_topic:.*|get_news_trends:.*|'
                    r'hide_contact:.*|AI iter \d+:.*)$',
                    _re.MULTILINE
                )
                # Для JSON-памяти берём только текстовые заметки (не rules)
                if _mem_json:
                    _notes = _mem_json.get('notes', '') or _mem_json.get('memory', '')
                    cleaned = str(_notes)[:400].strip() if _notes else ''
                else:
                    cleaned = _TOOL_JUNK_RE.sub('', decrypted_memory).strip()
                    cleaned = '\n'.join(line for line in cleaned.split('\n') if line.strip())
                if cleaned:
                    if lang == 'en':
                        memory_section = (
                            f"\nNOTES FROM PAST CONVERSATIONS (HISTORICAL context only — "
                            f"tasks/goals mentioned here may be ALREADY COMPLETED or DELETED. "
                            f"Do NOT claim the user has an active task based on these notes. "
                            f"For actual current tasks ALWAYS call list_tasks()):\n"
                            f"{cleaned[:400]}"
                        )
                    else:
                        memory_section = (
                            f"\nЗАМЕТКИ О ПРОШЛЫХ РАЗГОВОРАХ (ИСТОРИЧЕСКИЙ контекст — "
                            f"задачи/цели упомянутые здесь могут быть УЖЕ ЗАВЕРШЕНЫ или УДАЛЕНЫ. "
                            f"НЕ утверждай что у пользователя есть активная задача на основе этих заметок. "
                            f"Для проверки актуальных задач ВСЕГДА вызывай list_tasks()):\n"
                            f"{cleaned[:400]}"
                        )
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to process user memory: {e}")

    # Current task
    task_section = ""
    if current_task_info:
        task_status = current_task_info.get('status', 'pending')
        if task_status == 'completed':
            # Задача завершена — показываем как историческую, не активную
            if lang == 'en':
                task_section = f"""
LAST TASK ( COMPLETED): "{current_task_info['title']}" (ID: {current_task_info['id']})
This task is DONE. Do NOT mention it as active or pending. For current tasks use list_tasks()."""
            else:
                task_section = f"""
ПОСЛЕДНЯЯ ЗАДАЧА ( ЗАВЕРШЕНА): "{current_task_info['title']}" (ID: {current_task_info['id']})
Эта задача УЖЕ ВЫПОЛНЕНА. НЕ упоминай её как активную или незавершённую. Для актуальных задач — list_tasks()."""
        else:
            if lang == 'en':
                task_section = f"""
ACTIVE TASK: "{current_task_info['title']}" (ID: {current_task_info['id']})
If user says "done/finished/completed" → complete_task()"""
            else:
                task_section = f"""
АКТИВНАЯ ЗАДАЧА: "{current_task_info['title']}" (ID: {current_task_info['id']})
Если пользователь говорит "сделал/готово/выполнил" → complete_task()"""
    else:
        task_section = "\n" + ("ACTIVE TASK: none" if lang == 'en' else "АКТИВНАЯ ЗАДАЧА: нет")

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
        if lang == 'en':
            if len(profile_missing) >= 3:
                profile_instruction = (
                    f"\n\nCRITICAL — PROFILE IS EMPTY (missing: {missing_str}). "
                    f"The person is writing from Telegram, they don't have a profile popup like on the website. "
                    f"YOU are the only way to learn about them. Without a profile you are useless. "
                    f"In EVERY response ask ONE specific question until all fields are filled. "
                    f"Order: 1) What they do (field/position) 2) City 3) Main goal right now 4) Key skills/interests. "
                    f"The question should be natural, not survey-like. Weave it into conversation. "
                    f"On each user answer — immediately update_profile. "
                    f"DON'T call research_topic, get_news_trends until you know at least their field.\n"
                )
            elif len(profile_missing) >= 2:
                profile_instruction = (
                    f"\n\nPROFILE INCOMPLETE (missing: {missing_str}). "
                    f"YOU MUST ask a SPECIFIC question about the missing field at the END of your response — "
                    f"NOT a generic 'what do you do', but specifically about {profile_missing[0]}: "
                    f"for example 'what is your main goal right now?' or 'what skills do you consider key?'. "
                    f"Weave the question into conversation. When they answer — immediately update_profile.\n"
                )
            else:
                profile_instruction = f"\n\nProfile almost complete (missing: {missing_str}). When appropriate, find out naturally and save via update_profile.\n"
        else:
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
                    f"ОБЯЗАТЕЛЬНО В КОНЦЕ ОТВЕТА задай КОНКРЕТНЫЙ вопрос о недостающем поле — "
                    f"НЕ общий 'чем занимаешься', а именно про {profile_missing[0]}: "
                    f"например 'какая у тебя главная цель сейчас?' или 'какие навыки считаешь ключевыми?'. "
                    f"Вопрос вплетай в разговор. Когда ответит — сразу update_profile.\n"
                )
            else:
                profile_instruction = f"\n\nПрофиль почти полный (нет: {missing_str}). При случае узнай естественно и сохрани через update_profile.\n"

    # Выбираем версию промпта
    complexity = "medium"  # Можно определить на основе контекста
    base_prompt = select_prompt_version(subscription_tier, complexity, lang=lang)

    # Заполняем шаблон — ручной replace чтобы избежать KeyError/ValueError
    # от JSON-примеров вроде {"email": "..."} внутри промпта
    prompt = base_prompt
    for _key, _val in (
        ('tier_info', tier_info),
        ('user_username', user_username),
        ('current_time_str', current_time_str),
        ('current_date_str', current_date_str),
        ('tier_value', tier_value),
        ('profile', profile),
        ('search_context', search_context),
        ('memory_section', memory_section),
        ('weather', weather),
        ('news', news),
        ('proactive_context', proactive_context or ""),
        ('task_section', task_section),
    ):
        prompt = prompt.replace('{' + _key + '}', str(_val))

    # Добавляем инструкцию по профилю — ПЕРЕД промптом для 2+ пустых полей (чтобы перебивала всё)
    if profile_instruction and len(profile_missing) >= 2:
        prompt = profile_instruction + "\n" + prompt
    elif profile_instruction:
        prompt += profile_instruction

    # ── ПРАВИЛА ПОЛЬЗОВАТЕЛЯ — добавляем В САМОЕ НАЧАЛО промпта (высший приоритет) ──
    if rules_section:
        prompt = rules_section + "\n\n" + prompt

    return prompt