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
                    elif response.status == 401:
                        # API authentication failed - use local classification
                        print(f"API 401 error - switching to local classification")
                        return None  # Signal to use local classification
                    else:
                        return "conversation"  # fallback
        except Exception as e:
            print(f"AI call failed: {e}")
            return None  # Signal to use local classification

    @classmethod
    async def classify_intent(cls, message: str, user_id: int) -> str:
        """Ultra minimal AI classification - let AI figure out everything"""

        # For testing and reliability, prefer local classification
        # Only use AI if explicitly configured and working
        if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY in ['test_key', 'dummy_key'] or DEEPSEEK_API_KEY.startswith('test'):
            print(f"[INTENT] Using local classification for: {message[:50]}...")
            return cls._local_classify(message)

        print(f"[INTENT] Using AI classification for: {message[:50]}...")
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

            # If API call failed (returned None), use local classification
            if response is None:
                print(f"[INTENT] API call failed, using local classification for: {message[:50]}...")
                return cls._local_classify(message)

            print(f"[INTENT] AI response: '{response}'")
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
            return cls._local_classify(message)  # Fallback to local on error

    @classmethod
    def _local_classify(cls, message: str) -> str:
        """Local rule-based intent classification using improved patterns"""
        import re
        msg = message.lower().strip()
        
        # Enhanced intent mapping with regex patterns and context analysis
        intent_patterns = {
            # Add task patterns - look for creation keywords + time indicators
            'add_task': [
                r'\b(создай|добавь|напомни|поставь|нужно|запланируй|закажи|закажу|купить|сделать|подготовить|организовать)\b.*\b(завтра|сегодня|через|в|на|утром|вечером|днем)\b',
                r'\b(создай|добавь|напомни|поставь|нужно|запланируй)\b.*\b(задач|дело|напоминани|событи)\b',
                r'\b(напомни|поставь)\b.*\b(о|про|что)\b',
                r'\b(нужно|надо)\b.*\b(сделать|подготовить|организовать|купить|заказать)\b',
                r'\b(час|минут|день|недел|месяц)\b.*\b(назад|спустя|позже)\b',
                r'\b(встреча|совещани|звонок|позвонить|написать|отправить|приехать|уйти|вернуться)\b.*\b(в|на|завтра|сегодня|через)\b'
            ],
            
            # Complete task patterns
            'complete_task': [
                r'\b(готово|сделал|выполнил|завершил|закончил|выполнена|завершена|закончена)\b',
                r'\b(я сделал|я выполнил|я завершил|уже сделал|уже выполнил)\b',
                r'\b(отметь|пометить)\b.*\b(готов|выполнен|завершен)\b',
                r'\b(задача|дело)\b.*\b(готов|выполнен|завершен|сделан)\b'
            ],
            
            # List tasks patterns
            'list_tasks': [
                r'\b(покажи|список|мои|все|активные)\b.*\b(задач|дела|напоминани)\b',
                r'\b(что|какие)\b.*\b(задач|дела|напоминани)\b.*\b(у меня|есть)\b',
                r'\b(мои задачи|мои дела|список задач)\b',
                r'\b(что|какие)\b.*\b(дела|задачи)\b.*\b(на сегодня|сегодня|завтра|на этой неделе)\b',
                r'\b(расскажи|покажи)\b.*\b(что|какие)\b.*\b(дела|задачи)\b',
                r'\b(у меня есть|есть ли)\b.*\b(задачи|дела)\b'
            ],
            
            # Delete task patterns
            'delete_task': [
                r'\b(удали|убери|удалить|убрать|сотри|стереть|сбрось)\b.*\b(задач|дело|напоминани)\b',
                r'\b(больше не нужно|отмени|отменить|удали|убери)\b.*\b(задач|дело|напоминани)\b',
                r'\b(удали|убери)\b.*\b(про|о|задачу|дело)\b',
                r'\b(удали|убери)\b.*\b(звонок|встречу|покупк|отчет|презентаци)\b',
                r'\b(сотри|стереть)\b.*\b(напоминани|задач)\b',  # Добавлено для "Сотри напоминание"
                r'\bсотри\b.*\bнапоминани\b',  # Более конкретный паттерн для "Сотри напоминание"
                r'\bсбрось\b.*\bнапоминани\b'  # Для случаев типа "сбрось напоминание"
            ],
            
            # Delete all tasks patterns
            'delete_all_tasks': [
                r'\b(удали|убери|очисти|сотри)\b.*\b(все|всё)\b.*\b(задач|дела|напоминани)\b',
                r'\b(очистить|удалить)\b.*\b(список|все задачи|все дела)\b',
                r'\b(сброс|reset)\b.*\b(задач|дел|напоминани)\b',
                r'\b(удали все|убери все|очисти все)\b',
                r'\b(очисти|очистить)\b.*\b(список|все)\b',  # Добавлено для "Очисти список задач"
                r'\b(сбрось|сбросить)\b.*\b(все|всё)\b.*\b(напоминани|задач)\b',  # Добавлено для "Сбрось все напоминания"
                r'\bсбрось\b.*\bвсе\b.*\bнапоминани\b',  # Более конкретный паттерн для "Сбрось все напоминания"
                r'\bсбрось\b.*\bнапоминани\b'  # Для случаев типа "сбрось напоминания"
            ],
            
            # Edit task patterns
            'edit_task': [
                r'\b(измени|отредактируй|исправь|поправь|добавь|обнови)\b.*\b(задач|дело|напоминани|задачу|делу|описани)\b',
                r'\b(изменить|обновить|добавить)\b.*\b(время|дату|названи|описани|текст)\b',
                r'\b(отредактируй|измени)\b.*\b(задачу|дело)\b',
                r'\b(добавь|измени)\b.*\b(описани|названи)\b'
            ],
            
            # Reschedule task patterns
            'reschedule_task': [
                r'\b(перенеси|отложи|измени время|поменяй время|сдвинь)\b.*\b(задач|дело)\b',
                r'\b(перенеси|отложи)\b.*\b(на|через|завтра|позже)\b',
                r'\b(давай перенесем|перенесем|давай отложим)\b.*\b(на|через)\b',  # Добавлено для "Давай перенесем"
                r'\b(поставь|измени)\b.*\b(на другое время|позже|раньше)\b',
                r'\b(перенеси|отложи)\b.*\b(её|его|эту|ту)\b',
                r'\b(её|его|эту|ту)\b.*\b(перенеси|отложи)\b',
                r'\b(перенесем|отложим)\b.*\b(на|через|минут|час)\b'  # Добавлено для "перенесем на 5 минут"
            ],
            
            # Update profile patterns
            'update_profile': [
                r'\b(я из|работаю|интересует|занимаюсь|живу|город|компания|должность)\b',
                r'\b(обнови|измени|исправь)\b.*\b(профиль|данные|информаци)\b',
                r'\b(я|мне|мой)\b.*\b(имя|фамилия|город|работа|компания|должность|интересы|навыки|цели)\b',
                r'\b(интересует|интересуют|интересуемся)\b.*\b(программирован|машинн|обучен|разработк|дизайн|маркетинг|менеджмент)\b',
                r'\b(меня зовут|я|мое имя)\b.*\b(из|город|москва|питер|казань|екатеринбург)\b',
                r'\b(занимаюсь|работаю|программист|разработчик|дизайнер|менеджер|аналитик)\b',
                r'\b(люблю|увлекаюсь|интересуюсь)\b.*\b(программирован|кодинг|дизайн|фотографи|спорт|музык)\b',
                r'\b(интересуюсь|интересует меня)\b.*\b(программирован|машинн|обучен|разработк|дизайн|маркетинг)\b',
                r'\bинтересуюсь\b.*\b(и|python|машинным|обучением|программированием)\b'
            ],
            
            # Find partners patterns
            'find_partners': [
                r'\b(найди|поищи|ищу)\b.*\b(партнер|единомышленник|коллег|людей|друзей)\b',
                r'\b(кто похож|познакомь)\b.*\b(с людьми|с единомышленниками|на меня)\b',  # Добавлено "на меня"
                r'\b(найди единомышленников|поищи партнеров)\b',
                r'\b(хочу познакомиться|ищу знакомства)\b',
                r'\b(кто похож)\b.*\b(на меня)\b'  # Добавлено для "Кто похож на меня?"
            ],
            
            # Find relevant contacts for task patterns
            'find_relevant_contacts_for_task': [
                r'\b(кто может|кто поможет|нужен|ищу)\b.*\b(помочь|сделать|разобраться|помочь с)\b',
                r'\b(кто разбирается|кто знает|кто умеет)\b.*\b(в|с)\b',
                r'\b(нужен|ищу)\b.*\b(программист|дизайнер|менеджер|специалист|эксперт)\b',
                r'\b(кто может помочь)\b.*\b(с|в)\b',
                r'\b(помогите|нужна помощь)\b.*\b(с|в)\b'
            ],
            
            # Delegate task patterns - expanded for @mentions and delegation keywords
            'delegate_task': [
                r'\b(поручи|делегируй|передай|отдай)\b.*\b(задач|дело)\b',
                r'\b(кому-то|кому-нибудь|другому)\b.*\b(сделать|выполнить)\b',
                r'\b(поручи|делегируй|передай)\b.*@',
                r'@\w+.*\b(сделай|выполни|подготовь|организуй)\b',
                r'\b(задач|дело)\b.*@\w+',
                r'\b(поручи|делегируй)\b.*\b(кому|кому-то)\b',
                r'\b(передай|отдай)\b.*\b(задачу|дело)\b.*@\w+',
                r'\b(делегируй|поручи)\b.*\b(звонок|задачу|дело)\b.*@\w+',
                r'\b(делегируй|поручи)\b.*@\w+.*\b(сделать|выполнить|подготовить)\b'
            ],
            
            # Get task details patterns
            'get_task_details': [
                r'\b(расскажи|подробно|детали|информация)\b.*\b(о|про|задач|дело)\b',
                r'\b(что|какие)\b.*\b(детали|информация|подробности)\b.*\b(задач|дело)\b'
            ]
        }
        
        # Check patterns in order of priority (more specific first)
        priority_order = ['complete_task', 'delete_task', 'delete_all_tasks', 'delegate_task', 'reschedule_task', 'add_task', 'list_tasks', 'edit_task', 'find_relevant_contacts_for_task', 'get_task_details', 'update_profile', 'find_partners']
        
        for intent in priority_order:
            if intent in intent_patterns:
                for pattern in intent_patterns[intent]:
                    if re.search(pattern, msg, re.IGNORECASE):
                        return intent
        
        # Fallback to simple keyword matching for remaining cases
        simple_mapping = {
            'удали все': 'delete_all_tasks',
            'очисти': 'delete_all_tasks',
            'убери все': 'delete_all_tasks',
            'сбрось все': 'delete_all_tasks',
            'сотри': 'delete_task',
            'сбрось': 'delete_task',
            'запомни': 'update_user_memory',
            'сохрани': 'update_user_memory',
            'соглашусь': 'accept_delegated_task',
            'приму': 'accept_delegated_task',
            'откажусь': 'reject_delegated_task',
            'не могу': 'reject_delegated_task',
            'где': 'get_delegation_progress',
            'как дела': 'get_delegation_progress',
            'кто может': 'find_relevant_contacts_for_task',
            'нужен': 'find_relevant_contacts_for_task'
        }
        
        for key, intent in simple_mapping.items():
            if key in msg:
                return intent
        
        # Default to conversation
        return 'conversation'

    @classmethod
    def get_command_class(cls, intent: str):
        """Map intent to command class"""
        from .commands import (
            CreateTaskCommand, CompleteTaskCommand, ListTasksCommand,
            DeleteTaskCommand, RescheduleTaskCommand, UpdateProfileCommand, FindPartnersCommand,
            DelegateTaskCommand, ConversationCommand, GetTaskDetailsCommand,
            EditTaskCommand, FindRelevantContactsForTaskCommand, UpdateUserMemoryCommand, DeleteAllTasksCommand
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
            'get_task_details': GetTaskDetailsCommand,
            'edit_task': EditTaskCommand,
            'find_relevant_contacts_for_task': FindRelevantContactsForTaskCommand,
            'update_user_memory': UpdateUserMemoryCommand,
            'delete_all_tasks': DeleteAllTasksCommand,
        }

        return mapping.get(intent)