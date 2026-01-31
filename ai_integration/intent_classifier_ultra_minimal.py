import json
from typing import Optional
import aiohttp
import logging
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from .tools import TOOLS

logger = logging.getLogger(__name__)

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
            logger.error(f"AI call failed: {e}")
            return "conversation"  # fallback

    @classmethod
    async def classify_intent(cls, message: str, user_id: int) -> str:
        """AI classification with context understanding"""

        prompt = f"""Анализ намерения пользователя в боте задач.

ТВОЯ ЗАДАЧА: Определи операцию и верни ТОЛЬКО английское слово.

ОПЕРАЦИИ:
add_task - создание задачи с напоминанием (напомни, создай, добавь, нужно, поставь напоминание)
complete_task - завершение задачи (готово, сделал, выполнил, закончил, завершил, проверил)
list_tasks - показ задач (покажи, список, что у меня, мои дела, запланировано)
delete_task - удаление одной задачи (удали задачу, убери встречу, отмени)
delete_all_tasks - удаление всех задач (удали все, очисти все, убери все дела)
reschedule_task - перенос времени задачи (перенеси, измени время, отложи, подвинь)
delegate_task - делегирование задачи другому (делегируй, поручи @username)
set_recurring_task - повторяющаяся задача (каждый день/неделю, напоминай регулярно, ежедневно)
get_task_details - получить детали задачи (расскажи подробнее о, детали задачи)
find_partners - поиск партнеров (найди партнеров, ищу единомышленников, подбери коллег)
find_relevant_contacts_for_task - поиск помощи для конкретной задачи (кто может помочь с, нужен дизайнер)
update_profile - обновление профиля (я из Москвы, работаю программистом, люблю спорт)
update_user_memory - сохранение в память (запомни что я, сохрани предпочтение)
conversation - остальное (привет, спасибо, как дела, что умеешь)

ПРИМЕРЫ КЛАССИФИКАЦИИ:
"Напомни позвонить клиенту завтра в 10" → add_task
"Создай задачу купить молоко через час" → add_task
"Добавь задачу проверить почту" → add_task
"Нужно сделать презентацию" → add_task
"Поставь напоминание встреча" → add_task
"Готово" → complete_task
"Сделал презентацию" → complete_task
"Выполнил задачу про почту" → complete_task
"Закончил встречу" → complete_task
"Уже проверил почту" → complete_task
"Покажи мои задачи" → list_tasks
"Что у меня запланировано" → list_tasks
"Список задач" → list_tasks
"Мои дела" → list_tasks
"Удали задачу про молоко" → delete_task
"Убери встречу" → delete_task
"Удали все задачи" → delete_all_tasks
"Очисти все напоминания" → delete_all_tasks
"Перенеси на завтра" → reschedule_task
"Отложи задачу на час" → reschedule_task
"Напоминай зарядку каждый день в 7 утра" → set_recurring_task
"Каждую среду в 19:00 напоминай про встречу" → set_recurring_task
"Каждый понедельник отчет" → set_recurring_task
"Ежедневно в 8 утра" → set_recurring_task
"Регулярно напоминай" → set_recurring_task
"Я из Москвы" → update_profile
"Работаю программистом" → update_profile
"Найди партнеров" → find_partners
"Ищу единомышленников" → find_partners
"Кто может помочь с дизайном" → find_relevant_contacts_for_task
"Нужен программист" → find_relevant_contacts_for_task
"Кто разбирается в маркетинге" → find_relevant_contacts_for_task
"Привет" → conversation
"Спасибо" → conversation

КРИТИЧНО:
• Если видишь "каждый день/неделю/час" или "ежедневно/еженедельно" → set_recurring_task, НЕ add_task!
• Если "кто может помочь с X" или "нужен X" → find_relevant_contacts_for_task, НЕ find_partners!
• Иначе для создания задачи → add_task

Сообщение: "{message}"

Операция (одно английское слово):"""

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
            logger.error(f"Intent classification error: {e}")
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