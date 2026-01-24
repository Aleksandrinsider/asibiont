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
from .memory import decrypt_data
from .utils import (
    determine_timezone_from_time, analyze_user_context_for_advice,
    replace_placeholders, clean_technical_details,
    post_process_tool_calls, smart_fallback_handler,
    post_process_response
)
from .prompts import get_extended_system_prompt
from .tools import TOOLS

logger = logging.getLogger(__name__)

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
accept_delegated_task = handlers.accept_delegated_task
reject_delegated_task = handlers.reject_delegated_task
list_tasks = handlers.list_tasks
find_partners = handlers.find_partners
update_profile = handlers.update_profile
update_user_memory = handlers.update_user_memory
delegate_task = handlers.delegate_task
delete_all_tasks = handlers.delete_all_tasks
delete_task = handlers.delete_task
edit_task = handlers.edit_task
check_subscription_status = handlers.check_subscription_status
create_subscription_payment = handlers.create_subscription_payment
cancel_subscription = handlers.cancel_subscription
brainstorm_ideas = handlers.brainstorm_ideas
enrich_task_list_with_insights = handlers.enrich_task_list_with_insights
get_partners_list = handlers.get_partners_list


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
            logger.info(f"[PROCESS_TOOL_CALLS] Tool call {i}: {tc}")
    else:
        logger.warning("[PROCESS_TOOL_CALLS] tool_calls is empty or None!")
    
    if user_id is None:
        logger.error(f"[PROCESS_TOOL_CALLS] ERROR: user_id is None! Cannot process tool calls without user_id")
        return None
        
    if not tool_calls:
        return None
        
    # ПОСТ-ПРОЦЕССИНГ: Корректируем tool calls на основе intent
    corrected_tool_calls = post_process_tool_calls(intent, tool_calls, message)
    if corrected_tool_calls:
        tool_calls = corrected_tool_calls

    # Убираем дубликаты tool calls по function name и arguments
    seen_calls = set()
    unique_tool_calls = []
    for call in tool_calls:
        call_key = (call.get("function", {}).get("name"), str(call.get("function", {}).get("arguments")))
        if call_key not in seen_calls:
            seen_calls.add(call_key)
            unique_tool_calls.append(call)
        else:
            logger.warning(f"[TOOL CALLS] Removed duplicate tool call: {call_key}")
    
    tool_calls = unique_tool_calls
    
    logger.info(f"[PROCESS_TOOL_CALLS] After deduplication: {len(tool_calls)} unique tool calls")
    if not tool_calls:
        logger.warning("[PROCESS_TOOL_CALLS] No tool calls to process after deduplication!")

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
                
                # ПРОВЕРКА СУЩЕСТВУЮЩИХ ЗАДАЧ: ищем похожие задачи
                existing_tasks = list_tasks(user_id=user_id, session=db_session)
                
                # Проверяем, есть ли уже похожая задача (по заголовку)
                is_duplicate = False
                if existing_tasks:
                    from difflib import SequenceMatcher
                    for line in existing_tasks.split('\n'):
                        # Извлекаем название задачи из строки (до даты/времени)
                        if '📌' in line or '✅' in line:
                            # Формат: "📌 Название задачи (DD.MM.YYYY HH:MM)"
                            parts = line.split('(')
                            if len(parts) > 0:
                                existing_title = parts[0].replace('📌', '').replace('✅', '').strip()
                                # Fuzzy match: проверяем схожесть названий (>0.87 = очень похожи)
                                similarity = SequenceMatcher(None, task_title.lower(), existing_title.lower()).ratio()
                                if similarity > 0.87 and ('В работе' in line or 'Ожидает' in line):
                                    logger.warning(f"[ADD TASK] DUPLICATE DETECTED - similar task (similarity={similarity:.2f}): '{existing_title}' vs '{task_title}'")
                                    tool_results.append({"function": func_name, "result": f"DUPLICATE_TASK: Похожая задача уже существует - '{existing_title}'"})
                                    is_duplicate = True
                                    break
                
                if is_duplicate:
                    continue
                
                # КРИТИЧЕСКАЯ ПРОВЕРКА: ищем КОНКРЕТНОЕ время в ОРИГИНАЛЬНОМ сообщении пользователя
                # Только точные форматы времени, неточные формулировки ("завтра с утра") НЕ считаются
                time_patterns = [
                    r'\d{1,2}:\d{2}',  # 10:00, 8:30
                    r'через\s+\d+\s+(минут[уы]?|час[аов]?|дне[йя]|секунд)',  # через 30 минут, через 2 часа
                    r'завтра\s+в\s+\d{1,2}',  # завтра в 10
                    r'сегодня\s+в\s+\d{1,2}',  # сегодня в 15
                    r'в\s+\d{1,2}\s+(час|утра|вечера|дня)',  # в 10 утра, в 15 часов
                    r'\d{1,2}\s+(утра|вечера|дня|ночи)',  # 10 утра, 15 дня
                ]
                
                has_explicit_time = False
                for pattern in time_patterns:
                    if re.search(pattern, original_message.lower()):
                        has_explicit_time = True
                        break
                
                # Если пользователь НЕ указал время в сообщении - устанавливаем состояние ожидания
                if not has_explicit_time:
                    logger.info(f"[ADD TASK] No time specified - setting waiting state for user {user_id}")
                    
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
                relative_time_result = parse_relative_time(original_message, current_time)
                if relative_time_result:
                    # Если нашли относительное время - ИСПОЛЬЗУЕМ его вместо AI расчета
                    reminder_time = relative_time_result.strftime("%Y-%m-%d %H:%M")
                    logger.info(f"[ADD TASK] Recalculated relative time: {reminder_time} (current_time: {current_time.strftime('%H:%M')})")
                
                if not reminder_time or '@unknown' in str(reminder_time):
                    reminder_time = intent.get("params", {}).get("reminder_time")
                
                # Валидация reminder_time
                has_time = intent.get("params", {}).get("has_time", False)
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
                result = update_profile(
                    city=args.get("city"),
                    company=args.get("company"),
                    position=args.get("position"),
                    interests=args.get("interests"),
                    skills=args.get("skills"),
                    goals=args.get("goals"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delegate_task":
                result = delegate_task(
                    title=args.get("title"),
                    delegated_to_username=args.get("delegated_to_username"),
                    reminder_time=args.get("reminder_time"),
                    user_id=user_id,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delete_all_tasks":
                result = delete_all_tasks(user_id=user_id, session=db_session)
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "delete_task":
                result = await delete_task(
                    task_id=args.get("task_id"),
                    task_title=args.get("task_title"),
                    user_id=user_id,
                    session=db_session,
                    confirmed=True  # AI уже подтвердил через tool call
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "edit_task":
                result = edit_task(
                    task_id=args.get("task_id"),
                    title=args.get("title"),
                    description=args.get("description"),
                    reminder_time=args.get("reminder_time"),
                    user_id=user_id,
                    session=db_session,
                )
                tool_results.append({"function": func_name, "result": result})

            elif func_name == "edit_task":
                # Special handling for time-only updates
                if intent.get("params", {}).get("time_only"):
                    # Find the most recent pending task to update
                    temp_session = Session()
                    try:
                        user_obj = temp_session.query(User).filter_by(telegram_id=user_id).first()
                        if user_obj:
                            recent_task = temp_session.query(Task).filter_by(
                                user_id=user_obj.id, 
                                status="pending"
                            ).order_by(Task.created_at.desc()).first()
                            
                            if recent_task:
                                # Parse time from message
                                time_match = re.search(r'(\d{1,2}):(\d{2})', original_message)
                                if time_match:
                                    hours, minutes = time_match.groups()
                                    # Get user timezone
                                    user_tz = pytz.timezone(user_obj.timezone) if user_obj.timezone else pytz.UTC
                                    # Assume tomorrow if "завтра" in message, otherwise today
                                    base_date = datetime.now(user_tz)
                                    if 'завтра' in original_message.lower():
                                        base_date += timedelta(days=1)
                                    
                                    # Set time in user's timezone, then convert to UTC
                                    reminder_time = base_date.replace(hour=int(hours), minute=int(minutes), second=0, microsecond=0)
                                    if reminder_time.tzinfo is None:
                                        reminder_time = user_tz.localize(reminder_time)
                                    reminder_time = reminder_time.astimezone(pytz.UTC)
                                    
                                    result = edit_task(
                                        task_id=recent_task.id,
                                        title=None,
                                        description=None,
                                        reminder_time=reminder_time.isoformat(),
                                        user_id=user_id,
                                        session=db_session,
                                    )
                                else:
                                    result = "Не удалось распознать время в сообщении"
                            else:
                                result = "Нет активных задач для обновления времени"
                        else:
                            result = "Пользователь не найден"
                    except Exception as e:
                        logger.error(f"Error in edit_task time_only: {e}")
                        result = f"Ошибка обновления времени: {str(e)}"
                    finally:
                        temp_session.close()
                else:
                    # Regular edit_task handling
                    result = edit_task(
                        task_id=args.get("task_id"),
                        title=args.get("title"),
                        description=args.get("description"),
                        reminder_time=args.get("reminder_time"),
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
                result = brainstorm_ideas(
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
                result = update_user_memory(
                    info=args.get("info"),
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
                # AI должен прокомментировать изменение - НЕ добавляем в natural_responses
                pass

            elif "Задача удалена" in result_text or "Задача.*удалена" in result_text:
                natural_responses.append("Задача удалена")

            elif "Задача обновлена" in result_text:
                natural_responses.append("Задача обновлена")

            elif "Статус подписки:" in result_text:
                natural_responses.append(result_text)

            elif "Платеж создан" in result_text:
                natural_responses.append("Платеж создан, следуйте инструкциям для оплаты")

            elif "Идеи сгенерированы" in result_text or "мозговой штурм" in result_text.lower():
                natural_responses.append(result_text)

            elif "Задачи с инсайтами:" in result_text:
                natural_responses.append(result_text)

            elif "Список партнеров:" in result_text:
                natural_responses.append(result_text)

            elif "NEED_TIME_FOR_TASK:" in result_text:
                # AI должен спросить о времени для задачи - создаем понятный вопрос
                if ":" in result_text:
                    task_title = result_text.split(":", 1)[1].strip()
                    natural_responses.append(f"На какое время поставить задачу '{task_title}'?")
                else:
                    natural_responses.append("На какое время поставить задачу?")

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
            elif any("TASK_CREATED:" in r for r in natural_responses):
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
   
   в) ПЕРСОНАЛИЗАЦИЯ:
      - Используй данные профиля (интересы, навыки, цели)
      - Упомяни релевантные детали: если задача про AI и у пользователя интерес к AI
      - Учти историю взаимодействий

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


async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    logger = logging.getLogger(__name__)
    logger.info(f"[CHAT_WITH_AI] Called with user_id={user_id}")
    
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
    fresh_tasks_info = f"\n[АКТУАЛЬНЫЕ ЗАДАЧИ НА МОМЕНТ ЗАПРОСА]\n{current_tasks}\n"
    
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

                if profile_info:
                    user_memory += f"\nПрофиль: {', '.join(profile_info)}"
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
                        user_memory += f"\nЕСЛИ ПОДХОДИТ КОНТЕКСТ: можешь ненавязчиво спросить о {field_to_ask}е для персонализации советов (только если разговор естественным образом к этому ведет)"
                    else:
                        # Если основные поля заполнены, не спрашиваем вообще
                        pass

                profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                logger.info(f"[PROFILE DEBUG] Profile filled status: {profile_filled}, fields count: {len(profile_info)}")

                # ДЕМОНСТРАЦИЯ ВОЗМОЖНОСТЕЙ: каждые 5-7 взаимодействий
                interaction_count = getattr(profile, 'interaction_count', 0) or 0
                if interaction_count > 0 and interaction_count % 6 == 0:  # Каждые 6 взаимодействий
                    user_memory += "\nДЕМОНСТРИРУЙ ВОЗМОЖНОСТИ: Расскажи о полезных функциях - поиске контактов, делегировании задач, анализе прогресса. Сделай это естественно в контексте ответа!"

                # Если профиль совсем пустой - мягко предложи заполнить в первом сообщении
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\nПЕРВОЕ ЗНАКОМСТВО: Если пользователь здоровается, можешь ненавязчиво предложить рассказать о себе (город, интересы) для персонализации, но только если разговор естественно к этому ведет"
            else:
                user_memory += "\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"
                logger.info("[PROFILE DEBUG] No profile found, will request profile filling")

            # ЗАГРУЖАЕМ ПОЛНЫЙ СПИСОК ЗАДАЧ ДЛЯ ПРЕДОТВРАЩЕНИЯ ВЫДУМЫВАНИЯ
            # Агент НЕ ДОЛЖЕН выдумывать задачи - только использовать реальные данные из БД
            # Используем свежие данные, полученные в начале функции
            logger.info(f"[TASKS DEBUG] Using fresh tasks: {fresh_tasks_info[:100] if fresh_tasks_info else 'None'}...")
            if fresh_tasks_info and "У вас" in fresh_tasks_info:
                user_memory += f"\n\n{fresh_tasks_info}\n\nВАЖНО: НЕ выдумывай задачи! Используй ТОЛЬКО те задачи которые указаны выше. Если говоришь о задаче, ОБЯЗАТЕЛЬНО проверь что она есть в списке."
            else:
                user_memory += "\n\nУ пользователя нет активных задач."

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
                if partners:
                    # partners - это список объектов UserProfile
                    partners_info = []
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
                        user_memory += f"\nДоступные контакты с общими интересами: {', '.join(partners_info)}"
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

        # Use basic intent classification
        intent = {"type": "conversation", "confidence": 0.5, "params": {}}
        logger.info("[INTENT] Using basic intent classification")

        # Special handling for delegation requests from frontend buttons
        if mentions and any(word in clean_message.lower() for word in ['делегировать', 'поручить', 'delegate', 'поручить']):
            if 'список' in clean_message.lower() or 'активные' in clean_message.lower():
                # Request to show task list for delegation
                intent = {"type": "list_tasks", "confidence": 0.9, "params": {"for_delegation": True, "target_user": mentions[0]}}
                logger.info(f"[DELEGATION LIST] Setting intent to list_tasks for delegation to {mentions[0]}")
            else:
                # Direct delegation request
                intent = {"type": "delegate_task", "confidence": 0.9, "params": {"delegated_to_username": mentions[0]}}
                logger.info(f"[DELEGATION DETECTED] Setting intent to delegate_task for message: {clean_message[:50]}...")

        # Special handling for delete task requests
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            if any(word in clean_message.lower() for word in ['удали', 'удалить', 'delete', 'remove', 'сними', 'отмени']):
                if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']):
                    intent = {"type": "delete_task", "confidence": 0.9, "params": {}}
                    logger.info(f"[DELETE TASK DETECTED] Setting intent to delete_task for message: {clean_message[:50]}...")

        # Special handling for add task requests - EXPLICIT (добавь задачу, создай задачу)
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            if any(word in clean_message.lower() for word in ['добавь', 'добавить', 'создай', 'создать', 'запланируй', 'запланировать', 'запланируем', 'add', 'create', 'schedule']):
                if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']) or not any(word in clean_message.lower() for word in ['профиль', 'контакт', 'profile', 'contact']):
                    intent = {"type": "add_task", "confidence": 0.9, "params": {}}
                    logger.info(f"[ADD TASK DETECTED] Setting intent to add_task for message: {clean_message[:50]}...")

        # Special handling for IMPLICIT task creation (нужно сходить, надо купить)
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            implicit_indicators = ['нужно', 'надо', 'должен', 'планирую', 'собираюсь', 'need to', 'have to']
            action_words = ['сходить', 'купить', 'позвонить', 'написать', 'встретиться', 'подготовить', 'сделать', 'закончить']
            
            has_implicit = any(word in clean_message.lower() for word in implicit_indicators)
            has_action = any(word in clean_message.lower() for word in action_words)
            
            # Добавим обработку для напоминаний
            reminder_words = ['напомни', 'напомнить', 'remind']
            has_reminder = any(word in clean_message.lower() for word in reminder_words)
            
            if (has_implicit and has_action) or has_reminder:
                intent = {"type": "add_task", "confidence": 0.85, "params": {}}
                logger.info(f"[IMPLICIT TASK DETECTED] Setting intent to add_task for implicit request: {clean_message[:50]}...")

        # Special handling for complete task requests
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            if any(word in clean_message.lower() for word in ['заверши', 'выполни', 'complete', 'finish', 'done']):
                if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']):
                    intent = {"type": "complete_task", "confidence": 0.9, "params": {}}
                    logger.info(f"[COMPLETE TASK DETECTED] Setting intent to complete_task for message: {clean_message[:50]}...")

        # Special handling for list tasks requests
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            if any(word in clean_message.lower() for word in ['покажи', 'список', 'list', 'show']):
                if any(word in clean_message.lower() for word in ['задачи', 'задач', 'tasks']):
                    intent = {"type": "list_tasks", "confidence": 0.9, "params": {}}
                    logger.info(f"[LIST TASKS DETECTED] Setting intent to list_tasks for message: {clean_message[:50]}...")

        # Special handling for update profile requests - ONLY if not a task-related request
        if intent.get('type') == 'conversation':  # Проверяем, что intent еще не определен как задача
            profile_explicit = ['обнови профиль', 'измени профиль', 'заполни профиль', 'update profile']
            is_explicit_profile = any(phrase in clean_message.lower() for phrase in profile_explicit)
            
            if is_explicit_profile or (
                any(word in clean_message.lower() for word in ['обнови', 'измени', 'оставь', 'очистить']) 
                and not any(word in clean_message.lower() for word in ['задач', 'task'])
            ):
                intent = {"type": "update_profile", "confidence": 0.8, "params": {}}
                logger.info(f"[UPDATE PROFILE DETECTED] Setting intent to update_profile for message: {clean_message[:50]}...")

        # Special handling for profile information sharing - расширенная версия
        if intent.get('type') == 'conversation':  # Только если intent еще не определен
            # Личные местоимения + профессиональная информация
            personal_pronouns = ['я', 'мне', 'мой', 'моя', 'мои', 'i am', 'i work', 'работаю']
            professional_info = ['директор', 'менеджер', 'разработчик', 'аналитик', 'компания', 'фирма', 
                               'навыки', 'умею', 'знаю', 'занимаюсь', 'специализируюсь',
                               'python', 'sql', 'java', 'javascript', 'react', 'программирую',
                               'живу', 'город', 'москва', 'петербург', 'екатеринбург',
                               'интересы', 'увлечения', 'интересуюсь', 'люблю',
                               'director', 'manager', 'developer', 'company', 'analyst', 'skills']
            
            # Интересы и увлечения - расширенная детекция
            interests_keywords = ['хочу заняться', 'начну заниматься', 'планирую заняться', 'буду заниматься',
                                'интересуюсь', 'увлекаюсь', 'нравится', 'люблю', 'хочу изучить', 'изучаю',
                                'спорт', 'фитнес', 'тренажерный зал', 'бег', 'плавание', 'йога',
                                'программирование', 'дизайн', 'музыка', 'рисование', 'чтение',
                                'путешествия', 'кулинария', 'танцы', 'фотография']
            
            has_personal = any(word in clean_message.lower() for word in personal_pronouns)
            has_professional = any(word in clean_message.lower() for word in professional_info)
            has_interests = any(phrase in clean_message.lower() for phrase in interests_keywords)
            
            # Также проверим, есть ли явная просьба заполнить профиль
            profile_fill_request = any(phrase in clean_message.lower() for phrase in 
                                      ['заполн', 'давай заполн', 'обнов', 'расскаж о себе'])
            
            if (has_personal and has_professional) or has_interests or profile_fill_request:
                intent = {"type": "profile_info", "confidence": 0.85, "params": {}}
                logger.info(f"[PROFILE INFO DETECTED] Setting intent to profile_info for message: {clean_message[:50]}...")

        # Special handling for time expressions (update existing task)
        # Только если уже нет более приоритетного intent
        if intent.get('type') == 'conversation':
            time_patterns = [
                r'завтра\s+в\s+\d{1,2}:\d{2}',
                r'сегодня\s+в\s+\d{1,2}:\d{2}',
                r'через\s+\d+\s+(час|часа|часов|мин|минуту|минут|минуты)\s+в\s+\d{1,2}:\d{2}',
                r'в\s+\d{1,2}:\d{2}',
                r'\d{1,2}:\d{2}'
            ]
            if any(re.search(pattern, clean_message.lower()) for pattern in time_patterns):
                # Check if there are pending tasks to update
                if user:
                    pending_tasks = db_session.query(Task).filter_by(user_id=user.id, status="pending").all()
                    if pending_tasks:
                        intent = {"type": "edit_task", "confidence": 0.8, "params": {"time_only": True}}
                        logger.info(f"[TIME EXPRESSION DETECTED] Setting intent to edit_task for time update: {clean_message[:50]}...")

        # Убрана специальная обработка приветствий - все через AI промпт

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

        system_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory,
            subscription_tier=subscription_tier)
        logger.info("[PROMPTS] Using extended prompt system")

        # Проверяем контекст последней созданной задачи для edit_task
        last_task_context = ""
        if user_id:
            try:
                # Получаем последнюю созданную задачу из БД
                last_task = db_session.query(Task).filter(
                    Task.user_id == user.id
                ).order_by(Task.created_at.desc()).first()
                if last_task:
                    # Конвертируем время в часовой пояс пользователя
                    task_time_str = ''
                    if last_task.reminder_time:
                        try:
                            user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                            reminder_dt = last_task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                            tz_name = user_tz.zone if user_tz != pytz.UTC else 'UTC'
                            task_time_str = f"{reminder_dt.strftime('%d.%m.%Y %H:%M')} ({tz_name})"
                        except Exception:
                            task_time_str = str(last_task.reminder_time)
                    
                    last_task_context = f"\n\nКОНТЕКСТ ПОСЛЕДНЕЙ ЗАДАЧИ: ID={last_task.id}, название='{last_task.title}', время='{task_time_str}'. ЕСЛИ пользователь даёт уточнения (я ошибся, не завтра а сегодня, изменить время и т.д.), ОБЯЗАТЕЛЬНО используй edit_task(task_id={last_task.id}, ...)!"
                    logger.info(f"[LAST_TASK_CONTEXT] Loaded for user {user_id}: ID={last_task.id}, title={last_task.title}, time={task_time_str}")
            except Exception as e:
                logger.error(f"Error loading last_task from DB: {e}")

        messages = [{"role": "system", "content": system_prompt}]
        if context and isinstance(context, list):
            for item in context:
                if "user" in item:
                    messages.append({"role": "user", "content": item["user"]})
                if "agent" in item:
                    messages.append({"role": "assistant", "content": item["agent"]})
        # Добавляем текущее сообщение с контекстом последней задачи
        user_message_with_context = message + last_task_context
        messages.append({"role": "user", "content": user_message_with_context})

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

        # Умная логика выбора инструментов на основе intent classification
        intent_type = intent.get('type', 'unknown')

        if intent_type in ['conversation', 'unknown'] and is_advice_question:
            # Вопросы о совете - не используем инструменты, отвечаем текстом
            tool_choice = "none"
        elif intent_type == 'greeting':
            # Приветствия - не используем инструменты, отвечаем текстом
            tool_choice = "none"
        elif intent_type in ['add_task', 'complete_task', 'list_tasks', 'edit_task', 'delete_task', 'delegate_task']:
            # Явные запросы на управление задачами - ОБЯЗАТЕЛЬНО используем инструменты
            tool_choice = "required"
            logger.info(f"[TOOL CHOICE] REQUIRED for task management: {intent_type}")
        elif intent_type == 'find_partners':
            # Поиск партнеров - используем инструменты
            tool_choice = "auto"
        elif intent_type in ['update_profile', 'profile_info']:
            # Обновление профиля или информация о профиле - ОБЯЗАТЕЛЬНО используем инструменты
            tool_choice = "required"
            logger.info(f"[TOOL CHOICE] REQUIRED for profile update: {intent_type}")
        else:
            # По умолчанию - автоопределение
            tool_choice = "auto"

        # Динамическая температура в зависимости от типа сообщения
        temperature = 0.7  # Default
        top_p = 1.0  # Default

        if intent_type == 'greeting':
            # Для приветствий нужна максимальная вариативность
            temperature = 1.0
            top_p = 0.95  # Nucleus sampling для разнообразия
        elif intent_type in ['conversation', 'unknown'] and is_advice_question:
            # Для советов нужна креативность
            temperature = 0.85
            top_p = 0.95
        elif intent_type in ['add_task', 'complete_task', 'list_tasks']:
            # Для задач нужна точность
            temperature = 0.6
            top_p = 1.0
        elif intent_type == 'profile_info':
            # Для информации о профиле нужна максимальная точность
            temperature = 0.1
            top_p = 1.0
        else:
            # По умолчанию
            temperature = 0.7
            top_p = 1.0

        logger.info(f"Using temperature {temperature}, top_p {top_p} for intent type '{intent_type}'")

        # ИНТЕЛЛЕКТУАЛЬНОЕ КЭШИРОВАНИЕ: только для определенных типов запросов
        # Не кэшируем conversational запросы, поиск партнеров и запросы требующие актуальности
        should_cache = intent_type not in [
            'conversation', 'unknown', 'greeting', 'find_partners', 'profile_info'
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
                intent_type,
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
            logger.info(f"Skipping cache for intent_type '{intent_type}' (requires freshness)")
            cache_key = None  # Для сохранения в кэш позже

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        
        # Параметры запроса согласно документации DeepSeek API
        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": 4096,  # Максимум токенов для ответа
            "frequency_penalty": 0.0,  # Не повторять одни и те же фразы
            "presence_penalty": 0.0,  # Стимулировать новые темы
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

                                    # Проверяем tool_calls в API response
                                    tool_calls = message_response.get("tool_calls")
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

                            if tool_calls:
                                result = await process_tool_calls(tool_calls, intent, message, user_id, db_session, session, url, headers, system_prompt, user_now, current_time_str, original_message, mentions_str, is_advice_question, current_time=user_now)
                                if result:
                                    return result
                                # tool_calls были проигнорированы для вопроса совета, переходим к обычной обработке
                            
                            # Успех - выходим из retry loop
                            logger.info(f"[SUCCESS] API call successful, content length: {len(content) if content else 0}")
                            logger.info(f"[SUCCESS] Tool calls found: {len(tool_calls) if tool_calls else 0}")
                            # Устанавливаем флаг успешного выполнения
                            success = True
                            break
                        
                        elif response.status == 429:
                            # Rate limit - обязательно retry
                            logger.warning(f"Rate limit (429), retry {attempt + 1}/{max_retries + 1}")
                            if attempt < max_retries:
                                continue
                            else:
                                content = "Извините, слишком много запросов. Попробуйте через несколько секунд."
                                break
                        
                        elif response.status in [500, 502, 503, 504]:
                            # Server errors - retry
                            error_text = await response.text()
                            logger.error(f"Server error {response.status}: {error_text[:200]}")
                            if attempt < max_retries:
                                logger.info(f"Retrying due to server error ({response.status})")
                                continue
                            else:
                                content = "Извините, сервер временно недоступен. Попробуйте позже."
                                break
                        
                        elif response.status == 400:
                            # Bad request - не retry, логируем для отладки
                            error_text = await response.text()
                            logger.error(f"Bad request (400): {error_text}")
                            logger.error(f"Request data: {json.dumps(data, ensure_ascii=False, indent=2)}")
                            content = "Извините, некорректный запрос. Попробуйте переформулировать."
                            break
                        
                        elif response.status == 401:
                            # Unauthorized - критическая ошибка
                            logger.error("API key invalid or expired (401)")
                            content = "Извините, проблема с авторизацией API. Обратитесь к администратору."
                            break
                        
                        else:
                            # Другие ошибки
                            error_text = await response.text()
                            logger.error(f"Unexpected status {response.status}: {error_text[:200]}")
                            if attempt < max_retries:
                                continue
                            else:
                                content = "Извините, произошла ошибка. Попробуйте еще раз."
                                break
                                
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries:
                    continue
                else:
                    content = "Извините, проблема с сетевым соединением. Попробуйте позже."
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
                content = "Извините, не удалось получить ответ. Попробуйте позже."
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
        return f"ERROR: {str(e)} [chat_with_ai]"
        # Добавляем номер строки для отладки
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last_frame = tb[-1]
            logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
        return f"OUTER ERROR: {str(e)} [v2]"


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
        user_now = datetime.now(pytz.UTC)
        # Формат времени С ТАЙМЗОНОЙ для промпта
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        user_username = user.username if user.username else "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory)

        system_prompt = base_prompt

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        user_prompt = f"Сгенерируй персонализированное напоминание о задаче: '{task_title}'."
        if task_context:
            user_prompt += f"\n{task_context}"
        if profile_context:
            user_prompt += f"\n{profile_context}"
        user_prompt += "\n\nДай конкретные практические советы, мотивируй, учитывай контекст пользователя."

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
        user_now = datetime.now(pytz.UTC)
        # Формат времени С ТАЙМЗОНОЙ для промпта
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(
            user_now,
            current_time_str,
            current_date_str,
            user_username,
            mentions_str,
            user_memory)

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


async def generate_proactive_message(user_id, context="general", task_count=0, overdue_count=0):
    """Генерирует проактивное сообщение по основному промпту системы, как обычные ответы AI"""
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
                # Формат времени С ТАЙМЗОНОЙ для промпта
                current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                user_tz = pytz.UTC

                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                        user_now = base_now.astimezone(user_tz)
                        # Обновляем с учетом таймзоны пользователя
                        current_time_str = f"{user_now.strftime('%H:%M')} ({user.timezone})"
                        current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                    except Exception as e:
                        logger.error(f"Error setting user timezone: {e}")

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
            subscription_tier=subscription_tier)
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

СИТУАЦИЯ: Стандартное проактивное взаимодействие.
ТОНАЛЬНОСТЬ: Дружелюбная, универсальная.
ЗАДАЧА: Общая поддержка продуктивности на основе профиля.
ФОРМАТ: 1-2 абзаца с персональными рекомендациями."""
        }

        # Выбираем подходящий промпт
        selected_prompt = proactive_prompts.get(context, proactive_prompts["general"])

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
                        "general": "Привет! Как дела с задачами? Может, есть что-то, в чем я могу помочь - планирование, поиск контактов или просто обсуждение планов?"
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
            "general": "Добрый день! Учитывая твой профиль и текущие задачи, могу предложить несколько идей для продуктивного дня. Есть ли что-то конкретное, над чем ты работаешь сейчас?"
        }
        return fallback_messages.get(context, fallback_messages["general"])


async def generate_daily_report(user_id):
    """Генерирует ежедневный отчет о задачах"""
    try:
        # Получить задачи пользователя
        db_session = Session()
        tasks = db_session.query(Task).filter_by(user_id=user_id).all()
        db_session.close()

        completed = [t for t in tasks if t.status == "completed"]
        pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

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
        user_now = datetime.now(pytz.UTC)
        # Формат времени С ТАЙМЗОНОЙ для промпта
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory)

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
        user_now = datetime.now(pytz.UTC)
        # Формат времени С ТАЙМЗОНОЙ для промпта
        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
        current_date_str = user_now.strftime("%Y-%m-%d")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

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
