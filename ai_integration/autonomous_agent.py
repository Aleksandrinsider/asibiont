"""
Adaptive Autonomous Agent — стандартный tool calling loop
с адаптивной логикой из лучших итераций.

Архитектура:
1. Собираем контекст (1 запрос к БД)
2. Tool calling loop (max 5 итераций)
3. Обучение на успехах + адаптация

Умные фичи из 73dc138:
- force_tool_choice для явных запросов (новости, задачи, партнёры)
- success_patterns — обучение на успешных паттернах
- user_preferences — адаптация под пользователя
- context_memory — краткосрочная контекстная память
- auto-trigger awareness (check_time_conflicts → add_task)
- parameter auto-fix для известных tool quirks
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
import logging
import random
import re
import inspect
import traceback
import pytz
from datetime import datetime, timezone

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, Task, UserProfile, Goal
from .prompts import get_extended_system_prompt
from .dynamic_tools import tool_discovery
from .tools import get_available_tools
from .vector_memory import store_conversation_turn, build_memory_context, search_memory
from .multi_agent import get_orchestrator
from .self_learning import get_learner

logger = logging.getLogger(__name__)


# ===== KEYWORDS для force_tool_choice =====
TOOL_REQUIRED_KEYWORDS = {
    'news': ['что нового', 'что посоветуешь', 'расскажи новости', 'новости',
             'что происходит', 'тренды'],
    'tasks': ['задачи', 'что по задачам', 'мои задачи', 'список задач',
              'покажи задачи', 'что делать'],
    'contacts': ['партнеры', 'найти людей', 'единомышленники', 'контакты',
                 'кто может помочь'],
    'research': ['исследуй', 'найди информацию', 'что известно о',
                 'разберись в', 'проанализируй'],
    'complete': ['сделал', 'готово', 'выполнил', 'завершил', 'закончил',
                 'сделала', 'выполнила', 'завершила', 'закрыть задачу'],
    'delete': ['удали задачу', 'убери задачу', 'удалить задачу', 'сотри задачу',
               'отмени задачу', 'удали ', 'убери '],
    'edit': ['перенеси', 'переназначь', 'измени задачу', 'обнови задачу',
             'перенести задачу'],
    'profile': ['я работаю', 'я маркетолог', 'я разработчик', 'я дизайнер',
                'живу в', 'увлекаюсь', 'я фронтендер', 'мой навык'],
}

# Слова, означающие что пользователь ЯВНО просит создать задачу
# Без этих слов add_task блокируется — чтобы агент не плодил задачи из вопросов
TASK_CREATION_SIGNALS = [
    'создай', 'добавь', 'запланируй', 'напомни', 'поставь задачу',
    'создать', 'добавить', 'запланировать', 'напомнить',
    'задачу на', 'задачу к', 'новую задачу', 'ещё задачу', 'еще задачу',
]

# Сигналы завершения задачи — чтобы не блокировать complete_task
TASK_COMPLETION_SIGNALS = [
    'сделал', 'готово', 'выполнил', 'завершил', 'закончил',
    'сделала', 'выполнила', 'завершила', 'закрыть',
]

# Сигналы удаления
TASK_DELETION_SIGNALS = [
    'удали', 'убери', 'сотри', 'отмени задачу', 'удалить',
]


class HybridAutonomousAgent:
    """
    Адаптивный агент: standard tool calling loop + обучение + force_tool_choice.
    Без мульти-агентного pipeline, без дублированного контекста.
    """

    def __init__(self):
        self.execution_history = []
        self.tool_discovery = tool_discovery
        self._initialize_tools()
        self.active_sessions = 0

        # === Адаптивные фичи (из 73dc138) ===
        self.context_memory = []          # Краткосрочная память контекста
        self.success_patterns = {}        # Паттерны успешных действий
        self.user_preferences = {}        # Предпочтения пользователей
        self._progress_callback = None

        # Загружаем статистику tool discovery
        self.tool_discovery.load_stats()

    def _initialize_tools(self):
        """Инициализирует динамическую систему инструментов."""
        try:
            from . import handlers
            self.tool_discovery.discover_tools_from_module(handlers)
            logger.info(f"[AGENT] Initialized {len(self.tool_discovery.discovered_tools)} dynamic tools")
        except Exception as e:
            logger.error(f"[AGENT] Failed to initialize tools: {e}")

    # ===== AI API =====

    async def call_ai(self, messages, use_tools=False, subscription_tier=None,
                      tool_choice=None, exclude_tools=None, model=None, **kwargs):
        """Универсальный вызов DeepSeek API.
        
        Args:
            model: Модель для вызова. По умолчанию DEEPSEEK_MODEL.
        """
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        chosen_model = model or DEEPSEEK_MODEL

        data = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": kwargs.pop("max_tokens", 1800),
            "temperature": kwargs.pop("temperature", 0.7),
            **kwargs
        }

        if use_tools:
            available_tools = get_available_tools(subscription_tier)
            if exclude_tools:
                available_tools = [t for t in available_tools
                                   if t['function']['name'] not in exclude_tools]
            data["tools"] = available_tools
            data["tool_choice"] = tool_choice or "auto"
            logger.info(f"[AI] {len(available_tools)} tools, tier={subscription_tier}, "
                        f"tool_choice={data['tool_choice']}")

        logger.info(f"[AI] Calling model={chosen_model}, tokens={data.get('max_tokens')}")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    # Логируем результат
                    if use_tools:
                        msg = result.get('choices', [{}])[0].get('message', {})
                        tcs = msg.get('tool_calls', [])
                        if tcs:
                            logger.info(f"[AI] Called {len(tcs)} tools: "
                                        f"{[tc['function']['name'] for tc in tcs]}")
                        else:
                            logger.info(f"[AI] No tools called, text response")
                    return result
                error = await resp.text()
                raise Exception(f"AI call failed: {resp.status} {error}")

    # ===== ADAPTIVE TOOL CHOICE =====

    # Тривиальные сообщения — tool_choice=auto (не заставляем)
    TRIVIAL_MESSAGES = [
        'ок', 'окей', 'ладно', 'хорошо', 'да', 'нет',
        'ага', 'угу', 'понял', 'ясно', 'спасибо',
        'спс', 'благодарю', 'пока', 'до свидания', 'кек', 'лол',
    ]

    def _determine_tool_choice(self, user_message, profile_data=None, tasks_data=None):
        """Определяет tool_choice через multi-signal scoring.
        
        Суммирует баллы из нескольких независимых сигналов:
        - Явные keyword-запросы данных (+3.0)
        - Intent: greeting/task_management/info_request (+2.0)
        - Наличие профиля с данными (+1.5)
        - Наличие задач (+1.0)
        - Длина сообщения > 5 слов (+0.5)
        - Тривиальные/прощальные сообщения (-3.0)
        
        required при score >= 2.0, иначе auto.
        """
        msg_lower = user_message.lower().strip()
        words = msg_lower.split()
        score = 0.0
        signals = []

        # 0. Тривиальные — быстрый выход
        if msg_lower in self.TRIVIAL_MESSAGES or len(msg_lower) <= 3:
            logger.info(f"[TOOL_CHOICE] Score=trivial → auto for: '{user_message[:40]}'")
            return "auto"

        # 1. Явные keyword-запросы данных (+3.0 за категорию)
        for category, keywords in TOOL_REQUIRED_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                score += 3.0
                signals.append(f'keyword({category}):+3')
                break  # одной категории достаточно

        # 2. Intent из CognitiveEngine (+2.0 для greeting/task/info/advice)
        from .cognitive import CognitiveEngine
        intent = CognitiveEngine.classify_intent(user_message)
        intent_tool_weights = {
            'greeting': 2.0,       # первое впечатление — ВСЕГДА с инструментами
            'task_management': 2.0,
            'information_request': 2.0,
            'advice_seeking': 1.5,
            'emotional_sharing': 1.0,
            'general': 0.5,
            'farewell': -1.0,      # прощание — не нужны инструменты
        }
        iw = intent_tool_weights.get(intent, 0.0)
        if iw:
            score += iw
            signals.append(f'intent({intent}):{iw:+.1f}')

        # 3. Профиль: любые данные = контекст для проактивности (+1.5)
        if profile_data:
            has_any = any(profile_data.get(f) for f in 
                         ['city', 'interests', 'goals', 'skills', 'position', 'company'])
            if has_any:
                score += 1.5
                signals.append('profile:+1.5')

        # 4. Есть задачи — можно проверить статус (+1.0)
        if tasks_data:
            score += 1.0
            signals.append('tasks:+1.0')

        # 5. Длина сообщения > 5 слов — вероятно содержательный запрос (+0.5)
        if len(words) > 5:
            score += 0.5
            signals.append('len>5:+0.5')

        # 6. Прощальные маркеры — не надо инструменты (-2.0)
        farewell_words = ['пока', 'до свидания', 'спокойной', 'до завтра']
        if any(fw in msg_lower for fw in farewell_words):
            score -= 2.0
            signals.append('farewell:-2.0')

        THRESHOLD = 2.0
        result = "required" if score >= THRESHOLD else "auto"
        logger.info(f"[TOOL_CHOICE] Score={score:.1f} (threshold={THRESHOLD}) "
                    f"signals=[{', '.join(signals)}] → {result} "
                    f"for: '{user_message[:50]}'")
        return result

    _TOOL_PROGRESS_MAP = {
        'get_tasks': 'Смотрю задачи ...',
        'add_task': 'Создаю задачу ...',
        'complete_task': 'Завершаю задачу ...',
        'edit_task': 'Обновляю задачу ...',
        'delete_task': 'Удаляю задачу ...',
        'reschedule_task': 'Переношу задачу ...',
        'quick_topic_search': 'Ищу информацию ...',
        'research_topic': 'Исследую тему ...',
        'get_news_trends': 'Ищу новости ...',
        'find_relevant_contacts_for_task': 'Ищу контакты ...',
        'get_stock_price': 'Проверяю котировки ...',
        'get_weather': 'Смотрю погоду ...',
        'update_profile': 'Обновляю профиль ...',
        'get_user_goals': 'Проверяю цели ...',
        'create_goal': 'Создаю цель ...',
        'generate_post': 'Пишу пост ...',
        'create_post': 'Публикую пост ...',
        'edit_post': 'Редактирую пост ...',
        'get_posts': 'Смотрю посты ...',
        'delete_post': 'Удаляю пост ...',
    }

    def _tool_progress_text(self, tool_name, iteration):
        """Генерирует текст прогресса для Telegram по имени инструмента."""
        text = self._TOOL_PROGRESS_MAP.get(tool_name, 'Работаю ...')
        if iteration > 1:
            text = 'Углубляюсь ...'
        return text

    # ===== TOKEN BUDGET =====

    # Максимальный бюджет в символах (~12000 токенов для рус. текста, ratio ~3 chars/token)
    MAX_PROMPT_CHARS = 36000  # ~12000 tokens
    MAX_HISTORY_CHARS = 12000  # ~4000 tokens для истории

    @staticmethod
    def _estimate_tokens(text):
        """Грубая оценка кол-ва токенов для русского текста (~3 chars/token)."""
        return len(text) // 3 if text else 0

    def _trim_prompt_to_budget(self, base_prompt, history):
        """Обрезает системный промпт и историю до бюджета токенов.
        
        Приоритет сохранения (от высшего к низшему):
        1. Базовый системный промпт (ядро — неприкосновенно)
        2. Последние 4 сообщения истории
        3. Когнитивные подсказки
        4. Мультиагентный контекст
        5. Самообучение / preferences
        6. Старые сообщения истории
        7. Ранее обсуждали / memory
        
        Returns:
            (trimmed_prompt: str, trimmed_history: list)
        """
        prompt_chars = len(base_prompt)
        history_chars = sum(len(m.get('content', '')) for m in history)
        total = prompt_chars + history_chars
        
        if total <= self.MAX_PROMPT_CHARS:
            return base_prompt, history  # Всё влезает
        
        overflow = total - self.MAX_PROMPT_CHARS
        trimmed = 0
        logger.info(f"[TOKEN_BUDGET] Over budget by ~{overflow // 3} tokens "
                    f"({prompt_chars} prompt + {history_chars} history chars)")
        
        # 1. Обрезаем историю — оставляем последние 4 сообщения
        if len(history) > 4 and history_chars > self.MAX_HISTORY_CHARS:
            old_len = len(history)
            # Сжимаем старые сообщения: оставляем последние 4
            keep = history[-4:]
            removed_chars = sum(len(m.get('content', '')) for m in history[:-4])
            history = keep
            trimmed += removed_chars
            logger.info(f"[TOKEN_BUDGET] Trimmed history: {old_len} → {len(history)} msgs, "
                       f"freed ~{removed_chars // 3} tokens")
        
        if trimmed >= overflow:
            return base_prompt, history
        
        # 2. Обрезаем секции промпта по приоритету (от наименее важных)
        sections_to_trim = [
            '[РАНЕЕ ОБСУЖДАЛИ:',
            '[ЭМОЦИОНАЛЬНЫЙ ТРЕНД',
            '[ПРОАКТИВНОЕ ДЕЙСТВИЕ',
            '[ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ',
            '[MULTI-AGENT',
            '[ГЛУБОКИЙ АНАЛИЗ R1]',
        ]
        
        for marker in sections_to_trim:
            if trimmed >= overflow:
                break
            idx = base_prompt.find(marker)
            if idx == -1:
                continue
            # Ищем конец секции (следующая секция или конец строки)
            next_section = len(base_prompt)
            for other in ['[РАНЕЕ', '[ЭМОЦ', '[ПРОАК', '[ПРЕД', '[MULTI', '[ГЛУБ',
                          '[СТРАТЕГИЯ', '[КОГНИТИВНЫЕ', '\n\n[']:
                pos = base_prompt.find(other, idx + len(marker))
                if pos != -1 and pos < next_section:
                    next_section = pos
            
            removed = base_prompt[idx:next_section]
            base_prompt = base_prompt[:idx] + base_prompt[next_section:]
            trimmed += len(removed)
            logger.info(f"[TOKEN_BUDGET] Trimmed section '{marker[:20]}', "
                       f"freed ~{len(removed) // 3} tokens")
        
        return base_prompt, history

    # ===== КОНТЕКСТ =====

    # Кэш контекста погоды/новостей: {user_id: {'weather': ..., 'news': ..., 'expires': float}}
    _weather_news_cache = {}
    _WEATHER_NEWS_TTL = 900  # 15 мин — не перезапрашиваем API на каждое сообщение

    async def _get_weather_news_cached(self, city):
        """Получить погоду/новости через async api_client с per-user TTL кэшем.
        Избегает блокировки event loop (в отличие от старых sync utils).
        """
        import time as _time
        cache_key = city.lower().strip() if city else "__no_city__"
        cached = self._weather_news_cache.get(cache_key)
        if cached and cached['expires'] > _time.time():
            logger.debug(f"[CTX_CACHE] Using cached weather/news for {city}")
            return cached['weather'], cached['news']

        weather_info = None
        news_info = None
        try:
            from .api_client import get_api_client
            api = get_api_client()
            weather_data = await api.get_weather(city, cache_ttl=1800) if city else None
            if weather_data:
                weather_info = (
                    f"{weather_data['city_name']}: {weather_data['temp']:.0f}°C, "
                    f"{weather_data['description']}, влажность {weather_data['humidity']}%, "
                    f"ветер {weather_data['wind_speed']} м/с"
                )
            news_articles = await api.get_news(topic=city, page_size=3, cache_ttl=900) if city else None
            if news_articles:
                titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                if titles:
                    news_info = f"Новости {city}:\n" + "\n".join(titles)
        except Exception as e:
            logger.warning(f"[CTX_CACHE] Failed to load weather/news via api_client: {e}")

        self._weather_news_cache[cache_key] = {
            'weather': weather_info,
            'news': news_info,
            'expires': _time.time() + self._WEATHER_NEWS_TTL,
        }
        return weather_info, news_info

    async def _build_context(self, user_id):
        """Собирает весь контекст пользователя за 1 сессию БД.
        Async: погода/новости загружаются через api_client (не блокируют event loop).
        Returns: dict с полями для промпта + метаданные.
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return None

            # Время
            base_now = datetime.now(pytz.UTC)
            tz_name = user.timezone or 'Europe/Moscow'
            months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                      'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
            try:
                user_tz = pytz.timezone(tz_name)
                user_now = base_now.astimezone(user_tz)
            except Exception:
                user_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(user_tz)
                tz_name = 'Europe/Moscow'

            hour = user_now.hour
            if 6 <= hour < 12: tod = "утро"
            elif 12 <= hour < 18: tod = "день"
            elif 18 <= hour < 23: tod = "вечер"
            else: tod = "ночь"

            time_str = f"{user_now.strftime('%H:%M')} ({tod}, {tz_name})"
            date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

            # Профиль
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_data = {}
            weather_info = news_info = None
            if profile:
                for field in ('city', 'company', 'position', 'goals', 'skills',
                              'interests', 'birthdate'):
                    val = getattr(profile, field, None)
                    if val:
                        profile_data[field] = val
                if profile.city:
                    # Async weather/news через api_client (не блокирует event loop)
                    weather_info, news_info = await self._get_weather_news_cached(profile.city)
            if user.telegram_channel:
                profile_data['telegram_channel'] = user.telegram_channel

            # Задачи пользователя (для CognitiveEngine strategy)
            tasks_data = []
            try:
                user_tasks = session.query(Task).filter_by(
                    user_id=user.id
                ).filter(
                    Task.status.in_(['pending', 'in_progress'])
                ).order_by(Task.due_date.asc().nullslast()).limit(20).all()
                for t in user_tasks:
                    task_info = {'id': t.id, 'title': t.title, 'status': t.status}
                    if t.due_date:
                        task_info['deadline'] = t.due_date.isoformat()
                    tasks_data.append(task_info)
            except Exception as e:
                logger.warning(f"[CTX] Failed to load tasks: {e}")

            # Память
            decrypted_memory = ""
            if user.memory:
                try:
                    from .memory import decrypt_data
                    decrypted_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.debug(f"Failed to decrypt user memory: {e}")

            # Текущая задача
            current_task_info = None
            if user.current_task_id:
                task = session.query(Task).filter_by(id=user.current_task_id).first()
                if task:
                    current_task_info = {'id': task.id, 'title': task.title,
                                         'status': task.status}

            # Проактивный контекст
            from .context_builder import ContextBuilder
            ctx = ContextBuilder()
            proactive_context = ctx.build_proactive_context(user_id, session)

            # Подписка
            sub_tier = getattr(user, 'subscription_tier', 'LIGHT')

            # Базовый промпт
            base_prompt = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=time_str,
                current_date_str=date_str,
                user_username=user.username or "пользователь",
                mentions_str="",
                user_memory=decrypted_memory,
                context=None, intent=None,
                subscription_tier=sub_tier,
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                proactive_context=proactive_context,
                current_task_info=current_task_info,
                user_id_param=user_id
            )

            return {
                'base_prompt': base_prompt,
                'sub_tier': sub_tier,
                'profile_data': profile_data,
                'tasks': tasks_data,
                'user_now': user_now,
                'time_str': time_str,
                'date_str': date_str,
            }
        finally:
            session.close()

    # ===== EXECUTE =====

    async def execute_actions(self, actions, user_id, session=None,
                              user_message=None, progress_callback=None):
        """Выполняет tool calls через handlers.
        
        Включает:
        - parameter auto-fix для известных tool quirks
        - session management с лимитами
        - tool discovery learning
        """
        from . import handlers

        close_session = False
        if session is None:
            if self.active_sessions >= 5:
                return [{"tool": "limit", "success": False,
                         "error": "Слишком много запросов. Попробуй через минуту."}]
            session = Session()
            close_session = True
            self.active_sessions += 1

        results = []
        try:
            for action in actions:
                tool_name = action.get('tool')
                params = dict(action.get('params', {}))
                reason = action.get('reason', '')

                handler_func = getattr(handlers, tool_name, None)
                if not handler_func:
                    results.append({"tool": tool_name, "success": False,
                                    "error": f"Handler {tool_name} not found"})
                    continue

                params['user_id'] = user_id
                sig = inspect.signature(handler_func)
                if 'session' in sig.parameters:
                    params['session'] = session

                # === Parameter auto-fix для известных quirks ===
                params = self._fix_tool_params(tool_name, params, user_message)

                try:
                    # Списываем токены за инструмент (если стоимость > 0)
                    from token_service import spend_tokens, ACTION_COSTS, DEFAULT_TOOL_COST
                    from config import FREE_ACCESS_MODE
                    tool_cost = ACTION_COSTS.get(tool_name, DEFAULT_TOOL_COST)
                    if not FREE_ACCESS_MODE and tool_cost > 0:
                        token_result = spend_tokens(user_id, tool_name, description=reason)
                        if not token_result['success']:
                            results.append({"tool": tool_name, "success": False,
                                            "error": token_result['error'], "reason": reason})
                            logger.info(f"[EXEC] {tool_name} — недостаточно токенов")
                            continue

                    # Логируем параметры ДО вызова
                    safe_params = {k: v for k, v in params.items() if k != 'session'}
                    logger.info(f"[EXEC] {tool_name} CALL params={safe_params}")

                    if asyncio.iscoroutinefunction(handler_func):
                        result = await handler_func(**params)
                    else:
                        result = handler_func(**params)

                    self.tool_discovery.learn_from_success(
                        func_name=tool_name, user_id=user_id,
                        context=reason, result=result)

                    results.append({"tool": tool_name, "success": True,
                                    "result": result, "reason": reason})
                    
                    logger.info(f"[EXEC] {tool_name} ✓ result={str(result)[:200]} — {reason}")

                except Exception as e:
                    logger.error(f"[EXEC] {tool_name} ✗ — {e}")
                    self.tool_discovery.learn_from_failure(
                        func_name=tool_name, error=str(e))
                    results.append({"tool": tool_name, "success": False,
                                    "error": str(e), "reason": reason})
        finally:
            if close_session:
                try:
                    session.close()
                except Exception as e:
                    logger.debug(f"Session close error: {e}")
                self.active_sessions = max(0, self.active_sessions - 1)

        return results

    def _fix_tool_params(self, tool_name, params, user_message=None):
        """Фиксит известные проблемы с параметрами tools.
        
        AI иногда передаёт неправильные имена параметров —
        эта функция исправляет самые частые ошибки.
        """
        if tool_name == 'find_relevant_contacts_for_task':
            if 'description' in params and 'task_description' not in params:
                params['task_description'] = params.pop('description')
            elif 'task_description' not in params:
                params['task_description'] = 'помощь с задачей'

        elif tool_name == 'quick_topic_search' and not params.get('topic'):
            if user_message:
                stop = {'что', 'как', 'где', 'когда', 'почему', 'а', 'и', 'но'}
                words = [w for w in re.findall(r'\b\w+\b', user_message.lower())
                         if w not in stop and len(w) > 2][:3]
                params['topic'] = ' '.join(words) if words else user_message[:50]
            else:
                params['topic'] = 'общая информация'

        elif tool_name == 'research_topic':
            if 'topic' in params and 'query' not in params:
                params['query'] = params.pop('topic')
            elif 'query' not in params:
                params['query'] = user_message[:200] if user_message else 'исследование'

        elif tool_name == 'update_profile' and user_message:
            # Универсальный fallback: если DeepSeek вызвал update_profile без данных,
            # извлекаем факты из сообщения пользователя по разным формулировкам.
            profile_fields = ['city', 'skills', 'interests', 'goals', 'company', 'position', 'birth_date']
            has_any = any(params.get(f) for f in profile_fields)
            if not has_any:
                msg = user_message
                logger.info(f"[FIX_PARAMS] update_profile empty params — extracting from message")
                import re as _re
                
                # === ГОРОД ===
                # «живу в Москве», «я из Питера», «город Казань», «переехал в Казань»,
                # «нахожусь в Перми», «в городе Тула», «город: Казань»
                city_patterns = [
                    r'(?:живу|нахожусь|обитаю|базируюсь|переехал[а]?)\s+в\s+([А-ЯЁ][а-яё\-]+(?:[\-\s][А-ЯЁ][а-яё]+)?)',
                    r'(?:я\s+из|приехал[а]?\s+из|родом\s+из)\s+([А-ЯЁ][а-яё\-]+)',
                    r'город[уе]?[:\s]+([А-ЯЁ][а-яё\-]+)',
                    r'в\s+городе\s+([А-ЯЁ][а-яё\-]+)',
                ]
                for pat in city_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        city_raw = m.group(1).strip()
                        # Нормализация: «Питере» → «Санкт-Петербург», «Питера» → «Санкт-Петербург» 
                        if _re.match(r'питер', city_raw, _re.IGNORECASE):
                            city_raw = 'Санкт-Петербург'
                        elif _re.match(r'мск|москв', city_raw, _re.IGNORECASE):
                            city_raw = 'Москва'
                        elif _re.match(r'спб|петербург', city_raw, _re.IGNORECASE):
                            city_raw = 'Санкт-Петербург'
                        elif _re.match(r'нск|новосиб', city_raw, _re.IGNORECASE):
                            city_raw = 'Новосибирск'
                        elif _re.match(r'екб|екат', city_raw, _re.IGNORECASE):
                            city_raw = 'Екатеринбург'
                        # Убираем падежное окончание: Казани → Казань, Перми → Пермь
                        city_raw = _re.sub(r'[иеуюя]$', '', city_raw)
                        if len(city_raw) >= 2:
                            # Первая буква заглавная
                            city_raw = city_raw[0].upper() + city_raw[1:]
                            params['city'] = city_raw
                        break
                
                # === НАВЫКИ ===
                # «навыки: Python, React», «умею Python и FastAPI», «знаю React»,
                # «владею Python», «разбираюсь в ML», «специализируюсь на backend»,
                # «занимаюсь разработкой», «мои скиллы: Python, Go»
                skills_patterns = [
                    r'навыки?[:\s]+([^.!?]+)',
                    r'скилл[ыа]?[:\s]+([^.!?]+)',
                    r'(?:умею|знаю|владею|освоил[а]?)\s+([^.!?]+)',
                    r'(?:разбираюсь|специализируюсь)\s+(?:в|на)\s+([^.!?]+)',
                ]
                for pat in skills_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        if len(val) > 1:
                            params['skills'] = val
                        break
                
                # === ИНТЕРЕСЫ ===
                # «интересуюсь ML», «увлекаюсь спортом», «люблю музыку»,
                # «интересы: ML, робототехника», «хобби: шахматы»,
                # «мне интересно AI», «нравится программирование»
                interests_patterns = [
                    r'интересы?[:\s]+([^.!?]+)',
                    r'хобби[:\s]+([^.!?]+)',
                    r'увлечени[яе][:\s]+([^.!?]+)',
                    r'(?:интересуюсь|увлекаюсь|люблю|нравится|обожаю)\s+([^.!?]+)',
                    r'мне\s+интересн[оа]\s+([^.!?]+)',
                ]
                for pat in interests_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        if len(val) > 1:
                            params['interests'] = val
                        break
                
                # === ЦЕЛИ ===
                # «моя цель — запустить MVP», «хочу выйти на 100 клиентов»,
                # «планирую переехать», «стремлюсь к 1 млн выручки»,
                # «цели: запустить MVP, найти инвестора»
                goals_patterns = [
                    r'цел[иья][:\s—–-]+([^.!?]+)',
                    r'(?:хочу|планирую|стремлюсь|мечтаю|собираюсь|намерен[а]?)\s+([^.!?]+)',
                ]
                for pat in goals_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        if len(val) > 2:
                            params['goals'] = val
                        break
                
                # === ДОЛЖНОСТЬ ===
                # «я разработчик», «работаю программистом», «должность: CTO»,
                # «я тимлид», «по профессии дизайнер»
                position_patterns = [
                    r'(?:должность|позиция|роль)[:\s]+([^,.!?]+)',
                    r'(?:работаю|тружусь)\s+([а-яёА-ЯЁa-zA-Z\-]+(?:ом|ем|ёром|ером|стом|ком|чиком))',
                    r'по\s+професси[ию]\s+([^,.!?]+)',
                    r'я\s+((?:разработчик|программист|дизайнер|менеджер|директор|инженер|аналитик|тимлид|CTO|CEO|COO|CFO|фрилансер|предприниматель|маркетолог|продюсер|консультант)[а-яё]*)',
                ]
                for pat in position_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip()
                        if len(val) > 1:
                            params['position'] = val
                        break
                
                # === КОМПАНИЯ ===
                # «работаю в Яндексе», «компания: Google», «я из ASI Biont»,
                # «сотрудник Сбера», «основатель AI Startup»
                company_patterns = [
                    r'(?:компани[яию]|фирм[ауе]|организаци[яию])[:\s]+([^,.!?]+)',
                    r'работаю\s+в\s+(?:компании\s+)?([A-ZА-ЯЁ][^,.!?]{1,30})',
                    r'(?:сотрудник|основатель|со-?основатель|партнёр)\s+(?:компании\s+)?([A-ZА-ЯЁ][^,.!?]{1,30})',
                ]
                for pat in company_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip()
                        if len(val) > 1:
                            params['company'] = val
                        break
                
                extracted = {k: v for k, v in params.items() if k not in ('user_id', 'session')}
                logger.info(f"[FIX_PARAMS] Extracted: {extracted}")

        return params

    # ===== ОСНОВНОЙ FLOW =====

    async def process_request(self, user_message, user_id, context=None,
                              session=None, subscription_tier=None,
                              progress_callback=None):
        """
        Адаптивный tool calling loop:
        1. Собираем контекст (1 запрос к БД)
        2. Определяем tool_choice (auto/required)
        3. Tool calling loop (max 5 итераций)
        4. Обучение + сохранение
        """
        self._progress_callback = progress_callback

        try:
            # Тариф
            if subscription_tier is None:
                s = Session()
                try:
                    u = s.query(User).filter_by(telegram_id=user_id).first()
                    subscription_tier = getattr(u, 'subscription_tier', 'LIGHT') if u else 'LIGHT'
                finally:
                    s.close()

            # Сохраняем сообщение пользователя в историю
            from .conversation_history import save_message_to_history
            save_message_to_history(user_id, "user", user_message)

            # Контекст (async — погода/новости через api_client)
            ctx = await self._build_context(user_id)
            if not ctx:
                return "Не удалось загрузить профиль. Попробуй ещё раз."

            base_prompt = ctx['base_prompt']
            sub_tier = ctx['sub_tier']

            # ═══ ИСТОРИЯ ДИАЛОГА (загружаем рано — нужна для anti-repetition) ═══
            from .conversation_history import get_conversation_history
            full_history = get_conversation_history(user_id, session=None, limit=16)

            # ═══ КОГНИТИВНОЕ ОБОГАЩЕНИЕ ═══
            from .cognitive import CognitiveEngine
            profile_data = ctx.get('profile_data', {})
            cognitive_hints = CognitiveEngine.build_cognitive_hints(
                user_message, profile_data=profile_data,
                conversation_history=full_history
            )
            
            # Оценка ситуации — контекст для самостоятельного рассуждения AI
            tasks_data = ctx.get('tasks', [])
            strategy = CognitiveEngine.plan_response_strategy(user_message, profile_data, tasks_data)
            if strategy:
                cognitive_hints += f"\n\n[СИТУАЦИЯ]\n{strategy['why']}\nТон: {strategy['tone']}"
            
            if cognitive_hints:
                base_prompt += cognitive_hints

            # ═══ МУЛЬТИАГЕНТНЫЙ АНАЛИЗ ═══
            try:
                emotion = CognitiveEngine.detect_emotion(user_message)
                intent = CognitiveEngine.classify_intent(user_message)
                
                # Семантическая память из Pinecone
                memory_context = ""
                try:
                    memory_context = await build_memory_context(user_id, user_message, max_chars=600)
                    if memory_context:
                        base_prompt += memory_context
                except Exception as e:
                    logger.warning(f"[VECTOR] Memory search failed: {e}")
                
                orchestrator = get_orchestrator()
                user_now = ctx.get('user_now')
                time_of_day = "день"
                if user_now:
                    h = user_now.hour
                    if 6 <= h < 12: time_of_day = "утро"
                    elif 12 <= h < 18: time_of_day = "день"
                    elif 18 <= h < 23: time_of_day = "вечер"
                    else: time_of_day = "ночь"
                
                multi_context = orchestrator.build_multi_agent_context(
                    user_message=user_message,
                    profile_data=profile_data,
                    tasks_data=tasks_data,
                    memory_context=memory_context,
                    emotion=emotion,
                    intent=intent,
                    time_of_day=time_of_day
                )
                if multi_context:
                    base_prompt += multi_context
            except Exception as e:
                logger.warning(f"[MULTI-AGENT] Context build failed: {e}")
            
            # ═══ САМООБУЧЕНИЕ — ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ ═══
            try:
                learner = get_learner()
                user_prefs = learner.get_user_preferences(user_id)
                if user_prefs:
                    base_prompt += user_prefs
                
                emotional_trend = learner.get_emotional_trend(user_id)
                if emotional_trend:
                    base_prompt += f"\n{emotional_trend}"
                
                proactive_hint = learner.suggest_proactive_action(user_id, profile_data)
                if proactive_hint:
                    base_prompt += f"\n{proactive_hint}"
            except Exception as e:
                logger.warning(f"[SELF-LEARN] Preferences failed: {e}")

            if len(full_history) > 10:
                old_msgs = full_history[:-8]
                history = full_history[-8:]
                topics = CognitiveEngine.extract_conversation_topics(old_msgs)
                if topics:
                    base_prompt += f"\n\n[РАНЕЕ ОБСУЖДАЛИ: {', '.join(topics)}]"
            else:
                history = full_history

            # ═══ TOKEN BUDGET — обрезаем если превышен лимит ═══
            base_prompt, history = self._trim_prompt_to_budget(base_prompt, history)

            messages = [{"role": "system", "content": base_prompt}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Адаптивный tool_choice (с учётом профиля и задач)
            initial_tool_choice = self._determine_tool_choice(
                user_message, profile_data=profile_data, tasks_data=tasks_data
            )

            # ===== Tool calling loop (max 4 итераций) =====
            all_execution_results = []
            MAX_ITERATIONS = 4
            seen_tools = set()  # Для предотвращения дублей

            # Прогресс в Telegram — "Думаю ..."
            if self._progress_callback:
                try:
                    await self._progress_callback('Думаю ...')
                except Exception:
                    pass

            for iteration in range(MAX_ITERATIONS):
                # Первая итерация может быть "required", остальные "auto"
                tc = initial_tool_choice if iteration == 0 else "auto"

                response = await self.call_ai(
                    messages, use_tools=True, subscription_tier=sub_tier,
                    tool_choice=tc)

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → когнитивная валидация → готово
                    return self._finalize_response(
                        content, user_message, user_id, all_execution_results)

                # AI вызвал tools → добавляем assistant message в цепочку
                messages.append(msg)

                for tc_item in tool_calls:
                    func = tc_item.get('function', {})
                    name = func.get('name', '')
                    try:
                        args = json.loads(func.get('arguments', '{}'))
                    except Exception:
                        args = {}

                    # Dedup: предотвращаем повторные вызовы того же tool с теми же параметрами
                    dedup_key = f"{name}:{json.dumps(args, sort_keys=True)}"
                    if dedup_key in seen_tools:
                        logger.warning(f"[DEDUP] Skipping duplicate {name}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_item['id'],
                            "content": '{"status": "skipped: duplicate call"}'
                        })
                        continue
                    seen_tools.add(dedup_key)

                    # GUARD: блокируем add_task если пользователь НЕ просил создать задачу
                    # Решает баг: AI создаёт задачи из вопросов/ответов
                    if name == 'add_task':
                        msg_lower = user_message.lower()
                        if not any(sig in msg_lower for sig in TASK_CREATION_SIGNALS):
                            logger.info(f"[GUARD] Blocked add_task — no creation signal in: '{user_message[:60]}'")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_item['id'],
                                "content": '{"status": "blocked: user did not ask to create a task. Answer with text instead."}'
                            })
                            continue

                    # GUARD: блокируем complete_task если пользователь просил УДАЛИТЬ задачу
                    # Решает баг: AI путает "удали" с "сделал" когда оба слова в тексте
                    if name == 'complete_task':
                        msg_lower = user_message.lower()
                        if any(sig in msg_lower for sig in TASK_DELETION_SIGNALS):
                            logger.info(f"[GUARD] Blocked complete_task → user wants delete_task: '{user_message[:60]}'")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_item['id'],
                                "content": '{"status": "blocked: user asked to DELETE, not complete. Use delete_task instead."}'
                            })
                            continue

                    # GUARD: блокируем create_post если пользователь НЕ давал согласия на публикацию
                    # Пост публикуется ТОЛЬКО по прямой просьбе или подтверждению
                    if name == 'create_post':
                        msg_lower = user_message.lower()
                        post_approval_signals = [
                            'опубликуй', 'запости', 'публикуй', 'постни',
                            'да, публикуй', 'ок, давай', 'давай', 'да',
                            'ок', 'запость', 'пост в ленту', 'опубликуй пост',
                            'сделай пост', 'напиши пост', 'пост',
                        ]
                        if not any(sig in msg_lower for sig in post_approval_signals):
                            logger.info(f"[GUARD] Blocked create_post — no approval signal in: '{user_message[:60]}'")
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_item['id'],
                                "content": '{"status": "blocked: user did not approve publishing. First SUGGEST the post topic, then wait for user OK before calling create_post."}'
                            })
                            continue

                    # Execute single tool — с прогрессом в Telegram
                    if self._progress_callback:
                        status = self._tool_progress_text(name, iteration + 1)
                        try:
                            await self._progress_callback(status)
                        except Exception:
                            pass

                    action = [{"tool": name, "params": args,
                               "reason": f"AI iter {iteration+1}: {name}"}]
                    results = await self.execute_actions(
                        action, user_id, session=session,
                        user_message=user_message,
                        progress_callback=progress_callback)

                    r = results[0] if results else {"success": False,
                                                     "error": "no result"}
                    all_execution_results.append(r)

                    # Добавляем tool result в messages (со сжатием)
                    if r.get('success'):
                        rc = json.dumps(r['result'], ensure_ascii=False,
                                        default=str)
                        rc = CognitiveEngine.compress_tool_result(rc)
                    else:
                        rc = json.dumps({"error": str(r.get('error', ''))},
                                        ensure_ascii=False)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_item['id'],
                        "content": rc
                    })

                # Продолжаем цикл — AI увидит результаты и решит
                # ответить текстом или вызвать ещё tools

            # Если вышли из цикла — финальный вызов без tools
            messages.append({
                "role": "user",
                "content": "Сформируй финальный ответ на основе выполненных действий."
            })
            final_resp = await self.call_ai(
                messages, use_tools=False, temperature=0.7)
            final_text = final_resp['choices'][0]['message'].get('content', '')
            return self._finalize_response(
                final_text, user_message, user_id, all_execution_results)

        except Exception as e:
            logger.error(f"[AGENT] Error: {e}\n{traceback.format_exc()}")
            error_responses = [
                "Что-то пошло не так. Перефразируй запрос.",
                "Техническая ошибка. Попробуй ещё раз.",
                "Упс, сбой. Скажи то же самое другими словами.",
                "Технические неполадки. Давай попробуем по-другому.",
                "Что-то сломалось. Перефразируй, пожалуйста.",
            ]
            return random.choice(error_responses)

    # ===== КОГНИТИВНАЯ ФИНАЛИЗАЦИЯ =====

    def _finalize_response(self, content, user_message, user_id, execution_results):
        """Clean → validate → save → return.
        
        Единая точка выхода: чистка тех. деталей, когнитивная валидация
        (убирает шаблонные начала, markdown, автоответчик, списки),
        сохранение в историю и обучение.
        """
        from .utils import clean_technical_details
        from .cognitive import CognitiveEngine

        final = clean_technical_details(content).strip()
        if not final:
            final = content.strip() or "Готово!"

        # Когнитивная валидация (quality gate)
        final, issues = CognitiveEngine.validate_response(final, user_message)
        if issues:
            logger.info(f"[COGNITIVE] Response fixed: {issues}")

        # Рефлексия для обучения
        tools_used = [r['tool'] for r in execution_results if r.get('success')]
        CognitiveEngine.reflect_on_response(user_message, final, tools_used)

        self._save_and_learn(user_message, user_id, execution_results, final)
        return final

    # ===== ОБУЧЕНИЕ И АДАПТАЦИЯ =====

    def _save_and_learn(self, user_message, user_id, execution_results, response):
        """Сохраняет в историю, обучается на результатах, обновляет паттерны."""
        
        # === Запись в execution_history ===
        tools_used = [r['tool'] for r in execution_results if r.get('success')]
        entry = {
            'message': user_message,
            'user_id': user_id,
            'results': execution_results,
            'tools_used': tools_used,
            'response': response,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'success': all(r.get('success', False) for r in execution_results)
                       if execution_results else True
        }
        self.execution_history.append(entry)
        if len(self.execution_history) > 50:
            self.execution_history = self.execution_history[-50:]

        # === Ответ в историю диалога ===
        from .conversation_history import save_message_to_history
        save_message_to_history(user_id, "assistant", response)

        # === Обучение на успешных паттернах ===
        if entry['success'] and tools_used:
            self._learn_from_success(user_message, user_id, tools_used)

        # === Контекстная память ===
        if tools_used:
            self.context_memory.append({
                'user_id': user_id,
                'tools': tools_used,
                'message_hint': user_message[:50],
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            if len(self.context_memory) > 100:
                self.context_memory = self.context_memory[-100:]

        # === Семантическая память (Pinecone) — fire-and-forget ===
        try:
            from .cognitive import CognitiveEngine
            emotion = CognitiveEngine.detect_emotion(user_message)
            intent = CognitiveEngine.classify_intent(user_message)
            asyncio.get_event_loop().create_task(
                store_conversation_turn(
                    user_id=user_id,
                    user_message=user_message,
                    bot_response=response,
                    emotion=emotion,
                    intent=intent
                )
            )
        except Exception as e:
            logger.warning(f"[VECTOR] Store failed: {e}")

        # === Self-learning feedback loop ===
        try:
            from .cognitive import CognitiveEngine
            emotion = CognitiveEngine.detect_emotion(user_message)
            intent = CognitiveEngine.classify_intent(user_message)
            _, issues = CognitiveEngine.validate_response(response, user_message)
            
            learner = get_learner()
            learner.record_turn(
                user_id=user_id,
                user_message=user_message,
                response=response,
                tools_used=tools_used,
                emotion=emotion,
                intent=intent,
                issues=issues if issues else None
            )
        except Exception as e:
            logger.warning(f"[SELF-LEARN] Record failed: {e}")

        # === Долгосрочная память — только значимые факты ===
        # НЕ сохраняем CRUD-операции (задачи уже в БД)
        try:
            from .memory import update_user_memory
            facts = []
            for r in execution_results:
                if r.get('success') and r.get('tool') in (
                    'create_goal', 'update_goal_progress',
                    'set_content_strategy', 'set_contact_alert',
                    'research_topic', 'get_news_trends'
                ):
                    if r['tool'] in ('research_topic', 'get_news_trends'):
                        facts.append(f"Искал: {r.get('reason', '')[:100]}")
                    else:
                        result_str = str(r.get('result', ''))[:150]
                        facts.append(f"{r['tool']}: {result_str}")
            if facts:
                update_user_memory("\n".join(facts), user_id=user_id)
                logger.info(f"[MEMORY] Saved {len(facts)} facts to long-term memory")
        except Exception as e:
            logger.warning(f"[MEMORY] Save failed: {e}")

    # ===== ЕДИНЫЙ МОЗГ ДЛЯ СИСТЕМНЫХ СООБЩЕНИЙ =====

    async def generate_system_message(self, user_id, mode, instruction,
                                       extra_context=None, max_tokens=1000,
                                       max_iterations=2):
        """Генерация системного сообщения (напоминание, проактивное, поздравление)
        через тот же мозг с tool calling, но без сохранения в историю диалога.

        Args:
            user_id: telegram ID пользователя
            mode: 'reminder' | 'proactive' | 'result_check'
            instruction: текст задания для AI (что сгенерировать)
            extra_context: дополнительный контекст (ситуация, красные флаги и т.д.)
            max_tokens: лимит токенов (короткие сообщения = меньше)
            max_iterations: макс. итераций tool calling (2 для скорости)

        Returns:
            str — готовый текст сообщения
        """
        try:
            # Контекст — тот же что и для обычного чата (async)
            ctx = await self._build_context(user_id)
            if not ctx:
                return self._system_message_fallback(mode, instruction)

            base_prompt = ctx['base_prompt']
            sub_tier = ctx['sub_tier']

            # Добавляем режим в системный промпт
            mode_instructions = {
                'reminder': (
                    "\n\n[РЕЖИМ: НАПОМИНАНИЕ]\n"
                    "Время задачи пришло. Подумай: можешь ли ты ПОМОЧЬ решить, а не просто напомнить?\n"
                    "Задача требует информации → найди через инструменты и дай результат.\n"
                    "Задача простая → напомни кратко. Спроси о статусе. НЕ создавай новые задачи."
                ),
                'task_assist': (
                    "\n\n[РЕЖИМ: ПОМОЩЬ С ЗАДАЧЕЙ]\n"
                    "Помоги решить задачу — не предлагай, а СДЕЛАЙ.\n"
                    "Используй инструменты и дай конкретный результат.\n"
                    "НЕ создавай новые задачи. До 10 предложений."
                ),
                'proactive': (
                    "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                    "Ты сам решил написать. СНАЧАЛА вызови инструменты (research_topic, get_news_trends, list_tasks) "
                    "для получения РЕАЛЬНЫХ данных, ПОТОМ отчитайся что нашёл.\n"
                    "НЕ ПИШИ 'проверил тренды' или 'нашёл данные' если не вызвал инструмент — это ЛОЖЬ.\n"
                    "Дай конкретную пользу с реальными данными + вопрос для вовлечения.\n"
                    "5-10 предложений. Деловой тон. Без воды и общих фраз."
                ),
                'result_check': (
                    "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                    "Задача выполнена — поздравь кратко. 1-2 предложения."
                ),
                'anchor': (
                    "\n\n[РЕЖИМ: ANCHOR ENGINE]\n"
                    "Ты — мозг AnchorEngine. Тебе переданы ЯКОРЯ (события/факты) + полный контекст.\n"
                    "РЕШЕНИЕ 1: Если якоря не стоят сообщения — верни ровно слово SKIP.\n"
                    "РЕШЕНИЕ 2: Если стоит написать — СНАЧАЛА ОБЯЗАТЕЛЬНО вызови инструменты "
                    "(research_topic, get_news_trends, list_tasks), получи РЕАЛЬНЫЕ данные, "
                    "ПОТОМ напиши сообщение на основе этих данных.\n"
                    "КАТЕГОРИЧЕСКИЙ ЗАПРЕТ: НЕ ПИШИ 'проверил тренды', 'посмотрел новости', "
                    "'нашёл данные' если ты НЕ вызвал инструмент. Это ЛОЖЬ и обман пользователя.\n"
                    "Если у пользователя 0 задач — предложи создать первую задачу по его интересам.\n"
                    "3-8 предложений. Деловой тон. Каждое утверждение — из реальных данных инструментов.\n"
                    "Объединяй несколько якорей в одно естественное сообщение. Без воды и общих фраз."
                ),
            }

            system_prompt = base_prompt + mode_instructions.get(mode, '')

            # Собираем messages — БЕЗ истории диалога (это системное сообщение)
            messages = [{"role": "system", "content": system_prompt}]

            # Если есть extra_context (ситуация, красные флаги) — добавляем
            if extra_context:
                messages.append({
                    "role": "user",
                    "content": f"[КОНТЕКСТ СИТУАЦИИ]\n{extra_context}"
                })

            messages.append({"role": "user", "content": instruction})

            # Определяем какие инструменты ИСКЛЮЧИТЬ по режиму
            exclude_tools = set()
            if mode == 'reminder':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}
            elif mode == 'task_assist':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}
            elif mode == 'result_check':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task',
                                 'edit_task', 'reschedule_task'}
            elif mode == 'proactive':
                exclude_tools = {'delegate_task'}
            elif mode == 'anchor':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}

            # ===== Tool calling loop (облегчённый) =====
            all_execution_results = []
            seen_tools = set()

            # Для anchor/proactive — первая итерация ОБЯЗАТЕЛЬНО вызывает инструменты
            # чтобы AI не выдумывал данные, а получал реальные
            force_tools_modes = {'anchor', 'proactive'}

            for iteration in range(max_iterations):
                # Первая итерация для anchor/proactive = required (заставляем вызвать инструмент)
                # Остальные = auto (AI решает сам)
                if iteration == 0 and mode in force_tools_modes:
                    current_tool_choice = "required"
                else:
                    current_tool_choice = "auto"

                response = await self.call_ai(
                    messages, use_tools=True, subscription_tier=sub_tier,
                    tool_choice=current_tool_choice, max_tokens=max_tokens,
                    exclude_tools=list(exclude_tools))

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → готово
                    from .utils import clean_technical_details
                    final = clean_technical_details(content).strip()
                    if final:
                        return final
                    # Если clean_technical_details убрала всё (DSML), retry без tools
                    if content.strip():
                        logger.warning(f"[AGENT:SYSTEM] Content cleaned to empty, retrying without tools")
                        retry_resp = await self.call_ai(
                            messages, use_tools=False, max_tokens=max_tokens)
                        retry_content = retry_resp['choices'][0]['message'].get('content', '')
                        retry_clean = clean_technical_details(retry_content).strip()
                        if retry_clean:
                            return retry_clean
                    return self._system_message_fallback(mode, instruction)

                # AI вызвал tools
                messages.append(msg)

                for tc_item in tool_calls:
                    func = tc_item.get('function', {})
                    name = func.get('name', '')
                    try:
                        args = json.loads(func.get('arguments', '{}'))
                    except Exception:
                        args = {}

                    # Dedup
                    dedup_key = f"{name}:{json.dumps(args, sort_keys=True)}"
                    if dedup_key in seen_tools:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_item['id'],
                            "content": '{"status": "skipped: duplicate"}'
                        })
                        continue
                    seen_tools.add(dedup_key)

                    # Блокируем запрещённые инструменты
                    if name in exclude_tools:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_item['id'],
                            "content": f'{{"status": "blocked: {name} not available in {mode} mode"}}'
                        })
                        continue

                    # Execute
                    action = [{"tool": name, "params": args,
                               "reason": f"system:{mode} iter {iteration+1}"}]
                    results = await self.execute_actions(
                        action, user_id, session=None, user_message=instruction)

                    r = results[0] if results else {"success": False, "error": "no result"}
                    all_execution_results.append(r)

                    if r.get('success'):
                        rc = json.dumps(r['result'], ensure_ascii=False, default=str)[:1500]
                    else:
                        rc = json.dumps({"error": str(r.get('error', ''))}, ensure_ascii=False)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc_item['id'],
                        "content": rc
                    })

            # Финальный вызов без tools после исчерпания итераций
            final_resp = await self.call_ai(
                messages, use_tools=False, max_tokens=max_tokens)
            final_text = final_resp['choices'][0]['message'].get('content', '')
            from .utils import clean_technical_details
            return clean_technical_details(final_text).strip() or self._system_message_fallback(mode, instruction)

        except Exception as e:
            logger.error(f"[AGENT:SYSTEM] Error in {mode}: {e}\n{traceback.format_exc()}")
            return self._system_message_fallback(mode, instruction)

    def _system_message_fallback(self, mode, instruction):
        """Fallback текст если AI недоступен."""
        if mode == 'reminder':
            # Извлекаем имя задачи из instruction
            import re
            match = re.search(r"[«'](.+?)[»']", instruction)
            task_name = match.group(1) if match else "задача"
            return f"Напоминаю о задаче: {task_name}. Как продвигается?"
        elif mode == 'result_check':
            return "Отлично, задача выполнена! 👍"
        else:
            return "Привет! Готов помочь с задачами и целями."

    def _learn_from_success(self, message, user_id, tools_used):
        """Обучение на успешных паттернах.
        
        Запоминает какие tools работали для каких типов запросов.
        Позволяет в будущем быстрее определять правильную стратегию.
        """
        # Определяем intent по tools
        intent = '_'.join(sorted(set(tools_used)))
        pattern_key = f"{user_id}:{intent}"
        
        if pattern_key not in self.success_patterns:
            self.success_patterns[pattern_key] = []
        
        self.success_patterns[pattern_key].append({
            'message': message[:100],
            'tools': tools_used,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Ограничиваем размер
        if len(self.success_patterns[pattern_key]) > 10:
            self.success_patterns[pattern_key] = self.success_patterns[pattern_key][-10:]
        
        logger.info(f"[LEARN] Pattern '{intent}' for user {user_id}, "
                     f"total patterns: {len(self.success_patterns)}")

    def get_similar_patterns(self, user_id, tools_hint=None):
        """Получить похожие успешные паттерны для пользователя."""
        results = []
        prefix = f"{user_id}:"
        for key, patterns in self.success_patterns.items():
            if key.startswith(prefix):
                results.extend(patterns)
        return sorted(results, key=lambda x: x.get('timestamp', ''), reverse=True)[:5]

    def adapt_to_user(self, user_id, preference_key, value):
        """Адаптация под предпочтения пользователя.
        
        Пример: adapt_to_user(123, 'response_style', 'brief')
        """
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id][preference_key] = value
        logger.info(f"[ADAPT] User {user_id}: {preference_key}={value}")

    def get_user_preference(self, user_id, preference_key, default=None):
        """Получить предпочтение пользователя."""
        return self.user_preferences.get(user_id, {}).get(preference_key, default)


# ===== ГЛОБАЛЬНЫЕ =====

_autonomous_agent = None


def get_autonomous_agent():
    """Глобальный экземпляр агента."""
    global _autonomous_agent
    if _autonomous_agent is None:
        _autonomous_agent = HybridAutonomousAgent()
    return _autonomous_agent


async def chat_with_ai(message, context=None, user_id=None, file_content=None,
                       db_session=None, message_type=None, subscription_tier=None,
                       progress_callback=None):
    """Главная точка входа. Совместима со всеми вызовами в проекте."""
    logger.info(f"[AGENT] START user={user_id} msg='{str(message)[:50]}...'")

    if user_id is None:
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        agent = get_autonomous_agent()
        history_len = len(agent.execution_history)

        response_text = await agent.process_request(
            message, user_id, context, db_session,
            subscription_tier, progress_callback=progress_callback)

        # Извлекаем tool_calls для тестов и мониторинга
        tool_calls = []
        tools_used = []
        if len(agent.execution_history) > history_len:
            last = agent.execution_history[-1]
            for r in last.get('results', []):
                if r.get('success'):
                    tools_used.append(r['tool'])
                    tool_calls.append({
                        'function': {
                            'name': r['tool'],
                            'arguments': json.dumps(r.get('params', {}))
                        }
                    })

        return {
            'response': response_text,
            'tool_calls': tool_calls,
            'tools_used': tools_used
        }

    except Exception as e:
        logger.error(f"[AGENT] ERROR: {e}\n{traceback.format_exc()}")
        return {
            'response': f"Извините, произошла ошибка: {str(e)}",
            'tool_calls': []
        }
