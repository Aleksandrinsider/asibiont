import json
from typing import Optional
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .tools import TOOLS

class IntentClassifier:
    """AI-powered intent classification for unlimited command variations"""

    # Extract all available intents from TOOLS
    INTENTS = {}
    for tool in TOOLS:
        name = tool["function"]["name"]
        description = tool["function"]["description"]
        INTENTS[name] = description

    # Add conversation as fallback
    INTENTS['conversation'] = 'Общий разговор или непонятный запрос'

    @classmethod
    async def _call_ai(cls, prompt: str) -> str:
        """Make a direct AI call for intent classification"""
        try:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 100
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result["choices"][0]["message"]["content"].strip()
                    else:
                        return "conversation"  # fallback
        except Exception as e:
            print(f"AI call failed: {e}")
            return "conversation"  # fallback

    @classmethod
    async def classify_intent(cls, message: str, user_id: int) -> str:
        """Use AI to classify user intent from natural language"""

        # Fast pre-check for recurring tasks (highest priority)
        message_lower = message.lower()
        recurring_keywords = ['каждый день', 'ежедневно', 'каждую неделю', 'еженедельно',
                             'каждый месяц', 'ежемесячно', 'каждый год', 'ежегодно',
                             'повторять', 'регулярно', 'каждые', 'еженедельно']
        if any(keyword in message_lower for keyword in recurring_keywords):
            return "set_recurring_task"

        # Fast pre-check for common patterns
        if any(word in message_lower for word in ['создай', 'создать', 'напомни', 'запланируй', 'добавь', 'нужно']):
            if 'задачу' in message_lower or 'дело' in message_lower or 'напомни' in message_lower:
                return "add_task"

        if any(word in message_lower for word in ['покажи', 'список', 'мои', 'какие']):
            if 'задач' in message_lower or 'дел' in message_lower:
                return "list_tasks"

        if any(word in message_lower for word in ['готово', 'сделал', 'выполнил', 'завершил', 'закончил']):
            return "complete_task"

        if any(word in message_lower for word in ['удали', 'убери', 'сотри']):
            if 'задач' in message_lower:
                return "delete_task"

        if any(word in message_lower for word in ['перенеси', 'измени время', 'поставь на']):
            return "reschedule_task"

        if any(word in message_lower for word in ['обнови', 'измени']):
            if 'профиль' in message_lower:
                return "update_profile"

        if any(word in message_lower for word in ['найди', 'поищи']):
            if 'партнер' in message_lower:
                return "find_partners"

        prompt = """
Ты - классификатор намерений для системы управления задачами.

ПРОСТЫЕ ПРАВИЛА:
- ЧИТАЙ СООБЩЕНИЕ ВНИМАТЕЛЬНО
- ВЫБИРАЙ ТОЛЬКО ИЗ СПИСКА НИЖЕ
- ЕСЛИ НЕТ ТОЧНОГО СОВПАДЕНИЯ - ВЕРНИ "conversation"

ТОЧНЫЕ СООТВЕТСТВИЯ:

add_task - Создание новой задачи с напоминанием
complete_task - Завершение существующей задачи
list_tasks - Просмотр списка задач
delete_task - Удаление задачи
reschedule_task - Изменение времени задачи
edit_task - Изменение текста задачи
set_recurring_task - Повторяющиеся задачи (уже проверено выше)
update_profile - Обновление информации о пользователе
find_partners - Поиск партнеров для сотрудничества
get_task_details - Просмотр деталей одной задачи
update_user_memory - Сохранение личных предпочтений
delete_all_tasks - Удаление всех задач
delegate_task - Делегирование задачи другому
get_delegation_progress - Статус делегированных задач
conversation - Все остальное

ПРИМЕРЫ:
"Создай задачу на завтра" -> add_task
"Какие у меня задачи" -> list_tasks
"Я сделал работу" -> complete_task
"Удалить задачу" -> delete_task
"Перенеси на завтра" -> reschedule_task
"Обнови профиль" -> update_profile
"Найди партнеров" -> find_partners
"Привет" -> conversation

Сообщение: "{message}"

Ответь ТОЛЬКО названием функции (одно слово):
"""

        try:
            # Use AI for classification
            response = await cls._call_ai(prompt)

            # Clean response and check if it's a valid intent
            if response:
                intent = response.strip().lower()
                if intent in cls.INTENTS:
                    return intent

            # Fallback to conversation if classification fails
            return 'conversation'

        except Exception as e:
            print(f"Intent classification error: {e}")
            return 'conversation'

    @classmethod
    def get_command_class(cls, intent: str):
        """Map intent to command class"""
        from .commands import (
            CreateTaskCommand, ListTasksCommand, CompleteTaskCommand,
            DeleteTaskCommand, RescheduleTaskCommand, UpdateProfileCommand,
            FindPartnersCommand, DelegateTaskCommand, ConversationCommand
        )

        # Map function names from TOOLS to command classes
        mapping = {
            'add_task': CreateTaskCommand,
            'create_task': CreateTaskCommand,  # Alias for router compatibility
            'list_tasks': ListTasksCommand,
            'complete_task': CompleteTaskCommand,
            'delete_task': DeleteTaskCommand,
            'reschedule_task': RescheduleTaskCommand,
            'edit_task': ConversationCommand,
            'set_recurring_task': ConversationCommand,
            'update_profile': UpdateProfileCommand,
            'find_partners': FindPartnersCommand,
            'get_task_details': ConversationCommand,
            'update_user_memory': ConversationCommand,
            'delete_all_tasks': ConversationCommand,
            'delegate_task': DelegateTaskCommand,
            'get_delegation_progress': ConversationCommand,
            'accept_delegated_task': ConversationCommand,
            'reject_delegated_task': ConversationCommand,
            'conversation': ConversationCommand,
        }

        return mapping.get(intent, ConversationCommand)

    @classmethod
    async def classify_intent_with_params(cls, message: str, user_id: int) -> dict:
        """Classify intent and extract parameters using AI"""
        intent = await cls.classify_intent(message, user_id)

        # If conversation, no parameters needed
        if intent == 'conversation':
            return {'type': intent, 'confidence': 0.9, 'params': {}}

        # Find the tool definition for this intent
        tool_def = None
        for tool in TOOLS:
            if tool["function"]["name"] == intent:
                tool_def = tool
                break

        if not tool_def:
            return {'type': intent, 'confidence': 0.9, 'params': {}}

        # Use AI to extract parameters based on the tool schema
        params = await cls._extract_parameters_with_ai(message, tool_def)
        return {'type': intent, 'confidence': 0.9, 'params': params}

    @classmethod
    async def _extract_parameters_with_ai(cls, message: str, tool_def: dict) -> dict:
        """Extract parameters using AI based on tool schema"""
        function_name = tool_def["function"]["name"]
        description = tool_def["function"]["description"]
        parameters = tool_def["function"]["parameters"]

        prompt = """
Извлеки параметры из сообщения пользователя на основе описания функции.

Функция: {function_name}
Описание: {description}
Параметры: {json.dumps(parameters, ensure_ascii=False, indent=2)}

Сообщение: "{message}"

Верни ТОЛЬКО JSON с извлеченными параметрами. Если параметр не найден в сообщении, не включай его в результат.
"""

        try:
            response = await cls._call_ai(prompt)
            if response:
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = response[start:end]
                    params = json.loads(json_str)
                    return params
        except Exception as e:
            print(f"Parameter extraction error: {e}")

        return {}