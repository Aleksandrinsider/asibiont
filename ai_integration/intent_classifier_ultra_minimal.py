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
        """AI classification with context understanding"""

        prompt = f"""Ты - анализатор намерений пользователя в боте управления задачами.

Определи ОДНУ операцию из сообщения. Верни ТОЛЬКО английское слово:

ОПЕРАЦИИ:
• add_task - создать новую задачу (напомни, создай, добавь)
• complete_task - завершить задачу (готово, сделал, выполнил, закончил)
• list_tasks - показать задачи (покажи, список, что у меня)
• delete_task - удалить задачу (удали, убери)
• reschedule_task - перенести задачу (перенеси, измени время)
• update_profile - обновить профиль (я из [город], работаю [кем], люблю [что])
• find_partners - найти партнеров (найди, ищу партнеров/коллег)
• delegate_task - делегировать (делегируй, поручи [кому])
• set_recurring_task - повторяющаяся задача (каждый день/неделю/месяц)
• delete_all_tasks - удалить все задачи (удали все, очисти все)
• conversation - обычный разговор, вопросы, уточнения

ПРИМЕРЫ:
"Готово отчет" → complete_task
"Сделал" → complete_task
"Выполнил задачу" → complete_task
"Напомни позвонить" → add_task
"Покажи задачи" → list_tasks
"Удали встречу" → delete_task
"Перенеси на завтра" → reschedule_task
"Я из Москвы" → update_profile
"Найди партнеров" → find_partners
"Делегируй Ивану" → delegate_task
"Каждый день зарядка" → set_recurring_task
"Удали все" → delete_all_tasks
"Привет" → conversation
"Как дела?" → conversation

Сообщение: "{message}"

Операция:"""

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