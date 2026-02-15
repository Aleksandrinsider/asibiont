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
}

# Слова, означающие что пользователь ЯВНО просит создать задачу
# Без этих слов add_task блокируется — чтобы агент не плодил задачи из вопросов
TASK_CREATION_SIGNALS = [
    'создай', 'добавь', 'запланируй', 'напомни', 'поставь задачу',
    'создать', 'добавить', 'запланировать', 'напомнить',
    'задачу на', 'задачу к', 'новую задачу', 'ещё задачу', 'еще задачу',
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
                      tool_choice=None, exclude_tools=None, **kwargs):
        """Универсальный вызов DeepSeek API."""
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": kwargs.pop("temperature", 0.7),
            "max_tokens": kwargs.pop("max_tokens", 1200),
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

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data,
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    # Логируем результат для tools
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

    def _determine_tool_choice(self, user_message):
        """Определяет нужно ли принудительно требовать tool calls.
        
        Для явных запросов данных (задачи, новости, партнёры) — required,
        чтобы AI гарантированно использовал инструменты.
        Для остального — auto.
        """
        msg_lower = user_message.lower()
        
        for category, keywords in TOOL_REQUIRED_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                logger.info(f"[ADAPTIVE] force_tool_choice=required for '{category}' "
                            f"keywords in: '{user_message[:50]}'")
                return "required"
        
        return "auto"

    # ===== КОНТЕКСТ =====

    def _build_context(self, user_id):
        """Собирает весь контекст пользователя за 1 сессию БД.
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
                    from .utils import get_weather_info, get_news_info
                    weather_info = get_weather_info(profile.city)
                    news_info = get_news_info(profile.city)
            if user.telegram_channel:
                profile_data['telegram_channel'] = user.telegram_channel

            # Память
            decrypted_memory = ""
            if user.memory:
                try:
                    from .memory import decrypt_data
                    decrypted_memory = decrypt_data(user.memory)
                except Exception:
                    pass

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
                    if asyncio.iscoroutinefunction(handler_func):
                        result = await handler_func(**params)
                    else:
                        result = handler_func(**params)

                    self.tool_discovery.learn_from_success(
                        func_name=tool_name, user_id=user_id,
                        context=reason, result=result)

                    results.append({"tool": tool_name, "success": True,
                                    "result": result, "reason": reason})
                    
                    logger.info(f"[EXEC] {tool_name} ✓ — {reason}")

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
                except Exception:
                    pass
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

            # Контекст
            ctx = self._build_context(user_id)
            if not ctx:
                return "Не удалось загрузить профиль. Попробуй ещё раз."

            base_prompt = ctx['base_prompt']
            sub_tier = ctx['sub_tier']

            # ═══ КОГНИТИВНОЕ ОБОГАЩЕНИЕ ═══
            from .cognitive import CognitiveEngine
            cognitive_hints = CognitiveEngine.build_cognitive_hints(user_message)
            if cognitive_hints:
                base_prompt += cognitive_hints

            # Собираем историю с учётом старого контекста
            from .conversation_history import get_conversation_history
            full_history = get_conversation_history(user_id, session=None, limit=16)

            if len(full_history) > 10:
                # Извлекаем темы из старых сообщений (без API вызова)
                old_msgs = full_history[:-8]
                history = full_history[-8:]
                topics = CognitiveEngine.extract_conversation_topics(old_msgs)
                if topics:
                    base_prompt += f"\n\n[РАНЕЕ ОБСУЖДАЛИ: {', '.join(topics)}]"
            else:
                history = full_history

            messages = [{"role": "system", "content": base_prompt}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Адаптивный tool_choice
            initial_tool_choice = self._determine_tool_choice(user_message)

            # ===== Tool calling loop (max 5 итераций) =====
            all_execution_results = []
            MAX_ITERATIONS = 5
            seen_tools = set()  # Для предотвращения дублей

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

                    # Execute single tool
                    action = [{"tool": name, "params": args,
                               "reason": f"AI iter {iteration+1}: {name}"}]
                    results = await self.execute_actions(
                        action, user_id, session=None,
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
                                       extra_context=None, max_tokens=600,
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
            # Контекст — тот же что и для обычного чата
            ctx = self._build_context(user_id)
            if not ctx:
                return self._system_message_fallback(mode, instruction)

            base_prompt = ctx['base_prompt']
            sub_tier = ctx['sub_tier']

            # Добавляем режим в системный промпт
            mode_instructions = {
                'reminder': (
                    "\n\n[РЕЖИМ: НАПОМИНАНИЕ]\n"
                    "Ты отправляешь НАПОМИНАНИЕ о задаче. Правила:\n"
                    "- Краткость: 2-4 предложения максимум\n"
                    "- Дружественный тон, адаптированный под время суток\n"
                    "- ОБЯЗАТЕЛЬНО заверши вопросом о статусе задачи\n"
                    "- Можешь использовать get_task_details для контекста\n"
                    "- НЕ создавай новые задачи\n"
                    "- НЕ пиши длинные мотивационные тексты"
                ),
                'proactive': (
                    "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                    "Ты SAM решил написать пользователю — не в ответ на его запрос.\n"
                    "Правила:\n"
                    "- 2-5 предложений, живой тон, конкретика\n"
                    "- Каждое сообщение = минимум 1 КОНКРЕТНОЕ действие\n"
                    "- Можешь использовать инструменты для актуальных данных\n"
                    "- НЕ начинай с банального 'Привет!' без пользы\n"
                    "- НЕ перечисляй функции бота\n"
                    "- НЕ придумывай @username, контакты, цифры"
                ),
                'result_check': (
                    "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                    "Задача выполнена — поздравь КРАТКО и позитивно.\n"
                    "1-2 предложения максимум. Без лишних вопросов."
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
            elif mode == 'result_check':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task',
                                 'edit_task', 'reschedule_task'}
            elif mode == 'proactive':
                exclude_tools = {'delegate_task'}

            # ===== Tool calling loop (облегчённый) =====
            all_execution_results = []
            seen_tools = set()

            for iteration in range(max_iterations):
                response = await self.call_ai(
                    messages, use_tools=True, subscription_tier=sub_tier,
                    tool_choice="auto", max_tokens=max_tokens,
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
