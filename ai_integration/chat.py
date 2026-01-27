from . import handlers
import aiohttp
import json
import logging
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
import re
import pytz
import hashlib
import time
from functools import lru_cache

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, Task, UserProfile, Subscription
from .memory import encrypt_data, decrypt_data
from .utils import (
    determine_timezone_from_time, analyze_user_context_for_advice,
    replace_placeholders, clean_technical_details,
    post_process_tool_calls, smart_fallback_handler,
    post_process_response
)
from .prompts import get_extended_system_prompt
from .tools import TOOLS
from .handlers import (
    add_task, delete_all_tasks, complete_task, skip_task, restore_task, reschedule_task,
    get_task_advice, delegate_task_with_session, check_subscription_status, accept_delegated_task,
    reject_delegated_task, get_delegation_progress, get_delegation_progress_for_task, cancel_delegation, edit_task,
    list_tasks, enrich_task_list_with_insights, get_partners_list, find_partners,
    generate_delegation_notification_async, generate_progress_request, schedule_delegation_monitoring,
    check_delegation_deadlines, update_user_memory_async, delete_task_sync, create_subscription_payment,
    cancel_subscription, brainstorm_ideas_async, get_task_details_async, suggest_alternatives_async,
    suggest_trends_and_opportunities_async as suggest_trends_and_opportunities, update_profile, delete_task
)

logger = logging.getLogger(__name__)

# Базовый системный промпт для простых сообщений
system_prompt = "Ты - ASI Biont, умный AI-помощник для управления задачами и повышения продуктивности. Отвечай кратко и по делу."

# ПРОСТОЙ IN-MEMORY КЭШ ДЛЯ ОТВЕТОВ AI
class SimpleCache:
    """Простой in-memory кеш с TTL и ограничением размера"""
    def __init__(self, max_size=1000, ttl_seconds=300):  # 5 минут TTL
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl_seconds
        logger.info("[CACHE] Using in-memory cache")

    def _get_key(self, messages, temperature, max_tokens):
        """Генерируем ключ на основе содержимого запроса"""
        content = ""
        for msg in messages:
            content += f"{msg.get('role', '')}:{msg.get('content', '')}"
        content += f"temp:{temperature}max:{max_tokens}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, messages, temperature, max_tokens):
        key = self._get_key(messages, temperature, max_tokens)
        if key in self.cache:
            entry = self.cache[key]
            if time.time() - entry['timestamp'] < self.ttl:
                logger.info(f"[CACHE HIT] Using cached response for key {key[:8]}...")
                return entry['response']
            else:
                # Удаляем просроченный кэш
                del self.cache[key]
        return None

    def set(self, messages, temperature, max_tokens, response):
        key = self._get_key(messages, temperature, max_tokens)
        if len(self.cache) >= self.max_size:
            # Удаляем самый старый элемент
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k]['timestamp'])
            del self.cache[oldest_key]

        self.cache[key] = {
            'response': response,
            'timestamp': time.time()
        }
        logger.info(f"[CACHE SET] Cached response for key {key[:8]}...")

    def get_by_key(self, key):
        if key in self.cache:
            entry = self.cache[key]
            if time.time() - entry['timestamp'] < entry.get('ttl', self.ttl):
                logger.info(f"[CACHE HIT] Using cached response for key {key[:8]}...")
                return entry['response']
            else:
                # Удаляем просроченный кэш
                del self.cache[key]
        return None

    def set_by_key(self, key, response, ttl=None):
        ttl = ttl or self.ttl

        if len(self.cache) >= self.max_size:
            # Удаляем самый старый элемент
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k]['timestamp'])
            del self.cache[oldest_key]

        self.cache[key] = {
            'response': response,
            'timestamp': time.time(),
            'ttl': ttl
        }
        logger.info(f"[CACHE SET] In-memory cached response for key {key[:8]}...")

# Глобальный кэш
cache = SimpleCache()

add_task = handlers.add_task
complete_task = handlers.complete_task
delegate_task = handlers.delegate_task
accept_delegated_task = handlers.accept_delegated_task
reject_delegated_task = handlers.reject_delegated_task
list_tasks = handlers.list_tasks
find_partners = handlers.find_partners
update_profile = handlers.update_profile
update_user_memory = handlers.update_user_memory_async
delegate_task = handlers.delegate_task_with_session
delete_task = handlers.delete_task
edit_task = handlers.edit_task
get_delegation_progress = handlers.get_delegation_progress

async def send_error_notification_to_bot(error_message, user_id=None, error_details=None, target_user_id=None):
    """Отправляет уведомление об ошибке разработчику в Telegram или указанному пользователю"""
    try:
        from config import TELEGRAM_TOKEN, DEVELOPER_CHAT_ID

        if not TELEGRAM_TOKEN:
            logger.warning("TELEGRAM_TOKEN not configured, skipping error notification")
            return

        # Определяем, кому отправлять уведомление
        chat_id = target_user_id if target_user_id else DEVELOPER_CHAT_ID

        if not chat_id:
            logger.warning("No chat_id configured (neither target_user_id nor DEVELOPER_CHAT_ID), skipping error notification")
            return

        # Формируем сообщение об ошибке
        notification_text = f"🚨 СИСТЕМНАЯ ОШИБКА\n\n"
        if user_id:
            notification_text += f"👤 Пользователь: {user_id}\n"
        notification_text += f"⏰ Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        if error_details:
            notification_text += f"📋 Детали: {error_details[:500]}\n"  # Ограничиваем длину
        notification_text += f"💬 Сообщение: {error_message[:200]}"

        # Используем Telegram Bot API для отправки сообщения
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": notification_text,
            "parse_mode": "HTML"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    logger.info(f"Error notification sent to {'user ' + str(target_user_id) if target_user_id else 'developer'} successfully")
                else:
                    error_text = await response.text()
                    logger.warning(f"Failed to send error notification to {'user ' + str(target_user_id) if target_user_id else 'developer'}: {response.status} - {error_text}")

    except Exception as e:
        logger.error(f"Error sending notification to {'user ' + str(target_user_id) if target_user_id else 'developer'}: {e}")
        # Не выбрасываем исключение, чтобы не прерывать основной поток
check_subscription_status = handlers.check_subscription_status
create_subscription_payment = handlers.create_subscription_payment
cancel_subscription = handlers.cancel_subscription
brainstorm_ideas = handlers.brainstorm_ideas_async
enrich_task_list_with_insights = handlers.enrich_task_list_with_insights
get_partners_list = handlers.get_partners_list
get_task_details = handlers.get_task_details_async
get_delegation_progress = handlers.get_delegation_progress
cancel_delegation = handlers.cancel_delegation
suggest_alternatives = handlers.suggest_alternatives_async

async def process_tool_calls(tool_calls, intent, message, user_id, db_session, session_http, url, headers, system_prompt, user_now, current_time_str, original_message, mentions_str, is_advice_question=False, current_time=None):
    """Обрабатывает tool calls и возвращает естественный ответ
    
    Args:
        current_time: Текущее время пользователя (datetime object с timezone)
    """
    logger = logging.getLogger(__name__)
    
    # Если current_time не передан, используем user_now
    if current_time is None:
        current_time = user_now
    logger.info(f"[PROCESS_TOOL_CALLS] Called with user_id={user_id}, tool_calls count={len(tool_calls) if tool_calls else 0}")
    
    # Print tool_calls for debugging
    if tool_calls:
        for i, tc in enumerate(tool_calls):
            logger.info(f"[PROCESS_TOOL_CALLS] Tool call {i}: function={tc.get('function', {}).get('name')}, args={tc.get('function', {}).get('arguments', '')[:100]}")
    else:
        logger.warning("[PROCESS_TOOL_CALLS] tool_calls is empty or None!")
    
    if user_id is None:
        logger.error(f"[PROCESS_TOOL_CALLS] ERROR: user_id is None! Cannot process tool calls without user_id")
        return None
        
    if not tool_calls:
        return None
    
    # КРИТИЧЕСКАЯ ВАЛИДАЦИЯ: Проверяем соответствие tool calls команде пользователя
    message_lower = message.lower()
    tool_names = [tc.get('function', {}).get('name') for tc in tool_calls]
    
    # Определяем ожидаемые tools на основе команды
    expected_tools = None
    disallowed_tools = []  # Инструменты, которые точно НЕ должны вызываться
    
    # ПРИОРИТЕТНАЯ детекция: проверяем сначала самые специфичные паттерны
    # 0. МАССОВЫЕ ОПЕРАЦИИ (наивысший приоритет)
    if any(kw in message_lower for kw in ['все задачи', 'всех задач', 'все мои задачи']):
        if any(kw in message_lower for kw in ['удали', 'убери', 'очисти', 'закрой', 'удалить']):
            expected_tools = ['delete_all_tasks']
            disallowed_tools = ['add_task', 'delete_task', 'complete_task', 'edit_task']
        elif any(kw in message_lower for kw in ['готово', 'завершил', 'выполнил', 'сделал', 'завершить', 'закончил']):
            expected_tools = ['complete_all_tasks', 'delete_all_tasks']  # Может быть любой массовой операцией
            disallowed_tools = ['add_task', 'delete_task', 'complete_task', 'edit_task']
        else:
            expected_tools = ['list_tasks']
            disallowed_tools = ['add_task', 'delete_task', 'complete_task', 'edit_task']
    # 1. ЗАВЕРШЕНИЕ (высокий приоритет)
    elif any(kw in message_lower for kw in ['готово', 'сделал', 'выполнил', 'завершил', 'задача выполнена', 'выполнена']):
        expected_tools = ['complete_task']
        disallowed_tools = ['add_task', 'delete_task', 'edit_task']  # Не создавать/удалять при завершении
    # 2. УДАЛЕНИЕ (высокий приоритет)
    elif any(kw in message_lower for kw in ['удали', 'убери', 'удалить']):
        expected_tools = ['delete_task']
        disallowed_tools = ['add_task', 'complete_task', 'edit_task']  # Не создавать при удалении
    # 3. ПРОСМОТР (высокий приоритет)
    elif any(kw in message_lower for kw in ['покажи', 'список', 'какие задачи', 'мои задачи', 'что запланировано']):
        expected_tools = ['list_tasks']
        disallowed_tools = ['add_task', 'complete_task', 'delete_task', 'edit_task']  # Не изменять при просмотре
    # 4. ПЕРЕНОС/ИЗМЕНЕНИЕ (средний приоритет)
    elif any(kw in message_lower for kw in ['перенеси', 'измени', 'обнови', 'перенести']):
        expected_tools = ['edit_task', 'reschedule_task']
        disallowed_tools = ['add_task']  # Не создавать новую при переносе
    # 5. СОЗДАНИЕ (низкий приоритет - только если есть явное указание времени)
    elif any(kw in message_lower for kw in ['напомни', 'создай', 'добавь']) and any(kw in message_lower for kw in ['через', 'в', 'завтра', 'сегодня', 'час', 'минут']):
        expected_tools = ['add_task']
        disallowed_tools = ['edit_task', 'reschedule_task', 'complete_task', 'delete_task']  # Не переносить/завершать при создании
    
    # Проверяем недопустимые tools
    if disallowed_tools:
        has_disallowed = False
        for tool_name in tool_names:
            if tool_name in disallowed_tools:
                logger.error(f"[TOOL VALIDATION FAILED] Disallowed tool {tool_name} for message: {message[:100]}")
                logger.error(f"[TOOL VALIDATION] Expected one of {expected_tools}, got disallowed {tool_name}")
                has_disallowed = True
        
        if has_disallowed:
            logger.error("[VALIDATION FAILED] Setting tool_calls to empty to trigger anti-hallucination")
            # Возвращаем пустой список вместо None для правильной обработки
            return None
    
    # Если есть ожидаемые tools, проверяем соответствие
    if expected_tools:
        if not any(tool in expected_tools for tool in tool_names):
            logger.error(f"[TOOL VALIDATION FAILED] Expected {expected_tools}, got {tool_names}")
            logger.error(f"[TOOL VALIDATION] Message: {message[:100]}")
            logger.error("[VALIDATION FAILED] Setting tool_calls to empty to trigger anti-hallucination")
            # Возвращаем None, чтобы сработала антигаллюцинация
            return None
        
    # ПОСТ-ПРОЦЕССИНГ: Корректируем tool calls на основе intent
    corrected_tool_calls = post_process_tool_calls(intent, tool_calls, message)
    if corrected_tool_calls:
        tool_calls = corrected_tool_calls

    logger.info(f"[PROCESS_TOOL_CALLS] After duplicate check: {len(tool_calls)} tool calls")
    if not tool_calls:
        logger.warning("[PROCESS_TOOL_CALLS] No tool calls to process after duplicate check!")

    # Если это вопрос о совете, игнорируем tool_calls и обрабатываем как обычный текст
    if is_advice_question:
        return None
        
    # Обработка tool calls
    tool_results = []
    logger.info(f"[PROCESS_TOOL_CALLS] Starting to process {len(tool_calls)} tool calls")
    for tool_call in tool_calls:
        try:
            func_name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])
            logger.info(f"[TOOL CALL] Executing {func_name} with args: {args}")

            if func_name == "add_task":
                # logger.info(
                #     f"[AI TOOL CALL] add_task called with args: {args}, intent params: {intent.get('params', {})}")
                
                # КРИТИЧНО: Фильтруем слишком длинные title (берут весь текст сообщения)
                task_title = args.get("title", args.get("task_title", "Задача"))
                
                # Если title слишком длинный (>60 символов или >10 слов) - пытаемся извлечь правильный
                word_count = len(task_title.split())
                if len(task_title) > 60 or word_count > 10:
                    logger.warning(f"[ADD TASK] Title too long ({len(task_title)} chars, {word_count} words), extracting short version from: {task_title[:80]}")
                    
                    # Пытаемся извлечь короткое название из оригинального сообщения
                    from .utils import extract_short_title_from_message
                    extracted_title = await extract_short_title_from_message(original_message, task_title)
                    
                    if extracted_title:
                        logger.info(f"[ADD TASK] Extracted short title: '{extracted_title}'")
                        # Обновляем args с правильным title
                        args["title"] = extracted_title
                        task_title = extracted_title
                    else:
                        # Если не удалось извлечь - возвращаем ошибку
                        logger.warning(f"[ADD TASK] SKIPPED - failed to extract short title from: {task_title[:80]}")
                        tool_results.append({"function": func_name, "result": f"ERROR: Название задачи слишком длинное ({word_count} слов). Нужно краткое название (2-5 слов). Пример: 'Позвонить клиенту', 'Подготовить отчёт'."})
                        continue
                
                # ПРОВЕРКА ВРЕМЕНИ: извлекаем точное время из сообщения и сравниваем с тем что предложил AI
                from .utils import extract_time_from_message
                user_specified_time = extract_time_from_message(original_message)
                if user_specified_time:
                    # Проверяем что AI правильно распарсил время
                    ai_time = args.get("reminder_time", "")
                    if ai_time and user_specified_time not in ai_time:
                        logger.warning(f"[ADD TASK] Time mismatch: user said '{user_specified_time}' but AI parsed '{ai_time}'")
                        # ИСПРАВЛЕНИЕ: заменяем на простой формат HH:MM
                        # Handler в add_task правильно обработает такой формат с учётом текущего времени
                        args["reminder_time"] = user_specified_time
                        logger.info(f"[ADD TASK] Corrected time from '{ai_time}' to '{user_specified_time}' (simple HH:MM format)")
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА: Если AI вызвал add_task, значит время указано
                # Проверяем есть ли reminder_time в аргументах
                if not args.get("reminder_time"):
                    logger.info(f"[ADD TASK] No reminder_time in args - setting waiting state for user {user_id}")
                    
                    # Получаем пользователя для обновления состояния
                    from models import User
                    user_obj = db_session.query(User).filter_by(telegram_id=user_id).first()
                    if user_obj:
                        # Сохраняем данные задачи для создания
                        task_data = {
                            'title': task_title,
                            'description': args.get('description', ''),
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        user_obj.conversation_state = 'waiting_for_task_time'
                        user_obj.pending_task_data = json.dumps(task_data)
                        db_session.commit()
                        
                        tool_results.append({"function": func_name, "result": f"NEED_TIME_FOR_TASK: {task_title}"})
                        continue
                    else:
                        logger.error(f"[ADD TASK] User not found for telegram_id {user_id}")
                        tool_results.append({"function": func_name, "result": "Ошибка: пользователь не найден"})
                        continue
                
                # КРИТИЧЕСКИ ВАЖНО: Правильно обрабатываем относительное время
                # Если в сообщении "через X минут/часов" - ВСЕГДА пересчитываем от current_time
                reminder_time = args.get("reminder_time")
                
                # Проверяем относительное время в оригинальном сообщении
                from ai_integration.utils import parse_relative_time
                logger.info(f"[ADD TASK] About to call parse_relative_time with current_time type: {type(current_time)}, value: {current_time}")
                relative_time_result = parse_relative_time(original_message, current_time)
                if relative_time_result:
                    # Если нашли относительное время - ИСПОЛЬЗУЕМ его вместо AI расчета
                    reminder_time = relative_time_result.strftime("%Y-%m-%d %H:%M")
                    logger.info(f"[ADD TASK] Recalculated relative time: {reminder_time} (current_time: {current_time.strftime('%H:%M')})")
                
                if not reminder_time or '@unknown' in str(reminder_time):
                    reminder_time = intent.get("params", {}).get("reminder_time")
                
                # Валидация reminder_time
                # logger.info(f"[ADD TASK] reminder_time={reminder_time}, has_time={has_time}")

                # Если reminder_time не валиден - устанавливаем состояние ожидания
                if not reminder_time or reminder_time in ['', 'None', 'null', '@unknown']:
                    logger.info(f"[ADD TASK] Invalid reminder_time - setting waiting state for user {user_id}")
                    
                    # Получаем пользователя для обновления состояния
                    from models import User
                    user_obj = db_session.query(User).filter_by(telegram_id=user_id).first()
                    if user_obj:
                        # Сохраняем данные задачи для создания
                        task_data = {
                            'title': task_title,
                            'description': args.get('description', ''),
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }
                        user_obj.conversation_state = 'waiting_for_task_time'
                        user_obj.pending_task_data = json.dumps(task_data)
                        db_session.commit()
                        
                        tool_results.append({"function": func_name, "result": f"NEED_TIME_FOR_TASK: {task_title}"})
                    else:
                        logger.error(f"[ADD TASK] User not found for telegram_id {user_id}")
                        tool_results.append({"function": func_name, "result": "Ошибка: пользователь не найден"})
                else:
                    # Вызываем add_task только с валидным временем
                    result = add_task(
                        title=args.get("title", args.get("task_title", "Задача")),
                        description=args.get("description", ""),
                        reminder_time=reminder_time,
                        user_id=user_id,
                        session=db_session,
                    )
                    tool_results.append({"function": func_name, "result": result})

            elif func_name == "complete_task":
                task_title = args.get("task_title") or intent.get("params", {}).get("task_title")
                result = await complete_task(
                    task_id=args.get("task_id"),
                    task_title=task_title,
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})
                # Перезагрузить список задач после завершения
                updated_tasks = list_tasks(user_id=user_id, session=db_session)
                tool_results.append({"function": "list_tasks", "result": f"[Обновленный список после завершения] {updated_tasks}"})

            elif func_name == "accept_delegated_task":
                result = accept_delegated_task(
                    task_id=args.get("task_id"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "reject_delegated_task":
                result = reject_delegated_task(
                    task_id=args.get("task_id"),
                    task_title=args.get("task_title"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "list_tasks":
                include_completed = args.get("include_completed", False)
                result = list_tasks(user_id=user_id, session=db_session, include_completed=include_completed)
                # Add delegation instructions if this is for delegation
                if intent.get("params", {}).get("for_delegation"):
                    target_user = intent.get("params", {}).get("target_user", "")
                    result += f"\n\nЧтобы делегировать задачу, скажите: 'делегировать задачу [ID или название] пользователю {target_user} дедлайн [время]'"
                    result += f"\nНапример: 'делегировать задачу 1 пользователю {target_user} дедлайн завтра в 15:00'"
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "find_partners":
                result = find_partners(user_id=user_id, session=db_session)
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "update_profile":
                # ВАЛИДАЦИЯ: Для неявных обновлений профиля обязательно уведомляем пользователя
                is_explicit_update = intent.get("type") == "update_profile"
                result = await update_profile(
                    city=args.get("city"),
                    company=args.get("company"),
                    position=args.get("position"),
                    interests=args.get("interests"),
                    skills=args.get("skills"),
                    goals=args.get("goals"),
                    user_id=user_id,
                    session=db_session,
                )

                # Если это не явное обновление профиля, добавляем уведомление
                if not is_explicit_update:
                    result += "\n\n📝 Профиль автоматически обновлен на основе нашего разговора. Если информация не верна, скажите 'исправь мой профиль'."

                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delegate_task":
                result = delegate_task(
                    title=args.get("title"),
                    description=args.get("description", ""),
                    reminder_time=args.get("reminder_time"),
                    delegated_to_username=args.get("delegated_to_username"),
                    user_id=user_id,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delete_all_tasks":
                result = delete_all_tasks(user_id=user_id, session=db_session)
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delete_task":
                result = delete_task_sync(
                    task_id=args.get("task_id"),
                    task_title=args.get("task_title"),
                    user_id=user_id,
                    session=db_session,
                    confirmed=True  # AI уже подтвердил через tool call
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "edit_task":
                # КРИТИЧЕСКИ ВАЖНО: Правильно обрабатываем относительное время для edit_task
                # Если в сообщении "через X минут/часов" - ВСЕГДА пересчитываем от current_time
                reminder_time = args.get("reminder_time")
                
                # Проверяем относительное время в оригинальном сообщении
                from ai_integration.utils import parse_relative_time
                logger.info(f"[EDIT TASK] About to call parse_relative_time with current_time type: {type(current_time)}, value: {current_time}")
                relative_time_result = parse_relative_time(original_message, current_time)
                if relative_time_result:
                    # Если нашли относительное время - ИСПОЛЬЗУЕМ его вместо AI расчета
                    reminder_time = relative_time_result.strftime("%Y-%m-%d %H:%M")
                    logger.info(f"[EDIT TASK] Recalculated relative time: {reminder_time} (current_time: {current_time.strftime('%H:%M')})")
                
                result = handlers.edit_task(
                    task_id=args.get("task_id"),
                    title=args.get("title"),
                    description=args.get("description"),
                    reminder_time=reminder_time,
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "reschedule_task":
                result = await handlers.reschedule_task(
                    task_title=args.get("task_title"),
                    new_time=args.get("new_time"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "check_subscription_status":
                result = check_subscription_status(user_id=user_id)
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "create_subscription_payment":
                result = create_subscription_payment(
                    tier=args.get("tier"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "brainstorm_ideas":
                result = await brainstorm_ideas(
                    topic=args.get("topic"),
                    context=args.get("context"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "enrich_task_list_with_insights":
                result = enrich_task_list_with_insights(user_id=user_id, session=db_session)
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "get_partners_list":
                # Convert telegram_id to database user.id
                temp_session = Session()
                temp_user = temp_session.query(User).filter_by(telegram_id=user_id).first()
                if temp_user:
                    result = get_partners_list(user_id=temp_user.id, session=temp_session)
                else:
                    result = []
                temp_session.close()
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "update_user_memory":
                result = await update_user_memory(
                    memory_type=args.get("memory_type"),
                    content=args.get("content"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "get_task_details":
                result = await get_task_details(
                    task_title=args.get("task_title"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "suggest_alternatives":
                result = await suggest_alternatives(
                    task_title=args.get("task_title"),
                    reason=args.get("reason"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "suggest_trends_and_opportunities":
                result = await suggest_trends_and_opportunities(
                    focus_area=args.get("focus_area"),
                    num_suggestions=args.get("num_suggestions", 3),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            else:
                logger.warning(f"[TOOL CALL] Unknown function: {func_name}")
                tool_results.append({"function": func_name, "result": f"Неизвестная функция: {func_name}"})

        except Exception as e:
            logger.error(f"[TOOL CALL] Error executing {func_name}: {e}")
            tool_results.append(
                {"function": func_name, "result": f"Ошибка выполнения: {str(e)}"}
            )

    # Генерируем естественный ответ на основе результатов tool calls
    if tool_results:
        natural_responses = []
        has_list_tasks = False
        list_tasks_result = None

        for action in tool_results:
            result_text = action["result"]
            func_name = action["function"]

            # Проверяем, есть ли list_tasks в результатах
            if func_name == "list_tasks":
                has_list_tasks = True
                list_tasks_result = result_text
            
            # Если нужно время - запрашиваем (задача НЕ создана)
            if result_text == "NEED_TIME" or (result_text and result_text.startswith("NEED_TIME:")):
                # Задача НЕ создана - передаём только факт, промпт знает что делать
                messages = [{"role": "user", "content": original_message}]
                messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
                messages.append({"role": "user", "content": "Задача НЕ создана - пользователь не указал время."})
                
                data = {
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 300
                }
                
                try:
                    async with session_http.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as ai_response:
                        if ai_response.status == 200:
                            ai_result = await ai_response.json()
                            time_request = ai_result["choices"][0]["message"]["content"].strip()
                            natural_responses.append(time_request)
                        else:
                            natural_responses.append("Когда тебе напомнить об этом? Укажи время")
                except Exception as e:
                    logger.warning(f"[NEED_TIME AI] Failed: {e}")
                    natural_responses.append("Когда тебе напомнить? Укажи удобное время")
                continue

            if "Добавлена задача" in result_text:
                # Парсим детали задачи для персонализированного ответа
                match = re.search(r"Добавлена задача '([^']+)'", result_text)
                if match:
                    task_title = match.group(1)
                    # Передаем детали для умного ответа
                    time_match = re.search(r"с напоминанием на ([\d\.]+) в ([\d:]+)", result_text)
                    if time_match:
                        date_part = time_match.group(1)
                        time_part = time_match.group(2)
                        natural_responses.append(f"TASK_CREATED: {task_title} | scheduled_at: {date_part} {time_part}")
                    else:
                        natural_responses.append(f"TASK_CREATED: {task_title}")
                else:
                    natural_responses.append("TASK_CREATED: Новая задача")
            
            elif "DUPLICATE_TASK:" in result_text:
                # Дубликат задачи - НЕ добавляем маркер, AI сам обработает
                pass

            elif "Задача выполнена" in result_text or "отмечена как выполненная" in result_text:
                natural_responses.append("Задача выполнена")

            elif "Вы приняли задачу" in result_text:
                # Обработка принятия делегированной задачи
                match = re.search(r"Вы приняли задачу '([^']+)'", result_text)
                if match:
                    task_title = match.group(1)
                    natural_responses.append(f"TASK_ACCEPTED: {task_title}")
                else:
                    natural_responses.append("TASK_ACCEPTED")

            elif "Найдены партнеры:" in result_text or "партнеры найдены" in result_text.lower():
                natural_responses.append(result_text)

            elif "Профиль обновлен" in result_text:
                # Парсим детали обновления и передаем AI только факты
                if "added_interests:" in result_text:
                    match = re.search(r"added_interests:([^;]+)", result_text)
                    if match:
                        items = match.group(1).strip()
                        natural_responses.append(f"PROFILE_UPDATED: added_interests={items}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=interests")

                elif "removed_interests:" in result_text:
                    match = re.search(r"removed_interests:([^;]+)", result_text)
                    if match:
                        items = match.group(1).strip()
                        natural_responses.append(f"PROFILE_UPDATED: removed_interests={items}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: removed=interests")

                elif "cleared_and_added_interests:" in result_text:
                    match = re.search(r"cleared_and_added_interests:([^;]+)", result_text)
                    if match:
                        items = match.group(1).strip()
                        natural_responses.append(f"PROFILE_UPDATED: cleared_and_added_interests={items}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=interests")
                
                elif "TASK_ACCEPTED" in result_text:
                    # Принятие делегированной задачи уже обработано выше
                    pass

                elif "changed_city:" in result_text:
                    match = re.search(r"changed_city:([^->]+)->([^;]+)", result_text)
                    if match:
                        old_city = match.group(1).strip()
                        new_city = match.group(2).strip()
                        natural_responses.append(f"PROFILE_UPDATED: city={old_city}->{new_city}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=city")

                elif "changed_company:" in result_text:
                    match = re.search(r"changed_company:([^->]+)->([^;]+)", result_text)
                    if match:
                        new_company = match.group(2).strip()
                        natural_responses.append(f"PROFILE_UPDATED: company={new_company}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=company")

                elif "added_skills:" in result_text:
                    match = re.search(r"added_skills:([^;]+)", result_text)
                    if match:
                        items = match.group(1).strip()
                        natural_responses.append(f"PROFILE_UPDATED: added_skills={items}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=skills")

                elif "added_goals:" in result_text:
                    match = re.search(r"added_goals:([^;]+)", result_text)
                    if match:
                        items = match.group(1).strip()
                        natural_responses.append(f"PROFILE_UPDATED: added_goals={items}")
                    else:
                        natural_responses.append("PROFILE_UPDATED: type=goals")

                else:
                    natural_responses.append("Профиль обновлен")

            elif "Все задачи удалены" in result_text:
                natural_responses.append("Все задачи удалены")

            elif "TASK_DELETED_ASK_REASON:" in result_text:
                # AI должен спросить о причине удаления - НЕ добавляем в natural_responses,
                # это инструкция для AI, а не сообщение пользователю
                pass
            
            elif "TASK_COMPLETED_ASK_RESULT:" in result_text:
                # AI должен спросить о результате выполнения - НЕ добавляем в natural_responses
                pass
            
            elif "TASK_UPDATED:" in result_text:
                # AI должен прокомментировать изменение задачи
                natural_responses.append("Задача обновлена")

            elif "Задача удалена" in result_text or "Задача.*удалена" in result_text:
                natural_responses.append("Задача удалена")

            elif "Задача обновлена" in result_text:
                natural_responses.append("Задача обновлена")

            elif "Статус подписки:" in result_text:
                natural_responses.append(result_text)

            elif "Платеж создан" in result_text:
                natural_responses.append("Платеж создан, следуйте инструкциям для оплаты")

            elif "TASK_TIME_UPDATED:" in result_text:
                natural_responses.append(result_text)

            elif "TIME_PARSE_FAILED:" in result_text:
                natural_responses.append(result_text)

            elif "NO_ACTIVE_TASKS:" in result_text:
                natural_responses.append(result_text)

            elif "USER_NOT_FOUND:" in result_text:
                natural_responses.append(result_text)

            elif "TASK_COMPLETED:" in result_text:
                natural_responses.append(result_text)

            elif "TASK_DELEGATED:" in result_text:
                natural_responses.append(result_text)

            elif "COMPLETION_ERROR:" in result_text:
                natural_responses.append(result_text)

            elif "DELEGATION_ERROR:" in result_text:
                natural_responses.append(result_text)

            elif "TIME_UPDATE_ERROR:" in result_text:
                natural_responses.append(result_text)

            elif "TASK_DELEGATED_SUCCESS:" in result_text:
                natural_responses.append(result_text)

            elif "DELEGATION_REPORT:" in result_text:
                natural_responses.append(result_text)

            elif "DELEGATION_SUBSCRIPTION_REQUIRED:" in result_text:
                natural_responses.append(result_text)

            elif "SELF_DELEGATION_ERROR:" in result_text:
                natural_responses.append(result_text)

            elif "Идеи сгенерированы" in result_text or "мозговой штурм" in result_text.lower():
                natural_responses.append(result_text)

            elif "Задачи с инсайтами:" in result_text:
                natural_responses.append(result_text)

            elif "🥉 Делегирование задач доступно только на тарифах" in result_text:
                natural_responses.append("DELEGATION_BLOCKED_LIGHT: Делегирование недоступно на Light")

            elif "Задача.*делегирована" in result_text or "делегирована" in result_text:
                natural_responses.append("TASK_DELEGATED: Задача успешно делегирована")

            elif "NEED_TIME_FOR_TASK:" in result_text:
                # AI должен спросить о времени для задачи - передаем контекст для естественного ответа
                if ":" in result_text:
                    task_title = result_text.split(":", 1)[1].strip()
                    natural_responses.append(f"НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: {task_title}")
                else:
                    natural_responses.append("НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: неизвестная задача")

            else:
                # Для неизвестных результатов передаем как есть
                natural_responses.append(result_text)

        # УПРОЩЕННАЯ ОБРАБОТКА: Формируем финальный контент на основе результатов
        if natural_responses:
            # Формируем ДВА контента:
            # 1. ai_context - для передачи AI (с маркерами и структурой)
            # 2. fallback_message - для пользователя если AI не ответит (читаемый текст)
            
            ai_context = " | ".join(natural_responses)  # Для AI - со всеми маркерами
            fallback_message = "Действие выполнено"  # Fallback для пользователя
            
            # Специальная обработка для обновления профиля
            if any("PROFILE_UPDATED" in r for r in natural_responses):
                profile_responses = [r for r in natural_responses if "PROFILE_UPDATED" in r]
                details = []
                for pr in profile_responses:
                    if "added_interests=" in pr:
                        items = pr.split("=", 1)[1]
                        details.append(f"добавлены интересы {items}")
                    elif "removed_interests=" in pr:
                        items = pr.split("=", 1)[1]
                        details.append(f"удалены интересы {items}")
                    elif "cleared_and_added_interests=" in pr:
                        items = pr.split("=", 1)[1]
                        details.append(f"оставил только интересы {items}")
                    elif "city=" in pr:
                        city_info = pr.split("=", 1)[1]
                        details.append(f"город {city_info}")
                    elif "company=" in pr:
                        company = pr.split("=", 1)[1]
                        details.append(f"компания {company}")
                    elif "added_skills=" in pr:
                        items = pr.split("=", 1)[1]
                        details.append(f"добавлены навыки {items}")
                    elif "added_goals=" in pr:
                        items = pr.split("=", 1)[1]
                        details.append(f"добавлены цели {items}")
                    else:
                        details.append("профиль обновлен")
                if details:
                    ai_context = f"Профиль обновлён — {', '.join(details)}."
                    fallback_message = f"Профиль обновлён — {', '.join(details)}."
                else:
                    ai_context = "Профиль обновлен."
                    fallback_message = "Профиль обновлен."
            elif any("НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ:" in r for r in natural_responses):
                # Специальная обработка для запроса времени
                time_responses = [r for r in natural_responses if "НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ:" in r]
                for tr in time_responses:
                    if ":" in tr:
                        task_title = tr.split(":", 1)[1].strip()
                        ai_context = tr  # Передаем маркер AI для естественной генерации
                        fallback_message = f"Во сколько поставить задачу '{task_title}'?"  # Fallback на случай если AI не сгенерирует ответ
                    else:
                        ai_context = "НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: неизвестная задача"
                        fallback_message = "Во сколько поставить задачу?"
            elif any("TASK_ACCEPTED" in r for r in natural_responses):
                # Обработка принятия делегированной задачи
                task_accepted_responses = [r for r in natural_responses if "TASK_ACCEPTED" in r]
                for tr in task_accepted_responses:
                    if ":" in tr:
                        task_title = tr.split(":", 1)[1].strip()
                        ai_context = f"TASK_ACCEPTED: {task_title}"
                        fallback_message = f"Задача '{task_title}' принята в работу"
                    else:
                        ai_context = "TASK_ACCEPTED"
                        fallback_message = "Задача принята в работу"
            elif any("DELEGATION_BLOCKED_LIGHT:" in r for r in natural_responses):
                ai_context = "Пользователь с тарифом Light попытался делегировать задачу. Объясни, что делегирование доступно только на Standard/Premium, расскажи о преимуществах этих тарифов, покажи ссылку https://asibiont.ru/subscription_tiers и предложи обновить подписку."
                fallback_message = "Делегирование недоступно на вашем тарифе Light. Обновите до Standard или Premium для доступа к этой функции."
                # Обработка создания задачи
                task_created_responses = [r for r in natural_responses if "TASK_CREATED:" in r]
                for tr in task_created_responses:
                    if ":" in tr and "|" in tr:
                        # Парсим: "TASK_CREATED: Заказать продукты | scheduled_at: 25.01.2026 00:03"
                        parts = tr.split(":", 1)[1].strip()  # "Заказать продукты | scheduled_at: 25.01.2026 00:03"
                        if "|" in parts:
                            task_title = parts.split("|")[0].strip()  # "Заказать продукты"
                            time_info = parts.split("scheduled_at:", 1)[1].strip() if "scheduled_at:" in parts else ""
                            ai_context = tr  # Оставляем полный контекст для AI
                            fallback_message = f"Задача '{task_title}' запланирована на {time_info}"
                        else:
                            task_title = parts.strip()
                            ai_context = tr
                            fallback_message = f"Задача '{task_title}' создана"
                    else:
                        ai_context = tr
                        fallback_message = "Новая задача создана"
            elif any("НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ:" in r for r in natural_responses):
                # Обработка запроса времени для задачи
                time_request_responses = [r for r in natural_responses if "НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ:" in r]
                for tr in time_request_responses:
                    if ":" in tr:
                        task_title = tr.split(":", 1)[1].strip()
                        ai_context = f"НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: {task_title}"
                        fallback_message = f"Во сколько тебе удобно выполнить '{task_title}'?"
                    else:
                        ai_context = "НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: неизвестная задача"
                        fallback_message = "Во сколько тебе удобно выполнить эту задачу?"
            elif has_list_tasks and list_tasks_result:
                # Специальная обработка для списка задач - возвращаем результат напрямую
                ai_context = list_tasks_result
                fallback_message = list_tasks_result
            # Для других случаев ai_context уже установлен выше

            # Добавляем контекст профиля для list_tasks
            profile_context = ""
            if has_list_tasks and list_tasks_result:
                try:
                    db_session_local = Session()
                    prof = db_session_local.query(UserProfile).filter_by(user_id=user_id).first()
                    if prof:
                        profile_data = []
                        if prof.city: profile_data.append(f"город: {prof.city}")
                        if prof.company: profile_data.append(f"компания: {prof.company}")
                        if prof.position: profile_data.append(f"должность: {prof.position}")
                        if prof.interests: profile_data.append(f"интересы: {prof.interests}")
                        if prof.skills: profile_data.append(f"навыки: {prof.skills}")
                        if prof.goals: profile_data.append(f"цели: {prof.goals}")
                        if prof.current_plans: profile_data.append(f"планы: {prof.current_plans}")
                        if profile_data:
                            profile_context = f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {', '.join(profile_data)}"
                    db_session_local.close()
                except Exception as e:
                    logger.warning(f"Failed to get profile context: {e}")

            # ФОРМИРУЕМ КОНТЕКСТ ДЛЯ AI: результаты + профиль + инструкции
            tool_context_msg = f"""РЕЗУЛЬТАТЫ ВЫПОЛНЕННЫХ ДЕЙСТВИЙ:
{ai_context}{profile_context}

ИНСТРУКЦИЯ ДЛЯ ФИНАЛЬНОГО ОТВЕТА:
Сгенерируй ЕСТЕСТВЕННЫЙ, ПЕРСОНАЛИЗИРОВАННЫЙ ответ, который интегрирует результат действия в живой диалог:

1. СТИЛЬ И ТОН:
   - Общайся как умный друг, НЕ как робот-помощник
   - Избегай формальности: "Отлично!", "Замечательно!", "Конечно!"
   - Разнообразь структуру ответов, не используй шаблоны
   - Подстройся под настроение пользователя

2. СОДЕРЖАНИЕ (ОБЯЗАТЕЛЬНО):
   а) ИНТЕГРАЦИЯ РЕЗУЛЬТАТА:
      - НЕ дублируй технический ответ функции
      - Естественно встрой результат действия в разговор
      - Пример: "Поставил напоминание на 14:30" вместо "Добавлена задача с напоминанием на..."
   
   б) КОНТЕКСТНЫЕ СОВЕТЫ (2-3 конкретных):
      - Учти текущее время суток и ситуацию
      - Дай практические рекомендации по выполнению
      - Если задача на завтра - комментируй планирование
      - Если задача через 5 минут - дай быстрые советы
   
   в) СПЕЦИАЛЬНЫЕ СИТУАЦИИ:
      - Если результат содержит "НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ: [название]", спроси у пользователя удобное время естественным образом
      - Пример: вместо "На какое время поставить задачу?", скажи "Во сколько тебе удобно забрать сына с баскетбола?" или "Когда планируешь забрать ребенка?"
      - Адаптируй вопрос под контекст задачи и стиль общения

3. ДЛИНА И СТРУКТУРА:
   - 3-5 предложений для создания/изменения задач
   - 2-3 предложения для простых действий
   - Избегай списков и нумерации
   - Используй эмоджи умеренно (1-2 на ответ)

4. ПРИМЕРЫ КАЧЕСТВЕННЫХ ОТВЕТОВ:
   ❌ ПЛОХО: "Отлично! Поставил напоминание проверить почту на 17:57. Рекомендую открыть почтовый клиент заранее."
   ✅ ХОРОШО: "Напомню проверить почту в 17:57 — через 5 минут. Учитывая поздний час, стоит быстро пробежаться по важным письмам, отложив детальные ответы на утро. Если ожидаешь что-то срочное, можно сразу настроить фильтры для приоритетных отправителей."

ВАЖНО: Создавай УНИКАЛЬНЫЙ ответ для каждой ситуации, НЕ используй одинаковые фразы!"""

            logger.info(f"[AI CONTEXT] ai_context={ai_context[:200]}")
            logger.info(f"[AI CONTEXT] fallback_message={fallback_message[:200]}")
            logger.info(f"[AI CONTEXT] tool_context_msg={tool_context_msg[:300]}")

            # Добавляем контекст в messages
            messages = [{"role": "system", "content": system_prompt}]
            messages.append({"role": "user", "content": original_message})
            messages.append({"role": "user", "content": tool_context_msg})

            # Запрашиваем естественный ответ от AI
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.8,  # Повышена для более креативных ответов
                "max_tokens": 2000   # Увеличено для развернутых персонализированных ответов
            }

            # Используем fallback_message в качестве значения по умолчанию
            final_content = fallback_message
            logger.info(f"[AI REQUEST] Requesting AI natural response with {len(messages)} messages")
            logger.info(f"[AI REQUEST] Requesting AI natural response with {len(messages)} messages")
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    async with aiohttp.ClientSession() as ai_session:
                        async with ai_session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=40)) as ai_response:
                            if ai_response.status == 200:
                                ai_result = await ai_response.json()
                                final_content = ai_result["choices"][0]["message"]["content"].strip()
                                logger.info(f"[AI NATURAL RESPONSE] Generated: {final_content[:200]}")
                                break
                            else:
                                error_body = await ai_response.text()
                                logger.warning(f"[AI NATURAL RESPONSE] Status {ai_response.status}, attempt {attempt+1}/{max_retries}, error: {error_body[:200]}")
                except Exception as e:
                    logger.error(f"[AI NATURAL RESPONSE] Error on attempt {attempt+1}/{max_retries}: {e}", exc_info=True)
                    if attempt == max_retries - 1:
                        # Fallback уже установлен выше (fallback_message)
                        logger.warning(f"[AI NATURAL RESPONSE] All attempts failed, using fallback: {final_content}")

        else:
            # Нет результатов tool calls - обычная обработка
            logger.info("[AI RESPONSE] No natural_responses, skipping AI natural response generation")
            final_content = None

    logger.info(f"[CHAT_WITH_AI] Returning final_content: {final_content[:200] if final_content else 'None'}")
    return final_content


async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None, message_type=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    logger = logging.getLogger(__name__)
    logger.info(f"[CHAT_WITH_AI] Called with user_id={user_id}")

    if user_id is None:
        logger.error(f"[CHAT_WITH_AI] ERROR: user_id is None! This will cause issues with tool calls")
    
    if user_id is None:
        logger.error(f"[CHAT_WITH_AI] ERROR: user_id is None! This will cause issues with tool calls")

    # Ensure context is a list or None
    if context is not None and not isinstance(context, list):
        logger.warning(f"context is not a list: {type(context)}, setting to None")
        context = None

    # Use provided db_session or create new one if not provided
    if db_session is None:
        from models import Session
        db_session = Session()
        close_session = True
    else:
        close_session = False

    # Получаем пользователя и его состояние
    user = None
    if user_id:
        from models import User
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            # Создаем пользователя если не существует
            user = User(telegram_id=user_id, conversation_state='normal', timezone='Europe/Moscow')
            db_session.add(user)
            db_session.commit()
            logger.info(f"Created new user {user_id}")

    # Управление состоянием разговора
    conversation_state = user.conversation_state if user else 'normal'
    pending_task_data = None
    if user and user.pending_task_data:
        try:
            import json
            pending_task_data = json.loads(user.pending_task_data)
        except:
            pending_task_data = None

    # Обновляем время последнего взаимодействия
    if user:
        user.last_interaction_at = datetime.now(timezone.utc)
        db_session.commit()

    # Управление контекстом разговора
    conversation_context = []
    if user and user.conversation_context:
        try:
            import json
            conversation_context = json.loads(user.conversation_context)
        except:
            conversation_context = []

    # ВСЕГДА получаем актуальный список задач перед обработкой
    # Это гарантирует, что AI видит свежие данные после операций через веб-интерфейс
    current_tasks = list_tasks(user_id=user_id, session=db_session, include_completed=False)
    
    # Для proactive режима фильтруем только предстоящие задачи
    if message_type == 'proactive':
        # Получаем timezone пользователя
        user_tz = pytz.UTC
        if user and user.timezone:
            try:
                user_tz = pytz.timezone(user.timezone)
            except:
                user_tz = pytz.UTC
        
        # Получаем текущее время пользователя
        base_now = datetime.now(pytz.UTC)
        user_now = base_now.astimezone(user_tz) if user_tz != pytz.UTC else base_now
        
        # Фильтруем только предстоящие задачи
        upcoming_tasks = []
        if current_tasks and "РЈ РІР°СЃ" in current_tasks:  # Проверяем, что есть задачи
            # Парсим текст задач (это грубый парсинг, но работает для текущего формата)
            lines = current_tasks.split('\n')
            for line in lines:
                if '•' in line and ('на' in line.lower() or 'завтра' in line.lower() or 'через' in line.lower()):
                    # Это задача с временем - проверяем, не просрочена ли
                    if '[ПРОСРОЧЕНА]' not in line:
                        upcoming_tasks.append(line.strip())
                elif '•' in line and '[ПРОСРОЧЕНА]' not in line and 'на' not in line.lower():
                    # Задача без времени считается предстоящей
                    upcoming_tasks.append(line.strip())
        
        if upcoming_tasks:
            filtered_tasks = "РџСЂРµРґСЃС‚РѕСЏС‰РёРµ Р·Р°РґР°С‡Рё:\n" + '\n'.join(upcoming_tasks[:5])
        else:
            filtered_tasks = "РќРµС‚ РїСЂРµРґСЃС‚РѕСЏС‰РёС… Р·Р°РґР°С‡. РћС‚Р»РёС‡РЅРѕРµ РІСЂРµРјСЏ РґР»СЏ РїР»Р°РЅРёСЂРѕРІР°РЅРёСЏ!"
        
        fresh_tasks_info = f"\n[РђРљРўРЈРђР›Р¬РќР«Р• Р—РђР”РђР§Р РќРђ РњРћРњР•РќРў Р—РђРџР РћРЎРђ]\n{filtered_tasks}\n"
    else:
        fresh_tasks_info = f"\n[РђРљРўРЈРђР›Р¬РќР«Р• Р—РђР”РђР§Р РќРђ РњРћРњР•РќРў Р—РђРџР РћРЎРђ]\n{current_tasks}\n"
    
    # Очищаем упоминания задач из старого контекста, чтобы AI не ссылался на выполненные задачи
    # Оставляем только последние 3 сообщения для сохранения контекста разговора
    if len(conversation_context) > 3:
        conversation_context = conversation_context[-3:]

    # Добавляем текущее сообщение в контекст
    conversation_context.append({
        'role': 'user',
        'content': message,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

    # Ограничиваем контекст последними 10 сообщениями
    if len(conversation_context) > 10:
        conversation_context = conversation_context[-10:]

    # Сохраняем обновленный контекст
    if user:
        try:
            import json
            user.conversation_context = json.dumps(conversation_context)
            db_session.commit()
        except Exception as e:
            logger.warning(f"Failed to save conversation context: {e}")

    # Проверяем сообщение о времени и обновляем timezone
    time_message_match = re.search(r"мое\s+местное\s+время:\s*(\d{1,2}:\d{2})", message.lower())
    if time_message_match:
        user_time_str = time_message_match.group(1)
        detected_timezone = determine_timezone_from_time(user_time_str, user_id)
        if detected_timezone:
            logger.info(f"Detected timezone {detected_timezone} from time {user_time_str}")
            update_profile(timezone=detected_timezone, user_id=user_id, session=db_session)

    # Сохраняем оригинальное сообщение ДО очистки
    original_message = message

    # Extract mentions before cleaning message
    mentions = re.findall(r"@[\w]+", message)
    mentions_str = ", ".join(mentions) if mentions else "нет"
    # Clean message from mentions for processing
    clean_message = re.sub(r"@[\w]+", "", message).strip()

    # ОБРАБОТКА СОСТОЯНИЙ РАЗГОВОРА
    if conversation_state == 'waiting_for_task_time' and pending_task_data:
        # Проверяем, похоже ли сообщение на указание времени
        time_patterns = [
            r'\d{1,2}:\d{2}',  # 10:00, 15:30
            r'\d{1,2}\s*(час|минут)',  # через 2 часа, 30 минут
            r'(завтра|сегодня|послезавтра)',
            r'(утр|вечер|ноч|обед|дн)',
            r'через',
        ]
        
        looks_like_time = any(re.search(pattern, clean_message.lower()) for pattern in time_patterns)
        
        if not looks_like_time:
            # Сообщение НЕ похоже на время - сбрасываем состояние и обрабатываем как обычное
            logger.info(f"[STATE] Message doesn't look like time, resetting state: {clean_message}")
            user.conversation_state = 'normal'
            user.pending_task_data = None
            db_session.commit()
            # Продолжаем обычную обработку (не возвращаем здесь)
        else:
            # Пользователь отвечает на вопрос о времени для задачи
            logger.info(f"[STATE] Processing time response for pending task: {pending_task_data}")
            
            # Парсим время из сообщения
            from ai_integration.utils import parse_relative_time, parse_natural_time
            current_time = datetime.now(timezone.utc)
            if user and user.timezone:
                try:
                    user_tz = pytz.timezone(user.timezone)
                    current_time = current_time.astimezone(user_tz)
                except:
                    pass
            
            # Сначала пробуем распознать абсолютное время (завтра в 10 утра)
            parsed_time = parse_natural_time(clean_message, current_time)
            if not parsed_time:
                # Если не получилось, пробуем относительное время (через 2 часа)
                parsed_time = parse_relative_time(clean_message, current_time)
            
            if parsed_time:
                # Создаем задачу с распознанным временем
                try:
                    task_data = pending_task_data
                    result = add_task(
                        title=task_data.get('title', 'Задача'),
                        description=task_data.get('description', ''),
                        reminder_time=parsed_time.strftime('%Y-%m-%d %H:%M'),
                        user_id=user_id,
                        session=db_session
                    )
                    
                    # Сбрасываем состояние
                    user.conversation_state = 'normal'
                    user.pending_task_data = None
                    db_session.commit()
                    
                    # Добавляем ответ AI в контекст
                    conversation_context.append({
                        'role': 'assistant',
                        'content': result,
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    })
                    user.conversation_context = json.dumps(conversation_context)
                    db_session.commit()
                    
                    return result
                except Exception as e:
                    logger.error(f"Failed to create task from pending data: {e}")
                    user.conversation_state = 'normal'
                    user.pending_task_data = None
                    db_session.commit()
                    return "Извините, не удалось создать задачу. Попробуйте еще раз."
            else:
                # Время не распознано, просим уточнить
                return "Не удалось распознать время. Попробуйте сказать 'завтра в 10 утра' или 'через 2 часа'."

    context_len = (
        len(context) if context and not isinstance(context, int) else (context if isinstance(context, int) else 0)
    )
    logger.info(
        f"chat_with_ai called with message: {clean_message[:50]}..., mentions: {mentions_str}, context len: {context_len}, user_id: {user_id}, file: {file_content is not None}")
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")

    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ключ DeepSeek не настроен. Обратитесь к администратору для настройки."

    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        user = None
        profile = None
        session = None
        subscription_tier = None
        # Initialize time variables with defaults
        base_now = datetime.now(pytz.UTC)
        user_now = base_now
        # Формат времени С ТАЙМЗОНОЙ для промпта: "15:43 (UTC)"
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
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
        current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
        user_username = "user"

        if user_id:
            user = db_session.query(User).filter_by(telegram_id=user_id).first()

            # Создать пользователя если не существует
            if not user:
                user = User(telegram_id=user_id)
                db_session.add(user)
                db_session.commit()
                
            # Получаем профиль пользователя
            profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
            logger.info(f"[PROFILE] User {user_id} profile loaded: {'Yes' if profile else 'No'}")
            
            # Устанавливаем имя пользователя
            if user.username:
                user_username = user.username
            elif user.first_name:
                user_username = user.first_name
            else:
                user_username = "user"
                
            # Get user current time FIRST before using it (moved BEFORE subscription check)
            base_now = datetime.now(pytz.UTC)
            logger.info(f"[TIME CHECK] Real UTC now: {base_now}")
            logger.info(f"[TIME CHECK] Formatted: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            user_now = base_now  # Default to base_now
            # Формат времени С ТАЙМЗОНОЙ для промпта
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            user_tz = pytz.UTC  # Default
            if user:
                tz_str = user.timezone if user.timezone else "UTC"
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    # Формат времени С ТАЙМЗОНОЙ для промпта: "15:43 (Europe/Moscow)"
                    current_time_str = f"{user_now.strftime('%H:%M')} ({tz_str})"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Error setting user timezone: {e}")
                    user_tz = pytz.UTC
                    user_now = base_now
                    # Формат времени С ТАЙМЗОНОЙ для промпта
                    current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            
            # Получаем subscription_tier
            subscription_tier = user.subscription_tier.value if user and hasattr(user, 'subscription_tier') and user.subscription_tier else None
            logger.info(f"[SUBSCRIPTION] User {user_id} tier from DB: {user.subscription_tier if user else 'None'}, value: {subscription_tier}")

            # Check subscription
            from config import FREE_ACCESS_MODE
            logger.info(f"[SUBSCRIPTION] FREE_ACCESS_MODE = {FREE_ACCESS_MODE}")

            if not FREE_ACCESS_MODE:
                subscription = db_session.query(Subscription).filter_by(user_id=user.id, status="active").first()
                if not subscription:
                    db_session.close()
                    # Генерируем сообщение о подписке через AI
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "У пользователя нет активной подписки. Сообщи об этом и предложи активировать подписку в @asibiont_bot."}]
                        data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.7, "max_tokens": 80}
                        async with aiohttp.ClientSession() as sess:
                            async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    result = await resp.json()
                                    return result["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                    return "Для использования требуется активная подписка 💳 Активируйте её в @asibiont_bot"

            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""  # If decryption fails, skip

            # Добавляем информацию о времени суток для персонализации
            current_hour = user_now.hour
            if 6 <= current_hour < 12:
                time_context = "УТРО: Предлагай планирование дня, утренние задачи, зарядку"
            elif 12 <= current_hour < 18:
                time_context = "ДЕНЬ: Предлагай текущие задачи, встречи, активную работу"
            elif 18 <= current_hour < 22:
                time_context = "ВЕЧЕР: Предлагай подведение итогов дня, отдых, планирование завтра"
            else:
                time_context = "НОЧЬ: Предлагай отдых, подготовку ко сну, легкие задачи"
            
            user_memory += f"\nВРЕМЯ СУТОК: {time_context}"
            profile_filled = False
            
            # Helper function to check if field is empty
            def is_empty_field(value):
                return not value or (isinstance(value, str) and not value.strip())
            
            if profile:
                profile_info = []
                if not is_empty_field(profile.city):
                    profile_info.append(f"Город: {profile.city}")
                if not is_empty_field(profile.company):
                    profile_info.append(f"Компания: {profile.company}")
                if not is_empty_field(profile.position):
                    profile_info.append(f"Должность: {profile.position}")
                if hasattr(profile, 'languages') and not is_empty_field(profile.languages):
                    profile_info.append(f"Языки: {profile.languages}")
                if not is_empty_field(profile.skills):
                    profile_info.append(f"Навыки: {profile.skills}")
                if not is_empty_field(profile.interests):
                    profile_info.append(f"Интересы: {profile.interests}")
                if not is_empty_field(profile.goals):
                    profile_info.append(f"Цели: {profile.goals}")

                # Определяем незаполненные поля
                empty_fields = []
                if is_empty_field(profile.city):
                    empty_fields.append("город")
                if is_empty_field(profile.company):
                    empty_fields.append("компания")
                if is_empty_field(profile.position):
                    empty_fields.append("должность")
                if is_empty_field(profile.skills):
                    empty_fields.append("навыки")
                if is_empty_field(profile.interests):
                    empty_fields.append("интересы")
                if is_empty_field(profile.goals):
                    empty_fields.append("цели")
                if not (hasattr(profile, 'languages') and not is_empty_field(profile.languages)):
                    empty_fields.append("языки")

                # СТРУКТУРИРОВАННАЯ ИНФОРМАЦИЯ О ПРОФИЛЕ ДЛЯ AI
                if profile_info:
                    user_memory += f"\n\n📋 ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:"
                    for info in profile_info:
                        user_memory += f"\n• {info}"
                    user_memory += f"\n\n🎯 ИСПОЛЬЗУЙ ЭТУ ИНФОРМАЦИЮ ДЛЯ ПЕРСОНАЛИЗАЦИИ: адаптируй советы под навыки, интересы и профессиональную сферу пользователя. Каждый ответ должен учитывать профиль!"
                    logger.info(f"[PROFILE DEBUG] Profile info added to prompt: {profile_info}")

                # Проактивное заполнение при незаполненных полях
                if empty_fields and len(empty_fields) > 0:
                    logger.info(f"[PROFILE DEBUG] Empty fields detected: {empty_fields}")
                    # Выбираем только 1-2 наиболее важных незаполненных поля для естественного вопроса
                    priority_fields = []
                    if is_empty_field(profile.city):
                        priority_fields.append("город")
                    if is_empty_field(profile.interests):
                        priority_fields.append("интересы")
                    if is_empty_field(profile.company):
                        priority_fields.append("компания")
                    if not profile.skills:
                        priority_fields.append("навыки")
                    
                    if priority_fields:
                        # Спрашиваем только об одном поле за раз, естественно в контексте
                        field_to_ask = priority_fields[0]  # Берем первое по приоритету
                        user_memory += f"\n💡 ЕСЛИ ПОДХОДИТ КОНТЕКСТ: можешь ненавязчиво спросить о {field_to_ask}е для персонализации советов (только если разговор естественным образом к этому ведет)"
                    else:
                        # Если основные поля заполнены, не спрашиваем вообще
                        pass

                profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                logger.info(f"[PROFILE DEBUG] Profile filled status: {profile_filled}, fields count: {len(profile_info)}")

                # ДЕМОНСТРАЦИЯ ВОЗМОЖНОСТЕЙ: каждые 5-7 взаимодействий
                interaction_count = getattr(profile, 'interaction_count', 0) or 0
                if interaction_count > 0 and interaction_count % 6 == 0:  # Каждые 6 взаимодействий
                    user_memory += "\n🚀 ДЕМОНСТРИРУЙ ВОЗМОЖНОСТИ: Расскажи о полезных функциях - поиске контактов, делегировании задач, анализе прогресса. Сделай это естественно в контексте ответа!"

                # Если профиль совсем пустой - мягко предложи заполнить в первом сообщении
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\n👋 ПЕРВОЕ ЗНАКОМСТВО: Если пользователь здоровается, можешь ненавязчиво предложить рассказать о себе (город, интересы) для персонализации, но только если разговор естественно к этому ведет"
            else:
                user_memory += "\n❌ ПРОФИЛЬ НЕ ЗАПОЛНЕН: начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели). Это критически важно для персонализации!"
                logger.info("[PROFILE DEBUG] No profile found, will request profile filling")

            # ЗАГРУЖАЕМ ПОЛНЫЙ СПИСОК ЗАДАЧ ДЛЯ ПРЕДОТВРАЩЕНИЯ ВЫДУМЫВАНИЯ
            # Агент НЕ ДОЛЖЕН выдумывать задачи - только использовать реальные данные из БД
            # Используем свежие данные, полученные в начале функции
            logger.info(f"[TASKS DEBUG] Using fresh tasks: {fresh_tasks_info[:100] if fresh_tasks_info else 'None'}...")
            if fresh_tasks_info and "У вас" in fresh_tasks_info:
                user_memory += f"\n\n📝 АКТИВНЫЕ ЗАДАЧИ:\n{fresh_tasks_info}\n\n⚠️  ВАЖНО: НЕ выдумывай задачи! Используй ТОЛЬКО те задачи которые указаны выше. Если говоришь о задаче, ОБЯЗАТЕЛЬНО проверь что она есть в списке."
            else:
                user_memory += "\n\n📝 ЗАДАЧИ: У пользователя нет активных задач."
                # ДОБАВЛЯЕМ ПРЕДЛОЖЕНИЯ НА ОСНОВЕ ПРОФИЛЯ
                if profile and profile_filled:
                    suggestions = []
                    if profile.skills:
                        suggestions.append(f"учитывая навыки в {profile.skills}")
                    if profile.interests:
                        suggestions.append(f"связанные с интересами в {profile.interests}")
                    if profile.goals:
                        suggestions.append(f"для достижения целей в {profile.goals}")
                    if profile.company or profile.position:
                        job_info = []
                        if profile.company: job_info.append(profile.company)
                        if profile.position: job_info.append(profile.position)
                        suggestions.append(f"по работе в {' '.join(job_info)}")
                    
                    if suggestions:
                        user_memory += f"\n💡 ПРЕДЛОЖЕНИЯ: Можешь предложить задачи {', '.join(suggestions[:2])}"

            # Add delegated tasks info
            if user.username:
                delegated_tasks = (
                    db_session.query(Task)
                    .filter(Task.delegated_to_username.ilike(user.username.replace('@', '')), Task.delegation_status == "pending")
                    .all()
                )
                if delegated_tasks:
                    delegated_info = [
                        f"Задача '{t.title}' от @{creator.username if (creator := db_session.query(User).filter_by(id=t.user_id).first()) else 'unknown'}"
                        for t in delegated_tasks[:3]
                    ]
                    user_memory += f"\nДелегированные задачи для принятия: {', '.join(delegated_info)}"

            # Add info about tasks delegated BY user
            my_delegated_tasks = (
                db_session.query(Task)
                .filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.delegation_status.in_(["pending", "accepted"]),
                )
                .all()
            )
            if my_delegated_tasks:
                my_delegated_info = [
                    f"Задача '{t.title}' поручена @{t.delegated_to_username} (статус: {t.delegation_status})"
                    for t in my_delegated_tasks[:3]
                ]
                user_memory += f"\nЗадачи поручённые другим: {', '.join(my_delegated_info)}"

            # Add partners/contacts info with common interests/skills/tasks
            try:
                # user_id here is telegram_id, need to get database user.id
                memory_user = db_session.query(User).filter_by(telegram_id=user_id).first()
                partners = get_partners_list(user_id=memory_user.id if memory_user else None, session=db_session) if memory_user else []
                
                # Get favorite contacts
                favorite_contacts_info = []
                if memory_user:
                    user_profile = db_session.query(UserProfile).filter_by(user_id=memory_user.id).first()
                    if user_profile and user_profile.favorite_contacts:
                        try:
                            import json
                            favorite_data = json.loads(user_profile.favorite_contacts)
                            for item in favorite_data:
                                if isinstance(item, str):
                                    fav_user = db_session.query(User).filter(
                                        User.username == item.replace('@', '')
                                    ).first()
                                    if fav_user and fav_user.username:
                                        favorite_contacts_info.append(f"@{fav_user.username} (избранный)")
                        except Exception as e:
                            logger.error(f"Error parsing favorite_contacts: {e}")
                
                if partners or favorite_contacts_info:
                    # partners - это список объектов UserProfile
                    partners_info = []
                    
                    # Add favorite contacts first
                    partners_info.extend(favorite_contacts_info)
                    
                    # Add recommended partners
                    for p in partners[:5]:
                        partner_user = db_session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            # Собираем информацию об общем
                            common_details = []
                            if hasattr(p, 'common_interests') and p.common_interests:
                                common_details.append(f"интересы: {p.common_interests}")
                            if hasattr(p, 'common_skills') and p.common_skills:
                                common_details.append(f"навыки: {p.common_skills}")
                            if hasattr(p, 'common_goals') and p.common_goals:
                                common_details.append(f"цели: {p.common_goals}")
                            if hasattr(p, 'common_tasks') and p.common_tasks:
                                common_details.append(f"задачи: {p.common_tasks}")
                            
                            if common_details:
                                partners_info.append(f"@{partner_user.username} (общее: {'; '.join(common_details)})")
                            else:
                                partners_info.append(f"@{partner_user.username}")
                    
                    if partners_info:
                        user_memory += f"\n\n🤝 ДОСТУПНЫЕ КОНТАКТЫ: {', '.join(partners_info)}"
                        user_memory += f"\n💡 ПРЕДЛАГАЙ СВЯЗАТЬСЯ: Если пользователь ищет партнеров или хочет пообщаться, активно предлагай эти контакты! УПОМИНАЙ НИКИ КАК @username В ТЕКСТЕ."
            except Exception as e:
                logger.error(f"Error getting partners: {e}")

            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars

            # Add feed posts for context
            try:
                from models import Post
                # Get user's profile with favorites
                user_profile = db_session.query(UserProfile).filter_by(user_id=memory_user.id if memory_user else None).first()
                
                # Parse favorite contacts from JSON
                favorite_user_ids = []
                if user_profile and user_profile.favorite_contacts:
                    try:
                        import json
                        favorite_data = json.loads(user_profile.favorite_contacts)
                        for item in favorite_data:
                            if isinstance(item, int):
                                favorite_user_ids.append(item)
                            elif isinstance(item, str):
                                fav_user = db_session.query(User).filter(
                                    User.username == item.replace('@', '')
                                ).first()
                                if fav_user:
                                    favorite_user_ids.append(fav_user.id)
                    except Exception as e:
                        logger.error(f"Error parsing favorite_contacts: {e}")
                
                # Include own posts too
                all_user_ids = favorite_user_ids + [memory_user.id] if memory_user else []
                
                # Get recent posts (last 10)
                if all_user_ids:
                    posts = db_session.query(Post).filter(
                        Post.user_id.in_(all_user_ids)
                    ).order_by(Post.created_at.desc()).limit(10).all()
                    
                    if posts:
                        posts_info = []
                        for post in posts:
                            post_user = db_session.query(User).filter_by(id=post.user_id).first()
                            if post_user:
                                username = post_user.username or post_user.first_name or 'пользователь'
                                posts_info.append(f"@{username}: {post.content[:100]}")
                        
                        if posts_info:
                            user_memory += f"\n\nПОСЛЕДНИЕ ПОСТЫ В ЛЕНТЕ (для контекста):\n" + "\n".join(posts_info[:5])
            except Exception as e:
                logger.error(f"Error getting feed posts for context: {e}")

            # Обработка pending_action
            if user and user.pending_action:
                try:
                    pending_data = json.loads(user.pending_action)
                    action_type = pending_data.get("type")

                    # Проверка на таймаут (24 часа)
                    timestamp = pending_data.get("timestamp")
                    if timestamp:
                        created_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) - created_at > timedelta(hours=24):
                            logger.info(f"Pending action timed out for user {user_id}, clearing")
                            user.pending_action = None
                            db_session.commit()
                            # Продолжить с обычной обработкой
                            pass
                        else:
                            # Продолжить обработку pending_action
                            pass

                    if action_type == "result_check_response":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # Сохранить ответ пользователя как completion_notes
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            task.completion_notes = original_message  # Сохраняем полный ответ пользователя
                            db_session.commit()
                        # Очистить pending_action
                        user.pending_action = None
                        db_session.commit()
                        # Вернуть специальный ответ для обработки результата
                        return f"Спасибо за информацию о задаче '{task_title}'! Результат сохранён для анализа."

                    elif action_type == "task_skip_confirmation":
                        task_id = pending_data.get("task_id")
                        task_title = pending_data.get("task_title")
                        # Обработать ответ пользователя о пропуске задачи
                        task = db_session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
                        if task:
                            if "да" in original_message.lower() or "пропустить" in original_message.lower():
                                skip_response = f"Задача '{task_title}' отмечена как пропущенная. Могу предложить альтернативы или создать новую задачу."
                                return skip_response
                            else:
                                keep_response = f"Хорошо, оставляем задачу '{task_title}' активной. Чем могу помочь?"
                                return keep_response
                        user.pending_action = None
                        db_session.commit()
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"Error processing pending_action: {e}")
                    user.pending_action = None
                    db_session.commit()

        db_session.close()

        # БАЗОВАЯ INTENT CLASSIFICATION для антигаллюцинационной системы
        # Нужна для корректной работы валидации tool calls
        message_lower = original_message.lower()
        intent = {"type": "conversation", "confidence": 0.5, "params": {}}
        
        # Определяем intent для основных команд
        # Сначала проверяем массовые операции
        if any(kw in message_lower for kw in ['все задачи', 'всех задач', 'все мои задачи']):
            if any(kw in message_lower for kw in ['удали', 'убери', 'очисти', 'закрой', 'удалить']):
                intent = {"type": "delete_all_tasks", "confidence": 0.95, "params": {}}
            elif any(kw in message_lower for kw in ['готово', 'завершил', 'выполнил', 'сделал']):
                intent = {"type": "complete_all_tasks", "confidence": 0.95, "params": {}}
            else:
                intent = {"type": "list_tasks", "confidence": 0.9, "params": {}}
        elif any(kw in message_lower for kw in ['покажи', 'список', 'какие задачи', 'мои задачи', 'что у меня']):
            intent = {"type": "list_tasks", "confidence": 0.9, "params": {}}
        elif any(kw in message_lower for kw in ['готово', 'сделал', 'выполнил', 'завершил', 'задача выполнена', 'закончил']):
            intent = {"type": "complete_task", "confidence": 0.9, "params": {}}
        elif any(kw in message_lower for kw in ['удали', 'убери', 'удалить задачу']):
            intent = {"type": "delete_task", "confidence": 0.9, "params": {}}
        elif any(kw in message_lower for kw in ['перенеси', 'измени', 'обнови', 'перенести на']):
            intent = {"type": "edit_task", "confidence": 0.9, "params": {}}
        elif any(kw in message_lower for kw in ['напомни', 'создай задачу', 'добавь задачу', 'нужно', 'надо']):
            intent = {"type": "add_task", "confidence": 0.9, "params": {}}
        
        logger.info(f"[INTENT] Detected: {intent['type']} (confidence: {intent['confidence']})")

        # AI-FIRST APPROACH: Полный контроль за AI с базовым intent для валидации
        logger.info("[INTENT] AI-first approach with basic intent classification for validation")

        # ГЛУБОКИЙ АНАЛИЗ КОНТЕКСТА ДЛЯ ПЕРСОНАЛИЗИРОВАННЫХ СОВЕТОВ
        # context_analysis = analyze_user_context_for_advice(user_id, db_session)
        # if "error" not in context_analysis:
        #     # Большой блок анализа отключен - теперь используется пост-обработка в utils.py
        #     pass

        # Construct system prompt with replaced placeholders
        # Расширяем system prompt для работы с относительным временем
        user_username = f"@{user.username}" if user and user.username else "@unknown"

        # Извлекаем последние 2 ответа агента для предотвращения повторов
        last_responses = []
        if context and isinstance(context, list):
            for item in context[-3:]:  # Последние 3 сообщения
                if "agent" in item and item["agent"] and isinstance(item["agent"], str):
                    # Берём первые 40 символов
                    response_text = item["agent"][:40].strip()
                    if response_text and response_text not in last_responses:
                        last_responses.append(response_text)

        # Ограничиваем до 2 последних
        last_responses = last_responses[-2:]

        # СПЕЦИАЛЬНАЯ ОБРАБОТКА СИСТЕМНЫХ СООБЩЕНИЙ (результаты действий)
        is_system_message = (
            original_message.startswith(('TASK_', 'DUPLICATE_TASK:', 'NEED_TIME_FOR_TASK:')) and
            'ASK_' not in original_message  # Исключаем сообщения, которые требуют вопросов
        )

        # Определяем тип сообщения для промпта
        message_type_for_prompt = message_type or ('system' if is_system_message else None)

        # ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ДЛЯ СИСТЕМНЫХ СООБЩЕНИЙ
        task_details_context = ""
        if is_system_message and user_id:
            try:
                # Извлекаем информацию о задаче из сообщения
                if "TASK_COMPLETED" in original_message and "ASK_" not in original_message:
                    # Ищем завершенные задачи пользователя за последний час
                    one_hour_ago = datetime.now(pytz.UTC) - timedelta(hours=1)
                    recent_completed = db_session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.status == "completed",
                        Task.actual_completion_time >= one_hour_ago
                    ).order_by(Task.actual_completion_time.desc()).first()

                    if recent_completed:
                        details = []
                        if recent_completed.completion_notes:
                            try:
                                decrypted_notes = decrypt_data(recent_completed.completion_notes)
                                if decrypted_notes and len(decrypted_notes.strip()) > 0:
                                    # Берем первые 50 символов заметки
                                    short_note = decrypted_notes.strip()[:50]
                                    if len(decrypted_notes) > 50:
                                        short_note += "..."
                                    details.append(f"результат: {short_note}")
                            except:
                                pass

                        if recent_completed.actual_completion_time and recent_completed.created_at:
                            completion_duration = recent_completed.actual_completion_time - recent_completed.created_at.replace(tzinfo=pytz.UTC)
                            hours = completion_duration.total_seconds() / 3600
                            if hours < 1:
                                minutes = int(completion_duration.total_seconds() / 60)
                                details.append(f"выполнена за {minutes} мин")
                            else:
                                details.append(f"выполнена за {hours:.1f} ч")

                        if details:
                            task_details_context = f"\nДЕТАЛИ ЗАДАЧИ: {', '.join(details)}"

                elif "TASK_DELETED" in original_message and "ASK_" not in original_message:
                    # Ищем недавно удаленные задачи (со статусом deleted или просто удаленные)
                    # Проверяем skipped_reason в последних взаимодействиях
                    recent_deleted = db_session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.status == "deleted"
                    ).order_by(Task.created_at.desc()).first()

                    if recent_deleted and recent_deleted.skipped_reason:
                        task_details_context = f"\nПРИЧИНА УДАЛЕНИЯ: {recent_deleted.skipped_reason}"

            except Exception as e:
                logger.warning(f"[SYSTEM MESSAGE] Could not extract task details: {e}")

        system_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            subscription_tier=subscription_tier,
            message_type=message_type_for_prompt)
        logger.info("[PROMPTS] Using extended prompt system")

        # СПЕЦИАЛЬНАЯ ОБРАБОТКА СИСТЕМНЫХ СООБЩЕНИЙ (результаты действий)
        is_system_message = original_message.startswith(('TASK_', 'DUPLICATE_TASK:', 'NEED_TIME_FOR_TASK:')) and 'ASK_' not in original_message and 'ASK_' not in original_message

        messages = [{"role": "system", "content": system_prompt}]
        
        # КРИТИЧНО: Для команд создания/изменения задач НЕ используем контекст
        # Это предотвращает путаницу когда AI пытается выполнить все команды из истории
        is_task_command = intent.get('type') in ['add_task', 'complete_task', 'delete_task', 'edit_task', 'delete_all_tasks', 'complete_all_tasks']
        
        # Используем conversation_context для истории разговора, но ТОЛЬКО для разговорных команд
        if conversation_context and isinstance(conversation_context, list) and not is_task_command:
            # Берем последние 4 сообщения для контекста (2 пары вопрос-ответ) - УМЕНЬШЕНО для предотвращения галлюцинаций
            recent_context = conversation_context[-4:] if len(conversation_context) > 4 else conversation_context
            for item in recent_context:
                if item.get("role") == "user":
                    messages.append({"role": "user", "content": item["content"]})
                elif item.get("role") == "assistant":
                    messages.append({"role": "assistant", "content": item["content"]})
            logger.info(f"[CONTEXT] Added {len(recent_context)} context messages for conversation")
        elif is_task_command:
            logger.info(f"[CONTEXT] SKIPPED context for task command: {intent.get('type')}")
        
        # Добавляем текущее сообщение
        messages.append({"role": "user", "content": message})

        # Используем intent classification вместо hardcoded проверок
        is_advice_question = intent.get('type') in [
            'conversation',
            'unknown'] and any(
            word in clean_message.lower() for word in [
                "что делать",
                "как",
                "совет",
                "помоги",
                "что посоветуешь",
                "как быть",
                "что предпринять",
                "какие шаги",
                "что делать с",
                "как решить",
                "не знаю с чего начать",
                "с чего начать",
                "как начать",
                "что делать дальше",
                "что делать если",
                "как лучше",
                "что посоветуешь",
                "какой совет",
                "нужен совет",
                "посоветуй",
                "как поступить",
                "что делать в ситуации",
                "как оптимизировать",
                "как улучшить",
                "как подготовиться",
                "как начать",
                "с чего начать",
                "как эффективно",
                "что можно сделать",
                "как решить проблему"])

        # Определяем, является ли сообщение запросом на управление задачами на основе intent
        is_task_request = intent.get('type') in [
            'add_task', 'complete_task', 'list_tasks', 'edit_task', 'delete_task',
            'delegate_task', 'find_partners', 'update_profile', 'profile_info'
        ]

        # МИНИМАЛЬНАЯ ЛОГИКА: Полный AI-first подход с умными подсказками
        if is_system_message:
            # Системные сообщения - без tools
            tool_choice = "none"
            parallel_tool_calls = False
            logger.info(f"[TOOL CHOICE] NONE for system message")
        else:
            # AI сам решает, но мы логируем намерение для отладки
            tool_choice = "auto"
            parallel_tool_calls = True
            
            # Детектируем тип команды для логирования
            if "удали" in clean_message.lower() or "убери" in clean_message.lower():
                logger.info(f"[TOOL HINT] DELETE detected: {clean_message[:50]}")
            elif any(kw in clean_message.lower() for kw in ["напомни", "через", "нужно", "надо"]):
                logger.info(f"[TOOL HINT] CREATE detected: {clean_message[:50]}")
            
            logger.info(f"[TOOL CHOICE] AUTO for: {clean_message[:50]}")

        # УПРОЩЕННЫЙ АНАЛИЗ СООБЩЕНИЯ ДЛЯ ПАРАМЕТРОВ AI
        message_lower = clean_message.lower()
        has_questions = '?' in clean_message
        has_technical_terms = any(term in message_lower for term in [
            'api', 'база данных', 'алгоритм', 'код', 'программирование', 'sql', 'python',
            'анализ', 'отчет', 'презентация', 'проект', 'задача', 'план'
        ])
        is_creative_request = any(term in message_lower for term in [
            'идеи', 'креатив', 'варианты', 'предложения', 'мозговой штурм'
        ])

        # Упрощенная логика температуры на основе типа сообщения
        if is_creative_request:
            # Для креативных запросов нужна высокая вариативность
            temperature = 0.9
            top_p = 0.95
        elif has_technical_terms:
            # Технические темы требуют точности
            temperature = 0.4
            top_p = 0.9
        else:
            # По умолчанию средняя вариативность
            temperature = 0.7
            top_p = 1.0

        logger.info(f"Using temperature {temperature}, top_p {top_p} for message analysis: questions={has_questions}, technical={has_technical_terms}, creative={is_creative_request}")

        # ИНТЕЛЛЕКТУАЛЬНОЕ КЭШИРОВАНИЕ: только для определенных типов запросов
        # Не кэшируем conversational запросы, поиск партнеров и запросы требующие актуальности
        should_cache = intent.get('type') not in [
            'conversation', 'unknown', 'greeting', 'find_partners', 'profile_info', 'edit_task', 'delete_task'
        ] and not is_advice_question  # Вопросы совета тоже не кэшируем

        if should_cache:
            # КЭШИРОВАНИЕ ОТВЕТОВ ДЛЯ СНИЖЕНИЯ НАГРУЗКИ НА API
            # Создаем ключ кэша на основе основных параметров
            # Добавляем динамические параметры для лучшей персонализации
            tasks_count = 0
            if user:
                try:
                    tasks_count = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
                except:
                    tasks_count = 0

            cache_key_components = [
                str(user_id or "anonymous"),
                clean_message[:200],  # Ограничиваем длину сообщения
                intent.get('type'),
                str(temperature),
                str(top_p),
                tool_choice,
                current_time_str,  # Время влияет на ответы
                subscription_tier or "free",
                str(tasks_count),  # Количество активных задач
                current_date_str,  # Дата для уникальности по дням
            ]
            cache_key = "|".join(cache_key_components)
            logger.info(f"Cache key generated: {cache_key[:100]}...")

            # Проверяем кэш перед отправкой запроса к API
            cached_response = cache.get_by_key(cache_key)
            if cached_response:
                logger.info("Cache hit! Returning cached response")
                # Обновляем счетчик взаимодействий
                if user:
                    profile = db_session.query(User).filter_by(id=user_id).first()
                    if profile:
                        profile.interaction_count = (profile.interaction_count or 0) + 1
                        db_session.commit()
                return cached_response
        else:
            logger.info(f"Skipping cache for intent_type '{intent.get('type')}' (requires freshness)")
            cache_key = None  # Для сохранения в кэш позже

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА TIME_ONLY: выполняем edit_task напрямую без AI
        # УБРАНА - теперь все через AI с tool calls
        # if intent.get("params", {}).get("time_only"):
        #     logger.info("[TIME_ONLY] Direct execution without AI call")
        #     # Find the most relevant pending task to update based on message content
        #     from models import Session as TempSession
        #     temp_session = TempSession()
        #     try:
        #         user_obj = temp_session.query(User).filter_by(telegram_id=user_id).first()
        #         if user_obj:
        #             # Get all pending tasks
        #             pending_tasks = temp_session.query(Task).filter_by(
        #                 user_id=user_obj.id, 
        #                 status="pending"
        #             ).order_by(Task.created_at.desc()).all()
        #             
        #             if pending_tasks:
        #                 # Try to find task by keywords in message
        #                 target_task = None
        #                 message_lower = original_message.lower()
        #                 
        #                 # Extract keywords from message (remove time-related words)
        #                 keywords = re.sub(r'\d{1,2}:\d{2}|завтра|сегодня|через|перенеси|напомни|минут|час|время', '', message_lower)
        #                 keywords = re.sub(r'\s+', ' ', keywords).strip()
        #                 
        #                 # Find task with highest keyword match
        #                 best_match_score = 0
        #                 for task in pending_tasks:
        #                     task_title_lower = task.title.lower()
        #                     score = 0
        #                     
        #                     # Check if keywords appear in task title
        #                     for keyword in keywords.split():
        #                         if len(keyword) > 2:  # Skip short words
        #                             if keyword in task_title_lower:
        #                                 score += 1
        #                     
        #                     # Bonus for exact phrase match
        #                     if keywords and keywords in task_title_lower:
        #                         score += 5
        #                     
        #                     if score > best_match_score:
        #                         best_match_score = score
        #                         target_task = task
        #                 
        #                 # If no good keyword match, use most recent task
        #                 if not target_task or best_match_score == 0:
        #                     target_task = pending_tasks[0]
        #                     logger.info(f"[TIME_ONLY] No keyword match, using most recent task: {target_task.title}")
        #                 else:
        #                     logger.info(f"[TIME_ONLY] Found task by keywords (score {best_match_score}): {target_task.title}")
        #                 
        #                 # Parse time from message
        #                 time_match = re.search(r'(\d{1,2}):(\d{2})', original_message)
        #                 if time_match:
        #                     hours, minutes = time_match.groups()
        #                     # Get user timezone
        #                     user_tz = pytz.timezone(user_obj.timezone) if user_obj.timezone else pytz.UTC
        #                     # Assume tomorrow if "завтра" in message, otherwise today
        #                     base_date = datetime.now(user_tz)
        #                     if 'завтра' in original_message.lower():
        #                         base_date += timedelta(days=1)
        #                     
        #                     # Set time in user's timezone, then convert to UTC
        #                     reminder_time = base_date.replace(hour=int(hours), minute=int(minutes), second=0, microsecond=0)
        #                     if reminder_time.tzinfo is None:
        #                         reminder_time = user_tz.localize(reminder_time)
        #                     reminder_time = reminder_time.astimezone(pytz.UTC)
        #                     
        #                     result = handlers.edit_task(
        #                         task_id=target_task.id,
        #                         title=None,
        #                         description=None,
        #                         reminder_time=reminder_time.isoformat(),
        #                         user_id=user_id,
        #                         session=db_session,
        #                     )
        #                     logger.info(f"[TIME_ONLY] Task updated: {result}")
        #                     # Вместо статического ответа передаем маркер для AI
        #                     return f"TASK_TIME_UPDATED: Задача '{target_task.title}' перенесена на {reminder_time.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')}."
        #                 else:
        #                     # Try relative time parsing
        #                     from ai_integration.utils import parse_relative_time
        #                     user_tz = pytz.timezone(user_obj.timezone) if user_obj.timezone else pytz.UTC
        #                     current_time = datetime.now(user_tz)
        #                     relative_time = parse_relative_time(original_message, current_time)
        #                     if relative_time:
        #                         reminder_time = relative_time.astimezone(pytz.UTC)
        #                         result = handlers.edit_task(
        #                             task_id=target_task.id,
        #                             title=None,
        #                             description=None,
        #                             reminder_time=reminder_time.isoformat(),
        #                             user_id=user_id,
        #                             session=db_session,
        #                         )
        #                         logger.info(f"[TIME_ONLY] Task updated with relative time: {result}")
        #                         return f"TASK_TIME_UPDATED: Задача '{target_task.title}' перенесена на {reminder_time.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')}."
        #                     else:
        #                         return "TIME_PARSE_FAILED: Не удалось распознать время в сообщении."
        #             else:
        #                 return "NO_ACTIVE_TASKS: Нет активных задач для обновления времени."
        #         else:
        #             return "USER_NOT_FOUND: Пользователь не найден."
        #     except Exception as e:
        #         logger.error(f"Error in time_only direct execution: {e}")
        #         return f"TIME_UPDATE_ERROR: Ошибка обновления времени: {str(e)}"
        #     finally:
        #         temp_session.close()
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА COMPLETION: выполняем complete_task напрямую без AI
        # УБРАНА - теперь все через AI с tool calls
        # if intent.get("type") == "complete_task" and intent.get("params", {}).get("task_title"):
        #     logger.info("[COMPLETION] Direct execution without AI call")
        #     task_title = intent.get("params", {}).get("task_title")
        #     try:
        #         result = await complete_task(
        #             task_id=None,
        #             task_title=task_title,
        #             user_id=user_id,
        #             session=db_session,
        #         )
        #         logger.info(f"[COMPLETION] Task completed: {result}")
        #         # Вместо статического ответа передаем маркер для AI
        #         return f"TASK_COMPLETED: {result}"
        #     except Exception as e:
        #         logger.error(f"Error in completion direct execution: {e}")
        #         return f"COMPLETION_ERROR: Ошибка завершения задачи: {str(e)}"
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА DELEGATION: выполняем delegate_task напрямую без AI
        # УБРАНА - теперь все через AI с tool calls
        # if intent.get("type") == "delegate_task" and intent.get("params", {}).get("task_title") and intent.get("params", {}).get("delegate_to") and intent.get("params", {}).get("reminder_time"):
        #     logger.info("[DELEGATION] Direct execution without AI call")
        #     task_title = intent.get("params", {}).get("task_title")
        #     delegate_to = intent.get("params", {}).get("delegate_to")
        #     reminder_time = intent.get("params", {}).get("reminder_time")
        #     try:
        #         result = delegate_task(
        #             title=task_title,
        #             description="",
        #             reminder_time=reminder_time,
        #             delegated_to_username=delegate_to,
        #             user_id=user_id,
        #             session=db_session,
        #         )
        #         logger.info(f"[DELEGATION] Task delegated: {result}")
        #         # Вместо статического ответа передаем маркер для AI
        #         return f"TASK_DELEGATED: {result}"
        #     except Exception as e:
        #         logger.error(f"Error in delegation direct execution: {e}")
        #         return f"DELEGATION_ERROR: Ошибка делегирования задачи: {str(e)}"
        
        # Параметры запроса согласно документации DeepSeek API (расширенные настройки)
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 4096,  # Максимум токенов для ответа
            "frequency_penalty": 0.0,  # Не повторять одни и те же фразы
            "presence_penalty": 0.0,  # Стимулировать новые темы
            "stop": None,  # Нет стоп-слов
            "stream": False,  # Не используем streaming для надежности
            "logprobs": None,  # Не запрашиваем вероятности токенов
            "top_logprobs": None,  # Не запрашиваем top вероятности
            "metadata": None,  # Нет метаданных
            "safety_instructions": None,  # Безопасность обрабатывается на уровне промпта
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages, temp={temperature}, top_p={top_p}")
        # Retry loop с exponential backoff
        max_retries = 3  # Увеличиваем до 3 попыток
        message_response = {"content": ""}  # Initialize with default
        tool_calls = []  # Initialize tool_calls
        success_flag = False  # Флаг успешного выполнения
        success = False  # Флаг успешного выполнения
        
        for attempt in range(max_retries + 1):
            try:
                # Exponential backoff: 0, 2, 4, 8 секунд
                if attempt > 0:
                    backoff_time = 2 ** (attempt - 1)
                    logger.info(f"Waiting {backoff_time}s before retry {attempt}/{max_retries}")
                    await asyncio.sleep(backoff_time)
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        logger.info(f"DeepSeek API response status: {response.status} (attempt {attempt + 1}/{max_retries + 1})")
                        
                        # Обработка различных HTTP статусов согласно документации
                        if response.status == 200:
                            # Успешный ответ - обрабатываем
                            tool_calls = []
                            try:
                                result = await response.json()
                                if "choices" in result and result["choices"]:
                                    message_response = result["choices"][0]["message"]
                                    content = message_response.get("content", "")
                                    # Фильтровать сырые tool calls
                                    content = re.sub(r"<\|.*?\|>", "", content).strip()
                                    content = re.sub(
                                        r"<｜DSML｜function_calls>.*?</｜DSML｜function_calls>",
                                        "",
                                        content,
                                        flags=re.DOTALL,
                                    ).strip()
                                    # Удаляем JSON блоки с tool_calls если они попали в текст
                                    content = re.sub(
                                        r'```json\s*\{.*?"tool_calls".*?\}\s*```', "", content, flags=re.DOTALL
                                    ).strip()
                                    content = re.sub(r'\{.*?"tool_calls".*?\}', "", content, flags=re.DOTALL).strip()
                                    content = re.sub(
                                        r'\{.*?"name":\s*"".*?"arguments".*?\}', "", content, flags=re.DOTALL
                                    ).strip()
                                    # КРИТИЧЕСКИ ВАЖНО: Удаляем явные упоминания функций в тексте
                                    # AI не должен писать add_task(...) в своём ответе пользователю
                                    content = re.sub(
                                        r'(add_task|delete_task|complete_task|list_tasks|edit_task|delegate_task)\s*\([^)]*\)',
                                        '',
                                        content
                                    ).strip()

                                    # Проверяем tool_calls в API response
                                    tool_calls = message_response.get("tool_calls")
                                    
                                    # КРИТИЧЕСКОЕ ЛОГИРОВАНИЕ: Что отправил AI?
                                    logger.info(f"[AI RESPONSE] Content: {content[:200]}")
                                    logger.info(f"[AI RESPONSE] Tool calls: {tool_calls}")
                                    if tool_calls:
                                        for tc in tool_calls:
                                            func_name = tc.get('function', {}).get('name')
                                            logger.info(f"[AI RESPONSE] Calling tool: {func_name}")
                                else:
                                    logger.error(f"No choices in API response: {result}")
                                    content = "Извините, произошла ошибка при обработке запроса."
                                    tool_calls = []
                            except Exception as e:
                                logger.error(f"Error parsing API response: {e}")
                                if attempt < max_retries:
                                    logger.info(f"Retrying API call due to parse error (attempt {attempt + 1})")
                                    await asyncio.sleep(1)
                                    continue
                                content = "Извините, произошла ошибка при обработке ответа от ИИ. Попробуйте еще раз."

                            # Обработка tool calls и т.д.
                            tool_results = []  # Инициализируем заранее

                            # Проверяем, не написал ли AI JSON в текст вместо tool_calls
                            json_in_text = re.search(
                                r'\{.*?"name":\s*"(.*?)"\s*,\s*"arguments":\s*(\{.*?\})\s*\}', content, re.DOTALL)
                            if json_in_text and not tool_calls:
                                try:
                                    func_name = json_in_text.group(1)
                                    func_args = json.loads(json_in_text.group(2))
                                    tool_calls = [{
                                        'function': {
                                            'name': func_name,
                                            'arguments': json.dumps(func_args, ensure_ascii=False)
                                        }
                                    }]
                                    # Удаляем JSON из текста
                                    content = re.sub(
                                        r'\{.*?"name":\s*".*?"\s*,\s*"arguments":\s*\{.*?\}\s*\}',
                                        '',
                                        content,
                                        flags=re.DOTALL).strip()
                                except Exception as e:
                                    logger.warning(f"Could not parse JSON in content: {e}")
                                    pass

                            # ПЕРВИЧНАЯ ОБРАБОТКА TOOL CALLS
                            validation_failed = False
                            if tool_calls:
                                result = await process_tool_calls(tool_calls, intent, message, user_id, db_session, session, url, headers, system_prompt, user_now, current_time_str, original_message, mentions_str, is_advice_question, current_time=user_now)
                                if result:
                                    return result
                                elif result is None and tool_calls:
                                    # Если process_tool_calls вернул None при наличии tool_calls - значит валидация не прошла
                                    # Заменяем tool_calls на пустой список, чтобы сработала антигаллюцинация
                                    logger.error("[VALIDATION FAILED] Setting tool_calls to empty to trigger anti-hallucination")
                                    tool_calls = []
                                    validation_failed = True
                                # tool_calls были проигнорированы для вопроса совета, переходим к обычной обработке
                            
                            # КРИТИЧЕСКАЯ ПРОВЕРКА: AI НЕ ДОЛЖЕН ГАЛЛЮЦИНИРОВАТЬ ДЕЙСТВИЯ
                            # Если AI говорит о выполненном действии, но не вызвал tool - это ошибка
                            # ИЛИ если валидация tool calls не прошла
                            if (not tool_calls or validation_failed) and content:
                                content_lower = content.lower()
                                
                                # ДЕТЕКЦИЯ ЯВНЫХ УПОМИНАНИЙ ФУНКЦИЙ В ТЕКСТЕ (критическая галлюцинация!)
                                # AI не должен писать имена функций в своём ответе
                                function_mentions = re.findall(r'(add_task|delete_task|complete_task|list_tasks|edit_task|delegate_task)\s*\(', content)
                                if function_mentions:
                                    detected_action = function_mentions[0]
                                    logger.error(f"[FUNCTION HALLUCINATION] AI wrote function call '{detected_action}()' in text without executing it!")
                                    logger.error(f"[FUNCTION HALLUCINATION] Content: {content[:200]}")
                                else:
                                    # Детектируем галлюцинации для разных типов действий
                                    hallucination_patterns = {
                                        'add_task': ['добавил задачу', 'создал задачу', 'поставил напоминание',
                                                     'задача добавлена', 'задача создана', 'напоминание поставлено',
                                                     'напоминание установлено', 'запланировал', 'запланирована'],
                                        'delete_task': ['удалил задачу', 'задача удалена', 'убрал задачу',
                                                        'задача убрана', 'удалил напоминание', 'убрал напоминание'],
                                        'complete_task': ['задача выполнена', 'отметил выполненной', 'завершил задачу',
                                                          'задача завершена', 'отметил как выполненную'],
                                        'list_tasks': ['вот твои задачи', 'список задач', 'твои задачи',
                                                       'показываю задачи', 'у тебя задачи'],
                                        'edit_task': ['изменил задачу', 'обновил задачу', 'перенес на',
                                                      'задача изменена', 'задача обновлена', 'время изменено'],
                                        'delegate_task': ['делегировал задачу', 'поручил задачу', 'передал задачу',
                                                          'задача делегирована', 'задача поручена']
                                    }
                                    
                                    # Добавляем intent-based detection
                                    detected_action = None
                                    
                                    # Если валидация не прошла, используем intent как источник правды
                                    if validation_failed:
                                        intent_type = intent.get('type')
                                        if intent_type in ['add_task', 'delete_task', 'complete_task', 'list_tasks', 'edit_task', 'delegate_task']:
                                            detected_action = intent_type
                                            logger.error(f"[VALIDATION-BASED DETECTION] Validation failed for intent: {intent_type}")
                                    
                                    # Если intent не помог, проверяем текстовые паттерны
                                    if not detected_action:
                                        for action_type, phrases in hallucination_patterns.items():
                                            if any(phrase in content_lower for phrase in phrases):
                                                detected_action = action_type
                                                break
                                
                                if detected_action:
                                    logger.error(f"[HALLUCINATION DETECTED] AI claimed '{detected_action}' but no tool calls!")
                                    logger.error(f"[HALLUCINATION] Message: {clean_message[:100]}")
                                    logger.error(f"[HALLUCINATION] Response: {content[:200]}")
                                    
                                    # Формируем усиленный промпт в зависимости от типа действия
                                    action_instructions = {
                                        'add_task': 'вызови add_task() с правильным title и reminder_time',
                                        'delete_task': 'вызови delete_task() с правильным task_title',
                                        'complete_task': 'вызови complete_task() с правильным task_title',
                                        'list_tasks': 'вызови list_tasks()',
                                        'edit_task': 'вызови edit_task() с правильными параметрами',
                                        'delegate_task': 'вызови delegate_task() с правильными параметрами'
                                    }
                                    
                                    instruction = action_instructions.get(detected_action, 'вызови соответствующий tool')
                                    
                                    # Пересылаем запрос с усиленным промптом
                                    enhanced_message = f"""КРИТИЧЕСКИ ВАЖНО: Пользователь написал: "{original_message}"

ТЫ ОБЯЗАН ВЫЗВАТЬ СООТВЕТСТВУЮЩИЙ TOOL! ГАЛЛЮЦИНАЦИЯ ЗАПРЕЩЕНА!

Ты должен {instruction}

ОТВЕТЬ ТОЛЬКО ВЫЗОВОМ TOOL! НЕ ПИШИ ТЕКСТ БЕЗ TOOL CALL!"""
                                    
                                    retry_messages = [{"role": "system", "content": system_prompt}]
                                    retry_messages.append({"role": "user", "content": enhanced_message})
                                    
                                    retry_data = {
                                        "model": DEEPSEEK_MODEL,
                                        "messages": retry_messages,
                                        "tools": TOOLS,
                                        "tool_choice": "auto",
                                        "parallel_tool_calls": True,
                                        "temperature": 0.3,  # Снижаем температуру для точности
                                        "max_tokens": 4096
                                    }
                                    
                                    try:
                                        async with aiohttp.ClientSession() as retry_session:
                                            async with retry_session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=30)) as retry_response:
                                                if retry_response.status == 200:
                                                    retry_result = await retry_response.json()
                                                    retry_message = retry_result["choices"][0]["message"]
                                                    retry_tool_calls = retry_message.get("tool_calls")
                                                    
                                                    if retry_tool_calls:
                                                        logger.info(f"[HALLUCINATION FIX] Retry successful, got {len(retry_tool_calls)} tool calls")
                                                        result = await process_tool_calls(retry_tool_calls, intent, message, user_id, db_session, session, url, headers, system_prompt, user_now, current_time_str, original_message, mentions_str, is_advice_question, current_time=user_now)
                                                        if result:
                                                            return result
                                                    else:
                                                        logger.warning(f"[HALLUCINATION FIX] Retry failed - still no tool calls")
                                    except Exception as retry_e:
                                        logger.error(f"[HALLUCINATION FIX] Retry failed with error: {retry_e}")
                            
                            # Успех - выходим из retry loop
                            logger.info(f"[SUCCESS] API call successful, content length: {len(content) if content else 0}")
                            logger.info(f"[SUCCESS] Tool calls found: {len(tool_calls) if tool_calls else 0}")
                            
                            # ПРОВЕРКА: Если tool_choice был REQUIRED, но AI не вызвал tool - критическая ошибка
                            if tool_choice == "required" and not tool_calls:
                                logger.error(f"[VALIDATION FAILED] tool_choice=required but no tools called!")
                                logger.error(f"[VALIDATION] Intent: {intent.get('type')}, Message: {clean_message[:100]}")
                                logger.error(f"[VALIDATION] AI response: {content[:200]}")
                                
                                # Пытаемся понять какой tool должен был вызваться
                                fallback_tool = None
                                if any(word in message_lower for word in ['создай', 'добавь', 'напомни', 'запланируй']):
                                    content = "NEED_TIME_FOR_TASK: Когда напомнить? (завтра в 10:00, через час, сегодня в 15:00)"
                                elif any(word in message_lower for word in ['готово', 'сделал', 'выполнил', 'закончил', 'задача выполнена']):
                                    content = "Отлично! Какую именно задачу завершили? Уточните название."
                                elif any(word in message_lower for word in ['покажи', 'список', 'какие', 'мои задачи']):
                                    # Принудительно вызываем list_tasks
                                    tasks = list_tasks(user_id=user_id, session=db_session, include_completed=False)
                                    content = tasks
                                elif any(word in message_lower for word in ['удали', 'убери']):
                                    content = "Какую задачу удалить? Уточните название."
                                else:
                                    content = "Понял ваше намерение, но мне нужно больше информации. Уточните детали."
                            
                            # ПРОВЕРКА: Если tool_choice был REQUIRED, но AI вызвал неправильный tool
                            elif tool_choice == "required" and tool_calls:
                                expected_tools = {
                                    'add_task': ['add_task'],
                                    'complete_task': ['complete_task', 'list_tasks'],  # complete_task может вызывать list_tasks для обновления
                                    'edit_task': ['edit_task'],
                                    'delete_task': ['delete_task'],
                                    'delegate_task': ['delegate_task'],
                                    'update_profile': ['update_profile']
                                }
                                
                                intent_type = intent.get('type')
                                if intent_type in expected_tools:
                                    actual_tools = [tc.get('function', {}).get('name') for tc in tool_calls]
                                    expected = expected_tools[intent_type]
                                    
                                    # Проверяем, что хотя бы один ожидаемый tool вызван
                                    if not any(tool in expected for tool in actual_tools):
                                        logger.error(f"[VALIDATION FAILED] Intent '{intent_type}' expects tools {expected}, but got {actual_tools}")
                                        logger.error(f"[VALIDATION] Message: {clean_message[:100]}")
                                        
                                        # Fallback для неправильных tool calls
                                        if intent_type == 'complete_task':
                                            content = "Отлично! Какую именно задачу завершили? Уточните название."
                                        elif intent_type == 'edit_task':
                                            content = "Какую задачу изменить и на какое время?"
                                        elif intent_type == 'add_task':
                                            content = "NEED_TIME_FOR_TASK: Когда напомнить? (завтра в 10:00, через час, сегодня в 15:00)"
                                        elif intent_type == 'delegate_task':
                                            # Для делегирования - принудительно вызываем delegate_task
                                            logger.info(f"[FORCED DELEGATION] Intent was delegate_task, forcing delegate_task call")
                                            try:
                                                from .handlers import delegate_task
                                                result = delegate_task(
                                                    title=intent.get('params', {}).get('task_title', 'Задача'),
                                                    description="",
                                                    reminder_time=intent.get('params', {}).get('reminder_time'),
                                                    delegated_to_username=intent.get('params', {}).get('delegate_to'),
                                                    user_id=user_id,
                                                    session=db_session,
                                                )
                                                content = f"TASK_DELEGATED: {result}"
                                                logger.info(f"[FORCED DELEGATION] Successfully delegated task: {result}")
                                            except Exception as e:
                                                logger.error(f"[FORCED DELEGATION] Failed: {e}")
                                                content = f"DELEGATION_ERROR: Ошибка делегирования: {str(e)}"
                                        else:
                                            content = f"Понял намерение '{intent_type}', но нужно больше деталей."
                            
                            # Устанавливаем флаг успешного выполнения
                            success = True
                            break
                        
                        elif response.status == 429:
                            # Rate limit - обязательно retry
                            logger.warning(f"Rate limit (429), retry {attempt + 1}/{max_retries + 1}")
                            if attempt < max_retries:
                                continue
                            else:
                                content = "🤖 Извините, сейчас много запросов к ИИ. Подождите немного и попробуйте снова - обычно это занимает 10-20 секунд."
                                # Отправляем уведомление о rate limit
                                asyncio.create_task(send_error_notification_to_bot(f"Rate limit exceeded (429) for user {user_id}", user_id, f"Status: {response.status}", target_user_id=146333757))
                                break

                        elif response.status in [500, 502, 503, 504]:
                            # Server errors - retry
                            error_text = await response.text()
                            logger.error(f"Server error {response.status}: {error_text[:200]}")
                            if attempt < max_retries:
                                logger.info(f"Retrying due to server error ({response.status})")
                                continue
                            else:
                                content = "🔧 Сервер ИИ временно недоступен. Это бывает редко, но случается. Попробуйте через 1-2 минуты - обычно всё восстанавливается быстро."
                                # Отправляем уведомление о server error
                                asyncio.create_task(send_error_notification_to_bot(f"Server error ({response.status}) for user {user_id}", user_id, f"Error: {error_text[:200]}", target_user_id=146333757))
                                break

                        elif response.status == 400:
                            # Bad request - не retry, логируем для отладки
                            error_text = await response.text()
                            logger.error(f"Bad request (400): {error_text}")
                            logger.error(f"Request data: {json.dumps(data, ensure_ascii=False, indent=2)}")
                            content = "📝 Что-то пошло не так с запросом. Попробуйте переформулировать сообщение по-другому - иногда ИИ лучше понимает другие формулировки."
                            # Отправляем уведомление о bad request
                            asyncio.create_task(send_error_notification_to_bot(f"Bad request (400) for user {user_id}", user_id, f"Error: {error_text[:200]}", target_user_id=146333757))
                            break

                        elif response.status == 401:
                            # Unauthorized - критическая ошибка
                            logger.error("API key invalid or expired (401)")
                            content = "🔐 Проблема с доступом к ИИ. Администраторы уже уведомлены и работают над решением. Попробуйте позже."
                            # Отправляем уведомление о проблеме с API key
                            asyncio.create_task(send_error_notification_to_bot(f"API authorization error (401) for user {user_id}", user_id, "API key may be invalid or expired", target_user_id=146333757))
                            break

                        else:
                            # Другие ошибки
                            error_text = await response.text()
                            logger.error(f"Unexpected status {response.status}: {error_text[:200]}")
                            if attempt < max_retries:
                                continue
                            else:
                                content = "⚠️ Произошла неожиданная ошибка. Разработчики уже получили уведомление. Попробуйте отправить сообщение еще раз."
                                # Отправляем уведомление о неожиданной ошибке
                                asyncio.create_task(send_error_notification_to_bot(f"Unexpected API error ({response.status}) for user {user_id}", user_id, f"Error: {error_text[:200]}", target_user_id=146333757))
                                break
                                
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries:
                    continue
                else:
                    content = "🌐 Проблема с подключением к ИИ. Проверьте интернет-соединение и попробуйте еще раз через минуту."
                    # Отправляем уведомление о сетевой ошибке
                    asyncio.create_task(send_error_notification_to_bot(f"Network error for user {user_id}", user_id, f"Error: {str(e)}", target_user_id=146333757))
                    break
            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                if attempt < max_retries:
                    continue
                else:
                    content = "Извините, произошла неожиданная ошибка. Попробуйте еще раз."
                    break
                
        # ОБРАБОТКА РЕЗУЛЬТАТОВ ПОСЛЕ RETRY LOOP
        # Проверяем флаг успеха
        if 'success' not in locals() or not success:
            logger.warning("[RETRY FAILED] All retry attempts failed")
            if not content:
                content = "🤖 К сожалению, ИИ временно недоступен после нескольких попыток. Разработчики уведомлены. Попробуйте позже - обычно это решается в течение 5-10 минут."
                # Отправляем уведомление о полном отказе API
                asyncio.create_task(send_error_notification_to_bot(f"All API retry attempts failed for user {user_id}", user_id, "Multiple retry attempts exhausted", target_user_id=146333757))
            return content
            
        # Обработка успешного ответа
        # Обработка успешного ответа
        if not content or len(content.strip()) < 5:
            logger.warning("[FALLBACK] No valid content after retry loop")
            content = "Хорошо, понял. Продолжим работу!"
        
        # Получаем финальный контент от AI
        logger.info("[AI RESPONSE] Processing AI response for conversation")
        final_content = content
        final_content = replace_placeholders(final_content, user_now, current_time_str)

        # Пост-обработка
        final_content = post_process_response(final_content, user_id, db_session)

        # Финальная проверка
        if not final_content or len(final_content.strip()) < 5:
            logger.warning("[FINAL FALLBACK] Content empty after processing, using fallback")
            final_content = "Хорошо, понял. Продолжим работу!"

        # Сохраняем ответ AI в контекст разговора
        if user:
            conversation_context.append({
                'role': 'assistant',
                'content': final_content,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            # Ограничиваем контекст до последних 20 сообщений
            if len(conversation_context) > 20:
                conversation_context = conversation_context[-20:]
            try:
                user.conversation_context = json.dumps(conversation_context)
                db_session.commit()
            except Exception as e:
                logger.warning(f"Failed to save conversation context: {e}")

        # Сохраняем взаимодействие в таблицу Interaction для dashboard
        try:
            from main import save_context_to_db
            save_context_to_db(user_id, message, final_content)
            logger.info(f"Saved interaction to database: user={user_id}")
        except Exception as e:
            logger.warning(f"Failed to save interaction to database: {e}")

        return final_content

    except Exception as e:
        logger.error(f"Error in chat_with_ai: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")

        # Добавляем номер строки для отладки
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last_frame = tb[-1]
            logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")

        # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ ОБ ОШИБКЕ В БОТА
        try:
            error_details = f"{type(e).__name__}: {str(e)}"
            # Отправляем уведомление разработчику (как прежде)
            asyncio.create_task(send_error_notification_to_bot(str(e), user_id, error_details))
            # Отправляем уведомление пользователю 146333757
            asyncio.create_task(send_error_notification_to_bot(str(e), user_id, error_details, target_user_id=146333757))
        except Exception as notify_e:
            logger.error(f"Failed to send error notification: {notify_e}")

        # РЕАБИЛИТАЦИЯ: пытаемся дать полезный ответ вместо ошибки
        try:
            # Получаем базовую информацию о пользователе
            user_info = ""
            task_info = ""
            if user_id and db_session:
                user = db_session.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    user_info = f"@{user.username}" if user.username else "пользователь"

                    # Получаем актуальные задачи
                    try:
                        current_tasks = list_tasks(user_id=user_id, session=db_session, include_completed=False)
                        # Извлекаем количество задач из строки ответа
                        match = re.search(r'У вас (\d+)', current_tasks)
                        if match:
                            task_count = int(match.group(1))
                            if task_count > 0:
                                task_info = f" У тебя {task_count} активных задач."
                            else:
                                task_info = " У тебя сейчас нет активных задач."
                        else:
                            task_info = ""
                    except Exception as task_e:
                        logger.warning(f"Could not get tasks in error recovery: {task_e}")
                        task_info = ""

            # Формируем реабилитирующий ответ
            recovery_responses = [
                f"🤖 Извини, произошла техническая ошибка в работе ИИ. Команда разработчиков уже получила уведомление и работает над исправлением.{task_info} Давай продолжим работу — что планируешь сделать дальше?",
                f"🔧 К сожалению, возник временный сбой в системе ИИ. Разработчики уведомлены и уже занимаются решением.{task_info} Расскажи, чем могу помочь прямо сейчас?",
                f"⚠️ Произошла непредвиденная ошибка в работе ИИ. Мы зафиксировали проблему и передали в разработку.{task_info} Не останавливаемся — давай решим твои текущие задачи!",
                f"🚨 Технический сбой в ИИ, но я остаюсь на связи! Информация о проблеме передана команде разработчиков.{task_info} Что тебя интересует? Готов помочь с задачами!"
            ]

            import random
            recovery_message = random.choice(recovery_responses)

            # Сохраняем контекст ошибки для анализа
            if user and db_session:
                try:
                    error_context = f"Error: {str(e)[:200]} at {datetime.now(timezone.utc).isoformat()}"
                    if user.memory:
                        existing_memory = decrypt_data(user.memory)
                        updated_memory = f"{existing_memory}\n[SYSTEM_ERROR_RECOVERY] {error_context}"
                    else:
                        updated_memory = f"[SYSTEM_ERROR_RECOVERY] {error_context}"

                    user.memory = encrypt_data(updated_memory)
                    db_session.commit()
                except Exception as mem_e:
                    logger.warning(f"Could not save error context: {mem_e}")

            return recovery_message

        except Exception as recovery_e:
            logger.error(f"Error in recovery mechanism: {recovery_e}")
            return "Извини, произошла ошибка. Попробуй еще раз или свяжись с поддержкой."


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
        from datetime import datetime
        import pytz
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
• Начни с приветствия и напоминания о задаче
• Добавь мотивацию и практические советы
• Закончи вопросом для вовлечения
• НЕ пиши промежуточные мысли или "сейчас посмотрю задачи"

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
                    return f"Привет! ⏰ Напоминаю о задаче: {task_title}\n\nПора начинать! Как планируешь подойти к выполнению?"
    except Exception as e:
        logger.error(f"Error in generate_reminder: {e}", exc_info=True)
        # Более качественный fallback с контекстом
        return f"Привет! ⏰ Напоминаю о задаче: {task_title}\n\nВремя приступить к выполнению. Готов начать?"


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
        from datetime import datetime
        import pytz
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
                    return f"Отлично! Задача '{task_title}' выполнена! ✅"
    except Exception as e:
        logger.error(f"Error in generate_result_check: {e}")
        return f"Поздравляю с выполнением задачи '{task_title}'! 🎉"


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
        from models import Interaction

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

        # Извлекаем последние ответы агента для предотвращения повторов
        last_responses = []
        if context and isinstance(context, list):
            for item in context[-3:]:
                if "agent" in item:
                    response_text = item["agent"][:40].strip()
                    if response_text and response_text not in last_responses:
                        last_responses.append(response_text)
        last_responses = last_responses[-2:]

        system_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            subscription_tier=subscription_tier,
            message_type='proactive')
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
        proactive_prompts = {
            "no_tasks": """ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя НЕТ АКТИВНЫХ ЗАДАЧ.

СИТУАЦИЯ: Полное отсутствие задач - отличная возможность предложить создание новых.
ТОНАЛЬНОСТЬ: Мотивирующая, энергичная, с конкретными предложениями.
ЗАДАЧА: Предложи 2-3 конкретные идеи задач на основе профиля пользователя.
ФОРМАТ: 1-2 абзаца с вопросом в конце.
ПРИМЕРЫ ФОКУСА:
- Планирование дня/недели
- Развитие навыков из профиля  
- Networking с контактами
- Личные/профессиональные цели""",

            "few_tasks": f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя МАЛО ЗАДАЧ ({task_count}).

СИТУАЦИЯ: Оптимальная загруженность - можно предложить оптимизацию или дополнения.
ТОНАЛЬНОСТЬ: Поддерживающая, аналитическая.
ЗАДАЧА: Проанализируй текущие задачи и предложи улучшения или дополнения.
ФОРМАТ: 1-2 абзаца с практическими советами.
ПРИМЕРЫ ФОКУСА:
- Приоритизация существующих задач
- Добавление связанных подзадач
- Оптимизация расписания
- Предложение релевантных контактов""",

            "many_tasks": f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя МНОГО ЗАДАЧ ({task_count}).

СИТУАЦИЯ: Высокая загруженность - нужна помощь с организацией.
ТОНАЛЬНОСТЬ: Поддерживающая, не перегружающая дополнительными предложениями.
ЗАДАЧА: Предложи способы упрощения или делегирования.
ФОРМАТ: Короткий, практичный совет в 1 абзаце.
ПРИМЕРЫ ФОКУСА:
- Делегирование части задач
- Методы приоритизации  
- Оптимизация процессов
- Поддержка и мотивация""",

            "overdue_tasks": f"""ПРОАКТИВНОЕ СООБЩЕНИЕ: У пользователя ПРОСРОЧЕННЫЕ ЗАДАЧИ ({overdue_count}).

СИТУАЦИЯ: СРОЧНО - есть просроченные задачи, требуется немедленное внимание.
ТОНАЛЬНОСТЬ: Деликатная, но настойчивая. Без осуждения.
ЗАДАЧА: Мягко напомни о просроченных задачах и предложи план действий.
ФОРМАТ: 1 абзац с конкретным планом.
ПРИМЕРЫ ФОКУСА:
- Приоритизация просроченного
- Перепланирование дедлайнов
- Разбиение на подзадачи
- Поиск помощи/делегирование""",

            "general": """ПРОАКТИВНОЕ СООБЩЕНИЕ: Общий проактивный контакт.

СИТУАЦИЯ: Стандартное проактивное взаимодействие без специального контекста.
ТОНАЛЬНОСТЬ: Дружелюбная, полезная, конкретная.
ЗАДАЧА: Дать персонализированный совет по продуктивности на основе профиля пользователя.
ФОРМАТ: 1-2 абзаца с конкретными предложениями и вопросом в конце.

ОБЯЗАТЕЛЬНО ВКЛЮЧИ:
- Анализ профиля пользователя (навыки, интересы, цели)
- Конкретные предложения действий (не просто "посмотрю задачи")
- Вопрос для вовлечения пользователя

ПРИМЕРЫ ПОЛЕЗНЫХ СОВЕТОВ:
• "Учитывая твои навыки в [навык], предлагаю поработать над [конкретная задача]"
• "Для достижения цели [цель] рекомендую начать с [конкретное действие]"
• "В твоем городе [город] можно найти интересные возможности в [область]"
• "Пора обновить профиль - добавить [конкретное поле] поможет в [польза]"

НЕ ПИСАТЬ: "Я сейчас посмотрю твои задачи" или общие фразы без конкретных советов."""
        }

        # Выбираем подходящий промпт
        # Убеждаемся, что context - строка
        if isinstance(context, list):
            context = "general"  # Если context - список, используем general
        selected_prompt = proactive_prompts.get(context, proactive_prompts["general"])
        
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
        temperature = 0.7  # Для персонализированных советов
        top_p = 0.95

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 800  # Увеличиваем для 1-3 абзацев
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
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
        from datetime import datetime
        import pytz
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
        from datetime import datetime
        import pytz
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
                    return "Заметил просроченные задачи 📌"
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
        return "Задачи ждут внимания 📌"


def validate_response_compliance(content, msg_type):
    """Проверка соответствия ответа промту"""
    if not content:
        return False, ["Empty content"]
    
    content_lower = content.lower()
    word_count = len(content.split())
    issues = []
    
    # Общие правила
    if word_count > 100:  # Слишком длинный
        issues.append("Too long")
    if word_count < 5:  # Слишком короткий
        issues.append("Too short")
    if any(word in content_lower for word in ["здравствуйте", "спасибо за вопрос", "я помогу"]):  # Клише
        issues.append("Contains clichés")
    
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
