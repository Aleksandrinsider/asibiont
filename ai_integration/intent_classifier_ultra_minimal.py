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

            system_prompt = """Ты - классификатор намерений для AI-агента управления задачами.
            
На основе сообщения пользователя определи его намерение и верни ТОЛЬКО одно слово из списка:
- add_task (создание задачи)
- complete_task (завершение задачи)
- list_tasks (просмотр задач)
- delete_task (удаление задачи)
- reschedule_task (перенос задачи)
- update_profile (обновление профиля)
- find_partners (общий поиск партнеров/единомышленников)
- find_relevant_contacts_for_task (поиск контактов для конкретной задачи или специалиста)
- delegate_task (делегирование задачи)
- conversation (общий разговор, приветствия, вопросы о боте)

Верни ТОЛЬКО одно слово без объяснений."""

            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 20
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        result = await response.json()
                        intent = result["choices"][0]["message"]["content"].strip().lower()
                        # Validate intent
                        valid_intents = ['add_task', 'complete_task', 'list_tasks', 'delete_task', 'reschedule_task', 'update_profile', 'find_partners', 'delegate_task', 'conversation']
                        if intent in valid_intents:
                            return intent
                        else:
                            return None  # Use local classification
                    elif response.status == 401:
                        # API authentication failed - use local classification
                        print(f"API 401 error - switching to local classification")
                        return None  # Signal to use local classification
                    else:
                        return None  # Use local classification
        except Exception as e:
            print(f"AI call failed: {e}")
            return None  # Signal to use local classification

    @classmethod
    async def classify_intent(cls, message: str, user_id: int) -> str:
        """Use AI classification first, fallback to local"""
        
        # Try AI classification first
        ai_result = await cls._call_ai(message)
        if ai_result:
            print(f"[INTENT] AI classified '{message[:50]}...' as: {ai_result}")
            return ai_result
        
        # Fallback to local classification
        print(f"[INTENT] AI failed, using local classification for: {message[:50]}...")
        result = cls._local_classify(message)
        
        # If result is dict, extract intent and store params for later use
        if isinstance(result, dict):
            intent = result.get('intent', 'conversation')
            return intent
        else:
            return result

    @classmethod
    def _local_classify(cls, message: str):
        """Local rule-based intent classification using improved patterns"""
        import re
        msg = message.lower().strip()
        
        # FIRST: Check for explicit conversation patterns (highest priority)
        conversation_patterns = [
            # Greetings and introductions
            r'\b(привет|здравствуй|добрый|доброе|доброго|хай|hi|hello|hey)\b',
            r'\b(как дела|как жизнь|как настроение)\b',
            r'\b(расскажи о себе|кто ты|что ты|ты кто|ты что)\b',
            r'\b(что ты умеешь|что можешь|твои возможности|твои функции)\b',
            r'\b(давай поговорим|поговори со мной|хочу пообщаться)\b',
            r'\b(спасибо|благодарю|спс)\b.*\b(за|что)\b',
            r'\b(извини|прости|сорри)\b',
            r'\b(пока|до свидания|до встречи|bye|goodbye)\b',
            # General conversation starters
            r'\b(что нового|что интересного|что происходит)\b',
            r'\b(расскажи|поведай)\b.*\b(о себе|про себя)\b',
            r'\b(ты знаешь|ты умеешь|ты можешь)\b.*\?',
            r'\b(помоги|помощь|нужна помощь)\b.*\b(понять|разобраться)\b',
            # Questions about the bot itself
            r'\b(как ты работаешь|как ты функционируешь)\b',
            r'\b(что ты думаешь|каково твое мнение)\b',
            r'\b(ты живой|ты ИИ|ты искусственный интеллект)\b'
        ]
        
        for pattern in conversation_patterns:
            if re.search(pattern, msg, re.IGNORECASE):
                return 'conversation'
        
        # Enhanced intent mapping with regex patterns and context analysis
        intent_patterns = {
            # Create worker task patterns - check FIRST for monitoring commands
            'create_worker_task': [
                r'\b(создай|настрой|запланируй)\b.*\b(worker|фоновую задачу|мониторинг|автоматическ)\b',
                r'\b(мониторь|следить|отслеживать)\b.*\b(рынок|золото|цену|каждый час|валют|акций|металл)\b',
                r'\b(создай worker|автоматическая задача)\b.*\b(для|чтобы|каждые)\b',
                r'\b(автоматическ|периодическ)\b.*\b(проверка|мониторинг|анализ)\b',
                r'\b(информируй|уведомляй)\b.*\b(когда|если)\b.*\b(хорошая|возможность)\b',
                r'\b(мониторь|следить)\b.*\b(погоду|погод|температур)\b',
                r'\b(уведом|сообщи)\b.*\b(если|когда)\b.*\b(дождь|снег|холодно|жарко)\b',
                r'\b(мониторь|следить)\b.*\b(золото|серебро|валют|акций|металл|курс|цену)\b',
                r'\b(создай|настрой)\b.*\b(мониторинг|отслеживание)\b.*\b(золота|серебра|валют|акций)\b',
                r'\b(хочу|нужно)\b.*\b(мониторить|следить|отслеживать)\b.*\b(золото|серебро|валют|акций|металл)\b'
            ],
            
            # Add task patterns - more specific to avoid conflicts with list_tasks
            'add_task': [
                r'\b(создай|добавь|напомни|поставь|нужно|запланируй|закажи|закажу|купить|сделать|подготовить|организовать)\b.*\b(завтра|сегодня|через|в|на|утром|вечером|днем)\b',
                r'\b(создай|добавь|напомни|поставь|нужно|запланируй)\b.*\b(задач|дело|напоминани|событи)\b',
                r'\b(напомни|поставь)\b.*\b(о|про|что)\b',
                r'\b(нужно|надо)\b.*\b(сделать|подготовить|организовать|купить|заказать)\b',
                r'\b(час|минут|день|недел|месяц)\b.*\b(назад|спустя|позже)\b',
                r'\b(встреча|совещани|звонок|позвонить|написать|отправить|приехать|уйти|вернуться)\b.*\b(в|на|завтра|сегодня|через)\b',
                r'\b(создай|добавь|напомни)\b.*\b(новую|ещё одну)\b.*\b(задач|дело)\b'  # Более специфично для создания
            ],
            'complete_task': [
                r'\b(готово|сделал|выполнил|завершил|закончил|выполнена|завершена|закончена)\b',
                r'\b(я сделал|я выполнил|я завершил|уже сделал|уже выполнил)\b',
                r'\b(отметь|пометить)\b.*\b(готов|выполнен|завершен)\b',
                r'\b(задача|дело)\b.*\b(готов|выполнен|завершен|сделан)\b'
            ],
            
            # List tasks patterns - expanded for better detection
            'list_tasks': [
                r'\b(покажи|список|мои|все|активные)\b.*\b(задач|дела|напоминани)\b',
                r'\b(что|какие)\b.*\b(задач|дела|напоминани)\b.*\b(у меня|есть)\b',
                r'\b(мои задачи|мои дела|список задач)\b',
                r'\b(что|какие)\b.*\b(дела|задачи)\b.*\b(на сегодня|сегодня|завтра|на этой неделе|на среду|на неделю)\b',
                r'\b(расскажи|покажи)\b.*\b(что|какие)\b.*\b(дела|задачи)\b',
                r'\b(у меня есть|есть ли)\b.*\b(задачи|дела)\b',
                r'\b(запланирован|запланированы)\b.*\b(задачи|дела)\b',  # Добавлено для "запланированы"
                r'\b(покажи|список)\b.*\b(на|для)\b.*\b(среду|неделю|месяц|день)\b',  # Добавлено для "на среду"
                r'\b(покажи|список)\b.*\b(автоматические|автоматическая|worker)\b.*\b(задач|дела)\b',  # Автоматические задачи
                r'\b(мониторинг|мониторинга)\b.*\b(задач|дела)\b',  # Задачи мониторинга
                r'\b(автоматическ|автоматические)\b.*\b(задач|дела)\b'  # Автоматические задачи
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
            
            # Create worker task patterns
            'create_worker_task': [
                r'\b(создай|настрой|запланируй)\b.*\b(worker|фоновую задачу|мониторинг|автоматическ)\b',
                r'\b(мониторь|следить|отслеживать)\b.*\b(рынок|золото|цену|каждый час|валют|акций|металл)\b',
                r'\b(создай worker|автоматическая задача)\b.*\b(для|чтобы|каждые)\b',
                r'\b(автоматическ|периодическ)\b.*\b(проверка|мониторинг|анализ)\b',
                r'\b(информируй|уведомляй)\b.*\b(когда|если)\b.*\b(хорошая|возможность)\b',
                r'\b(мониторь|следить)\b.*\b(погоду|погод|температур)\b',
                r'\b(уведом|сообщи)\b.*\b(если|когда)\b.*\b(дождь|снег|холодно|жарко)\b',
                r'\b(мониторь|следить)\b.*\b(золото|серебро|валют|акций|металл|курс|цену)\b',
                r'\b(создай|настрой)\b.*\b(мониторинг|отслеживание)\b.*\b(золота|серебра|валют|акций)\b',
                r'\b(техническ|анализ|индикатор|rsi|macd|bollinger)\b.*\b(анализ|мониторинг)\b',
                r'\b(анализируй|проанализируй)\b.*\b(рынок|акции|валют|металл)\b',
                r'\b(сигнал|рекомендаци)\b.*\b(покупк|продаж|техническ)\b',
                r'\b(объем|volume)\b.*\b(торгов|анализ)\b'
            ],
            
            # Delete worker task patterns
            'delete_worker_task': [
                r'\b(удали|останови|выключи)\b.*\b(worker|фоновую задачу|мониторинг)\b',
                r'\b(удали|останови)\b.*\b(мою|мою фоновую|мою автоматическ)\b.*\b(задач|мониторинг)\b',
                r'\b(перестань|прекрати)\b.*\b(мониторить|отслеживать|проверять)\b',
                r'\b(отключи|выключи)\b.*\b(автоматическ|периодическ)\b.*\b(задач|проверку)\b'
            ],
            
            # Update user memory patterns
            'update_user_memory': [
                r'\b(запомни|помни|сохрани)\b.*\b(что|мне)\b',
                r'\b(я люблю|я предпочитаю|у меня аллергия)\b',
                r'\b(запомни|помни)\b.*\b(мой|мою|мои)\b'
            ],
            
            # Accept delegated task patterns
            'accept_delegated_task': [
                r'\b(соглашусь|приму|возьму|выполню)\b.*\b(задач|дело|поручени)\b',
                r'\b(да|согласен|принимаю)\b.*\b(задач|дело)\b'
            ],
            
            # Reject delegated task patterns
            'reject_delegated_task': [
                r'\b(откажусь|не могу|не возьму|не выполню)\b.*\b(задач|дело|поручени)\b',
                r'\b(нет|отказываюсь)\b.*\b(от|задач|дела)\b'
            ],
            
            # Get delegation progress patterns
            'get_delegation_progress': [
                r'\b(где|как|что|статус)\b.*\b(делегирован|поручен|мои|задач)\b',
                r'\b(мои поручения|делегированные задачи)\b'
            ],
            
            # Get task details patterns
            'get_task_details': [
                r'\b(расскажи|подробно|детали|информация)\b.*\b(о|про|задач|дело)\b',
                r'\b(что|какие)\b.*\b(детали|информация|подробности)\b.*\b(задач|дело)\b',
                r'\b(расскажи|подробно)\b.*\b(про|о)\b.*\b(задач|дело)\b'
            ]
        }
        
        # Check patterns in order of priority (more specific first)
        priority_order = ['complete_task', 'delete_all_tasks', 'delete_task', 'delegate_task', 'reschedule_task', 'add_task', 'list_tasks', 'edit_task', 'find_relevant_contacts_for_task', 'get_task_details', 'update_profile', 'update_user_memory', 'accept_delegated_task', 'reject_delegated_task', 'get_delegation_progress', 'find_partners', 'create_worker_task', 'delete_worker_task']
        
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
        
        # SPECIAL CASE: Check for background tasks filter
        background_patterns = [
            r'\b(покажи|список)\b.*\b(автоматические|автоматическая|worker)\b.*\b(задач|дела)\b',
            r'\b(мониторинг|мониторинга)\b.*\b(задач|дела)\b',
            r'\b(автоматическ|автоматические)\b.*\b(задач|дела)\b'
        ]
        
        for pattern in background_patterns:
            if re.search(pattern, msg, re.IGNORECASE):
                return {'intent': 'list_tasks', 'params': {'filter_type': 'Автоматические'}}
        
        # Default to conversation
        return 'conversation'

    @classmethod
    def get_command_class(cls, intent: str):
        """Map intent to command class"""
        from .commands import (
            CreateTaskCommand, CompleteTaskCommand, ListTasksCommand,
            DeleteTaskCommand, RescheduleTaskCommand, UpdateProfileCommand, FindPartnersCommand,
            DelegateTaskCommand, ConversationCommand, GetTaskDetailsCommand,
            EditTaskCommand, FindRelevantContactsForTaskCommand, UpdateUserMemoryCommand, DeleteAllTasksCommand,
            AcceptDelegatedTaskCommand, RejectDelegatedTaskCommand, GetDelegationProgressCommand,
            CreateWorkerTaskCommand, DeleteWorkerTaskCommand
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
            'accept_delegated_task': AcceptDelegatedTaskCommand,
            'reject_delegated_task': RejectDelegatedTaskCommand,
            'get_delegation_progress': GetDelegationProgressCommand,
            'create_worker_task': CreateWorkerTaskCommand,
            'delete_worker_task': DeleteWorkerTaskCommand,
        }

        return mapping.get(intent)