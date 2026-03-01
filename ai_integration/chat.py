from . import handlers
from models import Session, User, Task, UserProfile, Subscription, Interaction, Goal
import aiohttp
import json
import logging
import asyncio
import traceback
from datetime import datetime, timedelta
import re
import pytz
import hashlib
import time

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .memory import encrypt_data, decrypt_data
from .utils import (
    replace_placeholders, clean_technical_details,
)
from .prompts import get_extended_system_prompt
from .tools import TOOLS
from .handlers import (  # noqa: F401
    add_task, list_tasks, complete_task,
    delegate_task, check_subscription_status, accept_delegated_task,
    reject_delegated_task, get_delegation_progress, cancel_delegation, edit_task,
    get_partners_list, research_topic,
    check_time_conflicts,
    generate_delegation_notification_async, generate_progress_request, schedule_delegation_monitoring,
    check_delegation_deadlines, create_subscription_payment,
    cancel_subscription,
    update_profile, delete_task, find_relevant_contacts_for_task, get_news_trends,
)
from .autonomous_agent import chat_with_ai as autonomous_chat_with_ai

logger = logging.getLogger(__name__)

# Базовый системный промпт для простых сообщений
system_prompt = "Ты - ASI Biont, умный AI-помощник для управления задачами и повышения продуктивности. Отвечай кратко и по делу."

# ── Bilingual labels for proactive context & situation prompt ──
def _t(key, lang='ru'):
    """Get a translated label by key."""
    return _T.get(key, {}).get(lang, _T.get(key, {}).get('ru', key))

_T = {
    'months': {
        'ru': ['января','февраля','марта','апреля','мая','июня',
               'июля','августа','сентября','октября','ноября','декабря'],
        'en': ['January','February','March','April','May','June',
               'July','August','September','October','November','December'],
    },
    'user_info': {'ru': 'Информация о пользователе', 'en': 'User information'},
    'profile': {'ru': 'Профиль', 'en': 'Profile'},
    'profile_fields': {
        'ru': [('city','Город'),('company','Компания'),('position','Должность'),
               ('languages','Языки'),('skills','Навыки'),('interests','Интересы'),('goals','Цели')],
        'en': [('city','City'),('company','Company'),('position','Position'),
               ('languages','Languages'),('skills','Skills'),('interests','Interests'),('goals','Goals')],
    },
    'stats_label': {'ru': '📊 Статистика', 'en': '📊 Stats'},
    'tasks_created': {'ru': 'создано задач', 'en': 'tasks created'},
    'completed': {'ru': 'завершено', 'en': 'completed'},
    'skipped': {'ru': 'пропущено', 'en': 'skipped'},
    'avg_time': {'ru': 'ср. время выполнения', 'en': 'avg completion time'},
    'key_interests': {'ru': '🎯 Устойчивые интересы', 'en': '🎯 Key interests'},
    'recent_searches': {'ru': '🔍 Недавно искал', 'en': '🔍 Recent searches'},
    'projects_label': {'ru': '📁 Проекты', 'en': '📁 Projects'},
    'humidity': {'ru': 'влажность', 'en': 'humidity'},
    'wind': {'ru': 'ветер', 'en': 'wind'},
    'wind_unit': {'ru': 'м/с', 'en': 'm/s'},
    'news_city': {'ru': 'Новости {city}', 'en': '{city} news'},
    'news_general': {'ru': 'Свежие новости России', 'en': 'Latest news'},
    'weather_hdr': {'ru': '🌤 ПОГОДА', 'en': '🌤 WEATHER'},
    'news_hdr': {'ru': '📰 НОВОСТИ', 'en': '📰 NEWS'},
    'insights_hdr': {'ru': '🔥 ИНСАЙТЫ', 'en': '🔥 INSIGHTS'},
    'partners_hdr': {'ru': '👥 ПАРТНЁРЫ', 'en': '👥 PARTNERS'},
    'overdue_mark': {'ru': '⚠️ПРОСРОЧЕНО', 'en': '⚠️OVERDUE'},
    'days_short': {'ru': 'дн', 'en': 'd'},
    'goals_hdr': {'ru': '🎯 ЦЕЛИ', 'en': '🎯 GOALS'},
    'active_tasks': {'ru': 'Активных задач', 'en': 'Active tasks'},
    'overdue_hdr': {'ru': 'ПРОСРОЧЕННЫЕ', 'en': 'OVERDUE'},
    'deadline_soon': {'ru': '⏰ СКОРО ДЕДЛАЙН', 'en': '⏰ DEADLINE SOON'},
    'in_hours': {'ru': 'через {h}ч', 'en': 'in {h}h'},
    'goal_alerts_hdr': {'ru': '🎯 АЛЕРТЫ ЦЕЛЕЙ', 'en': '🎯 GOAL ALERTS'},
    'deadline_in': {'ru': 'дедлайн через {d}дн ({p}%)', 'en': 'deadline in {d}d ({p}%)'},
    'almost_done': {'ru': 'почти готово ({p}%)', 'en': 'almost done ({p}%)'},
    'no_progress': {'ru': 'нет прогресса {d}дн', 'en': 'no progress {d}d'},
    'stale_tasks': {'ru': '📋 ЗАСТОЙ: {n} задач висят больше недели без выполнения',
                    'en': '📋 STALE: {n} tasks pending for over a week'},
    'recent_topics': {'ru': 'Недавние темы', 'en': 'Recent topics'},
    'productive': {'ru': 'продуктивный', 'en': 'productive'},
    'delegates_pat': {'ru': 'делегирует', 'en': 'delegates'},
    'patterns_lbl': {'ru': 'Паттерны', 'en': 'Patterns'},
    'active_morning': {'ru': 'активен утром', 'en': 'active in the morning'},
    'active_evening': {'ru': 'активен вечером', 'en': 'active in the evening'},
    'insights_section': {'ru': '💡 ИНСАЙТЫ', 'en': '💡 INSIGHTS'},
    # ── _build_situation_prompt labels ──
    'sit_opening': {
        'ru': 'Ты пишешь проактивное сообщение пользователю. Не в ответ на его запрос — ты сам решил написать.',
        'en': 'You are writing a proactive message to the user. Not in response to their request — you decided to reach out.',
    },
    'time_morning': {'ru': 'Утро', 'en': 'Morning'},
    'time_afternoon': {'ru': 'День', 'en': 'Afternoon'},
    'time_evening': {'ru': 'Вечер', 'en': 'Evening'},
    'time_late': {'ru': 'Позднее время', 'en': 'Late night'},
    'sit_situation': {'ru': '=== СИТУАЦИЯ ===', 'en': '=== SITUATION ==='},
    'sit_time': {'ru': 'Время', 'en': 'Time'},
    'sit_tasks': {'ru': 'Задач', 'en': 'Tasks'},
    'sit_overdue': {'ru': 'Просроченных', 'en': 'Overdue'},
    'sit_profile_ok': {'ru': 'Профиль заполнен', 'en': 'Profile filled'},
    'sit_goals_ok': {'ru': 'Цели заданы', 'en': 'Goals set'},
    'yes': {'ru': 'Да', 'en': 'Yes'},
    'no': {'ru': 'НЕТ', 'en': 'NO'},
    'sit_obs': {'ru': '=== НАБЛЮДЕНИЯ ===', 'en': '=== OBSERVATIONS ==='},
    'obs_no_profile': {'ru': 'Профиль не заполнен — не знаешь этого человека',
                       'en': "Profile not filled — you don't know this person"},
    'obs_no_goals': {'ru': 'Целей нет', 'en': 'No goals set'},
    'obs_overdue': {'ru': 'Просроченных задач: {n}', 'en': 'Overdue tasks: {n}'},
    'sit_resources': {'ru': '=== РЕСУРСЫ ===', 'en': '=== RESOURCES ==='},
    'res_role': {'ru': 'Роль', 'en': 'Role'},
    'res_skills': {'ru': 'Навыки', 'en': 'Skills'},
    'res_interests': {'ru': 'Интересы', 'en': 'Interests'},
    'res_goals': {'ru': 'Цели', 'en': 'Goals'},
    'sit_data': {'ru': '=== ДОСТУПНЫЕ ДАННЫЕ ===', 'en': '=== AVAILABLE DATA ==='},
    'data_weather': {'ru': 'Погода', 'en': 'Weather'},
    'data_news': {'ru': 'Новости', 'en': 'News'},
    'data_partners': {'ru': 'Партнёры/контакты', 'en': 'Partners/contacts'},
    'data_obs': {'ru': 'Наблюдения', 'en': 'Observations'},
    'data_goals': {'ru': 'Цели', 'en': 'Goals'},
    'data_interests': {'ru': 'Устойчивые интересы', 'en': 'Key interests'},
    'data_searches': {'ru': 'Недавние поиски', 'en': 'Recent searches'},
    'data_projects': {'ru': 'Проекты', 'en': 'Projects'},
    'sit_tasks_hdr': {'ru': 'ЗАДАЧИ', 'en': 'TASKS'},
    'sit_overdue_hdr': {'ru': '⚠️ ПРОСРОЧЕННЫЕ', 'en': '⚠️ OVERDUE'},
    'sit_overdue_tasks': {'ru': '⚠️ ПРОСРОЧЕННЫЕ ЗАДАЧИ', 'en': '⚠️ OVERDUE TASKS'},
    'sit_accent': {'ru': 'Акцент', 'en': 'Focus'},
    'sit_type': {'ru': '=== ТИП СООБЩЕНИЯ: {t} ===', 'en': '=== MESSAGE TYPE: {t} ==='},
    'sit_no_plan': {
        'ru': '⛔ НЕ предлагай план дня / список задач. Сегодня другой тип сообщения.',
        'en': '⛔ Do NOT suggest a daily plan / task list. Today is a different message type.',
    },
    'sit_reacts': {'ru': 'Реагирует на', 'en': 'Reacts to'},
    'sit_ignores': {
        'ru': 'Часто игнорирует — пиши только с реальной пользой',
        'en': 'Often ignores — only write with real value',
    },
    'sit_rules': {
        'ru': """
Стиль ответа — точно такой же как в обычном чате: живой, разговорный, без формальностей. Человек не должен чувствовать, что это системное сообщение.
ФОРМАТ: 300-500 символов, сплошной текст, никаких списков и заголовков. Конкретика. Не перечисляй функции. Не выдумывай данные.

⚠️ ПРАВИЛО ВЕРИФИКАЦИИ: упоминай задачи/цели ТОЛЬКО если получил их из инструментов (list_tasks, list_goals).
НЕ бери задачи/цели/посты из памяти или истории — они могут быть удалены.

🗣️ ЯЗЫК: Пиши ТОЛЬКО на русском языке.""",
        'en': """
Style: exactly like a regular chat reply — alive, conversational, no formality. User must not feel it's a system message.
FORMAT: 300-500 characters, flowing text, no lists or headers. Be specific. Don't list features. Don't invent data.

⚠️ VERIFICATION RULE: mention tasks/goals ONLY if you got them from tools (list_tasks, list_goals).
Do NOT take tasks/goals/posts from memory or history — they may have been deleted.

🗣️ LANGUAGE: Write ONLY in English. Even if tool results or context data are in Russian, you MUST respond in English.""",
    },
    # ── generate_proactive_message labels ──
    'pro_instruction': {
        'ru': (
            "Напиши проактивное сообщение пользователю на основе анализа ситуации выше. "
            "Используй инструменты если нужны актуальные данные.\n\n"
            "ФОРМАТ: без приветствий и клише («Утро — время планирования»). Сразу по делу. "
            "Максимум 4-5 предложений + 1 вопрос или конкретное предложение действия.\n\n"
            "ОБЯЗАТЕЛЬНО: если предлагаешь шаги или план — укажи 1-2 АЛЬТЕРНАТИВЫ "
            "(другой подход, другой инструмент, другой приоритет). "
            "Например, вместо только 'написать пост' → 'написать пост ИЛИ сначала исследовать конкурентов'. "
            "Альтернативы делают сообщение практичным, а не директивным."
        ),
        'en': (
            "Write a proactive message to the user based on the situation analysis above. "
            "Use tools if you need current data.\n\n"
            "FORMAT: no greetings or clichés. Get straight to the point. "
            "Max 4-5 sentences + 1 question or specific action.\n\n"
            "REQUIRED: if suggesting steps or a plan — include 1-2 ALTERNATIVES "
            "(a different approach, tool, or priority). "
            "E.g. instead of just 'write a post' → 'write a post OR research competitors first'. "
            "Alternatives make the message practical, not prescriptive."
        ),
    },
    'pro_task_help': {
        'ru': "Помоги пользователю решить задачу из контекста выше. "
              "ОБЯЗАТЕЛЬНО используй инструменты (research_topic, find_relevant_contacts_for_task, get_news_trends) "
              "чтобы дать КОНКРЕТНЫЙ результат. Не предлагай помощь — СДЕЛАЙ работу.",
        'en': "Help the user solve the task from the context above. "
              "You MUST use tools (research_topic, find_relevant_contacts_for_task, get_news_trends) "
              "to deliver a CONCRETE result. Don't offer help — DO the work.",
    },
    'anti_repeat_intro': {
        'ru': "\n\nЗАПРЕЩЕНО повторять эти фразы (твои последние ответы):\n",
        'en': "\n\nDo NOT repeat these phrases (your recent responses):\n",
    },
    'anti_repeat_suffix': {'ru': "\nГенерируй УНИКАЛЬНЫЙ ответ!", 'en': "\nGenerate a UNIQUE response!"},
    'fallback_short': {
        'ru': "Привет! Готов помочь. Что обсудим?",
        'en': "Hey! Ready to help. What shall we discuss?",
    },
    'fallback_full': {
        'ru': "Привет! Готов помочь с задачами и целями. Что обсудим?",
        'en': "Hey! Ready to help with tasks and goals. What shall we discuss?",
    },
    'error_user_not_found': {
        'ru': "Ошибка: пользователь не найден",
        'en': "Error: user not found",
    },
    'error_processing': {
        'ru': "Извините, произошла ошибка при обработке запроса. Попробуйте ещё раз.",
        'en': "Sorry, an error occurred while processing your request. Please try again.",
    },
}

# Intent labels (bilingual)
_INTENT_LABELS = {
    'ru': {
        'morning': "Утро — план дня", 'evening': "Вечер — итоги",
        'overdue': "Просроченные задачи", 'insight': "Находка по интересам",
        'trend': "Тренд/новость", 'weather': "Погода + активность",
        'contact': "Партнёр/контакт", 'productivity': "Продуктивность",
    },
    'en': {
        'morning': "Morning — daily plan", 'evening': "Evening — wrap-up",
        'overdue': "Overdue tasks", 'insight': "Interest-based discovery",
        'trend': "Trend/news", 'weather': "Weather + activity",
        'contact': "Partner/contact", 'productivity': "Productivity",
    },
}

# Topic keywords (bilingual keys, mixed word lists for matching)
_TOPIC_KW = {
    'ru': {
        'работа/задачи': ['задач','task','дел','работ','проект'],
        'нетворкинг': ['знаком','партнер','контакт','встреч'],
        'цели/рост': ['цель','goal','план','развити','рост'],
        'продуктивность': ['врем','time','продуктив','эффект'],
        'здоровье': ['здоров','спорт','тренир','сон','питан'],
        'финансы': ['деньг','финанс','инвест','бюджет','доход'],
        'обучение': ['учи','курс','книг','навык','изуч'],
    },
    'en': {
        'work/tasks': ['task','work','project','deadline','deliver','задач'],
        'networking': ['partner','contact','meeting','connect','знаком'],
        'goals/growth': ['goal','plan','develop','grow','цель'],
        'productivity': ['time','productive','efficient','focus','продуктив'],
        'health': ['health','sport','workout','sleep','diet','здоров'],
        'finance': ['money','finance','invest','budget','income','финанс'],
        'learning': ['learn','course','book','skill','study','учи','курс'],
    },
}

def _msg_type_instructions(lang, ctx, rotation_hash):
    """Build message_types list with bilingual instructions."""
    task_count = ctx.get('task_count', 0)
    L = lang  # shortcut

    if L == 'en':
        q_variants = [
            'Ask the user ONE precise question about their current work. Not about plans — what are they doing right now and what is blocking them.',
            'Ask about a result. Not "how are you?" — what specifically worked/didn\'t work on the latest topic. Reference recent tasks/goals.',
            'Ask a provocative question about their field — one that makes them want to answer. Not generic, but with your own observation from the data.',
        ]
    else:
        q_variants = [
            'Задай пользователю ОДИН точный вопрос про его текущую работу. Не о планах — а что конкретно делает сейчас и что мешает.',
            'Спроси про результат. Не "как дела?" — а что конкретно получилось/не получилось по последней теме. Опирайся на недавние задачи/цели.',
            'Задай провокационный вопрос по его сфере — от которого хочется ответить. Не банальный, а со своим наблюдением из данных.',
        ]
    message_types = [{'type': 'question', 'instruction': q_variants[rotation_hash % len(q_variants)]}]

    if ctx.get('goals') or task_count > 0:
        if L == 'en':
            stat_variants = [
                'Look at tasks and goals and make ONE specific observation — a pattern, bottleneck, or unexpected connection. Then offer 2 alternative next steps (different approaches). Don\'t recap the task list.',
                'Compare the current situation with the goals. Where is the gap? Suggest 1 concrete action + an alternative path to the same goal via a different tool or resource.',
                'Look at progress and find what is going well. Praise a specific achievement and offer two next-step options for the user to choose from.',
            ]
        else:
            stat_variants = [
                'Посмотри на задачи и цели и сделай ОДНО конкретное наблюдение — паттерн, узкое место, неожиданная связь. Предложи 2 варианта следующего шага (разные подходы). Не пересказывай список задач.',
                'Сравни текущую ситуацию с целями. Где разрыв? Предложи 1 конкретное действие + альтернативный путь к той же цели через другой инструмент или ресурс.',
                'Посмотри на прогресс и найди что идёт хорошо. Похвали за конкретное достижение и предложи два варианта следующего шага на выбор пользователя.',
            ]
        message_types.append({'type': 'analysis', 'instruction': stat_variants[rotation_hash % len(stat_variants)]})

    if ctx.get('partners'):
        instr = ('Tell about a SPECIFIC relevant contact from the partner data. Who they are, how they can help, why worth connecting. Use ONLY real @username from data.'
                 if L == 'en' else
                 'Расскажи о КОНКРЕТНОМ релевантном контакте из данных партнёров. Кто это, чем полезен, почему стоит связаться. Используй ТОЛЬКО реальные @username из данных.')
        message_types.append({'type': 'contact', 'instruction': instr})

    if ctx.get('news'):
        if L == 'en':
            news_variants = [
                'Find something in the news that DIRECTLY affects the user. Don\'t summarize the news — explain how it changes their situation. Ask their opinion.',
                'Connect a news item/trend with the user\'s tasks or goals. Show an opportunity or risk. Suggest an action.',
                'Use research_topic to dig deeper into news related to the user\'s field. Bring facts not in the headlines. Show what you found and ask their opinion.',
            ]
        else:
            news_variants = [
                'Найди в новостях то, что ПРЯМО влияет на пользователя. Не пересказывай новость — объясни как это меняет его ситуацию. Спроси мнение.',
                'Свяжи новость/тренд с задачами или целями пользователя. Покажи возможность или риск. Предложи действие.',
                'Используй research_topic чтобы углубиться в новость по сфере пользователя. Принеси факты, которых нет в новостях. Покажи что накопал и спроси мнение.',
            ]
        message_types.append({'type': 'discussion', 'instruction': news_variants[rotation_hash % len(news_variants)]})

    if task_count == 0:
        instr = ('Ask what the user is working on RIGHT NOW. Offer 2 alternatives — either help with analysis/research, or suggest the ONE most impactful action toward their goal. Do NOT list 3 steps — just the sharpest one + alternative.'
                 if L == 'en' else
                 'Спроси над чем пользователь работает СЕЙЧАС. Предложи 2 варианта — либо помощь с анализом/исследованием, либо одно самое важное действие в сторону цели. НЕ давай список из 3 шагов — только самый острый шаг + альтернатива.')
        message_types.append({'type': 'plan', 'instruction': instr})

    if ctx.get('weather'):
        instr = ('Brief weather update + a specific activity suggestion tied to their interests. Not just "nice weather" — what specifically can they do.'
                 if L == 'en' else
                 'Коротко о погоде + конкретное предложение активности, связанное с интересами. Не просто "хорошая погода" — а что конкретно можно сделать.')
        message_types.append({'type': 'weather', 'instruction': instr})

    if task_count > 0:
        if L == 'en':
            task_variants = [
                'Pick the most important task and HELP with it — research the topic (research_topic), find a contact (find_relevant_contacts_for_task). Come with a result, not a reminder.',
                'Look at tasks: which can be combined, which depend on each other, which one unblocks the rest. Give one specific piece of advice.',
                'Find a task that can be closed right now — small, quick. Suggest starting with it to build momentum.',
            ]
        else:
            task_variants = [
                'Выдели самую важную задачу и ПОМОГИ с ней — исследуй тему (research_topic), найди контакт (find_relevant_contacts_for_task). Приди с результатом, не с напоминанием.',
                'Посмотри на задачи: какие можно объединить, какие зависят друг от друга, какая разблокирует остальные. Дай один конкретный совет.',
                'Найди задачу которую можно закрыть прямо сейчас — маленькую, быструю. Предложи начать с неё чтобы набрать темп.',
            ]
        message_types.append({'type': 'tasks', 'instruction': task_variants[rotation_hash % len(task_variants)]})

    if ctx.get('deadline_soon'):
        tasks_soon = ctx['deadline_soon']
        task_names = ', '.join(t.title for t in tasks_soon[:3])
        instr = (f'DEADLINE ALERT: tasks due soon: {task_names}. Remind specifically — what needs to be done, offer help (break down, reschedule, finish). Tone: gentle but specific.'
                 if L == 'en' else
                 f'АЛЕРТ ДЕДЛАЙНА: скоро срок у задач: {task_names}. Напомни конкретно — что по ним нужно сделать, предложи помощь (разбить, перенести, доделать). Тон мягкий но конкретный.')
        message_types.append({'type': 'deadline_alert', 'instruction': instr})

    if ctx.get('goals_alert'):
        alerts = ctx['goals_alert']
        almost_done = [a for a in alerts if a['type'] == 'almost_done']
        stagnant = [a for a in alerts if a['type'] == 'stagnant']
        deadline_close = [a for a in alerts if a['type'] == 'deadline_close']
        if almost_done:
            gn = almost_done[0]['goal']; gp = almost_done[0]['progress']
            instr = (f'PROGRESS ALERT: goal "{gn}" at {gp}%! Congratulate on the progress, ask what remains to reach 100%, suggest a concrete next step to finish.'
                     if L == 'en' else
                     f'АЛЕРТ ПРОГРЕССА: цель "{gn}" на {gp}%! Поздравь с прогрессом, спроси что осталось до 100%, предложи конкретный следующий шаг чтобы добить цель.')
            message_types.append({'type': 'goal_milestone', 'instruction': instr})
        if stagnant:
            gn = stagnant[0]['goal']; gd = stagnant[0]['days_old']
            instr = (f'STAGNATION ALERT: goal "{gn}" created {gd} days ago, but progress is 0%. Gently ask — is it still relevant? Maybe break into steps? Or rephrase?'
                     if L == 'en' else
                     f'АЛЕРТ ЗАСТОЯ: цель "{gn}" создана {gd} дней назад, но прогресс 0%. Мягко спроси — актуальна ли ещё? Может разбить на шаги? Или пересмотреть формулировку?')
            message_types.append({'type': 'goal_stagnation', 'instruction': instr})
        if deadline_close:
            gn = deadline_close[0]['goal']; gd2 = deadline_close[0]['days']; gp2 = deadline_close[0]['progress']
            instr = (f'ALERT: goal "{gn}" — deadline in {gd2} days, progress {gp2}%. Help plan the final push — what specifically remains?'
                     if L == 'en' else
                     f'АЛЕРТ: цель "{gn}" — дедлайн через {gd2} дн, прогресс {gp2}%. Помоги спланировать финишный рывок — что конкретно остаётся сделать?')
            message_types.append({'type': 'goal_deadline', 'instruction': instr})

    if ctx.get('stale_task_count', 0) >= 3:
        n = ctx['stale_task_count']
        instr = (f'STAGNATION ALERT: {n} tasks pending for over a week. Suggest sorting them out — what is still relevant, what to cancel, what to reschedule. Specifically: "Let\'s review the old tasks? Some have been sitting for a week with no progress."'
                 if L == 'en' else
                 f'АЛЕРТ ЗАСТОЯ: {n} задач висят больше недели. Предложи разобрать — что ещё актуально, что отменить, что перенести. Конкретно: "Давай разберём старые задачи? Некоторые уже неделю без движения."')
        message_types.append({'type': 'task_cleanup', 'instruction': instr})

    if ctx.get('pending_tasks_full'):
        tasks = ctx['pending_tasks_full']
        task_idx = rotation_hash % len(tasks)
        task = tasks[task_idx]
        task_desc = f" — {task.description[:100]}" if task.description else ""
        if L == 'en':
            help_variants = [
                f'Help solve task "{task.title}"{task_desc}. Use research_topic to find useful information. Show the result and suggest an action.',
                f'Task "{task.title}"{task_desc}. Find a contact from the network who can help (find_relevant_contacts_for_task). Show who you found and why they fit.',
                f'Task "{task.title}"{task_desc}. Break it into 2-3 concrete steps and suggest starting with the first. If info is needed — research right away.',
            ]
        else:
            help_variants = [
                f'Помоги решить задачу "{task.title}"{task_desc}. Используй research_topic чтобы найти полезную информацию. Покажи результат и предложи действие.',
                f'Задача "{task.title}"{task_desc}. Найди контакт из сети кто может помочь (find_relevant_contacts_for_task). Покажи кого нашёл и почему он подходит.',
                f'Задача "{task.title}"{task_desc}. Разбей её на 2-3 конкретных шага и предложи начать с первого. Если нужна информация — исследуй сразу.',
            ]
        message_types.append({'type': 'task_help', 'instruction': help_variants[rotation_hash % len(help_variants)]})

    # ── Extended types ──
    if ctx.get('pending_tasks_full') and ctx.get('partners'):
        tasks = ctx['pending_tasks_full']
        task_idx = (rotation_hash + 1) % len(tasks)
        task = tasks[task_idx]
        instr = (f'Task "{task.title}" — find a suitable executor among contacts (use find_relevant_contacts_for_task). Show who you found and suggest delegating: "Found @username, they know this area. Delegate to them?"'
                 if L == 'en' else
                 f'Задача "{task.title}" — найди подходящего исполнителя среди контактов (используй find_relevant_contacts_for_task). Покажи кого нашёл и предложи делегировать: "Нашёл @username, он разбирается в этом. Делегировать ему?"')
        message_types.append({'type': 'delegation_suggest', 'instruction': instr})

    instr = ('Check delegated task status (get_delegation_progress). If there are unaccepted ones — suggest an alternative candidate. If there is progress — inform the user.'
             if L == 'en' else
             'Проверь статус делегированных задач (get_delegation_progress). Если есть непринятые — предложи альтернативного кандидата. Если есть прогресс — сообщи пользователю.')
    message_types.append({'type': 'delegation_status', 'instruction': instr})

    if ctx.get('news') or ctx.get('long_term_data', {}).get('interests'):
        instr = ('Suggest an idea for a channel post. Research the trend via tools (research_topic, get_news_trends). Show the result and suggest: "Here\'s a topic for a post — shall I write and publish it?"'
                 if L == 'en' else
                 'Предложи идею для поста в канал. Исследуй тренд через инструменты (research_topic, get_news_trends). Покажи результат и предложи: "Вот тема для поста — написать и опубликовать?"')
        message_types.append({'type': 'content_idea', 'instruction': instr})

    profile_obj = ctx.get('profile')
    has_profile = bool(profile_obj and (getattr(profile_obj, 'goals', None) or getattr(profile_obj, 'interests', None) or getattr(profile_obj, 'skills', None)))
    if has_profile:
        prof_interests = getattr(profile_obj, 'interests', '') or ''
        prof_goals_str = getattr(profile_obj, 'goals', '') or ''
        niche = prof_interests[:80] or prof_goals_str[:80] or ('their field' if L == 'en' else 'его сфера')
        instr = (f'MARKET MONITORING. Research via research_topic and get_news_trends what is happening in the user\'s niche ({niche}). Find a specific insight: new competitor, regulation change, demand shift, new technology. Show what you found and explain how it affects the user. Suggest an action.'
                 if L == 'en' else
                 f'МОНИТОРИНГ РЫНКА. Исследуй через research_topic и get_news_trends что происходит в нише пользователя ({niche}). Найди конкретный инсайт: новый конкурент, изменение регуляций, сдвиг спроса, новая технология. Покажи что нашёл и объясни как это влияет на пользователя. Предложи действие.')
        message_types.append({'type': 'market_monitor', 'instruction': instr})

    if ctx.get('goals'):
        goal = ctx['goals'][rotation_hash % len(ctx['goals'])]
        instr = (f'CONTENT FOR GOAL. User\'s goal: "{goal.title}" ({goal.progress_percentage}%). Come up with a channel post that ADVANCES this goal: attracts partners, clients, or attention. Research the topic (research_topic). Show the idea and suggest: "Here\'s a post. It will help with the goal — shall I publish it?"'
                 if L == 'en' else
                 f'КОНТЕНТ ДЛЯ ЦЕЛИ. Цель пользователя: "{goal.title}" ({goal.progress_percentage}%). Придумай пост для канала, который ПРОДВИНЕТ эту цель: привлечёт партнёров, клиентов, или внимание. Исследуй тему (research_topic). Покажи идею и предложи: "Вот пост. Он поможет с целью — опубликовать?"')
        message_types.append({'type': 'goal_content', 'instruction': instr})

    return message_types


def _build_session_summary(user_db_id, session):
    """Строит краткую сводку предыдущих сессий для контекстного окна чата.
    
    Анализирует последние сообщения, группирует их по сессиям (пауза > 2 часов),
    извлекает ключевые темы из прошлых сессий.
    """
    try:
        # Берём последние 30 сообщений пользователя
        interactions = session.query(Interaction).filter(
            Interaction.user_id == user_db_id,
            Interaction.message_type == 'user'
        ).order_by(Interaction.created_at.desc()).limit(30).all()
        
        if len(interactions) < 3:
            return None
        
        # Группируем по сессиям (пауза > 2ч)
        sessions = []
        current_session = [interactions[0]]
        SESSION_GAP = timedelta(hours=2)
        
        for i in range(1, len(interactions)):
            gap = interactions[i-1].created_at - interactions[i].created_at
            if gap > SESSION_GAP:
                sessions.append(current_session)
                current_session = [interactions[i]]
            else:
                current_session.append(interactions[i])
        sessions.append(current_session)
        
        # Пропускаем текущую сессию (первая в списке — самая новая)
        past_sessions = sessions[1:4]  # Берём 3 предыдущих сессии
        
        if not past_sessions:
            return None
        
        summaries = []
        for sess in past_sessions:
            # Извлекаем ключевые фразы из сообщений сессии
            topics = []
            for msg in sess:
                if msg.content and len(msg.content) > 3:
                    # Берём первые 50 символов каждого сообщения
                    topics.append(msg.content[:50].strip())
            
            if topics:
                when = sess[0].created_at
                time_ago = (datetime.now(pytz.UTC) - when.replace(tzinfo=pytz.UTC))
                if time_ago.days > 0:
                    ago_str = f"{time_ago.days}дн назад"
                else:
                    hours = time_ago.seconds // 3600
                    ago_str = f"{hours}ч назад" if hours > 0 else "недавно"
                
                # Краткое описание сессии
                topic_preview = "; ".join(topics[:3])
                summaries.append(f"[{ago_str}, {len(sess)} сообщ.] {topic_preview}")
        
        return "\n".join(summaries) if summaries else None
    except Exception as e:
        logger.warning(f"[SESSION_SUMMARY] Error: {e}")
        return None


async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None, message_type=None, progress_callback=None, web_context: bool = False):
    """Функция чата — делегирует в автономный агент (единая точка входа)."""

    logger.info(f"[CHAT_WITH_AI] START - user_id={user_id}, message='{message[:50]}...'")

    if user_id is None:
        logger.error("[CHAT_WITH_AI] ERROR: user_id is None!")
        return {'response': _t('error_user_not_found', 'ru'), 'tool_calls': []}

    try:
        # Делегируем в автономный агент — он сам строит контекст,
        # загружает профиль, погоду, новости, историю и т.д.
        response_data = await autonomous_chat_with_ai(
            message=message,
            context=context,
            user_id=user_id,
            file_content=file_content,
            db_session=db_session,
            message_type=message_type,
            subscription_tier=None,
            progress_callback=progress_callback,
            web_context=web_context
        )
        
        if response_data and 'response' in response_data:
            logger.info(f"[CHAT_WITH_AI] Response length: {len(response_data['response'])} chars")

        return response_data

    except Exception as e:
        logger.error(f"[CHAT_WITH_AI] ERROR: {e}")
        import traceback
        traceback.print_exc()
        try:
            from i18n import get_user_lang
            _err_lang = get_user_lang(user_id) if user_id else 'ru'
        except Exception:
            _err_lang = 'ru'
        return {
            'response': _t('error_processing', _err_lang),
            'tool_calls': []
        }

async def generate_reminder(user_id, task_title, task_id=None, escalation_level=1):
    """Generates a reminder through the unified agent brain (with tool calling).
    
    Args:
        user_id: User ID
        task_title: Task title
        task_id: Task ID (optional)
        escalation_level: Escalation level (1=soft, 2=insistent, 3=critical)
    """
    try:
        from .autonomous_agent import get_autonomous_agent
        from i18n import get_user_lang
        agent = get_autonomous_agent()
        lang = get_user_lang(user_id)

        if lang == 'en':
            escalation_tones = {
                1: "friendly and soft, like from a friend",
                2: "insistent (this is a REPEAT reminder, 15 minutes passed)",
                3: "urgent and serious (CRITICAL reminder, task needs immediate attention)"
            }
            tone = escalation_tones.get(escalation_level, escalation_tones[1])
            instruction = (
                f"Reminder about task \"{task_title}\""
                f"{f' (ID: {task_id})' if task_id else ''}.\n"
                f"Tone: {tone}. Escalation: {escalation_level}/3.\n"
                "Think: can you HELP solve this task, not just remind?\n"
                "Use find_relevant_contacts_for_task to check if someone in their network does similar activity — if so, mention it.\n"
                "Ask about status."
            )
        else:
            escalation_tones = {
                1: "дружелюбный и мягкий, как от друга",
                2: "настойчивый (это ПОВТОРНОЕ напоминание, прошло 15 минут)",
                3: "срочный и серьёзный (КРИТИЧЕСКОЕ напоминание, задача требует немедленного внимания)"
            }
            tone = escalation_tones.get(escalation_level, escalation_tones[1])
            instruction = (
                f"Напоминание о задаче «{task_title}»"
                f"{f' (ID: {task_id})' if task_id else ''}.\n"
                f"Тон: {tone}. Эскалация: {escalation_level}/3.\n"
                "Подумай: можешь ли ты ПОМОЧЬ решить эту задачу, а не просто напомнить?\n"
                "Используй find_relevant_contacts_for_task чтобы проверить, есть ли кто-то из сети кто тоже занимается похожей активностью — и если да, упомяни это.\n"
                "Спроси о статусе."
            )

        result = await agent.generate_system_message(
            user_id=user_id,
            mode='reminder',
            instruction=instruction,
            max_tokens=800,
            max_iterations=3
        )
        
        logger.info(f"[REMINDER] Generated via agent brain: {result[:100]}...")
        return result

    except Exception as e:
        logger.error(f"Error in generate_reminder: {e}", exc_info=True)
        from i18n import get_user_lang
        lang = get_user_lang(user_id)
        if lang == 'en':
            return f"Reminder about task: {task_title}\nTime to get started. Ready to begin?"
        return f"Напоминание о задаче: {task_title}\nВремя приступить к выполнению. Готов начать?"


async def generate_result_check(user_id, task_title):
    """Generates a task completion congratulation through the unified agent brain."""
    try:
        from .autonomous_agent import get_autonomous_agent
        from i18n import get_user_lang
        agent = get_autonomous_agent()
        lang = get_user_lang(user_id)

        if lang == 'en':
            instruction = (
                f"Task \"{task_title}\" marked as completed. "
                "Congratulate briefly and positively (1-2 sentences). "
                "Don't ask additional questions."
            )
        else:
            instruction = (
                f"Задача «{task_title}» отмечена как выполненная. "
                "Поздравь с завершением кратко и позитивно (1-2 предложения). "
                "Не задавай дополнительных вопросов."
            )

        result = await agent.generate_system_message(
            user_id=user_id,
            mode='result_check',
            instruction=instruction,
            max_tokens=150,
            max_iterations=1
        )

        logger.info(f"[RESULT_CHECK] Generated via agent brain: {result[:100]}...")
        return result

    except Exception as e:
        logger.error(f"Error in generate_result_check: {e}")
        from i18n import get_user_lang
        lang = get_user_lang(user_id)
        if lang == 'en':
            return f"Task \"{task_title}\" completed. Great job! 🎉"
        return f"Задача «{task_title}» выполнена. Отличная работа! 🎉"





def _analyze_proactive_engagement(user_db_id, session):
    """Анализирует реакции пользователя на проактивные сообщения.
    
    Смотрит: были ли ответы пользователя после AI-сообщений?
    Если да — тема вызвала интерес. Если нет — тема проигнорирована.
    Возвращает dict с engagement данными для адаптации будущих сообщений.
    """
    try:
        # Получаем последние 20 ai+user сообщений
        recent = session.query(Interaction).filter(
            Interaction.user_id == user_db_id,
            Interaction.message_type.in_(['ai', 'user'])
        ).order_by(Interaction.created_at.desc()).limit(20).all()
        
        if len(recent) < 4:
            return {}
        
        # Ищем пары: AI → user ответ (engaged) vs AI → долгое молчание (ignored)
        engaged_topics = []
        ignored_count = 0
        total_proactive = 0
        
        for i in range(len(recent) - 1):
            msg = recent[i]
            prev = recent[i + 1]
            
            # Нашли AI-сообщение, за которым следует user-ответ
            if prev.message_type == 'ai' and msg.message_type == 'user':
                gap = (msg.created_at - prev.created_at).total_seconds()
                total_proactive += 1
                
                if gap < 3600:  # Ответ в течение часа = engaged
                    # Извлекаем ключевые слова из AI-сообщения
                    if prev.content:
                        content_lower = prev.content[:200].lower()
                        topic_map = {
                            'задач': 'задачи',
                            'прогресс': 'продуктивность',
                            'погод': 'погода',
                            'новост': 'новости',
                            'партн': 'партнёры',
                            'цел': 'цели',
                            'совет': 'советы',
                            'трен': 'тренды',
                            'здоров': 'здоровье',
                            'финанс': 'финансы',
                            'дедлайн': 'дедлайны',
                            'застой': 'ревизия_задач',
                            'просроч': 'алерты',
                        }
                        for keyword, topic in topic_map.items():
                            if keyword in content_lower:
                                engaged_topics.append(topic)
                                break
                else:
                    ignored_count += 1
        
        result = {
            'engaged_topics': engaged_topics,
            'ignored_count': ignored_count,
            'total_proactive': total_proactive,
        }
        
        # Формируем summary для промпта
        if engaged_topics:
            from collections import Counter
            top_engaged = Counter(engaged_topics).most_common(3)
            topics_str = ", ".join(f"{t}({c})" for t, c in top_engaged)
            result['summary'] = f"Вовлечённость: реагирует на темы [{topics_str}]"
            if ignored_count > total_proactive * 0.6:
                result['summary'] += ", часто игнорирует проактивные"
        elif total_proactive > 3 and ignored_count > total_proactive * 0.7:
            result['summary'] = "Редко реагирует на проактивные — лучше писать только по делу"
        
        return result
    
    except Exception as e:
        logger.warning(f"[ENGAGEMENT] Analysis error: {e}")
        return {}


async def _build_proactive_context(user_id, lang='ru'):
    """Собирает полный контекст пользователя для проактивного сообщения.
    Возвращает dict со всеми данными или None если пользователь не найден."""
    months = _T['months'].get(lang, _T['months']['ru'])
    
    db_session = Session()
    try:
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        ctx = {
            'user': user,
            'username': f"@{user.username}" if user.username else "@unknown",
            'subscription_tier': None,  # Тарифы убраны, оплата токенами
        }
        
        # Время пользователя
        base_now = datetime.now(pytz.UTC)
        user_tz = pytz.timezone('Europe/Moscow')
        user_now = base_now.astimezone(user_tz)
        
        if user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
                user_now = base_now.astimezone(user_tz)
            except Exception as e:
                logger.debug(f"Invalid user timezone '{user.timezone}': {e}")
        
        ctx['user_tz'] = user_tz
        ctx['user_now'] = user_now
        ctx['current_time_str'] = f"{user_now.strftime('%H:%M')} ({user_tz.zone})"
        ctx['current_date_str'] = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        
        # Память пользователя — фильтруем tool-логи
        user_memory = ""
        if user.memory:
            try:
                import re as _re
                decrypted = decrypt_data(user.memory)
                # Убираем строки с результатами tool-вызовов — они НЕ факты
                _TOOL_JUNK_RE = _re.compile(
                    r'^(Искал:.*|create_goal:.*|update_goal_progress:.*|'
                    r'set_content_strategy:.*|set_contact_alert:.*|'
                    r'research_topic:.*|get_news_trends:.*|'
                    r'hide_contact:.*|AI iter \d+:.*)$',
                    _re.MULTILINE
                )
                decrypted = _TOOL_JUNK_RE.sub('', decrypted).strip()
                decrypted = '\n'.join(line for line in decrypted.split('\n') if line.strip())
                if decrypted:
                    user_memory = f"\n{_t('user_info', lang)}: {decrypted}"
            except Exception as e:
                logger.warning(f"Failed to decrypt user memory for user {user.id}: {e}")
        
        # Профиль
        profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
        ctx['profile'] = profile
        
        if profile:
            profile_parts = []
            for field, label in _T['profile_fields'].get(lang, _T['profile_fields']['ru']):
                val = getattr(profile, field, None)
                if val:
                    profile_parts.append(f"{label}: {val}")
            # Контактные данные из User (email, phone)
            if user.email:
                profile_parts.append(f"Email: {user.email}")
            if user.phone:
                profile_parts.append(f"{'Телефон' if lang == 'ru' else 'Phone'}: {user.phone}")
            if profile_parts:
                user_memory += f"\n{_t('profile', lang)}: {', '.join(profile_parts)}"
            
            # Статистика продуктивности
            stats_parts = []
            if profile.total_tasks_created:
                stats_parts.append(f"{_t('tasks_created', lang)}: {profile.total_tasks_created}")
            if profile.completed_tasks:
                stats_parts.append(f"{_t('completed', lang)}: {profile.completed_tasks}")
            if profile.skipped_tasks:
                stats_parts.append(f"{_t('skipped', lang)}: {profile.skipped_tasks}")
            if profile.average_completion_time:
                stats_parts.append(f"{_t('avg_time', lang)}: {profile.average_completion_time}")
            if stats_parts:
                user_memory += f"\n{_t('stats_label', lang)}: {', '.join(stats_parts)}"
        
        # Долгосрочная память (интересы, проекты, поисковые паттерны)
        ctx['long_term_data'] = {}
        if user.long_term_memory:
            try:
                ltm = json.loads(decrypt_data(user.long_term_memory))
                ctx['long_term_data'] = ltm
                
                # Интересы с весами — что пользователю реально важно
                interests = ltm.get('interests', {})
                if interests:
                    sorted_interests = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:5]
                    interest_str = ", ".join(f"{topic} ({count})" for topic, count in sorted_interests)
                    user_memory += f"\n{_t('key_interests', lang)}: {interest_str}"
                
                # Последние поисковые запросы — что волнует прямо сейчас
                searches = ltm.get('search_history', [])
                if searches:
                    recent_queries = [s['query'] for s in searches[-5:]]
                    user_memory += f"\n{_t('recent_searches', lang)}: {', '.join(recent_queries)}"
                
                # Проекты — долгосрочные активности
                projects = ltm.get('projects', {})
                if projects:
                    project_names = list(projects.keys())[-3:]
                    user_memory += f"\n{_t('projects_label', lang)}: {', '.join(project_names)}"
            except Exception as e:
                logger.warning(f"[PROACTIVE] Could not parse long_term_memory: {e}")
        
        # Погода и новости (async через api_client — не блокирует event loop)
        ctx['weather'] = None
        ctx['news'] = None
        try:
            from .api_client import get_api_client
            api = get_api_client()
            if profile and profile.city:
                weather_data = await api.get_weather(profile.city, cache_ttl=1800)
                if weather_data:
                    ctx['weather'] = (
                        f"{weather_data['city_name']}: {weather_data['temp']:.0f}°C, "
                        f"{weather_data['description']}, {_t('humidity', lang)} {weather_data['humidity']}%, "
                        f"{_t('wind', lang)} {weather_data['wind_speed']} {_t('wind_unit', lang)}"
                    )
                news_articles = await api.get_news(topic=profile.city, page_size=3, cache_ttl=21600)
                if news_articles:
                    titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                    if titles:
                        ctx['news'] = f"{_t('news_city', lang).format(city=profile.city)}:\n" + "\n".join(titles)
            if not ctx['news']:
                news_articles = await api.get_news(page_size=3, cache_ttl=21600)
                if news_articles:
                    titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                    if titles:
                        ctx['news'] = "Свежие новости России:\n" + "\n".join(titles)
            
            if ctx['weather']:
                user_memory += f"\n\n{_t('weather_hdr', lang)}: {ctx['weather']}"
            if ctx['news']:
                user_memory += f"\n\n{_t('news_hdr', lang)}:\n{ctx['news']}"
        except Exception as e:
            logger.warning(f"[PROACTIVE] Could not load weather/news: {e}")
        
        # Партнёры / инсайты (доступно всем, оплата токенами)
        ctx['partners'] = ""
        try:
            from .premium_simple import collect_premium_insights, manage_recommendations
            import asyncio
            try:
                insights_ctx = await collect_premium_insights(user_id, mode='prompt', session=db_session)
            except Exception:
                insights_ctx = None
            if insights_ctx and isinstance(insights_ctx, str) and insights_ctx.strip():
                ctx['partners'] = insights_ctx
                user_memory += f"\n\n{_t('insights_hdr', lang)}:\n{insights_ctx}"
            else:
                partner_ctx = manage_recommendations(user_id, 'get', session=db_session)
                if partner_ctx and partner_ctx.strip():
                    ctx['partners'] = partner_ctx
                    user_memory += f"\n\n{_t('partners_hdr', lang)}:\n{partner_ctx}"
        except Exception as e:
            logger.warning(f"[PROACTIVE] Could not load partners: {e}")
        
        # Цели пользователя
        active_goals = db_session.query(Goal).filter_by(user_id=user.id, status='active').order_by(Goal.priority.desc()).limit(5).all()
        ctx['goals'] = active_goals
        if active_goals:
            goal_lines = []
            for g in active_goals:
                line = f"{g.title} ({g.progress_percentage}%)"
                if g.target_date:
                    days = g.days_until_target()
                    if days is not None and days < 0:
                        line += f" {_t('overdue_mark', lang)}"
                    elif days is not None and days <= 7:
                        line += f" ⏳{days}{_t('days_short', lang)}"
                goal_lines.append(line)
            user_memory += f"\n\n{_t('goals_hdr', lang)}:\n" + "\n".join(f"- {l}" for l in goal_lines)
        
        # Задачи — сводка
        pending_count = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
        ctx['task_count'] = pending_count
        if pending_count:
            user_memory += f"\n{_t('active_tasks', lang)}: {pending_count}"
        
        # Задачи с деталями для автономной помощи
        pending_tasks_full = db_session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == "pending"
        ).order_by(Task.reminder_time).limit(10).all()
        ctx['pending_tasks_full'] = pending_tasks_full
        
        # Просроченные задачи
        overdue = db_session.query(Task).filter(
            Task.user_id == user.id,
            Task.reminder_time < user_now,
            Task.status == "pending"
        ).limit(5).all()
        ctx['overdue_count'] = len(overdue)
        ctx['overdue_titles'] = [t.title for t in overdue]
        if overdue:
            user_memory += f"\n{_t('overdue_hdr', lang)}: {', '.join(ctx['overdue_titles'])}"
        
        # === АЛЕРТЫ: задачи с приближающимся дедлайном (в ближайшие 24 часа) ===
        deadline_soon = db_session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == "pending",
            Task.reminder_time.isnot(None),
            Task.reminder_time >= base_now,
            Task.reminder_time <= base_now + timedelta(hours=24)
        ).order_by(Task.reminder_time).limit(5).all()
        ctx['deadline_soon'] = deadline_soon
        if deadline_soon:
            titles = [f"{t.title} ({_t('in_hours', lang).format(h=max(1, int((t.reminder_time.replace(tzinfo=pytz.UTC) - base_now).total_seconds() / 3600)))})" for t in deadline_soon]
            user_memory += f"\n{_t('deadline_soon', lang)}: {', '.join(titles)}"
        
        # === АЛЕРТЫ: цели с приближающейся датой или высоким прогрессом ===
        ctx['goals_alert'] = []
        if active_goals:
            for g in active_goals:
                days = g.days_until_target()
                if days is not None and 0 < days <= 3:
                    ctx['goals_alert'].append({'goal': g.title, 'type': 'deadline_close', 'days': days, 'progress': g.progress_percentage})
                elif g.progress_percentage >= 80 and g.progress_percentage < 100:
                    ctx['goals_alert'].append({'goal': g.title, 'type': 'almost_done', 'progress': g.progress_percentage})
                elif g.progress_percentage == 0 and g.created_at and (base_now - g.created_at.replace(tzinfo=pytz.UTC)).days > 3:
                    ctx['goals_alert'].append({'goal': g.title, 'type': 'stagnant', 'days_old': (base_now - g.created_at.replace(tzinfo=pytz.UTC)).days})
            if ctx['goals_alert']:
                alert_strs = []
                for a in ctx['goals_alert']:
                    if a['type'] == 'deadline_close':
                        alert_strs.append(f"{a['goal']} — {_t('deadline_in', lang).format(d=a['days'], p=a['progress'])}")
                    elif a['type'] == 'almost_done':
                        alert_strs.append(f"{a['goal']} — {_t('almost_done', lang).format(p=a['progress'])}")
                    elif a['type'] == 'stagnant':
                        alert_strs.append(f"{a['goal']} — {_t('no_progress', lang).format(d=a['days_old'])}")
                user_memory += f"\n{_t('goal_alerts_hdr', lang)}: {'; '.join(alert_strs)}"
        
        # === АЛЕРТЫ: застой задач (много pending без движения) ===
        stale_tasks = db_session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == "pending",
            Task.created_at < base_now - timedelta(days=7)
        ).count()
        ctx['stale_task_count'] = stale_tasks
        if stale_tasks >= 3:
            user_memory += f"\n{_t('stale_tasks', lang).format(n=stale_tasks)}"
        
        # Поведенческий анализ
        insights = []
        recent = db_session.query(Interaction).filter_by(
            user_id=user.id
        ).order_by(Interaction.created_at.desc()).limit(10).all()
        
        topic_keywords = _TOPIC_KW.get(lang, _TOPIC_KW['ru'])
        
        recent_topics = set()
        last_user_msg = ""
        for inter in recent:
            if inter.content:
                c_lower = inter.content.lower()
                if inter.message_type == 'user' and not last_user_msg:
                    last_user_msg = inter.content[:200]
                for topic, words in topic_keywords.items():
                    if any(w in c_lower for w in words):
                        recent_topics.add(topic)
        
        if recent_topics:
            insights.append(f"{_t('recent_topics', lang)}: {', '.join(recent_topics)}")
        ctx['recent_topics'] = recent_topics
        ctx['last_user_msg'] = last_user_msg
        
        # Паттерны задач
        all_tasks = db_session.query(Task).filter_by(user_id=user.id).limit(20).all()
        if all_tasks:
            completed = sum(1 for t in all_tasks if t.status == 'completed')
            pending = sum(1 for t in all_tasks if t.status == 'pending')
            delegated = sum(1 for t in all_tasks if t.delegated_to_username)
            patterns = []
            if completed > pending * 0.7:
                patterns.append(_t('productive', lang))
            if delegated > len(all_tasks) * 0.3:
                patterns.append(_t('delegates_pat', lang))
            if patterns:
                insights.append(f"{_t('patterns_lbl', lang)}: {', '.join(patterns)}")
        
        # Время активности
        if user.last_interaction_at:
            h = user.last_interaction_at.hour
            if 6 <= h <= 10:
                insights.append(_t('active_morning', lang))
            elif 18 <= h <= 23:
                insights.append(_t('active_evening', lang))
        
        if insights:
            user_memory += f"\n\n{_t('insights_section', lang)}:\n" + "\n".join(f"- {i}" for i in insights)
        ctx['insights'] = insights
        
        # Последние ответы агента — для антиповторов
        last_ai_msgs = db_session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'ai'
        ).order_by(Interaction.created_at.desc()).limit(5).all()
        ctx['last_responses'] = [m.content[:80] for m in last_ai_msgs if m.content and len(m.content) > 10]
        
        # Активное обучение: анализ реакций на проактивные сообщения
        ctx['proactive_engagement'] = _analyze_proactive_engagement(user.id, db_session)
        engagement = ctx['proactive_engagement']
        if engagement.get('summary'):
            insights.append(engagement['summary'])
        
        ctx['user_memory'] = user_memory
        ctx['lang'] = lang
        return ctx
    
    finally:
        db_session.close()


def _build_situation_prompt(ctx, intent=None, tasks_list=None, overdue_tasks_list=None):
    """Формирует умный промпт с ФРЕЙМВОРКОМ МЫШЛЕНИЯ — AI анализирует ситуацию и действует.
    
    Returns:
        tuple: (prompt_text, selected_message_type) — промпт и выбранный тип сообщения
    """
    import random
    
    lang = ctx.get('lang', 'ru')
    parts = []
    parts.append(_t('sit_opening', lang))
    
    # === АНАЛИЗ СИТУАЦИИ ===
    hour = ctx['user_now'].hour
    if 6 <= hour < 12:
        time_of_day = _t('time_morning', lang)
    elif 12 <= hour < 18:
        time_of_day = _t('time_afternoon', lang)
    elif 18 <= hour < 22:
        time_of_day = _t('time_evening', lang)
    else:
        time_of_day = _t('time_late', lang)
    
    task_count = ctx.get('task_count', 0)
    overdue_count = ctx.get('overdue_count', 0)
    has_goals = bool(ctx.get('goals'))
    # ctx['profile'] — объект UserProfile или None
    profile_obj = ctx.get('profile')
    has_profile = bool(profile_obj and (getattr(profile_obj, 'goals', None) or getattr(profile_obj, 'interests', None) or getattr(profile_obj, 'skills', None)))
    
    parts.append(f"\n{_t('sit_situation', lang)}")
    parts.append(f"{_t('sit_time', lang)}: {time_of_day} ({ctx['user_now'].strftime('%H:%M')})")
    parts.append(f"{_t('sit_tasks', lang)}: {task_count}")
    if overdue_count > 0:
        parts.append(f"{_t('sit_overdue', lang)}: {overdue_count}")
    parts.append(f"{_t('sit_profile_ok', lang)}: {_t('yes', lang) if has_profile else _t('no', lang)}")
    parts.append(f"{_t('sit_goals_ok', lang)}: {_t('yes', lang) if has_goals else _t('no', lang)}")
    if ctx.get('recent_topics'):
        parts.append(f"{_t('recent_topics', lang)}: {', '.join(list(ctx['recent_topics'])[:3])}")
    
    # === НАБЛЮДЕНИЯ ===
    observations = []
    
    if not has_profile:
        observations.append(_t('obs_no_profile', lang))
    
    if not has_goals and has_profile:
        observations.append(_t('obs_no_goals', lang))
    
    if overdue_count > 0:
        observations.append(_t('obs_overdue', lang).format(n=overdue_count))
    
    if observations:
        parts.append(f"\n{_t('sit_obs', lang)}")
        parts.extend(observations)
    
    # === РЕСУРСЫ ЧЕЛОВЕКА ===
    if profile_obj:
        skills = getattr(profile_obj, 'skills', None) or ''
        interests = getattr(profile_obj, 'interests', None) or ''
        position = getattr(profile_obj, 'position', None) or ''
        prof_goals = getattr(profile_obj, 'goals', None) or ''
        
        resources = []
        if position: resources.append(f"{_t('res_role', lang)}: {position}")
        if skills: resources.append(f"{_t('res_skills', lang)}: {skills[:60]}")
        if interests: resources.append(f"{_t('res_interests', lang)}: {interests[:60]}")
        if prof_goals: resources.append(f"{_t('res_goals', lang)}: {prof_goals[:60]}")
        
        if resources:
            parts.append(f"\n{_t('sit_resources', lang)}\n" + "\n".join(resources))
    
    # === ДОСТУПНЫЕ ДАННЫЕ ===
    available = []
    if ctx.get('weather'):
        available.append(f"{_t('data_weather', lang)}: {ctx['weather']}")
    if ctx.get('news'):
        available.append(f"{_t('data_news', lang)}: {str(ctx['news'])[:400]}")
    if ctx.get('partners'):
        available.append(f"{_t('data_partners', lang)}: {str(ctx['partners'])[:300]}")
    if ctx.get('insights'):
        available.append(f"{_t('data_obs', lang)}: {'; '.join(ctx['insights'])}")
    
    if ctx.get('goals'):
        goal_lines = []
        for g in ctx['goals']:
            line = f"{g.title} ({g.progress_percentage}%)"
            if g.target_date:
                days = g.days_until_target()
                if days is not None and days < 0:
                    line += f" {_t('overdue_mark', lang).lower()}"
                elif days is not None and days <= 7:
                    line += f" ⏳{days}{_t('days_short', lang)}"
            goal_lines.append(line)
        available.append(f"{_t('data_goals', lang)}: " + ", ".join(goal_lines))
    
    # Долгосрочные данные
    ltm_data = ctx.get('long_term_data', {})
    if ltm_data.get('interests'):
        sorted_ints = sorted(ltm_data['interests'].items(), key=lambda x: x[1], reverse=True)[:5]
        available.append(f"{_t('data_interests', lang)}: {', '.join(f'{t}({c})' for t, c in sorted_ints)}")
    if ltm_data.get('search_history'):
        recent_q = [s['query'] for s in ltm_data['search_history'][-3:]]
        available.append(f"{_t('data_searches', lang)}: {', '.join(recent_q)}")
    if ltm_data.get('projects'):
        available.append(f"{_t('data_projects', lang)}: {', '.join(list(ltm_data['projects'].keys())[-3:])}")
    
    if available:
        parts.append(f"\n{_t('sit_data', lang)}\n" + "\n".join(available))
    
    # === ЗАДАЧИ ===
    if tasks_list:
        user_tz = ctx.get('user_tz', pytz.UTC)
        now_utc = datetime.now(pytz.UTC)
        upcoming = []
        overdue_found = []
        
        for t in tasks_list[:10]:
            if t.status != 'pending':
                continue
            time_str = ""
            if t.reminder_time:
                try:
                    rt = t.reminder_time if t.reminder_time.tzinfo else pytz.UTC.localize(t.reminder_time)
                    if rt < now_utc:
                        overdue_found.append(t.title)
                        continue
                    at_label = "at" if lang == 'en' else "на"
                    time_str = f" ({at_label} {rt.astimezone(user_tz).strftime('%H:%M')})"
                except Exception as e:
                    logger.debug(f"Failed to format reminder time for task '{t.title}': {e}")
            desc = f" — {t.description[:80]}" if t.description else ""
            upcoming.append(f"• {t.title}{time_str}{desc}")
        
        if upcoming:
            parts.append(f"\n{_t('sit_tasks_hdr', lang)}:\n" + "\n".join(upcoming[:5]))
        if overdue_found:
            parts.append(f"\n{_t('sit_overdue_hdr', lang)}: " + ", ".join(overdue_found[:5]))
    elif ctx.get('overdue_titles'):
        parts.append(f"\n{_t('sit_overdue_hdr', lang)}: " + ", ".join(ctx['overdue_titles']))
    
    # Специальные задачи если переданы отдельно
    if overdue_tasks_list:
        if hasattr(overdue_tasks_list[0], 'title'):
            titles = [t.title for t in overdue_tasks_list[:5]]
        else:
            task_lbl = 'Task' if lang == 'en' else 'Задача'
            titles = [t.get('title', task_lbl) for t in overdue_tasks_list[:5]]
        parts.append(f"\n{_t('sit_overdue_tasks', lang)} ({len(overdue_tasks_list)}):\n" + "\n".join(f"• {t}" for t in titles))
    
    # === АКЦЕНТ ===
    if intent:
        intent_labels = _INTENT_LABELS.get(lang, _INTENT_LABELS['ru'])
        if intent in intent_labels:
            parts.append(f"\n{_t('sit_accent', lang)}: {intent_labels[intent]}")
    
    # === РОТАЦИЯ ТИПОВ СООБЩЕНИЙ ===
    import hashlib
    uid = ctx['user'].telegram_id if ctx.get('user') else 0
    rotation_seed = f"{ctx['user_now'].strftime('%Y-%m-%d-%H')}_{uid}"
    rotation_hash = int(hashlib.md5(rotation_seed.encode()).hexdigest(), 16)
    
    # Доступные типы сообщений — собираем через bilingual helper
    message_types = _msg_type_instructions(lang, ctx, rotation_hash)
    
    # Выбираем тип по ротации
    selected = message_types[rotation_hash % len(message_types)]
    
    parts.append(f"\n{_t('sit_type', lang).format(t=selected['type'].upper())}")
    parts.append(selected['instruction'])
    
    if selected['type'] != 'plan':
        parts.append(f"\n{_t('sit_no_plan', lang)}")
    
    # === ВОВЛЕЧЁННОСТЬ ===
    engagement = ctx.get('proactive_engagement', {})
    if engagement.get('engaged_topics'):
        from collections import Counter
        top = Counter(engagement['engaged_topics']).most_common(3)
        parts.append(f"\n{_t('sit_reacts', lang)}: {', '.join(t for t, _ in top)}")
    if engagement.get('ignored_count', 0) > engagement.get('total_proactive', 0) * 0.6:
        parts.append(_t('sit_ignores', lang))
    
    # === КАК РАБОТАТЬ ===
    parts.append(_t('sit_rules', lang))
    
    return "\n".join(parts), selected['type']


async def generate_proactive_message(user_id, context="general", task_count=0, overdue_count=0, tasks_list=None):
    """Единый умный генератор проактивных сообщений через мозг агента.
    
    Использует _build_proactive_context() для ситуационного анализа,
    затем передаёт всё через agent.generate_system_message() с tool calling.
    """
    try:
        # 1. Собираем полный контекст ситуации
        from i18n import get_user_lang
        lang = get_user_lang(user_id)
        ctx = await _build_proactive_context(user_id, lang=lang)
        if not ctx:
            return _t('fallback_short', lang)
        
        # 2. Определяем intent
        intent = None
        hour = ctx['user_now'].hour
        
        if isinstance(context, str) and context == 'overdue_tasks':
            intent = 'overdue'
        elif hour < 12:
            intent = 'morning'
        elif hour >= 20:
            intent = 'evening'
        
        # 3. Формируем ситуационный промпт (красные флаги, доступные данные, правила)
        situation_prompt, selected_type = _build_situation_prompt(ctx, intent=intent, tasks_list=tasks_list)
        
        # 4. Антиповтор — запрещаем повторять последние ответы
        anti_repeat = ""
        if ctx.get('last_responses'):
            anti_repeat = _t('anti_repeat_intro', lang)
            anti_repeat += "\n".join(f"- {r}" for r in ctx['last_responses'])
            anti_repeat += _t('anti_repeat_suffix', lang)
        
        # 5. Генерируем через единый мозг агента
        from .autonomous_agent import get_autonomous_agent
        agent = get_autonomous_agent()

        instruction = _t('pro_instruction', lang)

        # Для task_help используем режим task_assist с увеличенными лимитами
        if selected_type == 'task_help':
            mode = 'task_assist'
            max_tokens = 1500
            max_iterations = 3
            instruction = _t('pro_task_help', lang)
        else:
            mode = 'proactive'
            max_tokens = 1200
            max_iterations = 2

        result = await agent.generate_system_message(
            user_id=user_id,
            mode=mode,
            instruction=instruction,
            extra_context=situation_prompt + anti_repeat,
            max_tokens=max_tokens,
            max_iterations=max_iterations
        )

        logger.info(f"[PROACTIVE] Generated via agent brain: {result[:100]}...")
        return result

    except Exception as e:
        logger.error(f"Error in generate_proactive_message: {e}\n{traceback.format_exc()}")
        try:
            from .utils import generate_unified_recommendations
            return generate_unified_recommendations('fallback', task_count=task_count, overdue_count=overdue_count)
        except Exception:
            from i18n import get_user_lang as _gul
            _lang = _gul(user_id)
            return _t('fallback_full', _lang)


async def generate_daily_report(user_id):
    """Вечерний итог — делегирует в generate_proactive_message с intent='evening'.
    Сохранена для обратной совместимости (используется в reminder_service)."""
    return await generate_proactive_message(user_id, context="general")


async def generate_overdue_reminder(user_id, overdue_tasks, escalation_level=1):
    """Напоминание о просроченных — делегирует в generate_proactive_message с intent='overdue'.
    Сохранена для обратной совместимости."""
    return await generate_proactive_message(
        user_id, context="overdue_tasks",
        overdue_count=len(overdue_tasks) if overdue_tasks else 0,
        tasks_list=overdue_tasks
    )


