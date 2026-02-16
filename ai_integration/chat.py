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
    replace_placeholders, clean_technical_details, sanitize_fake_mentions,
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

            # Получаем погоду и новости для контекста (async через api_client)
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_city = profile.city if profile and profile.city else None
            weather_info = None
            news_info = None
            try:
                from .api_client import get_api_client
                api = get_api_client()
                if user_city:
                    weather_data = await api.get_weather(user_city, cache_ttl=1800)
                    if weather_data:
                        weather_info = (
                            f"{weather_data['city_name']}: {weather_data['temp']:.0f}°C, "
                            f"{weather_data['description']}, влажность {weather_data['humidity']}%, "
                            f"ветер {weather_data['wind_speed']} м/с"
                        )
                    news_articles = await api.get_news(topic=user_city, page_size=3, cache_ttl=900)
                    if news_articles:
                        titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                        if titles:
                            news_info = f"Новости {user_city}:\n" + "\n".join(titles)
                if not news_info:
                    news_articles = await api.get_news(page_size=3, cache_ttl=900)
                    if news_articles:
                        titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                        if titles:
                            news_info = "Свежие новости России:\n" + "\n".join(titles)
            except Exception as e:
                logger.warning(f"[CONTEXT] Failed to load weather/news via api_client: {e}")
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
                # Фильтруем ложные @username
                response_data['response'] = sanitize_fake_mentions(
                    response_data['response'], user_id, session
                )
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
    """Генерирует напоминание через единый мозг агента (с tool calling).
    
    Args:
        user_id: ID пользователя
        task_title: Название задачи
        task_id: ID задачи (опционально)
        escalation_level: Уровень эскалации (1=мягко, 2=настойчиво, 3=критично)
    """
    try:
        from .autonomous_agent import get_autonomous_agent
        agent = get_autonomous_agent()

        # Формируем инструкцию с учётом уровня эскалации
        escalation_tones = {
            1: "дружелюбный и мягкий, как от друга",
            2: "настойчивый (это ПОВТОРНОЕ напоминание, прошло 15 минут)",
            3: "срочный и серьёзный (КРИТИЧЕСКОЕ напоминание, задача требует немедленного внимания)"
        }
        tone = escalation_tones.get(escalation_level, escalation_tones[1])

        instruction = (
            f"Сгенерируй напоминание о задаче «{task_title}»"
            f"{f' (ID: {task_id})' if task_id else ''}.\n"
            f"Тон: {tone}.\n"
            f"Уровень эскалации: {escalation_level}/3.\n"
            "Используй get_task_details если нужен контекст задачи.\n"
            "ОБЯЗАТЕЛЬНО заверши вопросом: «Задача выполнена?» или «Как продвигается?»"
        )

        result = await agent.generate_system_message(
            user_id=user_id,
            mode='reminder',
            instruction=instruction,
            max_tokens=300,
            max_iterations=2
        )
        
        logger.info(f"[REMINDER] Generated via agent brain: {result[:100]}...")
        return result

    except Exception as e:
        logger.error(f"Error in generate_reminder: {e}", exc_info=True)
        return f"Напоминание о задаче: {task_title}\nВремя приступить к выполнению. Готов начать?"


async def generate_result_check(user_id, task_title):
    """Генерирует поздравление с выполнением задачи через единый мозг агента."""
    try:
        from .autonomous_agent import get_autonomous_agent
        agent = get_autonomous_agent()

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
                        f"{weather_data['description']}, влажность {weather_data['humidity']}%, "
                        f"ветер {weather_data['wind_speed']} м/с"
                    )
                news_articles = await api.get_news(topic=profile.city, page_size=3, cache_ttl=900)
                if news_articles:
                    titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                    if titles:
                        ctx['news'] = f"Новости {profile.city}:\n" + "\n".join(titles)
            if not ctx['news']:
                news_articles = await api.get_news(page_size=3, cache_ttl=900)
                if news_articles:
                    titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                    if titles:
                        ctx['news'] = "Свежие новости России:\n" + "\n".join(titles)
            
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
                import asyncio
                try:
                    premium_ctx = await collect_premium_insights(user_id, mode='prompt', session=db_session)
                except Exception:
                    premium_ctx = None
                if premium_ctx and isinstance(premium_ctx, str) and premium_ctx.strip():
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
    # ctx['profile'] — объект UserProfile или None
    profile_obj = ctx.get('profile')
    has_profile = bool(profile_obj and (getattr(profile_obj, 'goals', None) or getattr(profile_obj, 'interests', None) or getattr(profile_obj, 'skills', None)))
    
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
    
    # task_count == 0 больше НЕ критический флаг — это один из многих сценариев.
    # Разнообразие обеспечивается через ротацию типов сообщений ниже.
    
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
    
    # === ЗЕЛЁНЫЕ СИГНАЛЫ — возможности для роста ===
    opportunities = []
    
    if profile_obj:
        skills = getattr(profile_obj, 'skills', None) or ''
        interests = getattr(profile_obj, 'interests', None) or ''
        position = getattr(profile_obj, 'position', None) or ''
        prof_goals = getattr(profile_obj, 'goals', None) or ''
        
        # Пересечение навыков и интересов → уникальная комбинация
        if skills and interests and position:
            opportunities.append(
                f"РЕСУРСЫ: [{position} | {skills[:50]} | {interests[:50]}] — "
                "ищи где навыки + интересы создают уникальную комбинацию для роста"
            )
        
        # Навыки + цель → мост
        if skills and prof_goals:
            opportunities.append(
                f"СВЯЗКА: навыки [{skills[:40]}] + цель [{prof_goals[:40]}] — "
                "какой кратчайший путь от ресурсов к цели?"
            )
        
        # Работа ≠ интерес → точка роста на стыке
        if position and interests and position.lower() not in interests.lower():
            opportunities.append(
                f"ПЕРЕСЕЧЕНИЕ: работа [{position}] + интерес [{interests[:40]}] — "
                "возможная точка роста на стыке сфер"
            )
    
    if opportunities:
        parts.append("\n=== 🟢 ВОЗМОЖНОСТИ (ищи точки роста!) ===")
        parts.extend(opportunities)
    
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
    
    # === РОТАЦИЯ ТИПОВ СООБЩЕНИЙ ===
    # Каждое проактивное сообщение должно быть ДРУГОГО типа.
    # Используем хеш от даты+часа чтобы тип был детерминированным но разным.
    import hashlib
    rotation_seed = f"{ctx['user_now'].strftime('%Y-%m-%d-%H')}_{user_id if 'user' not in ctx else ctx['user'].telegram_id}"
    rotation_hash = int(hashlib.md5(rotation_seed.encode()).hexdigest(), 16)
    
    # Доступные типы сообщений с условиями
    message_types = []
    
    # Тип 1: Вопрос о планах/мнении (всегда доступен)
    message_types.append({
        'type': 'question',
        'instruction': 'Задай пользователю ОДИН интересный вопрос. Связь с его профилем/целями. Примеры: "Как продвигается [цель]?", "Что думаешь о [тренд в его сфере]?", "Какой главный приоритет на сегодня?". НЕ предлагай план — просто спроси.'
    })
    
    # Тип 2: Актуальная статистика/прогресс (если есть данные)
    if ctx.get('goals') or ctx.get('task_count', 0) > 0:
        message_types.append({
            'type': 'stats',
            'instruction': 'Покажи КРАТКУЮ статистику или прогресс. Например: прогресс по целям (%), выполненные задачи за неделю, тренд продуктивности. Сухие факты + 1 вывод.'
        })
    
    # Тип 3: Релевантный контакт/партнёр (если есть партнёры)
    if ctx.get('partners'):
        message_types.append({
            'type': 'contact',
            'instruction': 'Расскажи о КОНКРЕТНОМ релевантном контакте из данных партнёров. Кто это, чем полезен, почему стоит связаться. Используй ТОЛЬКО реальные @username из данных.'
        })
    
    # Тип 4: Новость/тренд по интересам (если есть новости)
    if ctx.get('news'):
        message_types.append({
            'type': 'discussion',
            'instruction': 'Поделись ОДНОЙ интересной новостью/трендом из данных, связанной с интересами/целями пользователя. Кратко суть + "Что думаешь?". Начни обсуждение, а не монолог.'
        })
    
    # Тип 5: Фокус на текущем моменте (если задач 0)
    if task_count == 0:
        message_types.append({
            'type': 'plan',
            'instruction': 'Спроси над чем пользователь работает СЕЙЧАС. Предложи помощь в текущем моменте — анализ, исследование, планирование. НЕ предлагай "создать задачу на завтра". Фокус на СЕГОДНЯ и СЕЙЧАС.'
        })
    
    # Тип 6: Погода + активность (если есть погода)
    if ctx.get('weather'):
        message_types.append({
            'type': 'weather',
            'instruction': 'Коротко о погоде + конкретное предложение активности, связанное с интересами. Не просто "хорошая погода" — а что конкретно можно сделать.'
        })
    
    # Тип 7: Актуальные задачи — напоминание/мотивация (если есть задачи)
    if task_count > 0:
        message_types.append({
            'type': 'tasks',
            'instruction': 'Напомни о текущих задачах. Какая самая важная? Что стоит сделать первым? Конкретный совет по приоритизации. Не перечисляй все — выдели 1 главную.'
        })
    
    # Выбираем тип по ротации (детерминированно, не случайно — чтобы не повторялся)
    selected = message_types[rotation_hash % len(message_types)]
    
    parts.append(f"\n=== ТИП СООБЩЕНИЯ: {selected['type'].upper()} ===")
    parts.append(selected['instruction'])
    
    # Если не выбран 'plan' — явно запрещаем предлагать план дня
    if selected['type'] != 'plan':
        parts.append("\n⛔ НЕ предлагай план дня / список задач. Сегодня другой тип сообщения.")
    
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

ПРОЦЕСС (тот же что в основном диалоге):
1. КАРТИНА: посмотри на все данные выше — профиль, задачи, цели, интересы, время суток
2. РЕСУРСЫ: что у человека уже есть? Навыки, контакты, опыт — что можно задействовать?
3. ВОЗМОЖНОСТИ: где он может расти? Какие тренды/изменения работают в его пользу? Какой один шаг даст максимум?
4. ПРОТИВОРЕЧИЯ: что не сходится? Цель без действий? Перегрузка? Ресурсы простаивают?
5. ОДНА МЫСЛЬ: выбери ОДНО самое ценное — возможность или противоречие — и скажи естественно

Если есть красные флаги — они ПРИОРИТЕТ.

ОБЯЗАТЕЛЬНО:
- 2-5 предложений, живой тон, конкретика
- Минимум 1 КОНКРЕТНОЕ действие (не "могу помочь", а конкретика)
- Привязывай к профилю/целям/интересам пользователя
- Если предлагаешь задачу — указывай ТОЧНОЕ ВРЕМЯ

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
    """Единый умный генератор проактивных сообщений через мозг агента.
    
    Использует _build_proactive_context() для ситуационного анализа,
    затем передаёт всё через agent.generate_system_message() с tool calling.
    """
    try:
        # 1. Собираем полный контекст ситуации
        ctx = await _build_proactive_context(user_id)
        if not ctx:
            return "Привет! Готов помочь. Что обсудим?"
        
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
        situation_prompt = _build_situation_prompt(ctx, intent=intent, tasks_list=tasks_list)
        
        # 4. Антиповтор — запрещаем повторять последние ответы
        anti_repeat = ""
        if ctx.get('last_responses'):
            anti_repeat = "\n\nЗАПРЕЩЕНО повторять эти фразы (твои последние ответы):\n"
            anti_repeat += "\n".join(f"- {r}" for r in ctx['last_responses'])
            anti_repeat += "\nГенерируй УНИКАЛЬНЫЙ ответ!"
        
        # 5. Генерируем через единый мозг агента
        from .autonomous_agent import get_autonomous_agent
        agent = get_autonomous_agent()

        instruction = (
            "Напиши проактивное сообщение пользователю на основе анализа ситуации выше. "
            "Используй инструменты если нужны актуальные данные (задачи, новости, погода)."
        )

        result = await agent.generate_system_message(
            user_id=user_id,
            mode='proactive',
            instruction=instruction,
            extra_context=situation_prompt + anti_repeat,
            max_tokens=600,
            max_iterations=2
        )

        logger.info(f"[PROACTIVE] Generated via agent brain: {result[:100]}...")
        return result

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
