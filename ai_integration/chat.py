from . import handlers
import aiohttp
import json
import logging
import asyncio
import traceback
from datetime import datetime, timezone, timedelta
import re
import pytz

from config import DEEPSEEK_API_KEY
from models import Session, User, Task, UserProfile, Subscription
from .memory import decrypt_data
from .utils import (
    determine_timezone_from_time, analyze_user_context_for_advice,
    replace_placeholders, clean_technical_details,
    post_process_tool_calls, smart_fallback_handler,
    redis_client, post_process_response
)
from .prompts import get_extended_system_prompt
from .tools import TOOLS

try:
    from improved_prompts_final import improved_classify_intent, get_optimized_prompt_final, PROMPTS_V2_AVAILABLE, improved_fallback, ai_classify_intent
except ImportError:
    PROMPTS_V2_AVAILABLE = False

logger = logging.getLogger(__name__)


add_task = handlers.add_task
complete_task = handlers.complete_task
list_tasks = handlers.list_tasks
find_partners = handlers.find_partners
update_profile = handlers.update_profile
delegate_task = handlers.delegate_task
delete_all_tasks = handlers.delete_all_tasks
delete_task = handlers.delete_task
edit_task = handlers.edit_task
check_subscription_status = handlers.check_subscription_status
create_subscription_payment = handlers.create_subscription_payment
brainstorm_ideas = handlers.brainstorm_ideas
enrich_task_list_with_insights = handlers.enrich_task_list_with_insights
get_partners_list = handlers.get_partners_list


async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    logger = logging.getLogger(__name__)

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

    # Проверяем сообщение о времени и обновляем timezone
    time_message_match = re.search(r"мое\s+местное\s+время:\s*(\d{1,2}:\d{2})", message.lower())
    if time_message_match:
        user_time_str = time_message_match.group(1)
        detected_timezone = determine_timezone_from_time(user_time_str, user_id)
        if detected_timezone:
            logger.info(f"Detected timezone {detected_timezone} from time {user_time_str}")
            update_profile(timezone=detected_timezone, user_id=user_id, db_session=db_session)

    # Сохраняем оригинальное сообщение ДО очистки
    original_message = message
    # Extract mentions before cleaning message
    mentions = re.findall(r"@[\w]+", message)
    mentions_str = ", ".join(mentions) if mentions else "нет"
    # Clean message from mentions for processing
    clean_message = re.sub(r"@[\w]+", "", message).strip()
    context_len = (
        len(context) if context and not isinstance(context, int) else (context if isinstance(context, int) else 0)
    )
    logger.info(
        f"chat_with_ai called with message: {
            clean_message[
                :50]}..., mentions: {mentions_str}, context len: {context_len}, user_id: {user_id}, file: {
            file_content is not None}")
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
        current_time_str = user_now.strftime("%H:%M")
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
            
            # Получаем subscription_tier
            subscription_tier = user.subscription_tier.value if user and hasattr(user, 'subscription_tier') and user.subscription_tier else None

            # Check subscription
            from config import FREE_ACCESS_MODE

            if not FREE_ACCESS_MODE:
                subscription = db_session.query(Subscription).filter_by(user_id=user.id, status="active").first()
                if not subscription:
                    db_session.close()
                    # Генерируем сообщение о подписке через AI
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "У пользователя нет активной подписки. Сообщи об этом и предложи активировать подписку в @asibiont_bot."}]
                        data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.7, "max_tokens": 80}
                        async with aiohttp.ClientSession() as sess:
                            async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                if resp.status == 200:
                                    result = await resp.json()
                                    return result["choices"][0]["message"]["content"].strip()
                    except Exception:
                        pass
                    return "Для использования требуется активная подписка 💳 Активируйте её в @asibiont_bot"

            # Get user current time FIRST before using it
            base_now = datetime.now(pytz.UTC)
            logger.info(f"[TIME CHECK] Real UTC now: {base_now}")
            logger.info(f"[TIME CHECK] Formatted: {base_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            user_now = base_now  # Default to base_now
            current_time_str = user_now.strftime("%H:%M")
            user_tz = pytz.UTC  # Default
            if user:
                tz_str = user.timezone if user.timezone else "UTC"
                logger.info(f"User timezone: {tz_str}")
                try:
                    user_tz = pytz.timezone(tz_str)
                    user_now = base_now.astimezone(user_tz)
                    current_time_str = user_now.strftime("%H:%M")
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Error setting user timezone: {e}")
                    user_tz = pytz.UTC
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""  # If decryption fails, skip

            # Добавляем информацию из профиля (компания, должность и т.д.)
            profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_filled = False
            if profile:
                profile_info = []
                if profile.city:
                    profile_info.append(f"Город: {profile.city}")
                if profile.company:
                    profile_info.append(f"Компания: {profile.company}")
                if profile.position:
                    profile_info.append(f"Должность: {profile.position}")
                if hasattr(profile, 'languages') and profile.languages:
                    profile_info.append(f"Языки: {profile.languages}")
                if profile.skills:
                    profile_info.append(f"Навыки: {profile.skills}")
                if profile.interests:
                    profile_info.append(f"Интересы: {profile.interests}")
                if profile.goals:
                    profile_info.append(f"Цели: {profile.goals}")

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
                if not (hasattr(profile, 'languages') and profile.languages):
                    empty_fields.append("языки")

                if profile_info:
                    user_memory += f"\nПрофиль: {', '.join(profile_info)}"

                # Проактивное заполнение при незаполненных полях
                if empty_fields:
                    fields_list = ', '.join(empty_fields[:3])  # Берем первые 3 незаполненных
                    user_memory += f"\n⚠️ НЕЗАПОЛНЕННЫЕ ПОЛЯ: {fields_list}. Каждые 5-7 сообщений ПРОАКТИВНО спрашивай об одном из них (естественно в контексте диалога, не навязчиво)!"

                profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                # Если профиль совсем пустой - срочно спроси в первом сообщении
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\nКРИТИЧНО ВАЖНО: Профиль почти ПУСТ! В первом ответе дружелюбно спроси о городе, компании или интересах для лучшей помощи!"
            else:
                user_memory += "\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"

            # НЕ загружаем задачи в user_memory! Агент должен сам вызвать list_tasks()
            # Это критично для предотвращения выдумывания задач

            # НО добавляем КРАТКУЮ сводку для контекста
            tasks_summary = db_session.query(Task).filter_by(user_id=user.id, status="pending").count()
            overdue_tasks = (
                db_session.query(Task)
                .filter(Task.user_id == user.id, Task.reminder_time < user_now, Task.status == "pending")
                .limit(5)
                .all()
            )

            if tasks_summary > 0:
                user_memory += f"\nСводка: всего активных задач {tasks_summary}"

            if overdue_tasks:
                overdue_titles = [f"{t.title}" for t in overdue_tasks]
                user_memory += f"\nПРОСРОЧЕННЫЕ ЗАДАЧИ: {', '.join(overdue_titles)} - предложи помощь!"

            # Add delegated tasks info
            if user.username:
                delegated_tasks = (
                    db_session.query(Task)
                    .filter(Task.delegated_to_username.ilike(user.username), Task.delegation_status == "pending")
                    .all()
                )
                if delegated_tasks:
                    delegated_info = [
                        f"Задача '{t.title}' (ID: {t.id}) от @{creator.username if (creator := db_session.query(User).filter_by(id=t.user_id).first()) else 'unknown'}"
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

            # Add partners/contacts info
            try:
                partners = get_partners_list(user_id=user_id, session=db_session)
                if partners:
                    # partners - это список объектов UserProfile
                    partners_usernames = []
                    for p in partners[:5]:
                        partner_user = db_session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            partners_usernames.append(f"@{partner_user.username}")
                    if partners_usernames:
                        user_memory += f"\nДоступные контакты: {', '.join(partners_usernames)}"
            except Exception as e:
                logger.error(f"Error getting partners: {e}")

            # Add file content if provided
            if file_content:
                user_memory += f"\nСодержимое прикрепленного файла: {file_content[:2000]}"  # Limit to 2000 chars

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

        # Classify user intent (use AI-powered version)
        if PROMPTS_V2_AVAILABLE:
            intent = await ai_classify_intent(clean_message, mentions_str, DEEPSEEK_API_KEY)
            logger.info(f"[AI INTENT] User intent: {intent['type']} (confidence: {intent['confidence']})")
        else:
            # Fallback to basic intent if improved_prompts_final.py not available
            intent = {"type": "conversation", "confidence": 0.5, "params": {}}
            logger.warning("[FALLBACK] improved_prompts_final.py not available, using basic intent")

        # Special handling for delegation requests from frontend buttons
        if not PROMPTS_V2_AVAILABLE and mentions and any(word in clean_message.lower() for word in ['делегировать', 'поручить', 'delegate', 'поручить']):
            if 'список' in clean_message.lower() or 'активные' in clean_message.lower():
                # Request to show task list for delegation
                intent = {"type": "list_tasks", "confidence": 0.9, "params": {"for_delegation": True, "target_user": mentions[0]}}
                logger.info(f"[DELEGATION LIST] Setting intent to list_tasks for delegation to {mentions[0]}")
            else:
                # Direct delegation request
                intent = {"type": "delegate_task", "confidence": 0.9, "params": {"delegated_to_username": mentions[0]}}
                logger.info(f"[DELEGATION DETECTED] Setting intent to delegate_task for message: {clean_message[:50]}...")

        # Special handling for delete task requests
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['удали', 'удалить', 'delete', 'remove', 'сними', 'отмени']):
            if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']):
                intent = {"type": "delete_task", "confidence": 0.9, "params": {}}
                logger.info(f"[DELETE TASK DETECTED] Setting intent to delete_task for message: {clean_message[:50]}...")

        # Special handling for add task requests
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['добавь', 'добавить', 'создай', 'создать', 'add', 'create']):
            if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']):
                intent = {"type": "add_task", "confidence": 0.9, "params": {}}
                logger.info(f"[ADD TASK DETECTED] Setting intent to add_task for message: {clean_message[:50]}...")

        # Special handling for complete task requests
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['заверши', 'выполни', 'complete', 'finish', 'done']):
            if any(word in clean_message.lower() for word in ['задачу', 'задачи', 'task', 'tasks']):
                intent = {"type": "complete_task", "confidence": 0.9, "params": {}}
                logger.info(f"[COMPLETE TASK DETECTED] Setting intent to complete_task for message: {clean_message[:50]}...")

        # Special handling for list tasks requests
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['покажи', 'список', 'list', 'show']):
            if any(word in clean_message.lower() for word in ['задачи', 'задач', 'tasks']):
                intent = {"type": "list_tasks", "confidence": 0.9, "params": {}}
                logger.info(f"[LIST TASKS DETECTED] Setting intent to list_tasks for message: {clean_message[:50]}...")

        # Special handling for update profile requests
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['обнови', 'измени', 'добавь', 'update', 'change', 'add']):
            if any(word in clean_message.lower() for word in ['профиль', 'профиле', 'profile']):
                intent = {"type": "update_profile", "confidence": 0.9, "params": {}}
                logger.info(f"[UPDATE PROFILE DETECTED] Setting intent to update_profile for message: {clean_message[:50]}...")

        # Special handling for profile information sharing
        if not PROMPTS_V2_AVAILABLE and any(word in clean_message.lower() for word in ['я', 'мне', 'мой', 'моя', 'мои', 'i am', 'i work', 'работаю']):
            if any(word in clean_message.lower() for word in ['директор', 'менеджер', 'разработчик', 'компания', 'фирма', 'director', 'manager', 'developer', 'company']):
                intent = {"type": "profile_info", "confidence": 0.8, "params": {}}
                logger.info(f"[PROFILE INFO DETECTED] Setting intent to profile_info for message: {clean_message[:50]}...")

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
                if "agent" in item:
                    # Берём первые 40 символов
                    response_text = item["agent"][:40].strip()
                    if response_text and response_text not in last_responses:
                        last_responses.append(response_text)

        # Ограничиваем до 2 последних
        last_responses = last_responses[-2:]

        if PROMPTS_V2_AVAILABLE:
            system_prompt = get_optimized_prompt_final(
                user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, last_responses
            )
            logger.info("[PROMPTS V2] Using optimized prompt system")
        else:
            system_prompt = get_extended_system_prompt(
                user_now,
                current_time_str,
                current_date_str,
                user_username,
                mentions_str,
                user_memory,
                subscription_tier=subscription_tier)
            logger.info("[LEGACY] Using extended prompt system")

        # Проверяем контекст последней созданной задачи для edit_task
        last_task_context = ""
        if redis_client and user_id:
            try:
                last_task_data = await redis_client.get(f"last_task_id:{user_id}")
                if last_task_data:
                    task_info = json.loads(last_task_data.decode("utf-8"))
                    last_task_context = f"\n\nКОНТЕКСТ ПОСЛЕДНЕЙ ЗАДАЧИ: ID={
                        task_info['id']}, название='{
                        task_info['title']}', время='{
                        task_info.get(
                            'reminder_time',
                            '')}'. ЕСЛИ пользователь даёт уточнения (я ошибся, не завтра а сегодня, изменить время и т.д.), ОБЯЗАТЕЛЬНО используй edit_task(task_id={
                        task_info['id']}, ...)!"
                    logger.info(f"[LAST_TASK_CONTEXT] Loaded for user {user_id}: {task_info}")
            except Exception as e:
                logger.error(f"Error loading last_task_id from Redis: {e}")

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
            'delegate_task', 'find_partners', 'update_profile'
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
            # Явные запросы на управление задачами - используем инструменты
            tool_choice = "auto"
        elif intent_type == 'find_partners':
            # Поиск партнеров - используем инструменты
            tool_choice = "auto"
        elif intent_type == 'update_profile':
            # Обновление профиля - используем инструменты
            tool_choice = "auto"
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

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-v3.2",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": tool_choice,
            "temperature": temperature,
            "top_p": top_p,
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        # Retry loop for API call
        max_retries = 2
        message_response = {"content": ""}  # Initialize with default
        tool_calls = []  # Initialize tool_calls
        for attempt in range(max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        logger.info(f"DeepSeek API response status: {response.status} (attempt {attempt + 1})")
                        if response.status == 200:
                            # Успешный ответ - обрабатываем
                            tool_calls = []
                            try:
                                result = await response.json()
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

                                # ПОСТ-ПРОЦЕССИНГ: Корректируем tool calls на основе intent
                                corrected_tool_calls = post_process_tool_calls(intent, tool_calls, message)
                                if corrected_tool_calls:
                                    tool_calls = corrected_tool_calls

                                # Если это вопрос о совете, игнорируем tool_calls и обрабатываем как обычный текст
                                if is_advice_question:
                                    tool_calls = None
                                else:
                                    # Обработка tool calls
                                    tool_results = []
                                    for tool_call in tool_calls:
                                        try:
                                            func_name = tool_call["function"]["name"]
                                            args = json.loads(tool_call["function"]["arguments"])
                                            logger.info(f"[TOOL CALL] Executing {func_name} with args: {args}")

                                            if func_name == "add_task":
                                                logger.info(
                                                    f"[AI TOOL CALL] add_task called with args: {args}, intent params: {intent.get('params', {})}")
                                                
                                                # СТРОГАЯ проверка наличия времени
                                                reminder_time = args.get("reminder_time")
                                                if not reminder_time or '@unknown' in str(reminder_time):
                                                    reminder_time = intent.get("params", {}).get("reminder_time")
                                                
                                                # Валидация reminder_time
                                                has_time = intent.get("params", {}).get("has_time", False)
                                                logger.info(f"[ADD TASK] reminder_time={reminder_time}, has_time={has_time}")
                                                
                                                # БЛОКИРУЕМ создание задач без времени
                                                if not reminder_time or reminder_time in ['', 'None', 'null', '@unknown']:
                                                    logger.warning(f"[ADD TASK] BLOCKED - no valid reminder_time provided")
                                                    tool_results.append({"function": func_name, "result": "NEED_TIME"})
                                                else:
                                                    # Вызываем add_task только с валидным временем
                                                    result = add_task(
                                                        title=args.get("title", args.get("task_title", "Задача")),
                                                        description=args.get("description", ""),
                                                        reminder_time=reminder_time,
                                                        user_id=user_id,
                                                        session=None,
                                                    )
                                                    tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "complete_task":
                                                task_title = args.get("task_title") or intent.get("params", {}).get("task_title")
                                                result = complete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=task_title,
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "list_tasks":
                                                result = list_tasks(user_id=user_id, session=None)
                                                # Add delegation instructions if this is for delegation
                                                if intent.get("params", {}).get("for_delegation"):
                                                    target_user = intent.get("params", {}).get("target_user", "")
                                                    result += f"\n\nЧтобы делегировать задачу, скажите: 'делегировать задачу [ID или название] пользователю {target_user} дедлайн [время]'"
                                                    result += f"\nНапример: 'делегировать задачу 1 пользователю {target_user} дедлайн завтра в 15:00'"
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "find_partners":
                                                result = find_partners(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "update_profile":
                                                result = update_profile(
                                                    city=args.get("city"),
                                                    company=args.get("company"),
                                                    position=args.get("position"),
                                                    interests=args.get("interests"),
                                                    user_id=user_id,
                                                    session=None,
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
                                                result = delete_all_tasks(user_id=user_id, session=None)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "delete_task":
                                                result = delete_task(
                                                    task_id=args.get("task_id"),
                                                    task_title=args.get("task_title"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "edit_task":
                                                result = edit_task(
                                                    task_id=args.get("task_id"),
                                                    title=args.get("title"),
                                                    description=args.get("description"),
                                                    reminder_time=args.get("reminder_time"),
                                                    user_id=user_id,
                                                    session=None,
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "check_subscription_status":
                                                result = check_subscription_status(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "create_subscription_payment":
                                                result = create_subscription_payment(user_id=user_id)
                                                tool_results.append({"function": func_name, "result": result})

                                            elif func_name == "brainstorm_ideas":
                                                result = brainstorm_ideas(
                                                    topic=args.get("topic"),
                                                    num_ideas=args.get("num_ideas", 5),
                                                    user_id=user_id
                                                )
                                                tool_results.append({"function": func_name, "result": result})

                                            else:
                                                logger.warning(f"[TOOL CALL] Unknown function: {func_name}")
                                                tool_results.append(
                                                    {"function": func_name, "result": f"Неизвестная функция: {func_name}"}
                                                )

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
                                            messages.append({"role": "user", "content": original_message})
                                            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
                                            messages.append({"role": "user", "content": "Задача НЕ создана - пользователь не указал время."})
                                            
                                            data = {
                                                "model": "deepseek-chat",
                                                "messages": messages,
                                                "temperature": 0.7,
                                                "max_tokens": 150
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
                                            match = re.search(r"Добавлена задача '([^']+)' \(ID: (\d+)\)", result_text)
                                            if match:
                                                title = match.group(1)
                                                task_id = int(match.group(2))

                                                # Получаем рекомендации и релевантные контакты из базы данных
                                                from models import Session as SessionModel
                                                session_db = SessionModel()
                                                try:
                                                    task = session_db.query(Task).filter_by(id=task_id).first()
                                                    recommendations = []
                                                    if task and task.recommendations:
                                                        try:
                                                            recommendations = json.loads(task.recommendations)
                                                        except Exception as e:
                                                            logger.warning(f"Could not parse recommendations: {e}")
                                                            pass

                                                    # КРИТИЧНО: Автоматически находим релевантные контакты для задачи
                                                    relevant_contacts = []
                                                    partners_result = find_partners(user_id=user_id, session=session_db)
                                                    if partners_result and "Нашёл подходящих" in partners_result:
                                                        # Извлекаем @username из результата
                                                        import re as re_module
                                                        usernames = re_module.findall(r'@(\w+)', partners_result)
                                                        relevant_contacts = usernames[:2]  # Максимум 2 контакта
                                                    
                                                    # ИСПРАВЛЕНО: Передаем контекст для AI с контактами
                                                    context_parts = [f"TASK_CREATED: title='{title}', id={task_id}"]
                                                    if relevant_contacts:
                                                        context_parts.append(f"RELEVANT_CONTACTS: {', '.join(['@' + u for u in relevant_contacts])}")
                                                    
                                                    natural_responses.append(" | ".join(context_parts))
                                                finally:
                                                    session_db.close()
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Завершена задача" in result_text:
                                            match = re.search(r"Завершена задача '([^']+)'", result_text)
                                            if match:
                                                title = match.group(1)
                                                # Передаем контекст для AI генерации естественного ответа
                                                natural_responses.append(f"TASK_COMPLETED: title='{title}'")
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Задачи:" in result_text:
                                            # Для list_tasks добавляем умный анализ вместо простого вывода
                                            natural = enrich_task_list_with_insights(result_text, user_id)
                                            natural_responses.append(natural)

                                        elif (
                                            "Найдены партнеры:" in result_text
                                            or "партнеры найдены" in result_text.lower()
                                        ):
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
                                                # Общий случай
                                                natural_responses.append("PROFILE_UPDATED: general")

                                        elif "Задача" in result_text and "делегирована" in result_text:
                                            natural_responses.append("TASK_DELEGATED")

                                        elif "Удалены все задачи" in result_text:
                                            natural_responses.append("ALL_TASKS_DELETED")

                                        elif "Задача" in result_text and "удалена" in result_text:
                                            match = re.search(r"Задача '([^']+)' удалена", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural_responses.append(f"TASK_DELETED: title='{title}'")
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Идеи для темы" in result_text:
                                            natural = f"{result_text}\n\nНадеюсь, эти идеи помогут! Если нужно углубить какую-то или сгенерировать больше вариантов - дай знать."
                                            natural_responses.append(natural)

                                        else:
                                            natural_responses.append(result_text)

                                    # Для list_tasks анализ уже добавлен выше

                                    final_content = "\n".join(natural_responses)

                                    # КРИТИЧНО: AI должен сформировать ответ по единому промпту для ВСЕХ случаев
                                    if final_content:
                                        # Получаем профиль пользователя для контекста
                                        profile_context = ""
                                        if db_session and user_id:
                                            try:
                                                user = db_session.query(User).filter_by(telegram_id=user_id).first()
                                                if user and user.profile:
                                                    prof = user.profile
                                                    profile_data = []
                                                    if prof.city: profile_data.append(f"город: {prof.city}")
                                                    if prof.company: profile_data.append(f"компания: {prof.company}")
                                                    if prof.position: profile_data.append(f"должность: {prof.position}")
                                                    if prof.goals: profile_data.append(f"цели: {prof.goals}")
                                                    if prof.current_plans: profile_data.append(f"планы: {prof.current_plans}")
                                                    if profile_data:
                                                        profile_context = f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {', '.join(profile_data)}"
                                            except Exception as e:
                                                logger.warning(f"Failed to get profile context: {e}")
                                        
                                        # ТОЛЬКО результаты и данные - БЕЗ инструкций, единый промпт сам всё знает
                                        tool_context_msg = f"СТРОГО СОБЛЮДАЙ: показывай ТОЛЬКО реальные задачи из предоставленных данных, НЕ выдумывай и НЕ придумывай задачи!\n\n{final_content}{profile_context}"
                                        
                                        # Добавляем контекст в messages
                                        messages.append({"role": "user", "content": original_message})
                                        messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
                                        messages.append({"role": "user", "content": tool_context_msg})
                                        
                                        # Запрашиваем естественный ответ от AI
                                        data = {
                                            "model": "deepseek-chat",
                                            "messages": messages,
                                            "temperature": 0.7,
                                            "max_tokens": 400  # Увеличено для вариантов действий
                                        }
                                        
                                        final_content = "Действие выполнено"  # Инициализация на случай всех ошибок
                                        max_retries = 3
                                        for attempt in range(max_retries):
                                            try:
                                                async with aiohttp.ClientSession() as ai_session:
                                                    async with ai_session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=40)) as ai_response:
                                                        if ai_response.status == 200:
                                                            ai_result = await ai_response.json()
                                                            final_content = ai_result["choices"][0]["message"]["content"].strip()
                                                            logger.info(f"[AI NATURAL RESPONSE] Generated natural response after tool calls")
                                                            break
                                                        else:
                                                            logger.warning(f"[AI NATURAL RESPONSE] Status {ai_response.status}, attempt {attempt+1}/{max_retries}")
                                                            if attempt == max_retries - 1:
                                                                # Последняя попытка - упрощённый запрос
                                                                try:
                                                                    simple_msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Действие выполнено. {profile_context}"}]
                                                                    simple_data = {"model": "deepseek-chat", "messages": simple_msg, "temperature": 0.7, "max_tokens": 300}
                                                                    async with aiohttp.ClientSession() as simple_session:
                                                                        async with simple_session.post(url, headers=headers, json=simple_data, timeout=aiohttp.ClientTimeout(total=30)) as simple_response:
                                                                            if simple_response.status == 200:
                                                                                simple_result = await simple_response.json()
                                                                                final_content = simple_result["choices"][0]["message"]["content"].strip()
                                                                except Exception as simple_error:
                                                                    logger.error(f"Simple retry failed: {simple_error}")
                                            except Exception as e:
                                                logger.warning(f"[AI NATURAL RESPONSE] Attempt {attempt+1} failed: {e}")
                                                if attempt == max_retries - 1:
                                                    # Крайний случай - минимальный запрос
                                                    try:
                                                        minimal_msg = [{"role": "user", "content": "Действие выполнено. Дай развёрнутый естественный ответ (2-3 абзаца)."}]
                                                        minimal_data = {"model": "deepseek-chat", "messages": minimal_msg, "temperature": 0.7, "max_tokens": 200}
                                                        async with aiohttp.ClientSession() as minimal_session:
                                                            async with minimal_session.post(url, headers=headers, json=minimal_data, timeout=aiohttp.ClientTimeout(total=20)) as minimal_response:
                                                                if minimal_response.status == 200:
                                                                    minimal_result = await minimal_response.json()
                                                                    final_content = minimal_result["choices"][0]["message"]["content"].strip()
                                                    except Exception as minimal_error:
                                                        logger.error(f"Minimal retry failed: {minimal_error}")
                                                else:
                                                    await asyncio.sleep(0.5)  # Пауза между попытками

                                    # Проверяем качество финального ответа после tool calls
                                    if not final_content or len(final_content.strip()) < 10:
                                        logger.warning(f"[TOOL RESPONSE] Final content too short or empty: '{final_content}', using fallback")
                                        # Создаем fallback ответ на основе результатов tool calls
                                        fallback_parts = []
                                        for action in tool_results:
                                            result_text = action["result"]
                                            func_name = action["function"]
                                            
                                            if "Добавлена задача" in result_text:
                                                match = re.search(r"Добавлена задача '([^']+)' \(ID: (\d+)\)", result_text)
                                                if match:
                                                    title = match.group(1)
                                                    task_id = match.group(2)
                                                    fallback_parts.append(f"✅ Задача '{title}' создана (ID: {task_id})")
                                                    if "с напоминанием на" in result_text:
                                                        match_time = re.search(r"с напоминанием на ([^)]+)", result_text)
                                                        if match_time:
                                                            time_str = match_time.group(1)
                                                            fallback_parts.append(f"🔔 Напоминание установлено на {time_str}")
                                            elif "Завершена задача" in result_text:
                                                match = re.search(r"Завершена задача '([^']+)'", result_text)
                                                if match:
                                                    title = match.group(1)
                                                    fallback_parts.append(f"✅ Задача '{title}' отмечена как выполненная")
                                            elif "Удалены все задачи" in result_text:
                                                fallback_parts.append("🗑️ Все задачи удалены")
                                            elif "Задача" in result_text and "делегирована" in result_text:
                                                fallback_parts.append("📤 Задача делегирована")
                                            else:
                                                fallback_parts.append(result_text)
                                        
                                        if fallback_parts:
                                            final_content = "\n".join(fallback_parts)
                                        else:
                                            final_content = "Действие выполнено успешно!"

                                    # Пост-обработка для улучшения качества ответа
                                    final_content = post_process_response(final_content, user_id, db_session)

                                    logger.info(
                                        f"[TOOL CALLS] Processed {
                                            len(tool_results)} tool calls, returning natural response")
                                    return final_content
                            else:
                                # tool_calls были проигнорированы для вопроса совета, переходим к обычной обработке
                                pass

                    # Все запросы обрабатывает AI, без принудительных триггеров
                    logger.info("[AI ONLY] All requests handled by AI without forced triggers")

                    # SMART FALLBACK: Проверяем, нужно ли применить умный fallback (use improved version if available)
                    # Определяем content заранее для использования в fallback
                    original_content = message_response.get("content", "")
                    content = original_content
                    content = replace_placeholders(content, user_now, current_time_str)

                    try:
                        if PROMPTS_V2_AVAILABLE:
                            fallback_result = improved_fallback(
                                intent, tool_calls if 'tool_calls' in locals() else None,
                                content, original_message, user_id
                            )
                            logger.info(f"[PROMPTS V2] Fallback actions: {len(fallback_result)}")
                        else:
                            fallback_result = smart_fallback_handler(original_message, mentions_str, user_id, content)
                            logger.info(f"[LEGACY] Fallback actions: {len(fallback_result)}")
                        logger.debug(
                            f"[FALLBACK] Fallback result: {len(fallback_result) if fallback_result else 0} actions"
                        )
                        if fallback_result:
                            logger.info(
                                f"[SMART FALLBACK] Applied {len(fallback_result)} fallback actions for user {user_id}"
                            )

                            # Обрабатываем результаты fallback аналогично tool calls
                            natural_responses = []
                            for action in fallback_result:
                                result_text = action["result"]
                                func_name = action["function"]

                                if "Добавлена задача" in result_text:
                                    match = re.search(
                                        r"Добавлена задача '([^']+)' \(ID: \d+\) с напоминанием на ([^)]+)", result_text
                                    )
                                    if match:
                                        title = match.group(1)
                                        time_str = match.group(2)
                                        natural = f'Отлично, добавил задачу "{title}" с напоминанием на {time_str}.'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "Завершена задача" in result_text:
                                    match = re.search(r"Завершена задача '([^']+)'", result_text)
                                    if match:
                                        title = match.group(1)
                                        natural = f'Отлично, отметил задачу "{title}" как выполненную! 👍'
                                        natural_responses.append(natural)
                                    else:
                                        natural_responses.append(result_text)

                                elif "Задачи:" in result_text:
                                    # Не добавляем сразу, анализ будет добавлен отдельно
                                    pass

                                elif "Удалены все задачи" in result_text:
                                    natural = (
                                        "Удалил все твои задачи. Теперь список пуст - можно начинать с чистого листа!"
                                    )
                                    natural_responses.append(natural)

                                elif "Задача" in result_text and "делегирована" in result_text:
                                    natural = "Отлично, задача делегирована! Я уведомлю получателя."
                                    natural_responses.append(natural)

                                else:
                                    natural_responses.append(result_text)

                            # Проверяем, есть ли list_tasks в результатах fallback
                            has_list_tasks = any(action["function"] == "list_tasks" for action in fallback_result)
                            list_tasks_result = None
                            if has_list_tasks:
                                for action in fallback_result:
                                    if action["function"] == "list_tasks":
                                        list_tasks_result = action["result"]
                                        break

                            # Для list_tasks просто добавляем результат - главный промпт уже содержит все правила
                            if has_list_tasks and list_tasks_result:
                                natural_responses.append(list_tasks_result)

                            # Формируем финальный контент
                            final_content = "\n".join(natural_responses)

                            # Enforcement отключен - AI должен отвечать естественно
                            # intent_type = "list_tasks" if has_list_tasks else None
                            # final_content = await enforce_prompt_compliance(
                            #     final_content, intent_type, user_id, context,
                            #     system_prompt, messages, url, headers
                            # )

                            # Пост-обработка для улучшения качества ответа
                            final_content = post_process_response(final_content, user_id, db_session)

                            return final_content
                    except Exception as e:
                        logger.error(f"[SMART FALLBACK] Error in fallback handler: {e}")

                    # Если forced calls не сработали, обрабатываем обычный ответ AI
                    # Обрабатываем обычный ответ AI без tool calls
                    logger.info("[TOOL CALLS] Tool calls completed, 0 results. Generating natural response...")

                    # Для обычных ответов ТОЛЬКО заменяем плейсхолдеры, без дополнительной очистки
                    content = replace_placeholders(content, user_now, current_time_str)

                    # КРИТИЧЕСКАЯ ПРОВЕРКА: если content пустой или слишком короткий
                    if not content or len(content.strip()) < 3:
                        logger.debug(
                            f"[RESPONSE] Content is empty or too short: '{content}', len={len(content.strip())}"
                        )
                        logger.warning(f"[EMPTY RESPONSE] Original: '{original_content[:100]}...', returning original")
                        content = original_content.strip()
                        if not content:
                            logger.warning("[RETRY] Response empty, retrying with explicit instruction")
                            retry_system = (
                                system_prompt +
                                "\n\nКРИТИЧЕСКИ ВАЖНО:\n1. НЕ возвращай JSON, code blocks или технические теги\n2. Отвечай ТОЛЬКО обычным текстом\n3. Если создал задачу - скажи об этом и предложи найти партнёра\n4. Минимум 20 слов в ответе\n5. Будь дружелюбным и конкретным!")

                            retry_messages = [{"role": "system", "content": retry_system}]
                            if context:
                                for item in context:
                                    if "user" in item:
                                        retry_messages.append({"role": "user", "content": item["user"]})
                                    if "assistant" in item:
                                        retry_messages.append({"role": "assistant", "content": item["assistant"]})
                            retry_messages.append({"role": "user", "content": original_message})

                            async with aiohttp.ClientSession() as retry_session:
                                async with retry_session.post(
                                    url,
                                    headers=headers,
                                    json={
                                        "model": "deepseek-reasoner",
                                        "messages": retry_messages,
                                        "temperature": 0.3,
                                    },
                                    timeout=aiohttp.ClientTimeout(total=120),
                                ) as retry_response:
                                    retry_result = await retry_response.json()
                                    retry_content = retry_result["choices"][0]["message"]["content"]
                                    retry_content = replace_placeholders(retry_content, user_now, current_time_str)
                                    content = retry_content.strip()
                                    logger.info(f"[RETRY] Got retry content: '{content[:100]}...'")
                                    if retry_content and len(retry_content.strip()) >= 3:
                                        content = retry_content
                                    else:
                                        content = "Хорошо, продолжим работу!"
                        else:
                            logger.info(f"[RECOVERED] Using original content: '{content[:100]}...'")

                    # Если все еще пустой после retry
                    if not content:
                        content = "Хорошо, продолжим работу!"

                    # ИЗБЫТОЧНЫЕ ОБРАБОТКИ УБРАНЫ:
                    # - enrich_response_with_engagement (AI сам задает вопросы через промпт)
                    # - validate_response_compliance (ничего не делает, enforce отключен)
                    # - clean_technical_details (только для сгенерированных ответов, не для основного AI)

                    # Метрики качества ответа
                    response_quality = {
                        'length': len(content),
                        'has_questions': '?' in content,
                        'has_tools': bool(tool_calls),
                        'intent_type': intent.get('type', 'unknown'),
                        'user_id': user_id
                    }
                    logger.info(f"[RESPONSE QUALITY] {response_quality}")

                    # Обработка ошибок: если ответ слишком короткий или пустой, дать fallback
                    if not content or len(content.strip()) < 10:
                        logger.warning("[FALLBACK] Empty or too short response, using fallback")
                        content = improved_fallback(intent, tool_calls, content, message, user_id)

                    # ДОПОЛНИТЕЛЬНЫЕ АНАЛИЗЫ ПОЛНОСТЬЮ УБРАНЫ ДЛЯ ЛАКОНИЧНОСТИ
                    # Никаких эмоций, рекомендаций, дубликатов - только чистый ответ AI

                    # Пост-обработка для улучшения качества ответа
                    content = post_process_response(content, user_id, db_session)

                    return content

            except Exception as e:
                logger.error(f"Error in chat_with_ai: {e}")
                logger.error(f"Error type: {type(e).__name__}")
                logger.error(f"Traceback:\n{traceback.format_exc()}")
                # Добавляем номер строки для отладки
                tb = traceback.extract_tb(e.__traceback__)
                if tb:
                    last_frame = tb[-1]
                    logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
                return f"Ошибка: {str(e)} [v2]"

    except Exception as e:
        import traceback

        logger.error(f"Error in chat_with_ai: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        # Добавляем номер строки для отладки
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last_frame = tb[-1]
            logger.error(f"Error location: {last_frame.filename}:{last_frame.lineno} in {last_frame.name}")
        return f"Ошибка: {str(e)} [v2]"


async def generate_reminder(user_id, task_title):
    """Генерирует текст напоминания о задаче"""
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
        current_time_str = user_now.strftime("%H:%M")
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

        # СПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ НАПОМИНАНИЙ:
        system_prompt = f"{base_prompt}\n\nСПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ НАПОМИНАНИЙ:\n"
        system_prompt += "Будь мотивирующим и поддерживающим\n"
        system_prompt += "Давай конкретные практические советы\n"
        system_prompt += "Учитывай время дня и контекст пользователя\n"
        system_prompt += "Предлагай 2-3 варианта подхода к задаче\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Завершай вопросом для продолжения диалога\n"
        system_prompt += "Учитывай информацию из памяти пользователя\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Сгенерируй персонализированное напоминание о задаче '{task_title}'. Учитывай контекст пользователя, давай практические советы, будь мотивирующим и заверши вопросом."},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
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
                    logger.error(f"Failed to generate reminder: status {response.status}")
                    return f"Привет! Напоминаю о задаче: {task_title}. Как продвигается?"
    except Exception as e:
        logger.error(f"Error in generate_reminder: {e}")
        return f"Пора заняться задачей: {task_title}. Все получится! 💪"


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
        current_time_str = user_now.strftime("%H:%M")
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

        # СПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ ПРОВЕРКИ РЕЗУЛЬТАТОВ:
        system_prompt = f"{base_prompt}\n\nСПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ ПРОВЕРКИ РЕЗУЛЬТАТОВ ЗАДАЧ:\n"
        system_prompt += "Уточни результат выполнения задачи\n"
        system_prompt += "Спроси о времени, затраченном на выполнение\n"
        system_prompt += "Интересуйся сложностями и уроками\n"
        system_prompt += "Предлагай улучшения для будущих задач\n"
        system_prompt += "Будь поддерживающим и анализируй прогресс\n"
        system_prompt += "2-4 предложения, живое общение\n"
        system_prompt += "Завершай вопросом для продолжения диалога\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Спроси о результате выполнения задачи '{task_title}'. Узнай о времени, сложностях, улучшениях.",
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
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
                    return f"Привет! Как прошло выполнение задачи '{task_title}'? Поделись результатами! 😊"
    except Exception as e:
        logger.error(f"Error in generate_result_check: {e}")
        return f"Как успехи с задачей '{task_title}'? Расскажи, что получилось!"


async def generate_proactive_message(user_id):
    """Генерирует проактивное сообщение, если нет задач на ближайший час"""
    try:
        # Получить память пользователя, планы других и текущие задачи
        user_memory = ""
        plans_info = ""
        tasks_info = ""
        if user_id:
            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if user is None:
                db_session.close()
                # Если пользователь не найден - генерируем приветствие через AI
                try:
                    url = "https://api.deepseek.com/v1/chat/completions"
                    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                    msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Новый пользователь. Создай короткое приветствие."}]
                    data = {"model": DEEPSEEK_MODEL, "messages": msg, "temperature": 0.8, "max_tokens": 50}
                    async with aiohttp.ClientSession() as sess:
                        async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                result = await resp.json()
                                return result["choices"][0]["message"]["content"].strip()
                except Exception:
                    pass
                return "Привет! 👋"
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            # Получить профиль пользователя
            user_profile = db_session.query(UserProfile).filter_by(user_id=user.id).first()
            if user_profile and user_profile.interests:
                # Найти планы других пользователей, совпадающие с интересами
                profiles = db_session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
                tips = []
                for p in profiles:
                    if p.current_plans and p.contact_info != f"user{user_id}":
                        for interest in user_profile.interests.split(","):
                            interest_words = interest.strip().lower().split()
                            if any(word in p.current_plans.lower() for word in interest_words):
                                tips.append(
                                    f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} - может быть интересно с твоими интересами в {interest.strip()}."
                                )
                                break
                if tips:
                    plans_info = "\nПланы людей: " + " ".join(tips[:2])
            # Получить текущие задачи
            tasks = db_session.query(Task).filter_by(user_id=user.id).all()
            pending_tasks = [t.title for t in tasks if t.status in ["pending", "in_progress"]]
            if pending_tasks:
                tasks_info = f"\nТекущие невыполненные задачи: {', '.join(pending_tasks[:3])}"
            db_session.close()

        # Используем единый унифицированный промпт для всех AI-сообщений
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(
            user_now,
            current_time_str,
            user_username,
            mentions_str,
            user_memory +
            plans_info +
            tasks_info)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        # Контекстный промпт для проактивного сообщения
        proactive_context = "Проактивное сообщение"
        if tasks_info:
            proactive_context = f"У пользователя есть задачи{tasks_info}. Проанализируй их и предложи конкретные действия или мотивацию для их выполнения."
        elif plans_info:
            proactive_context = f"У пользователя нет активных задач, но есть релевантные планы других{plans_info}. Предложи полезные связи или возможности."
        else:
            proactive_context = "У пользователя нет задач на ближайший час и нет релевантных планов других. Предложи создать полезную задачу или проверь, не забыл ли он что-то важное на сегодня."
        
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": proactive_context,
            },
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
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
                    # Проактивные сообщения уже вовлекающие, но можно усилить
                    content = enrich_response_with_engagement(content, user_id, "")

                    # Проверяем и принуждаем соблюдение промпта
                    is_compliant, issues = validate_response_compliance(content, "proactive")
                    if not is_compliant:
                        logger.warning(f"[COMPLIANCE] Proactive message response not compliant: {issues}")
                        # Принуждаем исправление - функция временно отключена
                        # content = await enforce_prompt_compliance(
                        #     content, "proactive", user_id, None, system_prompt, messages, url, headers
                        # )

                    return content
                else:
                    logger.error(f"Failed to generate proactive message: status {response.status}")
                    # Retry через упрощённый промпт
                    retry_msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Проактивное сообщение."}]
                    retry_data = {"model": "deepseek-chat", "messages": retry_msg, "temperature": 0.7, "max_tokens": 200}
                    async with session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=20)) as retry_resp:
                        if retry_resp.status == 200:
                            retry_result = await retry_resp.json()
                            return retry_result["choices"][0]["message"]["content"].strip()
                    return "Как дела? Чем могу помочь?"
    except Exception as e:
        logger.error(f"Error in generate_proactive_message: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Крайний случай - генерируем через AI с минимальным промптом
        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            context = "Ошибка генерации."
            if tasks_info:
                context = f"У пользователя есть задачи{tasks_info}. Создай короткий вопрос о задачах."
            elif user_memory:
                context = "У пользователя есть история общения. Создай короткий вопрос о прогрессе."
            msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": context}]
            data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.8, "max_tokens": 60}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
        # Более разнообразные fallback сообщения вместо повторяющегося "Привет! 👋"
        import random
        fallback_messages = [
            "Как твои дела сегодня?",
            "Чем занимаешься?",
            "Есть что-нибудь интересное?",
            "Как настроение?",
            "Что нового?",
            "Чем могу помочь?",
            "Как продвигаются дела?",
            "Что планируешь сегодня?",
            "Есть вопросы или нужна помощь?",
            "Как успехи?"
        ]
        return random.choice(fallback_messages)


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
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

        # УНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:
        system_prompt = f"{base_prompt}\n\nУНИФИЦИРОВАННЫЕ ПРАВИЛА ДЛЯ ВСЕХ AI-СООБЩЕНИЙ:\n"
        system_prompt += "Всегда заканчивай вопросом для продолжения диалога\n"
        system_prompt += "Анализируй ситуацию и давай конкретные рекомендации\n"
        system_prompt += "Будь персонализированным, используй информацию о пользователе\n"
        system_prompt += "Демонстрируй ценность: показывай как экономишь время, предотвращаешь проблемы\n"
        system_prompt += "2-4 предложения, живое общение как с другом\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её\n"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"},
        ]

        data = {"model": "deepseek-reasoner", "messages": messages}
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
                    retry_data = {"model": "deepseek-chat", "messages": retry_msg, "temperature": 0.7, "max_tokens": 200}
                    async with session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=20)) as retry_resp:
                        if retry_resp.status == 200:
                            retry_result = await retry_resp.json()
                            return retry_result["choices"][0]["message"]["content"].strip()
                    # Генерируем fallback через AI
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Время подвести итоги дня. Создай короткое напоминание."}]
                        data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.8, "max_tokens": 50}
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
            data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.8, "max_tokens": 50}
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
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"
        mentions_str = ""

        base_prompt = get_optimized_prompt_final(user_now, current_time_str, user_username, mentions_str, user_memory)

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
                "role": "user", "content": f"Напомни о просроченных задачах: {
                    ', '.join(task_titles)}. {tone_instruction} Предложи варианты решения.", }, ]

        data = {"model": "deepseek-reasoner", "messages": messages}
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
                    retry_data = {"model": "deepseek-chat", "messages": retry_msg, "temperature": 0.7, "max_tokens": 200}
                    async with session.post(url, headers=headers, json=retry_data, timeout=aiohttp.ClientTimeout(total=20)) as retry_resp:
                        if retry_resp.status == 200:
                            retry_result = await retry_resp.json()
                            return retry_result["choices"][0]["message"]["content"].strip()
                    # Генерируем сообщение через AI с контекстом просроченных задач
                    try:
                        url = "https://api.deepseek.com/v1/chat/completions"
                        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                        msg = [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Просроченные задачи пользователя: {overdue_info}. Создай короткое напоминание."}]
                        data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.8, "max_tokens": 80}
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
            data = {"model": "deepseek-chat", "messages": msg, "temperature": 0.8, "max_tokens": 50}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return res["choices"][0]["message"]["content"].strip()
        except:
            pass
        return "Задачи ждут внимания 📌"


# Функции для работы с задачами
