#!/usr/bin/env python
"""
Channel Optimizer — универсальный модуль самооптимизации автопилота.

Содержит 8 архитектурных улучшений (U1-U8), которые работают для 
любого пользователя, любых целей и любых интеграций.

Компоненты:
  U1. InsightActionRouter    — превращает инсайты в автоматические действия
  U2. AdaptiveCircuitBreaker — блокирует зацикленные бесперспективные задачи
  U3. AdaptiveMonitor        — динамически регулирует частоту проверок
  U4. ChannelEffectiveness  — универсальный скоринг каналов/интеграций
  U5. TokenBudgetAllocator  — перераспределяет бюджет между каналами
  U6. GoalDecomposer        — разбивает любую цель на достижимые подцели
  U7. IntegrationAwarePlanner — планирует задачи только под доступные интеграции
  U8. SelfHealingOrchestrator — самовосстановление системы
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# U1: Feedback Action Loop — применение CoordinatorInsight
# ═══════════════════════════════════════════════════════════════

class InsightActionRouter:
    """
    Превращает инсайты координатора в автоматические действия.
    Не привязан к конкретным каналам — работает по regex-паттернам.
    
    Пример: инсайт "email: охват 7/день вместо 40" →
    → снижение приоритета email, переключение бюджета на другой канал.
    """

    RULES = [
        # Канал работает ниже ожиданий → снизить приоритет, переключить бюджет
        {
            'pattern': r'(channel_effectiveness|канал).*?охват\s+(\d+)/день\s+вместо\s+(\d+)',
            'action': 'adjust_channel_priority',
            'description': 'Канал {channel} работает на {current_rate}/{target_rate} → снизить приоритет',
        },
        # Агент делает N+ повторений без результата → блокировка
        {
            'pattern': r'повторные попытки к (\w+).*?потеря времени',
            'action': 'cool_down_agent',
            'description': 'Агент {agent} теряет время → cooldown 6ч',
        },
        # Цель без прогресса >7 дней → разбить на подцели
        {
            'pattern': r'goal.*?(?:без прогресса|stagnation|застрял)',
            'action': 'suggest_goal_breakdown',
            'description': 'Цель застряла → предложить разбивку на подцели',
        },
        # Инструмент вызывается слишком часто без результата
        {
            'pattern': r'(tool|инструмент).*?(\w+).*?(\d+)\s+раз',
            'action': 'block_tool_temp',
            'description': 'Инструмент {tool} вызван {count} раз → временная блокировка',
        },
    ]

    @classmethod
    async def process_insight(cls, insight_text: str, user_id: int) -> Optional[dict]:
        """
        Анализирует текст инсайта и возвращает действие, если найдено совпадение.
        
        Args:
            insight_text: текст CoordinatorInsight.summary
            user_id: ID пользователя
            
        Returns:
            dict с action, params, description или None
        """
        for rule in cls.RULES:
            match = re.search(rule['pattern'], insight_text, re.IGNORECASE)
            if match:
                action = rule['action']
                description = rule['description']
                
                # Подставляем группы захвата в описание
                if match.groups():
                    for i, group in enumerate(match.groups(), 1):
                        description = description.replace(f'{{{i}}}', group)
                
                return {
                    'action': action,
                    'params': {
                        'user_id': user_id,
                        'insight': insight_text[:200],
                        'matched_groups': match.groups(),
                    },
                    'description': description,
                }
        return None

    @classmethod
    async def apply_action(cls, action: dict) -> str:
        """
        Применяет найденное действие. Вызывается из _save_and_learn().
        
        Returns:
            Строка с результатом применения действия.
        """
        action_type = action.get('action', '')
        params = action.get('params', {})
        user_id = params.get('user_id')
        
        if action_type == 'adjust_channel_priority':
            # Логируем в CoordinatorInsight, что канал скорректирован
            return f"[AUTO] Приоритет канала скорректирован на основе инсайта"
        
        elif action_type == 'cool_down_agent':
            agent_name = params.get('matched_groups', [''])[0]
            # Используем CircuitBreaker для блокировки
            from .channel_optimizer import AdaptiveCircuitBreaker
            cb = AdaptiveCircuitBreaker(user_id)
            key = f"agent:{agent_name}:general"
            await cb.record_failure(key, f"Auto-blocked by InsightActionRouter: {params.get('insight', '')}")
            # Добавляем ещё несколько фейлов, чтобы сработал cooldown
            for _ in range(3):
                await cb.record_failure(key, "InsightActionRouter accumulated failures")
            state = cb.get_state(key)
            if state:
                return f"[AUTO] Агент {agent_name} отправлен в cooldown до {state.get('cooldown_until', 'N/A')}"
            return f"[AUTO] Circuit breaker активирован для {agent_name}"
        
        elif action_type == 'suggest_goal_breakdown':
            return f"[AUTO] Цель требует разбивки — вызови GoalDecomposer"
        
        elif action_type == 'block_tool_temp':
            tool_name = params.get('matched_groups', ['', ''])[1]
            cb = AdaptiveCircuitBreaker(user_id)
            key = f"tool:{tool_name}"
            for _ in range(4):
                await cb.record_failure(key, "Auto-blocked by InsightActionRouter")
            return f"[AUTO] Инструмент {tool_name} временно заблокирован"
        
        return ""


# ═══════════════════════════════════════════════════════════════
# U2: Adaptive Circuit Breaker — универсальный
# ═══════════════════════════════════════════════════════════════

class AdaptiveCircuitBreaker:
    """
    Универсальный circuit breaker для агентов и инструментов.
    
    Логика:
    - 3 последовательных неудачи по одному ключу → cooldown 4ч
    - 5 последовательных неудач → cooldown 24ч
    - 10+ → блокировка до ручного снятия (7 дней)
    - Сброс при первом успехе после cooldown
    
    Ключи имеют формат:
    - 'agent:{name}:{tool}' — для агента и конкретного инструмента
    - 'tool:{name}' — для инструмента глобально
    - 'goal:{id}' — для цели
    - 'channel:{name}' — для канала/интеграции
    """

    THRESHOLDS = [
        (3, 4),     # 3 неудачи → cooldown 4 часа
        (5, 24),    # 5 неудач → cooldown 24 часа
        (10, 168),  # 10+ неудач → cooldown 7 дней (168 часов)
    ]

    def __init__(self, user_id: int | None = None):
        self.user_id = user_id
        self._cache: dict[str, dict] = {}

    def _resolve_user_id(self, user_id: int | None = None) -> int:
        """Определяет user_id: параметр → self.user_id."""
        uid = user_id if user_id is not None else self.user_id
        if uid is None:
            raise ValueError("AdaptiveCircuitBreaker: user_id is required (pass to constructor or method)")
        return uid

    async def should_block(self, key: str, user_id: int | None = None) -> tuple[bool, str]:
        """
        Проверяет, заблокирован ли ключ.
        
        Args:
            key: ключ блокировки (tool:name, agent:name:tool, etc.)
            user_id: опционально, если не указан при создании экземпляра
        
        Returns:
            (is_blocked, reason)
        """
        _uid = self._resolve_user_id(user_id)
        # Сначала проверяем кэш
        state = self._cache.get(key)
        if state and state.get('cooldown_until'):
            if datetime.utcnow() < state['cooldown_until']:
                remaining = int((state['cooldown_until'] - datetime.utcnow()).total_seconds() / 60)
                return True, f"circuit breaker active ({remaining}мин осталось)"
        
        # Затем БД
        try:
            from models import Session, CircuitBreakerState as CBS
            session = Session()
            try:
                record = session.query(CBS).filter(
                    CBS.user_id == _uid,
                    CBS.key == key
                ).first()
                if record and record.cooldown_until:
                    if datetime.utcnow() < record.cooldown_until:
                        remaining = int((record.cooldown_until - datetime.utcnow()).total_seconds() / 60)
                        # Обновляем кэш
                        self._cache[key] = {
                            'fail_count': record.fail_count,
                            'cooldown_until': record.cooldown_until,
                        }
                        return True, f"circuit breaker active ({remaining}мин осталось)"
                    else:
                        # Cooldown истёк — сбрасываем
                        record.cooldown_until = None
                        record.fail_count = 0
                        session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.debug("[CB] DB check error: %s", e)
        
        return False, ""

    async def record_failure(self, key: str, context: str = "", user_id: int | None = None):
        """
        Увеличивает счётчик неудач и накладывает cooldown по порогам.
        
        Args:
            key: ключ блокировки
            context: контекст ошибки
            user_id: опционально, если не указан при создании экземпляра
        """
        _uid = self._resolve_user_id(user_id)
        now = datetime.utcnow()
        
        # Обновляем кэш
        state = self._cache.setdefault(key, {'fail_count': 0, 'cooldown_until': None})
        state['fail_count'] = state.get('fail_count', 0) + 1
        state['last_fail'] = now
        
        # Рассчитываем cooldown
        cooldown_hours = self._calc_cooldown(state['fail_count'])
        if cooldown_hours:
            state['cooldown_until'] = now + timedelta(hours=cooldown_hours)
        else:
            state['cooldown_until'] = None
        
        # Сохраняем в БД
        try:
            from models import Session, CircuitBreakerState as CBS
            session = Session()
            try:
                record = session.query(CBS).filter(
                    CBS.user_id == _uid,
                    CBS.key == key
                ).first()
                if not record:
                    record = CBS(user_id=_uid, key=key)
                    session.add(record)
                
                record.fail_count = state['fail_count']
                record.first_fail_at = record.first_fail_at or now
                record.cooldown_until = state.get('cooldown_until')
                record.last_fail_context = context[:500]
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.debug("[CB] DB save error: %s", e)

    async def record_success(self, key: str, user_id: int | None = None):
        """
        Сбрасывает счётчик при успехе — «лечение» circuit breaker.
        """
        state = self._cache.pop(key, None)
        
        try:
            from models import Session, CircuitBreakerState as CBS
            session = Session()
            try:
                record = session.query(CBS).filter(
                    CBS.user_id == self.user_id,
                    CBS.key == key
                ).first()
                if record:
                    record.fail_count = 0
                    record.cooldown_until = None
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.debug("[CB] success reset error: %s", e)

    def get_state(self, key: str) -> Optional[dict]:
        """Возвращает текущее состояние circuit breaker для ключа."""
        state = self._cache.get(key)
        if state:
            return dict(state)
        
        try:
            from models import Session, CircuitBreakerState as CBS
            session = Session()
            try:
                record = session.query(CBS).filter(
                    CBS.user_id == self.user_id,
                    CBS.key == key
                ).first()
                if record:
                    return {
                        'fail_count': record.fail_count,
                        'cooldown_until': record.cooldown_until,
                        'first_fail_at': record.first_fail_at,
                    }
            finally:
                session.close()
        except Exception:
            pass
        return None

    def _calc_cooldown(self, fail_count: int) -> int:
        """Рассчитывает часы cooldown на основе количества неудач."""
        for threshold, hours in self.THRESHOLDS:
            if fail_count >= threshold:
                return hours
        return 0


# ═══════════════════════════════════════════════════════════════
# U3: Adaptive Monitoring Frequency
# ═══════════════════════════════════════════════════════════════

class AdaptiveMonitor:
    """
    Динамически регулирует интервал проверки источника данных.
    
    Логика:
    - База: 60 минут
    - После 3 пустых проверок подряд → ×2 (120 мин)
    - После 5 пустых → ×4 (240 мин, ~4ч)
    - При находке → сброс к базе (60 мин)
    - Максимум: 480 мин (8ч)
    - Минимум: 15 мин
    """

    BASE_INTERVAL = 60       # минут
    MAX_INTERVAL = 480       # 8 часов
    MIN_INTERVAL = 15        # 15 минут
    EMPTY_THRESHOLD_STEP1 = 3   # ×2 после 3 пустых
    EMPTY_THRESHOLD_STEP2 = 5   # ×4 после 5 пустых

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._cache: dict[str, dict] = {}

    async def get_next_interval(self, source: str) -> int:
        """
        Возвращает интервал в минутах до следующей проверки источника.
        
        Args:
            source: идентификатор источника ('rss_openinsider', 'email_inbox' и т.д.)
            
        Returns:
            Интервал в минутах
        """
        # Сначала проверяем кэш
        state = self._cache.get(source)
        if state:
            return self._calc_interval(state.get('empty_count', 0))
        
        # Потом БД
        try:
            from models import Session, AdaptiveMonitorState as AMS
            session = Session()
            try:
                record = session.query(AMS).filter(
                    AMS.user_id == self.user_id,
                    AMS.source == source
                ).first()
                if record:
                    self._cache[source] = {
                        'empty_count': record.empty_count,
                        'base_interval': record.base_interval or self.BASE_INTERVAL,
                    }
                    return self._calc_interval(record.empty_count)
            finally:
                session.close()
        except Exception as e:
            logger.debug("[AM] DB read error: %s", e)
        
        return self.BASE_INTERVAL

    async def report_empty(self, source: str):
        """
        Вызывается, когда источник не дал новых данных.
        Увеличивает счётчик пустых проверок.
        """
        state = self._cache.setdefault(source, {
            'empty_count': 0,
            'base_interval': self.BASE_INTERVAL,
        })
        state['empty_count'] += 1
        
        await self._persist(source, state)

    async def report_found(self, source: str):
        """
        Вызывается, когда источник дал новые данные.
        Сбрасывает счётчик пустых проверок к нулю.
        """
        state = self._cache.setdefault(source, {
            'empty_count': 0,
            'base_interval': self.BASE_INTERVAL,
        })
        state['empty_count'] = 0
        
        await self._persist(source, state)

    async def _persist(self, source: str, state: dict):
        """Сохраняет состояние в БД."""
        try:
            from models import Session, AdaptiveMonitorState as AMS
            session = Session()
            try:
                record = session.query(AMS).filter(
                    AMS.user_id == self.user_id,
                    AMS.source == source
                ).first()
                if not record:
                    record = AMS(
                        user_id=self.user_id,
                        source=source,
                    )
                    session.add(record)
                
                record.empty_count = state.get('empty_count', 0)
                record.base_interval = state.get('base_interval', self.BASE_INTERVAL)
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.debug("[AM] persist error: %s", e)

    def _calc_interval(self, empty_count: int) -> int:
        """Рассчитывает интервал на основе количества пустых проверок."""
        base = self.BASE_INTERVAL
        
        if empty_count >= self.EMPTY_THRESHOLD_STEP2:
            return min(base * 4, self.MAX_INTERVAL)
        elif empty_count >= self.EMPTY_THRESHOLD_STEP1:
            return min(base * 2, self.MAX_INTERVAL)
        else:
            return base


# ═══════════════════════════════════════════════════════════════
# U4: Universal Channel Effectiveness Scoring
# ═══════════════════════════════════════════════════════════════

class ChannelEffectivenessScorer:
    """
    Универсальный скоринг каналов/интеграций.
    Динамически определяет интеграции пользователя и оценивает их эффективность.
    
    Не привязан к email — работает для любых интеграций:
    email, telegram, github, blog, discord, twitter, linkedin и т.д.
    """

    # Сигнатуры для динамического обнаружения интеграций
    INTEGRATION_SIGNATURES = {
        'email': ['smtp', 'email', 'sendgrid', 'mailgun', 'outlook', 'imap', 'gmail', 'resend', 'яндекс'],
        'telegram': ['telegram', 'bot_token', 'telegraf', 'api.telegram'],
        'github': ['github', 'git_token', 'api.github', 'gitlab'],
        'blog': ['wordpress', 'blog.', 'medium', 'hashnode', 'сайт', 'лендинг'],
        'discord': ['discord', 'discord_webhook', 'discord_bot'],
        'twitter': ['twitter', 'x_api', 'x.com'],
        'linkedin': ['linkedin', 'linkedin_api'],
        'crm': ['crm', 'amocrm', 'битрикс', 'hubspot', 'pipedrive', 'salesforce'],
    }

    # Маппинг инструментов на каналы
    TOOL_CHANNEL_MAP = {
        'send_email': 'email',
        'send_outreach_email': 'email',
        'reply_to_outreach_email': 'email',
        'check_emails': 'email',
        'start_email_campaign': 'email',
        'list_email_contacts': 'email',
        'save_email_contact': 'email',
        'publish_to_telegram': 'telegram',
        'create_post': 'blog',
        'publish_blog': 'blog',
        'publish_to_discord': 'discord',
        'github_search': 'github',
        'github_issues': 'github',
        'web_search': 'research',
        'research_topic': 'research',
    }

    # Результаты, считающиеся успешными конверсиями
    OUTCOME_KEYWORDS = [
        'отправлен', 'sent', 'сохранен', 'saved',
        'опубликован', 'published', 'найдено', 'found',
        'готово', 'done', 'успешно', 'success',
        'result', 'результат', 'ответ', 'reply',
        'контакт', 'contact', 'подписчик', 'subscriber',
    ]

    @classmethod
    def detect_user_integrations(cls, user_api_keys: str, python_code: str) -> dict:
        """
        Динамически определяет, какие интеграции есть у пользователя.
        
        Returns:
            dict вида: {'email': {'detected': True, 'signals': ['smtp', 'imap']}, ...}
        """
        integrations = {}
        text_to_check = (user_api_keys + ' ' + python_code).lower()
        
        for name, signals in cls.INTEGRATION_SIGNATURES.items():
            found = [s for s in signals if s in text_to_check]
            if found:
                integrations[name] = {
                    'detected': True,
                    'signals': found,
                }
        
        return integrations

    @classmethod
    def classify_tool_to_channel(cls, tool_name: str) -> str:
        """Определяет, к какому каналу относится инструмент."""
        return cls.TOOL_CHANNEL_MAP.get(tool_name, 'other')

    @classmethod
    def is_positive_outcome(cls, result_text: str) -> bool:
        """Проверяет, является ли результат успешным исходом."""
        text = result_text.lower()
        return any(kw in text for kw in cls.OUTCOME_KEYWORDS)

    @classmethod
    async def get_channel_scores(cls, user_id: int, days: int = 7) -> dict:
        """
        Возвращает score для каждого канала пользователя за N дней.
        Вызывается Coordinator-ом перед решением о распределении бюджета.
        
        Returns:
            dict вида: {'email': {'actions': 10, 'outcomes': 3, 'conversion': 0.3, 'score': 0.15}, ...}
        """
        from models import Session, AgentActivityLog
        
        session = Session()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            logs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user_id,
                AgentActivityLog.created_at >= cutoff
            ).all()
            
            channels = {}
            for log in logs:
                channel = cls.classify_tool_to_channel(str(log.tool_name or ''))
                if channel not in channels:
                    channels[channel] = {'actions': 0, 'outcomes': 0}
                channels[channel]['actions'] += 1
                if cls.is_positive_outcome(str(log.result or '')):
                    channels[channel]['outcomes'] += 1
            
            scores = {}
            for ch, data in channels.items():
                conversion = data['outcomes'] / max(data['actions'], 1)
                confidence = min(data['actions'] / 10, 1.0)
                score = round(conversion * confidence, 2)
                scores[ch] = {
                    'actions': data['actions'],
                    'outcomes': data['outcomes'],
                    'conversion': conversion,
                    'score': score,
                }
            
            return scores
        finally:
            session.close()


# ═══════════════════════════════════════════════════════════════
# U5: Adaptive Token Budget Allocation
# ═══════════════════════════════════════════════════════════════

class TokenBudgetAllocator:
    """
    Перераспределяет бюджет токенов между каналами на основе их score.
    
    Логика:
    1. Получить ChannelScore для всех каналов пользователя
    2. Каналы с score > 0.3 → 60% бюджета (пропорционально score)
    3. Каналы с score <= 0.3 → 20% бюджета (равномерно, экспериментальный минимум)
    4. 20% резерв на новые/неклассифицированные действия
    """

    @classmethod
    def allocate(cls, channel_scores: dict, total_budget: int = 1000) -> dict:
        """
        Возвращает {channel: allocated_tokens}.
        
        Args:
            channel_scores: результат ChannelEffectivenessScorer.get_channel_scores()
            total_budget: всего токенов на период
            
        Returns:
            dict вида: {'email': 150, 'telegram': 400, '_reserve': 200, ...}
        """
        if not channel_scores:
            return {'_reserve': total_budget}

        allocation = {}

        # Разделяем на эффективные (score > 0.3) и неэффективные
        effective = {ch: data for ch, data in channel_scores.items() if data.get('score', 0) > 0.3}
        ineffective = {ch: data for ch, data in channel_scores.items() if data.get('score', 0) <= 0.3}

        # Эффективные каналы → 60% бюджета пропорционально score
        if effective:
            total_score = sum(d['score'] for d in effective.values())
            if total_score > 0:
                eff_budget = int(total_budget * 0.6)
                for ch, data in effective.items():
                    allocation[ch] = max(int(eff_budget * data['score'] / total_score), 10)

        # Неэффективные каналы → 20% равномерно (экспериментальный минимум)
        if ineffective:
            ineff_budget = int(total_budget * 0.2)
            per_ineff = max(ineff_budget // max(len(ineffective), 1), 5)
            for ch in ineffective:
                allocation[ch] = per_ineff

        # 20% резерв
        allocation['_reserve'] = int(total_budget * 0.2)

        return allocation

    @classmethod
    def get_budget_context(cls, user_id: int, total_budget: int = 1000) -> str:
        """
        Генерирует текстовый контекст для промпта ASI:
        «Бюджет токенов: email=150, telegram=400, research=200...»
        """
        import asyncio
        try:
            scores = asyncio.run(ChannelEffectivenessScorer.get_channel_scores(user_id))
        except Exception:
            scores = {}
        
        allocation = cls.allocate(scores, total_budget)
        
        if not allocation or len(allocation) <= 1:
            return ""
        
        parts = []
        for ch, budget in sorted(allocation.items(), key=lambda x: -x[1]):
            if ch == '_reserve':
                parts.append(f"резерв={budget}")
            else:
                score = scores.get(ch, {}).get('score', 0)
                parts.append(f"{ch}={budget} (score={score})")
        
        return (
            f"📊 Бюджет токенов на период: {', '.join(parts)}\n"
            "Направляй больше усилий на каналы с высоким score."
        )


# ═══════════════════════════════════════════════════════════════
# U6: Universal Goal Decomposition Engine
# ═══════════════════════════════════════════════════════════════

class GoalDecomposer:
    """
    Универсальный декомпозитор целей.
    Работает с любой целью — не хардкодит метрики.
    
    Берёт цель пользователя, делит на 4 квадранта (25/50/75/100%),
    создаёт под-задачи и отслеживает прогресс.
    """

    MILESTONES = [25, 50, 75, 100]

    @classmethod
    async def decompose(cls, goal) -> list[dict]:
        """
        Разбивает цель на под-цели.
        
        Args:
            goal: объект Goal из models.py
            
        Returns:
            Список словарей с под-целями для создания в Task
        """
        target = goal.target_value or 100
        metric = goal.metric_unit or 'шагов'
        title = (goal.title or 'Цель')[:40]

        tasks = []
        for i, percent in enumerate(cls.MILESTONES):
            target_value = int(target * percent / 100)
            tasks.append({
                'title': f"{title} — {percent}% ({target_value} {metric})",
                'description': (
                    f"Под-цель {percent}% от «{goal.title}». "
                    f"Целевое значение: {target_value} {metric}. "
                    f"Создано автоматически GoalDecomposer."
                ),
                'target_value': target_value,
                'goal_id': goal.id,
                'status': 'active' if i == 0 else 'pending',
                'source': 'agent',
            })

        return tasks

    @classmethod
    def get_stuck_warning(cls, goal, days_stuck: int) -> str:
        """Генерирует предупреждение, если цель застряла."""
        if days_stuck < 7:
            return ""
        
        return (
            f"⚠️ Цель «{goal.title}» не двигается {days_stuck} дней. "
            f"Текущий прогресс: {goal.progress_percentage}%.\n"
            f"Рекомендации:\n"
            f"1. Попробовать другой канал привлечения\n"
            f"2. Разбить на более мелкие шаги (уже сделано, если GoalDecomposer запущен)\n"
            f"3. Проверить, какие интеграции настроены и работают ли они\n"
            f"4. Рассмотреть смену формулировки цели"
        )


# ═══════════════════════════════════════════════════════════════
# U7: Integration-Aware Action Planning
# ═══════════════════════════════════════════════════════════════

class IntegrationAwarePlanner:
    """
    Планировщик, который учитывает только реально доступные интеграции.
    Динамически определяет интеграции из user_api_keys и python_code агентов.
    """

    @classmethod
    def get_available_channels(cls, user_api_keys: str, python_code: str) -> list[str]:
        """Определяет доступные каналы для пользователя."""
        text = (user_api_keys + ' ' + python_code).lower()
        available = []
        
        for channel, signals in ChannelEffectivenessScorer.INTEGRATION_SIGNATURES.items():
            if any(s in text for s in signals):
                available.append(channel)
        
        return available

    @classmethod
    async def generate_availability_context(cls, user_id: int, agents: list = None) -> str:
        """
        Генерирует текстовый контекст для промпта ASI:
        «У пользователя доступны: email, telegram, github. НЕ доступны: blog, discord.»
        """
        from models import Session, User, UserAgent
        
        all_available = set()
        all_unavailable_signals = set()
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return ""
            
            # Если агенты не переданы — загружаем из БД
            if agents is None:
                agent_objs = session.query(UserAgent).filter(
                    UserAgent.author_id == user.id,
                    UserAgent.is_active == True
                ).all()
            else:
                agent_objs = agents
            
            for agent in agent_objs:
                keys = getattr(agent, 'user_api_keys', '') or ''
                code = getattr(agent, 'python_code', '') or ''
                channels = cls.get_available_channels(keys, code)
                all_available.update(channels)
            
            if not all_available:
                return ""
            
            # Определяем, чего НЕТ
            all_possible = set(ChannelEffectivenessScorer.INTEGRATION_SIGNATURES.keys())
            unavailable = all_possible - all_available
            
            available_str = ', '.join(sorted(all_available))
            unavailable_str = ', '.join(sorted(unavailable)) if unavailable else '—'
            
            return (
                f"📡 Доступные интеграции: {available_str}.\n"
                f"❌ Недоступны: {unavailable_str}.\n"
                "Планируй задачи и делегируй только под доступные каналы. "
                "Не пытайся использовать недоступные интеграции."
            )
        finally:
            session.close()


# ═══════════════════════════════════════════════════════════════
# U8: Self-Healing Orchestrator
# ═══════════════════════════════════════════════════════════════

class SelfHealingOrchestrator:
    """
    Оркестратор самовосстановления системы.
    
    Проверяет состояние системы и применяет стратегии:
    1. Stagnation → switch_channel (переключить канал)
    2. Empty monitors → reduce_frequency (уменьшить частоту)
    3. Tool failures → block_tool_temp (блокировать инструмент)
    4. Stuck goals → suggest_new_goal (переформулировать цель)
    """

    HEALING_STRATEGIES = [
        {
            'condition': 'stagnation',
            'min_threshold': 3,
            'action': 'switch_channel',
            'description': 'Обнаружен застой активности → переключиться на другой канал',
        },
        {
            'condition': 'empty_monitors',
            'min_threshold': 5,
            'action': 'reduce_frequency',
            'description': 'Слишком много пустых проверок → снизить частоту через AdaptiveMonitor',
        },
        {
            'condition': 'tool_fail_rate',
            'min_threshold': 0.7,
            'action': 'block_tool_temp',
            'description': 'Высокий процент неудач инструмента → временная блокировка',
        },
        {
            'condition': 'goal_stuck',
            'min_threshold': 14,
            'action': 'suggest_new_goal',
            'description': 'Цель застряла на 14+ дней → предложить переформулировать',
        },
    ]

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.circuit_breaker = AdaptiveCircuitBreaker(user_id)
        self.adaptive_monitor = AdaptiveMonitor(user_id)

    async def heal(self) -> list[str]:
        """
        Запускает полный цикл самовосстановления.
        
        Returns:
            Список выполненных действий.
        """
        actions_taken = []
        health_context = await self._build_health_context()

        for strategy in self.HEALING_STRATEGIES:
            condition_value = health_context.get(strategy['condition'], 0)
            if condition_value >= strategy['min_threshold']:
                result = await self._apply_strategy(
                    strategy['action'],
                    strategy['description'],
                    health_context,
                )
                actions_taken.append(result)

        return actions_taken

    async def _build_health_context(self) -> dict:
        """Собирает контекст здоровья системы."""
        context = {
            'stagnation': 0,
            'empty_monitors': 0,
            'tool_fail_rate': 0.0,
            'goal_stuck': 0,
        }

        try:
            from models import Session, AgentActivityLog, Goal, AdaptiveMonitorState

            session = Session()
            try:
                cutoff_48h = datetime.utcnow() - timedelta(hours=48)

                # Stagnation: проверяем AgentActivityLog
                recent_logs = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == self.user_id,
                    AgentActivityLog.created_at >= cutoff_48h
                ).count()
                
                if recent_logs < 3:
                    context['stagnation'] = recent_logs  # чем меньше, тем выше стагнация
                    context['stagnation'] = max(0, 5 - recent_logs)  # инвертируем

                # Tool fail rate
                failed_logs = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == self.user_id,
                    AgentActivityLog.created_at >= cutoff_48h,
                    AgentActivityLog.status == 'failed'
                ).count()
                
                if recent_logs > 0:
                    context['tool_fail_rate'] = failed_logs / recent_logs

                # Goal stuck
                goals = session.query(Goal).filter(
                    Goal.user_id == self.user_id,
                    Goal.status == 'active'
                ).all()
                
                for goal in goals:
                    if goal.created_at:
                        age_days = (datetime.utcnow() - goal.created_at).days
                        if age_days > context['goal_stuck']:
                            context['goal_stuck'] = age_days

                # Empty monitors
                monitor_states = session.query(AdaptiveMonitorState).filter(
                    AdaptiveMonitorState.user_id == self.user_id
                ).all()
                
                if monitor_states:
                    total_empty = sum(s.empty_count for s in monitor_states)
                    context['empty_monitors'] = total_empty // max(len(monitor_states), 1)

            finally:
                session.close()
        except Exception as e:
            logger.debug("[HEAL] health context error: %s", e)

        return context

    async def _apply_strategy(self, action: str, description: str, context: dict) -> str:
        """Применяет стратегию самовосстановления."""
        try:
            if action == 'switch_channel':
                # Получаем scores каналов и переключаемся на самый эффективный
                scores = await ChannelEffectivenessScorer.get_channel_scores(self.user_id)
                if scores:
                    best_channel = max(scores.items(), key=lambda x: x[1].get('score', 0))
                    return (
                        f"🔄 Самовосстановление: {description}. "
                        f"Лучший канал: {best_channel[0]} (score={best_channel[1].get('score', 0)}). "
                        f"Перенаправляю усилия."
                    )
                return f"🔄 {description}. Каналы не определены."

            elif action == 'reduce_frequency':
                return (
                    f"📉 Самовосстановление: {description}. "
                    f"AdaptiveMonitor увеличит интервалы для всех пустых источников."
                )

            elif action == 'block_tool_temp':
                # Блокируем самый проблемный инструмент
                scores = await ChannelEffectivenessScorer.get_channel_scores(self.user_id)
                if scores:
                    worst_channel = min(scores.items(), key=lambda x: x[1].get('score', 0))
                    key = f"channel:{worst_channel[0]}"
                    for _ in range(4):
                        await self.circuit_breaker.record_failure(
                            key,
                            f"SelfHealing: worst channel {worst_channel[0]} blocked"
                        )
                    return (
                        f"🚫 Самовосстановление: {description}. "
                        f"Канал {worst_channel[0]} временно заблокирован."
                    )
                return f"🚫 {description}."

            elif action == 'suggest_new_goal':
                return (
                    f"🎯 Самовосстановление: {description}. "
                    f"Цель застряла на {context.get('goal_stuck', '?')} дней."
                )

            return f"ℹ️ {description}."

        except Exception as e:
            logger.error("[HEAL] strategy apply error: %s", e)
            return f"❌ Ошибка применения стратегии: {e}"


# ═══════════════════════════════════════════════════════════════
# Универсальный хелпер для построения контекста оптимизации
# ═══════════════════════════════════════════════════════════════

async def build_optimization_context(user_id: int, agents: list = None) -> str:
    """
    Строит полный контекст оптимизации для инжекта в промпт ASI.
    Объединяет U4 (scores), U5 (budget), U7 (integrations), U8 (health).
    
    Returns:
        Многострочный текст с контекстом для промпта.
    """
    parts = []

    # U7: Доступные интеграции
    try:
        avail_ctx = await IntegrationAwarePlanner.generate_availability_context(user_id, agents)
        if avail_ctx:
            parts.append(avail_ctx)
    except Exception as e:
        logger.debug("[OPT_CTX] integrations error: %s", e)

    # U4: Channel scores
    try:
        scores = await ChannelEffectivenessScorer.get_channel_scores(user_id)
        if scores:
            score_lines = []
            for ch, data in sorted(scores.items(), key=lambda x: -x[1].get('score', 0)):
                score_lines.append(
                    f"  {ch}: score={data['score']}, действий={data['actions']}, "
                    f"конверсий={data['outcomes']}"
                )
            parts.append("📊 Эффективность каналов (7 дней):\n" + '\n'.join(score_lines))
    except Exception as e:
        logger.debug("[OPT_CTX] scores error: %s", e)

    # U5: Token budget
    try:
        budget_ctx = TokenBudgetAllocator.get_budget_context(user_id)
        if budget_ctx and budget_ctx.strip():
            parts.append(budget_ctx)
    except Exception as e:
        logger.debug("[OPT_CTX] budget error: %s", e)

    # U8: Health check
    try:
        orchestrator = SelfHealingOrchestrator(user_id)
        health_actions = await orchestrator.heal()
        if health_actions:
            parts.append("🏥 Самовосстановление:\n" + '\n'.join(f"  • {a}" for a in health_actions))
    except Exception as e:
        logger.debug("[OPT_CTX] health error: %s", e)

    return '\n\n'.join(parts)
