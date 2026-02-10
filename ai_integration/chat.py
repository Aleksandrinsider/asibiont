from . import handlers
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
from models import Session, User, Task, UserProfile, Subscription
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
    add_task, list_tasks, complete_task, reschedule_task,
    delegate_task_with_session, delegate_task, check_subscription_status, accept_delegated_task,
    reject_delegated_task, get_delegation_progress, cancel_delegation, edit_task,
    list_tasks, get_partners_list, find_partners,
    generate_delegation_notification_async, generate_progress_request, schedule_delegation_monitoring,
    check_delegation_deadlines, update_user_memory_async, delete_task_sync, create_subscription_payment,
    cancel_subscription, get_task_details,
    update_profile, smart_update_profile, show_profile, delete_task, find_relevant_contacts_for_task, analyze_tasks, get_news_trends,
    quick_topic_search, check_topic_relevance
)
from .autonomous_agent import chat_with_ai as autonomous_chat_with_ai

logger = logging.getLogger(__name__)

# Базовый системный промпт для простых сообщений
system_prompt = "Ты - ASI Biont, умный AI-помощник для управления задачами и повышения продуктивности. Отвечай кратко и по делу."


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
            from .prompts import generate_proactive_context
            proactive_context = generate_proactive_context(user_id, session)
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
                subscription_tier=getattr(user, 'subscription_tier', 'FREE'),
                message_type=message_type,
                weather_info=weather_info,
                news_info=news_info,
                proactive_context=proactive_context,
                current_task_info=current_task_info
            )

            # Используем улучшенный гибридный автономный агент (трёхэтапный подход)
            response_data = await autonomous_chat_with_ai(
                message=message,
                context=context,
                user_id=user_id,
                file_content=file_content,
                db_session=session,
                message_type=message_type
            )
            
            # Отмечаем что Premium рекомендации были показаны (если были в промпте)
            if proactive_context and "ПРЕМИУМ РЕКОМЕНДАЦИИ" in proactive_context:
                try:
                    from ai_integration.premium_simple import mark_recommendation_shown
                    mark_recommendation_shown(user_id, session)
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

⚠️ ЭТО ПОВТОРНОЕ НАПОМИНАНИЕ - прошло 15 минут с первого

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

🚨 КРИТИЧЕСКОЕ НАПОМИНАНИЕ - задача требует срочного внимания

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


async def generate_proactive_message(user_id, context="general", task_count=0, overdue_count=0, tasks_list=None):
    """Генерирует проактивное сообщение по основному промпту системы, как обычные ответы AI
    
    Args:
        user_id: ID пользователя
        context: Контекст сообщения
        task_count: Количество задач
        overdue_count: Количество просроченных
        tasks_list: Список задач для анализа
    """
    try:
        # Используем тот же подход, что и в chat_with_ai
        import json

        # Получить контекст чата из БД
        context = []

        # Получить данные пользователя (как в chat_with_ai)
        user_memory = ""
        profile = None
        user = None
        subscription_tier = None
        weather_info = None
        news_info = None
        partner_recommendations = ""  # Для хранения рекомендаций партнеров
        months = [
            'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
            'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ]

        if user_id:
            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()

            if user:
                # Получаем subscription_tier
                subscription_tier = user.subscription_tier.value if user.subscription_tier else None

                # Получаем время пользователя
                base_now = datetime.now(pytz.UTC)
                user_now = base_now
                # Default to Moscow time instead of UTC
                user_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                        user_now = base_now.astimezone(user_tz)
                        # Обновляем с учетом таймзоны пользователя
                        current_time_str = f"{user_now.strftime('%H:%M')} ({user.timezone})"
                        current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                    except Exception as e:
                        logger.error(f"Error setting user timezone: {e}")
                        # Fallback to Moscow
                        user_tz = pytz.timezone('Europe/Moscow')
                        user_now = base_now.astimezone(user_tz)
                        current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                        current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

                # Получаем память пользователя
                if user.memory:
                    try:
                        decrypted = decrypt_data(user.memory)
                        user_memory = f"\nИнформация о пользователе: {decrypted}"
                    except Exception:
                        user_memory = ""

                # Получаем погоду и новости для контекста
                weather_info = None
                news_info = None
                try:
                    from .utils import get_weather_info, get_news_info
                    if profile and profile.city:
                        weather_info = get_weather_info(profile.city)
                        news_info = get_news_info(profile.city)
                    if not news_info:
                        news_info = get_news_info()  # Общие новости России
                    
                    if weather_info:
                        user_memory += f"\n\n🌤 ПОГОДА: {weather_info}"
                    if news_info:
                        user_memory += f"\n\n📰 АКТУАЛЬНЫЕ НОВОСТИ:\n{news_info}"
                except Exception as e:
                    logger.warning(f"[PROACTIVE] Could not load weather/news: {e}")

                # Получаем рекомендации партнеров/Premium инсайты
                try:
                    from .premium_simple import get_premium_recommendations_for_prompt, get_partner_recommendations_for_prompt
                    
                    if subscription_tier == 'PREMIUM':
                        # Для Premium показываем automation insights
                        premium_context = get_premium_recommendations_for_prompt(user_id, db_session)
                        if premium_context and premium_context.strip():
                            partner_recommendations = premium_context  # Сохраняем для fallback
                            user_memory += f"\n\n🔥 PREMIUM АВТОМАТИЗАЦИЯ:\n{premium_context}"
                            logger.info(f"[PROACTIVE] Added Premium automation context for user {user_id}")
                    else:
                        # Для обычных пользователей показываем партнеров
                        partner_context = get_partner_recommendations_for_prompt(user_id, db_session)
                        if partner_context and partner_context.strip():
                            partner_recommendations = partner_context  # Сохраняем для fallback
                            user_memory += f"\n\n👥 РЕКОМЕНДАЦИИ ПАРТНЕРОВ:\n{partner_context}"
                            logger.info(f"[PROACTIVE] Added partner recommendations context for user {user_id}")
                except Exception as e:
                    logger.warning(f"[PROACTIVE] Could not load partner recommendations: {e}")

                # Получаем профиль
                profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    profile_info = []
                    if profile.city:
                        profile_info.append(f"Город: {profile.city}")
                    if profile.company:
                        profile_info.append(f"Компания: {profile.company}")
                    if profile.position:
                        profile_info.append(f"Должность: {profile.position}")
                    if profile.languages:
                        profile_info.append(f"Языки: {profile.languages}")
                    if profile.skills:
                        profile_info.append(f"Навыки: {profile.skills}")
                    if profile.interests:
                        profile_info.append(f"Интересы: {profile.interests}")
                    if profile.goals:
                        profile_info.append(f"Цели: {profile.goals}")

                    if profile_info:
                        user_memory += f"\nПрофиль: {', '.join(profile_info)}"

                    # Определяем незаполненные поля
                    empty_fields = []
                    if not profile.city:
                        empty_fields.append("город")
                    if not profile.company:
                        empty_fields.append("компания")
                    if not profile.position:
                        empty_fields.append("должность")
                    if not profile.skills:
                        empty_fields.append("навыки")
                    if not profile.interests:
                        empty_fields.append("интересы")
                    if not profile.goals:
                        empty_fields.append("цели")
                    if not profile.languages:
                        empty_fields.append("языки")

                    if empty_fields:
                        fields_list = ', '.join(empty_fields[:3])
                        user_memory += f"\n⚠️ НЕЗАПОЛНЕННЫЕ ПОЛЯ: {fields_list}. Каждые 5-7 сообщений ПРОАКТИВНО спрашивай об одном из них (естественно в контексте диалога, не навязчиво). НЕ ПОВТОРЯЙ вопросы, которые уже задавал в последних сообщениях!"

                # Добавляем информацию о задачах
                tasks_summary = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
                if tasks_summary > 0:
                    user_memory += f"\nСводка: всего активных задач {tasks_summary}"

                overdue_tasks = (
                    db_session.query(Task)
                    .filter(Task.user_id == user.id, Task.reminder_time < user_now, Task.status == "pending")
                    .limit(5)
                    .all()
                )
                if overdue_tasks:
                    overdue_titles = [f"{t.title}" for t in overdue_tasks]
                    user_memory += f"\nПРОСРОЧЕННЫЕ ЗАДАЧИ: {', '.join(overdue_titles)} - предложи помощь!"

            db_session.close()

        # Формируем system_prompt ТОЧНО как в chat_with_ai
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        mentions_str = ""

        # Извлекаем последние ответы агента для предотвращения повторов (УСИЛЕННАЯ ВЕРСИЯ)
        last_responses = []
        if context and isinstance(context, list):
            for item in context[-5:]:
                if isinstance(item, dict) and 'agent' in item:
                    response_text = item['agent'].strip()
                    if response_text and len(response_text) > 10:
                        # Берем первые 80 символов для более точной проверки
                        last_responses.append(response_text[:80])
        # Убираем дубликаты, сохраняя порядок
        seen = set()
        last_responses = [x for x in last_responses if not (x in seen or seen.add(x))]
        last_responses = last_responses[-5:]  # Последние 5 уникальных ответов

        system_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            subscription_tier=subscription_tier,
            message_type='proactive')
        
        # Добавляем последние ответы для избегания повторов
        if last_responses:
            responses_text = "\n".join([f"- {resp}" for resp in last_responses])
            system_prompt += f"\n\n⚠️ ЗАПРЕЩЕНО ПОВТОРЯТЬ ЭТИ ФРАЗЫ (твои последние ответы):\n{responses_text}\n\nГенерируй НОВЫЙ уникальный ответ!"
        
        logger.info("[PROACTIVE] Using extended prompt system")

        # Создаем messages как в обычном чате, но с проактивным контекстом
        messages = [{"role": "system", "content": system_prompt}]

        # Добавляем последние сообщения из контекста
        if context and isinstance(context, list):
            for item in context[-6:]:  # Берем последние 6 сообщений для контекста
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})

        # Проактивный контекст - создаем разные сообщения для разных ситуаций
        import random
        
        proactive_prompts = {
            "no_tasks": [
                """Напиши естественное, дружелюбное проактивное сообщение для пользователя без активных задач.

ДОСТУПНЫЙ КОНТЕКСТ (используй ВСЁ релевантное):
- Профиль: интересы, навыки, цели, город, компания, должность
- Погода и новости (если есть)
- Рекомендации партнеров для знакомств (если есть)
- Premium automation insights (если есть)

ЦЕЛЬ: 
- Мотивировать к новым начинаниям
- Предложить 2-3 КОНКРЕТНЫЕ идеи задач из реальных интересов/целей пользователя
- Упомянуть возможность полезных знакомств (если есть рекомендации партнеров)
- Связать с текущим контекстом (погода, время суток, актуальные новости)

ФОРМАТ:
- Естественное обращение (без "ПРОАКТИВНОЕ СООБЩЕНИЕ")
- 3-5 предложений с живым, персональным тоном
- Упоминание РЕАЛЬНЫХ данных (не выдумывай!)
- Конкретные actionable предложения
- Вовлекающий вопрос в конце

НЕ ВЫДУМЫВАЙ данные! Используй только реальную информацию из контекста.""",
                
                """Сгенерируй мотивирующее проактивное сообщение - у пользователя нет задач, самое время для планирования!

ИСПОЛЬЗУЙ ВСЕ ДОСТУПНЫЕ ДАННЫЕ:
- Цели и интересы из профиля → предложи конкретные шаги к их достижению
- Навыки → идеи для их развития/применения
- Рекомендации партнеров → возможность познакомиться с людьми, у кого схожие интересы
- Погода/новости → свяжи с планами (например, хорошая погода → встреча, плохая → удаленка)
- Город/компания → локальные возможности

СТИЛЬ:
- Теплый, поддерживающий тон (как приятель, а не робот)
- Персонализация через упоминание конкретных данных профиля
- Фокус на действиях И нетворкинге (новые знакомства помогают достигать целей!)
- 3-5 предложений

Закончи вопросом, который приглашает к диалогу и действию."""
            ],

            "few_tasks": [
                f"""У пользователя {task_count} активных задач - оптимальная загруженность. Напиши естественное проактивное сообщение.

КОНТЕКСТ ДЛЯ ИСПОЛЬЗОВАНИЯ:
- Текущие задачи (см. список ниже)
- Профиль: интересы, цели, навыки
- Рекомендации партнеров (если есть)
- Погода/новости
- Premium insights (если есть)

ЦЕЛЬ:
- Поддержать текущий темп работы
- Предложить оптимизации или дополнительные возможности
- Упомянуть релевантных партнеров для коллабораций (если есть рекомендации)
- Связать с контекстом дня (погода, новости)

ФОРМАТ:
- Естественный, дружеский тон
- 3-4 предложения
- Признание текущих усилий + предложение развития
- Может упомянуть конкретную задачу из списка
- Вопрос для вовлечения

ВАЖНО: Используй только РЕАЛЬНЫЕ данные из контекста, не выдумывай!""",
                
                f"""Напиши поддерживающее сообщение для пользователя с {task_count} задачами.

ИНТЕГРИРУЙ РЕАЛЬНЫЙ КОНТЕКСТ:
- Задачи пользователя (анализируй их содержание)
- Цели и интересы → возможности для роста
- Навыки → как их применить эффективнее
- Партнеры (если есть) → кто может помочь с задачами или целями
- Текущая обстановка (погода, новости, время суток)

АКЦЕНТЫ:
- Признание продуктивности
- Конкретная идея для улучшения workflow
- Возможность делегирования или коллаборации (через партнеров)
- Баланс работы и общения (нетворкинг = источник новых возможностей!)

3-4 предложения, теплый тон, actionable совет, вопрос."""
            ],

            "many_tasks": [
                f"""У пользователя много задач ({task_count}). Напиши мягкое, поддерживающее проактивное сообщение.

ДОСТУПНЫЕ ДАННЫЕ:
- Список задач (анализируй приоритеты)
- Профиль: навыки, цели, контакты
- Рекомендации партнеров для делегирования/помощи (если есть)
- Premium automation возможности (если есть)

ЦЕЛЬ:
- Поддержать, не давить
- Предложить конкретные способы оптимизации:
  * Приоритизация (что важнее?)
  * Делегирование (можно предложить партнеров из рекомендаций!)
  * Автоматизация (если Premium)
  * Разбиение на этапы
- Напомнить о важности баланса и не забывать про отдых

СТИЛЬ:
- Эмпатичный, заботливый тон
- 2-3 предложения (не перегружать!)
- Конкретное, actionable предложение
- Легкое напоминание про self-care

НЕ ВЫДУМЫВАЙ информацию!""",
                
                f"""Сгенерируй деликатное сообщение для пользователя с высокой загрузкой ({task_count} задач).

ИСПОЛЬЗУЙ КОНТЕКСТ:
- Задачи → определи, что можно делегировать/автоматизировать
- Рекомендации партнеров → конкретные люди, которые могут помочь
- Premium возможности → automation insights
- Навыки → что делать самому, что можно поручить

ФОКУС:
- Забота о пользователе (многозадачность → burnout)
- Предложение конкретной разгрузки (делегирование партнерам, автоматизация)
- Напоминание: эффективность > количество
- Может, стоит обсудить приоритеты?

2-3 предложения, мягкий тон, практичный совет."""
            ],

            "overdue_tasks": [
                f"""У пользователя {overdue_count} просроченных задач. Напиши деликатное, поддерживающее сообщение.

КОНТЕКСТ:
- Просроченные задачи (см. список)
- Профиль пользователя
- Рекомендации партнеров (кто может помочь?)
- Погода/ситуация

ЦЕЛЬ:
- НЕ ВИНИТЬ, а поддержать
- Предложить конкретный план восстановления:
  * Выбрать 1-2 критичные задачи для старта
  * Делегировать часть (если есть партнеры)
  * Переоценить приоритеты
- Напомнить: просрочки случаются, важно как мы реагируем
- Предложить помощь

СТИЛЬ:
- Максимально эмпатичный, без осуждения
- 3-4 предложения
- Конкретный actionable план
- Вопрос-предложение помощи

ВАЖНО: Не усугублять стресс, а мотивировать!""",
                
                f"""Напиши мягкое напоминание о {overdue_count} просроченных задачах с планом действий.

ИСПОЛЬЗУЙ ДАННЫЕ:
- Просроченные задачи → какие критичнее?
- Профиль → цели, приоритеты пользователя
- Партнеры → кто может взять часть нагрузки?
- Контекст дня

ПОДХОД:
- Понимание: просрочки бывают у всех
- Фокус на решении, не на проблеме
- Конкретный первый шаг (одна задача для старта)
- Предложение делегирования через партнеров (если есть)
- Открытость к обсуждению

3-4 предложения, поддерживающий тон, план + вопрос."""
            ],

            "general": [
                """Напиши естественное, живое проактивное сообщение для пользователя.

ЭТО КЛЮЧЕВОЙ КОНТЕКСТ - ИСПОЛЬЗУЙ ВСЁ РЕЛЕВАНТНОЕ:

🎯 ПРОФИЛЬ:
- Интересы, цели, навыки
- Город, компания, должность, языки
- Что важно для пользователя?

👥 РЕКОМЕНДАЦИИ ПАРТНЕРОВ (если есть):
- Конкретные люди для знакомства
- Общие интересы, возможности коллабораций
- ВАЖНО: Это мощный инструмент для нетворкинга!

🔥 PREMIUM AUTOMATION (если есть):
- Найденные автоматически партнеры
- Activity/Contact alerts
- Инсайты для оптимизации

🌤️ ПОГОДА + 📰 НОВОСТИ (если есть):
- Свяжи с планами пользователя
- Актуальный контекст дня

📋 ЗАДАЧИ (если есть):
- Текущие активности
- Возможности для развития

ЦЕЛЬ СООБЩЕНИЯ:
✅ РЕАБИЛИТИРОВАТЬ пользователя к действиям и общению
✅ Мотивировать к конкретным шагам (задачи ИЛИ нетворкинг)
✅ Предложить РЕАЛЬНЫЕ возможности (из данных!)
✅ Вовлечь в диалог

ФОРМАТ:
- Естественный, дружеский тон (как сообщение от знакомого, а не бота)
- 3-5 предложений
- Упоминание КОНКРЕТНЫХ данных (партнеры, погода, интересы, цели)
- Actionable предложение: конкретная задача ИЛИ знакомство с партнером
- Может связать несколько элементов контекста (например: погода + цель + партнер)
- Вопрос для вовлечения

КРИТИЧЕСКИ ВАЖНО:
❌ НЕ ВЫДУМЫВАЙ информацию, которой нет!
✅ Если есть партнеры - ОБЯЗАТЕЛЬНО упомяни релевантного (@username)
✅ Если есть погода/новости - используй для контекста
✅ Фокус на новых знакомствах и полезных связях (это помогает расти!)
✅ Баланс между задачами и нетворкингом""",
                
                """Сгенерируй персонализированное проактивное сообщение.

ВЕСЬ ДОСТУПНЫЙ КОНТЕКСТ:
- Профиль (интересы, навыки, цели, город, компания)
- Рекомендации партнеров для нетворкинга
- Premium insights (automation, найденные контакты)
- Погода и актуальные новости
- Задачи пользователя

СТРАТЕГИЯ СООБЩЕНИЯ:
1. ПЕРСОНАЛИЗАЦИЯ через реальные данные
2. МОТИВАЦИЯ к действию (задача ИЛИ знакомство)
3. КОНТЕКСТ дня (погода/новости/время)
4. КОНКРЕТИКА: не "подумай о целях", а "давай поработаем над [конкретная цель]"
5. НЕТВОРКИНГ: если есть партнеры, предложи познакомиться/обсудить коллаборацию

АКЦЕНТЫ:
- Реабилитация к общению (новые знакомства = рост!)
- Actionable предложения (что сделать прямо сейчас?)
- Связь интересов/целей с текущими возможностями
- Упоминание РЕАЛЬНЫХ партнеров (@username), если есть

ФОРМАТ:
- 3-5 предложений, теплый тон
- Интеграция 2-3 элементов контекста
- Конкретное предложение (задача ИЛИ встреча/знакомство)
- Вопрос, приглашающий к действию

НЕ ВЫДУМЫВАЙ! Используй только реальные данные.""",
                
                """Создай вовлекающее проактивное сообщение с фокусом на возможности.

ИНТЕГРИРУЙ ВСЁ:

ДАННЫЕ ПОЛЬЗОВАТЕЛЯ:
- Что важно для него? (цели, интересы, навыки)
- Где он находится? (город → локальные возможности)
- Чем занимается? (компания, должность)

ВОЗМОЖНОСТИ ДЛЯ РОСТА:
- Партнеры: кто может быть полезен? (конкретные @username)
- Premium: какие insights/контакты найдены автоматически?
- Задачи: что можно улучшить/оптимизировать?

КОНТЕКСТ МОМЕНТА:
- Погода: влияет на планы (встречи, удаленка, etc.)
- Новости: связь с интересами/целями
- Время суток: утро (планирование), вечер (рефлексия)

ЦЕЛЬ:
- ПОМОЧЬ пользователю двигаться вперед через:
  * Конкретные задачи к целям
  * Полезные знакомства (партнеры!)
  * Оптимизацию текущих процессов
- ВОВЛЕЧЬ в активное общение и действия
- РЕАБИЛИТИРОВАТЬ к регулярному взаимодействию

СТИЛЬ:
- Живой, персональный (не роботизированный!)
- Вдохновляющий, но без банальностей
- 3-5 предложений
- Упоминание 2-3 реальных элементов контекста
- ОБЯЗАТЕЛЬНО: actionable предложение (что сделать?)
- Если есть партнеры → предложи познакомиться/обсудить идеи
- Вопрос для старта диалога"""
            ]
        }

        # Выбираем подходящий промпт (случайный вариант для разнообразия)
        # Убеждаемся, что context - строка
        if isinstance(context, list):
            context = "general"  # Если context - список, используем general
        
        prompt_options = proactive_prompts.get(context, proactive_prompts["general"])
        if isinstance(prompt_options, list):
            selected_prompt = random.choice(prompt_options)
        else:
            selected_prompt = prompt_options
        
        # Добавляем информацию о задачах, если есть
        if tasks_list:
            tasks_info = "\n\nАКТИВНЫЕ ЗАДАЧИ ПОЛЬЗОВАТЕЛЯ:\n"
            now_utc = datetime.now(pytz.UTC)
            upcoming_tasks = []
            overdue_tasks = []
            
            for task in tasks_list[:15]:  # Ограничиваем 15 задачами
                if task.status != 'pending':
                    continue  # Пропускаем неактивные задачи
                    
                task_time = ""
                if task.reminder_time:
                    try:
                        # Конвертируем в локальное время пользователя
                        if task.reminder_time.tzinfo is None:
                            task_time_utc = pytz.UTC.localize(task.reminder_time)
                        else:
                            task_time_utc = task.reminder_time
                        task_time_local = task_time_utc.astimezone(user_tz)
                        
                        # Проверяем, просрочена ли задача
                        if task_time_utc < now_utc:
                            overdue_tasks.append(task)
                        else:
                            upcoming_tasks.append(task)
                        
                        task_time = f" (на {task_time_local.strftime('%H:%M')})"
                    except Exception as e:
                        logger.warning(f"[PROACTIVE] Error formatting task time: {e}")
                else:
                    upcoming_tasks.append(task)  # Задачи без времени считаем предстоящими
            
            # Для proactive режима показываем ТОЛЬКО ПРЕДСТОЯЩИЕ задачи
            relevant_tasks = upcoming_tasks[:5]  # Ограничиваем 5 задачами для краткости
            
            if relevant_tasks:
                for task in relevant_tasks:
                    task_time = ""
                    if task.reminder_time:
                        try:
                            if task.reminder_time.tzinfo is None:
                                task_time_utc = pytz.UTC.localize(task.reminder_time)
                            else:
                                task_time_utc = task.reminder_time
                            task_time_local = task_time_utc.astimezone(user_tz)
                            task_time = f" (на {task_time_local.strftime('%H:%M')})"
                        except Exception as e:
                            logger.warning(f"[PROACTIVE] Error formatting task time in list: {e}")
                    tasks_info += f"• {task.title}{task_time}\n"
            else:
                tasks_info += "• Нет предстоящих задач\n"
                
            selected_prompt += tasks_info
        
        messages.append({"role": "user", "content": selected_prompt})

        # Используем параметры для более подробных, персонализированных сообщений
        temperature = 0.85  # Повысили для большего разнообразия и естественности
        top_p = 0.92  # Больше вариативности при сохранении качества

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 600  # Увеличили для более богатых, персонализированных сообщений (3-5 предложений)
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = replace_placeholders(content, user_now, current_time_str)
                    content = clean_technical_details(content)

                    # Пост-обработка как в обычных ответах
                    content = post_process_response(content, user_id, db_session)

                    logger.info(f"[PROACTIVE] Generated dynamic message: {content[:100]}...")
                    return content
                else:
                    logger.error(f"Failed to generate proactive message: status {response.status}")
                    # Улучшенные fallback сообщения с погодой/новостями/партнерами
                    fallback_base = ""
                    if weather_info:
                        fallback_base += f"🌤 {weather_info.split(':')[1].split(',')[0].strip()} сегодня. "
                    
                    # Добавляем упоминание партнеров, если есть
                    partner_mention = ""
                    if partner_recommendations:
                        # Извлекаем первого партнера из рекомендаций
                        if "@" in partner_recommendations:
                            partner_match = partner_recommendations.split("@")[1].split()[0] if len(partner_recommendations.split("@")) > 1 else None
                            if partner_match:
                                partner_mention = f" Кстати, @{partner_match} может быть интересен для твоих целей. "
                    
                    fallback_messages = {
                        "no_tasks": f"{fallback_base}Отличное время для планирования!{partner_mention}Может, создадим задачу или обсудим знакомства? Что актуально?",
                        "few_tasks": f"{fallback_base}У тебя {task_count} активных задач - хороший темп!{partner_mention}Может, добавим что-то еще или сфокусируемся на качестве?",
                        "many_tasks": f"У тебя {task_count} задач. {fallback_base}{partner_mention}Может, стоит делегировать часть или пересмотреть приоритеты?",
                        "overdue_tasks": f"{overdue_count} просроченных задач требуют внимания. Не переживай!{partner_mention}Давай составим план восстановления?",
                        "general": f"{fallback_base}Вижу интересные возможности.{partner_mention}Может, обсудим конкретные шаги или полезные знакомства?"
                    }
                    return fallback_messages.get(context, fallback_messages["general"])

    except Exception as e:
        logger.error(f"Error in generate_proactive_message: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Улучшенные fallback сообщения для исключений с упоминанием партнеров
        partner_mention = ""
        if partner_recommendations and "@" in partner_recommendations:
            partner_match = partner_recommendations.split("@")[1].split()[0] if len(partner_recommendations.split("@")) > 1 else None
            if partner_match:
                partner_mention = f" Вижу, что @{partner_match} может быть интересен. "
        
        fallback_messages = {
            "no_tasks": f"Добрый день! Чистый список задач - отличная возможность.{partner_mention}Может, обсудим цели и создадим конкретные шаги?",
            "few_tasks": f"Добрый день! {task_count} задач в работе - продуктивный темп!{partner_mention}Как продвигается выполнение?",
            "many_tasks": f"Добрый день! Вижу {task_count} задач - впечатляющая нагрузка!{partner_mention}Может, делегируем часть или оптимизируем?",
            "overdue_tasks": f"Добрый день! {overdue_count} просроченных задач ждут внимания.{partner_mention}Без паники! Давай составим план?",
            "general": f"Добрый день! Готов помочь с планированием.{partner_mention}Может, обсудим цели и создадим задачи? Что в приоритете?"
        }
        return fallback_messages.get(context, fallback_messages["general"])


async def generate_daily_report(user_id):
    """Генерирует ежедневный отчет о задачах"""
    try:
        # Получить пользователя для timezone
        db_session = Session()
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        db_session.close()

        # Получить задачи пользователя
        db_session = Session()
        tasks = db_session.query(Task).filter_by(user_id=user_id).all()
        db_session.close()

        completed = [t for t in tasks if t.status == "completed"]
        pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

        # Получить память пользователя
        user_memory = ""
        if user and user.memory:
            try:
                decrypted = decrypt_data(user.memory)
                user_memory = f"\nИнформация о пользователе: {decrypted}"
            except (Exception,):
                user_memory = ""

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
            logger.error(f"Error setting user timezone for daily_report: {e}")
            # Fallback to Moscow time
            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(moscow_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.warning(f"[DAILY_REPORT] Failed Moscow timezone fallback: {e}")
        
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, message_type='daily_report')

        system_prompt = base_prompt

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"},
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

                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "daily_report")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Daily report response not compliant: {issues}")
                        # Принуждаем исправление - функция временно отключена
                        # content = await enforce_prompt_compliance(
                        #     content, "daily_report", user_id, None, system_prompt, messages, url, headers
                        # )

                    return content
                else:
                    logger.error(f"Failed to generate daily report: status {response.status}")
                    retry_msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Ежедневный отчёт."}]
                    retry_data = {"model": DEEPSEEK_MODEL, "messages": retry_msg, "temperature": 0.7, "max_tokens": 200}
                    async with session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=20)) as retry_resp:
                        if retry_resp.status == 200:
                            retry_result = await retry_resp.json()
                            return retry_result["choices"][0]["message"]["content"].strip()
                    # Генерируем fallback через AI
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Время подвести итоги дня. Создай короткое напоминание."}]
                        data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.8, "max_tokens": 50}
                        async with aiohttp.ClientSession() as sess:
                            async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    result = await resp.json()
                                    return result["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                    return "Время подвести итоги! 🌙"
    except Exception as e:
        logger.error(f"Error in generate_daily_report: {e}")
        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Отчёт о дне. Создай короткий вопрос о дне."}]
            data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.8, "max_tokens": 50}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return res["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[REPORT] AI report generation failed: {e}")
        return "Как прошёл день? 🌆"


async def generate_overdue_reminder(user_id, overdue_tasks, escalation_level=1):
    """Генерирует напоминание о просроченных задачах"""
    try:
        # Поддержка как объектов Task, так и словарей
        if overdue_tasks and isinstance(overdue_tasks[0], dict):
            task_titles = [t.get('title', 'Задача') for t in overdue_tasks]
        else:
            task_titles = [t.title for t in overdue_tasks]
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
            logger.error(f"Error setting user timezone for overdue: {e}")
            # Fallback to Moscow time
            try:
                moscow_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(moscow_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.warning(f"[OVERDUE] Failed Moscow timezone fallback: {e}")
        
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, message_type='overdue')

        system_prompt = base_prompt

        # Адаптируем тон в зависимости от уровня эскалации
        if escalation_level == 1:
            tone_instruction = "Будь дружелюбным, но настойчивым. Напомни о важности выполнения задач."
        elif escalation_level == 2:
            tone_instruction = "Будь более строгим. Подчеркни негативные последствия невыполнения."
        else:  # 3+
            tone_instruction = "Будь очень строгим и мотивирующим. Предложи конкретные альтернативы и помощь."

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {
                "role": "system", "content": system_prompt}, {
                "role": "user", "content": f"Напомни о просроченных задачах: {', '.join(task_titles)}. {tone_instruction} Предложи конкретные шаги решения.", }, ]

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

                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "overdue")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Overdue reminder response not compliant: {issues}")
                        # Принуждаем исправление - функция временно отключена
                        # content = await enforce_prompt_compliance(
                        #     content, "overdue", user_id, None, system_prompt, messages, url, headers
                        # )

                    return content
                else:
                    logger.error(f"Failed to generate overdue reminder: status {response.status}")
                    retry_msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Напоминание о просроченных задачах."}]
                    retry_data = {"model": DEEPSEEK_MODEL, "messages": retry_msg, "temperature": 0.7, "max_tokens": 200}
                    async with session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=20)) as retry_resp:
                        if retry_resp.status == 200:
                            retry_result = await retry_resp.json()
                            return retry_result["choices"][0]["message"]["content"].strip()
                    # Генерируем сообщение через AI с контекстом просроченных задач
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Просроченные задачи пользователя: {', '.join(task_titles)}. Создай короткое напоминание."}]
                        data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.8, "max_tokens": 80}
                        async with aiohttp.ClientSession() as sess:
                            async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    result = await resp.json()
                                    return result["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                    return "Обратите внимание на просроченные задачи."
    except Exception as e:
        logger.error(f"Error in generate_overdue_reminder: {e}")
        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Просроченные задачи. Напомни коротко."}]
            data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.8, "max_tokens": 50}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return res["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[REMINDER] AI reminder generation failed: {e}")
        return "Задачи ожидают внимания."


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


async def call_ai_with_tools(user_message, system_prompt, user_id, context=None):
    """Вызов AI с инструментами для обработки запроса"""

    try:
        # Создаем сообщение для AI
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Добавляем контекст разговора
        if context:
            for msg in context[-5:]:  # Последние 5 сообщений
                if msg.get('user'):
                    messages.append({"role": "user", "content": msg['user']})
                if msg.get('agent'):
                    messages.append({"role": "assistant", "content": msg['agent']})

        # Добавляем текущее сообщение пользователя
        messages.append({"role": "user", "content": user_message})

        # Вызываем AI API
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 1000
            }

            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }

            logger.info(f"[AI_CALL] Sending request to {DEEPSEEK_MODEL}")

            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"[AI_CALL] Response received, choices: {len(data.get('choices', []))}")

                    choice = data['choices'][0]
                    message = choice['message']

                    # Проверяем, есть ли tool calls
                    tool_calls = message.get('tool_calls', [])
                    response_text = message.get('content', '')

                    # Если есть tool calls, выполняем их
                    if tool_calls:
                        logger.info(f"[AI_CALL] Executing {len(tool_calls)} tool calls")
                        tool_results = []

                        for tool_call in tool_calls:
                            try:
                                function_name = tool_call['function']['name']
                                function_args = json.loads(tool_call['function']['arguments'])

                                logger.info(f"[TOOL_CALL] {function_name} with args: {function_args}")

                                # Выполняем функцию
                                if function_name == 'add_task':
                                    result = await add_task(
                                        user_id=user_id,
                                        title=function_args.get('title'),
                                        description=function_args.get('description', ''),
                                        reminder_time=function_args.get('reminder_time'),
                                        is_recurring=function_args.get('is_recurring', False),
                                        recurrence_pattern=function_args.get('recurrence_pattern'),
                                        recurrence_interval=function_args.get('recurrence_interval')
                                    )
                                elif function_name == 'complete_task':
                                    result = await complete_task(user_id=user_id, task_title=function_args.get('task_title'))
                                elif function_name == 'list_tasks':
                                    result = list_tasks(user_id=user_id)
                                elif function_name == 'reschedule_task':
                                    result = await reschedule_task(
                                        user_id=user_id,
                                        task_title=function_args.get('task_title'),
                                        new_time=function_args.get('new_time')
                                    )
                                elif function_name == 'find_relevant_contacts_for_task':
                                    result = find_relevant_contacts_for_task(
                                        user_id=user_id,
                                        task_description=function_args.get('task_description'),
                                        limit=function_args.get('limit', 5)
                                    )
                                elif function_name == 'find_partners':
                                    result = find_partners(user_id=user_id)
                                elif function_name == 'update_profile':
                                    result = update_profile(user_id=user_id, **function_args)
                                elif function_name == 'analyze_tasks':
                                    result = await analyze_tasks(user_id=user_id)
                                else:
                                    result = f"Функция {function_name} не поддерживается"

                                tool_results.append({
                                    'tool_call_id': tool_call.get('id'),
                                    'name': function_name,
                                    'content': str(result)
                                })

                            except Exception as e:
                                logger.error(f"[TOOL_CALL] Error executing {function_name}: {e}")
                                tool_results.append({
                                    'tool_call_id': tool_call.get('id'),
                                    'name': function_name,
                                    'content': f"Ошибка: {e}"
                                })

                        # Отправляем результаты инструментов обратно в AI для формирования ответа
                        # Добавляем результаты как контекст для AI
                        tool_results_text = "\n\n".join([
                            f"Результат {tr['name']}:\n{tr['content']}"
                            for tr in tool_results
                        ])
                        
                        # Добавляем результаты как системное сообщение для контекста
                        messages.append({
                            "role": "user",
                            "content": f"""Результаты выполнения инструментов:

{tool_results_text}

Теперь сформулируй человечный и понятный ответ пользователю, используя эти результаты. 
Если контактов не найдено - объясни почему и что делать дальше.
Если найдены - покажи их и расскажи как с ними связаться.
Отвечай кратко и по делу."""
                        })
                        
                        # Второй запрос к AI для формирования итогового ответа
                        payload_second = {
                            "model": DEEPSEEK_MODEL,
                            "messages": messages,
                            "temperature": 0.7,
                            "max_tokens": 500
                        }
                        
                        logger.info(f"[AI_CALL] Sending tool results back to AI for final response")
                        
                        async with session.post(
                            "https://api.deepseek.com/v1/chat/completions",
                            json=payload_second,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as response_second:
                            if response_second.status == 200:
                                data_second = await response_second.json()
                                final_response = data_second['choices'][0]['message'].get('content', '')
                                
                                if not final_response:
                                    # Fallback если AI не вернул текст
                                    final_response = "Выполнено: " + "; ".join([tr['content'] for tr in tool_results])
                            else:
                                # Fallback при ошибке второго запроса
                                final_response = "Выполнено: " + "; ".join([tr['content'] for tr in tool_results])

                    else:
                        # Нет tool calls, возвращаем текстовый ответ
                        final_response = response_text or "Извините, я не понял запрос"

                    # Проверяем соответствие промпту только для ответов БЕЗ tool calls
                    # Ответы после tool calls могут быть длиннее и не требуют валидации
                    if not tool_calls:
                        is_compliant, issues = validate_response_compliance(final_response, "response")
                        if not is_compliant:
                            logger.warning(f"[RESPONSE] Non-compliant response: {issues}")
                            final_response = "Извините, произошла ошибка при формировании ответа. Попробуйте переформулировать запрос."

                    return {
                        'response': final_response,
                        'tool_calls': tool_calls
                    }

                else:
                    error_text = await response.text()
                    logger.error(f"[AI_CALL] API error {response.status}: {error_text}")
                    return {
                        'response': "Извините, произошла ошибка при обращении к AI. Попробуйте позже.",
                        'tool_calls': []
                    }

    except Exception as e:
        logger.error(f"[AI_CALL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return {
            'response': f"Извините, произошла техническая ошибка: {str(e)}",
            'tool_calls': []
        }


# Функции для работы с задачами
