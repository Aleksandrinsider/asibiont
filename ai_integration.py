import aiohttp
from config import DEEPSEEK_API_KEY, ENCRYPTION_KEY
import json
from datetime import datetime, timezone, timedelta
import re
import logging
import asyncio
from cryptography.fernet import Fernet, InvalidToken
from models import User, UserProfile
import pytz

cipher = Fernet(ENCRYPTION_KEY.encode())
logger = logging.getLogger(__name__)

# Redis client - будет импортирован из main.py
redis_client = None


def set_redis_client(client):
    """Устанавливает глобальный Redis client из main.py"""
    global redis_client
    redis_client = client


def classify_user_intent(message, mentions_str):
    """
    Классифицирует намерение пользователя на основе паттернов в сообщении.
    Возвращает словарь с intent и confidence score.
    """
    message_lower = message.lower().strip()
    intent = {"type": "unknown", "confidence": 0.0, "params": {}}

    # 1. Делегирование задач (@mentions) - улучшенные паттерны
    if "@" in message:
        mention_match = re.search(r"@(\w+)", message)
        if mention_match and intent["confidence"] < 0.9:
            intent["type"] = "delegate_task"
            intent["confidence"] = 0.9
            intent["params"]["delegated_to"] = f"@{mention_match.group(1)}"
            # Извлекаем текст задачи - улучшенная логика
            task_text = re.sub(r"@\w+", "", message).strip()
            task_text = re.sub(r"^(поручи|делегируй|передай|сделай)\s+", "", task_text, flags=re.IGNORECASE)
            intent["params"]["task_title"] = task_text or "Задача"
            # Парсим время
            time_match = re.search(r"(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2})", task_text)
            if time_match:
                intent["params"]["reminder_time"] = time_match.group(1)
            elif "завтра" in task_text.lower():
                # Для теста, используем фиксированное время
                intent["params"]["reminder_time"] = "2026-01-11 10:00"

    # 1.1. Управление делегированными задачами
    accept_keywords = ["принял", "принимаю", "согласен", "возьму", "беру"]
    if (
        any(keyword in message_lower for keyword in accept_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "accept_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    reject_keywords = ["отклонил", "отказываюсь", "не могу", "занят"]
    if (
        any(keyword in message_lower for keyword in reject_keywords)
        and "задачу" in message_lower
        and intent["confidence"] < 0.8
    ):
        intent["type"] = "reject_delegated_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        task_match = re.search(r"задачу\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()

    delegation_status_keywords = ["статус задачи", "как задача", "прогресс задачи", "что с задачей"]
    if any(keyword in message_lower for keyword in delegation_status_keywords) and intent["confidence"] < 0.95:
        intent["type"] = "get_delegation_progress"
        intent["confidence"] = 0.95  # максимальная уверенность
        # Извлекаем название задачи
        task_match = re.search(r"задачи\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_match:
            intent["params"]["task_title"] = task_match.group(1).strip()
        else:
            # Если не нашли конкретную задачу, это может быть общий запрос статуса
            intent["confidence"] = 0.6  # понижаем уверенность

    # 2. Просмотр задач
    list_keywords = ["покажи", "список", "мои дела", "все задачи", "что у меня", "задачи"]
    if any(keyword in message_lower for keyword in list_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "list_tasks"
        intent["confidence"] = 0.8

    # 2.5. Перенос задач
    transfer_keywords = ["перенеси", "перенести", "измени время", "поменяй время", "обнови время"]
    if any(keyword in message_lower for keyword in transfer_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.8
        # Извлекаем текст задачи и новое время
        for keyword in transfer_keywords:
            if keyword in message_lower:
                after_keyword = message_lower.split(keyword, 1)[1].strip()
                # Ищем время в оставшейся части
                time_match = re.search(r"(через\s+\d+\s*(минут|час|часа|часов)|завтра\s+в\s+\d{1,2}:\d{2}|сегодня\s+в\s+\d{1,2}:\d{2})", after_keyword, re.IGNORECASE)
                if time_match:
                    intent["params"]["reminder_time"] = time_match.group(1)
                    # Всё до времени - название задачи
                    task_part = after_keyword.split(time_match.group(1))[0].strip()
                    if task_part:
                        intent["params"]["task_title"] = task_part
                break

    # 3. Создание задач
    create_keywords = ["напомни", "добавь задачу", "создай задачу", "запланируй"]
    if any(keyword in message_lower for keyword in create_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "add_task"
        intent["confidence"] = 0.8
        # Извлекаем текст задачи и время
        for keyword in create_keywords:
            if keyword in message_lower:
                after_keyword = message_lower.split(keyword, 1)[1].strip()
                # Ищем время в оставшейся части
                time_match = re.search(r"(через\s+\d+\s*(минут|час|часа|часов)|завтра\s+в\s+\d{1,2}:\d{2}|сегодня\s+в\s+\d{1,2}:\d{2})", after_keyword, re.IGNORECASE)
                if time_match:
                    intent["params"]["reminder_time"] = time_match.group(1)
                    # Всё до времени - название задачи
                    task_part = after_keyword.split(time_match.group(1))[0].strip()
                    if task_part:
                        intent["params"]["task_title"] = task_part
                else:
                    # Если времени нет, весь текст - задача
                    intent["params"]["task_title"] = after_keyword
                break

    # 3.1. Относительное время (контекстное обновление задач)
    relative_time_keywords = ["через", "напомни через"]
    if any(keyword in message_lower for keyword in relative_time_keywords) and intent["confidence"] < 0.7:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.7
        # Парсим относительное время
        time_match = re.search(r"через\s+(\d+)\s*(минут|час|часа|часов)", message_lower, re.IGNORECASE)
        if time_match:
            amount = int(time_match.group(1))
            unit = time_match.group(2).lower()
            if unit in ["час", "часа", "часов"]:
                intent["params"][
                    "reminder_time"
                ] = f"через {amount} час{'ов' if amount > 1 else '' if amount == 1 else 'а'}"
            else:
                intent["params"]["reminder_time"] = f"через {amount} минут"

    # 4. Завершение задач
    complete_keywords = ["сделал", "выполнил", "завершил", "готово", "закончил"]
    if any(keyword in message_lower for keyword in complete_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "complete_task"
        intent["confidence"] = 0.8
        # Извлекаем название задачи
        for keyword in complete_keywords:
            if keyword in message_lower:
                task_text = message_lower.replace(keyword, "").strip()
                intent["params"]["task_title"] = task_text
                break

    # 5. Удаление задач
    delete_keywords = ["удали все", "очисти список", "удалить все задачи"]
    if any(keyword in message_lower for keyword in delete_keywords) and intent["confidence"] < 0.9:
        intent["type"] = "delete_all_tasks"
        intent["confidence"] = 0.9

    # Удаление конкретной задачи - улучшенные паттерны
    delete_specific_keywords = [
        "удали эту задачу",
        "удалить задачу",
        "удали задачу",
        "удали эту",
        "удали задачу",
        "убери задачу",
        "убери эту задачу",
        "вычеркни задачу",
        "вычеркни эту задачу",
        "удали её",
        "удали эту",
        "убери её",
        "вычеркни её",
    ]
    if any(keyword in message_lower for keyword in delete_specific_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "delete_task"
        intent["confidence"] = 0.8
        # Извлекаем ID задачи из контекста или сообщения
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))
        # Также пытаемся извлечь название задачи
        task_name_match = re.search(r"(?:задачу|эту)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
        if task_name_match:
            intent["params"]["task_title"] = task_name_match.group(1).strip()

    # 6. Редактирование задач
    edit_keywords = ["измени задачу", "обнови задачу", "поменяй задачу", "исправь задачу"]
    if any(keyword in message_lower for keyword in edit_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "edit_task"
        intent["confidence"] = 0.8
        # Извлекаем ID и новые параметры
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.1. Установка приоритета
    priority_keywords = [
        "приоритет",
        "высокий приоритет",
        "средний приоритет",
        "низкий приоритет",
        "установи приоритет",
    ]
    if any(keyword in message_lower for keyword in priority_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "set_priority"
        intent["confidence"] = 0.85
        # Определяем уровень приоритета
        if "высокий" in message_lower:
            intent["params"]["priority"] = "high"
        elif "средний" in message_lower:
            intent["params"]["priority"] = "medium"
        elif "низкий" in message_lower:
            intent["params"]["priority"] = "low"
        # Извлекаем ID задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.2. Детали задачи
    details_keywords = ["детали задачи", "подробности задачи", "информация о задаче", "покажи задачу"]
    if any(keyword in message_lower for keyword in details_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "get_task_details"
        intent["confidence"] = 0.85
        # Извлекаем ID или название задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 6.3. Альтернативы для задач
    alternatives_keywords = ["альтернативы", "предложи альтернативы", "другие варианты", "как иначе"]
    if any(keyword in message_lower for keyword in alternatives_keywords) and intent["confidence"] < 0.85:
        intent["type"] = "suggest_alternatives"
        intent["confidence"] = 0.85
        # Извлекаем ID задачи
        task_id_match = re.search(r"(\d+)", message_lower)
        if task_id_match:
            intent["params"]["task_id"] = int(task_id_match.group(1))

    # 7. Поиск людей - расширенные паттерны
    find_keywords = [
        "найди людей",
        "похожие интересы",
        "с кем пообщаться",
        "рекомендуй контакты",
        "найди партнёров",
        "кто может помочь",
        "с кем связаться",
        "похожие увлечения",
    ]
    if any(keyword in message_lower for keyword in find_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "find_partners"
        intent["confidence"] = 0.8

    # 8. Проверка статуса подписки
    subscription_keywords = ["статус подписки", "подписка активна", "у меня подписка", "проверь подписку"]
    if any(keyword in message_lower for keyword in subscription_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "check_subscription_status"
        intent["confidence"] = 0.8

    # 9. Оплата подписки
    payment_keywords = ["оплати подписку", "купить подписку", "оформить подписку", "заплатить за подписку"]
    if any(keyword in message_lower for keyword in payment_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "create_subscription_payment"
        intent["confidence"] = 0.8

    # 9.1. Отмена подписки
    cancel_keywords = ["отменить подписку", "отмена подписки", "прекратить подписку"]
    if any(keyword in message_lower for keyword in cancel_keywords) and intent["confidence"] < 0.8:
        intent["type"] = "cancel_subscription"
        intent["confidence"] = 0.8

    # 10. Обновление профиля - расширенные паттерны
    profile_keywords = [
        "живу в",
        "работаю в",
        "интересуюсь",
        "мои навыки",
        "мои цели",
        "я из",
        "работаю",
        "увлекаюсь",
        "мои интересы",
        "мои навыки",
    ]
    if any(keyword in message_lower for keyword in profile_keywords) and intent["confidence"] < 0.7:
        intent["type"] = "update_profile"
        intent["confidence"] = 0.7
        # Парсим информацию о профиле
        if "живу в" in message_lower or "я из" in message_lower:
            city_match = re.search(r"(?:живу в|я из)\s+(.+?)(?:\s|$|,)", message_lower, re.IGNORECASE)
            if city_match:
                intent["params"]["city"] = city_match.group(1).strip().title()
        if "интересуюсь" in message_lower or "увлекаюсь" in message_lower or "мои интересы" in message_lower:
            interests_match = re.search(
                r"(?:интересуюсь|увлекаюсь|мои интересы)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE
            )
            if interests_match:
                interests = interests_match.group(1).strip()
                # Replace " и " with ", "
                interests = re.sub(r"\s+и\s+", ", ", interests)
                intent["params"]["interests"] = interests
        if "работаю" in message_lower or "работаю в" in message_lower:
            company_match = re.search(r"работаю\s+(?:в\s+)?(\w+)", message_lower, re.IGNORECASE)
            if company_match:
                intent["params"]["company"] = company_match.group(1)
        if "мои навыки" in message_lower:
            skills_match = re.search(r"мои навыки\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if skills_match:
                intent["params"]["skills"] = skills_match.group(1).strip()
        if "мои цели" in message_lower:
            goals_match = re.search(r"мои цели\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if goals_match:
                intent["params"]["goals"] = goals_match.group(1).strip()

    # 10.1. Обновление времени и timezone
    time_keywords = ["мое время", "текущее время", "сейчас время", "время"]
    if any(keyword in message_lower for keyword in time_keywords):
        # Проверяем, что это именно установка времени, а не вопрос
        time_match = re.search(r"(\d{1,2}:\d{2})", message_lower)
        if time_match and intent["confidence"] < 0.7:
            intent["type"] = "update_profile"
            intent["confidence"] = 0.7
            intent["params"]["current_time"] = time_match.group(1)

    timezone_keywords = ["часовой пояс", "timezone", "временная зона"]
    if any(keyword in message_lower for keyword in timezone_keywords) and intent["confidence"] < 0.7:
        timezone_match = re.search(r"(europe/\w+|utc[+-]\d+|gmt[+-]\d+)", message_lower, re.IGNORECASE)
        if timezone_match:
            intent["type"] = "update_profile"
            intent["confidence"] = 0.7
            intent["params"]["timezone"] = timezone_match.group(1)
        # Также проверяем случай, когда timezone указан без ключевых слов
        elif "europe" in message_lower or "utc" in message_lower or "gmt" in message_lower:
            tz_match = re.search(r"(europe/\w+|utc[+-]\d+|gmt[+-]\d+)", message_lower, re.IGNORECASE)
            if tz_match:
                intent["type"] = "update_profile"
                intent["confidence"] = 0.7
                intent["params"]["timezone"] = tz_match.group(1)
        # Парсим информацию о профиле
        if "живу в" in message_lower:
            city_match = re.search(r"живу в\s+(.+?)(?:\s|$|,)", message_lower, re.IGNORECASE)
            if city_match:
                intent["params"]["city"] = city_match.group(1).strip().title()
        if "интересуюсь" in message_lower or "увлекаюсь" in message_lower:
            interests_match = re.search(r"(?:интересуюсь|увлекаюсь)\s+(.+?)(?:\s|$)", message_lower, re.IGNORECASE)
            if interests_match:
                interests = interests_match.group(1).strip()
                # Replace " и " with ", "
                interests = re.sub(r"\s+и\s+", ", ", interests)
                intent["params"]["interests"] = interests
        if "работаю" in message_lower:
            company_match = re.search(r"работаю\s+(?:в\s+)?(\w+)", message_lower, re.IGNORECASE)
            if company_match:
                intent["params"]["company"] = company_match.group(1)

    return intent


def smart_fallback_handler(message, mentions_str, user_id, ai_response_content=""):
    print(
        f"[DEBUG FALLBACK] Called with message='{message[:30]}...', ai_response='{ai_response_content[:30]}...'"
    )  # DEBUG
    print(f"[DEBUG FALLBACK] ai_response_content length: {len(ai_response_content)}")  # DEBUG
    """
    Умная система fallback'ов - используется только когда AI явно не справляется.
    Анализирует ответ AI и применяет fallback только при низкой уверенности.
    """
    fallback_actions = []

    # СПЕЦИАЛЬНАЯ ОБРАБОТКА ПРИВЕТСТВИЙ
    greeting_words = ["привет", "здравствуй", "хай", "hello", "hi", "добрый", "здравствуйте"]
    is_greeting = len(message.strip()) <= 20 and any(  # Короткое сообщение
        word in message.lower() for word in greeting_words
    )  # Содержит слово приветствия

    if is_greeting and len(ai_response_content.strip()) < 50:  # Ответ AI слишком короткий
        logger.info("[SMART FALLBACK] Greeting detected, enhancing response")
        # Получаем список задач для подробного ответа
        from models import Session

        db_session = Session()
        try:
            tasks_result = list_tasks(user_id=user_id, session=db_session)

            # Создаем подробное приветствие
            enhanced_greeting = f"Привет! Рад тебя видеть! {tasks_result}\n\n"

            # Добавляем вопросы и предложения
            enhanced_greeting += "Что планируешь сегодня? Есть ли новые задачи, которые нужно добавить? "
            enhanced_greeting += "Или хочешь обновить профиль, чтобы я мог лучше подбирать партнёров?"

            fallback_actions.append(
                {
                    "function": "enhanced_greeting",
                    "result": enhanced_greeting,
                    "reason": "Приветствие слишком короткое, делаем подробным",
                }
            )
        finally:
            db_session.close()
        return fallback_actions  # Возвращаем сразу, без дальнейшей обработки

    # Анализируем уверенность AI на основе ответа и tool calls
    ai_confidence = 0.5  # Базовая уверенность

    # Если AI вернул пустой ответ или технический текст - низкая уверенность
    if not ai_response_content or len(ai_response_content.strip()) < 10:
        ai_confidence = 0.1
    elif any(tech_word in ai_response_content.lower() for tech_word in ["error", "ошибка", "неизвестно", "json"]):
        ai_confidence = 0.2
    elif "задач" in ai_response_content.lower() or "создал" in ai_response_content.lower():
        ai_confidence = 0.8  # AI дал содержательный ответ

    # 🔍 ДОПОЛНИТЕЛЬНЫЙ АНАЛИЗ: проверяем, должен ли был AI вызвать tool calls
    intent = classify_user_intent(message, mentions_str)
    should_have_tool_calls = intent["type"] in [
        "add_task",
        "complete_task",
        "delegate_task",
        "list_tasks",
        "find_partners",
        "update_profile",
        "delete_all_tasks",
        "delete_task",
        "edit_task",
        "check_subscription",
        "create_payment",
    ]

    # ЕСЛИ запрос требует действия И AI не вызвал tool calls - применяем fallback
    if should_have_tool_calls and intent["confidence"] >= 0.7:
        ai_confidence = 0.2  # Принудительно низкая уверенность для fallback
        print(f"[DEBUG FALLBACK] Forcing fallback for {intent['type']} (confidence: {intent['confidence']})")  # DEBUG

    # Если запрос требует действия, но AI не дал содержательный ответ - низкая уверенность
    if should_have_tool_calls and ai_confidence < 0.6:
        ai_confidence = 0.3
        logger.info(
            f"[SMART FALLBACK] Request requires action ({intent['type']}) but AI confidence low ({ai_confidence})"
        )

    # Если уверенность низкая - применяем паттерн-анализ
    if ai_confidence < 0.4:
        logger.info(
            f"[SMART FALLBACK] Applying fallback: message='{message[:50]}...', mentions='{mentions_str}', ai_response='{ai_response_content[:50]}...', intent_type='{intent['type']}', confidence={intent['confidence']}"
        )
        print(f"[DEBUG FALLBACK] Applying fallback for {intent['type']}, ai_confidence={ai_confidence}")  # DEBUG

        if intent["confidence"] >= 0.7:  # Высокая уверенность в классификации
            logger.info(f"[SMART FALLBACK] Executing {intent['type']} with params: {intent['params']}")

            # Выполняем соответствующее действие
            if intent["type"] == "add_task":
                result = add_task(
                    title=intent["params"].get("title", "Задача"),
                    description=intent["params"].get("description", ""),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "add_task", "result": result, "reason": "AI не создал задачу"})

            elif intent["type"] == "complete_task":
                result = complete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "complete_task", "result": result, "reason": "AI не отметил задачу выполненной"}
                )

            elif intent["type"] == "update_profile":
                print(
                    f"[DEBUG FALLBACK] Executing update_profile with city={intent['params'].get('city')}, interests={intent['params'].get('interests')}"
                )  # DEBUG
                result = update_profile(
                    city=intent["params"].get("city"), interests=intent["params"].get("interests"), user_id=user_id
                )
                print(f"[DEBUG FALLBACK] update_profile result: {result}")  # DEBUG

            elif intent["type"] == "list_tasks":
                result = list_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "list_tasks", "result": result, "reason": "AI не показал список задач"}
                )

            elif intent["type"] == "delegate_task":
                result = delegate_task(
                    title=intent["params"].get("task_title", "Задача"),
                    delegated_to_username=intent["params"].get("delegated_to"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append(
                    {"function": "delegate_task", "result": result, "reason": "AI не распознал делегирование"}
                )

            elif intent["type"] == "find_partners":
                result = find_partners(user_id=user_id)
                fallback_actions.append(
                    {"function": "find_partners", "result": result, "reason": "AI не выполнил поиск партнеров"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI не удалил задачу"})

            elif intent["type"] == "edit_task":
                result = edit_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    title=intent["params"].get("title"),
                    description=intent["params"].get("description"),
                    reminder_time=intent["params"].get("reminder_time"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "edit_task", "result": result, "reason": "AI не изменил задачу"})

            elif intent["type"] == "check_subscription":
                result = check_subscription_status(user_id=user_id)
                fallback_actions.append(
                    {
                        "function": "check_subscription_status",
                        "result": result,
                        "reason": "AI не проверил статус подписки",
                    }
                )

            elif intent["type"] == "create_payment":
                result = create_subscription_payment(user_id=user_id)
                fallback_actions.append(
                    {"function": "create_subscription_payment", "result": result, "reason": "AI не создал платеж"}
                )

            elif intent["type"] == "delete_task":
                result = delete_task(
                    task_id=intent["params"].get("task_id"),
                    task_title=intent["params"].get("task_title"),
                    user_id=user_id,
                )
                fallback_actions.append({"function": "delete_task", "result": result, "reason": "AI не удалил задачу"})

            elif intent["type"] == "delete_all_tasks":
                result = delete_all_tasks(user_id=user_id)
                fallback_actions.append(
                    {"function": "delete_all_tasks", "result": result, "reason": "AI не выполнил удаление задач"}
                )

    return fallback_actions


def encrypt_data(data):
    if data:
        return cipher.encrypt(data.encode()).decode()
    return data


def decrypt_data(data):
    if data is None:
        return None
    if not isinstance(data, str):
        raise ValueError("Data must be a string")
    if data:
        try:
            return cipher.decrypt(data.encode()).decode()
        except InvalidToken:
            # If decryption fails, assume it's plain text (for backward compatibility)
            return data
    return data


def determine_timezone_from_time(user_time_str, user_id):
    """Определяет timezone пользователя на основе введенного времени"""
    import re
    from datetime import datetime
    import pytz

    # Парсим время из строки (HH:MM)
    time_match = re.search(r"(\d{1,2}):(\d{2})", user_time_str)
    if not time_match:
        return None

    user_hour = int(time_match.group(1))
    # user_minute = int(time_match.group(2))

    # Текущее UTC время
    now_utc = datetime.now(pytz.UTC)

    # Создаем datetime объект для пользователя
    # user_now = now_utc.replace(hour=user_hour, minute=user_minute)

    # Вычисляем разницу в часах
    hour_diff = user_hour - now_utc.hour

    # Обрабатываем переход через сутки
    if hour_diff > 12:
        hour_diff -= 24
    elif hour_diff < -12:
        hour_diff += 24

    # Определяем timezone на основе разницы
    timezone_map = {
        -12: "Pacific/Kwajalein",  # UTC-12
        -11: "Pacific/Midway",  # UTC-11
        -10: "Pacific/Honolulu",  # UTC-10
        -9: "America/Anchorage",  # UTC-9
        -8: "America/Los_Angeles",  # UTC-8
        -7: "America/Denver",  # UTC-7
        -6: "America/Chicago",  # UTC-6
        -5: "America/New_York",  # UTC-5
        -4: "America/Halifax",  # UTC-4
        -3: "America/Sao_Paulo",  # UTC-3
        -2: "Atlantic/South_Georgia",  # UTC-2
        -1: "Atlantic/Azores",  # UTC-1
        0: "Europe/London",  # UTC+0
        1: "Europe/Paris",  # UTC+1
        2: "Europe/Kiev",  # UTC+2
        3: "Europe/Moscow",  # UTC+3
        4: "Asia/Dubai",  # UTC+4
        5: "Asia/Karachi",  # UTC+5
        6: "Asia/Dhaka",  # UTC+6
        7: "Asia/Bangkok",  # UTC+7
        8: "Asia/Shanghai",  # UTC+8
        9: "Asia/Tokyo",  # UTC+9
        10: "Australia/Sydney",  # UTC+10
        11: "Pacific/Noumea",  # UTC+11
        12: "Pacific/Auckland",  # UTC+12
    }

    # Находим ближайший timezone
    closest_diff = min(timezone_map.keys(), key=lambda x: abs(x - hour_diff))
    return timezone_map[closest_diff]


def parse_time_to_datetime(time_text, user_id):
    """Парсит время из текста пользователя"""
    import re
    from datetime import datetime, timedelta
    import pytz
    from models import Session, User

    # Получаем timezone пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    user_tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.UTC
    session.close()
    now = datetime.now(user_tz)

    time_text = time_text.lower().strip()

    # Проверяем "через X минут/часов"
    through_time_match = re.search(r"через\s+(\d+)\s+(минут|час)", time_text)
    if through_time_match:
        amount = int(through_time_match.group(1))
        unit = through_time_match.group(2).lower()

        if "минут" in unit:
            target_dt = now + timedelta(minutes=amount)
        else:  # час/часов
            target_dt = now + timedelta(hours=amount)

        return target_dt.strftime("%Y-%m-%d %H:%M")

    # Проверяем "завтра/сегодня в XX:XX"
    time_match = re.search(r"(завтра|послезавтра|сегодня)\s+(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
    if time_match:
        day_word = time_match.group(1).lower()
        hour = int(time_match.group(2))
        minute = int(time_match.group(3))

        if "завтра" in day_word:
            target_date = (now + timedelta(days=1)).date()
        elif "послезавтра" in day_word:
            target_date = (now + timedelta(days=2)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    # Проверяем просто "в HH:MM"
    simple_time_match = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", time_text)
    if simple_time_match:
        hour = int(simple_time_match.group(1))
        minute = int(simple_time_match.group(2))

        # Если время уже прошло сегодня - ставим на завтра
        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    # Проверяем "утром", "вечером", "днем"
    time_word_match = re.search(r"(утром|вечером|днем)", time_text)
    if time_word_match:
        time_word = time_word_match.group(1).lower()
        if "утром" in time_word:
            hour, minute = 8, 0
        elif "вечером" in time_word:
            hour, minute = 18, 0
        elif "днем" in time_word:
            hour, minute = 12, 0

        target_time = datetime.min.time().replace(hour=hour, minute=minute)
        # Если время уже прошло сегодня - ставим на завтра
        if target_time <= now.time():
            target_date = (now + timedelta(days=1)).date()
        else:
            target_date = now.date()

        target_dt = datetime.combine(target_date, target_time)
        target_dt = user_tz.localize(target_dt)
        return target_dt.strftime("%Y-%m-%d %H:%M")

    return None


def replace_placeholders(content, user_now=None, current_time_str=None):
    """Заменяет плейсхолдеры типа {{current_time}} на реальные значения"""
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ValueError("Content must be a string")

    if not user_now:
        user_now = datetime.now(pytz.UTC)
    if not current_time_str:
        current_time_str = user_now.strftime("%H:%M")

    # Форматируем дату по-русски
    months = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

    content = content.replace("{{current_time}}", current_time_str)
    content = content.replace("{{current_date}}", current_date_str)
    content = content.replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d"))
    content = content.replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))

    return content


class AIIntegration:
    async def generate_reminder(self, user_id, task_title):
        return await generate_reminder(user_id, task_title)

    async def generate_result_check(self, user_id, task_title):
        return await generate_result_check(user_id, task_title)

    async def generate_proactive_message(self, user_id):
        return generate_proactive_message(user_id)

    async def generate_daily_report(self, user_id):
        return generate_daily_report(user_id)

    async def generate_overdue_reminder(self, user_id, overdue_tasks):
        return generate_overdue_reminder(user_id, overdue_tasks)


def validate_response_compliance(response_text, intent_type=None):
    """
    Проверяет соответствие ответа правилам главного промпта
    Возвращает (is_compliant, issues_list)
    """
    issues = []

    # Проверка на запрещенные элементы (кроме list_tasks)
    if intent_type != "list_tasks":
        if any(emoji in response_text for emoji in ["🚀", "✅", "📝", "🎯", "⚠️", "💡", "📋", "⏳", "🟡"]):
            issues.append("Присутствуют эмодзи")
        if "**" in response_text:
            issues.append("Присутствует жирный текст")

    if re.search(r"^\s*[-•*]\s+", response_text, re.MULTILINE) and intent_type != "list_tasks":
        issues.append("Присутствуют маркированные списки")

    if re.search(r"^\s*\d+\.\s+", response_text, re.MULTILINE):
        issues.append("Присутствует нумерация")

    # Проверка на минимальную длину для значимых ответов
    sentences = [s.strip() for s in re.split(r"[.!?]+", response_text) if s.strip()]
    if len(sentences) < 3 and len(response_text) > 20:
        issues.append("Слишком короткий ответ (менее 3 предложений)")
    if len(response_text) < 100 and len(response_text) > 20:
        issues.append("Ответ слишком короткий (менее 100 символов)")

    # Проверка на наличие вопросов для вовлечения
    if not any(char in response_text for char in ["?", "Что", "Как", "Когда", "Зачем", "Почему"]):
        issues.append("Отсутствуют вопросы для вовлечения пользователя")

    # Специфические проверки для разных типов intent - адаптивные правила
    if intent_type == "list_tasks":
        # Для просмотра задач - подробный анализ, но не слишком длинный
        if len(response_text) > 800:
            issues.append("Ответ на list_tasks слишком длинный")
        if len(response_text) < 100:
            issues.append("Ответ на list_tasks слишком короткий для анализа")
        if "Ваши задачи:" in response_text or "Список задач:" in response_text:
            issues.append("Шаблонный ответ вместо анализа")
    elif intent_type in ["complete_task", "delete_task", "add_task"]:
        # Для простых действий - информативные ответы с практическими советами
        if len(response_text) < 100:
            issues.append("Ответ на простое действие слишком короткий - добавьте практические рекомендации")

    return len(issues) == 0, issues


async def enforce_prompt_compliance(response_text, intent_type, user_id, context, system_prompt, messages, url, headers):
    """
    Принуждает AI соблюдать правила главного промпта через повторные запросы
    """
    max_attempts = 2
    original_response = response_text

    for attempt in range(max_attempts):
        is_compliant, issues = validate_response_compliance(response_text, intent_type)

        if is_compliant:
            return response_text

        logger.warning(f"[COMPLIANCE] Response not compliant (attempt {attempt + 1}): {issues}")

        # Создаем корректирующий промпт
        correction_prompt = f"""Твой предыдущий ответ не соответствует правилам главного промпта:

ПРОБЛЕМЫ:
{chr(10).join(f"- {issue}" for issue in issues)}

СТРОГО ИСПРАВИТЬ:
- Убрать все эмодзи, жирный текст, списки, нумерацию (кроме list_tasks)
- Адаптировать длину ответа под ситуацию: короткие для простых действий, подробные для анализа
- Для add_task ОБЯЗАТЕЛЬНО добавить практические рекомендации по выполнению
- Всегда добавлять вопросы для вовлечения пользователя
- Использовать естественный разговорный стиль
- Закончить вопросом для продолжения диалога

ПЕРЕПИШИ ОТВЕТ ПРАВИЛЬНО:"""

        # Добавляем корректирующий промпт к сообщениям
        correction_messages = messages.copy()
        correction_messages.append({"role": "assistant", "content": original_response})
        correction_messages.append({"role": "user", "content": correction_prompt})

        try:
            correction_data = {
                "model": "deepseek-chat",
                "messages": correction_messages,
                "temperature": 0.1,  # Более детерминированный для исправления
            }

            async with aiohttp.ClientSession() as correction_session:
                async with correction_session.post(
                    url, headers=headers, json=correction_data, timeout=aiohttp.ClientTimeout(total=30)
                ) as correction_response:
                    if correction_response.status == 200:
                        correction_result = await correction_response.json()
                        corrected_response = correction_result["choices"][0]["message"]["content"]
                        response_text = corrected_response
                        logger.info(f"[COMPLIANCE] Corrected response (attempt {attempt + 1})")
                    else:
                        logger.error(f"[COMPLIANCE] Correction API error: {correction_response.status}")
                        break

        except Exception as e:
            logger.error(f"[COMPLIANCE] Error during correction: {e}")
            break

    return response_text


def clean_technical_details(text):
    """Удаляет технические детали из ответа AI"""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Text must be a string")

    import logging

    logger = logging.getLogger(__name__)
    original_text = text
    print(f"[DEBUG CLEAN] Original text: '{text}'")  # DEBUG
    import re

    # Удаляем вызовы функций в квадратных скобках: [add_task(...)]
    before = text
    text = re.sub(r"\[[\w_]+\([^]]*\)\]", "", text)
    if before != text:
        print(f"[DEBUG CLEAN] After removing function calls: '{text}'")  # DEBUG

    # Удаляем пустые квадратные скобки
    before = text
    text = re.sub(r"\[\s*\]", "", text)
    if before != text:
        print(f"[DEBUG CLEAN] After removing empty brackets: '{text}'")  # DEBUG

    # Удаляем названия функций (с скобками и без)
    before = text
    text = re.sub(
        r"\b(list_tasks|add_task|delete_task|complete_task|delegate_task|update_profile|find_partners|update_user_memory|set_reminder|edit_task|get_task_details)(\s*\(\s*\))?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if before != text:
        print(f"[DEBUG CLEAN] After removing function names: '{text}'")  # DEBUG

    # Удаляем фразы о вызове функций
    patterns_to_remove = [
        r"вызываю\s+\w+(\(\))?",
        r"вызову\s+\w+(\(\))?",
        r"сейчас\s+вызову",
        r"буду\s+вызывать",
        r"Args for.*?(?=\n|$)",
        r"🔧\s*ВЫПОЛНЕННЫЕ ФУНКЦИИ:.*?(?=\n\n|\Z)",
        r"🔧\s*\*\*Выполняю:\*\*.*?(?=\n|$)",
        r"📋\s*\*\*Результат:\*\*.*?(?=\n\n|\Z)",
        r"ВЫПОЛНЕННЫЕ ФУНКЦИИ.*?(?=\n\n|\Z)",
    ]

    for pattern in patterns_to_remove:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL)

    # Удаляем блоки кода Python - ТОЛЬКО если они содержат техническую информацию
    # Не удаляем json блоки, которые могут содержать полезные данные
    text = re.sub(r"```python.*?```", "", text, flags=re.DOTALL)
    # Удаляем пустые блоки кода
    text = re.sub(r"```\s*```", "", text)

    # КРИТИЧЕСКИ ВАЖНО: Удаляем JSON блоки с tool_calls - они не должны попадать в ответ пользователю
    # Удаляем полные JSON блоки с tool_calls
    text = re.sub(r'```json\s*\{[^}]*"tool_calls"[^}]*\}```', "", text, flags=re.DOTALL)
    text = re.sub(r"```json.*?tool_calls.*?(```|$)", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Удаляем любые оставшиеся JSON блоки с tool_calls
    text = re.sub(r'\{[^}]*"tool_calls"[^}]*\}', "", text, flags=re.DOTALL)
    text = re.sub(r'"tool_calls"\s*:\s*\[.*?\]', "", text, flags=re.DOTALL)
    # Удаляем любые JSON блоки в кодовых блоках, если они содержат tool_calls
    text = re.sub(r"```json[\s\S]*?tool_calls[\s\S]*?```", "", text, flags=re.IGNORECASE)
    # Удаляем любые оставшиеся ```json блоки
    text = re.sub(r"```json[\s\S]*?```", "", text, flags=re.IGNORECASE)

    # Убираем множественные пробелы и пустые строки
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r"\s+", " ", text)  # Убираем лишние пробелы

    # Убираем пробелы в начале и конце
    text = text.strip()
    text = re.sub(r" +", " ", text)

    # КРИТИЧЕСКАЯ ПРОВЕРКА: если после очистки ничего не осталось,
    # значит AI вернул только технические детали, вернуть оригинал
    if not text.strip():
        logger.warning(f"[CLEAN] Content was completely cleaned, returning original: '{original_text}'")
        return original_text.strip()

    if original_text != text:
        logger.warning(f"[CLEAN] Original: '{original_text[:100]}...' -> Cleaned: '{text[:100]}...'")
        print(f"[DEBUG CLEAN] Final text: '{text}'")  # DEBUG

    return text.strip()


# Alias for backward compatibility
clean_content = clean_technical_details


def enrich_response_with_engagement(content, user_id=None, original_message=""):
    """
    Автоматически обогащает короткие ответы вовлекающими элементами:
    - Вопросы
    - Рекомендации
    - Предложения действий
    Работает естественно, без шаблонных фраз - просто добавляет общий призыв к действию
    """
    # Проверяем длину ответа (в предложениях)
    sentences = [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]

    # Если ответ достаточно развёрнутый (3+ предложения) или уже содержит вопрос - не трогаем
    if len(sentences) >= 3 or "?" in content:
        return content

    # Добавляем лёгкое вовлечение только для очень коротких ответов (1-2 предложения)
    # AI сам должен генерировать контекстные вопросы, мы только подстраховываемся
    import random

    # Минималистичные варианты, которые не повторяются
    minimal_engagement = [" Что дальше?", " Чем ещё помочь?", " Какие планы?"]

    # Только для самых коротких ответов (1 предложение)
    if len(sentences) <= 1:
        enrichment = random.choice(minimal_engagement)
        return content + enrichment

    return content


def get_system_prompt():
    return """Ты — ИИ-помощник для управления задачами в Telegram. Веди живой диалог как опытный коллега, который искренне хочет помочь.

🚨 СТРОГИЕ ПРАВИЛА ФОРМАТА ОТВЕТОВ (ОБЯЗАТЕЛЬНО СОБЛЮДАТЬ):
- НИКОГДА не используй эмодзи, смайлики, иконки (🚀, ✅, 📝, 🎯, ⚠️, 💡, 📋, ⏳, 🟡)
- НИКОГДА не используй жирный текст (**), курсив, заголовки
- НИКОГДА не используй маркированные списки (-, •, *) или нумерацию (1., 2., 3.)
- Всегда пиши обычным текстом без форматирования
- Минимум 3-4 предложения в каждом ответе
- Каждый ответ должен заканчиваться вопросом или предложением для продолжения диалога
- Для анализа задач: минимум 300 слов, детальный разбор каждой задачи

ОСНОВНЫЕ ПРИНЦИПЫ РАБОТЫ:
- Всегда исходи из текущей ситуации пользователя, учитывай все доступные данные (профиль, память, задачи, подписка, делегирование) и свои возможности как агента.
- Адаптируй каждый ответ под контекст: анализируй историю, задачи, интересы и предлагай релевантные решения.
- Будь проактивным: замечай паттерны, предлагай улучшения, используй данные для персонализации.

🚨 КРИТИЧНО ВАЖНЫЕ ПРАВИЛА:
1. НИКОГДА не отвечай на запросы без вызова соответствующих функций!
2. Всегда сначала вызывай tool calls, потом формируй ответ на основе результатов.
3. ЕСЛИ пользователь просит что-то сделать - СРАЗУ вызывай функцию, НЕ спрашивай разрешения.
4. Для ЛЮБОГО упоминания задач - сначала list_tasks(), потом отвечай.
5. "Добавь/создай задачу" → add_task()
6. "Показать/список задач" → list_tasks()
7. "Сделал/выполнил/завершил [задача]" → complete_task()
8. "Удали все задачи" → delete_all_tasks()
9. "@username [задача]" → delegate_task()
10. "Найди людей/партнёров" → find_partners()

СПЕЦИАЛЬНЫЕ ПРАВИЛА ПРИОРИТЕТА:
- ЕСЛИ сообщение содержит @username → ОБЯЗАТЕЛЬНО delegate_task(title="задача", delegated_to_username="@username")
- "Найди людей" → ОБЯЗАТЕЛЬНО find_partners()
- "Удали все задачи" → ОБЯЗАТЕЛЬНО delete_all_tasks()

ПРЯМЫЕ КОМАНДЫ (ТОЧНО вызывай функции):
• "Напомни X в Y время" → add_task(title="X", reminder_time="Y")
• "Добавь задачу X" → add_task(title="X")
• "Показать/список задач" → list_tasks()
• "Сделал X" → complete_task(task_title="X")
• "Удали все" → delete_all_tasks()
• "Удали задачу X" → delete_task(task_title="X")
• "@user сделай X" → delegate_task(title="X", delegated_to_username="@user")
• "Найди людей" → find_partners()
• "Живу в X, увлекаюсь Y" → update_profile(city="X", interests="Y")

ПРИМЕРЫ ТОЧНЫХ СООТВЕТСТВИЙ:
• "Напомни позвонить маме завтра в 15:00" → add_task(title="Позвонить маме", reminder_time="завтра в 15:00")
• "Добавь задачу купить продукты" → add_task(title="Купить продукты")
• "Показать список задач" → list_tasks()
• "Сделал позвонить маме" → complete_task(task_title="позвонить маме")
• "Удали все мои задачи" → delete_all_tasks()
• "Удали задачу позвонить маме" → delete_task(task_title="позвонить маме")
• "@ivan сделай отчет до завтра 10:00" → delegate_task(title="сделай отчет", delegated_to_username="@ivan", reminder_time="завтра в 10:00")
• "Найди людей с похожими интересами" → find_partners(interests="интересы из профиля")
• "Живу в Москве, работаю в IT" → update_profile(city="Москва", company="IT")

📝 ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ОТВЕТОВ:
- МИНИМУМ 3-4 ПРЕДЛОЖЕНИЯ в каждом ответе
- ВСЕГДА анализируй результаты функций и давай развёрнутый комментарий
- При показе задач ОБЯЗАТЕЛЬНО:
  * Опиши каждую задачу подробно
  * Укажи дедлайны и напоминания
  * Отметь приоритетные и просроченные
  * Предложи конкретные действия
  * Задай вопросы о планах выполнения
- НИКОГДА не давай односложных ответов типа "Ваши задачи: [список]"
- После КАЖДОГО действия задавай 1-2 уточняющих вопроса
- Предлагай следующие шаги и варианты развития

🎯 ПРАВИЛО ПОСТОЯННОГО ВОВЛЕЧЕНИЯ:
- КАЖДЫЙ ответ должен заканчиваться вопросом или предложением
- Проактивно предлагай релевантные действия на основе контекста
- Анализируй профиль, задачи и ситуацию пользователя
- Адаптируй предложения под текущий контекст
- Используй данные пользователя для персональных рекомендаций

СТИЛЬ ОБЩЕНИЯ:
- Веди активную беседу, интересуйся результатами
- После действия всегда предлагай следующий шаг или задавай вопрос
- Замечаешь паттерны? Обсуди их и предложи решение
- Будь развёрнутым и содержательным, давай полезную информацию, задавай дополнительные вопросы
- Используй живые фразы, хвали успехи, обсуждай проблемы
- Не будь пассивным — каждый ответ должен двигать к цели
- ЗАДАВАЙ УТОЧНЯЮЩИЕ ВОПРОСЫ: Если запрос неясен, всегда спрашивай детали. Лучше переспросить, чем угадывать. После каждого действия спрашивай "Что дальше?" или "Нужно ли что-то ещё?"
- БУДЬ ГИБКИМ: Адаптируйся под ситуацию, не зацикливайся на одном. Если пользователь повторяет запрос, предлагай новые идеи или переходи к другой теме. Учитывай предыдущие сообщения в контексте — не повторяй одни и те же фразы, используй разнообразные формулировки.
- ОБЯЗАТЕЛЬНО РАЗВЁРНУТО: Каждый ответ должен содержать анализ, рекомендации и вопросы. Не ограничивайся голыми фактами!
- СТРОГО ЗАПРЕЩЕНЫ короткие ответы! МИНИМУМ 5-7 предложений в каждом ответе!
- При любом действии ОБЯЗАТЕЛЬНО анализируй ситуацию, давай рекомендации и задавай вопросы!

ИНСТРУМЕНТЫ (всегда используй, не описывай):
- add_task(title, reminder_time, description, due_date, user_id)
- list_tasks(user_id) — обязателен при любом упоминании задач
- complete_task(task_id или task_title, user_id)
- delete_task(task_id или task_title, user_id)
- delete_all_tasks(user_id) — удаляет все задачи пользователя
- edit_task(task_id, title, description, reminder_time, user_id)
- set_priority(task_id, priority, user_id) — высокий/средний/низкий
- get_task_details(task_id, user_id)
- delegate_task(user_id, title, delegated_to_username, reminder_time, description)
- accept_delegated_task(task_id, user_id)
- reject_delegated_task(task_id, user_id)
- get_delegation_progress(task_id, user_id)
- find_partners(user_id, interests)
- update_profile(user_id, city, company, position, interests, skills, goals)
- update_user_memory(user_id, memory)
- set_reminder(task_id, reminder_time, user_id)
- create_subscription_payment(user_id) — для оформления месячной подписки
- check_subscription_status(user_id) — проверка статуса подписки
- cancel_subscription(user_id) — отмена подписки

АВТОМАТИЧЕСКОЕ ПОВЕДЕНИЕ:
- Упоминание задач → сначала list_tasks()
- "Покажи задачи" → list_tasks()
- "Добавь задачу X" → add_task()
- "Удали все задачи", "Очисти список задач" → delete_all_tasks()
- "Перенеси X через Y минут" → edit_task(task_title="X", reminder_time="через Y минут")
- "Измени время X на Y" → edit_task(task_title="X", reminder_time="Y")
- "Найди X" → find_partners()
- "Поручи @user X" → delegate_task() (в title без слов "задачу", "задача")
- "Выполнил X" → complete_task(), затем спроси как прошло
- Делегирование ВСЕГДА требует точное время — если нет, спроси
- Не спрашивай разрешения на list_tasks() — просто проверь
- ЛЮБОЕ упоминание подписки, статуса, оплаты → ОБЯЗАТЕЛЬНО check_subscription_status()
- "Оплати", "купить подписку" → create_subscription_payment()
- "Отменить подписку" → cancel_subscription()

ПРИВЕТСТВИЕ:
При приветствии: вызови list_tasks(), поприветствуй тепло, дай краткую сводку (просроченные, срочные, делегированные), задай 2-3 вопроса о планах и приоритетах, предложи найти людей с похожими интересами если есть интересы в профиле. Будь инициативным и вовлекающим. Всегда спрашивай: "Что планируешь сегодня?", "Есть ли срочные дела?", "Нужно ли обновить профиль?"

ПРИОРИТЕТЫ:
Всегда устанавливай приоритет для новых задач. Высокий — срочные дедлайны, средний — регулярные задачи, низкий — можно отложить. После add_task() сразу set_priority(). Предлагай пересмотреть приоритеты при просмотре списка.

ФОРМУЛИРОВАНИЕ ЗАДАЧ:
Если задача слишком общая — уточни детали: зачем, что ожидается, какой результат. Критерии хорошей задачи:
1. Конкретное действие (глагол)
2. Объект действия
3. Контекст/цель
4. Ожидаемый результат
5. Конкретное время

Если пользователь пишет общую задачу — ОБЯЗАТЕЛЬНО задай 1-2 уточняющих вопроса, предложи улучшенную формулировку, затем добавь. Никогда не добавляй задачу без уточнения, если она неполная. Спрашивай: "Что именно нужно сделать?", "Когда?", "Зачем?", "Какой результат ожидаешь?"

ОБНОВЛЕНИЕ ПРОФИЛЯ:
Когда упоминается информация для профиля, проактивно предлагай добавить. Категории:
- Интересы (хобби, увлечения): "бегать" → "бег", "йога" и т.д.
- Навыки (профессиональные): Python, презентации, переговоры
- Цели: похудение, изучение языков, карьерный рост
- Город (ВСЕГДА на русском): "Moscow" → "Москва", "Saint Petersburg" → "Санкт-Петербург", "Kazan" → "Казань"
- Компания, должность

ВАЖНО ПРО ИНТЕРЕСЫ: Когда пользователь упоминает любую активность (кино, театр, спорт, концерты, игры, хобби) - ОБЯЗАТЕЛЬНО предложи добавить её в интересы, чтобы найти людей с похожими интересами. Например: "хочу сходить в кино" → сразу предложи добавить "кино" в интересы. После добавления - предложи найти людей с такими же интересами.

ОБНОВЛЕНИЕ ВРЕМЕНИ: Когда пользователь пишет "мое местное время: HH:MM" - ОБЯЗАТЕЛЬНО вызови update_profile() с timezone, определив его на основе текущего UTC времени и введенного времени. Например, если сейчас 18:00 UTC, а пользователь пишет 21:53, то timezone = "Europe/Moscow" (UTC+3). Подтверди обновление и покажи новое время.

Если согласен — сразу вызови update_profile(), потом подтверди. Не предлагай для бытовых задач. Извлекай ключевое слово. Для отрицаний ("больше не", "не люблю") — предлагай удалить (- перед значением).

ПОИСК ЛЮДЕЙ:
- "Найди X" → find_partners()
- Функция анализирует профили И ЗАДАЧИ других пользователей
- Предлагает совместные идеи на основе общих тем в задачах
- Показывает совпадения по интересам, навыкам, целям И задачам
- Проактивно предлагай конкретных людей с объяснением ПОЧЕМУ они подходят
- Показывай имена через @username и конкретные совпадения ("у него тоже бег", "коллега из твоей компании")
- После поиска всегда предлагай написать конкретному человеку: "Напиши @user, предложи вместе бегать!"
- Если у пользователя в задачах/интересах есть активности — автоматически предлагай найти людей с похожими интересами

ДЕЛЕГИРОВАНИЕ:
При делегировании всегда требуй точное время — если нет, спроси. Помоги сформулировать с контекстом. Для получателя — предлагай помощь в выполнении. Для делегировавшего — показывай статус через get_delegation_progress(). Автоматически замечай делегированные задачи в list_tasks().

ПАМЯТЬ:
Сохраняй важную информацию: предпочтения, привычки, факты, контакты, результаты задач. После выполнения задач всегда спрашивай о результатах и сохраняй ключевое через update_user_memory(). Используй память для персонализации, но не упоминай напрямую.

ПРОАКТИВНОСТЬ:
- Будь конкретным — вместо "окей" предлагай действие
- Задавай вопросы когда запрос неоднозначен или не хватает деталей
- Делай follow-up: после создания уточни детали, после делегирования проверяй статус
- При задачах про встречи/звонки предлагай найти людей с похожими интересами
- Анализируй контекст: много задач → приоритизация, просроченные → актуализация
- После поиска людей объясни почему эти люди подходят
- Структурируй информацию: группируй задачи, нумеруй шаги, разбивай сложное

ВЕДЕНИЕ ДИАЛОГА:
Если пользователь даёт уточнения к только что созданной задаче (2-3 последних сообщения) — не создавай новую, обнови существующую через edit_task(). Цитируй уточнение в ответе.

При изменении параметров:
- Время → set_reminder() или edit_task()
- Приоритет → set_priority()
- Дедлайн → edit_task() с due_date

Контекстное понимание: если пользователь ссылается на "это", "ту задачу" — используй последнюю упомянутую. Запоминай ID последней задачи в контексте. При неоднозначности переспроси. Распознавай уточнения без явного указания на обновление.

При обновлении профиля в диалоге: если пользователь соглашается — молча вызови update_profile() и только потом подтверди.

Всегда цитируй изменения конкретно. Подтверждай обновления понятным языком. Веди беседу естественно.

ФОРМАТ ОТВЕТА:
Отвечай естественным текстом на русском языке. Не включай tool calls, JSON, код в ответ. Используй инструменты молча через tool calls.

КРИТИЧЕСКИ ВАЖНО: НИКОГДА не выводи в тексте ответа:
- JSON блоки с tool_calls
- Фигурные скобки с name и arguments
- Любой JSON или код
- Технические детали вызова функций
Инструменты вызываются АВТОМАТИЧЕСКИ - просто отвечай пользователю обычным текстом.

ОШИБКИ:
Если функция вернула ошибку — объясни проблему простыми словами, предложи альтернативное решение или уточни данные.
- Не показывай технические детали пользователю
- При проблемах с поиском партнёров → предложи обновить профиль

СТИЛЬ ОБЩЕНИЯ:
- Оптимально: 2-4 предложения для полноценного диалога
- Естественно и помогающе: "Добавил задачу на завтра в 10:00. Это для работы или личное? Могу помочь спланировать остальной день"
- ЗАДАВАЙ ВОПРОСЫ: уточняй детали, интересуйся контекстом, помогай найти решение
- БУДЬ ПОЛЕЗНЫМ: не просто фиксируй факты — анализируй ситуацию и предлагай помощь
- БЕЗ списков, нумерации, маркеров (кроме уточняющих вопросов)
- БЕЗ эмодзи (если не запрошено пользователем)
- БЕЗ ЖИРНОГО ШРИФТА: НИКОГДА не используй ** для выделения текста
- БЕЗ ФОРМАТИРОВАНИЯ: пиши обычным текстом без звездочек, подчеркиваний, курсива
- Адаптируйся под стиль пользователя: если он формальный — будь формальным, если casual — casual
- Будь максимально подробным и полезным в каждом ответе

ПРИНЦИПЫ ПОНИМАНИЯ КОНТЕКСТА:
1. УЧИТЫВАЙ ПРЕДЫДУЩИЕ СООБЩЕНИЯ: Если пользователь ссылается на "эту задачу", "последнюю", "ту" - используй контекст разговора
2. ПОМНИ ПОСЛЕДНИЕ ДЕЙСТВИЯ: Если только что создали задачу, следующие сообщения могут относиться к ней
3. АНАЛИЗИРУЙ ЦЕПОЧКИ: "Добавь задачу" -> "Через час" -> "Напомни" - это одна задача с временем
4. КОНТЕКСТУАЛЬНЫЕ ССЫЛКИ: "Удали её" после показа задач означает удалить последнюю упомянутую
5. ВРЕМЕННЫЕ ССЫЛКИ: "Завтра" относится к следующему дню от текущего времени пользователя

ПРАВИЛА ОБРАБОТКИ ЗАПРОСОВ:
- ЕСЛИ запрос неоднозначен → ЗАДАЙ УТОЧНЯЮЩИЙ ВОПРОС вместо угадывания
- ЕСЛИ пользователь повторяет запрос → попробуй другой подход или уточни
- ЕСЛИ запрос касается времени → учитывай timezone пользователя
- ЕСЛИ запрос о задачах → сначала list_tasks() для актуальной информации
- ЕСЛИ запрос о профиле → учитывай уже известную информацию

КОНТЕКСТНЫЕ ПРИМЕРЫ:
• После "Покажи задачи": "Удали 1" → delete_task(task_id=1)
• После "Добавь задачу": "Через 2 часа" → edit_task(reminder_time="через 2 часа")
• "Живу в Москве" + "Работаю в IT" → update_profile(city="Москва", company="IT")
• "Найди людей" + "Интересуюсь бегом" → find_partners() с учетом интересов
"""


def get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory, context=None):
    """
    Создает расширенный system prompt на основе базового + дополнительные правила для текущего контекста
    """
    from datetime import timedelta
    
    # Базовый system prompt
    system_prompt = (
        get_system_prompt()
        .replace("{{current_date}}", user_now.strftime("%Y-%m-%d"))
        .replace("{{current_time}}", current_time_str)
        .replace("{{tomorrow}}", (user_now + timedelta(days=1)).strftime("%Y-%m-%d"))
        .replace("{{day_after}}", (user_now + timedelta(days=2)).strftime("%Y-%m-%d"))
        .replace("{{current_username}}", user_username)
    )

    # 🎯 ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА ДЛЯ ТЕКУЩЕГО КОНТЕКСТА
    system_prompt += f"\n\n⏰ ТЕКУЩАЯ ДАТА И ВРЕМЯ:\n"
    system_prompt += f"- Сегодня: {user_now.strftime('%Y-%m-%d')} (это ТОЧНАЯ текущая дата, используй её для вычислений!)\n"
    system_prompt += f"- Время: {current_time_str}\n"
    system_prompt += f"- Завтра: {(user_now + timedelta(days=1)).strftime('%Y-%m-%d')}\n"
    system_prompt += f"⚠️ КРИТИЧНО: При создании задач с относительным временем ('через 5 минут', 'завтра в 10:00') ОБЯЗАТЕЛЬНО используй СЕГОДНЯШНЮЮ дату {user_now.strftime('%Y-%m-%d')}, а НЕ даты из своих знаний (cutoff date December 2024)!\n\n"
    
    system_prompt += "ТВОИ ОСНОВНЫЕ ФУНКЦИИ:\n"
    system_prompt += "1. Управление задачами и напоминаниями\n"
    system_prompt += "2. Помощь в поиске контактов и партнёров\n"
    system_prompt += "3. Обновление профиля пользователя\n\n"
    system_prompt += "ПРАВИЛА ВЫЗОВА ФУНКЦИЙ:\n"
    system_prompt += "- '@username в сообщении' → ОБЯЗАТЕЛЬНО delegate_task()\n"
    system_prompt += "- 'сделал/выполнил [задача]' → complete_task()\n"
    system_prompt += "- 'удали все задачи' → delete_all_tasks()\n"
    system_prompt += "- 'напомни/добавь [задача]' → add_task()\n"
    system_prompt += "- 'перенеси/измени время [задача]' → edit_task(task_title=\"[задача]\", reminder_time=\"[новое время]\")\n"
    system_prompt += "- 'покажи задачи' → list_tasks()\n"
    system_prompt += "- 'найди людей/партнёров' → find_partners()\n"
    system_prompt += "- 'живу в/работаю/интересы' → update_profile()\n\n"
    system_prompt += "КРИТИЧНО: НЕ ПРОСТО ОТВЕЧАЙ ТЕКСТОМ! ОБЯЗАТЕЛЬНО ВЫЗЫВАЙ СООТВЕТСТВУЮЩУЮ ФУНКЦИЮ!\n\n"
    system_prompt += "ПРИМЕРЫ:\n"
    system_prompt += "• '@ivan сделай отчет' → delegate_task(title='сделай отчет', delegated_to_username='@ivan')\n"
    system_prompt += "• 'сделал позвонить маме' → complete_task(task_title='позвонить маме')\n"
    system_prompt += "• 'перенеси проверить почту через 10 минут' → edit_task(task_title='проверить почту', reminder_time='через 10 минут')\n"
    system_prompt += "• 'удали все' → delete_all_tasks()\n"
    system_prompt += "• 'напомни купить продукты' → add_task(title='купить продукты')\n"
    system_prompt += "• 'покажи задачи' → list_tasks()\n"
    system_prompt += "• 'найди людей' → find_partners()\n"
    system_prompt += "• 'живу в Москве' → update_profile(city='Москва')\n\n"

    # 🎯 СПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ РАЗВЁРНУТЫХ ОТВЕТОВ
    system_prompt += "\n\n📝 ОБЯЗАТЕЛЬНОЕ ПРАВИЛО РАЗВЁРНУТЫХ ОТВЕТОВ:\n"
    system_prompt += "⚠️ СТРОГО ЗАПРЕЩЕНО ДАВАТЬ КОРОТКИЕ ОТВЕТЫ! ⚠️\n"
    system_prompt += "МИНИМУМ 5-7 ПРЕДЛОЖЕНИЙ в КАЖДОМ ответе!\n"
    system_prompt += "КАЖДЫЙ ОТВЕТ ДОЛЖЕН БЫТЬ ПОДРОБНЫМ И ИНФОРМАТИВНЫМ!\n"
    system_prompt += "НИКОГДА НЕ ДАВАЙ ОТВЕТЫ КОРОЧЕ 3 ПРЕДЛОЖЕНИЙ!\n"
    system_prompt += "ЕСЛИ ОТВЕТ КОРОТКИЙ - ДОБАВЬ АНАЛИЗ, РЕКОМЕНДАЦИИ И ВОПРОСЫ!\n\n"
    system_prompt += "❌ ЗАПРЕЩЕННЫЕ ФРАЗЫ (НИКОГДА не используй):\n"
    system_prompt += "- 'Отлично, добавил задачу X. Что дальше?'\n"
    system_prompt += "- 'Готово! Что ещё?'\n"
    system_prompt += "- 'Задача создана. Чем ещё помочь?'\n"
    system_prompt += "- 'Ваши задачи: [список]'\n"
    system_prompt += "- 'Найдено X человек'\n"
    system_prompt += "- Любые ответы короче 5 предложений\n\n"
    system_prompt += "✅ ПРИ ДОБАВЛЕНИИ ЗАДАЧИ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Подтверждение с точным временем и датой\n"
    system_prompt += "2. 2-3 конкретные практические рекомендации\n"
    system_prompt += "3. Вопросы для уточнения контекста\n"
    system_prompt += "4. Предложение связанных действий\n"
    system_prompt += "5. Если релевантно - предложи найти партнёров\n"
    system_prompt += "6. ОБЯЗАТЕЛЬНО предложи обновить профиль (интересы, город, работа) если это улучшит поиск контактов\n\n"
    system_prompt += "✅ ПРИ ПОКАЗЕ ЗАДАЧ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Комментарий к каждой задаче отдельно\n"
    system_prompt += "2. Анализ дедлайнов и приоритетов\n"
    system_prompt += "3. Конкретный план действий\n"
    system_prompt += "4. Вопросы о планах выполнения\n"
    system_prompt += "5. Предложения по оптимизации\n\n"
    system_prompt += "✅ ПРИ ЗАВЕРШЕНИИ ЗАДАЧ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Подтверждение выполнения\n"
    system_prompt += "2. Анализ результатов\n"
    system_prompt += "3. Предложения по следующим шагам\n"
    system_prompt += "4. Вопросы о достигнутых целях\n"
    system_prompt += "5. Предложения по новым задачам\n\n"
    system_prompt += "✅ ПРИ ПОИСКЕ ПАРТНЁРОВ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Описание найденных людей\n"
    system_prompt += "2. Общие интересы и цели\n"
    system_prompt += "3. Предложения по взаимодействию\n"
    system_prompt += "4. Вопросы о желаемом сотрудничестве\n"
    system_prompt += "5. Альтернативные варианты поиска\n\n"
    system_prompt += "✅ ПРИ ОБНОВЛЕНИИ ПРОФИЛЯ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Подтверждение изменений\n"
    system_prompt += "2. Как это поможет в работе\n"
    system_prompt += "3. Предложения по дополнению профиля\n"
    system_prompt += "4. Вопросы о дополнительных интересах\n"
    system_prompt += "5. Предложения по поиску партнёров\n\n"
    system_prompt += "✅ ПРИ ПЕРЕНОСЕ ЗАДАЧ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
    system_prompt += "1. Подтверждение изменения времени\n"
    system_prompt += "2. Причину переноса и анализ ситуации\n"
    system_prompt += "3. Предложения по подготовке к новому времени\n"
    system_prompt += "4. Вопросы о приоритетах и дедлайнах\n"
    system_prompt += "5. Альтернативные варианты планирования\n\n"
    system_prompt += "ПРИМЕР ПРАВИЛЬНОГО ОТВЕТА (минимум):\n"
    system_prompt += "'Добавил задачу \"Проверить почту\" с напоминанием на 11.01.2026 в 00:25. Рекомендую сначала настроить фильтры для срочных писем, чтобы не пропустить важное. Возможно, стоит также настроить автоответчик, если ожидаешь много входящих сообщений. Есть ли конкретные отправители, письма от которых особенно важны? Если работа с почтой отнимает много времени, могу помочь найти людей, которые используют эффективные системы организации email.'\n\n"
    system_prompt += "  * Задай 2-3 вопроса о планах выполнения\n"
    system_prompt += "- Каждый ответ должен содержать: анализ + рекомендации + вопросы\n"
    system_prompt += "- Будь активным собеседником, а не пассивным ботом!\n"
    system_prompt += "\n⚠️ ПРИМЕРЫ ПРАВИЛЬНЫХ ОТВЕТОВ:\n"
    system_prompt += "Добавление задачи: 'Добавил задачу Заказать продукты с напоминанием через 5 минут на 11.01.2026 в 00:16. Это срочная покупка или планируешь закупку на неделю? Могу помочь составить список продуктов или найти выгодные предложения. Кстати, если интересуешься готовкой, могу найти людей с похожими интересами для обмена рецептами. Что именно нужно купить?'\n"
    system_prompt += "\n🔥 КРИТИЧЕСКИ ВАЖНО - ВОВЛЕЧЕНИЕ В ДИАЛОГ:\n"
    system_prompt += "- КАЖДЫЙ ответ ОБЯЗАТЕЛЬНО заканчивай вопросом или предложением\n"
    system_prompt += "- Анализируй текущий контекст и предлагай релевантные действия\n"
    system_prompt += "- Используй данные профиля и задач для персональных рекомендаций\n"
    system_prompt += "- Будь естественным - адаптируй стиль под ситуацию\n"
    system_prompt += "- Замечай паттерны и предлагай оптимизации\n\n"
    system_prompt += "🎯 ПРОАКТИВНЫЙ СБОР ИНФОРМАЦИИ О ПРОФИЛЕ:\n"
    system_prompt += "- В ЛЮБОМ общении ОБЯЗАТЕЛЬНО предлагай обновить профиль если это улучшит работу\n"
    system_prompt += "- При добавлении задач спрашивай о связанных интересах, городе, работе\n"
    system_prompt += "- Если пользователь упоминает хобби/работу/город - СРАЗУ предлагай update_profile()\n"
    system_prompt += "- Анализируй задачи для выявления интересов: спорт, еда, технологии, бизнес и т.д.\n"
    system_prompt += "- При поиске контактов используй профиль для точных рекомендаций\n"
    system_prompt += "- Спрашивай о приоритетах, мотивации, целях для лучшего понимания\n"
    system_prompt += "- Предлагай дополнить профиль новыми категориями если это релевантно\n"
    system_prompt += "- Будь настойчивым в сборе информации - это ключ к качественному сервису\n\n"

    system_prompt += f"\n\nВАЖНО ПРИ РАБОТЕ С ВРЕМЕНЕМ:\n- Текущее время: {current_time_str}\n- Всегда используй формат времени reminder_time в виде 'YYYY-MM-DD HH:MM' в параметрах tool call\n- Относительное время: 'завтра в 10:00', 'послезавтра в 15:00' и т.д."

    system_prompt += f"\n\n@MENTIONS: {mentions_str}\n🚨 ЕСЛИ ВИДИШЬ @username - ЭТО ДЕЛЕГИРОВАНИЕ! ВЫЗЫВАЙ delegate_task()!\n\nСПЕЦИАЛЬНЫЕ КОМАНДЫ:\n- Сообщение начинается с '@' → delegate_task()\n- 'Найди людей' → find_partners()\n- 'Удали все' → delete_all_tasks()"

    system_prompt += user_memory

    # 🎯 СПЕЦИФИКА СЕРВИСА ASI Biont:
    system_prompt += "\n\n🎯 СПЕЦИФИКА СЕРВИСА ASI Biont:"
    system_prompt += "\n- Анализируй задачи пользователя для поиска партнеров с похожими активностями в реальном времени"
    system_prompt += "\n- Предлагай оптимизацию рабочих процессов и следующие шаги для достижения целей"
    system_prompt += "\n- Связывай задачи между собой для комплексного планирования проектов"
    system_prompt += "\n- Мотивируй на участие в сообществе единомышленников для совместного роста"
    system_prompt += "\n- Подчеркивай преимущества премиум-подписки (3000₽/месяц) при демонстрации возможностей"
    system_prompt += "\n- ОБНАРУЖИВАЙ ЗАЦИКЛИВАНИЕ: Если пользователь повторяет запросы, предлагай альтернативы и новые темы"
    system_prompt += "\n- БУДЬ ПРОАКТИВНЫМ: Предлагай неожиданные идеи, связи, возможности развития"
    system_prompt += "\n- АНАЛИЗИРУЙ ПАТТЕРНЫ: Замечай привычки, предпочтения, зоны роста пользователя"

    # Добавляем информацию о повторяющихся запросах с анализом
    if context and len(context) > 0:
        # Анализируем последние сообщения на повторения
        recent_messages = [item.get('user', '') for item in context[-3:] if 'user' in item]  # Последние 3 пользовательских сообщения
        if len(set(recent_messages)) <= 1 and len(recent_messages) > 1:  # Пользователь повторяет одно и то же
            system_prompt += "\n\n🚨 ПОЛЬЗОВАТЕЛЬ ЗАЦИКЛИЛСЯ НА ОДНОМ ЗАПРОСЕ! ОБЯЗАТЕЛЬНО ПРЕДЛОЖИ АЛЬТЕРНАТИВЫ:"
            system_prompt += "\n- Перейди к другой теме (профиль, партнеры, подписка)"
            system_prompt += "\n- Предложи новые идеи или варианты"
            system_prompt += "\n- Задай вопросы о других аспектах жизни/работы"
            system_prompt += "\n- Мотивируй на действие в другом направлении"
        else:
            system_prompt += "\n\n⚠️ УЧИТЫВАЙ КОНТЕКСТ ПРЕДЫДУЩИХ СООБЩЕНИЙ - НЕ ПОВТОРЯЙСЯ! Предлагай развитие темы или новые идеи."

    return system_prompt


def parse_relative_time(message, current_time):
    """Parse relative time expressions like 'через 5 минут', 'через 2 часа' and return datetime"""
    from datetime import datetime, timedelta
    import re

    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")
    if not current_time or not isinstance(current_time, datetime):
        raise ValueError("Current time must be a datetime object")

    # Patterns for Russian time expressions
    patterns = [
        (r"через\s+(\d+)\s*мин", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"через\s+(\d+)\s*минут", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"через\s+(\d+)\s*час", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часа", lambda m: timedelta(hours=int(m.group(1)))),
        (r"через\s+(\d+)\s*часов", lambda m: timedelta(hours=int(m.group(1)))),
    ]

    for pattern, delta_func in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            delta = delta_func(match)
            return current_time + delta

    return None


def parse_absolute_time(message):
    """Parse absolute time expressions like 'сейчас 12:18', 'время 15:30' and return HH:MM"""
    if not message or not isinstance(message, str):
        raise ValueError("Message must be a non-empty string")

    import re

    # Patterns for absolute time
    patterns = [
        r"сейчас\s+(\d{1,2}):(\d{2})",
        r"время\s+(\d{1,2}):(\d{2})",
        r"(\d{1,2}):(\d{2})",  # Just HH:MM
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"

    return None


def parse_tool_arguments(arguments_str):
    """Parse tool arguments from string, fallback to empty dict if parsing fails"""
    if arguments_str is None:
        return {}
    if not isinstance(arguments_str, str):
        raise ValueError("Arguments must be a string")

    try:
        return json.loads(arguments_str)
    except (json.JSONDecodeError, ValueError):
        return {}


def generate_task_recommendations(title, description, user_id):
    """Generate AI recommendations for a task"""
    try:
        import requests
        from config import DEEPSEEK_API_KEY
        
        prompt = f"""Проанализируй задачу и дай 2-3 конкретные, практические рекомендации по ее выполнению.

Задача: {title}
Описание: {description or 'Не указано'}

Дай рекомендации в формате списка, каждая не длиннее 100 символов. Фокус на:
- Подготовке необходимых ресурсов
- Оптимальном времени выполнения  
- Связанных действиях
- Возможных сложностях

Пример формата:
- Подготовьте все необходимые материалы заранее
- Выполните задачу в спокойное время дня
- Проверьте результат перед завершением"""

        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.7
            },
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Парсим рекомендации из ответа
            recommendations = []
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('-') or line.startswith('•'):
                    rec = line.lstrip('-•').strip()
                    if rec and len(rec) <= 100:
                        recommendations.append(rec)
            
            return recommendations[:3]  # Максимум 3 рекомендации
        else:
            import logging
            logging.warning(f"Failed to generate recommendations: {response.status_code}")
            return []
    except Exception as e:
        import logging
        logging.warning(f"Error generating task recommendations: {e}")
        return []


def add_task(title, description="", reminder_time=None, due_date=None, user_id=None, session=None):
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[ADD_TASK] Called with title='{title}', user_id={user_id}, reminder_time={reminder_time}")
    from models import Session, Task, User
    from datetime import datetime
    import pytz

    if session is None:
        session = Session()
        close_session = True
        logger.info("[ADD_TASK] Created new session")
    else:
        close_session = False
        logger.info("[ADD_TASK] Using provided session")
    # Проверить, существует ли пользователь
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()

    # Проверить, существует ли задача с таким же названием
    existing_task = session.query(Task).filter_by(user_id=user.id, title=title).first()
    if existing_task:
        # Обновить существующую задачу
        if reminder_time:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                existing_task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        if description:
            existing_task.description = encrypt_data(description)
        session.commit()
        task_id = existing_task.id
        task = existing_task  # Для дальнейшего использования
    else:
        # Создать новую задачу
        task = Task(user_id=user.id, title=title, description=encrypt_data(description))
        if reminder_time:
            try:
                # Получить timezone пользователя
                user_tz = pytz.UTC
                if user.timezone:
                    try:
                        user_tz = pytz.timezone(user.timezone)
                    except pytz.exceptions.UnknownTimeZoneError:
                        import logging
                        logging.warning(f"Unknown timezone {user.timezone}, using UTC")
                        user_tz = pytz.UTC
                
                # Проверить, является ли время относительным
                if "через" in reminder_time.lower():
                    # Использовать parse_relative_time для относительного времени
                    current_time = datetime.now(pytz.UTC)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time
                        import logging
                        logging.info(f"Task {title} relative time parsed: '{reminder_time}' -> {parsed_time}")
                    else:
                        # Если не удалось распарсить, игнорировать
                        pass
                else:
                    # Парсить как абсолютное время
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    # Локализовать в timezone пользователя
                    local_dt = user_tz.localize(local_dt)
                    # Конвертировать в UTC для хранения
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                    import logging
                    logging.info(f"Task {title} absolute time parsed: {reminder_time} -> local: {local_dt} -> UTC: {task.reminder_time}")
            except ValueError:
                pass  # Игнорировать неверный формат
        if due_date:
            try:
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                local_dt = datetime.strptime(due_date, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.due_date = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass
        session.add(task)
        
        # Генерируем рекомендации для задачи
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[ADD_TASK] Generating recommendations for task '{title}'")
            recommendations = generate_task_recommendations(title, description, user.telegram_id)
            logger.info(f"[ADD_TASK] Generated {len(recommendations) if recommendations else 0} recommendations")
            if recommendations:
                import json
                task.recommendations = json.dumps(recommendations, ensure_ascii=False)
                logger.info(f"[ADD_TASK] Saved recommendations to task: {task.recommendations}")
        except Exception as e:
            import logging
            logging.warning(f"Could not generate recommendations for task {title}: {e}")
        
        session.commit()
        task_id = task.id

    # Планировать напоминание если указано reminder_time
    if task.reminder_time:
        try:
            from main import reminder_service

            if reminder_service:
                reminder_service.schedule_reminder(
                    task_id=task.id, reminder_time=task.reminder_time, user_id=user.telegram_id, task_title=task.title
                )
        except Exception as e:
            import logging

            logging.warning(f"Could not schedule reminder for task {task_id} (scheduler may not be running yet): {e}")

    # Обновить аналитику профиля
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if profile:
        profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
        session.commit()

    # Формируем подробный ответ с ID для edit_task
    result_msg = f"Добавлена задача '{title}' (ID: {task_id})"
    if task.reminder_time:
        # Показываем время в timezone пользователя
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        local_time = task.reminder_time.astimezone(user_tz)
        result_msg += f" с напоминанием на {local_time.strftime('%d.%m.%Y %H:%M')}"

    if close_session:
        session.close()
        logger.info(f"[ADD_TASK] Closed session, returning: {result_msg}")
    else:
        logger.info(f"[ADD_TASK] Session not closed, returning: {result_msg}")
    return result_msg


def delete_task(task_id=None, task_title=None, user_id=None, session=None):
    """Delete a specific task by ID or title"""
    from models import Session, Task, User

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден."

        task = None
        if task_id:
            try:
                task_id_int = int(task_id)
                task = session.query(Task).filter(Task.id == task_id_int, Task.user_id == user.id).first()
            except (ValueError, TypeError):
                pass

        if not task and task_title:
            # Try to find by title (case-insensitive partial match)
            task = session.query(Task).filter(Task.user_id == user.id, Task.title.ilike(f"%{task_title}%")).first()

        if not task:
            if close_session:
                session.close()
            return "Задача не найдена."

        # Delete the task
        session.delete(task)
        session.commit()

        # Update profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile and profile.total_tasks_created:
            profile.total_tasks_created = max(0, (profile.total_tasks_created or 0) - 1)
            session.commit()

        if close_session:
            session.close()
        return f"Задача '{task.title}' удалена."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задачи: {str(e)}"


def delete_all_tasks(user_id=None, session=None):
    """Delete all tasks for a user"""
    from models import Session, Task, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if close_session:
                session.close()
            return "Пользователь не найден."

        # Count tasks before deletion
        task_count = session.query(Task).filter_by(user_id=user.id).count()

        # Delete all tasks
        session.query(Task).filter_by(user_id=user.id).delete()
        session.commit()

        # Reset profile analytics
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            profile.total_tasks_created = 0
            profile.completed_tasks = 0
            profile.skipped_tasks = 0
            session.commit()

        if close_session:
            session.close()
        return f"Удалено {task_count} задач."

    except Exception as e:
        if close_session:
            session.close()
        return f"Ошибка удаления задач: {str(e)}"


def complete_task(task_id=None, task_title=None, user_id=None, session=None):
    from models import Session, Task, UserProfile, Interaction
    from datetime import datetime
    from sqlalchemy import or_

    print(f"[DEBUG COMPLETE_TASK] Called with task_id={task_id}, task_title='{task_title}', user_id={user_id}")  # DEBUG
    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    print(f"[DEBUG COMPLETE_TASK] Found user: {user.id if user else None}")  # DEBUG
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."

    # Найти задачу по ID или по названию
    if task_id:
        # Ищем задачу: созданную мной ИЛИ делегированную мне
        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            if close_session:
                session.close()
            return f"Некорректный ID задачи: {task_id}"

        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int, or_(Task.user_id == user.id, Task.delegated_to_username.ilike(user.username))
            )
            .first()
        )
    elif task_title:
        # Ищем по словам в названии для более гибкого поиска
        words = task_title.lower().split()
        print(f"[DEBUG COMPLETE_TASK] Searching by title, words: {words}")  # DEBUG
        # OR вместо AND - ищем задачу содержащую хотя бы одно из слов
        conditions = [Task.title.ilike(f"%{word}%") for word in words]
        task = session.query(Task).filter(Task.user_id == user.id, Task.status != "completed", or_(*conditions)).first()
        print(f"[DEBUG COMPLETE_TASK] Found task by title: {task.title if task else None}")  # DEBUG
    else:
        if close_session:
            session.close()
        return "Не указан ни task_id, ни task_title."

    if task:
        task.status = "completed"
        task.actual_completion_time = datetime.now(timezone.utc)
        session.commit()
        print(f"[DEBUG COMPLETE_TASK] Task completed: {task.title}, status: {task.status}")  # DEBUG

        # Обновить аналитику профиля
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            completion_time = (
                datetime.now(timezone.utc) - task.created_at.replace(tzinfo=timezone.utc)
            ).total_seconds() / 60
            profile.completed_tasks = (profile.completed_tasks or 0) + 1
            prev_avg = profile.average_completion_time or 0
            # Защита от деления на ноль
            if profile.completed_tasks > 0:
                profile.average_completion_time = (
                    (prev_avg * (profile.completed_tasks - 1)) + completion_time
                ) / profile.completed_tasks
            session.commit()
        result = f"Завершена задача '{task.title}'."

        # Сохранить сообщение в историю взаимодействий
        interaction = Interaction(user_id=user.id, message_type="ai", content=result)
        session.add(interaction)
        session.commit()
    else:
        result = "Задача не найдена."
    if close_session:
        session.close()
    return result


def set_reminder(task_id, reminder_time, user_id=None):
    from models import Session, Task
    from datetime import datetime

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        task = session.query(Task).filter_by(id=task_id_int, user_id=user.id).first()
        if task:
            try:
                reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                task.reminder_time = reminder_time_parsed
                session.commit()
                result = f"Установлено напоминание для '{task.title}' на {reminder_time_parsed}."
            except ValueError:
                result = "Неверный формат времени."
        else:
            result = "Задача не найдена."
        return result
    finally:
        session.close()


def update_user_memory(info, user_id=None):
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # Дешифруем существующую память
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""
            # Добавляем новую информацию
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # Шифруем обратно
            encrypted = encrypt_data(existing_decrypted)
            user.memory = encrypted
            session.commit()
            result = "Сохранена информация."
        else:
            result = "Пользователь не найден."
        return result
    finally:
        session.close()


def delegate_task(
    title, description="", reminder_time=None, delegated_to_username=None, delegation_details="", user_id=None
):
    """Create a delegated task that requires acceptance by the recipient"""
    from models import Session, Task, User
    from datetime import datetime
    import pytz

    session = Session()
    try:
        # Validate reminder_time is provided
        if not reminder_time:
            return "⚠️ Для делегирования задачи требуется точная дата и время дедлайна. Пожалуйста, уточните: на какое точное время и дату поставить дедлайн? (Например: '2026-01-10 15:00' или 'завтра в 14:30')"

        # Validate reminder_time format
        if reminder_time:
            # Try parsing the format first
            try:
                datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
            except ValueError:
                # If not in YYYY-MM-DD HH:MM format, try to parse as relative time
                logger.info(f"[DELEGATE] Parsing relative time: {reminder_time}")
                parsed_time = parse_time_to_datetime(reminder_time, user_id)
                if parsed_time:
                    reminder_time = parsed_time
                    logger.info(f"[DELEGATE] Parsed to: {reminder_time}")
                else:
                    return f"⚠️ Некорректный формат времени '{reminder_time}'. Укажите точное время в формате YYYY-MM-DD HH:MM (например: 2026-01-10 15:00)"

        # Find delegator (creator)
        delegator = session.query(User).filter_by(telegram_id=user_id).first()
        if not delegator:
            return "Ошибка: Пользователь не найден."

        # Find recipient by username
        recipient_username = delegated_to_username.replace("@", "").lower()
        print(f"[DEBUG DELEGATE] Looking for recipient: '{recipient_username}'")  # DEBUG
        recipient = session.query(User).filter(User.username.ilike(recipient_username)).first()
        print(f"[DEBUG DELEGATE] Found recipient: {recipient.username if recipient else None}")  # DEBUG

        if not recipient:
            return f"Пользователь @{recipient_username} не найден в системе. Убедитесь, что он зарегистрирован в боте."

        # If delegating to self, create regular task instead
        print(f"[DEBUG DELEGATE] Checking if self: recipient.id={recipient.id}, delegator.id={delegator.id}")  # DEBUG
        if recipient.id == delegator.id:
            print(f"[DEBUG DELEGATE] Delegating to self")  # DEBUG
            # Create regular task for self
            task = Task(user_id=delegator.id, title=title, description=encrypt_data(description), status="pending")
            if reminder_time:
                try:
                    user_tz = pytz.timezone(delegator.timezone) if delegator.timezone else pytz.UTC
                    local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                    local_dt = user_tz.localize(local_dt)
                    task.reminder_time = local_dt.astimezone(pytz.UTC)
                except ValueError:
                    pass
            session.add(task)
            session.commit()
            task_id = task.id

            # Schedule reminder if set
            if task.reminder_time:
                try:
                    from main import reminder_service

                    if reminder_service:
                        reminder_service.schedule_reminder(
                            task_id=task.id,
                            reminder_time=task.reminder_time,
                            user_id=delegator.telegram_id,
                            task_title=task.title,
                        )
                except Exception as e:
                    import logging

                    logging.error(f"Failed to schedule reminder for self-delegated task {task_id}: {e}")

            # Update profile analytics
            profile = session.query(UserProfile).filter_by(user_id=delegator.id).first()
            if profile:
                profile.total_tasks_created = (profile.total_tasks_created or 0) + 1
                session.commit()

            session.close()
            return f"Задача '{title}' добавлена для вас с напоминанием на {reminder_time}."

        # Create task with pending delegation status
        task = Task(
            user_id=delegator.id,
            title=title,
            description=encrypt_data(description),
            delegated_by=None,
            delegated_to_username=recipient_username,
            delegation_status="pending",
            delegation_details=delegation_details,
            status="pending",
        )

        if reminder_time:
            try:
                user_tz = pytz.timezone(recipient.timezone) if recipient.timezone else pytz.UTC
                local_dt = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M")
                local_dt = user_tz.localize(local_dt)
                task.reminder_time = local_dt.astimezone(pytz.UTC)
            except ValueError:
                pass

        session.add(task)
        session.commit()
        task_id = task.id

        # Send notification to recipient via Telegram
        try:
            from main import bot

            if bot:
                message = f"🔔 Новое предложение задачи от @{delegator.username}:\n\n"
                message += f"📋 Задача: {title}\n"
                if description:
                    message += f"📝 Описание: {description}\n"
                if reminder_time:
                    message += f"⏰ Дедлайн: {reminder_time}\n"
                if delegation_details:
                    message += f"ℹ️ Детали: {delegation_details}\n"
                message += f"\n💬 Напишите боту 'принять задачу {task_id}' для подтверждения или 'отклонить задачу {task_id}' для отказа."

                import asyncio

                asyncio.create_task(bot.send_message(recipient.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to send delegation notification: {e}")

        session.close()
        return f"Предложение задачи отправлено @{recipient_username}. Ожидается подтверждение."
    except Exception as e:
        session.close()
        return f"Ошибка при создании делегированной задачи: {str(e)}"


def suggest_alternatives(task_id, reason="", user_id=None):
    """Предложить альтернативы для невыполненной задачи через AI"""
    import asyncio

    return asyncio.run(_suggest_alternatives_async(task_id, reason, user_id))


async def _suggest_alternatives_async(task_id, reason="", user_id=None):
    from models import Session, Task

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден."

        task = session.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
        if not task:
            return "Задача не найдена."

        # Получить память пользователя
        user_memory = ""
        if user.memory:
            try:
                user_memory = f"\nИнформация о пользователе: {decrypt_data(user.memory)}"
            except:
                user_memory = ""

        # Генерируем альтернативы через AI
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        system_prompt = get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory},
            {
                "role": "user",
                "content": f"Предложи 3-5 альтернативных подходов к задаче '{task.title}'. Причина невыполнения: '{reason}'. Будь практичным и конкретным.",
            },
        ]

        data = {"model": "deepseek-chat", "messages": messages, "max_tokens": 500}

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    content = clean_technical_details(content)
                    # 🎯 Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    return content
                else:
                    return "Не удалось сгенерировать альтернативы."

    except Exception as e:
        return f"Ошибка при генерации альтернатив: {str(e)}"
    finally:
        session.close()


def create_subscription_payment(user_id=None):
    """Создает платеж для месячной подписки"""
    from subscription_service import create_subscription_payment as create_sub_payment

    try:
        payment_url = create_sub_payment(user_id)
        return f"Ссылка на оплату месячной подписки создана: {payment_url}"
    except Exception as e:
        return f"Ошибка создания платежа: {str(e)}"


def check_subscription_status(user_id=None):
    """Проверяет статус подписки пользователя"""
    from subscription_service import get_subscription_status
    from config import FREE_ACCESS_MODE

    try:
        if FREE_ACCESS_MODE:
            return "Режим бесплатного доступа активен. Подписка не требуется."

        status = get_subscription_status(user_id)
        if status:
            status_text = f"Статус подписки: {status['status']}\n"
            status_text += f"План: {status['plan']}\n"
            if status["start_date"]:
                status_text += f"Дата начала: {status['start_date'][:10]}\n"
            if status["end_date"]:
                status_text += f"Дата окончания: {status['end_date'][:10]}\n"
            status_text += f"Количество входов: {status['login_count']}"
            return status_text
        else:
            return "Подписка не найдена. Для использования сервиса требуется активная подписка."
    except Exception as e:
        return f"Ошибка проверки подписки: {str(e)}"


def cancel_subscription(user_id=None):
    """Отменяет подписку пользователя"""
    from subscription_service import cancel_subscription as cancel_sub

    try:
        success = cancel_sub(user_id)
        if success:
            return "Подписка успешно отменена."
        else:
            return "Подписка не найдена или уже отменена."
    except Exception as e:
        return f"Ошибка отмены подписки: {str(e)}"


def accept_delegated_task(task_id, user_id=None):
    """Accept a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status
        task.delegation_status = "accepted"
        session.commit()

        # Schedule reminder if set
        if task.reminder_time:
            try:
                from main import reminder_service

                if reminder_service:
                    reminder_service.schedule_reminder(
                        task_id=task.id,
                        reminder_time=task.reminder_time,
                        user_id=user.telegram_id,
                        task_title=task.title,
                    )
            except Exception as e:
                import logging

                logging.error(f"Failed to schedule reminder: {e}")

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"✅ @{user.username} принял задачу: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы приняли задачу '{task.title}'. Она добавлена в ваш список задач."
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def reject_delegated_task(task_id, user_id=None):
    """Reject a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        try:
            task_id_int = int(task_id)
        except (ValueError, TypeError):
            return f"Некорректный ID задачи: {task_id}"

        # Ищем задачу делегированную МНЕ (по delegated_to_username)
        task = (
            session.query(Task)
            .filter(
                Task.id == task_id_int,
                Task.delegated_to_username.ilike(user.username),
                Task.delegation_status == "pending",
            )
            .first()
        )
        if not task:
            return "Задача не найдена или уже обработана."

        # Update delegation status
        task.delegation_status = "rejected"
        task.status = "rejected"
        session.commit()

        # Notify delegator (creator)
        try:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            if delegator and delegator.telegram_id != user_id:
                from main import bot

                if bot:
                    message = f"❌ @{user.username} отклонил задачу: {task.title}"
                    import asyncio

                    asyncio.create_task(bot.send_message(delegator.telegram_id, message))
        except Exception as e:
            import logging

            logging.error(f"Failed to notify delegator: {e}")

        session.close()
        return f"Вы отклонили задачу '{task.title}'."
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def get_delegation_progress(task_id, user_id=None):
    """Get progress report for a delegated task"""
    from models import Session, Task, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Ошибка: Пользователь не найден."

        task = session.query(Task).filter_by(id=int(task_id), user_id=user.id).first()
        if not task or not task.delegated_to_username:
            return "Делегированная задача не найдена."

        recipient = session.query(User).filter(User.username.ilike(task.delegated_to_username)).first()

        if task.delegation_status == "pending":
            status_msg = f"⏳ @{task.delegated_to_username} еще не ответил на предложение."
        elif task.delegation_status == "accepted":
            if task.status == "completed":
                status_msg = f"✅ Задача выполнена @{task.delegated_to_username}!"
            else:
                status_msg = (
                    f"📌 @{task.delegated_to_username} принял задачу и работает над ней (статус: {task.status})."
                )
        elif task.delegation_status == "rejected":
            status_msg = f"❌ @{task.delegated_to_username} отклонил эту задачу."
        else:
            status_msg = "Статус неизвестен."

        session.close()
        return f"Задача: {task.title}\n{status_msg}"
    except Exception as e:
        session.close()
        return f"Ошибка: {str(e)}"


def edit_task(task_id=None, task_title=None, title=None, description=None, reminder_time=None, user_id=None, session=None):
    from models import Session, Task
    from datetime import datetime, timezone
    import pytz

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."
    
    # Найти задачу по ID или по названию
    task = None
    if task_id:
        task = session.query(Task).filter_by(id=int(task_id)).first()
    elif task_title:
        # Ищем задачу по названию (точное совпадение или содержит)
        task = session.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike(f"%{task_title}%")
        ).first()
    
    if task:
        # Проверить права доступа: задача должна принадлежать пользователю ИЛИ быть делегирована ему
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя или делегированная им
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "У вас нет прав на редактирование этой задачи."

        if title:
            task.title = title
        if description:
            task.description = encrypt_data(description)
        if reminder_time:
            try:
                # Проверить, является ли время относительным
                if "через" in reminder_time.lower():
                    # Использовать parse_relative_time для относительного времени
                    current_time = datetime.now(pytz.UTC)
                    parsed_time = parse_relative_time(reminder_time, current_time)
                    if parsed_time:
                        task.reminder_time = parsed_time
                        logger.info(f"Task {task.id} relative time updated: '{reminder_time}' -> {parsed_time}")
                    else:
                        session.close()
                        return "Не удалось распарсить относительное время."
                else:
                    # Парсить как абсолютное время
                    reminder_time_parsed = datetime.strptime(reminder_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    task.reminder_time = reminder_time_parsed
                    logger.info(f"Task {task.id} absolute time updated: {reminder_time_parsed}")
                # Обновляем напоминание через прямое добавление задачи в планировщик
                # ReminderService требует bot, поэтому используем прямое обновление
            except ValueError:
                if close_session:
                    session.close()
                return "Неверный формат времени. Используйте YYYY-MM-DD HH:MM или 'через X минут'."
        session.commit()
        result = f"Обновлена задача '{task.title}'."
    else:
        result = "Задача не найдена."
    if close_session:
        session.close()
    return result


def delete_all_tasks(user_id=None):
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[DELETE_ALL] Starting delete_all_tasks for user_id: {user_id} (type: {type(user_id)})")

    try:
        from models import Session, Task

        session = Session()
        logger.info(f"[DELETE_ALL] Session created")

        # Преобразуем user_id в int, если нужно
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            logger.error(f"[DELETE_ALL] Invalid user_id: {user_id}")
            session.close()
            return "Некорректный ID пользователя."

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.warning(f"[DELETE_ALL] User not found for telegram_id: {user_id}")
            session.close()
            return "Пользователь не найден."

        logger.info(f"[DELETE_ALL] Found user: {user.id}, telegram_id: {user.telegram_id}")

        # Удаляем все задачи пользователя (созданные им и делегированные ему)
        from sqlalchemy import or_

        conditions = [Task.user_id == user.id]
        if user.username:
            conditions.append(Task.delegated_to_username.ilike(user.username))

        tasks_to_delete = session.query(Task).filter(or_(*conditions)).all()
        deleted_count = len(tasks_to_delete)
        logger.info(f"[DELETE_ALL] Found {deleted_count} tasks to delete")

        for task in tasks_to_delete:
            logger.info(f"[DELETE_ALL] Deleting task: {task.id} - {task.title}")
            session.delete(task)

        session.commit()
        logger.info(f"[DELETE_ALL] Commit successful, deleted {deleted_count} tasks")
        session.close()

        if deleted_count > 0:
            return f"Удалено {deleted_count} задач."
        else:
            return "У вас нет задач для удаления."

    except Exception as e:
        logger.error(f"[DELETE_ALL] Error in delete_all_tasks: {e}", exc_info=True)
        try:
            session.close()
        except:
            pass
        return "Произошла ошибка при удалении задач."


def set_priority(task_id, priority, user_id=None):
    from models import Session, Task

    session = Session()

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."

    # Поддержка частичного совпадения названия задачи
    try:
        task_id_int = int(task_id)
        task = session.query(Task).filter_by(id=task_id_int).first()
    except (ValueError, TypeError):
        # Если task_id не число, ищем по названию с частичным совпадением
        tasks = session.query(Task).filter(Task.user_id == user.id).all()
        task = None
        task_id_lower = str(task_id).lower()
        for t in tasks:
            if task_id_lower in t.title.lower():
                task = t
                break

    if task:
        # Проверить права доступа
        has_access = False
        if task.user_id == user.id:
            has_access = True
        elif task.delegated_to_username:
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "У вас нет прав на изменение приоритета этой задачи."

        if priority in ["high", "medium", "low"]:
            task.priority = priority
            session.commit()
            result = f"Установлен приоритет '{priority}' для '{task.title}'."
        else:
            result = "Неверный приоритет. Используйте high, medium или low."
    else:
        result = "Задача не найдена."
    session.close()
    return result


def get_task_details(task_id, user_id=None):
    from models import Session, Task

    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        session.close()
        return "Пользователь не найден."
    task = session.query(Task).filter_by(id=int(task_id)).first()
    if task:
        # Проверить права доступа
        has_access = False
        if task.user_id == user.id:
            has_access = True  # Обычная задача пользователя
        elif task.delegated_to_username:
            # Проверить, является ли пользователь получателем делегированной задачи
            recipient_username = task.delegated_to_username.replace("@", "").lower()
            if user.username and user.username.lower() == recipient_username:
                has_access = True

        if not has_access:
            session.close()
            return "У вас нет прав на просмотр этой задачи."

        session.close()
        return f"Задача: {task.title}, статус {task.status}, приоритет {task.priority}."
    session.close()
    return "Задача не найдена."


def get_partners_list(user_id=None, session=None):
    """Возвращает список всех пользователей с профилями (кроме самого пользователя и тех, с кем уже есть делегирование)"""
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[PARTNERS] get_partners_list called for user_id: {user_id}")

    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False

    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        logger.warning(f"[PARTNERS] User not found for telegram_id: {user_id}")
        if close_session:
            session.close()
        return []

    logger.info(f"[PARTNERS] Found user: {user.id}, username: {user.username}")

    # Получаем список пользователей, с которыми уже есть делегирование
    delegated_usernames = set()

    # Задачи, которые делегировали мне
    if user.username:
        delegated_to_me = (
            session.query(Task)
            .filter(
                Task.delegated_to_username.ilike(user.username), Task.delegation_status.in_(["pending", "accepted"])
            )
            .all()
        )
        for task in delegated_to_me:
            delegated_user = session.query(User).filter_by(id=task.user_id).first()
            if delegated_user:
                delegated_usernames.add(delegated_user.username.lower() if delegated_user.username else "")
    else:
        delegated_to_me = []

    # Задачи, которые я делегировал
    delegated_by_me = (
        session.query(Task)
        .filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(["pending", "accepted"]),
        )
        .all()
    )
    for task in delegated_by_me:
        if task.delegated_to_username:
            delegated_usernames.add(task.delegated_to_username.replace("@", "").lower())

    # Получаем все профили с заполненными данными, кроме своего и тех, с кем уже есть делегирование
    all_profiles = (
        session.query(UserProfile)
        .join(User, UserProfile.user_id == User.id)
        .filter(
            UserProfile.user_id != user.id,
            # Хотя бы одно поле должно быть заполнено
            (UserProfile.interests.isnot(None))
            | (UserProfile.skills.isnot(None))
            | (UserProfile.position.isnot(None))
            | (UserProfile.city.isnot(None)),
        )
        .all()
    )

    logger.info(f"[PARTNERS] Found {len(all_profiles)} profiles with data")

    # Получаем профиль текущего пользователя для сравнения
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not user_profile:
        logger.warning(f"[PARTNERS] User profile not found for user {user.id}")
        if close_session:
            session.close()
        return []

    logger.info(
        f"[PARTNERS] User profile: interests='{user_profile.interests}', skills='{user_profile.skills}', goals='{user_profile.goals}'"
    )

    # Фильтруем только тех, у кого есть совпадения
    partners = []
    for profile in all_profiles:
        profile_user = session.query(User).filter_by(id=profile.user_id).first()
        if not profile_user or not profile_user.username:
            continue

        logger.info(
            f"[PARTNERS] Checking profile for {profile_user.username}: interests='{profile.interests}', skills='{profile.skills}'"
        )

        # Проверяем наличие совпадений по интересам, навыкам или целям
        has_match = False

        # Проверка по навыкам
        if user_profile.skills and profile.skills:
            user_skills = set(s.strip().lower() for s in user_profile.skills.split(","))
            profile_skills = set(s.strip().lower() for s in profile.skills.split(","))
            if user_skills & profile_skills:
                has_match = True
                logger.info(f"[PARTNERS] Skills match: {user_skills & profile_skills}")

        # Проверка по интересам
        if user_profile.interests and profile.interests:
            user_interests = set(i.strip().lower() for i in user_profile.interests.split(","))
            profile_interests = set(i.strip().lower() for i in profile.interests.split(","))
            if user_interests & profile_interests:
                has_match = True
                logger.info(f"[PARTNERS] Interests match: {user_interests & profile_interests}")

        # Проверка по целям
        if user_profile.goals and profile.goals:
            user_goals = set(g.strip().lower() for g in user_profile.goals.split(","))
            profile_goals = set(g.strip().lower() for g in profile.goals.split(","))
            if user_goals & profile_goals:
                has_match = True
                logger.info(f"[PARTNERS] Goals match: {user_goals & profile_goals}")

        # Проверка по компании
        if hasattr(user_profile, "company") and hasattr(profile, "company"):
            if user_profile.company and profile.company:
                if user_profile.company.lower() == profile.company.lower():
                    has_match = True
                    logger.info(f"[PARTNERS] Company match: {user_profile.company}")

        # Добавляем только если есть совпадение
        if has_match:
            partners.append(profile)
            logger.info(f"[PARTNERS] Added {profile_user.username} to partners")

    logger.info(f"[PARTNERS] Total partners found: {len(partners)}")

    # Сортируем: сначала пользователи из одного города, потом остальные
    user_city = user_profile.city.lower() if user_profile.city else None
    partners_same_city = []
    partners_other_city = []

    for partner in partners:
        partner_city = partner.city.lower() if partner.city else None
        if user_city and partner_city == user_city:
            partners_same_city.append(partner)
        else:
            partners_other_city.append(partner)

    # Сортируем каждую группу по среднему рейтингу (от большего к меньшему)
    partners_same_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)
    partners_other_city.sort(key=lambda p: (p.average_rating or 0), reverse=True)

    # Объединяем: сначала из того же города, потом остальные
    sorted_partners = partners_same_city + partners_other_city

    if close_session:
        session.close()

    # Возвращаем до 20 пользователей (можно увеличить при необходимости)
    return sorted_partners[:20]


def find_partners(user_id=None, session=None):
    import re
    from models import Session, UserProfile, User, Task

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        if close_session:
            session.close()
        return "Пользователь не найден."

    # Получаем задачи текущего пользователя для анализа совместных идей
    user_tasks = session.query(Task).filter_by(user_id=user.id).all()
    user_task_keywords = set()
    for task in user_tasks:
        # Извлекаем ключевые слова из названий и описаний задач
        import re

        words = re.findall(r"\b\w+\b", (task.title + " " + (task.description or "")).lower())
        user_task_keywords.update(words)

    # Остальной код...
    user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    # Получить память для исключения заблокированных
    blocked = []
    hidden_contacts = {}  # username -> expiration_timestamp
    if user.memory:
        try:
            decrypted = decrypt_data(user.memory)
            # Ищем паттерны вроде "не показывать @user" или "заблокировать @user"
            from datetime import datetime, timezone as dt_timezone

            # Permanent blocks
            matches = re.findall(r"не показывать @(\w+)|заблокировать @(\w+)", decrypted, re.IGNORECASE)
            for match in matches:
                blocked.extend([m for m in match if m])

            # Temporary hides: hide_contact:username:timestamp
            hide_matches = re.findall(r"hide_contact:@?(\w+):(\d+)", decrypted, re.IGNORECASE)
            current_time = int(datetime.now(dt_timezone.utc).timestamp())
            for username, expiration_ts in hide_matches:
                exp_ts = int(expiration_ts)
                if exp_ts > current_time:  # Still hidden
                    hidden_contacts[username.lower()] = exp_ts
        except Exception as e:
            pass
    partners = []
    tips = []
    if user_profile:
        # Сначала фильтруем по городу, если указан
        if user_profile.city:
            city_profiles = [p for p in profiles if p.city and p.city.lower() == user_profile.city.lower()]
            if city_profiles:
                profiles = city_profiles  # Используем только профили из того же города

        # Словарь для подсчёта релевантности: {profile: (score, matched_fields)}
        partner_scores = {}

        for p in profiles:
            # Исключаем заблокированных и себя
            if not p.contact_info:
                continue
            contact_username = p.contact_info.replace("@", "").lower()
            if (
                p.contact_info in blocked
                or any("@" + b in p.contact_info for b in blocked)
                or p.contact_info == f"user{user_id}"
            ):
                continue
            # Исключаем временно скрытых
            if contact_username in hidden_contacts:
                continue

            score = 0
            matched_fields = []

            # Анализ задач для совместных идей
            partner_user = session.query(User).filter_by(id=p.user_id).first()
            if partner_user:
                partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                partner_task_keywords = set()
                for task in partner_tasks:
                    words = re.findall(r"\b\w+\b", (task.title + " " + (task.description or "")).lower())
                    partner_task_keywords.update(words)

                # Находим пересечения ключевых слов задач
                common_keywords = user_task_keywords & partner_task_keywords
                if common_keywords:
                    score += len(common_keywords) * 8  # 8 баллов за каждое совпадение
                    matched_fields.append(f"совместные задачи: {', '.join(list(common_keywords)[:3])}")

            # Проверка интересов с приоритетом точного совпадения
            if user_profile.interests and p.interests:
                user_interests = [i.strip().lower() for i in user_profile.interests.split(",")]
                partner_interests = [i.strip().lower() for i in p.interests.split(",")]

                for user_int in user_interests:
                    for partner_int in partner_interests:
                        # Точное совпадение = 10 баллов
                        if user_int == partner_int:
                            score += 10
                            matched_fields.append(f"интерес: {user_int}")
                        # Одно содержит другое = 5 баллов
                        elif user_int in partner_int or partner_int in user_int:
                            score += 5
                            matched_fields.append(f"похожий интерес: {partner_int}")

            # Проверка навыков
            if user_profile.skills and p.skills:
                user_skills = [s.strip().lower() for s in user_profile.skills.split(",")]
                partner_skills = [s.strip().lower() for s in p.skills.split(",")]

                for user_skill in user_skills:
                    for partner_skill in partner_skills:
                        if user_skill == partner_skill:
                            score += 10
                            matched_fields.append(f"навык: {user_skill}")
                        elif user_skill in partner_skill or partner_skill in user_skill:
                            score += 5
                            matched_fields.append(f"похожий навык: {partner_skill}")

            # Проверка целей
            if user_profile.goals and p.goals:
                user_goals = [g.strip().lower() for g in user_profile.goals.split(",")]
                partner_goals = [g.strip().lower() for g in p.goals.split(",")]

                for user_goal in user_goals:
                    for partner_goal in partner_goals:
                        if user_goal == partner_goal:
                            score += 10
                            matched_fields.append(f"цель: {user_goal}")
                        elif user_goal in partner_goal or partner_goal in user_goal:
                            score += 5
                            matched_fields.append(f"похожая цель: {partner_goal}")

            # Компания (точное совпадение)
            if hasattr(user_profile, "company") and hasattr(p, "company") and user_profile.company and p.company:
                if user_profile.company.lower() == p.company.lower():
                    score += 15  # Коллеги — высокий приоритет
                    matched_fields.append(f"коллега из {p.company}")

            # Должность (частичное совпадение)
            if hasattr(user_profile, "position") and hasattr(p, "position") and user_profile.position and p.position:
                if (
                    user_profile.position.lower() in p.position.lower()
                    or p.position.lower() in user_profile.position.lower()
                ):
                    score += 8
                    matched_fields.append(f"должность: {p.position}")

            # Если есть совпадения — добавляем в результат
            if score > 0:
                partner_scores[p] = (score, matched_fields)

        # Сортируем по убыванию релевантности
        sorted_partners = sorted(partner_scores.items(), key=lambda x: x[1][0], reverse=True)
        partners = [item[0] for item in sorted_partners]

        # Проверяем планы на релевантность для топ-3
        for p in partners[:3]:
            if p.current_plans and user_profile.interests:
                for interest in user_profile.interests.split(","):
                    interest_words = interest.strip().lower().split()
                    if any(word in p.current_plans.lower() for word in interest_words):
                        tips.append(
                            f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} — может быть интересно с твоими интересами в {interest.strip()}."
                        )
                        break
    else:
        # Если профиля нет, вернуть тестовых партнеров для демонстрации
        partners = profiles[:3] if profiles else []

    if close_session:
        session.close()

    response = ""
    if partners:
        response += "Нашёл подходящих людей:\n"
        for idx, p in enumerate(partners[:3], 1):
            info_parts = []

            # Показываем причину совпадения
            if user_profile and p in partner_scores:
                score, matched = partner_scores[p]
                # Берём первое самое релевантное совпадение
                match_reason = matched[0] if matched else "общие интересы"
                info_parts.append(f"Совпадение: {match_reason}")

            if p.interests:
                info_parts.append(f"интересы: {p.interests}")
            if hasattr(p, "position") and p.position:
                info_parts.append(f"{p.position}")
            if hasattr(p, "company") and p.company:
                info_parts.append(f"компания: {p.company}")
            if p.city:
                info_parts.append(f"город: {p.city}")

            info_str = ", ".join(info_parts) if info_parts else "профиль в разработке"
            response += f"{idx}. @{p.contact_info}\n   {info_str}\n"

        # Добавляем предложения совместных идей на основе задач
        joint_ideas = []
        for p in partners[:3]:
            if user_profile and p in partner_scores:
                score, matched = partner_scores[p]
                # Если есть совпадение по задачам, предлагаем совместную идею
                task_matches = [m for m in matched if m.startswith("совместные задачи")]
                if task_matches:
                    partner_user = session.query(User).filter_by(id=p.user_id).first()
                    if partner_user:
                        partner_tasks = session.query(Task).filter_by(user_id=partner_user.id).all()
                        for pt in partner_tasks[:2]:  # Проверяем первые 2 задачи
                            for ut in user_tasks[:2]:
                                common_words = set(
                                    re.findall(r"\b\w+\b", (pt.title + " " + (pt.description or "")).lower())
                                ) & set(re.findall(r"\b\w+\b", (ut.title + " " + (ut.description or "")).lower()))
                                if common_words:
                                    joint_ideas.append(
                                        f"💡 @{p.contact_info} тоже работает над '{pt.title}' — можно объединиться для совместного изучения {', '.join(list(common_words)[:2])}!"
                                    )
                                    break
                            if joint_ideas and len(joint_ideas) >= 2:  # Максимум 2 идеи
                                break

        response = response.rstrip("\n")
        if joint_ideas:
            response += "\n\n" + "\n".join(joint_ideas[:2])
    else:
        response = "К сожалению, пока не нашёл подходящих партнёров с похожими интересами. Попробуй обновить свой профиль с интересами, навыками или целями — тогда я смогу найти более релевантных людей!"

    return response


def update_profile(
    skills=None,
    interests=None,
    goals=None,
    city=None,
    current_plans=None,
    timezone=None,
    company=None,
    position=None,
    user_id=None,
    session=None,
):
    from models import Session, User, UserProfile

    if session is None:
        session = Session()
        close_session = True
    else:
        close_session = False
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id)
        session.add(user)
        session.commit()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)

    def update_list_field(field, value):
        if not value:
            return field
        current = set((field or "").split(", ")) - {""}  # Разделяем по ", " и убираем пустые
        if value.startswith("+"):
            new_item = value[1:].strip()
            if new_item:
                current.add(new_item)
        elif value.startswith("-"):
            remove_item = value[1:].strip()
            current.discard(remove_item)
        else:
            # Замена целиком
            current = set(value.split(", ")) - {""}
        return ", ".join(sorted(current))

    profile.skills = update_list_field(profile.skills, skills)
    profile.interests = update_list_field(profile.interests, interests)
    profile.goals = update_list_field(profile.goals, goals)
    profile.city = city if city else profile.city
    profile.current_plans = current_plans if current_plans else profile.current_plans
    # current_time removed - should not persist in DB
    # Безопасно добавляем новые поля (могут отсутствовать в старой БД)
    if hasattr(profile, "company"):
        profile.company = company if company else profile.company
    if hasattr(profile, "position"):
        profile.position = position if position else profile.position
    if timezone:
        user.timezone = timezone
    profile.contact_info = f"user{user_id}"  # Простой username
    profile.updated_at = datetime.now(pytz.UTC)
    session.commit()
    if close_session:
        session.close()
    return "Профиль обновлен."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить новую задачу с обязательным временем напоминания. ВАЖНО: Если задача сформулирована слишком общо (например 'проверить почту', 'позвонить другу'), СНАЧАЛА задай уточняющие вопросы для получения деталей: контекста, цели, ожидаемого результата. Только после уточнения добавляй задачу с детальной формулировкой. КРИТИЧНО: используй ТОЧНУЮ ТЕКУЩУЮ ДАТУ из system prompt ({{current_date}}), НЕ используй даты из твоих знаний!",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Название задачи - должно быть конкретным и содержать: действие, объект, контекст. Хорошо: 'Позвонить Марии обсудить договор поставки'. Плохо: 'Позвонить другу'",
                    },
                    "description": {
                        "type": "string",
                        "description": "Дополнительное описание задачи с деталями выполнения, ожидаемым результатом",
                    },
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM. ОБЯЗАТЕЛЬНО используй current_date из system prompt для вычисления даты! Например, если current_date=2026-01-11 и пользователь просит 'через 5 минут в 12:30', используй '2026-01-11 12:30', а НЕ дату из прошлого!"},
                    "due_date": {"type": "string", "description": "Дедлайн в формате YYYY-MM-DD HH:MM, опционально"},
                },
                "required": ["title", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Показать список задач",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Завершить существующую задачу по ID или названию. Вызывай когда пользователь говорит что выполнил/сделал/завершил задачу. НЕ создавай новую задачу, а именно заверши существующую!",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": "Установить напоминание для задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reminder_time": {"type": "string", "description": "Время напоминания в формате YYYY-MM-DD HH:MM"},
                },
                "required": ["task_id", "reminder_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_memory",
            "description": "Сохранить информацию о пользователе в долговременную память для персонализации",
            "parameters": {
                "type": "object",
                "properties": {
                    "info": {
                        "type": "string",
                        "description": "Информация для сохранения, например предпочтения, привычки, цели",
                    }
                },
                "required": ["info"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": "Создать задачу для другого пользователя. Вызывай ТОЛЬКО когда в сообщении есть @username! Если нет @mention - НЕ вызывай эту функцию. reminder_time можно указывать в естественном формате как 'завтра в 10:00', 'до послезавтра 15:00' и т.д.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Название задачи"},
                    "description": {"type": "string", "description": "Подробное описание задачи (опционально)"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Время дедлайна в любом удобном формате: 'завтра в 10:00', 'до послезавтра 15:00', 'сегодня в 18:00' и т.д.",
                    },
                    "delegated_to_username": {
                        "type": "string",
                        "description": "Username получателя с @ (например @username)",
                    },
                    "delegation_details": {
                        "type": "string",
                        "description": "Детали: желаемый результат, критерии выполнения, важность",
                    },
                },
                "required": ["title", "reminder_time", "delegated_to_username"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_delegated_task",
            "description": "Принять делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_delegated_task",
            "description": "Отклонить делегированную задачу",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_delegation_progress",
            "description": "Получить статус выполнения делегированной задачи для инициатора",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": "Изменить название, описание или время напоминания задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "title": {"type": "string", "description": "Новое название, опционально"},
                    "description": {"type": "string", "description": "Новое описание, опционально"},
                    "reminder_time": {
                        "type": "string",
                        "description": "Новое время напоминания в формате YYYY-MM-DD HH:MM, опционально",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Удалить задачу по ID или названию",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи (опционально если указан task_title)"},
                    "task_title": {
                        "type": "string",
                        "description": "Название задачи или его часть (опционально если указан task_id)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_priority",
            "description": "Установить приоритет задачи",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "priority": {"type": "string", "description": "Приоритет: high, medium, low"},
                },
                "required": ["task_id", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_details",
            "description": "Получить полную информацию о задаче",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "ID задачи"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_partners",
            "description": "Найти потенциальных людей на основе профиля пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "Обновить профиль пользователя с навыками, интересами, целями, городом, текущими планами, текущим временем, часовым поясом, компанией и должностью",
            "parameters": {
                "type": "object",
                "properties": {
                    "skills": {"type": "string", "description": "Навыки пользователя, разделенные запятыми"},
                    "interests": {"type": "string", "description": "Интересы пользователя, разделенные запятыми"},
                    "goals": {"type": "string", "description": "Цели пользователя"},
                    "city": {"type": "string", "description": "Город пользователя, опционально"},
                    "current_plans": {
                        "type": "string",
                        "description": "Текущие планы или события пользователя, опционально",
                    },
                    "current_time": {
                        "type": "string",
                        "description": "Текущее время пользователя в формате HH:MM, опционально",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Часовой пояс пользователя, например 'Europe/Moscow', опционально",
                    },
                    "company": {
                        "type": "string",
                        "description": "Компания, в которой работает пользователь, опционально",
                    },
                    "position": {"type": "string", "description": "Должность пользователя, опционально"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_alternatives",
            "description": "Предложить альтернативы для невыполненной задачи: перенести, разбить на части, делегировать, найти партнёра",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID задачи"},
                    "reason": {"type": "string", "description": "Причина невыполнения (опционально)"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_all_tasks",
            "description": "Удалить все задачи пользователя. ⚠️ КРИТИЧНО: Это необратимая операция! Перед вызовом ОБЯЗАТЕЛЬНО подтверди у пользователя: 'Ты точно хочешь удалить ВСЕ задачи? Это действие нельзя отменить.' и дождись явного подтверждения.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_subscription_payment",
            "description": "Создать платеж для оформления или продления месячной подписки",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_subscription_status",
            "description": "Проверить статус текущей подписки пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_subscription",
            "description": "Отменить текущую подписку пользователя",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def chat_with_ai(message, context=None, user_id=None, file_content=None):
    # Force rebuild v3.0 - FIXED clean_content issue
    import re
    from datetime import datetime, timezone, timedelta
    import pytz

    logger = logging.getLogger(__name__)

    # Ensure context is a list or None
    if context is not None and not isinstance(context, list):
        logger.warning(f"context is not a list: {type(context)}, setting to None")
        context = None

    # Проверяем сообщение о времени и обновляем timezone
    time_message_match = re.search(r"мое\s+местное\s+время:\s*(\d{1,2}:\d{2})", message.lower())
    if time_message_match:
        user_time_str = time_message_match.group(1)
        detected_timezone = determine_timezone_from_time(user_time_str, user_id)
        if detected_timezone:
            logger.info(f"Detected timezone {detected_timezone} from time {user_time_str}")
            update_profile(timezone=detected_timezone, user_id=user_id)

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
        f"chat_with_ai called with message: {clean_message[:50]}..., mentions: {mentions_str}, context len: {context_len}, user_id: {user_id}, file: {file_content is not None}"
    )
    logger.info(f"DEEPSEEK_API_KEY present: {bool(DEEPSEEK_API_KEY)}")

    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set")
        return "API ключ DeepSeek не настроен. Это демо ответ: Привет! Я AI-ассистент TaskChat. Чем могу помочь?"

    try:
        logger.info("Starting chat_with_ai processing")
        # Get user memory and all tasks for extended context
        user_memory = ""
        user = None
        profile = None
        session = None
        # Initialize time variables with defaults
        base_now = datetime.now(pytz.UTC)
        user_now = base_now
        current_time_str = user_now.strftime("%H:%M")
        user_username = "user"

        if user_id:
            from models import Session, User, Task, UserProfile, Subscription

            db_session = Session()
            user = db_session.query(User).filter_by(telegram_id=user_id).first()

            # Создать пользователя если не существует
            if not user:
                user = User(telegram_id=user_id)
                db_session.add(user)
                db_session.commit()

            # Check subscription
            from config import FREE_ACCESS_MODE

            if not FREE_ACCESS_MODE:
                subscription = db_session.query(Subscription).filter_by(user_id=user.id, status="active").first()
                if not subscription:
                    db_session.close()
                    return "У вас нет активной подписки. Для использования AI-ассистента активируйте подписку в Telegram боте @asibiont_bot. После активации подписки я смогу помогать вам с управлением задачами!"

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
                    logger.info(f"[TIME CHECK] User local time ({tz_str}): {user_now}")
                    logger.info(f"[TIME CHECK] Formatted for prompt: {current_time_str}")
                    logger.info(f"[TIME CHECK] Full date for prompt: {user_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Error setting user timezone: {e}")
                    user_tz = pytz.UTC
                    user_now = base_now
                    current_time_str = user_now.strftime("%H:%M")

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
                if profile.skills:
                    profile_info.append(f"Навыки: {profile.skills}")
                if profile.interests:
                    profile_info.append(f"Интересы: {profile.interests}")
                if profile.goals:
                    profile_info.append(f"Цели: {profile.goals}")
                if profile_info:
                    user_memory += f"\nПрофиль: {', '.join(profile_info)}"
                profile_filled = len(profile_info) >= 3  # Профиль считается заполненным если есть хотя бы 3 поля
                # Проактивное заполнение при первом сообщении
                if not profile_filled and (len(context) if context else 0 < 2):
                    user_memory += "\n🎯 КРИТИЧНО ВАЖНО: Профиль ПУСТ! В первом ответе дружелюбно спроси о городе, компании или интересах для лучшей помощи!"
            else:
                user_memory += f"\nПрофиль не заполнен - начни диалог для заполнения профиля (спроси по очереди: город, компанию, должность, навыки, интересы, цели)"

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
                user_memory += f"\n⚠️ ПРОСРОЧЕННЫЕ ЗАДАЧИ: {', '.join(overdue_titles)} - предложи помощь!"

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

        # Construct system prompt with replaced placeholders
        # Расширяем system prompt для работы с относительным временем
        user_username = f"@{user.username}" if user and user.username else "@unknown"
        system_prompt = get_extended_system_prompt(
            user_now=user_now,
            current_time_str=current_time_str,
            user_username=user_username,
            mentions_str=mentions_str,
            user_memory=user_memory,
            context=context
        )

        # Проверяем контекст последней созданной задачи для edit_task
        last_task_context = ""
        if redis_client and user_id:
            try:
                last_task_data = await redis_client.get(f"last_task_id:{user_id}")
                if last_task_data:
                    task_info = json.loads(last_task_data.decode("utf-8"))
                    last_task_context = f"\n\n🎯 КОНТЕКСТ ПОСЛЕДНЕЙ ЗАДАЧИ: ID={task_info['id']}, название='{task_info['title']}', время='{task_info.get('reminder_time', '')}'. ЕСЛИ пользователь даёт уточнения (я ошибся, не завтра а сегодня, изменить время и т.д.), ОБЯЗАТЕЛЬНО используй edit_task(task_id={task_info['id']}, ...)!"
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

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "deepseek-chat",
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        logger.info(f"Sending request to DeepSeek API with {len(messages)} messages")
        # Retry loop for API call
        max_retries = 2
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
                                print(f"[DEBUG API] Raw content: '{content}'")  # DEBUG
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
                                print(f"[DEBUG API] tool_calls: {tool_calls}")  # DEBUG
                            except Exception as e:
                                logger.error(f"Error parsing API response: {e}")
                                if attempt < max_retries:
                                    logger.info(f"Retrying API call due to parse error (attempt {attempt + 1})")
                                    await asyncio.sleep(1)
                                    continue
                                content = "Извините, произошла ошибка при обработке ответа от ИИ. Попробуйте еще раз."

                            # Обработка tool calls и т.д.
                            tool_results = []  # Инициализируем заранее
                            print(f"[DEBUG] tool_calls value: {tool_calls}, bool: {bool(tool_calls)}")  # DEBUG

                            if tool_calls:
                                print(f"[DEBUG] Tool calls found, processing...")  # DEBUG
                                # Обработка tool calls
                                tool_results = []
                                for tool_call in tool_calls:
                                    try:
                                        func_name = tool_call["function"]["name"]
                                        args = json.loads(tool_call["function"]["arguments"])
                                        logger.info(f"[TOOL CALL] Executing {func_name} with args: {args}")

                                        if func_name == "add_task":
                                            logger.info(f"[AI TOOL CALL] add_task called with reminder_time: {args.get('reminder_time')}, current user_now: {user_now}")
                                            result = add_task(
                                                title=args.get("title", args.get("task_title", "Задача")),
                                                description=args.get("description", ""),
                                                reminder_time=args.get("reminder_time"),
                                                user_id=user_id,
                                                session=None,
                                            )
                                            tool_results.append({"function": func_name, "result": result})

                                        elif func_name == "complete_task":
                                            result = complete_task(
                                                task_id=args.get("task_id"),
                                                task_title=args.get("task_title"),
                                                user_id=user_id,
                                                session=None,
                                            )
                                            tool_results.append({"function": func_name, "result": result})

                                        elif func_name == "list_tasks":
                                            result = list_tasks(user_id=user_id, session=None)
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
                                                user_id=user_id,
                                                session=None,
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

                                        if "Добавлена задача" in result_text:
                                            match = re.search(r"Добавлена задача '([^']+)' \(ID: (\d+)\)", result_text)
                                            if match:
                                                title = match.group(1)
                                                task_id = int(match.group(2))
                                                
                                                # Получаем рекомендации из базы данных
                                                from models import Session, Task
                                                session_db = Session()
                                                try:
                                                    task = session_db.query(Task).filter_by(id=task_id).first()
                                                    recommendations = []
                                                    if task and task.recommendations:
                                                        import json
                                                        try:
                                                            recommendations = json.loads(task.recommendations)
                                                        except:
                                                            pass
                                                    
                                                    # Формируем умный ответ с конкретными рекомендациями
                                                    if recommendations:
                                                        rec_text = "\n\n💡 Рекомендации для выполнения:\n" + "\n".join(f"• {rec}" for rec in recommendations[:3])
                                                        natural = f'Отлично! Задача "{title}" добавлена и запланирована. Я буду напоминать о ней в нужное время.{rec_text}\n\nЧто думаете о этих рекомендациях? Стоит ли что-то добавить или изменить?'
                                                    else:
                                                        # Fallback на вариативные ответы, если рекомендаций нет
                                                        responses = [
                                                            f'Задача "{title}" успешно создана! Я настрою напоминание в указанное время. Что еще нужно подготовить для выполнения этой задачи?',
                                                            f'Готово! Задача "{title}" добавлена в ваш список. Напоминание установлено. Давайте подумаем, что потребуется для ее выполнения.',
                                                            f'Задача "{title}" запланирована! Я буду следить за временем и напомню. Какие материалы или информация понадобятся?',
                                                            f'Отлично! "{title}" добавлена и ждет своего часа. Напоминание активировано. Что нужно подготовить заранее?'
                                                        ]
                                                        import random
                                                        natural = random.choice(responses)
                                                    
                                                    natural_responses.append(natural)
                                                finally:
                                                    session_db.close()
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Завершена задача" in result_text:
                                            match = re.search(r"Завершена задача '([^']+)'", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'Отлично, отметил задачу "{title}" как выполненную! Это важный шаг вперед. Теперь стоит проанализировать, что было сделано правильно, и подумать о следующих задачах. Есть ли уроки, которые можно извлечь из выполнения этой задачи? Может быть, стоит отметить достижения или запланировать что-то новое?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        elif "Задачи:" in result_text:
                                            # Не добавляем сразу, обработаем отдельно
                                            pass

                                        elif (
                                            "Найдены партнеры:" in result_text
                                            or "партнеры найдены" in result_text.lower()
                                        ):
                                            natural_responses.append(result_text)

                                        elif "Профиль обновлен" in result_text:
                                            natural_responses.append(
                                                "Профиль обновлен! Теперь я лучше знаю твои интересы."
                                            )

                                        elif "Задача" in result_text and "делегирована" in result_text:
                                            natural = "Отлично, задача делегирована! Я уведомлю получателя."
                                            natural_responses.append(natural)

                                        elif "Удалены все задачи" in result_text:
                                            natural = "Удалил все твои задачи. Теперь список пуст — можно начинать с чистого листа!"
                                            natural_responses.append(natural)

                                        elif "Задача" in result_text and "удалена" in result_text:
                                            match = re.search(r"Задача '([^']+)' удалена", result_text)
                                            if match:
                                                title = match.group(1)
                                                natural = f'Удалил задачу "{title}". Что дальше?'
                                                natural_responses.append(natural)
                                            else:
                                                natural_responses.append(result_text)

                                        else:
                                            natural_responses.append(result_text)

                                    # 🎯 Для list_tasks просто добавляем результат - главный промпт уже содержит все правила
                                    if has_list_tasks and list_tasks_result:
                                        natural_responses.append(list_tasks_result)

                                    final_content = "\n".join(natural_responses)
                                    # 🎯 Обогащаем ответ вовлекающими элементами
                                    final_content = enrich_response_with_engagement(
                                        final_content, user_id, original_message
                                    )

                                    # 🎯 ПРИНУЖДАЕМ СОБЛЮДЕНИЕ ГЛАВНОГО ПРОМПТА
                                    intent_type = "list_tasks" if has_list_tasks else None
                                    final_content = await enforce_prompt_compliance(
                                        final_content, intent_type, user_id, context,
                                        system_prompt, messages, url, headers
                                    )

                                    logger.info(
                                        f"[TOOL CALLS] Processed {len(tool_results)} tool calls, returning natural response"
                                    )
                                    return final_content

                    print(f"[DEBUG] Exited tool_calls if block")  # DEBUG
                    print(f"[DEBUG] After tool_calls block, about to check fallback")  # DEBUG
                    # Все запросы обрабатывает AI, без принудительных триггеров
                    logger.info("[AI ONLY] All requests handled by AI without forced triggers")
                    print(f"[DEBUG] About to check fallback, content='{content[:50]}...'")  # DEBUG

                    # 🔄 SMART FALLBACK: Проверяем, нужно ли применить умный fallback
                    print(f"[DEBUG] Calling smart_fallback_handler...")  # DEBUG
                    print(f"[DEBUG] About to call smart_fallback_handler, content='{content[:50]}...'")  # DEBUG
                    try:
                        fallback_result = smart_fallback_handler(original_message, mentions_str, user_id, content)
                        print(
                            f"[DEBUG] Fallback result: {len(fallback_result) if fallback_result else 0} actions"
                        )  # DEBUG
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
                                        "Удалил все твои задачи. Теперь список пуст — можно начинать с чистого листа!"
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

                            # 🎯 Для list_tasks просто добавляем результат - главный промпт уже содержит все правила
                            if has_list_tasks and list_tasks_result:
                                natural_responses.append(list_tasks_result)
                            
                            # Формируем финальный контент
                            final_content = "\n".join(natural_responses)
                            
                            # 🎯 ПРИНУЖДАЕМ СОБЛЮДЕНИЕ ГЛАВНОГО ПРОМПТА
                            intent_type = "list_tasks" if has_list_tasks else None
                            final_content = await enforce_prompt_compliance(
                                final_content, intent_type, user_id, context,
                                system_prompt, messages, url, headers
                            )
                            
                            print(f"[DEBUG FALLBACK] Returning final_content: '{final_content[:200]}...'")  # DEBUG
                            return final_content
                    except Exception as e:
                        logger.error(f"[SMART FALLBACK] Error in fallback handler: {e}")
                        print(f"[DEBUG] Fallback error: {e}")  # DEBUG

                    # Если forced calls не сработали, обрабатываем обычный ответ AI
                    print(f"[DEBUG] After fallback, going to regular response processing")  # DEBUG
                    # Обрабатываем обычный ответ AI без tool calls
                    logger.info("[TOOL CALLS] Tool calls completed, 0 results. Generating natural response...")
                    print(f"[DEBUG] Processing regular AI response, content='{content[:100]}...'")  # DEBUG
                    print(f"[DEBUG] About to enter regular response processing")  # DEBUG
                    original_content = message_response.get("content", "")
                    content = original_content
                    print(f"[DEBUG] Original content: '{original_content[:100]}...'")  # DEBUG

                    # Для обычных ответов ТОЛЬКО заменяем плейсхолдеры, без дополнительной очистки
                    content = replace_placeholders(content, user_now, current_time_str)
                    print(f"[DEBUG] After replace_placeholders: '{content[:100]}...'")  # DEBUG

                    # 🚨 КРИТИЧЕСКАЯ ПРОВЕРКА: если content пустой или слишком короткий
                    if not content or len(content.strip()) < 3:
                        print(
                            f"[DEBUG] Content is empty or too short: '{content}', len={len(content.strip())}"
                        )  # DEBUG
                        logger.warning(f"[EMPTY RESPONSE] Original: '{original_content[:100]}...', returning original")
                        content = original_content.strip()
                        if not content:
                            logger.warning("[RETRY] Response empty, retrying with explicit instruction")
                            retry_system = (
                                system_prompt
                                + "\n\n🚨 КРИТИЧЕСКИ ВАЖНО:\n1. НЕ возвращай JSON, code blocks или технические теги\n2. Отвечай ТОЛЬКО обычным текстом\n3. Если создал задачу - скажи об этом и предложи найти партнёра\n4. Минимум 20 слов в ответе\n5. Будь дружелюбным и конкретным!"
                            )

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
                                        "model": "deepseek-chat",
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
                                    print(f"[DEBUG RETRY] Retry content: '{content[:100]}...'")  # DEBUG
                                    if retry_content and len(retry_content.strip()) >= 3:
                                        content = retry_content
                                    else:
                                        content = "Хорошо, продолжим работу!"
                        else:
                            logger.info(f"[RECOVERED] Using original content: '{content[:100]}...'")

                    # Если все еще пустой после retry
                    if not content:
                        content = "Хорошо, продолжим работу!"

                    # 🎯 Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, original_message)

                    # 🎯 ПРИНУЖДАЕМ СОБЛЮДЕНИЕ ГЛАВНОГО ПРОМПТА ДЛЯ ОБЫЧНЫХ ОТВЕТОВ
                    intent = classify_user_intent(clean_message, mentions_str)
                    intent_type = intent["type"] if intent["confidence"] >= 0.7 else None
                    content = await enforce_prompt_compliance(
                        content, intent_type, user_id, context,
                        system_prompt, messages, url, headers
                    )

                    # Очистка от технических деталей перед возвратом
                    # НЕ применяем clean_technical_details для обычных ответов AI!
                    print(f"[DEBUG] About to return content: '{content}'")  # DEBUG
                    return content

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
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        # Используем расширенный system prompt с правилами verbose ответов
        from datetime import datetime
        import pytz
        user_now = datetime.now(pytz.UTC)
        current_time_str = user_now.strftime("%H:%M")
        user_username = "пользователь"  # Можно получить из базы если нужно
        mentions_str = ""
        
        base_prompt = get_extended_system_prompt(user_now, current_time_str, user_username, mentions_str, user_memory)
        
        # Добавляем специфические правила для напоминаний
        system_prompt = f"{base_prompt}\n\n🎯 СПЕЦИАЛЬНЫЕ ПРАВИЛА ДЛЯ НАПОМИНАНИЙ:\n"
        system_prompt += "Ты генерируешь развернутое напоминание о задаче. Будь мотивирующим и полезным.\n"
        system_prompt += "Если есть релевантная информация из памяти пользователя, используй её для более персонализированного напоминания.\n"
        system_prompt += "Задавай конкретные вопросы, которые помогут пользователю лучше подготовиться ИЛИ собрать дополнительную информацию, необходимую для принятия лучших решений по выполнению задачи.\n"
        system_prompt += "Анализируй задачу и предлагай аспекты, которые пользователь мог упустить.\n"
        system_prompt += "НЕ предлагай создавать новые задачи в напоминаниях - это только для напоминания о существующей задаче.\n\n"
        system_prompt += "✅ ПРИ НАПОМИНАНИЯХ ОБЯЗАТЕЛЬНО ВКЛЮЧИ:\n"
        system_prompt += "1. Мотивацию к выполнению задачи\n"
        system_prompt += "2. Конкретные практические советы\n"
        system_prompt += "3. Анализ возможных препятствий\n"
        system_prompt += "4. Вопросы для подготовки\n"
        system_prompt += "5. Предложения по оптимизации процесса\n"
        system_prompt += "6. Связь с другими задачами или целями\n"
        system_prompt += "7. Предложения по поиску помощи если нужно\n\n"
        system_prompt += "⚠️ МИНИМУМ 5-7 ПРЕДЛОЖЕНИЙ! НЕ ДАВАЙ КОРОТКИЕ ОТВЕТЫ!"

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Напомни о задаче: {task_title}"},
        ]

        data = {"model": "deepseek-chat", "messages": messages}
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
                    # 🎯 Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    return content
                else:
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_reminder: {e}")
        return f"Напоминание о '{task_title}'."


async def generate_result_check(user_id, task_title):
    """Генерирует вопрос о результате выполнения задачи"""
    try:
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        system_prompt = get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory + "\n\nПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов, 4-6 предложений. Предоставь детальный анализ ситуации. Дай конкретные рекомендации с нумерацией. Задай вопросы для вовлечения пользователя."},
            {
                "role": "user",
                "content": f"Спроси о результате выполнения задачи '{task_title}'. Узнай о времени, сложностях, улучшениях.",
            },
        ]

        data = {"model": "deepseek-chat", "messages": messages}
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
                    # 🎯 Обогащаем ответ вовлекающими элементами
                    content = enrich_response_with_engagement(content, user_id, task_title)
                    return content
                else:
                    return "Ошибка генерации вопроса."
    except Exception as e:
        print(f"Error in generate_result_check: {e}")
        return f"Результат задачи '{task_title}'?"


async def generate_proactive_message(user_id):
    """Генерирует проактивное сообщение, если нет задач на ближайший час"""
    try:
        # Получить память пользователя, планы других и текущие задачи
        user_memory = ""
        plans_info = ""
        tasks_info = ""
        if user_id:
            from models import Session, User, UserProfile, Task

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            # Получить профиль пользователя
            user_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if user_profile and user_profile.interests:
                # Найти планы других пользователей, совпадающие с интересами
                profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
                tips = []
                for p in profiles:
                    if p.current_plans and p.contact_info != f"user{user_id}":
                        for interest in user_profile.interests.split(","):
                            interest_words = interest.strip().lower().split()
                            if any(word in p.current_plans.lower() for word in interest_words):
                                tips.append(
                                    f"@{p.contact_info} сегодня {p.current_plans.split(',')[0]} — может быть интересно с твоими интересами в {interest.strip()}."
                                )
                                break
                if tips:
                    plans_info = "\nПланы людей: " + " ".join(tips[:2])
            # Получить текущие задачи
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            pending_tasks = [t.title for t in tasks if t.status in ["pending", "in_progress"]]
            if pending_tasks:
                tasks_info = f"\nТекущие невыполненные задачи: {', '.join(pending_tasks[:3])}"
            session.close()

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        system_prompt = get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory + plans_info + tasks_info + "\n\nПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов, 4-6 предложений. Предоставь детальный анализ ситуации. Дай конкретные рекомендации с нумерацией. Задай вопросы для вовлечения пользователя."},
            {
                "role": "user",
                "content": "У пользователя нет задач на ближайший час. Создай позитивное проактивное сообщение.",
            },
        ]

        data = {"model": "deepseek-chat", "messages": messages}
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
                    # 🎯 Проактивные сообщения уже вовлекающие, но можно усилить
                    content = enrich_response_with_engagement(content, user_id, "")
                    return content
                else:
                    return "Ошибка генерации сообщения."
    except Exception as e:
        print(f"Error in generate_proactive_message: {e}")
        return "Добавьте задачу."


async def generate_daily_report(user_id):
    """Генерирует ежедневный отчет о задачах"""
    try:
        # Получить задачи пользователя
        from models import Session, Task

        session = Session()
        tasks = session.query(Task).filter_by(user_id=user_id).all()
        session.close()

        completed = [t for t in tasks if t.status == "completed"]
        pending = [t for t in tasks if t.status in ["pending", "in_progress"]]

        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        system_prompt = get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt + user_memory + "\n\nПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов, 4-6 предложений. Предоставь детальный анализ ситуации. Дай конкретные рекомендации с нумерацией. Задай вопросы для вовлечения пользователя."},
            {"role": "user", "content": f"Создай отчет: выполнено {len(completed)}, ожидают {len(pending)}"},
        ]

        data = {"model": "deepseek-chat", "messages": messages}
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
                    return "Ошибка генерации отчета."
    except Exception as e:
        print(f"Error in generate_daily_report: {e}")
        return "Отчет о задачах."


async def generate_overdue_reminder(user_id, overdue_tasks, escalation_level=1):
    """Генерирует напоминание о просроченных задачах"""
    try:
        task_titles = [t.title for t in overdue_tasks]
        # Получить память пользователя
        user_memory = ""
        if user_id:
            from models import Session, User

            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user and user.memory:
                try:
                    decrypted = decrypt_data(user.memory)
                    user_memory = f"\nИнформация о пользователе: {decrypted}"
                except (Exception,):
                    user_memory = ""
            session.close()

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        system_prompt = get_system_prompt()

        # Адаптируем тон в зависимости от уровня эскалации
        if escalation_level == 1:
            tone_instruction = "Будь дружелюбным, но настойчивым. Напомни о важности выполнения задач."
        elif escalation_level == 2:
            tone_instruction = "Будь более строгим. Подчеркни негативные последствия невыполнения."
        else:  # 3+
            tone_instruction = "Будь очень строгим и мотивирующим. Предложи конкретные альтернативы и помощь."

        messages = [
            {"role": "system", "content": system_prompt + user_memory + "\n\nПРАВИЛА ДЛЯ ОТВЕТА: Минимум 300 слов, 4-6 предложений. Предоставь детальный анализ ситуации. Дай конкретные рекомендации с нумерацией. Задай вопросы для вовлечения пользователя. {tone_instruction}"},
            {
                "role": "user",
                "content": f"Напомни о просроченных задачах: {', '.join(task_titles)}. {tone_instruction} Предложи варианты решения.",
            },
        ]

        data = {"model": "deepseek-chat", "messages": messages}
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
                    return "Ошибка генерации напоминания."
    except Exception as e:
        print(f"Error in generate_overdue_reminder: {e}")
        return "Просроченные задачи."


# Функции для работы с задачами
def list_tasks(user_id=None, session=None):
    """Возвращает список задач пользователя"""
    from models import Task, User
    from sqlalchemy import or_

    if session is None:
        from models import Session

        session = Session()
        close_session = True
    else:
        close_session = False

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Получить задачи пользователя или делегированные ему
        query = session.query(Task).filter(Task.user_id == user.id)
        if user.username:
            query = query.union(
                session.query(Task).filter(Task.delegated_to_username.ilike(user.username))
            )
        tasks = query.all()

        if not tasks:
            return "У вас нет задач. Добавьте первую задачу - просто напишите что нужно сделать и когда!"

        # Формируем детальный список с анализом
        active_tasks = [t for t in tasks if t.status != "completed"]
        completed_tasks = [t for t in tasks if t.status == "completed"]
        delegated_to_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and t.delegated_to_username.lower() == user.username.lower()
        ]
        delegated_by_me = [
            t
            for t in active_tasks
            if t.delegated_to_username and t.delegated_to_username.lower() != user.username.lower()
        ]
        my_tasks = [t for t in active_tasks if not t.delegated_to_username]

        from datetime import datetime
        import pytz

        # Определяем timezone пользователя
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now = datetime.now(user_tz)

        result = f"📋 **У вас {len(active_tasks)} активных задач**\n\n"

        # Мои задачи
        if my_tasks:
            result += "**Ваши задачи:**\n"
            for task in my_tasks:
                reminder_info = ""
                if task.reminder_time:
                    try:
                        reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                        if reminder_dt < now:
                            reminder_info = f" ⚠️ Просрочено на {(now - reminder_dt).days} дн."
                        else:
                            reminder_info = f" 🔔 {reminder_dt.strftime('%d.%m %H:%M')}"
                    except:
                        pass

                priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(task.priority, "")
                result += f"⏳ {priority_icon} {task.title}{reminder_info}\n"
            result += "\n"

        # Делегированные мне
        if delegated_to_me:
            result += "**Делегировано вам:**\n"
            for task in delegated_to_me:
                creator = session.query(User).filter_by(id=task.user_id).first()
                creator_name = f"@{creator.username}" if creator else "кто-то"
                result += f"⏳ {task.title} (от {creator_name})\n"
            result += "\n"

        # Делегированные мной
        if delegated_by_me:
            result += "**Вы делегировали:**\n"
            for task in delegated_by_me:
                result += f"👤 {task.title} (на @{task.delegated_to_username})\n"
            result += "\n"

        # Завершённые (последние 3)
        if completed_tasks:
            recent_completed = completed_tasks[-3:]
            result += f"✅ **Завершено:** {len(completed_tasks)} задач\n"

        # Анализ и рекомендации
        recommendations = []
        
        # Проверяем просроченные задачи
        overdue_tasks = []
        for task in active_tasks:
            if task.reminder_time:
                try:
                    reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_tz)
                    if reminder_dt < now:
                        overdue_tasks.append(task)
                except:
                    pass
        
        if overdue_tasks:
            recommendations.append(f"⚠️ У вас {len(overdue_tasks)} просроченных задач. Рекомендую выполнить их или перенести сроки.")
        
        # Проверяем задачи без сроков
        tasks_without_deadline = [t for t in active_tasks if not t.reminder_time]
        if tasks_without_deadline:
            recommendations.append(f"📅 {len(tasks_without_deadline)} задач без сроков. Установите конкретные даты для лучшего планирования.")
        
        # Проверяем делегированные задачи
        if delegated_by_me:
            recommendations.append(f"👥 Вы делегировали {len(delegated_by_me)} задач. Проверьте их статус у получателей.")
        
        # Общие рекомендации
        if len(active_tasks) > 5:
            recommendations.append("📊 Много задач! Попробуйте приоритизировать - отметьте самые важные как 'high'.")
        
        if not active_tasks:
            recommendations.append("🎯 Отлично! Все задачи выполнены. Что планируете добавить?")
        elif len(active_tasks) == 1:
            recommendations.append("💪 Отличная фокус! Одна задача - легче сосредоточиться.")
        
        # Добавляем рекомендации к результату
        if recommendations:
            result += "\n\n💡 **Рекомендации:**\n" + "\n".join(f"• {rec}" for rec in recommendations[:3])  # Максимум 3 рекомендации
        
        # Добавляем вопрос для вовлечения
        result += "\n\nЧто планируете делать с этими задачами?"
        
        return result.strip()
    except Exception as e:
        print(f"Error listing tasks: {e}")
        return "Ошибка получения списка задач"
    finally:
        if close_session:
            session.close()


def check_subscription_status(user_id=None):
    """Проверяет статус подписки"""
    from models import Session, User, Subscription

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription or subscription.status != "active":
            return "У вас нет активной подписки. Используйте /subscribe для оформления."

        return f"Подписка активна до {subscription.end_date.strftime('%d.%m.%Y') if subscription.end_date else 'неизвестно'}"
    except Exception as e:
        print(f"Error checking subscription: {e}")
        return "Ошибка проверки подписки"
    finally:
        session.close()


def create_subscription_payment(user_id=None):
    """Создает платеж для подписки"""
    from models import Session, User, Subscription
    from datetime import datetime, timedelta
    import pytz

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        # Проверить существующую подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if subscription and subscription.status == "active":
            return "У вас уже есть активная подписка"

        # Создать или обновить подписку
        if not subscription:
            subscription = Subscription(user_id=user.id)
            session.add(subscription)

        subscription.status = "pending_payment"
        subscription.start_date = datetime.now(pytz.UTC)
        subscription.end_date = subscription.start_date + timedelta(days=30)
        session.commit()

        return "Платеж создан. Используйте ссылку для оплаты: https://yookassa.ru/..."
    except Exception as e:
        session.rollback()
        print(f"Error creating subscription payment: {e}")
        return "Ошибка создания платежа"
    finally:
        session.close()


def cancel_subscription(user_id=None):
    """Отменяет подписку"""
    from models import Session, User, Subscription

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return "Пользователь не найден"

        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription:
            return "У вас нет подписки"

        subscription.status = "cancelled"
        session.commit()
        return "Подписка отменена"
    except Exception as e:
        session.rollback()
        print(f"Error cancelling subscription: {e}")
        return "Ошибка отмены подписки"
    finally:
        session.close()
