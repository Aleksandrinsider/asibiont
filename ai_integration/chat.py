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
    determine_timezone_from_time,
    replace_placeholders, clean_technical_details,
    post_process_tool_calls,
    post_process_response,
    get_news_info,
    get_weather_info
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


async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None, message_type=None):
    """Функция чата с использованием tools-based подхода"""

    logger.info(f"[CHAT_WITH_AI] START - user_id={user_id}, message='{message[:50]}...'")

    if user_id is None:
        logger.error("[CHAT_WITH_AI] ERROR: user_id is None!")
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        # Получаем информацию о пользователе
        session = Session() if db_session is None else db_session
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                logger.error(f"[CHAT_WITH_AI] User not found: {user_id}")
                return {'response': "Пользователь не найден", 'tool_calls': []}

            # Получаем профиль пользователя
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()

            # Вычисляем полноту профиля
            profile_complete = False
            if profile:
                profile_missing = []
                if not profile.goals or not profile.goals.strip():
                    profile_missing.append('цели')
                if not profile.skills or not profile.skills.strip():
                    profile_missing.append('навыки')
                if not profile.interests or not profile.interests.strip():
                    profile_missing.append('интересы')
                if len(profile_missing) <= 1:  # Если отсутствует не более одного поля
                    profile_complete = True
            logger.info(f"[PROFILE] Profile complete: {profile_complete}, missing: {profile_missing if 'profile_missing' in locals() else []}")

            # Определяем текущее время пользователя
            base_now = datetime.now(pytz.UTC)
            user_now = base_now
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            current_date_str = user_now.strftime("%Y-%m-%d")
            
            months = [
                'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
            ]
            
            # Получаем timezone пользователя, по умолчанию Москва
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                logger.info(f"[DATETIME] User timezone: {user_timezone}, current_time_str: {current_time_str}, current_date_str: {current_date_str}")
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")
                # Fallback на московское время
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                    logger.info(f"[DATETIME] Fallback to Moscow: {current_time_str}, {current_date_str}")
                except Exception as e:
                    logger.warning(f"[DATETIME] Error in Moscow fallback: {e}")

            # Генерируем проактивный контекст
            from .context_builder import ContextBuilder
            context_builder = ContextBuilder()
            proactive_context = context_builder.build_proactive_context(user_id, session, profile_complete=profile_complete)
            logger.info(f"[PROACTIVE] Generated context length: {len(proactive_context)}")

            # Получаем погоду и новости для контекста
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_city = profile.city if profile and profile.city else None
            weather_info = get_weather_info(user_city) if user_city else None
            news_info = get_news_info(user_city) if user_city else get_news_info()
            logger.info(f"[CONTEXT] Weather: {bool(weather_info)}, News: {bool(news_info)}")

            # Расшифровываем память пользователя
            decrypted_memory = ""
            if user.memory:
                try:
                    decrypted_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.error(f"Error decrypting user memory: {e}")
            
            # Дополняем контекст данными из long_term_memory
            if user.long_term_memory:
                try:
                    ltm = json.loads(decrypt_data(user.long_term_memory))
                    interests = ltm.get('interests', {})
                    if interests:
                        sorted_interests = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:5]
                        interest_str = ", ".join(f"{topic} ({count})" for topic, count in sorted_interests)
                        decrypted_memory += f"\n🎯 Устойчивые интересы: {interest_str}"
                    searches = ltm.get('search_history', [])
                    if searches:
                        recent_queries = [s['query'] for s in searches[-5:]]
                        decrypted_memory += f"\n🔍 Недавно искал: {', '.join(recent_queries)}"
                    projects = ltm.get('projects', {})
                    if projects:
                        project_names = list(projects.keys())[-3:]
                        decrypted_memory += f"\n📁 Проекты: {', '.join(project_names)}"
                except Exception as e:
                    logger.warning(f"[CHAT] Could not parse long_term_memory: {e}")
            
            # Дополняем контекст статистикой продуктивности
            if profile:
                stats_parts = []
                if profile.total_tasks_created:
                    stats_parts.append(f"создано задач: {profile.total_tasks_created}")
                if profile.completed_tasks:
                    stats_parts.append(f"завершено: {profile.completed_tasks}")
                if profile.skipped_tasks:
                    stats_parts.append(f"пропущено: {profile.skipped_tasks}")
                if profile.average_completion_time:
                    stats_parts.append(f"ср. время: {profile.average_completion_time}")
                if stats_parts:
                    decrypted_memory += f"\n📊 Статистика: {', '.join(stats_parts)}"
            
            # Цели пользователя
            active_goals = session.query(Goal).filter_by(user_id=user.id, status='active').order_by(Goal.priority.desc()).limit(5).all()
            if active_goals:
                goal_lines = []
                for g in active_goals:
                    line = f"{g.title} ({g.progress_percentage}%)"
                    if g.target_date:
                        days = g.days_until_target()
                        if days is not None and days < 0:
                            line += " ⚠️ПРОСРОЧЕНО"
                        elif days is not None and days <= 7:
                            line += f" ⏳{days}дн"
                    goal_lines.append(line)
                decrypted_memory += f"\n🎯 ЦЕЛИ:\n" + "\n".join(f"- {l}" for l in goal_lines)
            
            # Сводка предыдущих сессий (контекстное окно)
            try:
                session_summary = _build_session_summary(user.id, session)
                if session_summary:
                    decrypted_memory += f"\n\n📝 ПРЕДЫДУЩИЕ СЕССИИ:\n{session_summary}"
            except Exception as e:
                logger.warning(f"[CHAT] Could not build session summary: {e}")

            # Получаем информацию о текущей задаче если есть
            current_task_info = None
            if user.current_task_id:
                try:
                    task = session.query(Task).filter_by(id=user.current_task_id).first()
                    if task:
                        current_task_info = {
                            'id': task.id,
                            'title': task.title,
                            'status': task.status
                        }
                        logger.info(f"[CONTEXT] Current task in focus: '{task.title}' (ID: {task.id})")
                except Exception as e:
                    logger.error(f"Error loading current task: {e}")

            # Получаем системный промпт с проактивным контекстом
            system_prompt = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=current_time_str,
                current_date_str=current_date_str,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=decrypted_memory,
                context=context,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'LIGHT'),
                message_type=message_type,
                weather_info=weather_info,
                news_info=news_info,
                proactive_context=proactive_context,
                current_task_info=current_task_info,
                user_id_param=user_id
            )

            # Используем улучшенный гибридный автономный агент (трёхэтапный подход)
            response_data = await autonomous_chat_with_ai(
                message=message,
                context=context,
                user_id=user_id,
                file_content=file_content,
                db_session=session,
                message_type=message_type,
                subscription_tier=getattr(user, 'subscription_tier', 'LIGHT')
            )
            
            # ПОСТ-ОБРАБОТКА: минимальная очистка (агент сам формирует проактивный язык через промпт)
            if response_data and 'response' in response_data:
                logger.info(f"[POST_PROCESS] Response length: {len(response_data['response'])} chars")
            
            # Отмечаем что Premium рекомендации были показаны (если были в промпте)
            if proactive_context and "ПРЕМИУМ РЕКОМЕНДАЦИИ" in proactive_context:
                try:
                    from ai_integration.premium_simple import manage_recommendations
                    manage_recommendations(user_id, 'mark_shown', session=session)
                    logger.info(f"[PREMIUM] Marked recommendations as shown for user {user_id}")
                except Exception as e:
                    logger.warning(f"[PREMIUM] Failed to mark recommendations: {e}")

            return response_data

        finally:
            if db_session is None:
                session.close()

        return response_data

    except Exception as e:
        logger.error(f"[CHAT_WITH_AI] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            'response': f"Извините, произошла ошибка при обработке запроса: {str(e)}",
            'tool_calls': []
        }

async def generate_reminder(user_id, task_title, task_id=None, escalation_level=1):
    """Генерирует текст напоминания о задаче с полным контекстом
    
    Args:
        user_id: ID пользователя
        task_title: Название задачи
        task_id: ID задачи (опционально)
        escalation_level: Уровень эскалации (1=мягко, 2=настойчиво, 3=критично)
    """
    try:
        # Получить полную информацию о задаче и пользователе
        db_session = Session()
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        
        if not user:
            db_session.close()
            return f"Привет! Напоминаю о задаче: {task_title}. Время начать!"
        
        # Получить задачу для дополнительного контекста
        task = None
        task_context = ""
        if task_id:
            task = db_session.query(Task).filter_by(id=task_id).first()
            if task:
                # Добавляем контекст о делегировании
                if task.delegated_to_username:
                    delegator = db_session.query(User).filter_by(id=task.user_id).first()
                    delegator_name = f"@{delegator.username}" if delegator and delegator.username else "другой пользователь"
                    task_context += f"\nЭто делегированная задача от {delegator_name}."
                
                # Описание задачи
                if task.description:
                    try:
                        desc = decrypt_data(task.description)
                        if desc:
                            task_context += f"\nДетали: {desc}"
                    except Exception as e:
                        logger.warning(f"[CONTEXT] Error decrypting task description: {e}")
        
        # Получить память и профиль пользователя
        user_memory = ""
        profile_context = ""
        if user.memory:
            try:
                decrypted = decrypt_data(user.memory)
                user_memory = f"\nИнформация о пользователе: {decrypted}"
            except Exception as e:
                logger.warning(f"[CONTEXT] Error decrypting user memory: {e}")
        
        # Получить профиль для контекста
        profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            if profile.current_plans:
                profile_context += f"\nТекущие планы пользователя: {profile.current_plans}"
            if profile.goals:
                profile_context += f"\nЦели: {profile.goals}"
        
        db_session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        base_now = datetime.now(pytz.UTC)
        user_now = base_now  # Default to UTC
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        
        months = [
            'января',
            'февраля',
            'марта',
            'апреля',
            'мая',
            'июня',
            'июля',
            'августа',
            'сентября',
            'октября',
            'ноября',
            'декабря']
        
        # Get user timezone if available, default to Moscow if not set
        user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
        try:
            user_tz = pytz.timezone(user_timezone)
            user_now = base_now.astimezone(user_tz)
            current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
            current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        except Exception as e:
            logger.error(f"Error setting user timezone for reminder: {e}")
            # Fallback to Moscow time
            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(moscow_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.warning(f"[CHAT] Failed Moscow timezone fallback: {e}")
        
        user_username = user.username if user and user.username else "пользователь"
        mentions_str = ""
        
        # Анализ времени суток для контекстного тона
        hour = user_now.hour
        time_context = ""
        if 0 <= hour < 6:
            time_context = "Ранее утро (0-6): тон очень мягкий, деликатный"
        elif 6 <= hour < 9:
            time_context = "Утро (6-9): бодрый, мотивирующий тон"
        elif 9 <= hour < 12:
            time_context = "До обеда (9-12): рабочий, продуктивный тон"
        elif 12 <= hour < 14:
            time_context = "Обед (12-14): легкий, ненавязчивый тон"
        elif 14 <= hour < 18:
            time_context = "После обеда (14-18): активный, деловой тон"
        elif 18 <= hour < 22:
            time_context = "Вечер (18-22): умеренный, спокойный тон"
        else:
            time_context = "Позднее время (22-0): очень мягкий, расслабленный тон"

        base_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            message_type='reminder')

        system_prompt = base_prompt

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        # Настройка тона в зависимости от уровня эскалации
        escalation_prompts = {
            1: """Сгенерируй ДРУЖЕЛЮБНОЕ и МЯГКОЕ напоминание о задаче: '{task_title}'.

ВРЕМЯ СУТОК: {time_context}

ТОН: Легкий, не навязчивый, поддерживающий (адаптируй под время суток)
СТИЛЬ: Как напоминание от друга, мотивирующее

ФОРМАТ ОТВЕТА: Напиши готовое сообщение для отправки пользователю (1-2 абзаца максимум).
- Начни с дружеского приветствия с учётом времени суток
- Напомни о задаче деликатно
- Добавь мотивацию и практические советы
- ОБЯЗАТЕЛЬНО ЗАКОНЧИ ВОПРОСОМ: "Задача выполнена?" или "Как продвигается?"
- НЕ пиши промежуточные мысли или "сейчас посмотрю задачи"

КОНТЕКСТ ЗАДАЧИ:{context_tasks}
КОНТЕКСТ ПРОФИЛЯ:{context_profile}""",
            
            2: """Сгенерируй НАСТОЙЧИВОЕ повторное напоминание о задаче: '{task_title}'.

ВНИМАНИЕ: ЭТО ПОВТОРНОЕ НАПОМИНАНИЕ - прошло 15 минут с первого

ВРЕМЯ СУТОК: {time_context}

ТОН: Более настойчивый, но всё ещё дружелюбный и мотивирующий (адаптируй под время суток)
СТИЛЬ: Акцент на важности задачи и последствиях откладывания

ФОРМАТ ОТВЕТА: Напиши готовое сообщение для отправки пользователю (2-3 абзаца).
- УКАЖИ что это повторное напоминание
- Подчеркни важность задачи
- Спроси что мешает начать или предложи разбить на части
- Дай конкретный совет как приступить
- ОБЯЗАТЕЛЬНО ЗАКОНЧИ ВОПРОСОМ О СТАТУСЕ

КОНТЕКСТ ЗАДАЧИ:{context_tasks}
КОНТЕКСТ ПРОФИЛЯ:{context_profile}""",
            
            3: """Сгенерируй КРИТИЧНОЕ напоминание о задаче: '{task_title}'.

КРИТИЧЕСКОЕ НАПОМИНАНИЕ - задача требует срочного внимания

ВРЕМЯ СУТОК: {time_context}

ТОН: Срочный, серьёзный, но конструктивный (несмотря на время суток)
СТИЛЬ: Акцент на последствиях и необходимости действовать сейчас

ФОРМАТ ОТВЕТА: Напиши готовое сообщение для отправки пользователю (2-3 абзаца).
- ЯВНО укажи критичность ситуации
- Объясни возможные последствия откладывания
- Предложи экстренный план действий (первый минимальный шаг)
- Спроси нужна ли помощь/делегирование/перенос
- ОБЯЗАТЕЛЬНО ЗАКОНЧИ ТРЕБОВАНИЕМ ОТВЕТА

КОНТЕКСТ ЗАДАЧИ:{context_tasks}
КОНТЕКСТ ПРОФИЛЯ:{context_profile}"""
        }
        
        prompt_template = escalation_prompts.get(escalation_level, escalation_prompts[1])
        user_prompt = prompt_template.format(
            task_title=task_title,
            time_context=time_context,
            context_tasks=task_context if task_context else 'Нет дополнительного контекста',
            context_profile=profile_context if profile_context else 'Нет информации о профиле'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.8, "max_tokens": 200}  # Уменьшено с 300
        
        logger.info(f"[REMINDER] Generating AI reminder for task_id={task_id}, user={user_id}")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)
                    
                    logger.info(f"[REMINDER] AI generated: {content[:100]}...")
                    return content
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to generate reminder: status {response.status}, error: {error_text}")
                    # Более качественный fallback
                    return f"Напоминание о задаче: {task_title}\n\nПора приступить к выполнению. Как планируете подойти к задаче?"
    except Exception as e:
        logger.error(f"Error in generate_reminder: {e}", exc_info=True)
        # Более качественный fallback с контекстом
        return f"Напоминание о задаче: {task_title}\n\nВремя приступить к выполнению. Готов начать?"


async def generate_result_check(user_id, task_title):
    """Генерирует вопрос о результате выполнения задачи"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            db_session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений


        base_now = datetime.now(pytz.UTC)
        user_now = base_now  # Default to UTC
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        
        months = [
            'января',
            'февраля',
            'марта',
            'апреля',
            'мая',
            'июня',
            'июля',
            'августа',
            'сентября',
            'октября',
            'ноября',
            'декабря']
        
        # Get user timezone if available, default to Moscow if not set
        user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
        try:
            user_tz = pytz.timezone(user_timezone)
            user_now = base_now.astimezone(user_tz)
            current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
            current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        except Exception as e:
            logger.error(f"Error setting user timezone for result_check: {e}")
            # Fallback to Moscow time
            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(moscow_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.warning(f"[RESULT_CHECK] Failed Moscow timezone fallback: {e}")
        
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            message_type='result_check')

        system_prompt = base_prompt

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Задача '{task_title}' отмечена как выполненная. Поздравь с завершением задачи кратко и позитивно (1-2 предложения). Не задавай дополнительных вопросов.",
            },
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    # Заменяем плейсхолдеры на реальные значения
                    content = replace_placeholders(
                        content, datetime.now(pytz.UTC), datetime.now(pytz.UTC).strftime("%H:%M")
                    )
                    content = clean_technical_details(content)

                    return content
                else:
                    logger.error(f"Failed to generate result check: status {response.status}")
                    return f"Задача '{task_title}' выполнена успешно."
    except Exception as e:
        logger.error(f"Error in generate_result_check: {e}")
        return f"Задача '{task_title}' выполнена."





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


async def _build_proactive_context(user_id):
    """Собирает полный контекст пользователя для проактивного сообщения.
    Возвращает dict со всеми данными или None если пользователь не найден."""
    months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ]
    
    db_session = Session()
    try:
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        ctx = {
            'user': user,
            'username': f"@{user.username}" if user.username else "@unknown",
            'subscription_tier': user.subscription_tier.value if user.subscription_tier else None,
        }
        
        # Время пользователя
        base_now = datetime.now(pytz.UTC)
        user_tz = pytz.timezone('Europe/Moscow')
        user_now = base_now.astimezone(user_tz)
        
        if user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
                user_now = base_now.astimezone(user_tz)
            except Exception:
                pass
        
        ctx['user_tz'] = user_tz
        ctx['user_now'] = user_now
        ctx['current_time_str'] = f"{user_now.strftime('%H:%M')} ({user_tz.zone})"
        ctx['current_date_str'] = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        
        # Память пользователя
        user_memory = ""
        if user.memory:
            try:
                decrypted = decrypt_data(user.memory)
                user_memory = f"\nИнформация о пользователе: {decrypted}"
            except Exception:
                pass
        
        # Профиль
        profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
        ctx['profile'] = profile
        
        if profile:
            profile_parts = []
            for field, label in [('city', 'Город'), ('company', 'Компания'), ('position', 'Должность'),
                                  ('languages', 'Языки'), ('skills', 'Навыки'), ('interests', 'Интересы'), ('goals', 'Цели')]:
                val = getattr(profile, field, None)
                if val:
                    profile_parts.append(f"{label}: {val}")
            if profile_parts:
                user_memory += f"\nПрофиль: {', '.join(profile_parts)}"
            
            # Статистика продуктивности
            stats_parts = []
            if profile.total_tasks_created:
                stats_parts.append(f"создано задач: {profile.total_tasks_created}")
            if profile.completed_tasks:
                stats_parts.append(f"завершено: {profile.completed_tasks}")
            if profile.skipped_tasks:
                stats_parts.append(f"пропущено: {profile.skipped_tasks}")
            if profile.average_completion_time:
                stats_parts.append(f"ср. время выполнения: {profile.average_completion_time}")
            if stats_parts:
                user_memory += f"\n📊 Статистика: {', '.join(stats_parts)}"
        
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
                    user_memory += f"\n🎯 Устойчивые интересы: {interest_str}"
                
                # Последние поисковые запросы — что волнует прямо сейчас
                searches = ltm.get('search_history', [])
                if searches:
                    recent_queries = [s['query'] for s in searches[-5:]]
                    user_memory += f"\n🔍 Недавно искал: {', '.join(recent_queries)}"
                
                # Проекты — долгосрочные активности
                projects = ltm.get('projects', {})
                if projects:
                    project_names = list(projects.keys())[-3:]
                    user_memory += f"\n📁 Проекты: {', '.join(project_names)}"
            except Exception as e:
                logger.warning(f"[PROACTIVE] Could not parse long_term_memory: {e}")
        
        # Погода и новости
        ctx['weather'] = None
        ctx['news'] = None
        try:
            from .utils import get_weather_info as _get_weather, get_news_info
            if profile and profile.city:
                ctx['weather'] = _get_weather(profile.city)
                ctx['news'] = get_news_info(profile.city)
            if not ctx['news']:
                ctx['news'] = get_news_info()
            
            if ctx['weather']:
                user_memory += f"\n\n🌤 ПОГОДА: {ctx['weather']}"
            if ctx['news']:
                user_memory += f"\n\n📰 НОВОСТИ:\n{ctx['news']}"
        except Exception as e:
            logger.warning(f"[PROACTIVE] Could not load weather/news: {e}")
        
        # Партнёры / Premium инсайты
        ctx['partners'] = ""
        try:
            from .premium_simple import collect_premium_insights, manage_recommendations
            if ctx['subscription_tier'] == 'PREMIUM':
                premium_ctx = collect_premium_insights(user_id, mode='prompt', session=db_session)
                if premium_ctx and premium_ctx.strip():
                    ctx['partners'] = premium_ctx
                    user_memory += f"\n\n🔥 PREMIUM:\n{premium_ctx}"
            else:
                partner_ctx = manage_recommendations(user_id, 'get', session=db_session)
                if partner_ctx and partner_ctx.strip():
                    ctx['partners'] = partner_ctx
                    user_memory += f"\n\n👥 ПАРТНЁРЫ:\n{partner_ctx}"
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
                        line += " ⚠️ПРОСРОЧЕНО"
                    elif days is not None and days <= 7:
                        line += f" ⏳{days}дн"
                goal_lines.append(line)
            user_memory += f"\n\n🎯 ЦЕЛИ:\n" + "\n".join(f"- {l}" for l in goal_lines)
        
        # Задачи — сводка
        pending_count = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
        ctx['task_count'] = pending_count
        if pending_count:
            user_memory += f"\nАктивных задач: {pending_count}"
        
        # Просроченные задачи
        overdue = db_session.query(Task).filter(
            Task.user_id == user.id,
            Task.reminder_time < user_now,
            Task.status == "pending"
        ).limit(5).all()
        ctx['overdue_count'] = len(overdue)
        ctx['overdue_titles'] = [t.title for t in overdue]
        if overdue:
            user_memory += f"\nПРОСРОЧЕННЫЕ: {', '.join(ctx['overdue_titles'])}"
        
        # Поведенческий анализ
        insights = []
        recent = db_session.query(Interaction).filter_by(
            user_id=user.id
        ).order_by(Interaction.created_at.desc()).limit(10).all()
        
        topic_keywords = {
            'работа/задачи': ['задач', 'task', 'дел', 'работ', 'проект'],
            'нетворкинг': ['знаком', 'партнер', 'контакт', 'встреч'],
            'цели/рост': ['цель', 'goal', 'план', 'развити', 'рост'],
            'продуктивность': ['врем', 'time', 'продуктив', 'эффект'],
            'здоровье': ['здоров', 'спорт', 'тренир', 'сон', 'питан'],
            'финансы': ['деньг', 'финанс', 'инвест', 'бюджет', 'доход'],
            'обучение': ['учи', 'курс', 'книг', 'навык', 'изуч'],
        }
        
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
            insights.append(f"Недавние темы: {', '.join(recent_topics)}")
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
                patterns.append("продуктивный")
            if delegated > len(all_tasks) * 0.3:
                patterns.append("делегирует")
            if patterns:
                insights.append(f"Паттерны: {', '.join(patterns)}")
        
        # Время активности
        if user.last_interaction_at:
            h = user.last_interaction_at.hour
            if 6 <= h <= 10:
                insights.append("активен утром")
            elif 18 <= h <= 23:
                insights.append("активен вечером")
        
        if insights:
            user_memory += f"\n\n💡 ИНСАЙТЫ:\n" + "\n".join(f"- {i}" for i in insights)
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
        return ctx
    
    finally:
        db_session.close()


def _build_situation_prompt(ctx, intent=None, tasks_list=None, overdue_tasks_list=None):
    """Формирует умный промпт с ФРЕЙМВОРКОМ МЫШЛЕНИЯ — AI анализирует ситуацию и действует."""
    import random
    
    parts = []
    parts.append("Ты пишешь проактивное сообщение пользователю. Не в ответ на его запрос — ты сам решил написать.")
    
    # === АНАЛИЗ СИТУАЦИИ ===
    hour = ctx['user_now'].hour
    if 6 <= hour < 12:
        time_of_day = "Утро"
    elif 12 <= hour < 18:
        time_of_day = "День"
    elif 18 <= hour < 22:
        time_of_day = "Вечер"
    else:
        time_of_day = "Позднее время"
    
    task_count = ctx.get('task_count', 0)
    overdue_count = ctx.get('overdue_count', 0)
    has_goals = bool(ctx.get('goals'))
    has_profile = bool(ctx.get('profile') and ctx['profile'].strip() and ctx['profile'].strip() != 'Профиль не заполнен')
    
    parts.append(f"\n=== СИТУАЦИЯ ===")
    parts.append(f"Время: {time_of_day} ({ctx['user_now'].strftime('%H:%M')})")
    parts.append(f"Задач: {task_count}")
    if overdue_count > 0:
        parts.append(f"Просроченных: {overdue_count}")
    parts.append(f"Профиль заполнен: {'Да' if has_profile else 'НЕТ'}")
    parts.append(f"Цели заданы: {'Да' if has_goals else 'НЕТ'}")
    if ctx.get('recent_topics'):
        parts.append(f"Недавние темы: {', '.join(list(ctx['recent_topics'])[:3])}")
    
    # === КРАСНЫЕ ФЛАГИ (приоритет над всем) ===
    critical_flags = []
    
    if task_count == 0:
        critical_flags.append("""🚩 КРИТИЧНО: У пользователя НЕТ ЗАДАЧ!
Человек без плана = потерянный день. Ты ОБЯЗАН:
- Предложить 2-3 КОНКРЕТНЫЕ задачи с ТОЧНЫМ ВРЕМЕНЕМ (10:00, 14:00, 17:00)
- Основываясь на его профиле/целях/интересах
- Если профиль пуст — предложи базовые: утренняя рутина, главное дело дня, вечерний обзор
- Спроси: "Создать эти задачи?" — и будь готов создать по ответу
НЕ ПИШИ "могу помочь с планированием" — СРАЗУ покажи план!""")
    
    if not has_profile:
        critical_flags.append("""🚩 КРИТИЧНО: Профиль ПУСТ!
Без профиля ты работаешь ВСЛЕПУЮ — не можешь персонализировать советы.
Задай 1-2 конкретных вопроса:
- "Чем занимаешься? Онлайн или офлайн?"
- "Какая главная цель на ближайший месяц?"
Объясни ПОЧЕМУ: "Чтобы давать точные советы, мне нужно знать контекст"
НЕ спрашивай всё разом — 1-2 вопроса за раз.""")
    
    if not has_goals and has_profile:
        critical_flags.append("""🚩 ВАЖНО: Нет целей!
Пользователь не задал цели — значит нет ориентира для советов.
Предложи: "Давай поставим 1 главную цель на месяц? Что для тебя сейчас самое важное?"
Используй create_goal когда он ответит.""")
    
    if overdue_count > 0:
        critical_flags.append(f"""🚩 ВНИМАНИЕ: {overdue_count} просроченных задач!
Мягко напомни. НЕ ругай. Предложи: перенести, разбить на подзадачи, или отменить если неактуально.""")
    
    if critical_flags:
        parts.append("\n=== 🚩 КРАСНЫЕ ФЛАГИ (РЕАГИРУЙ ПЕРВЫМ ДЕЛОМ!) ===")
        parts.extend(critical_flags)
    
    # === ДОСТУПНЫЕ ДАННЫЕ ===
    available = []
    if ctx.get('weather'):
        available.append(f"Погода: {ctx['weather']}")
    if ctx.get('news'):
        available.append(f"Новости: {str(ctx['news'])[:400]}")
    if ctx.get('partners'):
        available.append(f"Партнёры/контакты: {str(ctx['partners'])[:300]}")
    if ctx.get('insights'):
        available.append(f"Наблюдения: {'; '.join(ctx['insights'])}")
    
    if ctx.get('goals'):
        goal_lines = []
        for g in ctx['goals']:
            line = f"{g.title} ({g.progress_percentage}%)"
            if g.target_date:
                days = g.days_until_target()
                if days is not None and days < 0:
                    line += " ⚠️просрочено"
                elif days is not None and days <= 7:
                    line += f" ⏳{days}дн"
            goal_lines.append(line)
        available.append(f"Цели: " + ", ".join(goal_lines))
    
    # Долгосрочные данные
    ltm_data = ctx.get('long_term_data', {})
    if ltm_data.get('interests'):
        sorted_ints = sorted(ltm_data['interests'].items(), key=lambda x: x[1], reverse=True)[:5]
        available.append(f"Устойчивые интересы: {', '.join(f'{t}({c})' for t, c in sorted_ints)}")
    if ltm_data.get('search_history'):
        recent_q = [s['query'] for s in ltm_data['search_history'][-3:]]
        available.append(f"Недавние поиски: {', '.join(recent_q)}")
    if ltm_data.get('projects'):
        available.append(f"Проекты: {', '.join(list(ltm_data['projects'].keys())[-3:])}")
    
    if available:
        parts.append("\n=== ДОСТУПНЫЕ ДАННЫЕ ===\n" + "\n".join(available))
    
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
                    time_str = f" (на {rt.astimezone(user_tz).strftime('%H:%M')})"
                except Exception:
                    pass
            desc = f" — {t.description[:80]}" if t.description else ""
            upcoming.append(f"• {t.title}{time_str}{desc}")
        
        if upcoming:
            parts.append("\nЗАДАЧИ:\n" + "\n".join(upcoming[:5]))
        if overdue_found:
            parts.append("\n⚠️ ПРОСРОЧЕННЫЕ: " + ", ".join(overdue_found[:5]))
    elif ctx.get('overdue_titles'):
        parts.append("\n⚠️ ПРОСРОЧЕННЫЕ: " + ", ".join(ctx['overdue_titles']))
    
    # Специальные задачи если переданы отдельно
    if overdue_tasks_list:
        if hasattr(overdue_tasks_list[0], 'title'):
            titles = [t.title for t in overdue_tasks_list[:5]]
        else:
            titles = [t.get('title', 'Задача') for t in overdue_tasks_list[:5]]
        parts.append(f"\n⚠️ ПРОСРОЧЕННЫЕ ЗАДАЧИ ({len(overdue_tasks_list)}):\n" + "\n".join(f"• {t}" for t in titles))
    
    # === INTENT ===
    if intent:
        intent_hints = {
            'morning': "Утро — подготовь ПЛАН дня. Если задач 0 → предложи конкретные. Если есть → напомни приоритетные.",
            'evening': "Вечер — итог дня. Что сделано? Что на завтра? Конкретно, по фактам.",
            'overdue': "Просроченные задачи — мягко, но конкретно. Что делаем: переносить, разбить, или отменить?",
            'insight': "Полезная находка — свяжи с профилем/целями. Конкретный вывод + следующий шаг.",
            'trend': "Тренд/новость релевантная целям — конкретика + как использовать.",
            'weather': "Погода → конкретная активность. Связь с задачами/целями.",
            'contact': "Интересный партнёр — конкретно кто и ЗАЧЕМ.",
            'productivity': "Продуктивность — КОНКРЕТНОЕ наблюдение + КОНКРЕТНЫЙ совет.",
        }
        if intent in intent_hints:
            parts.append(f"\nАКЦЕНТ: {intent_hints[intent]}")
    
    # Фокус ТОЛЬКО если нет критических флагов и нет intent
    if not intent and not critical_flags:
        possible_focuses = [
            "Конкретный инсайт по теме интересов/целей пользователя",
            "Прогресс по целям — что сделано, что дальше, конкретный шаг",
            "Интересная возможность из новостей/трендов связанная с профилем",
            "Практический совет из недавней темы разговора",
            "Связь текущих задач с долгосрочными целями — что упущено",
        ]
        if not ctx.get('weather'):
            possible_focuses = [f for f in possible_focuses if 'погода' not in f.lower()]
        if not ctx.get('partners'):
            possible_focuses = [f for f in possible_focuses if 'партнёр' not in f.lower()]
        if not ctx.get('news'):
            possible_focuses = [f for f in possible_focuses if 'новост' not in f.lower()]
        
        focus = random.choice(possible_focuses) if possible_focuses else "Конкретная помощь на основе профиля"
        parts.append(f"\nФОКУС: {focus}")
    
    # === ВОВЛЕЧЁННОСТЬ ===
    engagement = ctx.get('proactive_engagement', {})
    if engagement.get('engaged_topics'):
        from collections import Counter
        top = Counter(engagement['engaged_topics']).most_common(3)
        parts.append(f"\n📈 ВОВЛЕЧЁННОСТЬ: пользователь реагирует на: {', '.join(t for t, _ in top)}")
    if engagement.get('ignored_count', 0) > engagement.get('total_proactive', 0) * 0.6:
        parts.append("⚠️ Пользователь часто игнорирует — пиши только когда есть РЕАЛЬНАЯ конкретная польза")
    
    # === ПРАВИЛА ===
    parts.append("""
=== ПРАВИЛА ГЕНЕРАЦИИ ===
ОБЯЗАТЕЛЬНО:
- Пройди ФРЕЙМВОРК МЫШЛЕНИЯ перед ответом: Ситуация → Проблема → Анализ → Действие → Подача
- Если есть красные флаги — они ПРИОРИТЕТ над всем остальным
- 2-5 предложений, живой тон, конкретика
- Каждое сообщение = минимум 1 КОНКРЕТНОЕ действие (не "могу помочь", а конкретика)
- Если предлагаешь задачи — указывай ТОЧНОЕ ВРЕМЯ (10:00, 14:00, 17:00)
- Привязывай к профилю/целям/интересам пользователя

ЗАПРЕЩЕНО:
- Общие фразы: "как дела", "могу помочь", "чем заняться"
- Перечисление функций бота
- Придумывать @username, контакты, статистику, цифры
- Начинать с банального «Привет!» без конкретной пользы
- Писать «я заметил/я проанализировал» — просто дай пользу
- Предлагать ЛОКАЛЬНЫЕ мероприятия/сообщества если бизнес ОНЛАЙН
- Повторять формулировки и темы предыдущих сообщений""")
    
    return "\n".join(parts)


async def generate_proactive_message(user_id, context="general", task_count=0, overdue_count=0, tasks_list=None):
    """Единый умный генератор проактивных сообщений.
    
    Вместо жёстких категорий (no_tasks/few_tasks/etc.) — AI сам выбирает,
    что полезнее всего сказать на основе полного контекста ситуации.
    
    Заменяет: старый generate_proactive_message с 690 строками хардкод-промптов.
    """
    try:
        # 1. Собираем полный контекст
        ctx = await _build_proactive_context(user_id)
        if not ctx:
            return "Привет! Готов помочь. Что обсудим?"
        
        # 2. Определяем intent на основе ситуации
        intent = None
        hour = ctx['user_now'].hour
        
        if isinstance(context, str) and context == 'overdue_tasks':
            intent = 'overdue'
        elif hour < 12:
            intent = 'morning'
        elif hour >= 20:
            intent = 'evening'
        # Остальные случаи — AI сам выберет фокус
        
        # 3. Формируем ситуационный промпт
        situation_prompt = _build_situation_prompt(ctx, intent=intent, tasks_list=tasks_list)
        
        # 4. System prompt
        system_prompt = get_extended_system_prompt(
            ctx['user_now'],
            ctx['current_time_str'],
            ctx['current_date_str'],
            ctx['username'],
            "",
            ctx['user_memory'],
            subscription_tier=ctx['subscription_tier'],
            message_type='proactive'
        )
        
        # Антиповтор — запрещаем повторять последние ответы
        if ctx.get('last_responses'):
            anti_repeat = "\n".join(f"- {r}" for r in ctx['last_responses'])
            system_prompt += f"\n\nЗАПРЕЩЕНО повторять эти фразы (твои последние ответы):\n{anti_repeat}\nГенерируй УНИКАЛЬНЫЙ ответ!"
        
        # 5. Собираем messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": situation_prompt}
        ]
        
        # 6. Запрос к DeepSeek
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 600
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = replace_placeholders(content, ctx['user_now'], ctx['current_time_str'])
                    content = clean_technical_details(content)
                    content = post_process_response(content, user_id)
                    logger.info(f"[PROACTIVE] Generated smart message: {content[:100]}...")
                    return content
                else:
                    logger.error(f"Failed to generate proactive message: status {response.status}")
                    from .utils import generate_unified_recommendations
                    return generate_unified_recommendations(
                        'fallback', task_count=task_count, overdue_count=overdue_count,
                        profile=ctx.get('profile'), weather_info=ctx.get('weather'),
                        partner_recommendations=ctx.get('partners', ''), tasks_list=tasks_list
                    )
    
    except Exception as e:
        logger.error(f"Error in generate_proactive_message: {e}\n{traceback.format_exc()}")
        try:
            from .utils import generate_unified_recommendations
            return generate_unified_recommendations('fallback', task_count=task_count, overdue_count=overdue_count)
        except Exception:
            return "Привет! Готов помочь с задачами и целями. Что обсудим?"


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


def validate_response_compliance(content, msg_type):
    """Проверка соответствия ответа промту"""
    if not content:
        return False, ["Empty content"]
    
    content_lower = content.lower()
    word_count = len(content.split())
    issues = []
    
    # Общие правила (смягчённые для трёхэтапного подхода)
    if word_count > 150:  # Слишком длинный (увеличено со 100 до 150)
        issues.append("Too long")
    if word_count < 3:  # Слишком короткий (уменьшено с 5 до 3)
        issues.append("Too short")
    # Убрали проверку на клише - агент формирует естественные ответы
    
    # Специфические по типу
    if msg_type in ["reminder", "overdue"]:
        if "?" not in content:  # Должен быть вопрос
            issues.append("No question")
        if word_count > 40:  # Слишком длинный
            issues.append("Too long for type")
        if word_count < 10:  # Слишком короткий
            issues.append("Too short for type")
    
    if msg_type == "proactive":
        if word_count > 50:  # Разрешить до 50
            issues.append("Too long for proactive")
        if word_count < 10:  # Минимум 10
            issues.append("Too short for proactive")
    
    if msg_type == "daily_report":
        if word_count > 30:
            issues.append("Too long for report")
        if word_count < 5:
            issues.append("Too short for report")
    
    if msg_type == "create_task":
        if "завтра в" not in content_lower and "время" not in content_lower:
            issues.append("No time indication")
    
    if msg_type == "complete_task":
        if "выполнена" not in content_lower and "завершена" not in content_lower:
            issues.append("No completion confirmation")
    
    return len(issues) == 0, issues


# Функции для работы с задачами
