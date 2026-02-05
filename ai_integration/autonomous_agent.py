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

logger = logging.getLogger(__name__)

class HybridAutonomousAgent:
    """
    Улучшенный гибридный автономный агент с:
    - Планированием стратегии
    - Использованием готовых handlers
    - Self-reflection
    - Адаптацией к ошибкам
    """

    def __init__(self):
        self.execution_history = []  # История выполнения
        self.available_tools = self._discover_handlers()  # Динамическое обнаружение handlers
        self.context_memory = []  # Краткосрочная память контекста
        self.success_patterns = {}  # Паттерны успешных действий
        self.user_preferences = {}  # Предпочтения пользователей

    def _discover_handlers(self):
        """Динамически обнаружить все доступные handlers"""
        from . import handlers
        import inspect
        
        discovered = {}
        
        # Автоматически находим все функции в handlers
        for name, func in inspect.getmembers(handlers, inspect.isfunction):
            if not name.startswith('_'):  # Игнорируем приватные
                # Извлекаем сигнатуру функции
                sig = inspect.signature(func)
                params = [p for p in sig.parameters.keys() if p != 'user_id']
                
                # Пытаемся получить описание из docstring
                doc = inspect.getdoc(func) or f"Функция {name}"
                first_line = doc.split('\n')[0]
                
                discovered[name] = {
                    "description": first_line,
                    "params": params,
                    "required": []  # AI сам определит обязательные
                }
        
        # Добавляем базовый набор, если автообнаружение не сработало
        if not discovered:
            discovered = self._get_default_tools()
        
        logger.info(f"[AGENT] Discovered {len(discovered)} handlers: {list(discovered.keys())}")
        return discovered
    
    def _get_default_tools(self):
        """Базовый набор инструментов (fallback)"""
        return {
            "add_task": {
                "description": "Создать новую задачу с напоминанием",
                "params": ["title", "description", "reminder_time", "is_recurring", "recurrence_pattern"],
                "required": ["title", "reminder_time"]
            },
            "list_tasks": {
                "description": "Получить список задач пользователя",
                "params": ["filter_type", "sort_by", "limit"],
                "required": []
            },
            "complete_task": {
                "description": "Отметить задачу как выполненную", 
                "params": ["task_title", "completion_note"],
                "required": ["task_title"]
            },
            "reschedule_task": {
                "description": "Перенести задачу на другое время",
                "params": ["task_title", "new_time"],
                "required": ["task_title", "new_time"]
            },
            "delete_task": {
                "description": "Удалить задачу",
                "params": ["task_title"],
                "required": ["task_title"]
            },
            "edit_task": {
                "description": "Редактировать существующую задачу",
                "params": ["task_title", "new_title", "new_description", "new_reminder_time"],
                "required": ["task_title"]
            },
            "get_task_details": {
                "description": "Получить подробную информацию о задаче",
                "params": ["task_title"],
                "required": ["task_title"]
            },
            "find_relevant_contacts_for_task": {
                "description": "Найти релевантные контакты для задачи/активности",
                "params": ["task_description", "limit"],
                "required": ["task_description"]
            },
            "delegate_task": {
                "description": "Делегировать задачу другому пользователю",
                "params": ["task_title", "worker_username", "deadline"],
                "required": ["task_title", "worker_username"]
            },
            "show_profile": {
                "description": "Показать профиль пользователя",
                "params": [],
                "required": []
            },
            "update_profile": {
                "description": "Обновить профиль пользователя",
                "params": ["field", "value"],
                "required": ["field", "value"]
            },
            "check_subscription_status": {
                "description": "Проверить статус подписки",
                "params": [],
                "required": []
            }
        }

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
        ШАГ 1: AI планирует стратегию выполнения запроса с учетом предыдущего опыта
        Возвращает список действий, которые нужно выполнить
        """
        
        tools_info = json.dumps(self.available_tools, indent=2, ensure_ascii=False)
        
        # Получаем информацию о пользователе
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {
                    "intent": "пользователь не найден",
                    "needs_context": False,
                    "actions": [],
                    "response_strategy": "сообщить об ошибке"
                }
            
            # Получаем задачи
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status != 'completed'
            ).limit(10).all()
            tasks_summary = [{"title": t.title, "due_date": str(t.due_date) if t.due_date else None} for t in tasks]
            
            # Анализируем историю для похожих паттернов
            learning_context = ""
            recent_success = [
                e for e in self.execution_history[-10:]
                if e.get('user_id') == user_id and e.get('success')
            ]
            if recent_success:
                learning_context = "\n\nУСПЕШНЫЙ ОПЫТ ПОЛЬЗОВАТЕЛЯ:\n"
                for entry in recent_success[-3:]:
                    actions_used = ", ".join([a.get('tool', '') for a in entry.get('plan', {}).get('actions', [])])
                    learning_context += f"- '{entry['message'][:50]}' → использовал: {actions_used}\n"
            
            # Получаем задачи
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status != 'completed'
            ).limit(10).all()
            tasks_summary = [{"title": t.title, "due_date": str(t.due_date) if t.due_date else None} for t in tasks]
            
            # Получаем базовый промпт
            base_prompt = get_extended_system_prompt(
                user_now=None,
                current_time_str=None,
                current_date_str=None,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=user.memory or "",
                context=context,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'FREE'),
                message_type=None,
                weather_info=None,
                news_info=None
            )
        finally:
            session.close()
        
        # Дополняем базовый промпт инструкциями для планирования
        system_prompt = f"{base_prompt}\n\n" + f"""\n---

РЕЖИМ: ПЛАНИРОВАНИЕ ДЕЙСТВИЙ

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{tools_info}

ТЕКУЩИЕ ЗАДАЧИ:
{json.dumps(tasks_summary, indent=2, ensure_ascii=False)}
{learning_context}

ЗАДАЧА: Проанализируй запрос и составь ПЛАН действий в JSON формате:

{{
    "intent": "намерение пользователя",
    "actions": [
        {{"tool": "название", "params": {{}}, "reason": "зачем"}}
    ]
}}

ПРАВИЛА:
- Используй ТОЛЬКО инструменты из списка
- Минимум действий для достижения цели
- Извлекай параметры из запроса пользователя
- Для задач про активности добавляй find_relevant_contacts_for_task
- УЧИСЬ: примени успешные паттерны из истории если они релевантны
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Запрос: {user_message}"}
        ]

        response = await self.call_ai(messages)
        content = response['choices'][0]['message']['content']

        try:
            # Извлекаем JSON
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                return plan
            else:
                return {
                    "intent": "не распознано",
                    "needs_context": False,
                    "actions": [],
                    "response_strategy": "ответить естественно"
                }
        except Exception as e:
            logger.error(f"Ошибка парсинга плана: {e}")
            return {
                "intent": "ошибка парсинга",
                "needs_context": False,
                "actions": [],
                "response_strategy": "извиниться и попросить переформулировать"
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
                
                # Выполняем handler
                result = await handler_func(**params) if asyncio.iscoroutinefunction(handler_func) else handler_func(**params)
                
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

    async def process_request(self, user_message, user_id, context=None):
        """
        Основной процесс обработки запроса:
        1. Планирование стратегии
        2. Выполнение действий
        3. Рефлексия и формирование ответа
        """
        
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
            
            # ШАГ 3: Рефлексия и ответ
            logger.info(f"[AGENT] Step 3: Reflecting and generating response")
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
        return {
            'response': response_text,
            'tool_calls': []  # Автономный агент управляет вызовами инструментов самостоятельно
        }

    except Exception as e:
        logger.error(f"[HYBRID_AGENT] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            'response': f"Извините, произошла ошибка: {str(e)}",
            'tool_calls': []
        }