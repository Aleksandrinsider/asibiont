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
    update_profile, smart_update_profile, show_profile, delete_task, find_relevant_contacts_for_task, analyze_tasks,
    analyze_goal_progress
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
                except:
                    pass  # Оставляем UTC

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
                proactive_context=proactive_context
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

async def generate_reminder(user_id, task_title, task_id=None):
    """Генерирует текст напоминания о задаче с полным контекстом"""
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
                    except:
                        pass
        
        # Получить память и профиль пользователя
        user_memory = ""
        profile_context = ""
        if user.memory:
            try:
                decrypted = decrypt_data(user.memory)
                user_memory = f"\nИнформация о пользователе: {decrypted}"
            except:
                pass
        
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
            except:
                pass  # Keep UTC if all fails
        
        user_username = user.username if user and user.username else "пользователь"
        mentions_str = ""

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

        user_prompt = f"""Сгенерируй персонализированное напоминание о задаче: '{task_title}'.

ФОРМАТ ОТВЕТА: Напиши готовое сообщение для отправки пользователю (1-2 абзаца максимум).
- Начни с приветствия и напоминания о задаче
- Добавь мотивацию и практические советы
- ОБЯЗАТЕЛЬНО ЗАКОНЧИ ВОПРОСОМ О СТАТУСЕ ЗАДАЧИ: "Задача выполнена?" или "Как продвигается выполнение?" или подобным
- НЕ пиши промежуточные мысли или "сейчас посмотрю задачи"

КОНТЕКСТ ЗАДАЧИ:{task_context if task_context else 'Нет дополнительного контекста'}
КОНТЕКСТ ПРОФИЛЯ:{profile_context if profile_context else 'Нет информации о профиле'}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        data = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.8, "max_tokens": 300}
        
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
            except:
                pass  # Keep UTC if all fails
        
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
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя НЕТ АКТИВНЫХ ЗАДАЧ.

Создай КОРОТКОЕ (1-2 предложения) мотивирующее сообщение с 1-2 конкретными идеями задач на основе профиля. Добавь вопрос для вовлечения.""",
                
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: Пустой список задач - время для новых начинаний!

Напиши КОРОТКОЕ (1-2 предложения) сообщение с предложением 1-2 идей для задач из профиля пользователя. Закончи вопросом.""",
                
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: Нет активных задач - отличная возможность для планирования!

Создай КОРОТКОЕ (1-2 предложения) сообщение с конкретными предложениями задач на основе реальных данных профиля. Добавь вовлекающий вопрос."""
            ],

            "few_tasks": [
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя МАЛО ЗАДАЧ ({task_count}).

Создай КОРОТКОЕ (1-2 предложения) сообщение с практическим советом по оптимизации. Добавь вопрос.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: Оптимальная загруженность - {task_count} активных задач.

Напиши КОРОТКОЕ (1-2 предложения) с советом по улучшению продуктивности. Закончи вопросом.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: {task_count} задач - хорошая нагрузка для продуктивной работы.

Создай КОРОТКОЕ (1-2 предложения) сообщение с идеей оптимизации. Добавь вовлекающий вопрос."""
            ],

            "many_tasks": [
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя МНОГО ЗАДАЧ ({task_count}).

Создай КОРОТКОЕ (1 предложение) сообщение с советом по приоритизации или делегированию.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: Высокая загруженность - {task_count} активных задач.

Напиши КОРОТКОЕ (1 предложение) с предложением по управлению задачами.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: Много дел ({task_count} задач) - нужна организация.

Создай КОРОТКОЕ (1 предложение) сообщение с советом по приоритизации."""
            ],

            "overdue_tasks": [
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя ПРОСРОЧЕННЫЕ ЗАДАЧИ ({overdue_count}).

Создай КОРОТКОЕ (1-2 предложения) деликатное напоминание с предложением помощи.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: Есть просроченные задачи ({overdue_count}) - время действовать!

Напиши КОРОТКОЕ (1-2 предложения) мягкое напоминание с предложением помощи.""",
                
                f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: {overdue_count} просроченных задач требуют внимания.

Создай КОРОТКОЕ (1-2 предложения) деликатное сообщение с планом действий."""
            ],

            "general": [
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: Общий контакт.

Создай КОРОТКОЕ (1-2 предложения) персонализированное сообщение на основе РЕАЛЬНЫХ данных профиля. Добавь вопрос. НЕ ВЫДУМЫВАЙ информацию!""",
                
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: Регулярный контакт для поддержки продуктивности.

Напиши КОРОТКОЕ (1-2 предложения) с персонализированным советом из профиля. Закончи вопросом.""",
                
                """ПРОАКТИВНОЕ СООБЩЕНИЕ: Проверка прогресса и поддержка.

Создай КОРОТКОЕ (1-2 предложения) сообщение на основе реальных данных профиля. Добавь вовлекающий вопрос."""
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
                    except:
                        pass
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
                        except:
                            pass
                    tasks_info += f"• {task.title}{task_time}\n"
            else:
                tasks_info += "• Нет предстоящих задач\n"
                
            selected_prompt += tasks_info
        
        messages.append({"role": "user", "content": selected_prompt})

        # Используем параметры для более подробных, но не многословных сообщений
        temperature = 0.8  # Повысили для большего разнообразия
        top_p = 0.9

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 300  # Уменьшили для более коротких сообщений
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
                    # Контекстные fallback сообщения
                    fallback_messages = {
                        "no_tasks": "Привет! Вижу, что сейчас у тебя нет активных задач. Отличное время для планирования! Может, стоит добавить что-то важное на сегодня или подумать о целях на ближайшие дни?",
                        "few_tasks": f"Привет! У тебя сейчас {task_count} активные задачи - оптимальная загруженность! Может, есть что-то еще, что стоит добавить к планам, или нужна помощь с приоритизацией?",
                        "many_tasks": f"Привет! Вижу, что у тебя много дел ({task_count} задач). Возможно, стоит что-то делегировать или пересмотреть приоритеты? Могу помочь с организацией.",
                        "overdue_tasks": f"Привет! Обратил внимание, что есть {overdue_count} просроченных задач. Не переживай, давай вместе разберем их по приоритетам и составим план действий?",
                        "general": "Привет! Учитывая твой профиль, могу предложить несколько конкретных идей для продуктивного дня. Например, поработать над развитием навыков или планированием целей. Есть ли что-то конкретное, над чем ты хочешь сосредоточиться сегодня?"
                    }
                    return fallback_messages.get(context, fallback_messages["general"])

    except Exception as e:
        logger.error(f"Error in generate_proactive_message: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Контекстные fallback сообщения для исключений
        fallback_messages = {
            "no_tasks": "Добрый день! Отличное время для создания новых задач. Есть ли цели, над которыми ты хочешь поработать?",
            "few_tasks": f"Добрый день! Вижу у тебя {task_count} задач в работе. Как дела с выполнением? Нужна помощь с планированием?",
            "many_tasks": f"Добрый день! У тебя сейчас много задач ({task_count}). Может, стоит что-то делегировать или переосмыслить приоритеты?",
            "overdue_tasks": f"Добрый день! Есть {overdue_count} просроченных задач. Давай разберем их вместе и составим план восстановления?",
            "general": "Добрый день! Учитывая твой профиль и текущие задачи, могу предложить несколько конкретных идей для продуктивного дня. Например, поработать над развитием навыков или планированием целей. Есть ли что-то конкретное, над чем ты хочешь сосредоточиться?"
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
            except:
                pass  # Keep UTC if all fails
        
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
        except:
            pass
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
            except:
                pass  # Keep UTC if all fails
        
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
        except:
            pass
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
