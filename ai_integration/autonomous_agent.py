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
        ШАГ 1: ГИБРИДНОЕ ПЛАНИРОВАНИЕ - ПРАВИЛА + AI ДЛЯ ОБЩЕНИЯ
        """

        message_lower = user_message.lower()

        # СТРОГИЕ ПРАВИЛА ДЛЯ КОМАНД (БЕЗ AI)
        command_patterns = {
            'delete_task': ['удали', 'сотри', 'удалить'],
            'list_tasks': ['покажи', 'список', 'мои'],
            'reschedule_task': ['перенеси', 'отложи', 'измени'],
            'find_relevant_contacts_for_task': ['контакты', 'поможет'],
            'find_partners': ['единомышленники', 'познакомься', 'найди'],
            'complete_task': ['готово', 'сделал', 'завершил', 'выполнил'],
            'add_task': ['создай', 'добавь', 'напомни'],
            'delegate_task': ['делегируй', 'передай', 'поручи'],
            'update_profile': ['обнови'],
            'update_user_memory': ['запомни']
        }

        # Проверяем на команды задач
        for intent, keywords in command_patterns.items():
            if any(keyword in message_lower for keyword in keywords):
                # Специальные проверки для некоторых команд
                if intent in ['delete_task', 'list_tasks', 'reschedule_task', 'add_task', 'complete_task', 'delegate_task']:
                    # Для этих команд обязательно должно быть слово "задач" ИЛИ другие признаки
                    if ('задач' in message_lower or 
                        intent == 'complete_task' or  # "готово" не требует "задач"
                        intent == 'delegate_task'):   # "делегируй" не требует "задач"
                        return self._create_command_plan(intent, user_message)
                else:
                    return self._create_command_plan(intent, user_message)

        # СПЕЦИАЛЬНАЯ ОБРАБОТКА ВОПРОСОВ О ВРЕМЕНИ
        time_keywords = ['время', 'времени', 'час', 'сколько времени', 'который час']
        if any(keyword in message_lower for keyword in time_keywords):
            return {
                "intent": "time_query",
                "actions": [],
                "response_strategy": "direct_time_response"
            }

        # ЕСЛИ НЕ КОМАНДА - ИСПОЛЬЗУЕМ AI ДЛЯ ОБЩЕГО ОБЩЕНИЯ
        return await self._plan_general_chat(user_message, user_id)

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

Если пользователь хочет пообщаться - отвечай естественно и полезно."""

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
                results_summary.append(f"✅ {result['tool']}: {result['reason']}\nРезультат: {str(result['result'])[:200]}")
            else:
                results_summary.append(f"❌ {result['tool']}: {result['error']}")
        
        results_text = "\n\n".join(results_summary)
        
        # Получаем информацию о пользователе для базового промпта
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first() if user_id else None
            
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
                weather_info=None,
                news_info=None
            )
        finally:
            session.close()
        
        # Дополняем базовый промпт инструкциями для ответа
        system_prompt = f"{base_prompt}\n\n" + f"""\n---

РЕЖИМ: ФОРМИРОВАНИЕ ОТВЕТА

ЗАПРОС: {user_message}

ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ:
{results_text}

ЗАДАЧА: Сформируй естественный дружелюбный ответ.

ПРАВИЛА:
- Говори от первого лица: "Я создал", "Вот твои задачи"
- Будь конкретным: укажи время, детали, количество
- Используй 1-2 эмодзи: ✅ 📝 ⏰ 🎯
- Завершай полезным предложением или вопросом
- БЕЗ форматирования, списков, жирного текста

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
        
        # Загружаем из базы с оптимизацией
        tasks = session.query(Task).filter(
            Task.user_id == user_id,
            Task.status != 'completed'
        ).limit(100).all()  # Ограничиваем для производительности
        
        self.tasks_cache[cache_key] = tasks
        self.cache_expiry[cache_key] = current_time + 30  # 30 секунд
        
        return tasks

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
        # Простая эвристика для времени
        message_lower = message.lower()
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
                # Были выполнены действия - формируем ответ на основе результатов
                logger.info(f"[AGENT] Step 3: Generating response from execution results")
                response_parts = []
                for result in execution_results:
                    if result['success']:
                        tool_name = result['tool']
                        if tool_name == 'add_task':
                            response_parts.append("✅ Задача создана!")
                        elif tool_name == 'complete_task':
                            response_parts.append("✅ Задача выполнена!")
                        elif tool_name == 'delete_task':
                            response_parts.append("✅ Задача удалена!")
                        elif tool_name == 'list_tasks':
                            tasks = result.get('result', [])
                            if tasks:
                                response_parts.append(f"📝 У вас {len(tasks)} активных задач")
                            else:
                                response_parts.append("📝 У вас нет активных задач")
                        elif tool_name == 'reschedule_task':
                            response_parts.append("✅ Время задачи изменено!")
                        elif tool_name == 'find_relevant_contacts_for_task':
                            response_parts.append("👥 Контакты найдены!")
                        elif tool_name == 'find_partners':
                            response_parts.append("🤝 Единомышленники найдены!")
                        else:
                            response_parts.append(f"✅ {tool_name} выполнен")
                    else:
                        response_parts.append(f"❌ Ошибка: {result['error']}")
                
                response = " ".join(response_parts)
            elif plan.get('response_strategy') == 'direct_time_response':
                # Специальная обработка для вопросов о времени
                logger.info(f"[AGENT] Step 3: Direct time response")
                # Получаем актуальное время пользователя
                session = Session()
                try:
                    user = self._get_cached_user(user_id, session)
                    if user:
                        from datetime import datetime
                        import pytz
                        base_now = datetime.now(pytz.UTC)
                        user_now = base_now
                        current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
                        
                        user_timezone = user.timezone if user.timezone else 'Europe/Moscow'
                        try:
                            user_tz = pytz.timezone(user_timezone)
                            user_now = base_now.astimezone(user_tz)
                            current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                        except Exception as e:
                            logger.error(f"Error setting user timezone: {e}")
                        
                        response = f"Сейчас {current_time_str}. ⏰"
                    else:
                        response = "Сейчас время по UTC. ⏰"
                finally:
                    session.close()
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