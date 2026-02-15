"""
Autonomous Agent — простая архитектура:
1 API call (AI + tools) → если tools вызваны: execute → 1 reflect
Никаких мульти-агентов, дублированного context building, 50 правил.
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


class HybridAutonomousAgent:
    """
    Простой агент: 1 вызов AI с tools → execute → reflect (если были tools).
    Без мульти-агентного pipeline, без дублированного контекста.
    """

    def __init__(self):
        self.execution_history = []
        self.tool_discovery = tool_discovery
        self._initialize_tools()
        self.active_sessions = 0

    def _initialize_tools(self):
        """Инициализирует динамическую систему инструментов"""
        try:
            from . import handlers
            self.tool_discovery.discover_tools_from_module(handlers)
            self.tool_discovery.load_stats()
            logger.info(f"[AGENT] Initialized {len(self.tool_discovery.discovered_tools)} tools")
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
            "max_tokens": kwargs.pop("max_tokens", 2000),
            **kwargs
        }

        if use_tools:
            available_tools = get_available_tools(subscription_tier)
            if exclude_tools:
                available_tools = [t for t in available_tools
                                   if t['function']['name'] not in exclude_tools]
            data["tools"] = available_tools
            data["tool_choice"] = tool_choice or "auto"
            logger.info(f"[AI] {len(available_tools)} tools, tier={subscription_tier}")

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data,
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    return await resp.json()
                error = await resp.text()
                raise Exception(f"AI call failed: {resp.status} {error}")

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
        """Выполняет tool calls через handlers."""
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

                # Фиксы параметров для известных tools
                if tool_name == 'find_relevant_contacts_for_task':
                    if 'description' in params and 'task_description' not in params:
                        params['task_description'] = params.pop('description')
                    elif 'task_description' not in params:
                        params['task_description'] = 'помощь с задачей'

                if tool_name == 'quick_topic_search' and not params.get('topic'):
                    if user_message:
                        stop = {'что', 'как', 'где', 'когда', 'почему', 'а', 'и', 'но'}
                        words = [w for w in re.findall(r'\b\w+\b', user_message.lower())
                                 if w not in stop and len(w) > 2][:3]
                        params['topic'] = ' '.join(words) if words else user_message[:50]
                    else:
                        params['topic'] = 'общая информация'

                # research_topic: AI иногда передаёт topic вместо query
                if tool_name == 'research_topic':
                    if 'topic' in params and 'query' not in params:
                        params['query'] = params.pop('topic')
                    elif 'query' not in params:
                        params['query'] = user_message[:200] if user_message else 'исследование'

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
                except Exception as e:
                    logger.error(f"[EXEC] {tool_name} error: {e}")
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

    # ===== ОСНОВНОЙ FLOW =====

    async def process_request(self, user_message, user_id, context=None,
                              session=None, subscription_tier=None,
                              progress_callback=None):
        """
        Стандартный tool calling loop:
        1. Собираем контекст
        2. Отправляем AI с tools
        3. Если AI вызвал tools → execute → добавляем результаты → повтор
        4. Когда AI отвечает текстом → готово
        Max 3 итерации.
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

            # История
            from .conversation_history import save_message_to_history
            save_message_to_history(user_id, "user", user_message)

            # Контекст
            ctx = self._build_context(user_id)
            if not ctx:
                return "Не удалось загрузить профиль. Попробуй ещё раз."

            base_prompt = ctx['base_prompt']
            sub_tier = ctx['sub_tier']

            # Собираем сообщения
            system_content = base_prompt + "\n\nОТВЕЧАЙ И ДЕЙСТВУЙ.\n" \
                "ОДИН add_task = ОДНА задача. Перенос → edit_task. " \
                "В title — СУТЬ (2-5 слов)."

            from .conversation_history import get_conversation_history
            history = get_conversation_history(user_id, session=None, limit=6)

            messages = [{"role": "system", "content": system_content}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # ===== Tool calling loop (max 3 iterations) =====
            all_execution_results = []
            MAX_ITERATIONS = 3
            seen_add_task = False

            for iteration in range(MAX_ITERATIONS):
                response = await self.call_ai(
                    messages, use_tools=True, subscription_tier=sub_tier)

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → готово
                    from .utils import clean_technical_details
                    final = clean_technical_details(content).strip()
                    if not final:
                        final = content.strip() or "Готово!"
                    self._save_and_learn(user_message, user_id,
                                        all_execution_results, final)
                    return final

                # AI вызвал tools → добавляем assistant message в цепочку
                messages.append(msg)

                for tc in tool_calls:
                    func = tc.get('function', {})
                    name = func.get('name', '')
                    try:
                        args = json.loads(func.get('arguments', '{}'))
                    except Exception:
                        args = {}

                    # Dedup add_task
                    if name == 'add_task' and seen_add_task:
                        logger.warning("[DEDUP] Skipping duplicate add_task")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc['id'],
                            "content": '{"status": "skipped: duplicate"}'
                        })
                        continue
                    if name == 'add_task':
                        seen_add_task = True

                    # Execute single tool
                    action = [{"tool": name, "params": args,
                               "reason": f"AI: {name}"}]
                    results = await self.execute_actions(
                        action, user_id, session=None,
                        user_message=user_message,
                        progress_callback=progress_callback)

                    r = results[0] if results else {"success": False,
                                                     "error": "no result"}
                    all_execution_results.append(r)

                    # Добавляем tool result в messages
                    # NEED_TIME — НЕ shortcut, пусть AI сам сформулирует вопрос
                    if r.get('success'):
                        rc = json.dumps(r['result'], ensure_ascii=False,
                                        default=str)[:2000]
                    else:
                        rc = json.dumps({"error": str(r.get('error', ''))},
                                        ensure_ascii=False)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc['id'],
                        "content": rc
                    })

                # Продолжаем цикл — AI увидит результаты и решит
                # ответить текстом или вызвать ещё tools

            # Если вышли из цикла — финальный вызов без tools
            messages.append({
                "role": "user",
                "content": "Сформируй ответ на основе выполненных действий."
            })
            final_resp = await self.call_ai(
                messages, use_tools=False, temperature=0.7)
            final_text = final_resp['choices'][0]['message'].get('content', '')
            from .utils import clean_technical_details
            final = clean_technical_details(final_text).strip() or "Готово!"
            self._save_and_learn(user_message, user_id,
                                all_execution_results, final)
            return final

        except Exception as e:
            logger.error(f"[AGENT] Error: {e}\n{traceback.format_exc()}")
            return random.choice([
                "Что-то пошло не так. Перефразируй запрос.",
                "Техническая ошибка. Попробуй ещё раз.",
                "Упс, сбой. Скажи то же самое другими словами.",
            ])

    def _save_and_learn(self, user_message, user_id, execution_results, response):
        """Сохраняет в историю и память."""
        entry = {
            'message': user_message,
            'user_id': user_id,
            'results': execution_results,
            'response': response,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'success': all(r.get('success', False) for r in execution_results)
        }
        self.execution_history.append(entry)
        if len(self.execution_history) > 50:
            self.execution_history = self.execution_history[-50:]

        # Ответ в историю диалога
        from .conversation_history import save_message_to_history
        save_message_to_history(user_id, "assistant", response)

        # Память — только значимые факты
        try:
            from .memory import update_user_memory
            facts = []
            for r in execution_results:
                if r.get('success') and r['tool'] in (
                    'create_goal', 'update_goal_progress', 'set_content_strategy',
                    'set_contact_alert', 'research_topic', 'get_news_trends'
                ):
                    if r['tool'] in ('research_topic', 'get_news_trends'):
                        facts.append(f"Искал: {r.get('reason', '')[:100]}")
                    else:
                        facts.append(f"{r['tool']}: {str(r.get('result', ''))[:150]}")
            if facts:
                update_user_memory("\n".join(facts), user_id=user_id)
        except Exception as e:
            logger.warning(f"[MEMORY] Save failed: {e}")


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

        # Извлекаем tool_calls для тестов
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
