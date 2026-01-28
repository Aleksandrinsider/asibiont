import json
from typing import Optional
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .tools import TOOLS

class IntentClassifier:
    """AI-powered intent classification with minimal keywords - relies on AI understanding"""

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
                "max_tokens": 50
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
        """Use AI to classify user intent with minimal guidance"""

        # Very minimal prompt - let AI figure it out with command list
        prompt = f"""
Ты - эксперт по анализу намерений в системе управления задачами.

Проанализируй сообщение пользователя и определи его основное намерение.
Верни ТОЛЬКО в формате: КОМАНДА|УВЕРЕННОСТЬ

Где:
- КОМАНДА: одно слово из доступных команд
- УВЕРЕННОСТЬ: число от 0.0 до 1.0

Правила оценки уверенности:
- 0.9-1.0: Явное намерение (прямые команды: "создай задачу", "удали задачу", "покажи задачи")
- 0.7-0.8: Вероятное намерение (косвенные указания: "нужно сделать", "хочу найти", "я живу в")
- 0.5-0.6: Неоднозначное (может быть разное толкование)
- 0.0-0.4: Слишком неясно или не подходит

Доступные команды: add_task, complete_task, list_tasks, delete_task, reschedule_task, edit_task, set_recurring_task, update_profile, find_partners, get_task_details, update_user_memory, delete_all_tasks, delegate_task, get_delegation_progress, conversation

Примеры классификации:
"Создай задачу на завтра в 10 утра" → add_task|0.95
"Нужно сделать отчет к вечеру" → add_task|0.85
"Добавь напоминание о встрече" → add_task|0.9
"Я закончил с отчетом" → complete_task|0.9
"Уже выполнил задачу по уборке" → complete_task|0.85
"Готово с презентацией" → complete_task|0.9
"Сделал задачу" → complete_task|0.8
"Я доработал агента" → complete_task|0.9
"Завершил проект" → complete_task|0.9
"Выполнил задачу" → complete_task|0.85
"Покажи мои задачи" → list_tasks|0.95
"Какие у меня задачи" → list_tasks|0.9
"Удали задачу о покупке молока" → delete_task|0.9
"Сотри напоминание про встречу" → delete_task|0.85
"Измени время задачи на завтра" → reschedule_task|0.9
"Перенеси задачу на вечер" → reschedule_task|0.85
"Каждую неделю по средам напоминай" → set_recurring_task|0.95
"Ежедневно в 7 утра" → set_recurring_task|0.9
"Я живу в Санкт-Петербурге" → update_profile|0.75
"Мои хобби - фотография и путешествия" → update_profile|0.8
"Ищу единомышленников по дизайну" → find_partners|0.8
"Хочу найти коллег для приложений" → find_partners|0.85
"Расскажи подробнее о задаче" → get_task_details|0.9
"Что в задаче с презентацией" → get_task_details|0.85
"Запомни что я предпочитаю чай" → update_user_memory|0.9
"У меня аллергия на орехи" → update_user_memory|0.85
"Очисти все задачи" → delete_all_tasks|0.95
"Удали все напоминания" → delete_all_tasks|0.9
"Поручи задачу @user" → delegate_task|0.9
"Как продвигается делегированная задача" → get_delegation_progress|0.9
"Привет, как дела?" → conversation|0.95
"Спасибо за помощь" → conversation|0.9

Сообщение: "{message}"

Ответ (ТОЛЬКО в формате КОМАНДА|УВЕРЕННОСТЬ):
"""

        try:
            response = await cls._call_ai(prompt)

            # Clean response and check if it's a valid intent
            if response:
                response = response.strip().lower()
                if '|' in response:
                    parts = response.split('|')
                    if len(parts) == 2:
                        intent = parts[0].strip()
                        try:
                            confidence = float(parts[1].strip())
                        except:
                            confidence = 0.5
                    else:
                        intent = response.split()[0] if response else "conversation"
                        confidence = 0.5
                else:
                    intent = response.split()[0] if response else "conversation"
                    confidence = 0.5
                
                if intent in cls.INTENTS:
                    return f"{intent}|{confidence}"
                else:
                    return f"conversation|0.5"

            return "conversation|0.5"

        except Exception as e:
            print(f"Intent classification error: {e}")
            return "conversation|0.5"

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