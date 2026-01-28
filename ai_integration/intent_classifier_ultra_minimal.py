import json
from typing import Optional
import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .tools import TOOLS

class IntentClassifierUltraMinimal:
    """Ultra minimal intent classification - AI figures everything out"""

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
                "max_tokens": 30
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
        """Ultra minimal AI classification - let AI figure out everything"""

        # Ultra minimal prompt - no command list at all
        prompt = f"""
Ты - ИИ-ассистент для управления задачами. Проанализируй сообщение пользователя и определи, какую операцию он хочет выполнить.

Возможные операции: создание задачи, просмотр задач, завершение задачи, удаление задачи, перенос задачи, обновление профиля, поиск партнеров, общий разговор.

Верни ТОЛЬКО одно слово на английском: add_task, list_tasks, complete_task, delete_task, reschedule_task, update_profile, find_partners, или conversation.

Сообщение: "{message}"

Операция:
"""

        try:
            response = await cls._call_ai(prompt)

            # Clean response and check if it's a valid intent
            if response:
                intent = response.strip().lower()
                # Remove any extra text, keep only the first word
                intent = intent.split()[0] if intent else "conversation"

                # Map common variations to standard intents
                intent_mapping = {
                    'create_task': 'add_task',
                    'new_task': 'add_task',
                    'add': 'add_task',
                    'create': 'add_task',
                    'show_tasks': 'list_tasks',
                    'view_tasks': 'list_tasks',
                    'my_tasks': 'list_tasks',
                    'finish_task': 'complete_task',
                    'done': 'complete_task',
                    'finish': 'complete_task',
                    'готово': 'complete_task',
                    'сделал': 'complete_task',
                    'выполнил': 'complete_task',
                    'завершил': 'complete_task',
                    'я сделал': 'complete_task',
                    'я доработал': 'complete_task',
                    'я завершил': 'complete_task',
                    'я выполнил': 'complete_task',
                    'уже сделал': 'complete_task',
                    'уже выполнил': 'complete_task',
                    'уже завершил': 'complete_task',
                    'remove_task': 'delete_task',
                    'remove': 'delete_task',
                    'erase': 'delete_task',
                    'удали': 'delete_task',
                    'убери': 'delete_task',
                    'move_task': 'reschedule_task',
                    'change_time': 'reschedule_task',
                    'reschedule': 'reschedule_task',
                    'перенеси': 'reschedule_task',
                    'измени время': 'reschedule_task',
                    'update': 'update_profile',
                    'profile': 'update_profile',
                    'я из': 'update_profile',
                    'работаю': 'update_profile',
                    'интересует': 'update_profile',
                    'find': 'find_partners',
                    'partners': 'find_partners',
                    'search': 'find_partners',
                    'найди': 'find_partners',
                    'партнеры': 'find_partners',
                    'chat': 'conversation',
                    'talk': 'conversation',
                    'hello': 'conversation',
                    'hi': 'conversation',
                    'привет': 'conversation'
                }

                intent = intent_mapping.get(intent, intent)

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