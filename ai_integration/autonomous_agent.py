import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
import logging
from datetime import datetime, timezone
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, Task, UserProfile, Subscription
from .prompts import get_extended_system_prompt
from .dynamic_tools import tool_discovery

logger = logging.getLogger(__name__)

class HybridAutonomousAgent:
    """
    Улучшенный гибридный автономный агент с:
    - Планированием стратегии
    - Использованием готовых handlers
    - Self-reflection
    - Адаптацией к ошибкам
    - Динамическим обнаружением инструментов
    """

    def __init__(self):
        self.execution_history = []  # История выполнения
        self.tool_discovery = tool_discovery  # Используем глобальный экземпляр
        self._initialize_tools()  # Инициализация инструментов
        self.context_memory = []  # Краткосрочная память контекста
        self.success_patterns = {}  # Паттерны успешных действий
        self.user_preferences = {}  # Предпочтения пользователей
        
        # КЭШИРОВАНИЕ для производительности
        self.user_cache = {}  # Кэш пользователей (user_id -> user_data)
        self.tasks_cache = {}  # Кэш задач (user_id -> tasks_list, expires in 30 sec)
        self.cache_expiry = {}  # Время истечения кэша
        
        # Загружаем статистику, если есть
        self.tool_discovery.load_stats()

    def _initialize_tools(self):
        """Инициализирует динамическую систему инструментов"""
        # Обнаруживаем инструменты из handlers модуля
        try:
            from . import handlers
            self.tool_discovery.discover_tools_from_module(handlers)
            logger.info(f"[AGENT] Initialized {len(self.tool_discovery.discovered_tools)} dynamic tools")
        except Exception as e:
            logger.error(f"[AGENT] Failed to initialize dynamic tools: {e}")
            # Fallback на базовые инструменты
            self._init_default_tools()
    
    def _init_default_tools(self):
        """Инициализирует базовый набор инструментов (fallback)"""
        logger.warning("[AGENT] Using fallback default tools")
        # Здесь можно добавить базовый набор, если динамическое обнаружение не сработало

    async def call_ai(self, messages, **kwargs):
        """Универсальный вызов AI API"""
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000,
            **kwargs
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    raise Exception(f"AI call failed: {response.status} {error_text}")

    async def plan_strategy(self, user_message, user_id, context=None):
        """
        ШАГ 1: AI-ПЛАНИРОВАНИЕ БЕЗ ЖЕСТКИХ ПРАВИЛ
        AI анализирует запрос и сам решает, какие инструменты нужны
        """
        
        # Получаем контекст пользователя
        session = Session()
        try:
            user = self._get_cached_user(user_id, session)
            if not user:
                return {
                    "intent": "general_chat",
                    "actions": [],
                    "response_strategy": "natural_response"
                }

            # Получаем текущее время
            from datetime import datetime
            import pytz
            base_now = datetime.now(pytz.UTC)
            user_now = base_now
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            current_date_str = user_now.strftime("%Y-%m-%d")
            
            months = [
                'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
            ]
            
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")

            # Получаем список задач для контекста
            tasks = self._get_cached_tasks(user_id, session)
            tasks_summary = ""
            if tasks:
                active_tasks = [t for t in tasks if t.status != 'completed']
                if active_tasks:
                    tasks_summary = f"\nТекущие задачи ({len(active_tasks)}):\n"
                    for t in active_tasks[:5]:  # Первые 5
                        tasks_summary += f"- {t.title} (напоминание: {t.reminder_time})\n"

            # Получаем профиль
            profile_data = {}
            from models import UserProfile
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                if profile.city:
                    profile_data['city'] = profile.city
                if profile.goals:
                    profile_data['goals'] = profile.goals
                if profile.interests:
                    profile_data['interests'] = profile.interests
            
            profile_summary = ""
            if profile_data:
                profile_summary = "\nПрофиль:\n"
                if 'city' in profile_data:
                    profile_summary += f"Город: {profile_data['city']}\n"
                if 'goals' in profile_data:
                    profile_summary += f"Цели: {profile_data['goals']}\n"
                if 'interests' in profile_data:
                    profile_summary += f"Интересы: {profile_data['interests']}\n"

        finally:
            session.close()

        # AI планирует действия на основе контекста
        planning_prompt = f"""Ты - планировщик действий для AI-помощника. Проанализируй запрос пользователя и определи, нужны ли инструменты.

СЕЙЧАС: {current_time_str}, {current_date_str}
Пользователь: {user.username or "пользователь"}{profile_summary}{tasks_summary}

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
- add_task(title, reminder_time) - создать задачу с напоминанием
- list_tasks() - показать список задач
- complete_task(task_title) - отметить задачу выполненной
- delete_task(task_title) - удалить задачу
- reschedule_task(task_title, new_time) - перенести задачу
- find_relevant_contacts_for_task(task_description) - найти контакты для помощи
- find_partners() - найти единомышленников
- update_profile(...) - обновить профиль
- update_user_memory(memory_entry) - сохранить информацию

ЗАПРОС: "{user_message}"

ЗАДАЧА: Определи, нужны ли инструменты для выполнения запроса. Если нужны - укажи какие и параметры.

Верни JSON:
{{
  "needs_tools": true/false,
  "tools": [
    {{"tool": "имя_инструмента", "params": {{"param1": "value1"}}, "reason": "зачем"}}
  ],
  "response_type": "execute_and_respond" или "just_chat"
}}

Примеры:
- "привет" → {{"needs_tools": false, "response_type": "just_chat"}}
- "напомни через 5 минут позвонить" → {{"needs_tools": true, "tools": [{{"tool": "add_task", "params": {{"title": "позвонить", "reminder_time": "через 5 минут"}}, "reason": "создание напоминания"}}], "response_type": "execute_and_respond"}}
- "мои задачи?" → {{"needs_tools": true, "tools": [{{"tool": "list_tasks", "params": {{}}, "reason": "показать список"}}], "response_type": "execute_and_respond"}}
- "как дела?" → {{"needs_tools": false, "response_type": "just_chat"}}

Верни ТОЛЬКО JSON, без дополнительного текста."""

        messages = [
            {"role": "system", "content": planning_prompt},
            {"role": "user", "content": user_message}
        ]

        try:
            response = await self.call_ai(messages, temperature=0.3)
            content = response['choices'][0]['message']['content'].strip()
            
            # Extract JSON from response
            import json
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                plan_data = json.loads(json_match.group())
            else:
                plan_data = json.loads(content)
            
            if plan_data.get('needs_tools'):
                return {
                    "intent": "tool_execution",
                    "actions": plan_data.get('tools', []),
                    "response_strategy": "execute_action"
                }
            else:
                return {
                    "intent": "general_chat",
                    "actions": [],
                    "response_strategy": "natural_response"
                }
                
        except Exception as e:
            logger.error(f"[AGENT] Error in AI planning: {e}")
            # Fallback - простое общение
            return {
                "intent": "general_chat",
                "actions": [],
                "response_strategy": "natural_response"
            }

    def _create_command_plan(self, intent, user_message):
        """Создает план для команды"""
        params = {}
        if intent == 'delete_task':
            params = {"task_title": self._extract_task_title(user_message)}
        elif intent == 'complete_task':
            params = {"task_title": self._extract_task_title(user_message)}
        elif intent == 'reschedule_task':
            params = {
                "task_title": self._extract_task_title(user_message),
                "new_time": self._extract_time(user_message)
            }
        elif intent == 'add_task':
            title, time_str = self._extract_task_info(user_message)
            params = {"title": title, "reminder_time": time_str}
        elif intent == 'find_relevant_contacts_for_task':
            params = {"task_description": user_message}

        return {
            "intent": intent,
            "actions": [{
                "tool": intent,
                "params": params,
                "reason": f"Распознано по ключевым словам: {intent}"
            }],
            "response_strategy": "execute_action"
        }

    async def _plan_general_chat(self, user_message, user_id):
        """AI планирование для общего общения"""
        # Получаем информацию о пользователе для контекста - ИСПОЛЬЗУЕМ КЭШ
        session = Session()
        try:
            user = self._get_cached_user(user_id, session)
            if not user:
                return {
                    "intent": "general_chat",
                    "actions": [],
                    "response_strategy": "natural_response"
                }

            # Устанавливаем время пользователя (как в chat.py)
            from datetime import datetime
            import pytz
            base_now = datetime.now(pytz.UTC)
            user_now = base_now  # Default to UTC
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            current_date_str = user_now.strftime("%Y-%m-%d")
            
            months = [
                'января',
                'февраля',
                'марта',
                'апреля',
                'мая',
                'июня',
                'июля',
                'августа',
                'сентября',
                'октября',
                'ноября',
                'декабря']
            
            # Get user timezone if available, default to Moscow if not set
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone for chat: {e}")
                # Fallback to Moscow time
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                except:
                    pass  # Keep UTC if all fails

            base_prompt = get_extended_system_prompt(
                user_now,
                current_time_str,
                current_date_str,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=user.memory or "",
                context=None,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'FREE'),
                message_type=None,
                weather_info=None,
                news_info=None
            )
        finally:
            session.close()

        system_prompt = f"{base_prompt}\n\n" + """Ты ведешь естественный разговор. Можешь предлагать идеи, задавать вопросы, давать советы. Но НЕ ВЫЗЫВАЙ ИНСТРУМЕНТЫ для команд - это уже обработано правилами выше.

Если пользователь хочет пообщаться - отвечай естественно и полезно.

Адаптируйся под ситуацию: используй эмодзи когда уместно, выбирай подходящий стиль общения."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Сообщение: {user_message}"}
        ]

        response = await self.call_ai(messages)
        content = response['choices'][0]['message']['content']

        return {
            "intent": "general_chat",
            "actions": [],
            "response_strategy": "natural_response",
            "ai_response": content
        }

    async def execute_actions(self, actions, user_id):
        """
        ШАГ 2: Выполнить запланированные действия через готовые handlers
        """
        # Импортируем handlers
        from . import handlers
        
        results = []
        
        for action in actions:
            tool_name = action.get('tool')
            params = action.get('params', {})
            reason = action.get('reason', '')
            
            logger.info(f"[AGENT] Executing {tool_name} with params {params} - {reason}")
            
            try:
                # Получаем функцию handler
                handler_func = getattr(handlers, tool_name, None)
                
                if handler_func is None:
                    results.append({
                        "tool": tool_name,
                        "success": False,
                        "error": f"Handler {tool_name} not found"
                    })
                    continue
                
                # Добавляем user_id к параметрам
                params['user_id'] = user_id
                
                # Исправляем известные проблемы с параметрами
                if tool_name == 'find_relevant_contacts_for_task':
                    logger.info(f"[AGENT] Original params for {tool_name}: {params}")
                    if 'description' in params and 'task_description' not in params:
                        params['task_description'] = params.pop('description')
                        logger.info(f"[AGENT] Fixed parameter: description -> task_description")
                    elif 'task_description' not in params:
                        # Если нет task_description, берем из сообщения или устанавливаем по умолчанию
                        params['task_description'] = params.get('task_description', 'помощь с задачей')
                        logger.info(f"[AGENT] Added default task_description: {params['task_description']}")
                
                logger.info(f"[AGENT] Executing {tool_name} with final params: {params}")
                
                # Выполняем handler
                result = await handler_func(**params) if asyncio.iscoroutinefunction(handler_func) else handler_func(**params)
                
                # Обучаемся на успешном выполнении
                self.tool_discovery.learn_from_success(
                    func_name=tool_name,
                    user_id=user_id,
                    context=reason,
                    result=result
                )
                
                results.append({
                    "tool": tool_name,
                    "success": True,
                    "result": result,
                    "reason": reason
                })
                
            except Exception as e:
                logger.error(f"[AGENT] Error executing {tool_name}: {e}")
                import traceback
                traceback.print_exc()
                
                # Обучаемся на ошибке
                self.tool_discovery.learn_from_failure(
                    func_name=tool_name,
                    error=str(e)
                )
                
                results.append({
                    "tool": tool_name,
                    "success": False,
                    "error": str(e),
                    "reason": reason
                })
        
        return results

    async def reflect_and_respond(self, user_message, plan, execution_results, context=None, user_id=None):
        """
        ШАГ 3: AI рефлексирует над результатами и формирует естественный ответ
        """
        
        results_summary = []
        for result in execution_results:
            if result['success']:
                results_summary.append(f"УСПЕХ {result['tool']}: {result['reason']}\nРезультат: {str(result['result'])[:200]}")
            else:
                results_summary.append(f"ОШИБКА {result['tool']}: {result['error']}")
        
        results_text = "\n\n".join(results_summary)
        
        # Получаем информацию о пользователе для базового промпта
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first() if user_id else None
            
            # Получаем данные профиля
            profile_data = {}
            weather_info = None
            if user:
                from models import UserProfile
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    if profile.city:
                        profile_data['city'] = profile.city
                        # Получаем погоду для города
                        from .utils import get_weather_info
                        weather_info = get_weather_info(profile.city)
                    if profile.birthdate:
                        profile_data['birthdate'] = profile.birthdate
                    if profile.company:
                        profile_data['company'] = profile.company
                    if profile.position:
                        profile_data['position'] = profile.position
                    if profile.goals:
                        profile_data['goals'] = profile.goals
                    if profile.skills:
                        profile_data['skills'] = profile.skills
                    if profile.interests:
                        profile_data['interests'] = profile.interests
            
            # Получаем базовый промпт
            base_prompt = get_extended_system_prompt(
                user_now=None,
                current_time_str=None,
                current_date_str=None,
                user_username=user.username if user else "пользователь",
                mentions_str="",
                user_memory=user.memory if user else "",
                context=context,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'FREE') if user else 'FREE',
                message_type=None,
                weather_info=weather_info,
                news_info=None,
                profile_data=profile_data
            )
        finally:
            session.close()
        
        # Дополняем базовый промпт инструкциями для ответа
        profile_section = f"\nПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n{profile_data}" if profile_data else ""
        system_prompt = f"{base_prompt}{profile_section}\n\n" + f"""\n---

РЕЖИМ: ФОРМИРОВАНИЕ ОТВЕТА

ЗАПРОС: {user_message}

ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ:
{results_text}

ЗАДАЧА: Сформируй естественный дружелюбный ответ.

ПРАВИЛА:
- Говори естественно, от первого лица
- Будь конкретным, когда это важно
- Эмодзи используй, когда они добавляют эмоций
- Форматирование выбирай под ситуацию - иногда списки, иногда повествование
- Заканчивай так, чтобы продолжить разговор
- Не показывай личные данные, если не запрошены
- Не выдумывай информацию
- Используй данные профиля для персонализации, когда уместно
- Будь гибким в стиле общения

Верни ТОЛЬКО текст ответа.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Сформулируй ответ"}
        ]

        response = await self.call_ai(messages, temperature=0.8)
        content = response['choices'][0]['message']['content']
        
        return content.strip()

    def _get_cached_user(self, user_id, session):
        """Получить пользователя из кэша или базы данных"""
        import time
        
        current_time = time.time()
        
        # Проверяем кэш (кэш на 5 минут)
        if user_id in self.user_cache and user_id in self.cache_expiry:
            if current_time < self.cache_expiry[user_id]:
                return self.user_cache[user_id]
        
        # Загружаем из базы
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            self.user_cache[user_id] = user
            self.cache_expiry[user_id] = current_time + 300  # 5 минут
        
        return user

    def _get_cached_tasks(self, user_id, session, force_refresh=False):
        """Получить задачи пользователя из кэша или базы данных"""
        import time
        
        current_time = time.time()
        cache_key = f"tasks_{user_id}"
        
        # Проверяем кэш (кэш на 30 секунд для задач)
        if not force_refresh and cache_key in self.tasks_cache and cache_key in self.cache_expiry:
            if current_time < self.cache_expiry[cache_key]:
                return self.tasks_cache[cache_key]
        
        # Загружаем из базы с оптимизацией и обработкой ошибок
        try:
            from models import User
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return []
            
            tasks = session.query(Task).filter(
                Task.user_id == user.id
            ).limit(100).all()  # Ограничиваем для производительности
            
            # Фильтруем невыполненные задачи (status != 'completed')
            active_tasks = [t for t in tasks if t.status != 'completed']
            
            self.tasks_cache[cache_key] = active_tasks
            self.cache_expiry[cache_key] = current_time + 30  # 30 секунд
            
            return active_tasks
        except Exception as e:
            logger.error(f"Error loading tasks: {e}")
            # Возвращаем пустой список в случае ошибки
            return []

    def _extract_task_title(self, message):
        """Извлекает название задачи из сообщения"""
        # Простая эвристика - берем текст после ключевых слов
        words = message.lower().split()
        keywords = ['задачу', 'задачи', 'task']
        for i, word in enumerate(words):
            if any(kw in word for kw in keywords):
                # Берем следующие слова как название задачи
                remaining = ' '.join(words[i+1:])
                if remaining:
                    return remaining.strip()
        return message.strip()

    def _extract_time(self, message):
        """Извлекает время из сообщения"""
        import re
        message_lower = message.lower()
        
        # Ищем паттерны времени
        time_patterns = [
            r'через (\d+) (минут|час|часа|часов|дней|дня)',
            r'завтра(?: в (\d{1,2}:\d{2}))?',
            r'сегодня(?: в (\d{1,2}:\d{2}))?',
            r'в (\d{1,2}:\d{2})',
            r'(\d{1,2}:\d{2})'
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, message_lower)
            if match:
                if 'через' in pattern:
                    return f'через {match.group(1)} {match.group(2)}'
                elif 'завтра' in pattern:
                    time_part = match.group(1) if match.group(1) else ''
                    return f'завтра {time_part}'.strip()
                elif 'сегодня' in pattern:
                    time_part = match.group(1) if match.group(1) else ''
                    return f'сегодня {time_part}'.strip()
                else:
                    return match.group(1) if match.group(1) else match.group(0)
        
        # Fallback
        if 'завтра' in message_lower:
            return 'завтра'
        elif 'сегодня' in message_lower:
            return 'сегодня'
        elif 'через' in message_lower:
            return 'через час'
        return None

    def _extract_task_info(self, message):
        """Извлекает название задачи и время"""
        title = self._extract_task_title(message)
        time_str = self._extract_time(message)
        return title, time_str

    async def process_request(self, user_message, user_id, context=None):
        """
        Основной процесс обработки запроса:
        1. Планирование стратегии
        2. Выполнение действий
        3. Рефлексия и формирование ответа
        """
        
        from datetime import datetime, timezone
        
        try:
            # ШАГ 1: Планирование
            logger.info(f"[AGENT] Step 1: Planning strategy for '{user_message[:50]}...'")
            plan = await self.plan_strategy(user_message, user_id, context)
            
            actions = plan.get('actions', [])
            
            # ШАГ 2: Выполнение
            execution_results = []
            if actions:
                logger.info(f"[AGENT] Step 2: Executing {len(actions)} actions")
                execution_results = await self.execute_actions(actions, user_id)
            else:
                logger.info(f"[AGENT] No actions to execute, direct response")
            
            # ШАГ 3: Формирование ответа
            if execution_results:
                # Были выполнены действия - формируем естественный ответ через AI
                logger.info(f"[AGENT] Step 3: Generating natural response from execution results")
                response = await self.reflect_and_respond(
                    user_message, 
                    plan, 
                    execution_results, 
                    context,
                    user_id
                )
            else:
                # Общее общение - используем AI
                logger.info(f"[AGENT] Step 3: AI generating natural response")
                response = await self.reflect_and_respond(
                    user_message, 
                    plan, 
                    execution_results, 
                    context,
                    user_id
                )
            
            # Сохраняем в историю и обучаемся
            entry = {
                'message': user_message,
                'user_id': user_id,
                'plan': plan,
                'results': execution_results,
                'response': response,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'success': all(r.get('success', False) for r in execution_results)
            }
            self.execution_history.append(entry)
            
            # Обучаемся на успешных паттернах
            if entry['success'] and actions:
                self._learn_from_success(user_message, plan, user_id)
            
            # Ограничиваем размер истории
            if len(self.execution_history) > 50:  # Больше истории для обучения
                self.execution_history = self.execution_history[-50:]
            
            return response
            
        except Exception as e:
            logger.error(f"[AGENT] Error processing request: {e}")
            import traceback
            traceback.print_exc()
            return "Извините, произошла ошибка при обработке запроса. Попробуйте переформулировать."


    def _learn_from_success(self, message, plan, user_id):
        """Обучение на успешных паттернах"""
        intent = plan.get('intent', '')
        actions = plan.get('actions', [])
        
        # Сохраняем успешный паттерн
        pattern_key = f"{user_id}:{intent}"
        if pattern_key not in self.success_patterns:
            self.success_patterns[pattern_key] = []
        
        self.success_patterns[pattern_key].append({
            'message': message,
            'actions': [a.get('tool') for a in actions],
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Ограничиваем размер паттернов
        if len(self.success_patterns[pattern_key]) > 5:
            self.success_patterns[pattern_key] = self.success_patterns[pattern_key][-5:]
    
    def get_similar_patterns(self, user_id, intent):
        """Получить похожие успешные паттерны"""
        pattern_key = f"{user_id}:{intent}"
        return self.success_patterns.get(pattern_key, [])
    
    def adapt_to_user(self, user_id, preference_key, value):
        """Адаптация под предпочтения пользователя"""
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id][preference_key] = value


# Глобальный экземпляр агента
_autonomous_agent = None

def get_autonomous_agent():
    """Получить экземпляр гибридного автономного агента"""
    global _autonomous_agent
    if _autonomous_agent is None:
        _autonomous_agent = HybridAutonomousAgent()
    return _autonomous_agent

async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None, message_type=None):
    """Функция чата с использованием улучшенного гибридного автономного агента"""

    logger.info(f"[HYBRID_AGENT] START - user_id={user_id}, message='{message[:50]}...'")

    if user_id is None:
        logger.error("[HYBRID_AGENT] ERROR: user_id is None!")
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        # Получаем гибридного автономного агента
        agent = get_autonomous_agent()

        # Обрабатываем запрос через улучшенного агента
        response_text = await agent.process_request(message, user_id, context)

        # Возвращаем в формате, ожидаемом остальным кодом
        # Для тестирования возвращаем tool_calls из плана
        tool_calls = []
        try:
            # Попробуем извлечь информацию о вызванных инструментах из истории
            if agent.execution_history:
                last_execution = agent.execution_history[-1]
                if last_execution.get('plan', {}).get('actions'):
                    for action in last_execution['plan']['actions']:
                        tool_calls.append({
                            'function': {
                                'name': action.get('tool', ''),
                                'arguments': json.dumps(action.get('params', {}))
                            }
                        })
        except Exception as e:
            logger.warning(f"Could not extract tool calls: {e}")

        return {
            'response': response_text,
            'tool_calls': tool_calls
        }

    except Exception as e:
        logger.error(f"[HYBRID_AGENT] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            'response': f"Извините, произошла ошибка: {str(e)}",
            'tool_calls': []
        }