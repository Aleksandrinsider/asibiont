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

Примеры:
"Создай задачу на завтра" → add_task
"Покажи мои задачи" → list_tasks
"Я закончил задачу" → complete_task
"Удали задачу" → delete_task
"Перенеси задачу на завтра" → reschedule_task
"Я из Москвы" → update_profile
"Найди партнеров" → find_partners
"Привет" → conversation

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

                # Clean response and return as intent (fully trust AI)
                intent = response.strip().lower()
                # Remove any extra text, keep only the first word if multiple
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
            CreateTaskCommand, CompleteTaskCommand, ListTasksCommand,
            DeleteTaskCommand, RescheduleTaskCommand, UpdateProfileCommand, 
            FindPartnersCommand, DelegateTaskCommand, ConversationCommand
        )

        mapping = {
            'add_task': CreateTaskCommand,
            'complete_task': CompleteTaskCommand,
            'list_tasks': ListTasksCommand,
            'delete_task': DeleteTaskCommand,
            'reschedule_task': RescheduleTaskCommand,
            'update_profile': UpdateProfileCommand,
            'find_partners': FindPartnersCommand,
            'delegate_task': DelegateTaskCommand,
            'conversation': ConversationCommand,
        }

        return mapping.get(intent, ConversationCommand)