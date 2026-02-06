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

    async def _generate_proactive_context(self, user_id, session, user_now):
        """
        ПРОАКТИВНАЯ ЛОГИКА: Генерирует контекст для проактивных предложений
        Анализирует: время суток, интересы, задачи, доступных людей
        Возвращает строку с контекстом для AI
        """
        from models import UserProfile, Task, User
        from datetime import datetime, timedelta
        
        proactive_hints = []
        
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return ""
            
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if not profile:
                return ""
            
            # АНАЛИЗ ВРЕМЕНИ СУТОК
            hour = user_now.hour
            if 6 <= hour < 12:
                time_context = "утро"
                time_suggestions = ["энергичные активности", "планирование дня", "спорт"]
            elif 12 <= hour < 18:
                time_context = "день"
                time_suggestions = ["рабочие встречи", "обучение", "продуктивные задачи"]
            elif 18 <= hour < 23:
                time_context = "вечер"
                time_suggestions = ["отдых", "социальные активности", "анализ дня", "спорт"]
            else:
                time_context = "ночь"
                time_suggestions = ["отдых", "подготовка ко сну"]
            
            proactive_hints.append(f"Сейчас {time_context} - подходит для: {', '.join(time_suggestions)}")
            
            # АНАЛИЗ ИНТЕРЕСОВ И ЦЕЛЕЙ
            if profile.interests:
                interests_list = [i.strip() for i in profile.interests.split(',')[:3]]
                proactive_hints.append(f"Интересы пользователя: {', '.join(interests_list)}")
                
                # Поиск людей с похожими интересами
                from .handlers import get_partners_list
                partners = get_partners_list(user.id, session)
                if partners:
                    # Берем топ-3 партнера
                    top_partners = []
                    for p in partners[:3]:
                        partner_user = session.query(User).filter_by(id=p.user_id).first()
                        if partner_user and partner_user.username:
                            # Найти общие интересы
                            if p.interests:
                                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                                user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                                common = user_interests & partner_interests
                                if common:
                                    top_partners.append(f"@{partner_user.username} (интересы: {', '.join(list(common)[:2])})")
                    
                    if top_partners:
                        proactive_hints.append(f"Доступны для активностей: {'; '.join(top_partners[:2])}")
            
            if profile.goals:
                goals_list = [g.strip() for g in profile.goals.split(',')[:2]]
                proactive_hints.append(f"Цели: {', '.join(goals_list)}")
            
            # АНАЛИЗ ЗАДАЧ
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'active', 'in_progress'])
            ).order_by(Task.reminder_time.asc()).limit(5).all()
            
            if tasks:
                overdue = []
                today = []
                for task in tasks:
                    if task.reminder_time:
                        try:
                            from datetime import timezone
                            reminder_dt = task.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_now.tzinfo)
                            if reminder_dt < user_now:
                                overdue.append(task.title)
                            elif reminder_dt.date() == user_now.date():
                                today.append(task.title)
                        except:
                            pass
                
                if overdue:
                    proactive_hints.append(f"⚠️ Просроченные задачи: {', '.join(overdue[:2])}")
                if today:
                    proactive_hints.append(f"📅 Сегодня запланировано: {', '.join(today[:2])}")
            
            # Формируем итоговый проактивный контекст
            if proactive_hints:
                return "\n\nПРОАКТИВНЫЙ КОНТЕКСТ (используй для предложений):\n" + "\n".join(proactive_hints) + "\n\nНа основе этого контекста предложи 1-2 конкретных действия с указанием времени и людей."
            
        except Exception as e:
            logger.error(f"[PROACTIVE] Error generating proactive context: {e}")
            import traceback
            traceback.print_exc()
        
        return ""

    async def call_ai(self, messages, use_tools=False, **kwargs):
        """Универсальный вызов AI API с опциональными tools"""
        from .tools import TOOLS
        
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
        
        # Добавляем tools если нужно (HYBRID APPROACH)
        if use_tools:
            data["tools"] = TOOLS
            data["tool_choice"] = "auto"  # DeepSeek сам решает
            logger.info(f"[HYBRID] Calling AI with {len(TOOLS)} tools available")

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

            # ПРОАКТИВНЫЙ КОНТЕКСТ: анализируем ситуацию для предложений
            proactive_context = await self._generate_proactive_context(user_id, session, user_now)

        finally:
            session.close()

        # Добавляем контекст диалога если есть
        context_info = ""
        if context:
            context_info = f"\nКОНТЕКСТ ДИАЛОГА: {context}\n(ОБЯЗАТЕЛЬНО используй этот контекст для конкретизации задач!)"

        # УПРОЩЕННЫЙ ПЛАНИРОВЩИК: только анализ context, tools выберет AI
        planning_prompt = f"""Анализ запроса для проактивного ответа.

КОНТЕКСТ:
Время: {current_time_str}, {current_date_str}
Пользователь: {user.username or "пользователь"}{profile_summary}{tasks_summary}{context_info}{proactive_context}

Запрос: "{user_message}"

Не планируй инструменты - их выберет AI автоматически.
Твоя задача - определить тип запроса и проактивные подсказки.

JSON:
{{
  "intent_type": "command"|"question"|"task_management"|"general_chat"|"proactive_opportunity",
  "context_needed": true если нужны данные из БД для ответа,
  "proactive_hints": "краткие подсказки для AI что может быть полезно пользователю сейчас",
  "user_goal_evident": true если видна конкретная цель пользователя
}}

Примеры:
- "привет" → {{"intent_type": "general_chat", "context_needed": false, "proactive_hints": "утреннее приветствие, можно предложить планирование дня"}}
- "мои задачи" → {{"intent_type": "task_management", "context_needed": true, "proactive_hints": "показать задачи, предложить приоритизацию"}}
- "создай задачу пробежка" → {{"intent_type": "command", "context_needed": false, "proactive_hints": "спортивная активность - предложить партнера, учесть погоду", "user_goal_evident": true}}
- "как дела?" → {{"intent_type": "question", "context_needed": true, "proactive_hints": "спросить о прогрессе по задачам и целям"}}

ТОЛЬКО JSON:"""

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
            {"role": "user", "content": user_message}
        ]

        # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: используем tools для автоматического вызова
        response = await self.call_ai(messages, use_tools=True, temperature=0.7)
        
        message = response['choices'][0]['message']
        tool_calls = message.get('tool_calls', [])
        
        # Если AI запросил tool calls - выполняем их
        if tool_calls:
            logger.info(f"[HYBRID] AI requested {len(tool_calls)} tool calls")
            
            # Извлекаем actions из tool_calls
            new_actions = []
            for tool_call in tool_calls:
                func = tool_call['function']
                new_actions.append({
                    'tool': func['name'],
                    'params': json.loads(func['arguments']),
                    'reason': f"AI auto-decision: {func['name']}"
                })
            
            # ВЫПОЛНЯЕМ НОВЫЕ ИНСТРУМЕНТЫ
            new_results = await self.execute_actions(new_actions, user_id)
            execution_results.extend(new_results)
            
            # ПОВТОРНЫЙ ВЫЗОВ AI с результатами tool calls
            messages.append(message)  # Assistant message с tool_calls
            
            # Добавляем результаты каждого tool call
            for i, tool_call in enumerate(tool_calls):
                result_content = json.dumps(
                    new_results[i]['result'] if new_results[i]['success'] 
                    else {'error': new_results[i]['error']},
                    ensure_ascii=False
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call['id'],
                    "content": result_content
                })
            
            # Финальный ответ БЕЗ tools
            logger.info(f"[HYBRID] Getting final response after tool execution")
            final_response = await self.call_ai(messages, use_tools=False, temperature=0.7)
            content = final_response['choices'][0]['message']['content']
        else:
            # Просто текстовый ответ без tool calls
            content = message.get('content', '')
        
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
        # Для тестирования возвращаем tool_calls из execution_results
        tool_calls = []
        try:
            # Извлекаем информацию о вызванных инструментах из execution_history
            if agent.execution_history:
                last_execution = agent.execution_history[-1]
                # Берём results вместо plan - там реальные вызовы
                if last_execution.get('results'):
                    for result in last_execution['results']:
                        tool_name = result.get('tool', '')
                        if tool_name:  # Если инструмент был вызван
                            tool_calls.append({
                                'function': {
                                    'name': tool_name,
                                    'arguments': json.dumps(result.get('params', {}))
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