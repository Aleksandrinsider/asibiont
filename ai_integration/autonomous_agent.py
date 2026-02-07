import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
import logging
import pytz
from datetime import datetime, timezone
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, Task, UserProfile, Subscription
from .prompts import get_extended_system_prompt
from .dynamic_tools import tool_discovery
from .tools import TOOLS  # Импорт списка инструментов

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
                            reminder_dt = task.reminder_time.replace(tzinfo=pytz.UTC).astimezone(user_now.tzinfo)
                            if reminder_dt < user_now:
                                overdue.append(task.title)
                            elif reminder_dt.date() == user_now.date():
                                today.append(task.title)
                        except Exception as e:
                            logger.warning(f"[AGENT] Error parsing task reminder time: {e}")
                
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
            logger.debug(f"[HYBRID] Tools list: {[t['function']['name'] for t in TOOLS[:5]]}...")  # Первые 5

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    # Логируем, вызвал ли AI какие-то tools
                    if use_tools:
                        message = result.get('choices', [{}])[0].get('message', {})
                        tool_calls = message.get('tool_calls', [])
                        if tool_calls:
                            logger.info(f"[HYBRID] AI returned {len(tool_calls)} tool calls")
                        else:
                            logger.warning(f"[HYBRID] AI did NOT call any tools despite having {len(TOOLS)} available")
                            logger.warning(f"[HYBRID] AI response content: {message.get('content', '')[:200]}")
                    return result
                else:
                    error_text = await response.text()
                    raise Exception(f"AI call failed: {response.status} {error_text}")

    async def plan_strategy(self, user_message, user_id, context=None):
        """
        ШАГ 1: ГИБРИДНОЕ ПЛАНИРОВАНИЕ - Детектирование + принудительный tool_choice
        
        Применяем гибридный подход ко ВСЕМ командам для гарантии выполнения
        """
        
        message_lower = user_message.lower()
        
        # ЗАДАЧИ
        if any(kw in message_lower for kw in ['покажи задач', 'список', 'мои задач', 'что у меня']):
            if 'задач' in message_lower or 'дел' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'list_tasks')
        
        if any(kw in message_lower for kw in ['готово', 'сделал', 'завершил', 'выполнил', 'закончил', 'проверил']):
            return await self._plan_with_required_tool(user_message, user_id, 'complete_task')
        
        if any(kw in message_lower for kw in ['создай', 'добавь', 'напомни', 'поставь напоминание']):
            if not any(w in message_lower for w in ['перенес', 'отлож', 'подвин', 'измени']):
                time_indicators = ['завтра', 'сегодня', 'через', 'в ', ':', 'утра', 'вечера', 'дня', 'ночи', 'понедельник', 'вторник', 'среду', 'четверг', 'пятниц', 'суббот', 'воскресень']
                has_time = any(indicator in message_lower for indicator in time_indicators)
                if has_time:
                    return await self._plan_with_required_tool(user_message, user_id, 'add_task')
                else:
                    return await self._plan_general_chat(user_message, user_id)
        
        if any(kw in message_lower for kw in ['удали', 'сотри', 'убери задач']):
            return await self._plan_with_required_tool(user_message, user_id, 'delete_task')
        
        if any(kw in message_lower for kw in ['перенес', 'отлож', 'подвин']):
            if 'задач' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'reschedule_task')
        
        if any(kw in message_lower for kw in ['измени', 'переименуй', 'отредактируй']):
            if 'задач' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'edit_task')
        
        # АНАЛИЗ
        if any(kw in message_lower for kw in ['анализ', 'что делать', 'приоритет']):
            if 'задач' in message_lower or 'дел' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'analyze_tasks')
        
        # ПАРТНЕРЫ И КОНТАКТЫ
        if any(kw in message_lower for kw in ['найди партнер', 'ищу единомышленник', 'кто занимается', 'с кем можно']):
            return await self._plan_with_required_tool(user_message, user_id, 'find_partners')
        
        if any(kw in message_lower for kw in ['кто может помочь', 'кто поможет', 'найди кто']):
            return await self._plan_with_required_tool(user_message, user_id, 'find_relevant_contacts_for_task')
        
        # ДЕЛЕГИРОВАНИЕ
        if any(kw in message_lower for kw in ['делегируй', 'передай', 'поручи']):
            if 'задач' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'delegate_task')
        
        # ПРОФИЛЬ
        if any(kw in message_lower for kw in ['обнови профиль', 'измени профиль']):
            if any(kw2 in message_lower for kw2 in ['город', 'интерес', 'цел', 'навык', 'компани', 'должность']):
                return await self._plan_with_required_tool(user_message, user_id, 'update_profile')
        
        if any(kw in message_lower for kw in ['покажи профиль', 'мой профиль', 'что в профиле']):
            if 'обнов' not in message_lower and 'измен' not in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'show_profile')
        
        # ДЕТАЛИ ЗАДАЧИ
        if any(kw in message_lower for kw in ['детали задач', 'покажи задач', 'подробности задач']):
            if any(kw2 in message_lower for kw2 in ['какая', 'что', 'про']):
                return await self._plan_with_required_tool(user_message, user_id, 'get_task_details')
        
        # УДАЛИТЬ ВСЕ
        if any(kw in message_lower for kw in ['удали все', 'очисти все', 'сотри все']):
            if 'задач' in message_lower:
                return await self._plan_with_required_tool(user_message, user_id, 'delete_all_tasks')
        
        # Всё остальное - свободный AI с полным набором tools
        return await self._plan_general_chat(user_message, user_id)
    
    async def _plan_with_required_tool(self, user_message, user_id, tool_name):
        """
        Планирование с принудительным вызовом конкретного инструмента
        AI обязан вызвать указанный tool, но сам извлекает параметры
        """
        
        # Получаем базовый промпт
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {
                    "intent": "general_chat",
                    "actions": [],
                    "response_strategy": "natural_response"
                }

            # Собираем контекст (время, задачи, профиль)
            base_now = datetime.now(pytz.UTC)
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
            except:
                user_now = base_now
            
            # Текущая задача если есть
            current_task_info = None
            if user.current_task_id:
                try:
                    task = session.query(Task).filter_by(id=user.current_task_id).first()
                    if task:
                        current_task_info = {
                            'id': task.id,
                            'title': task.title,
                            'status': task.status
                        }
                except Exception as e:
                    logger.error(f"Error loading current task: {e}")
        finally:
            session.close()
        
        # Формируем промпт под конкретный инструмент
        tool_instructions = {
            'add_task': f"""Создать задачу: "{user_message}"
Вызови add_task с title и reminder_time (извлеки время: "завтра в 10" → "завтра 10:00", null если нет)
Сейчас: {user_now.strftime('%H:%M, %d.%m.%Y')}""",
            
            'complete_task': f"""Завершить: "{user_message}"
{f"ТЕКУЩАЯ ЗАДАЧА: {current_task_info['title']} (ID: {current_task_info['id']})" if current_task_info else ""}
Вызови complete_task""",
            
            'list_tasks': """Показать задачи. Вызови list_tasks.""",
            
            'delete_task': f"""Удалить задачу: "{user_message}". Вызови delete_task""",
            
            'reschedule_task': f"""Перенести: "{user_message}". Вызови reschedule_task""",
            
            'edit_task': f"""Изменить: "{user_message}". Вызови edit_task""",
            
            'analyze_tasks': """Проанализировать задачи. Вызови analyze_tasks.""",
            
            'find_partners': f"""Найти партнеров: "{user_message}". Вызови find_partners.""",
            
            'find_relevant_contacts_for_task': f"""Найти контакты для: "{user_message}". Вызови find_relevant_contacts_for_task с task_description""",
            
            'delegate_task': f"""Делегировать: "{user_message}". Вызови delegate_task""",
            
            'update_profile': f"""Обновить профиль: "{user_message}". Вызови update_profile с соответствующими параметрами""",
            
            'show_profile': """Показать профиль. Вызови show_profile.""",
            
            'get_task_details': f"""Детали задачи: "{user_message}". Вызови get_task_details с task_title""",
            
            'delete_all_tasks': """Удалить все задачи. Вызови delete_all_tasks."""
        }
        
        system_prompt = tool_instructions.get(tool_name, f"Вызови {tool_name}")
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # Вызываем AI с tool_choice для конкретного инструмента
        try:
            response = await self.call_ai(
                messages,
                use_tools=True,
                tool_choice={"type": "function", "function": {"name": tool_name}}
            )
            
            message = response['choices'][0]['message']
            tool_calls = message.get('tool_calls', [])
            
            if tool_calls:
                actions = []
                for tool_call in tool_calls:
                    function = tool_call.get('function', {})
                    try:
                        arguments = json.loads(function.get('arguments', '{}'))
                    except:
                        arguments = {}
                    
                    actions.append({
                        "tool": function.get('name', ''),
                        "params": arguments,
                        "reason": f"Required tool: {tool_name}"
                    })
                    logger.info(f"[HYBRID REQUIRED] AI called {function.get('name')} with params {arguments}")
                
                return {
                    "intent": tool_name,
                    "actions": actions,
                    "response_strategy": "execute_action"
                }
        except Exception as e:
            logger.error(f"[HYBRID REQUIRED] Failed to call with tool_choice: {e}")
            # Fallback на обычное планирование
            return await self._plan_general_chat(user_message, user_id)
        
        # Если AI не вызвал инструмент - fallback
        logger.warning(f"[HYBRID REQUIRED] AI did not call required tool {tool_name}")
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
        elif intent == 'edit_task':
            params = {
                "task_title": self._extract_task_title(user_message),
                "title": self._extract_new_title(user_message)
            }
        elif intent == 'add_task':
            title, time_str = self._extract_task_info(user_message)
            params = {"title": title, "reminder_time": time_str}
        elif intent == 'find_relevant_contacts_for_task':
            params = {"task_description": user_message}
        elif intent == 'get_task_details':
            params = {"task_title": self._extract_task_title(user_message)}
        elif intent == 'delegate_task':
            params = {
                "task_title": self._extract_task_title(user_message),
                "delegate_to": self._extract_delegate_username(user_message)
            }

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
        """
        AI планирование с TOOLS - гибридный подход.
        AI сам решает какие инструменты вызвать через DeepSeek tool_calls.
        """
        # Получаем информацию о пользователе напрямую из БД
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {
                    "intent": "general_chat",
                    "actions": [],
                    "response_strategy": "natural_response"
                }

            # Определяем текущее время пользователя
            base_now = datetime.now(pytz.UTC)
            user_now = base_now
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            current_date_str = user_now.strftime("%Y-%m-%d")
            
            months = [
                'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
            ]
            
            # Получаем timezone пользователя, по умолчанию Москва
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")
                # Fallback на московское время
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                except Exception as e:
                    logger.warning(f"[AGENT] Moscow timezone fallback failed: {e}")

            # Получаем погоду и новости
            weather_info = None
            news_info = None
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile and profile.city:
                from .utils import get_weather_info, get_news_info
                weather_info = get_weather_info(profile.city)
                news_info = get_news_info(profile.city)

            # Расшифровываем память
            decrypted_memory = ""
            if user.memory:
                try:
                    from .memory import decrypt_data
                    decrypted_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.error(f"Error decrypting memory: {e}")

            # Получаем информацию о текущей задаче если есть
            current_task_info = None
            if user.current_task_id:
                try:
                    task = session.query(Task).filter_by(id=user.current_task_id).first()
                    if task:
                        current_task_info = {
                            'id': task.id,
                            'title': task.title,
                            'status': task.status
                        }
                        logger.info(f"[AGENT] Current task in planning: '{task.title}' (ID: {task.id})")
                except Exception as e:
                    logger.error(f"Error loading current task: {e}")

            base_prompt = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=current_time_str,
                current_date_str=current_date_str,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=decrypted_memory,
                context=None,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'FREE'),
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                current_task_info=current_task_info
            )
        finally:
            session.close()

        system_prompt = f"{base_prompt}\n\n" + """Ты ведешь естественный разговор с доступом к инструментам.

🚨 АБСОЛЮТНЫЙ ПРИОРИТЕТ - ЗАКРЫТИЕ ТЕКУЩЕЙ ЗАДАЧИ:
ЕСЛИ в промпте выше есть "🎯 ТЕКУЩАЯ ЗАДАЧА В ФОКУСЕ":
- ANY подтверждение выполнения = НЕМЕДЛЕННО вызови complete_task
- Примеры: "сделал", "готово", "проверил", "выполнил", "закончил", "сделано", "завершил", "закончил с этим", "закончил с ней"
- ИЛИ фразы: "я уже [глагол]", "уже [глагол]", "всё", "её закрыл", "закрыл её", "его закрыл"
- ИЛИ просто глагол совершенного вида без дополнений
- НЕ спрашивай "какую задачу?" - используй ТЕКУЩУЮ ЗАДАЧУ из контекста выше
- ВЫЗОВИ complete_task БЕЗ ПАРАМЕТРОВ - система автоматически закроет current_task

⚡ ПРОАКТИВНОЕ ПОВЕДЕНИЕ ПРИ ПРОСМОТРЕ ЗАДАЧ:

КРИТИЧНО! ЕСЛИ пользователь просит показать задачи ("покажи задачи", "мои задачи", "список"):
1. Вызови list_tasks ОДИН раз
2. ПОДОЖДИ результат list_tasks  
3. В СЛЕДУЮЩЕМ вызове AI (с результатами):
   - Если в результатах есть просроченные задачи → предложи помощь с переносом
   - Если есть задачи с ключевыми словами "пробежка", "встреча", "тренировка", "спорт" → вызови find_relevant_contacts_for_task(task_description="название")
   - НЕ останавливайся на просмотре - ДЕЙСТВУЙ ПРОАКТИВНО

⚠️ ВАЖНО: Система позволяет множественные вызовы tool calls - используй это!
Не спрашивай разрешения - ДЕЙСТВУЙ СРАЗУ, а в ответе объясни что сделал.

Можешь использовать любые доступные инструменты когда это нужно."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Сообщение: {user_message}"}
        ]

        # ГИБРИДНЫЙ ПОДХОД: AI с tools - сам решает когда нужно вызвать инструменты
        response = await self.call_ai(messages, use_tools=True)
        
        message = response['choices'][0]['message']
        content = message.get('content', '')
        tool_calls = message.get('tool_calls', [])
        
        # Если AI вызвал инструменты - создаем план с действиями
        if tool_calls:
            actions = []
            for tool_call in tool_calls:
                function = tool_call.get('function', {})
                tool_name = function.get('name', '')
                try:
                    arguments = json.loads(function.get('arguments', '{}'))
                except:
                    arguments = {}
                
                actions.append({
                    "tool": tool_name,
                    "params": arguments,
                    "reason": f"AI решил вызвать {tool_name}"
                })
                logger.info(f"[AGENT] AI called tool: {tool_name} with params {arguments}")
            
            return {
                "intent": "ai_tool_call",
                "actions": actions,
                "response_strategy": "execute_action"
            }
        
        # Если инструменты не вызваны - просто общение
        return {
            "intent": "general_chat",
            "actions": [],
            "response_strategy": "natural_response",
            "ai_response": content
        }

    async def execute_actions(self, actions, user_id):
        """
        ШАГ 2: Выполнить запланированные действия через готовые handlers
        ПРОАКТИВНОСТЬ: Автоматический анализ результатов list_tasks
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
                
                # ⚡ ПРОАКТИВНЫЙ АНАЛИЗ after list_tasks
                if tool_name == 'list_tasks' and result and isinstance(result, str):
                    logger.info(f"[PROACTIVE] Analyzing list_tasks result for proactive actions")
                    proactive_actions = []
                    
                    # Проверка на социальные активности (автоматически ищем партнеров)
                    social_keywords = ['пробежка', 'встреча', 'тренировка', 'спорт', 'кофе']
                    for keyword in social_keywords:
                        if keyword.lower() in result.lower():
                            logger.info(f"[PROACTIVE] Found social activity with keyword '{keyword}', searching partners")
                            # Извлекаем название задачи с этим ключевым словом
                            import re
                            pattern = rf"'([^']*{keyword}[^']*?)'(?:\s+в\s+\d+:\d+|[\s\.])"
                            match = re.search(pattern, result, re.IGNORECASE)
                            if match:
                                social_title = match.group(1)
                                proactive_actions.append({
                                    'tool': 'find_relevant_contacts_for_task',
                                    'params': {'task_description': social_title},  # Исправлено: task_description вместо task_title
                                    'reason': f'Проактивно ищу партнеров для "{social_title}"'
                                })
                                break  # Только один поиск партнеров за раз
                    
                    # Выполняем проактивные действия
                    if proactive_actions:
                        logger.info(f"[PROACTIVE] Executing {len(proactive_actions)} proactive actions")
                        proactive_results = await self.execute_actions(proactive_actions, user_id)
                        results.extend(proactive_results)
                
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
            news_info = None
            if user:
                from models import UserProfile
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    if profile.city:
                        profile_data['city'] = profile.city
                        # Получаем погоду и новости для города
                        from .utils import get_weather_info, get_news_info
                        weather_info = get_weather_info(profile.city)
                        news_info = get_news_info(profile.city)
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
            
            # Определяем текущее время польз ователя
            base_now = datetime.now(pytz.UTC)
            user_now = base_now
            current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
            current_date_str = user_now.strftime("%Y-%m-%d")
            
            months = [
                'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
            ]
            
            # Получаем timezone пользователя, по умолчанию Москва
            user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
            try:
                user_tz = pytz.timezone(user_timezone)
                user_now = base_now.astimezone(user_tz)
                current_time_str = f"{user_now.strftime('%H:%M')} ({user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")
                # Fallback на московское время
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    current_time_str = f"{user_now.strftime('%H:%M')} (Europe/Moscow)"
                    current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
                except Exception as e:
                    logger.warning(f"[AGENT] Moscow timezone fallback failed in reflect: {e}")
            
            # Расшифровываем память
            decrypted_memory = ""
            if user and user.memory:
                try:
                    from .memory import decrypt_data
                    decrypted_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.error(f"Error decrypting memory: {e}")

            # Получаем информацию о текущей задаче если есть
            current_task_info = None
            if user and user.current_task_id:
                try:
                    task = session.query(Task).filter_by(id=user.current_task_id).first()
                    if task:
                        current_task_info = {
                            'id': task.id,
                            'title': task.title,
                            'status': task.status
                        }
                        logger.info(f"[AGENT] Current task in focus: '{task.title}' (ID: {task.id})")
                except Exception as e:
                    logger.error(f"Error loading current task: {e}")

            # Получаем базовый промпт
            base_prompt = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=current_time_str,
                current_date_str=current_date_str,
                user_username=user.username if user else "пользователь",
                mentions_str="",
                user_memory=decrypted_memory,
                context=context,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'FREE') if user else 'FREE',
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                profile_data=profile_data,
                current_task_info=current_task_info
            )
        finally:
            session.close()
        
        # Дополняем базовый промпт инструкциями для ответа
        profile_section = f"\nПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n{profile_data}" if profile_data else ""
        system_prompt = f"{base_prompt}{profile_section}\n\n" + f"""\n---

РЕЖИМ: ФОРМИРОВАНИЕ ОТВЕТА

ЗАПРОС ПОЛЬЗОВАТЕЛЯ: {user_message}

ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ И РЕЗУЛЬТАТЫ:
{results_text}

ЗАДАЧА: 
1. Проанализируй результаты
2. Сформируй естественный конкретный ответ с объяснением что сделано
3. Если есть просроченные задачи - упомяни их и предложи помощь
4. Если нашлись партнеры - расскажи о них
5. Будь проактивным в предложениях, но не навязчивым

Верни ТОЛЬКО текст ответа пользователю (БЕЗ вызовов функций).
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        # Генерируем ответ БЕЗ инструментов (они уже выполнены)
        response = await self.call_ai(messages, use_tools=False, temperature=0.7)
        
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
        
        # КРИТИЧЕСКИ ВАЖНО: Очищаем от технических деталей и DSML тегов
        from .utils import clean_technical_details
        content = clean_technical_details(content)
        
        return content.strip()

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

    def _extract_new_title(self, message):
        """Извлекает новое название из сообщения типа 'измени X на Y'"""
        import re
        # Ищем паттерн "на 'новое название'" или "на новое название"
        match = re.search(r"на ['\"]?([^'\"]+)['\"]?", message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Если не нашли, берём всё после ключевого слова
        words = message.lower().split()
        keywords = ['измени', 'поменяй', 'отредактируй']
        for i, word in enumerate(words):
            if any(kw in word for kw in keywords):
                remaining = ' '.join(words[i+1:])
                if 'на' in remaining:
                    parts = remaining.split('на', 1)
                    if len(parts) > 1:
                        return parts[1].strip().strip('"').strip("'")
        return message.strip()

    def _extract_delegate_username(self, message):
        """Извлекает имя пользователя для делегирования"""
        # Ищем @username или просто username после ключевых слов
        words = message.split()
        keywords = ['делегируй', 'передай', 'поручи']
        for i, word in enumerate(words):
            if any(kw in word.lower() for kw in keywords):
                # Берём последнее слово как username
                if i + 1 < len(words):
                    for w in words[i+1:]:
                        if w.startswith('@'):
                            return w[1:]  # убираем @
                        elif not w.lower() in ['задач', 'задачу', 'на']:
                            return w  # берём первое подходящее слово
        # Fallback - последнее слово
        return words[-1] if words else "unknown"

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