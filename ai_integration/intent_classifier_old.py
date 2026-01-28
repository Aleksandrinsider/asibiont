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

        prompt = f"""
Ты - классификатор намерений для системы управления задачами.

Проанализируй сообщение и верни ТОЛЬКО одно слово - название функции из списка:

ДОСТУПНЫЕ ФУНКЦИИ:
{chr(10).join([f"- {name}: {desc.split('.')[0]}" for name, desc in cls.INTENTS.items()])}

ПРАВИЛА:
- Используй ТОЧНОЕ название функции
- Если не уверен - верни "conversation"
- Для создания задач используй "add_task"
- Для завершения задач используй "complete_task"
- Для просмотра задач используй "list_tasks"
- Для удаления задач используй "delete_task"

Сообщение: "{message}"

Ответь ТОЛЬКО названием функции:
"""
   - "я выполнил уборку и хочу создать новую задачу"
   - "покажи задачи и напомни о встрече"
   - "сделай это и то"
   - Любые составные запросы с "и", "а также", "плюс"

3. СПЕЦИФИЧЕСКИЕ ПРАВИЛА:
   - Если сообщение начинается со слов "готово", "сделал", "выполнил", "завершил" - это complete_task
   - Если сообщение содержит "создать задачу", "напомнить", "запланировать" - это create_task
   - Если сообщение содержит "покажи", "список", "какие" + "задачи/дела" - это list_tasks
   - Если сообщение содержит "удали", "убери", "отмени" + задачу - это delete_task

ПРИМЕРЫ КОРРЕКТНОЙ КЛАССИФИКАЦИИ:

3. СПЕЦИФИЧЕСКИЕ ПРАВИЛА ПО ФУНКЦИЯМ:
   - add_task: ТОЛЬКО для создания новых задач с временем ("напомни купить хлеб завтра в 9")
   - complete_task: ТОЛЬКО для завершения существующих задач ("сделал уборку", "выполнил задачу")
   - list_tasks: ТОЛЬКО для просмотра списка задач ("покажи мои задачи", "список дел")
   - reschedule_task: ТОЛЬКО для переноса времени задач ("перенеси встречу на завтра в 16:00")
   - edit_task: ТОЛЬКО для изменения названия/описания задач ("измени название задачи на X")
   - delete_task: ТОЛЬКО для удаления задач ("удали задачу о встрече")
   - set_recurring_task: ТОЛЬКО для повторяющихся задач ("напоминай о зарядке каждый день в 8:00")
   - update_profile: ТОЛЬКО для обновления профиля ("обнови мой профиль: город Москва")
   - find_partners: ТОЛЬКО для поиска партнеров ("найди партнеров по интересам")
   - get_task_details: ТОЛЬКО для деталей одной задачи ("покажи детали задачи про презентацию")
   - update_user_memory: ТОЛЬКО для сохранения памяти ("запомни что я предпочитаю чай")
   - delegate_task: ТОЛЬКО для делегирования ("делегируй Ивану проверить документы")
   - get_delegation_progress: ТОЛЬКО для статуса делегирования ("покажи статус делегированных задач")
   - accept_delegated_task: ТОЛЬКО для принятия делегированной задачи
   - reject_delegated_task: ТОЛЬКО для отклонения делегированной задачи
   - delete_all_tasks: ТОЛЬКО для удаления ВСЕХ задач (ОПАСНО!)
   - conversation: Все остальное (привет, как дела, вопросы, непонятные запросы)

ПРИМЕРЫ КОРРЕКТНОЙ КЛАССИФИКАЦИИ:

✅ add_task:
"создай задачу купить молоко"
"напомни позвонить другу"
"запланируй встречу на завтра"

✅ complete_task:
"готово купить молоко"
"сделал уборку"
"выполнил задачу о звонке"

✅ list_tasks:
"покажи мои задачи"
"список дел"
"какие у меня задачи"

✅ reschedule_task:
"перенеси встречу на завтра в 16:00"
"измени время задачи про почту на 15:30"

✅ edit_task:
"измени название задачи на 'Встреча с командой'"
"добавь описание к задаче о презентации"

✅ set_recurring_task:
"напоминай о зарядке каждый день в 8:00"
"проверяй почту каждую неделю по понедельникам"

✅ update_profile:
"обнови мой профиль: город Москва"
"добавь в профиль навыки Python"

✅ find_partners:
"найди партнеров по интересам"
"поищи контакты для проекта"

✅ get_task_details:
"покажи детали задачи про презентацию"
"что в задаче о встрече"

✅ update_user_memory:
"запомни что я предпочитаю чай"
"помни что у меня аллергия на орехи"

✅ delegate_task:
"делегируй Ивану проверить документы"
"поручи @maria подготовить отчет на завтра"

✅ get_delegation_progress:
"покажи статус делегированных задач"

❌ conversation (ОПАСНЫЕ ИЛИ СЛОЖНЫЕ):
"удали все задачи" → conversation
"создай 1000 задач" → conversation
"я выполнил уборку и хочу создать новую задачу" → conversation
"покажи задачи и напомни о встрече" → conversation
"SELECT * FROM tasks" → conversation
"DROP TABLE users" → conversation
"<script>alert('xss')</script>" → conversation
"привет, как дела?" → conversation
"что ты умеешь?" → conversation

Сообщение: "{message}"

Ответь ТОЛЬКО в формате JSON:
""" + '{"intent": "add_task"}'

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
            DeleteTaskCommand, ConversationCommand
        )

        # Map function names from TOOLS to command classes
        mapping = {
            'add_task': CreateTaskCommand,  # add_task -> create_task
            'list_tasks': ListTasksCommand,
            'complete_task': CompleteTaskCommand,
            'delete_task': DeleteTaskCommand,
            'reschedule_task': ConversationCommand,  # Пока используем conversation
            'edit_task': ConversationCommand,  # Пока используем conversation
            'set_recurring_task': ConversationCommand,  # Пока используем conversation
            'update_profile': ConversationCommand,  # Пока используем conversation
            'find_partners': ConversationCommand,  # Пока используем conversation
            'get_task_details': ConversationCommand,  # Пока используем conversation
            'update_user_memory': ConversationCommand,  # Пока используем conversation
            'delete_all_tasks': ConversationCommand,  # Пока используем conversation
            'delegate_task': ConversationCommand,  # Пока используем conversation
            'get_delegation_progress': ConversationCommand,  # Пока используем conversation
            'accept_delegated_task': ConversationCommand,  # Пока используем conversation
            'reject_delegated_task': ConversationCommand,  # Пока используем conversation
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

        prompt = f"""
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