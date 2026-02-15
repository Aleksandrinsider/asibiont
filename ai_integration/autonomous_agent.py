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
from .tools import get_available_tools

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
        self.active_sessions = 0  # Счетчик активных сессий
        
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
            logger.warning("[AGENT] Dynamic tools initialization failed, using base tools only")

    # _generate_proactive_context удалён — весь проактивный контекст генерируется в context_builder.build_proactive_context()

    async def call_ai(self, messages, use_tools=False, save_history=False, user_id=None, subscription_tier=None, tool_choice=None, exclude_tools=None, **kwargs):
        """Универсальный вызов AI API с опциональными tools
        
        Args:
            exclude_tools: set of tool names to exclude (for preventing duplicates in reflect)
        """
        
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
            available_tools = get_available_tools(subscription_tier)
            # Фильтруем уже вызванные tools чтобы предотвратить дубликаты
            if exclude_tools:
                available_tools = [t for t in available_tools if t['function']['name'] not in exclude_tools]
                logger.info(f"[HYBRID] Excluded {len(exclude_tools)} already-executed tools: {exclude_tools}")
            data["tools"] = available_tools
            # Используем переданный tool_choice или по умолчанию "auto"
            data["tool_choice"] = tool_choice if tool_choice is not None else "auto"
            logger.info(f"[HYBRID] Calling AI with {len(available_tools)} tools available for tier {subscription_tier}, tool_choice={data['tool_choice']}")
            logger.info(f"[HYBRID] First 3 tools: {[t['function']['name'] for t in available_tools[:3]]}")
            logger.debug(f"[HYBRID] Tools list: {[t['function']['name'] for t in available_tools[:5]]}...")  # Первые 5

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    result = await response.json()
                    # Логируем, вызвал ли AI какие-то tools
                    if use_tools:
                        message = result.get('choices', [{}])[0].get('message', {})
                        tool_calls = message.get('tool_calls', [])
                        if tool_calls:
                            logger.info(f"[HYBRID] AI returned {len(tool_calls)} tool calls")
                            for i, tc in enumerate(tool_calls):
                                logger.info(f"[HYBRID] Tool call {i+1}: {tc.get('function', {}).get('name', 'unknown')}")
                        else:
                            logger.warning(f"[HYBRID] AI did NOT call any tools despite having {len(available_tools)} available")
                            logger.warning(f"[HYBRID] AI response content: {message.get('content', '')[:300]}")
                            logger.warning(f"[HYBRID] User message was: {messages[-1]['content'] if messages else 'unknown'}")
                    return result
                else:
                    error_text = await response.text()
                    raise Exception(f"AI call failed: {response.status} {error_text}")

    # ===== КЛАССИФИКАТОР СЛОЖНОСТИ ЗАПРОСА =====

    def _classify_complexity(self, user_message):
        """
        Классифицирует сложность запроса БЕЗ API-вызова (нулевая стоимость).
        
        Возвращает: 'simple', 'medium', 'complex', 'deep'
        
        simple  → 0 агентов, прямой AI (привет, ок, спасибо)
        medium  → 0 агентов, AI + tools (создай задачу, покажи цели)
        complex → 2 агента (аналитик + критик) + AI + tools
        deep    → полный pipeline (все агенты по тарифу)
        """
        import re
        msg = user_message.lower().strip()
        words = msg.split()
        word_count = len(words)
        
        # SIMPLE: приветствия, подтверждения, короткие реплики
        # Используем \b для точного совпадения слов (иначе "да" матчит "удали")
        simple_words = r'\b(привет|здравствуй|хай|hi|hello|добрый|ок|ладно|хорошо|понятно|ясно|спасибо|да|нет|угу|ага|пока|bye|здорово)\b'
        simple_phrases = ['как дела', 'что умеешь', 'кто ты']
        is_simple_word = bool(re.search(simple_words, msg))
        is_simple_phrase = any(p in msg for p in simple_phrases)
        if word_count <= 4 and (is_simple_word or is_simple_phrase):
            return 'simple'
        if word_count <= 2:
            return 'simple'
        
        # DEEP: аналитика, стратегия, исследование, прогнозы
        # Требуем минимум 7 слов, чтобы "оцени задачу" не стало deep
        deep_patterns = [
            'стратег', 'исследу', 'прогноз', 'тренд',
            'рынок', 'конкурент', 'инвест', 'масштабир', 'оптимиз',
            'план развития', 'бизнес-план', 'маркетинг',
            'что думаешь о', 'помоги разобраться',
            'в чём разница', 'сравни',
        ]
        if any(p in msg for p in deep_patterns) and word_count >= 7:
            return 'deep'
        
        # COMPLEX: длинные сообщения, вопросы, несколько предложений
        has_question = '?' in user_message
        has_multiple_sentences = user_message.count('.') >= 2 or user_message.count('!') >= 2
        if word_count >= 12 or (has_question and word_count >= 8) or has_multiple_sentences:
            return 'complex'
        
        # MEDIUM: всё остальное (команды, средние запросы)
        return 'medium'

    # ===== МУЛЬТИ-АГЕНТНЫЙ КОНВЕЙЕР МЫШЛЕНИЯ =====

    async def _run_thinking_pipeline(self, user_message, context_summary, subscription_tier='LIGHT', progress_callback=None):
        """
        Мульти-агентный конвейер мышления.
        Каждый агент — быстрый вызов AI без инструментов.
        progress_callback — async func(text) для стриминга прогресса пользователю.
        
        LIGHT:    Аналитик → Критик (2 вызова)
        STANDARD: Аналитик → Критик → Стратег (3 вызова)
        PREMIUM:  Аналитик → Критик → Стратег → Визионер (4 вызова)
        """
        thinking = {}
        
        # Нормализуем тариф (может быть enum или строка)
        tier_str = str(subscription_tier.value if hasattr(subscription_tier, 'value') else subscription_tier).upper()
        
        # Эмодзи и заголовки для стриминга
        agent_labels = {
            'analyst': ('🔍', 'Анализирую запрос...'),
            'critic': ('⚡', 'Проверяю риски...'),
            'strategist': ('🎯', 'Строю стратегию...'),
            'visionary': ('🔮', 'Прогнозирую...'),
        }
        
        async def _stream_agent(agent_key, result_text):
            """Отправляет краткое резюме агента пользователю"""
            if not progress_callback or not result_text:
                return
            emoji, _ = agent_labels.get(agent_key, ('💭', ''))
            import re
            # Убираем markdown: **жирный**, ### заголовки
            clean = re.sub(r'\*\*|###?\s*', '', result_text).strip()
            # Убираем начальную нумерацию (1. 2. и т.д.)
            clean = re.sub(r'^\d+\.\s*', '', clean)
            # Заменяем переносы на пробелы (для однострочного прогресса)
            clean = re.sub(r'\n+', ' ', clean).strip()
            # Берём первое ПОЛНОЕ предложение (до .!? с пробелом, но не после цифры)
            match = re.search(r'(?<!\d)[.!?](?:\s|$)', clean)
            if match:
                first_sentence = clean[:match.start() + 1].strip()
            else:
                first_sentence = clean[:120].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:117] + '...'
            if len(first_sentence) > 15:  # Минимум 15 символов
                await progress_callback(f"{emoji} {first_sentence}")
        
        try:
            # Шаг 1: Аналитик (все тарифы)
            thinking['analyst'] = await self._think_as_analyst(user_message, context_summary)
            logger.info(f"[THINKING] Analyst done: {len(thinking['analyst'])} chars")
            await _stream_agent('analyst', thinking['analyst'])
            
            # Шаг 2: Критик (все тарифы)
            thinking['critic'] = await self._think_as_critic(
                user_message, context_summary, thinking['analyst']
            )
            logger.info(f"[THINKING] Critic done: {len(thinking['critic'])} chars")
            await _stream_agent('critic', thinking['critic'])
            
            # Шаг 3: Стратег (STANDARD и PREMIUM)
            if tier_str in ('STANDARD', 'PREMIUM'):
                thinking['strategist'] = await self._think_as_strategist(
                    user_message, context_summary, thinking['analyst'], thinking['critic']
                )
                logger.info(f"[THINKING] Strategist done: {len(thinking['strategist'])} chars")
                await _stream_agent('strategist', thinking['strategist'])
            
            # Шаг 4: Визионер (только PREMIUM)
            if tier_str == 'PREMIUM':
                thinking['visionary'] = await self._think_as_visionary(
                    user_message, context_summary,
                    thinking['analyst'], thinking['critic'], thinking.get('strategist', '')
                )
                logger.info(f"[THINKING] Visionary done: {len(thinking['visionary'])} chars")
                await _stream_agent('visionary', thinking['visionary'])
            
        except Exception as e:
            logger.error(f"[THINKING] Pipeline error: {e}")
            import traceback
            logger.error(f"[THINKING] Traceback: {traceback.format_exc()}")
        
        return thinking

    def _format_thinking_for_prompt(self, thinking):
        """Форматирует результаты мышления для вставки в системный промпт"""
        if not thinking:
            return ""
        
        sections = []
        
        if thinking.get('analyst'):
            sections.append(f"АНАЛИТИК: {thinking['analyst']}")
        
        if thinking.get('critic'):
            sections.append(f"КРИТИК: {thinking['critic']}")
        
        if thinking.get('strategist'):
            sections.append(f"СТРАТЕГ: {thinking['strategist']}")
        
        if thinking.get('visionary'):
            sections.append(f"ВИЗИОНЕР: {thinking['visionary']}")
        
        if not sections:
            return ""
        
        return "\n\n## ВНУТРЕННИЙ АНАЛИЗ (используй для качества ответа, НЕ цитируй пользователю)\n\n" + "\n\n".join(sections) + "\n\nУчти этот анализ при формировании ответа и выборе инструментов. Не пересказывай анализ — покажи его РЕЗУЛЬТАТ в действиях и качестве ответа."

    async def _think_as_analyst(self, user_message, context_summary):
        """Аналитик: Что происходит? Истинная потребность пользователя."""
        system_prompt = f"""Ты — Аналитик в мульти-агентной системе мышления. Твоя задача — быстрый глубокий анализ.

Проанализируй КОРОТКО (3-5 предложений):
1. Истинная потребность пользователя (не буквальная формулировка, а глубинный запрос)
2. Какой контекст из профиля/задач/целей РЕЛЕВАНТЕН этому запросу
3. Чего НЕ ХВАТАЕТ для идеального ответа (информация, уточнения)
4. Эмоциональный тон и уровень срочности

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{context_summary[:2000]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        try:
            response = await self.call_ai(messages, use_tools=False, max_tokens=300, temperature=0.3)
            return response['choices'][0]['message'].get('content', '').strip()
        except Exception as e:
            logger.error(f"[THINKING] Analyst error: {e}")
            return ""

    async def _think_as_critic(self, user_message, context_summary, analyst_output):
        """Критик: Что не так? Риски, противоречия, слепые пятна."""
        system_prompt = f"""Ты — Критик в мульти-агентной системе мышления. Твоя задача — найти проблемы, которые другие пропустили.

На основе анализа найди КОРОТКО (3-5 предложений):
1. Какие РИСКИ не учтены в запросе пользователя
2. Есть ли ПРОТИВОРЕЧИЯ в его подходе или ситуации
3. Какие ПРЕДПОЛОЖЕНИЯ делаются (и верны ли они)
4. Что может пойти НЕ ТАК и как это предотвратить

АНАЛИЗ АНАЛИТИКА:
{analyst_output}

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{context_summary[:1500]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        try:
            response = await self.call_ai(messages, use_tools=False, max_tokens=300, temperature=0.4)
            return response['choices'][0]['message'].get('content', '').strip()
        except Exception as e:
            logger.error(f"[THINKING] Critic error: {e}")
            return ""

    async def _think_as_strategist(self, user_message, context_summary, analyst_output, critic_output):
        """Стратег: Как лучше? Связь с целями и оптимизация. (STANDARD+)"""
        system_prompt = f"""Ты — Стратег в мульти-агентной системе мышления. Твоя задача — оптимизировать план и связать с целями.

Оптимизируй КОРОТКО (3-5 предложений):
1. Как запрос связан с ДОЛГОСРОЧНЫМИ целями пользователя
2. Какие ПАТТЕРНЫ поведения заметны (повторяющиеся темы, ошибки, сильные стороны)
3. Какой ОПТИМАЛЬНЫЙ план действий с учётом критики
4. Какие СИНЕРГИИ можно использовать (связать задачи, цели, контакты)

АНАЛИЗ:
{analyst_output}

КРИТИКА:
{critic_output}

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{context_summary[:1500]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        try:
            response = await self.call_ai(messages, use_tools=False, max_tokens=300, temperature=0.4)
            return response['choices'][0]['message'].get('content', '').strip()
        except Exception as e:
            logger.error(f"[THINKING] Strategist error: {e}")
            return ""

    async def _think_as_visionary(self, user_message, context_summary, analyst_output, critic_output, strategist_output):
        """Визионер: Что дальше? Сценарии и долгосрочные последствия. (PREMIUM)"""
        system_prompt = f"""Ты — Визионер в мульти-агентной системе мышления. Твоя задача — видеть будущее и неочевидные возможности.

Спрогнозируй КОРОТКО (3-5 предложений):
1. Что произойдёт через 1 неделю / 1 месяц если действовать по плану
2. Какие РЫНОЧНЫЕ/ОТРАСЛЕВЫЕ тренды релевантны ситуации пользователя
3. Какие СЦЕНАРИИ стоит подготовить (оптимистичный / пессимистичный)
4. Какое КОНКУРЕНТНОЕ ПРЕИМУЩЕСТВО можно построить

АНАЛИЗ:
{analyst_output}

КРИТИКА:
{critic_output}

СТРАТЕГИЯ:
{strategist_output}

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{context_summary[:1500]}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        try:
            response = await self.call_ai(messages, use_tools=False, max_tokens=400, temperature=0.5)
            return response['choices'][0]['message'].get('content', '').strip()
        except Exception as e:
            logger.error(f"[THINKING] Visionary error: {e}")
            return ""

    async def plan_strategy(self, user_message, user_id, context=None):
        """
        Планирование стратегии - минимальные жесткие правила, остальное через AI (гибрид)
        """
        message_lower = user_message.lower()

        # ТОЛЬКО САМЫЕ ОЧЕВИДНЫЕ КОМАНДЫ - остальное решает AI
        if 'создай задачу' in message_lower or 'добавь задачу' in message_lower:
            return {
                "intent": "add_task",
                "actions": [{
                    "tool": "add_task",
                    "params": {
                        "title": self._extract_task_title(user_message),
                        "reminder_time": self._extract_time(user_message)
                    },
                    "reason": "Прямая команда создания задачи"
                }],
                "response_strategy": "execute_action"
            }

        if 'покажи задачи' in message_lower or 'мои задачи' in message_lower or 'список задач' in message_lower:
            return {
                "intent": "list_tasks",
                "actions": [{
                    "tool": "list_tasks",
                    "params": {},
                    "reason": "Запрос списка задач"
                }],
                "response_strategy": "execute_action"
            }

        # ВСЕ ОСТАЛЬНОЕ - через AI гибридный подход
        return await self._plan_general_chat(user_message, user_id, progress_callback=getattr(self, '_progress_callback', None))

    async def _plan_general_chat(self, user_message, user_id, progress_callback=None):
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
                # Определяем время суток
                hour = user_now.hour
                if 6 <= hour < 12:
                    time_of_day = "утро"
                elif 12 <= hour < 18:
                    time_of_day = "день"
                elif 18 <= hour < 23:
                    time_of_day = "вечер"
                else:
                    time_of_day = "ночь"
                current_time_str = f"{user_now.strftime('%H:%M')} ({time_of_day}, {user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")
                # Fallback на московское время
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    # Определяем время суток в fallback
                    hour = user_now.hour
                    if 6 <= hour < 12:
                        time_of_day = "утро"
                    elif 12 <= hour < 18:
                        time_of_day = "день"
                    elif 18 <= hour < 23:
                        time_of_day = "вечер"
                    else:
                        time_of_day = "ночь"
                    current_time_str = f"{user_now.strftime('%H:%M')} ({time_of_day}, Europe/Moscow)"
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

            # Генерируем проактивный контекст (профиль, интересы, партнеры, задачи)
            from .context_builder import ContextBuilder
            context_builder = ContextBuilder()
            proactive_context = context_builder.build_proactive_context(user_id, session)
            logger.info(f"[AGENT PLANNING] Generated proactive context length: {len(proactive_context)}")

            base_prompt = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=current_time_str,
                current_date_str=current_date_str,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=decrypted_memory,
                context=None,
                intent=None,
                subscription_tier=getattr(user, 'subscription_tier', 'LIGHT'),
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                proactive_context=proactive_context,
                current_task_info=current_task_info,
                user_id_param=user_id
            )
            
            # Сохраняем данные для использования после закрытия сессии
            user_sub_tier = getattr(user, 'subscription_tier', 'LIGHT')
            
            # Формируем контекст для конвейера мышления
            context_summary = f"Пользователь: @{user.username or 'unknown'}, тариф: {user_sub_tier}\n"
            context_summary += f"Время: {current_time_str}, {current_date_str}\n"
            if proactive_context:
                context_summary += proactive_context[:2000]
            if decrypted_memory:
                context_summary += f"\nПамять: {decrypted_memory[:500]}"
            if current_task_info:
                context_summary += f"\nТекущая задача в фокусе: {current_task_info}"
        finally:
            session.close()

        # --- УМНАЯ МАРШРУТИЗАЦИЯ ПО СЛОЖНОСТИ ---
        complexity = self._classify_complexity(user_message)
        logger.info(f"[AGENT] Message complexity: {complexity} ('{user_message[:40]}...')")
        
        thinking_results = {}
        thinking_section = ""
        
        if complexity == 'simple':
            # Простые сообщения — без агентов, прямой AI
            logger.info(f"[AGENT] Skipping thinking pipeline for simple message")
        elif complexity == 'medium':
            # Средние — без агентов, AI сам разберётся с tools
            logger.info(f"[AGENT] Skipping thinking pipeline for medium message")
        elif complexity == 'complex':
            # Сложные — только Аналитик + Критик (2 вызова)
            thinking_results = await self._run_thinking_pipeline(
                user_message, context_summary, subscription_tier='LIGHT',  # LIGHT = analyst + critic only
                progress_callback=progress_callback
            )
            thinking_section = self._format_thinking_for_prompt(thinking_results)
            logger.info(f"[AGENT] Complex: 2 agents, section={len(thinking_section)} chars")
        else:  # deep
            # Глубокие — полный pipeline по тарифу
            thinking_results = await self._run_thinking_pipeline(
                user_message, context_summary, subscription_tier=user_sub_tier,
                progress_callback=progress_callback
            )
            thinking_section = self._format_thinking_for_prompt(thinking_results)
            logger.info(f"[AGENT] Deep: full pipeline ({len(thinking_results)} agents), section={len(thinking_section)} chars")

        system_prompt = f"{base_prompt}{thinking_section}\n\n" + """ГИБРИДНЫЙ ПОДХОД — ты САМ решаешь когда нужны инструменты.

ВАЖНО:
- Упоминания о себе (навыки, работа, город, интересы) → update_profile
- Вопросы типа «как создать задачу?» → просто объясни, НЕ вызывай инструмент
- Длинное сообщение с вопросами → НЕ парси как команду
- Не извлекай весь текст сообщения как title задачи — только СУТЬ
- Для переноса задач используй edit_task(reminder_time=...), НЕ add_task"""

        # Загружаем историю диалога (session уже закрыта выше — передаём None, чтобы функция создала свою)
        from .conversation_history import get_conversation_history
        history = get_conversation_history(user_id, session=None, limit=6)  # Last 3 exchanges
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Добавляем историю
        if history:
            messages.extend(history)
            logger.info(f"[AGENT] Added {len(history)} messages from history")
        
        # Добавляем текущее сообщение
        # Для простых сообщений (приветствия) — инжектируем проактивную инструкцию
        # чтобы AI не просто описывал проблемы, а ДЕЙСТВОВАЛ и ЗАДАВАЛ ВОПРОСЫ
        if complexity == 'simple':
            # Создаём краткую сводку состояния пользователя из контекста
            proactive_inject = "\n\n[СИСТЕМНАЯ ИНСТРУКЦИЯ: Это приветствие. Ты ОБЯЗАН:"
            proactive_inject += "\n1. Если профиль пустой — задай 1 конкретный вопрос (чем занимаешься? какая главная цель?)"
            proactive_inject += "\n2. Если нет задач — предложи конкретную задачу с временем, исходя из профиля и времени суток"
            proactive_inject += "\n3. Если есть задачи/цели — дай статус и один полезный инсайт"
            proactive_inject += "\n4. Будь кратким (3-5 предложений), не перечисляй функции бота"
            proactive_inject += "\nНЕ описывай проблемы — РЕШАЙ их. Не говори 'профиль не заполнен' — спроси конкретно.]"
            messages.append({"role": "user", "content": user_message + proactive_inject})
        else:
            messages.append({"role": "user", "content": user_message})

        # ГИБРИДНЫЙ ПОДХОД: AI с tools - сам решает когда нужно вызвать инструменты
        # Для приветствий НЕ принуждаем — AI сам решит, нужен ли инструмент
        force_tool_choice = "auto"  # По умолчанию AI сам решает
        if any(keyword in user_message.lower() for keyword in ['что нового', 'что посоветуешь', 'расскажи новости', 'новости']):
            force_tool_choice = "required"  # Принудительно требуем tool calls для запросов новостей
            logger.info(f"[HYBRID] Forcing tool usage for news request: '{user_message}'")
        elif any(keyword in user_message.lower() for keyword in ['задачи', 'что по задачам', 'мои задачи']):
            force_tool_choice = "required"  # Принудительно требуем tool calls для запросов задач
            logger.info(f"[HYBRID] Forcing tool usage for tasks request: '{user_message}'")
        elif any(keyword in user_message.lower() for keyword in ['партнеры', 'найти людей', 'единомышленники']):
            force_tool_choice = "required"  # Принудительно требуем tool calls для поиска партнеров
            logger.info(f"[HYBRID] Forcing tool usage for partners request: '{user_message}'")
        
        response = await self.call_ai(messages, use_tools=True, subscription_tier=user_sub_tier, tool_choice=force_tool_choice)
        
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
                "response_strategy": "execute_action",
                "thinking_results": thinking_results
            }
        
        # Если инструменты не вызваны - просто общение
        return {
            "intent": "general_chat",
            "actions": [],
            "response_strategy": "natural_response",
            "ai_response": content,
            "thinking_results": thinking_results
        }

    async def execute_actions(self, actions, user_id, session=None, user_message=None, progress_callback=None):
        """
        ШАГ 2: Выполнить запланированные действия через готовые handlers
        ПРОАКТИВНОСТЬ: Автоматический анализ результатов list_tasks
        """
        progress_callback = progress_callback or getattr(self, '_progress_callback', None)
        # Импортируем handlers
        from . import handlers
        
        # Если session не передан, создаем его
        close_session = False
        if session is None:
            # Проверяем лимит активных сессий
            if self.active_sessions >= 5:  # Максимум 5 одновременных сессий
                logger.warning(f"[AGENT] Too many active sessions ({self.active_sessions}), rejecting request")
                return [{
                    "tool": "session_limit",
                    "success": False,
                    "error": "Слишком много одновременных запросов. Попробуйте через минуту."
                }]
            
            try:
                session = Session()
                close_session = True
                self.active_sessions += 1
                logger.info(f"[AGENT] Created new session for user {user_id} (active: {self.active_sessions})")
            except Exception as e:
                logger.error(f"[AGENT] Failed to create session: {e}")
                return [{
                    "tool": "session_creation",
                    "success": False,
                    "error": f"Не удалось создать подключение к базе данных: {e}"
                }]
        else:
            logger.info(f"[AGENT] Using provided session for user {user_id}")
        
        results = []
        
        try:
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
                    
                    # Добавляем session для функций, которые его требуют
                    import inspect
                    sig = inspect.signature(handler_func)
                    if 'session' in sig.parameters:
                        params['session'] = session
                    
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
                    
                    # 🔧 ОБРАБОТКА quick_topic_search - извлекаем topic из сообщения если не указан
                    if tool_name == 'quick_topic_search':
                        logger.info(f"[AGENT] Processing quick_topic_search params: {params}")
                        if 'topic' not in params or not params['topic']:
                            # Извлекаем topic из user_message
                            if user_message:
                                # Простая эвристика - берем ключевые слова из сообщения
                                import re
                                # Убираем стоп-слова и берем первые значимые слова
                                stop_words = ['что', 'как', 'где', 'когда', 'почему', 'а', 'и', 'но', 'или', 'да', 'нет', 'там']
                                words = re.findall(r'\b\w+\b', user_message.lower())
                                topic_words = [w for w in words if w not in stop_words and len(w) > 2][:3]
                                if topic_words:
                                    params['topic'] = ' '.join(topic_words)
                                    logger.info(f"[AGENT] Extracted topic from message: '{params['topic']}'")
                                else:
                                    params['topic'] = user_message[:50]  # fallback
                                    logger.info(f"[AGENT] Using message as topic: '{params['topic']}'")
                            else:
                                params['topic'] = 'общая информация'  # ultimate fallback
                                logger.warning(f"[AGENT] No topic provided and no message, using default")
                        
                        # Убеждаемся что topic - строка
                        if not isinstance(params['topic'], str):
                            params['topic'] = str(params['topic'])
                    
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
                    
                    # ⚡ АВТОМАТИЧЕСКИЙ ТРИГГЕР: после check_time_conflicts → add_task
                    if tool_name == 'check_time_conflicts' and result:
                        logger.info(f"[AUTO_TRIGGER] check_time_conflicts succeeded, auto-triggering add_task")
                        # Извлекаем информацию о задаче из исходного сообщения
                        task_title, task_time = self._extract_task_info(user_message)
                        if task_title:
                            auto_add_action = {
                                'tool': 'add_task',
                                'params': {'title': task_title, 'reminder_time': task_time},
                                'reason': f'Автоматически после проверки конфликтов времени'
                            }
                            # Выполняем add_task
                            add_result = await self.execute_actions([auto_add_action], user_id, session)
                            results.extend(add_result)
                            logger.info(f"[AUTO_TRIGGER] Auto-executed add_task for '{task_title}'")
                    
                    # Проактивный анализ после list_tasks убран:
                    # авто-поиск партнёров по ключевым словам задач был слишком агрессивным
                    # и вызывал непредсказуемые tool calls. AI сам предложит это в ответе.
                
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
        
        # Закрываем session если создали его здесь
        finally:
            if close_session and session:
                try:
                    session.close()
                    self.active_sessions -= 1
                    logger.info(f"[AGENT] Closed session for user {user_id} (active: {self.active_sessions})")
                except Exception as e:
                    logger.warning(f"[AGENT] Error closing session: {e}")
                    self.active_sessions = max(0, self.active_sessions - 1)  # Гарантируем не отрицательное
        
        return results

    async def reflect_and_respond(self, user_message, plan, execution_results, context=None, user_id=None, subscription_tier='LIGHT'):
        """
        ШАГ 3: AI рефлексирует над результатами и формирует естественный ответ
        С ВОЗМОЖНОСТЬЮ ДОПОЛНИТЕЛЬНОГО ВЫЗОВА ИНСТРУМЕНТОВ
        """
        
        results_summary = []
        for result in execution_results:
            if result['success']:
                results_summary.append(f"УСПЕХ {result['tool']}: {result['reason']}\nРезультат: {str(result['result'])[:200]}")
            else:
                results_summary.append(f"ОШИБКА {result['tool']}: {result['error']}")
        
        results_text = "\n\n".join(results_summary)
        
        # СПЕЦИАЛЬНАЯ ОБРАБОТКА: если задача не создана из-за отсутствия времени
        for result in execution_results:
            if result['success'] and result['tool'] == 'add_task':
                result_str = str(result['result'])
                if 'NEED_TIME_FOR_TASK:' in result_str:
                    # Извлекаем сообщение о необходимости времени
                    time_message = result_str.split('NEED_TIME_FOR_TASK:', 1)[1].strip()
                    logger.info(f"[AGENT] Task creation failed due to missing time, returning clarification request")
                    return f"Чтобы создать задачу, нужно указать время. {time_message}"
        
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
                # Добавляем telegram_channel из user (не profile)
                if user.telegram_channel:
                    profile_data['telegram_channel'] = user.telegram_channel
            
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
                # Определяем время суток
                hour = user_now.hour
                if 6 <= hour < 12:
                    time_of_day = "утро"
                elif 12 <= hour < 18:
                    time_of_day = "день"
                elif 18 <= hour < 23:
                    time_of_day = "вечер"
                else:
                    time_of_day = "ночь"
                current_time_str = f"{user_now.strftime('%H:%M')} ({time_of_day}, {user_timezone})"
                current_date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"
            except Exception as e:
                logger.error(f"Error setting user timezone: {e}")
                # Fallback на московское время
                try:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    user_now = base_now.astimezone(moscow_tz)
                    # Определяем время суток в fallback
                    hour = user_now.hour
                    if 6 <= hour < 12:
                        time_of_day = "утро"
                    elif 12 <= hour < 18:
                        time_of_day = "день"
                    elif 18 <= hour < 23:
                        time_of_day = "вечер"
                    else:
                        time_of_day = "ночь"
                    current_time_str = f"{user_now.strftime('%H:%M')} ({time_of_day}, Europe/Moscow)"
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
                subscription_tier=getattr(user, 'subscription_tier', 'LIGHT') if user else 'LIGHT',
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                profile_data=profile_data,
                current_task_info=current_task_info,
                user_id_param=user_id
            )
        finally:
            session.close()
        
        # Получаем результаты мышления из фазы планирования
        thinking_results = plan.get('thinking_results', {})
        thinking_section = self._format_thinking_for_prompt(thinking_results) if thinking_results else ""
        
        # Дополняем базовый промпт инструкциями для ответа
        profile_section = f"\nПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:\n{profile_data}" if profile_data else ""
        
        # Проверяем, пустой ли профиль
        is_profile_empty = not profile_data or len(profile_data) <= 2  # только базовые поля
        profile_instruction = ""
        if is_profile_empty:
            profile_instruction = "\n\n⚠️ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ПОЧТИ ПУСТОЙ! ОБЯЗАТЕЛЬНО СПРОСИ о целях, интересах, навыках, городе проживания. Это поможет давать более релевантные советы. Задавай вопросы естественно, без давления."
        
        system_prompt = f"{base_prompt}{profile_section}{profile_instruction}{thinking_section}\n\n" + f"""\n---

РЕЖИМ: ФОРМИРОВАНИЕ ОТВЕТА

ЗАПРОС ПОЛЬЗОВАТЕЛЯ: {user_message}

ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ И РЕЗУЛЬТАТЫ:
{results_text}

ЗАДАЧА — сформируй ПРАКТИЧЕСКИ ПОЛЕЗНЫЙ ответ:

1. НАЧНИ С ИНСАЙТА, АНАЛИЗА или ВЫВОДА — что это значит для пользователя. Факт о выполненном действии упомяни ВНУТРИ ответа, НЕ в начале.
2. КОНКРЕТНЫЙ следующий шаг с ДАТОЙ/ВРЕМЕНЕМ: "Завтра в 10:00 сделай X, потому что Y"
3. Если видишь РИСК, СЛАБОЕ МЕСТО или АЛЬТЕРНАТИВУ — назови прямо, но тактично
4. Если запрос неоднозначный — задай 1 уточняющий вопрос

ЗАПРЕЩЕНО:
- НАЧИНАТЬ со слов "Отлично", "Отличное", "Отличный" (даже с эмодзи: 🚀 Отлично — ЗАПРЕЩЕНО) — сразу давай суть
- Пересказывать слова пользователя — добавляй НОВУЮ информацию
- Давать ОБЩИЕ советы без цифр, дат, конкретных инструментов, платформ
- Соглашаться со всем — если идея слабая, предложи альтернативу

ЭКСПЕРТИЗА:
- Каждый совет ОБЯЗАН содержать ЦИФРЫ, МЕТРИКИ или КОНКРЕТНЫЕ инструменты
- Плохо: "Попробуй соцсети" → Хорошо: "LinkedIn Ads для B2B дают CAC $80-120, бюджет $30/день, таргетинг по должности"
- Плохо: "Изучи конкурентов" → Хорошо: "Зарегистрируйся на триал [X], зафиксируй 5 UX-проблем — это твоё преимущество"
- Если data из research_topic — синтезируй ВЫВОДЫ, не пересказывай сырые данные

ФОРМАТ: от 3 до 15 предложений, количество определяется СЛОЖНОСТЬЮ темы. Живой тон.
НЕ используй **жирный** или *курсив* — Telegram не рендерит markdown. Эмодзи ОБЯЗАТЕЛЬНЫ — минимум 1-2 в каждом ответе.
Списки (• или 1. 2. 3.) — когда сравниваешь варианты или даёшь пошаговый план.
Варьируй начало: факт из данных → предупреждение → вопрос → инсайт. НЕ начинай каждый ответ с одного и того же эмодзи.
Верни ТОЛЬКО текст ответа пользователю.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        # TOOL CHAINING: вместо полной блокировки tools в reflect,
        # исключаем только УЖЕ вызванные tools (предотвращает дубликаты: add_task x2)
        # но разрешаем КОМПЛЕМЕНТАРНЫЕ tools (add_task → find_relevant_contacts_for_task)
        # ОПТИМИЗАЦИЯ: если plan уже вызвал research_topic, исключаем его из reflect
        # чтобы не делать двойной research (основная причина медлительности)
        executed_tool_names = set()
        for r in execution_results:
            if r.get('success'):
                executed_tool_names.add(r['tool'])
        
        # Если research_topic уже вызывался — не повторяем (экономит 10-15с)
        if 'research_topic' in executed_tool_names:
            executed_tool_names.add('quick_topic_search')  # тоже не нужен после research
        
        if executed_tool_names:
            logger.info(f"[REFLECT] Plan executed tools: {executed_tool_names} — excluding from reflect, allowing complementary tools")
        else:
            logger.info(f"[REFLECT] No actions from plan — all tools enabled as fallback")
        
        response = await self.call_ai(
            messages, 
            use_tools=True, 
            subscription_tier=subscription_tier, 
            temperature=0.7,
            exclude_tools=executed_tool_names if executed_tool_names else None
        )
        
        if not response or 'choices' not in response or not response['choices']:
            logger.error(f"[AGENT] Invalid AI response structure: {response}")
            return "Извините, произошла ошибка при формировании ответа. Попробуйте перефразировать запрос."
        
        message = response['choices'][0]['message']
        tool_calls = message.get('tool_calls', [])
        
        # Если AI запросил дополнительные tool calls - выполняем их
        if tool_calls:
            logger.info(f"[REFLECT] AI requested {len(tool_calls)} additional tool calls")
            
            # Извлекаем actions из tool_calls
            new_actions = []
            for tool_call in tool_calls:
                func = tool_call['function']
                try:
                    params = json.loads(func['arguments'])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"[REFLECT] Failed to parse arguments for {func['name']}: {func['arguments'][:200]}")
                    continue
                new_actions.append({
                    'tool': func['name'],
                    'params': params,
                    'reason': f"AI reflection: {func['name']}"
                })
            
            # ВЫПОЛНЯЕМ НОВЫЕ ИНСТРУМЕНТЫ (session уже закрыта, execute_actions создаст свою)
            new_results = await self.execute_actions(new_actions, user_id, session=None)
            execution_results.extend(new_results)
            
            # ОБНОВЛЯЕМ ПРОМПТ с результатами новых действий
            results_summary = []
            for result in execution_results:
                if result['success']:
                    results_summary.append(f"УСПЕХ {result['tool']}: {result['reason']}\nРезультат: {str(result['result'])[:200]}")
                else:
                    results_summary.append(f"ОШИБКА {result['tool']}: {result['error']}")
            
            updated_results_text = "\n\n".join(results_summary)
            
            # ПОВТОРНЫЙ ВЫЗОВ AI для финального ответа БЕЗ инструментов
            messages.append({
                "role": "assistant", 
                "content": message.get('content', '') if message.get('content') else "Выполнил дополнительные действия"
            })
            
            # Добавляем результаты tool calls
            for i, tool_call in enumerate(tool_calls):
                if i < len(new_results):
                    result_content = json.dumps(
                        new_results[i]['result'] if new_results[i]['success'] 
                        else {'error': new_results[i]['error']},
                        ensure_ascii=False
                    )
                else:
                    result_content = json.dumps({'error': 'No result available'}, ensure_ascii=False)
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call['id'],
                    "content": result_content
                })
            
            # Финальный промпт с обновленными результатами
            final_system_prompt = f"{base_prompt}\n\nОБНОВЛЕННЫЕ РЕЗУЛЬТАТЫ ВСЕХ ДЕЙСТВИЙ:\n{updated_results_text}\n\n" + """\n---\n\nФИНАЛЬНЫЙ ОТВЕТ: Теперь у тебя есть ВСЕ результаты. Сформируй практически полезный ответ.

ОБЯЗАТЕЛЬНО:
- НАЧНИ С ГЛАВНОГО ВЫВОДА/ИНСАЙТА — что узнал, что это значит для пользователя
- Упомяни выполненные действия ВНУТРИ ответа, НЕ в первом предложении
- Дай 1 следующий шаг с датой/временем
- Если есть риск, альтернатива или нюанс — назови его
- НЕ начинай со слов "Отлично"/"Отличное"/"Отличный" (даже с эмодзи перед ними) — сразу давай суть

Верни ТОЛЬКО текст ответа пользователю."""
            
            final_messages = [
                {"role": "system", "content": final_system_prompt},
                {"role": "user", "content": user_message}
            ]
            
            final_response = await self.call_ai(final_messages, use_tools=False, temperature=0.7)
            content = final_response['choices'][0]['message']['content']
        else:
            # Просто текстовый ответ без дополнительных tool calls
            content = message.get('content', '')
        
        # КРИТИЧЕСКИ ВАЖНО: Очищаем от технических деталей и DSML тегов
        from .utils import clean_technical_details
        content = clean_technical_details(content)
        
        # Если после очистки ничего не осталось (AI вернул только JSON/код),
        # генерируем краткий fallback ответ
        if not content.strip():
            logger.warning("[AGENT] Response was empty after cleaning, generating fallback")
            content = "Готово, продолжаем!"
        
        return content.strip()

    def _extract_task_title(self, message):
        """Извлекает название задачи из сообщения"""
        if not message:
            return None
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
        if not message:
            return None
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

    async def process_request(self, user_message, user_id, context=None, session=None, subscription_tier=None, progress_callback=None):
        """
        Основной процесс обработки запроса:
        1. Планирование стратегии
        2. Выполнение действий
        3. Рефлексия и формирование ответа
        
        progress_callback — async func(text) для стриминга прогресса в чат пользователю.
        """
        # Сохраняем callback для доступа из вложенных методов
        self._progress_callback = progress_callback
        
        try:
            # Получаем информацию о пользователе для определения тарифа
            if subscription_tier is None:
                tier_session = Session()
                try:
                    user = tier_session.query(User).filter_by(telegram_id=user_id).first()
                    subscription_tier = getattr(user, 'subscription_tier', 'LIGHT') if user else 'LIGHT'
                    logger.info(f"[AGENT] User {user_id} has subscription tier: {subscription_tier}")
                finally:
                    tier_session.close()
            else:
                logger.info(f"[AGENT] Using provided subscription tier: {subscription_tier}")
            
            # Сохраняем сообщение пользователя в историю
            logger.info(f"[AGENT] About to save user message to history")
            from .conversation_history import save_message_to_history
            save_message_to_history(user_id, "user", user_message)
            logger.info(f"[AGENT] User message saved to history")
            
            # ШАГ 1: Планирование
            logger.info(f"[AGENT] Step 1: Planning strategy for '{user_message[:50]}...'")
            plan = await self.plan_strategy(user_message, user_id, context)
            
            actions = plan.get('actions', [])
            
            # ШАГ 2: Выполнение (execute_actions создаёт и закрывает свою сессию)
            execution_results = []
            if actions:
                logger.info(f"[AGENT] Step 2: Executing {len(actions)} actions")
                execution_results = await self.execute_actions(actions, user_id, session=None, user_message=user_message, progress_callback=progress_callback)
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
                    user_id,
                    subscription_tier
                )
            elif plan.get('ai_response'):
                # AI уже сформировал ответ на этапе планирования — используем его напрямую (экономим API-вызов)
                logger.info(f"[AGENT] Step 3: Using AI response from plan phase (no extra API call)")
                from .utils import clean_technical_details
                response = clean_technical_details(plan['ai_response']).strip()
            else:
                # Общее общение - используем AI
                logger.info(f"[AGENT] Step 3: AI generating natural response")
                response = await self.reflect_and_respond(
                    user_message, 
                    plan, 
                    execution_results, 
                    context,
                    user_id,
                    subscription_tier
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
            
            # Сохраняем ответ ассистента в историю
            logger.info(f"[AGENT] About to save assistant response to history")
            from .conversation_history import save_message_to_history
            save_message_to_history(user_id, "assistant", response)
            logger.info(f"[AGENT] Assistant response saved to history")
            
            # Сохраняем ключевую информацию в долгосрочную память (user.memory)
            # ТОЛЬКО значимые факты — НЕ CRUD-операции с задачами (задачи уже в БД)
            try:
                from .memory import update_user_memory
                memory_facts = []
                for r in execution_results:
                    if r.get('success') and r.get('tool') in (
                        'create_goal', 'update_goal_progress',
                        'set_content_strategy', 'set_contact_alert',
                        'research_topic', 'get_news_trends'
                    ):
                        # Для research/news — сохраняем краткую суть (что искал)
                        if r['tool'] in ('research_topic', 'get_news_trends'):
                            memory_facts.append(f"Искал: {r.get('reason', '')[:100]}")
                        else:
                            result_str = str(r.get('result', ''))[:150]
                            memory_facts.append(f"{r['tool']}: {result_str}")
                if memory_facts:
                    update_user_memory("\n".join(memory_facts), user_id=user_id)
                    logger.info(f"[AGENT] Saved {len(memory_facts)} meaningful facts to memory")
            except Exception as mem_err:
                logger.warning(f"[AGENT] Failed to save memory: {mem_err}")
            
            # Ограничиваем размер истории
            if len(self.execution_history) > 50:  # Больше истории для обучения
                self.execution_history = self.execution_history[-50:]
            
            return response
            
        except Exception as e:
            logger.error(f"[AGENT] Error processing request: {e}")
            logger.error(f"[AGENT] Error type: {type(e).__name__}")
            logger.error(f"[AGENT] User message: {user_message}")
            logger.error(f"[AGENT] User ID: {user_id}")
            import traceback
            logger.error(f"[AGENT] Full traceback:\n{traceback.format_exc()}")
            
            # Более естественные ответы при ошибках
            error_responses = [
                "Что-то пошло не так. Давай попробуем по-другому - перефразируй свой запрос.",
                "Извини, возникла техническая проблема. Можешь повторить по-другому?",
                "Упс, ошибка в системе. Попробуй сказать то же самое другими словами.",
                "Технические неполадки. Давай попробуем еще раз с другим формулировкой.",
                "Что-то сломалось. Перефразируй запрос, пожалуйста."
            ]
            
            import random
            return random.choice(error_responses)


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


# Глобальный экземпляр агента
_autonomous_agent = None

def get_autonomous_agent():
    """Получить экземпляр гибридного автономного агента"""
    global _autonomous_agent
    if _autonomous_agent is None:
        _autonomous_agent = HybridAutonomousAgent()
    return _autonomous_agent

async def chat_with_ai(message, context=None, user_id=None, file_content=None, db_session=None, message_type=None, subscription_tier=None, progress_callback=None):
    """Функция чата с использованием улучшенного гибридного автономного агента.
    
    progress_callback — async func(text) для стриминга прогресса в чат.
    """

    logger.info(f"[HYBRID_AGENT] START - user_id={user_id}, message='{str(message)[:50]}...'")

    if user_id is None:
        logger.error("[HYBRID_AGENT] ERROR: user_id is None!")
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        # Получаем гибридного автономного агента
        agent = get_autonomous_agent()

        # Запоминаем длину истории ДО вызова (чтобы не читать устаревшие записи)
        history_len_before = len(agent.execution_history)

        # Обрабатываем запрос через улучшенного агента
        response_text = await agent.process_request(message, user_id, context, db_session, subscription_tier, progress_callback=progress_callback)

        # Возвращаем в формате, ожидаемом остальным кодом
        # Для тестирования возвращаем tool_calls из execution_results
        tool_calls = []
        tools_used = []  # For test tracking
        try:
            # Извлекаем информацию о вызванных инструментах из execution_history
            # ТОЛЬКО если была добавлена НОВАЯ запись в этом ходе
            if len(agent.execution_history) > history_len_before:
                last_execution = agent.execution_history[-1]
                # Берём results вместо plan - там реальные вызовы
                if last_execution.get('results'):
                    for result in last_execution['results']:
                        tool_name = result.get('tool', '')
                        if tool_name and result.get('success'):  # Только успешные
                            tools_used.append(tool_name)
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
            'tool_calls': tool_calls,
            'tools_used': tools_used  # Add for test tracking
        }

    except Exception as e:
        logger.error(f"[HYBRID_AGENT] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {
            'response': f"Извините, произошла ошибка: {str(e)}",
            'tool_calls': []
        }