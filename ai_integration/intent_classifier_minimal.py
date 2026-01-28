import json
from typing import Optional
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .tools import TOOLS

class IntentClassifierMinimal:
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

        # Very minimal prompt - let AI figure it out
        prompt = f"""
Ты - эксперт по анализу намерений в системе управления задачами.

Проанализируй сообщение пользователя и определи его основное намерение.
Верни ТОЛЬКО одно слово - название функции из доступных команд.

Доступные команды: add_task, complete_task, list_tasks, delete_task, reschedule_task, edit_task, set_recurring_task, update_profile, find_partners, get_task_details, update_user_memory, delete_all_tasks, delegate_task, get_delegation_progress, conversation

Если сообщение не подходит ни под одну команду - верни "conversation".

Сообщение: "{message}"

Ответ (только одно слово):
"""

        try:
            response = await cls._call_ai(prompt)

            # Clean response and check if it's a valid intent
            if response:
                intent = response.strip().lower()
                # Remove any extra text, keep only the first word
                intent = intent.split()[0] if intent else "conversation"
                if intent in cls.INTENTS:
                    return intent

            return 'conversation'

        except Exception as e:
            print(f"Intent classification error: {e}")
            return 'conversation'

    @classmethod
    def get_command_class(cls, intent: str):
        """Map intent to command class"""
        from .commands import (
            AddTaskCommand, CompleteTaskCommand, ListTasksCommand,
            DeleteTaskCommand, RescheduleTaskCommand, EditTaskCommand,
            SetRecurringTaskCommand, UpdateProfileCommand, FindPartnersCommand,
            GetTaskDetailsCommand, UpdateUserMemoryCommand, DeleteAllTasksCommand,
            DelegateTaskCommand, GetDelegationProgressCommand
        )

        mapping = {
            'add_task': AddTaskCommand,
            'complete_task': CompleteTaskCommand,
            'list_tasks': ListTasksCommand,
            'delete_task': DeleteTaskCommand,
            'reschedule_task': RescheduleTaskCommand,
            'edit_task': EditTaskCommand,
            'set_recurring_task': SetRecurringTaskCommand,
            'update_profile': UpdateProfileCommand,
            'find_partners': FindPartnersCommand,
            'get_task_details': GetTaskDetailsCommand,
            'update_user_memory': UpdateUserMemoryCommand,
            'delete_all_tasks': DeleteAllTasksCommand,
            'delegate_task': DelegateTaskCommand,
            'get_delegation_progress': GetDelegationProgressCommand,
        }

        return mapping.get(intent)