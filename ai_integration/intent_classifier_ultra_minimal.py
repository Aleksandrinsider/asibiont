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
get_task_details - получить детали задачи (расскажи подробнее о, детали задачи, покажи детали)
delete_task - удаление одной задачи (удали задачу, убери встречу, отмени)
delete_all_tasks - удаление всех задач (удали все, очисти все, убери все дела)
reschedule_task - перенос времени задачи (перенеси, измени время, отложи, подвинь)
edit_task - изменение задачи (измени задачу, отредактируй, добавь описание, поменяй название)
delegate_task - делегирование задачи другому (делегируй, поручи @username)
accept_delegated_task - принять делегированную задачу (приму, соглашусь, выполню)
reject_delegated_task - отклонить делегированную задачу (отклоню, не могу, откажусь)
get_delegation_progress - статус делегированных задач (где моя задача, как дела с поручением)
find_partners - поиск партнеров (найди партнеров, ищу единомышленников, подбери коллег)
find_relevant_contacts_for_task - поиск помощи для конкретной задачи (кто может помочь с, нужен дизайнер)
update_profile - обновление профиля (я из Москвы, работаю программистом, люблю спорт)
update_user_memory - сохранение в память (запомни что я, сохрани предпочтение, не забудь что)
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
"Расскажи про задачу звонок" → get_task_details
"Покажи детали задачи про презентацию" → get_task_details
"Что с задачей купить молоко" → get_task_details
"Удали задачу про молоко" → delete_task
"Убери встречу" → delete_task
"Отмени напоминание про звонок" → delete_task
"Удали все задачи" → delete_all_tasks
"Очисти все напоминания" → delete_all_tasks
"Убери все дела" → delete_all_tasks
"Перенеси на завтра" → reschedule_task
"Отложи задачу на час" → reschedule_task
"Измени время встречи на 15:00" → reschedule_task
"Измени задачу про продукты: добавь описание" → edit_task
"Отредактируй задачу встреча: поменяй название" → edit_task
"Добавь описание к задаче" → edit_task
"Поручи задачу @ivanov" → delegate_task
"Делегируй встречу Петру" → delegate_task
"Соглашусь выполнить задачу от коллеги" → accept_delegated_task
"Приму поручение" → accept_delegated_task
"Отклоняю делегированную задачу" → reject_delegated_task
"Не смогу выполнить поручение" → reject_delegated_task
"Где моя делегированная задача" → get_delegation_progress
"Как дела с поручением от Петра" → get_delegation_progress
"Я из Москвы" → update_profile
"Работаю программистом" → update_profile
"Интересуюсь Python" → update_profile
"Запомни что я предпочитаю работать утром" → update_user_memory
"Сохрани: я не люблю телефонные звонки" → update_user_memory
"Не забудь что у меня аллергия" → update_user_memory
"Найди партнеров" → find_partners
"Ищу единомышленников" → find_partners
"Подбери коллег с похожими интересами" → find_partners
"Кто может помочь с дизайном" → find_relevant_contacts_for_task
"Нужен программист для проекта" → find_relevant_contacts_for_task
"Кто разбирается в маркетинге" → find_relevant_contacts_for_task
"Привет" → conversation
"Спасибо" → conversation
"Что ты умеешь" → conversation

КРИТИЧНО:
• Если "измени/отредактируй задачу" или "добавь описание/название" → edit_task, НЕ update_profile!
• Если "перенеси/отложи" ИЛИ меняется только ВРЕМЯ → reschedule_task, НЕ edit_task!
• Если "покажи детали/расскажи про задачу" → get_task_details, НЕ list_tasks!
• Если "запомни что я/сохрани" про предпочтения/факты → update_user_memory, НЕ update_profile!
• Если "я из/работаю/интересуюсь" → update_profile, НЕ update_user_memory!
• Если "кто может помочь с X" или "нужен X для задачи" → find_relevant_contacts_for_task, НЕ find_partners!
• Если "удали все/очисти все" → delete_all_tasks, НЕ delete_task!
• Если "поручи/делегируй @username" → delegate_task, НЕ add_task!
• Если "соглашусь/приму поручение" → accept_delegated_task!
• Если "откажусь/отклоню поручение" → reject_delegated_task!
• Если "где моя задача/как дела с поручением" → get_delegation_progress!
• Иначе для создания новой задачи → add_task
"Что ты умеешь" → conversation

КРИТИЧНО:
• Если видишь "каждый день/неделю/час" или "ежедневно/еженедельно" → set_recurring_task, НЕ add_task!
• Если "измени/отредактируй задачу" или "добавь описание к задаче" → edit_task, НЕ update_profile!
• Если "перенеси/отложи" ИЛИ меняется ВРЕМЯ → reschedule_task, НЕ edit_task!
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
                
                logger.info(f"[CLASSIFIER] Raw response: '{response}' -> parsed intent: '{intent}'")

                if intent in cls.INTENTS:
                    return intent
                else:
                    logger.warning(f"[CLASSIFIER] Intent '{intent}' not in INTENTS list, defaulting to conversation")

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