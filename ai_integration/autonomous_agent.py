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
        ШАГ 1: AI планирует стратегию выполнения запроса с учетом предыдущего опыта
        Возвращает список действий, которые нужно выполнить
        """
        
        # Получаем релевантные инструменты для данного контекста и пользователя
        relevant_tools = self.tool_discovery.get_tools_for_context(user_message, user_id)
        tools_info = json.dumps(relevant_tools, indent=2, ensure_ascii=False)
        
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