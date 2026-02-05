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
        self.available_tools = self._get_available_tools()  # Доступные инструменты
        self.context_memory = []  # Краткосрочная память контекста

    def _get_available_tools(self):
        """Получить список доступных инструментов (handlers)"""
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
        ШАГ 1: AI планирует стратегию выполнения запроса
        Возвращает список действий, которые нужно выполнить
        """
        
        tools_info = json.dumps(self.available_tools, indent=2, ensure_ascii=False)
        
        # Получаем краткую информацию о текущих задачах
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                tasks = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status != 'completed'
                ).limit(10).all()
                tasks_summary = [{"title": t.title, "due_date": str(t.due_date) if t.due_date else None} for t in tasks]
            else:
                tasks_summary = []
        finally:
            session.close()
        
        context_str = ""
        if context and len(context) > 0:
            recent_context = context[-5:]  # Последние 5 сообщений
            context_str = "\n".join([f"{msg.get('role', 'user')}: {msg.get('content', '')[:100]}" for msg in recent_context])

        system_prompt = f"""Ты - СТРАТЕГИЧЕСКИЙ ПЛАНИРОВЩИК для AI-ассистента управления задачами.

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
{tools_info}

ТЕКУЩИЕ ЗАДАЧИ ПОЛЬЗОВАТЕЛЯ:
{json.dumps(tasks_summary, indent=2, ensure_ascii=False)}

КОНТЕКСТ РАЗГОВОРА:
{context_str}

ТВОЯ ЗАДАЧА: Проанализировать запрос и составить ПЛАН действий.

ВЕРНИ JSON в ТОЧНО таком формате:
{{
    "intent": "краткое описание намерения пользователя",
    "needs_context": true/false,
    "actions": [
        {{
            "tool": "название_инструмента",
            "params": {{"param1": "value1"}},
            "reason": "зачем вызываем этот инструмент"
        }}
    ],
    "response_strategy": "как сформировать ответ пользователю"
}}

ПРАВИЛА:
1. Используй ТОЛЬКО инструменты из списка ДОСТУПНЫЕ ИНСТРУМЕНТЫ
2. По возможности планируй МИНИМАЛЬНОЕ количество действий
3. Если нужна информация о задачах - сначала вызови list_tasks или get_task_details
4. Для создания задачи ВСЕГДА требуй reminder_time
5. Для завершения/переноса/удаления задачи нужен task_title (ключевое слово из названия)
6. Если пользователь создает задачу про активность (спорт, встречи) - добавь find_relevant_contacts_for_task
7. Будь конкретным в параметрах - извлекай их из запроса пользователя

ПРИМЕРЫ:

Запрос: "создай задачу купить молоко завтра в 9"
План:
{{
    "intent": "создать задачу о покупке",
    "needs_context": false,
    "actions": [
        {{
            "tool": "add_task",
            "params": {{"title": "Купить молоко", "reminder_time": "завтра в 9:00"}},
            "reason": "пользователь хочет создать задачу"
        }}
    ],
    "response_strategy": "подтвердить создание задачи с деталями"
}}

Запрос: "покажи мои задачи"
План:
{{
    "intent": "просмотреть список задач",
    "needs_context": false,
    "actions": [
        {{
            "tool": "list_tasks",
            "params": {{}},
            "reason": "получить список задач пользователя"
        }}
    ],
    "response_strategy": "показать задачи в понятном формате"
}}

Запрос: "готово, купил молоко"
План:
{{
    "intent": "отметить задачу выполненной",
    "needs_context": false,
    "actions": [
        {{
            "tool": "complete_task",
            "params": {{"task_title": "молоко"}},
            "reason": "завершить задачу по ключевому слову"
        }}
    ],
    "response_strategy": "поздравить с выполнением"
}}

Запрос: "перенеси встречу на завтра"
План:
{{
    "intent": "перенести задачу",
    "needs_context": true,
    "actions": [
        {{
            "tool": "reschedule_task",
            "params": {{"task_title": "встреч", "new_time": "завтра"}},
            "reason": "перенести задачу со словом 'встреч'"
        }}
    ],
    "response_strategy": "подтвердить перенос с новым временем"
}}"""

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

    async def reflect_and_respond(self, user_message, plan, execution_results, context=None):
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
        
        context_str = ""
        if context and len(context) > 0:
            recent = context[-3:]
            context_str = "\n".join([f"{m.get('role')}: {m.get('content', '')[:80]}" for m in recent])
        
        system_prompt = f"""Ты - ASI Biont, дружелюбный AI-помощник для управления задачами.

ЗАПРОС ПОЛЬЗОВАТЕЛЯ: {user_message}

ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ:
{results_text}

КОНТЕКСТ:
{context_str}

ТВОЯ ЗАДАЧА: Сформировать ЕСТЕСТВЕННЫЙ, ДРУЖЕЛЮБНЫЙ ответ пользователю.

ПРАВИЛА ОТВЕТА:
1. Говори от первого лица: "Я создал задачу", "Вот твои задачи"
2. Будь конкретным: указывай время, детали, количества
3. Давай полезный контекст: что дальше, советы, альтернативы
4. Используй эмодзи умеренно: ✅ 📝 ⏰ 🎯
5. Если была ошибка - объясни причину и предложи решение
6. Структурируй информацию для читаемости
7. Завершай ответ полезным действием или вопросом

ПРИМЕРЫ:

Создание задачи:
"✅ Отлично! Я создал задачу 'Купить молоко' на завтра в 9:00. Напомню тебе за 30 минут. Хочешь добавить что-то еще?"

Список задач:
"📝 У тебя сейчас 5 активных задач:

1. ⏰ Купить молоко - завтра в 9:00
2. ⏰ Встреча с командой - сегодня в 15:00
3. ⏰ Позвонить маме - через 2 часа

Ближайшая - встреча через 3 часа. Готов?"

Выполнение задачи:
"🎉 Отлично! Задача 'Купить молоко' выполнена! У тебя осталось 4 задачи. Следующая - встреча с командой в 15:00."

Верни ТОЛЬКО текст ответа, без JSON, без технических деталей."""

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
                context
            )
            
            # Сохраняем в историю
            self.execution_history.append({
                'message': user_message,
                'plan': plan,
                'results': execution_results,
                'response': response,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            
            # Ограничиваем размер истории
            if len(self.execution_history) > 20:
                self.execution_history = self.execution_history[-20:]
            
            return response
            
        except Exception as e:
            logger.error(f"[AGENT] Error processing request: {e}")
            import traceback
            traceback.print_exc()
            return "Извините, произошла ошибка при обработке запроса. Попробуйте переформулировать."


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