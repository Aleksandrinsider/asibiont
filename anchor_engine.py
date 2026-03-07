#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AnchorEngine — единая событийная система автономного агента.

Заменяет:
- timer-based проактивные сообщения (chat.py _build_situation_prompt, 15+ типов)
- contact_alerts_service.py
- auto_post_service.py (триггеры)
- context_builder.py алерты

Принцип работы:
1. SCAN  — каждые 15-30 мин сканирует ВСЕ источники данных, создаёт якоря
2. EVALUATE — AI получает сработавшие якоря + полный контекст, РЕШАЕТ писать или нет
3. DELIVER — отправляет ОДНО сообщение (не шаблон — AI пишет с нуля)
4. FEEDBACK — отслеживает реакцию пользователя, адаптирует частоту

Антиспам (живая динамика, НЕ блокировка):
- CRITICAL/HIGH: доставляются ВСЕГДА (кроме DND/ночь), не считаются в лимите
- MEDIUM: обычный cooldown 3ч, лимит 6 диалогов/день
- LOW: cooldown 8ч, отключаются при ignore rate >70%
- Посты в ленту: отдельный лимит 2/день
- Посты в канал: отдельный лимит 1/день
- Min gap 10 мин между проактивными (но не для CRITICAL)
- DND, ночные часы — единственный полный блок
- Макс 6 диалоговых + 2 feed + 1 channel = 9 касаний/день
"""

import asyncio
import json
import logging
import re
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytz

from sqlalchemy import text

from models import (
    Session, User, UserProfile, Task, Goal, Interaction, Post,
    Anchor, AnchorDeliveryLog, AnchorPriority,
    ActivityAlert, ContactAlert, UserMessage,
    EmailCampaign, EmailOutreach, ContentCampaign,
    DelegationCampaign, AgentActivityLog,
)
from config import DEEPSEEK_API_KEY, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════

# ── Лимиты доставок (единые, контроль расхода через токены) ──
# Токены — основной ограничитель. Лимиты — только anti-spam предохранитель.
MAX_DIALOG_PER_DAY = 8
MAX_FEED_PER_DAY = 1
MAX_CHANNEL_PER_DAY = 1
# CRITICAL/HIGH якоря НЕ считаются в лимите — доставляются всегда

NIGHT_START_HOUR = PROACTIVE_NO_SEND_START_HOUR  # Общая настройка: 22
MORNING_START_HOUR = PROACTIVE_SEND_START_HOUR   # Общая настройка: 10
SCAN_INTERVAL_MINUTES = 5

# Минимальный интервал между ПРОАКТИВНЫМИ сообщениями (не блокирует CRITICAL)
MIN_PROACTIVE_GAP_MINUTES = 10

# Если пользователь писал в последние N минут — НЕ отправлять проактивные (кроме CRITICAL)
ACTIVE_DIALOG_SUPPRESS_MINUTES = 3

# Cooldown по приоритету (часы)
PRIORITY_COOLDOWN = {
    AnchorPriority.CRITICAL: 0.5,   # 30 мин
    AnchorPriority.HIGH: 1.5,
    AnchorPriority.MEDIUM: 3,
    AnchorPriority.LOW: 4,
}

# Якоря, которые ВСЕГДА доставляются (кроме DND/ночь)
ALWAYS_DELIVER_TYPES = {
    'task_reminder',             # Точное напоминание по reminder_time
    'task_overdue',              # Просроченная задача — критично
    'task_deadline_soon',        # Дедлайн скоро — критично
    'delegation_update',         # Результат делегирования — пользователь ждёт
    'goal_deadline',             # Горящий дедлайн цели
    'incoming_message',          # Непрочитанные входящие сообщения
    'token_low_balance',         # Критически низкий баланс токенов
    'email_reply_received',      # Входящий ответ на email-кампанию — критически важно
    'payment_failed',            # Неудачная попытка пополнить токены
    'background_research_ready', # Фоновое исследование завершено — пользователь ждёт результат
    'agent_inbox_reply',         # Агент-почтовик нашёл новые входящие письма
    'agent_task_blocked',        # Агент застрял — нужно решение пользователя
    'agent_delegation',          # Результат работы агента по dispatch — пользователь ждёт
}

# Якоря, которые дополнительно ЗАПУСКАЮТ агента (event-driven dispatch)
# Ключ: тип якоря → шаблон задачи (плейсхолдеры: {goal}, {progress}, {task})
_AGENT_DISPATCH_TRIGGERS: dict[str, str] = {
    # ── Цели ──
    'goal_stagnation':    "Цель '{goal}' застряла на {progress}%. Проанализируй причины и предложи 2-3 конкретных действия чтобы сдвинуться с места. Используй свои интеграции.",
    'goal_decomposition': "Разбей цель '{goal}' на конкретные задачи на ближайшую неделю и создай их в системе.",
    'goal_deadline':      "До дедлайна цели '{goal}' остаётся мало времени. Определи что можно сделать прямо сейчас и действуй.",
    # ── Задачи ──
    'task_stale':         "Задача '{task}' давно не обновлялась. Проверь её статус, ускори или предложи делегировать.",
    'task_overdue':       "Задача '{task}' просрочена. Определи причину задержки и предложи план завершения или перенеси срок.",
    'task_deadline_soon': "До дедлайна задачи '{task}' осталось мало времени. Подготовь всё необходимое для завершения.",
    # ── Делегирование ──
    'delegation_update':  "Получен результат делегирования по задаче '{task}'. Проанализируй качество, добавь к задаче и предложи следующие шаги.",
    # ── Сервисы ──
    'service_degraded':   "Сервис деградирован: {task}. Проведи диагностику и предложи решение.",
    'agent_task_blocked': "Агент заблокирован на задаче '{task}'. Проанализируй причину блокировки и предложи решение.",
    # ── Кампании ──
    'campaign_stagnation': "Кампания '{task}' не показывает активности 3+ дня. Проанализируй эффективность и предложи корректировку.",
}

# Группы батчинга
BATCH_GROUPS = {
    'task_reminder': 'tasks',
    'task_overdue': 'tasks',
    'task_deadline_soon': 'tasks',
    'task_stale': 'tasks',
    'task_completed_streak': 'tasks',
    'goal_progress': 'goals',
    'goal_stagnation': 'goals',
    'goal_deadline': 'goals',
    'contact_match': 'contacts',
    'delegation_pending': 'delegation',
    'delegation_update': 'delegation',
    'market_insight': 'insights',
    'content_opportunity': 'insights',
    'profile_gap': 'engagement',
    'dialog_followup': 'engagement',
    'morning_plan': 'daily',
    'evening_review': 'daily',
    'task_result_check': 'tasks',
    'recurring_task_due': 'tasks',
    'post_opportunity': 'posting',
    'channel_post': 'posting',
    'discord_post': 'posting',
    'weekly_milestone': 'milestones',
    'goal_milestone': 'milestones',
    'event_discovery': 'insights',
    'contact_activity': 'contacts',
    'incoming_message': 'engagement',
    'token_low_balance': 'engagement',
    'delegation_overdue': 'delegation',
    'goal_decomposition': 'goals',
    'inactivity_reengagement': 'engagement',
    # Email outreach
    'email_outreach_send': 'email',
    'email_follow_up': 'email',
    'email_reply_received': 'email',
    'email_campaign_report': 'email',
    'email_need_leads': 'email',    # Кампании нужны новые контакты
    # Content campaigns
    'content_campaign_publish': 'content',
    # Delegation campaigns
    'delegation_campaign_send': 'delegation',
    'delegation_campaign_follow_up': 'delegation',
    # System
    'service_degraded': 'system',
    'payment_failed': 'system',
    'agent_script_failed': 'system',    # Сбой скрипта/ключей у пользовательского агента
    'weather_extreme': 'system',        # Экстремальная погода в городе пользователя
    # Background research
    'background_research_ready': 'insights',
    # Интеграции пользовательских агентов
    'integration_alert': 'integration',  # Gmail/Ozon/RSS/любые скрипты
    # Офисный координатор (Living Office Engine)
    'agent_office_update': 'integration',  # АСИ назначил агенту задачу по целям
    # Делегирование (результаты от директорского агента)
    'agent_delegation': 'office',          # Итог делегирования субагенту
    # Кастомные якоря из UserAgent.custom_anchors
    'custom_anchor': 'integration',        # Пользовательский триггер агента
    # Новые офисные якоря
    'agent_inbox_reply': 'office',         # Агент нашёл новые входящие письма (IMAP)
    'agent_task_blocked': 'office',        # Агент застрял, нужно решение пользователя
}


def _t(user, ru: str, en: str) -> str:
    """Pick anchor topic string based on user language."""
    lang = getattr(user, 'language', 'ru') or 'ru'
    return en if lang == 'en' else ru


class AnchorEngine:
    """
    Единый движок автономии. Сканирует → Оценивает → Доставляет.
    """

    def __init__(self, bot=None):
        self.bot = bot
        self.running = False
        self._scan_locks = defaultdict(asyncio.Lock)
        # Семафор для AI-вызовов — ограничивает параллельные запросы к DeepSeek
        # 12 = баланс между скоростью обработки 1000 юзеров и лимитами DeepSeek API
        # (autonomous_agent использует 20, anchor engine — фоновый, поэтому чуть меньше)
        self._ai_semaphore = asyncio.Semaphore(12)
        logger.info("[ANCHOR] AnchorEngine initialized")

    # ═══════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════

    async def start(self):
        """Запуск бесконечного цикла сканирования"""
        self.running = True
        self._cycle_counter = 0
        logger.info(f"[ANCHOR] 🚀 Starting scan loop (every {SCAN_INTERVAL_MINUTES}min)")
        # Стартовая задержка: даём серверу прогреться перед первым сканированием.
        # Это предотвращает лавину якорей / уведомлений сразу после деплоя.
        await asyncio.sleep(120)
        while self.running:
            try:
                import time as _time
                cycle_start = _time.monotonic()
                self._cycle_counter += 1
                logger.info(f"[ANCHOR] 🔄 Starting scan cycle #{self._cycle_counter}")
                await self._scan_all_users()
                cycle_duration = _time.monotonic() - cycle_start

                # Периодическое обслуживание: mark_ignored каждые ~12 циклов (~60 мин),
                # cleanup каждые ~144 цикла (~12 часов)
                if self._cycle_counter % 12 == 0:
                    try:
                        await self.mark_ignored_deliveries()
                    except Exception as _mie:
                        logger.debug(f"[ANCHOR] mark_ignored error: {_mie}")
                if self._cycle_counter % 144 == 0:
                    try:
                        await self.cleanup_old_anchors()
                    except Exception as _coe:
                        logger.debug(f"[ANCHOR] cleanup error: {_coe}")

                # Adaptive sleep: если цикл занял долго, спим меньше
                target_interval = SCAN_INTERVAL_MINUTES * 60
                sleep_time = max(60, target_interval - cycle_duration)  # минимум 1 мин
                logger.info(f"[ANCHOR] ✅ Scan cycle complete in {cycle_duration:.1f}s, sleeping {sleep_time:.0f}s")
                await asyncio.sleep(sleep_time)
            except Exception as e:
                logger.error(f"[ANCHOR] Loop error: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(300)

    async def stop(self):
        self.running = False
        logger.info("[ANCHOR] Stopped")

    async def _scan_all_users(self):
        """Двухфазный пайплайн: bulk pre-filter → parallel scan+eval
        
        При 1000 юзерах:
        - Phase 0: 1 запрос, отсеивает ~60% (ночь/DND) → ~400 eligible
        - Phase 1: DB-scan 10 параллельно, без AI → ~200ms/user → 400/10 × 0.2 = 8s
        - Phase 2: AI eval только для юзеров с ready anchors (~5%) → ~20 AI calls
        """
        session = Session()
        try:
            # ── PHASE 0: Массовый pre-filter (1 запрос к БД) ──
            users = session.query(User).filter(
                User.telegram_id.isnot(None)
            ).all()

            now_utc = datetime.now(timezone.utc)

            # Batch pre-load night-exception flags for all users (avoid 4×N queries)
            _pf_uids = [u.id for u in users]
            _night_exc_reminders = {row[0] for row in session.query(Task.user_id).filter(
                Task.user_id.in_(_pf_uids),
                Task.reminder_sent == False,
                Task.reminder_time <= now_utc,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).distinct().all()} if _pf_uids else set()
            _night_exc_unreplied = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailOutreach.status == 'replied',
                EmailOutreach.reply_text.isnot(None),
                EmailOutreach.ai_reply_sent_at.is_(None),
            ).distinct().all()} if _pf_uids else set()
            _night_exc_drafts = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailCampaign.status == 'active',
                EmailOutreach.status == 'draft',
            ).distinct().all()} if _pf_uids else set()
            _night_exc_followups = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailCampaign.status == 'active',
                EmailOutreach.status.in_(['sent', 'delivered', 'opened']),
                EmailOutreach.next_follow_up_at <= now_utc,
            ).distinct().all()} if _pf_uids else set()

            eligible = []
            skipped_night = 0
            skipped_dnd = 0

            for u in users:
                # DND check
                if u.do_not_disturb_until:
                    dnd = u.do_not_disturb_until
                    if dnd.tzinfo is None:
                        dnd = dnd.replace(tzinfo=timezone.utc)
                    if now_utc < dnd:
                        skipped_dnd += 1
                        continue

                # Night hours check
                try:
                    user_tz = pytz.timezone(u.timezone or 'Europe/Moscow')
                    user_now = datetime.now(user_tz)
                    if user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR:
                        # Use pre-loaded batch sets (no per-user queries)
                        has_pending_reminder = u.id in _night_exc_reminders
                        has_unreplied_email = u.id in _night_exc_unreplied
                        has_email_drafts = u.id in _night_exc_drafts
                        has_follow_ups = u.id in _night_exc_followups
                        if not has_pending_reminder and not has_unreplied_email and not has_email_drafts and not has_follow_ups:
                            skipped_night += 1
                            continue
                        else:
                            if has_pending_reminder:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has pending reminder, including")
                            if has_unreplied_email:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has unreplied email, including")
                            if has_email_drafts:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has email drafts to send (silent), including")
                            if has_follow_ups:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has follow-ups to send (silent), including")
                except Exception:
                    pass  # если timezone кривой — пропускаем pre-filter, проверим в _process_user_inner

                eligible.append(u.telegram_id)

            logger.info(
                f"[ANCHOR] Pre-filter: {len(users)} total → {len(eligible)} eligible "
                f"(skipped: {skipped_night} night, {skipped_dnd} DND)"
            )
        finally:
            session.close()

        # ── PHASE 1+2: Параллельная обработка eligible пользователей ──
        # DB-scan безопасен при высоком параллелизме, AI ограничен семафором
        BATCH_CONCURRENCY = 10
        for i in range(0, len(eligible), BATCH_CONCURRENCY):
            batch = eligible[i:i + BATCH_CONCURRENCY]
            tasks = []
            for uid in batch:
                lock = self._scan_locks[uid]
                if lock.locked():
                    continue
                tasks.append(self._process_user_safe(uid, lock))
            if tasks:
                await asyncio.gather(*tasks)

    async def _process_user_safe(self, user_id: int, lock: asyncio.Lock):
        """Обёртка с lock для безопасной параллельной обработки"""
        async with lock:
            try:
                await self._process_user(user_id)
            except Exception as e:
                logger.error(f"[ANCHOR] Error processing user {user_id}: {e}")

    async def _process_user(self, user_id: int):
        """Полный цикл для одного пользователя: scan → evaluate → deliver"""
        session = Session()
        try:
            # ── DB-LEVEL ADVISORY LOCK — атомарная защита от параллельных процессов ──
            # pg_try_advisory_lock не блокирует, а возвращает False если lock занят другим процессом
            # PostgreSQL advisory lock — атомарная защита от параллельных процессов
            # SQLite не поддерживает advisory locks — пропускаем
            lock_id = abs(user_id) % 2147483647
            use_advisory_lock = False
            try:
                lock_result = session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id}
                ).scalar()
                if not lock_result:
                    logger.debug(f"[ANCHOR] User {user_id}: ⛔ advisory lock busy (another process), skip")
                    return
                use_advisory_lock = True
            except Exception:
                # SQLite или другая БД без advisory locks — продолжаем без них
                pass

            try:
                await self._process_user_inner(user_id, session)
            finally:
                if use_advisory_lock:
                    try:
                        session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                        session.commit()
                    except Exception:
                        pass

        except Exception as e:
            logger.error(f"[ANCHOR] _process_user({user_id}) error: {e}\n{traceback.format_exc()}")
            session.rollback()
        finally:
            session.close()

    async def _process_user_inner(self, user_id: int, session):
        """Внутренняя логика обработки пользователя (под advisory lock)"""
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.debug(f"[ANCHOR] User {user_id}: не найден в БД, пропуск")
            return

        # Проверка баланса токенов (минимум на 1 проактивное сообщение)
        # НЕ блокируем полностью — email silent имеет отдельную проверку токенов
        from token_service import has_enough_tokens, get_balance
        from config import FREE_ACCESS_MODE
        has_proactive_tokens = True
        if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'proactive_message'):
            balance = get_balance(user_id)
            has_proactive_tokens = False
            logger.info(f"[ANCHOR] User {user_id}: ⚠️ недостаточно токенов для proactive (баланс: {balance}), dialog/posts заблокированы, email silent продолжит")

        # Проверка DND
        if user.do_not_disturb_until:
            dnd = user.do_not_disturb_until
            if dnd.tzinfo is None:
                dnd = dnd.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < dnd:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ DND до {dnd}, пропуск")
                return

        # Проверка ночных часов — НЕ блокируем полностью, а помечаем флагом
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        is_night = user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR
        if is_night:
            # Проверяем есть ли pending task reminders — если есть, продолжаем для них
            has_pending = session.query(Task).filter(
                Task.user_id == user.id,
                Task.reminder_sent == False,
                Task.reminder_time <= datetime.now(timezone.utc),
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).first() is not None
            # Проверяем есть ли непрочитанные email-ответы (CRITICAL — нельзя блокировать)
            has_unreplied_email = session.query(EmailOutreach).join(EmailCampaign).filter(
                EmailCampaign.user_id == user.id,
                EmailOutreach.status == 'replied',
                EmailOutreach.reply_text.isnot(None),
                EmailOutreach.ai_reply_sent_at.is_(None),
            ).first() is not None
            if not has_pending and not has_unreplied_email:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы ({user_now.strftime('%H:%M')} {user.timezone or 'Europe/Moscow'}, окно {MORNING_START_HOUR}:00-{NIGHT_START_HOUR}:00) — dialog/posts заблокированы, silent продолжат")
                # НЕ return — email silent / content / delegation обрабатываются ниже по is_night флагу
            if has_pending:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы, но есть pending reminders — обрабатываем только CRITICAL")
            if has_unreplied_email:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы, но есть unreplied email — обрабатываем email_reply_received")

        # ── Подсчёт доставок за сегодня (раздельно) ──
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        today_logs = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= today_start_utc
        ).all()

        dialog_count = 0
        post_count = 0
        channel_count = 0
        discord_count = 0
        for log in today_logs:
            try:
                types = json.loads(log.anchor_types) if log.anchor_types else []
            except (json.JSONDecodeError, TypeError):
                types = []
            if 'channel_post' in types:
                channel_count += 1
            elif 'discord_post' in types:
                discord_count += 1
            elif 'post_opportunity' in types:
                post_count += 1
            else:
                dialog_count += 1

        # ── Подавление проактивных во время активного диалога ──
        last_user_msg = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user'
        ).order_by(Interaction.created_at.desc()).first()

        active_dialog = False
        if last_user_msg:
            lm_time = last_user_msg.created_at
            if lm_time.tzinfo is None:
                lm_time = lm_time.replace(tzinfo=timezone.utc)
            since_last_msg = datetime.now(timezone.utc) - lm_time
            if since_last_msg < timedelta(minutes=ACTIVE_DIALOG_SUPPRESS_MINUTES):
                active_dialog = True
                logger.info(f"[ANCHOR] User {user_id}: 💬 active dialog ({since_last_msg.total_seconds():.0f}s ago) — suppress regular proactive")

        # ── Последнее проактивное сообщение (gap между ними, но НЕ блокирует CRITICAL) ──
        last_proactive = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'proactive'
        ).order_by(Interaction.created_at.desc()).first()

        proactive_gap_ok = True
        if last_proactive:
            lp_time = last_proactive.created_at
            if lp_time.tzinfo is None:
                lp_time = lp_time.replace(tzinfo=timezone.utc)
            gap = datetime.now(timezone.utc) - lp_time
            if gap < timedelta(minutes=MIN_PROACTIVE_GAP_MINUTES):
                proactive_gap_ok = False

        # 1. SCAN — обнаружить новые якоря
        new_anchors = await self._scan_anchors(user, session)
        if new_anchors:
            session.add_all(new_anchors)
            session.commit()
            logger.info(f"[ANCHOR] User {user_id}: created {len(new_anchors)} new anchors")
            # 1b. EVENT DISPATCH — новые goal-якоря запускают нужных агентов в фоне
            if not is_night:
                asyncio.ensure_future(
                    self._dispatch_agents_for_new_anchors(user, new_anchors)
                )

        # 2. EVALUATE — собрать доставляемые якоря
        deliverable = session.query(Anchor).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.is_(None),
            Anchor.triggered_at.isnot(None),
        ).order_by(
            Anchor.priority.asc(),  # CRITICAL first (enum order)
            Anchor.created_at.asc()
        ).limit(20).all()

        # ── 0. BACKGROUND RESEARCH — выполнить отложенные исследования ──
        bg_due = [a for a in deliverable if a.anchor_type == 'background_research'
                  and a.triggered_at <= datetime.now(timezone.utc)]
        if bg_due and not is_night:
            for bra in bg_due[:2]:
                async with self._ai_semaphore:
                    await self._process_background_research_anchor(user, bra, session)
        elif bg_due and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ background research deferred (night hours)")
        # Исключаем background_research из потока доставки — они выполняются тихо
        deliverable = [a for a in deliverable if a.anchor_type != 'background_research']

        logger.info(f"[ANCHOR] User {user_id}: найдено {len(deliverable)} deliverable якорей")

        # Фильтруем: не истёкшие + cooldown
        ready = [a for a in deliverable if a.is_deliverable()]
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после is_deliverable() — 0 ready (expired/suppressed)")
            return

        # ── STALENESS CHECK: задача/цель могла быть выполнена/удалена после создания якоря ──
        task_anchor_types = {'task_overdue', 'task_deadline_soon', 'task_stale', 'task_reminder', 'task_result_check'}
        goal_anchor_types = {'goal_stagnation', 'goal_progress', 'goal_deadline', 'goal_decomposition'}
        # Batch-load all referenced tasks (avoid N+1 per anchor)
        _stale_tids = []
        _stale_gids = []
        for _sa in ready:
            if _sa.anchor_type in task_anchor_types and _sa.source and _sa.source.startswith('task:'):
                try:
                    _stale_tids.append(int(_sa.source.split(':')[1]))
                except (ValueError, IndexError):
                    pass
            elif _sa.anchor_type in goal_anchor_types and _sa.source and _sa.source.startswith('goal:'):
                try:
                    _stale_gids.append(int(_sa.source.split(':')[1]))
                except (ValueError, IndexError):
                    pass
        _src_task_by_id = {t.id: t for t in session.query(Task).filter(Task.id.in_(_stale_tids)).all()} if _stale_tids else {}
        _src_goal_by_id = {g.id: g for g in session.query(Goal).filter(Goal.id.in_(_stale_gids)).all()} if _stale_gids else {}
        stale_ids = []
        for a in ready:
            if a.anchor_type in task_anchor_types and a.source and a.source.startswith('task:'):
                try:
                    tid = int(a.source.split(':')[1])
                except (ValueError, IndexError):
                    continue
                src_task = _src_task_by_id.get(tid)
                if not src_task or src_task.status in ('completed', 'deleted', 'cancelled'):
                    a.delivered_at = datetime.now(timezone.utc)  # auto-expire
                    stale_ids.append(a.id)
            elif a.anchor_type in goal_anchor_types and a.source and a.source.startswith('goal:'):
                try:
                    gid = int(a.source.split(':')[1])
                except (ValueError, IndexError):
                    continue
                src_goal = _src_goal_by_id.get(gid)
                if not src_goal or src_goal.status in ('completed', 'paused', 'cancelled', 'deleted'):
                    a.delivered_at = datetime.now(timezone.utc)
                    stale_ids.append(a.id)
        if stale_ids:
            session.commit()
            ready = [a for a in ready if a.id not in stale_ids]
            logger.info(f"[ANCHOR] User {user_id}: ♻️ auto-expired {len(stale_ids)} stale task anchors")
            if not ready:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ все якоря были stale")
                return

        ready = self._apply_cooldowns(ready, user, session)
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после _apply_cooldowns — 0 ready")
            return

        # ── Разделяем потоки ──
        EMAIL_SILENT_TYPES = {'email_outreach_send', 'email_follow_up', 'email_need_leads'}
        CONTENT_SILENT_TYPES = {'content_campaign_publish'}
        DELEGATION_SILENT_TYPES = {'delegation_campaign_send', 'delegation_campaign_follow_up'}
        critical_anchors = [a for a in ready if a.anchor_type in ALWAYS_DELIVER_TYPES
                            or a.priority in (AnchorPriority.CRITICAL, AnchorPriority.HIGH)]
        post_anchors = [a for a in ready if a.anchor_type in ('post_opportunity', 'channel_post', 'discord_post')]
        email_silent_anchors = [a for a in ready if a.anchor_type in EMAIL_SILENT_TYPES]
        content_silent_anchors = [a for a in ready if a.anchor_type in CONTENT_SILENT_TYPES]
        delegation_silent_anchors = [a for a in ready if a.anchor_type in DELEGATION_SILENT_TYPES]
        regular_anchors = [a for a in ready if a not in critical_anchors and a not in post_anchors and a not in email_silent_anchors and a not in content_silent_anchors and a not in delegation_silent_anchors]

        logger.info(f"[ANCHOR] User {user_id}: ready={len(ready)} (critical={len(critical_anchors)}, regular={len(regular_anchors)}, posts={len(post_anchors)}, email_silent={len(email_silent_anchors)}, content_silent={len(content_silent_anchors)}, deleg_silent={len(delegation_silent_anchors)}) dialog_count={dialog_count} gap_ok={proactive_gap_ok}")

        # ── 3. ЕДИНАЯ ДОСТАВКА — critical + regular в ОДНОМ сообщении ──
        all_dialog_anchors = critical_anchors.copy()
        if not has_proactive_tokens:
            # Нет токенов на proactive — блокируем regular, но critical всё равно доставляем
            if regular_anchors:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (insufficient tokens)")
        elif is_night:
            # Ночью — только CRITICAL/ALWAYS_DELIVER (task_reminder, task_overdue и т.д.)
            if regular_anchors:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (night hours)")
        elif regular_anchors and dialog_count < MAX_DIALOG_PER_DAY and proactive_gap_ok and not active_dialog:
            all_dialog_anchors.extend(regular_anchors)
        elif regular_anchors:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (dialog_count={dialog_count}/{MAX_DIALOG_PER_DAY}, gap_ok={proactive_gap_ok}, active_dialog={active_dialog})")

        if all_dialog_anchors and has_proactive_tokens:
            anchor_types = ', '.join(set(a.anchor_type for a in all_dialog_anchors))
            logger.info(f"[ANCHOR] User {user_id}: 🔥 AI deciding for {len(all_dialog_anchors)} anchors ({anchor_types})...")
            # AI semaphore — ограничивает параллельные DeepSeek запросы
            async with self._ai_semaphore:
                message = await self._ai_decide_and_compose(user, all_dialog_anchors, session)
            if message:
                await self._deliver(user, all_dialog_anchors, message, session)
                logger.info(f"[ANCHOR] User {user_id}: ✅ Delivered {len(all_dialog_anchors)} anchors in ONE message")
            else:
                logger.info(f"[ANCHOR] User {user_id}: AI decided SKIP for all dialog anchors")

        # ── 3c. FEED POSTS — отдельный лимит (не ночью, нужны токены) ──
        if not is_night and has_proactive_tokens:
            feed_posts = [a for a in post_anchors if a.anchor_type == 'post_opportunity']
            if feed_posts and post_count < MAX_FEED_PER_DAY:
                for pa in feed_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)

            # ── 3d. CHANNEL POSTS — отдельный лимит ──
            channel_posts = [a for a in post_anchors if a.anchor_type == 'channel_post']
            if channel_posts and channel_count < MAX_CHANNEL_PER_DAY:
                for pa in channel_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)

            # ── 3e. DISCORD POSTS — автономный постинг в Discord-канал ──
            discord_posts = [a for a in post_anchors if a.anchor_type == 'discord_post']
            if discord_posts and discord_count < MAX_CHANNEL_PER_DAY:
                for pa in discord_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)
        elif post_anchors:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ posts blocked (night hours)")

        # ── 3e. EMAIL SILENT — автономная отправка/follow-up (ВСЕГДА, без сообщений юзеру) ──
        # Email outreach/follow-up — тихие операции, не будят пользователя → работают 24/7
        if email_silent_anchors:
            logger.info(f"[ANCHOR] User {user_id}: 📧 Processing {len(email_silent_anchors)} email silent anchors (night={is_night})...")
            for _ea_idx, ea in enumerate(email_silent_anchors[:5]):  # макс 5 за цикл
                if _ea_idx > 0:
                    await asyncio.sleep(5)  # Краткая задержка между email-якорями
                async with self._ai_semaphore:
                    await self._process_email_silent_anchor(user, ea, session)

        # ── 3f. CONTENT CAMPAIGNS — автономная публикация по расписанию (не ночью) ──
        if content_silent_anchors and not is_night:
            logger.info(f"[ANCHOR] User {user_id}: 📝 Processing {len(content_silent_anchors)} content campaign anchors...")
            for _cc_idx, cc in enumerate(content_silent_anchors[:2]):  # макс 2 за цикл
                if _cc_idx > 0:
                    await asyncio.sleep(3)
                async with self._ai_semaphore:
                    await self._process_content_campaign_anchor(user, cc, session)
        elif content_silent_anchors and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ content campaigns blocked (night hours)")

        # ── 3g. DELEGATION CAMPAIGNS — автономное делегирование (не ночью) ──
        if delegation_silent_anchors and not is_night:
            logger.info(f"[ANCHOR] User {user_id}: 🤝 Processing {len(delegation_silent_anchors)} delegation campaign anchors...")
            for _dc_idx, dc in enumerate(delegation_silent_anchors[:3]):  # макс 3 за цикл
                if _dc_idx > 0:
                    await asyncio.sleep(5)
                async with self._ai_semaphore:
                    await self._process_delegation_campaign_anchor(user, dc, session)
        elif delegation_silent_anchors and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ delegation campaigns blocked (night hours)")

    # ═══════════════════════════════════════════════════════
    # BACKGROUND RESEARCH — выполнение отложенных исследований
    # ═══════════════════════════════════════════════════════

    async def _process_background_research_anchor(self, user, anchor, session):
        """Выполняет фоновое исследование и создаёт background_research_ready якорь для доставки."""
        try:
            data = anchor.data or {}
            query = data.get('query', '')
            if not query:
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
                return

            logger.info(f"[ANCHOR] User {user.id}: 🔍 executing background research: '{query[:60]}'")

            # Выполняем исследование
            from ai_integration.handlers import research_topic
            result = await research_topic(query, depth='full', user_id=user.id, session=session)
            result_str = ''
            if isinstance(result, dict):
                result_str = result.get('summary', '') or result.get('result', '') or str(result)
            else:
                result_str = str(result) if result else ''
            result_str = result_str[:3000]

            now_utc = datetime.now(timezone.utc)

            # Помечаем исходный якорь выполненным
            anchor.delivered_at = now_utc

            # Создаём якорь для доставки результата пользователю
            reason = data.get('reason', '')
            ready_anchor = Anchor(
                user_id=user.id,
                anchor_type='background_research_ready',
                source=f'background_research:{anchor.id}',
                topic=query[:200],
                priority=AnchorPriority.HIGH,
                data={'query': query, 'result': result_str, 'reason': reason},
                triggered_at=now_utc,
            )
            session.add(ready_anchor)

            # Логируем в AgentActivityLog (отображается в дашборде → Активность)
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='background_research',
                title=query[:200],
                content=query,
                status='completed',
                result=result_str[:500],
            )
            session.add(log_entry)

            session.commit()
            logger.info(f"[ANCHOR] User {user.id}: ✅ background_research done → ready anchor queued for '{query[:50]}'")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_background_research_anchor error: {e}")
            try:
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
            except Exception:
                session.rollback()

    # ═══════════════════════════════════════════════════════
    # EVENT-DRIVEN AGENT DISPATCH
    # ═══════════════════════════════════════════════════════

    async def _dispatch_agents_for_new_anchors(self, user, new_anchors: list):
        """
        Event-driven: когда AnchorEngine создаёт signal-якорь (goal_stagnation,
        goal_deadline, task_stale и т.д.), мы сразу находим подходящего агента
        и запускаем его — не ждём следующего цикла L2 координатора.

        Это заменяет «polling каждые 2-4ч» реакцией на конкретное событие.
        Fire-and-forget: не блокирует основной цикл доставки якорей.
        """
        trigger_anchors = [a for a in new_anchors if a.anchor_type in _AGENT_DISPATCH_TRIGGERS]
        if not trigger_anchors:
            return

        try:
            from models import Session as _Db, UserAgent as _UA, AgentActivityLog as _AAL
            from ai_integration.autonomous_agent import _exec_agent_for_director

            _s = _Db()
            try:
                agents = (
                    _s.query(_UA)
                    .filter_by(author_id=user.id, status='active')
                    .limit(10).all()
                )
                if not agents:
                    return

                for anchor in trigger_anchors:
                    # Guard: не повторяем dispatch для того же источника чаще раза в 4ч
                    recent_dispatch = (
                        _s.query(_AAL)
                        .filter(
                            _AAL.user_id == user.id,
                            _AAL.activity_type == 'agent_event_dispatch',
                            _AAL.target == anchor.source,
                            _AAL.created_at >= datetime.now(timezone.utc) - timedelta(hours=4),
                        )
                        .first()
                    )
                    if recent_dispatch:
                        continue

                    # Строим задачу из шаблона
                    try:
                        data = anchor.data or {}
                        task_text = _AGENT_DISPATCH_TRIGGERS[anchor.anchor_type].format(
                            goal=data.get('title', anchor.topic or 'без названия'),
                            progress=data.get('progress', 0),
                            task=data.get('title', anchor.topic or 'без названия'),
                        )
                    except Exception:
                        task_text = anchor.topic or anchor.anchor_type

                    # Выбираем агента: AI решает кто лучше подходит (fallback → keywords)
                    chosen = await self._pick_best_agent(agents, task_text, anchor.anchor_type)

                    # Логируем dispatch (cooldown guard)
                    _s.add(_AAL(
                        user_id=user.id,
                        activity_type='agent_event_dispatch',
                        title=f'[dispatch] {chosen.name} ← {anchor.anchor_type}',
                        content=task_text[:500],
                        target=anchor.source,
                        status='in_progress',
                        ref_id=chosen.id,
                    ))
                    _s.commit()

                    # Собираем agent_data
                    import json as _jd
                    agent_data = {
                        'id': chosen.id,
                        'name': chosen.name,
                        'job_title': chosen.job_title or '',
                        'specialization': chosen.specialization or '',
                        'description': chosen.description or '',
                        'personality': chosen.personality or '',
                        'python_code': chosen.python_code or '',
                        'user_api_keys': chosen.user_api_keys or '',
                        'tools_allowed': chosen.tools_allowed or '',
                        'search_scope': chosen.search_scope or '',
                        'avatar_url': chosen.avatar_url or '',
                        'tools': _jd.loads(chosen.tools_allowed or '[]'),
                    }

                    # Запускаем агента и при необходимости продолжаем цепочку
                    try:
                        result = await _exec_agent_for_director(
                            agent_data, task_text, user.telegram_id,
                        )
                        # Обновляем лог: выполнено
                        _s2 = _Db()
                        try:
                            _log = (
                                _s2.query(_AAL)
                                .filter_by(
                                    user_id=user.id,
                                    activity_type='agent_event_dispatch',
                                    target=anchor.source,
                                )
                                .order_by(_AAL.id.desc()).first()
                            )
                            if _log:
                                _log.status = 'completed'
                                _log.result = (result or '')[:400]
                            _s2.commit()
                        finally:
                            _s2.close()

                        logger.info(
                            "[ANCHOR-DISPATCH] user %d: %s triggered by %s → %d chars",
                            user.id, chosen.name, anchor.anchor_type, len(result or ''),
                        )

                        # ── Создаём якорь-уведомление для пользователя ──
                        if result and result.strip():
                            _s3 = _Db()
                            try:
                                _notify_anchor = Anchor(
                                    user_id=user.id,
                                    anchor_type='agent_delegation',
                                    source=f'dispatch:{chosen.name}:{anchor.anchor_type}',
                                    topic=f'{chosen.name} выполнил задачу: {task_text[:80]}',
                                    priority=AnchorPriority.HIGH,
                                    data=json.dumps({
                                        'agent_name': chosen.name,
                                        'agent_id': chosen.id,
                                        'task': task_text[:200],
                                        'result': (result or '')[:400],
                                    }, ensure_ascii=False),
                                    triggered_at=datetime.now(timezone.utc),
                                    expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
                                    cooldown_hours=2,
                                )
                                _s3.add(_notify_anchor)
                                _s3.commit()
                            finally:
                                _s3.close()

                        # ── ASI-продолжение: анализ результата → следующий агент ──
                        if result and len(result) > 30:
                            await self._maybe_continue_chain(
                                user, chosen, anchor, task_text, result, agents, _s,
                            )

                    except Exception as _exec_e:
                        logger.debug("[ANCHOR-DISPATCH] exec error: %s", _exec_e)
            finally:
                _s.close()
        except Exception as e:
            logger.debug("[ANCHOR-DISPATCH] dispatch error for user %d: %s", user.id, e)

    async def _pick_best_agent(self, agents, task_text: str, anchor_type: str):
        """AI выбирает лучшего агента для задачи.
        Fallback на keyword matching если AI недоступен."""
        if len(agents) == 1:
            return agents[0]

        try:
            from ai_integration.autonomous_agent import _quick_ai_call_raw
            agent_descs = '\n'.join(
                f'{i+1}. {a.name} — {a.job_title or ""} / {a.specialization or ""} / {(a.description or "")[:80]}'
                for i, a in enumerate(agents)
            )
            resp = await _quick_ai_call_raw([{
                "role": "user",
                "content": (
                    f"Задача: {task_text[:200]}\n"
                    f"Тип события: {anchor_type}\n\n"
                    f"Доступные агенты:\n{agent_descs}\n\n"
                    "Выбери ОДНОГО агента, который лучше всего подходит.\n"
                    "Ответь ТОЛЬКО номером агента (1, 2, 3...)."
                ),
            }], max_tokens=10)
            if resp:
                import re as _re_pick
                _m = _re_pick.search(r'\d+', resp.strip())
                if _m:
                    idx = int(_m.group()) - 1
                    if 0 <= idx < len(agents):
                        return agents[idx]
        except Exception:
            pass

        # Fallback: keyword matching
        ANALYTIC_KW = {'аналит', 'страте', 'исследо', 'план', 'маркет', 'консульт'}
        TASK_KW = {'задач', 'план', 'менедж', 'координ', 'ассист', 'помощн'}
        kw_set = ANALYTIC_KW if anchor_type in ('goal_stagnation', 'goal_decomposition', 'goal_deadline') else TASK_KW
        for ag in agents:
            spec = ((ag.specialization or '') + ' ' + (ag.description or '')).lower()
            if any(k in spec for k in kw_set):
                return ag
        return agents[0]

    async def _maybe_continue_chain(self, user, prev_agent, anchor, task_text, result, agents, session):
        """ASI анализирует результат агента и решает — нужен ли следующий шаг.

        Если задача не завершена или нужна экспертиза другого агента,
        запускает следующего агента напрямую.
        Максимум 3 продолжения на исходный якорь (контролируемая цепочка).
        """
        try:
            # Guard: не создаём цепочку длиннее 3 продолжений
            from models import AgentActivityLog as _AAL2
            _cont_count = (
                session.query(_AAL2)
                .filter(
                    _AAL2.user_id == user.id,
                    _AAL2.activity_type == 'agent_chain_continue',
                    _AAL2.target == anchor.source,
                    _AAL2.created_at >= datetime.now(timezone.utc) - timedelta(hours=6),
                )
                .count()
            )
            if _cont_count >= 3:
                return

            # Guard: если агент заблокирован — не продолжаем
            if result.strip().startswith('BLOCKED:'):
                return

            # ASI анализирует результат
            from ai_integration.autonomous_agent import _quick_ai_call_raw
            _analysis = await _quick_ai_call_raw([{
                "role": "user",
                "content": (
                    f"Задача: {task_text[:200]}\n"
                    f"Агент {prev_agent.name} ответил:\n{result[:400]}\n\n"
                    f"Доступные агенты: {', '.join(a.name for a in agents)}\n\n"
                    "Задача ПОЛНОСТЬЮ решена? Если да — ответь JSON: {\"continue\": false}\n"
                    "Если нужен следующий шаг другим агентом — ответь JSON:\n"
                    "{\"continue\": true, \"agent_name\": \"имя\", \"task\": \"что сделать\"}\n"
                    "JSON без ```:"
                ),
            }], max_tokens=120)

            if not _analysis:
                return

            # Парсим решение
            import re as _re2
            _m = _re2.search(r'\{[\s\S]*?\}', _analysis)
            if not _m:
                return
            _decision = json.loads(_m.group())
            if not _decision.get('continue'):
                return

            _next_name = _decision.get('agent_name', '')
            _next_task = _decision.get('task', '')
            if not _next_name or not _next_task:
                return

            # Находим следующего агента
            _next_ag = None
            for ag in agents:
                if ag.name.lower() == _next_name.lower():
                    _next_ag = ag
                    break
            if not _next_ag:
                return

            # Логируем continuation
            from models import AgentActivityLog as _AAL3
            session.add(_AAL3(
                user_id=user.id,
                activity_type='agent_chain_continue',
                title=f'[chain] {prev_agent.name} → {_next_ag.name}',
                content=_next_task[:500],
                target=anchor.source,
                status='in_progress',
                ref_id=_next_ag.id,
            ))
            session.commit()

            # Запускаем следующего агента
            from ai_integration.autonomous_agent import _exec_agent_for_director
            import json as _jd2
            _next_data = {
                'id': _next_ag.id,
                'name': _next_ag.name,
                'job_title': _next_ag.job_title or '',
                'specialization': _next_ag.specialization or '',
                'description': _next_ag.description or '',
                'personality': _next_ag.personality or '',
                'python_code': _next_ag.python_code or '',
                'user_api_keys': _next_ag.user_api_keys or '',
                'tools_allowed': _next_ag.tools_allowed or '',
                'search_scope': getattr(_next_ag, 'search_scope', '') or '',
                'avatar_url': _next_ag.avatar_url or '',
                'tools': _jd2.loads(_next_ag.tools_allowed or '[]'),
            }
            _ctx = f"Предыдущий результат от {prev_agent.name}:\n{result[:300]}"
            _next_result = await _exec_agent_for_director(
                _next_data, _next_task, user.telegram_id, dialog_context=_ctx,
            )

            # Создаём якорь-уведомление о продолжении
            if _next_result and _next_result.strip():
                _s4 = Session()
                try:
                    _s4.add(Anchor(
                        user_id=user.id,
                        anchor_type='agent_delegation',
                        source=f'chain:{_next_ag.name}:{anchor.anchor_type}',
                        topic=f'{_next_ag.name} продолжил работу: {_next_task[:80]}',
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({
                            'agent_name': _next_ag.name,
                            'agent_id': _next_ag.id,
                            'task': _next_task[:200],
                            'result': (_next_result or '')[:400],
                            'chain_from': prev_agent.name,
                        }, ensure_ascii=False),
                        triggered_at=datetime.now(timezone.utc),
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
                        cooldown_hours=2,
                    ))
                    _s4.commit()
                finally:
                    _s4.close()

            logger.info(
                "[ANCHOR-CHAIN] user %d: %s → %s (task: %s) → %d chars",
                user.id, prev_agent.name, _next_ag.name, _next_task[:50], len(_next_result or ''),
            )

        except Exception as _chain_e:
            logger.debug("[ANCHOR-CHAIN] error for user %d: %s", user.id, _chain_e)

    # ═══════════════════════════════════════════════════════
    # SCAN — обнаружение якорей
    # ═══════════════════════════════════════════════════════

    async def _scan_anchors(self, user, session) -> list:
        """Сканирует ВСЕ источники данных, создаёт якоря.
        
        Не создаёт дубликаты — проверяет наличие необработанного якоря того же типа+source.
        """
        anchors = []

        # Получаем профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        now_utc = datetime.now(timezone.utc)

        # --- ЗАДАЧИ ---
        anchors.extend(self._scan_tasks(user, session, user_tz, user_now, now_utc))

        # --- ЦЕЛИ ---
        anchors.extend(self._scan_goals(user, session, now_utc))

        # --- ПРОФИЛЬ ---
        anchors.extend(self._scan_profile(user, profile, session))

        # --- ДЕЛЕГИРОВАНИЕ (открыто всем — оплата токенами) ---
        anchors.extend(self._scan_delegation(user, session, now_utc))

        # --- КОНТАКТЫ ---
        anchors.extend(self._scan_contacts(user, session, now_utc))

        # --- ДИАЛОГ (follow-up из LTM) ---
        anchors.extend(self._scan_dialog_followup(user, session, now_utc))

        # --- УТРО/ВЕЧЕР ---
        anchors.extend(self._scan_daily_rhythm(user, session, user_now))

        # --- РЫНОК/КОНТЕНТ (открыто всем) ---
        anchors.extend(self._scan_premium_insights(user, profile, session, now_utc))

        # --- СОБЫТИЯ / МЕРОПРИЯТИЯ ---
        anchors.extend(self._scan_events(user, profile, session, now_utc))

        # --- ВХОДЯЩИЕ СООБЩЕНИЯ ---
        anchors.extend(self._scan_incoming_messages(user, session, now_utc))

        # --- НИЗКИЙ БАЛАНС ТОКЕНОВ ---
        anchors.extend(self._scan_token_low_balance(user, session, now_utc))

        # --- ПРОСРОЧЕННЫЕ ДЕЛЕГИРОВАНИЯ ---
        anchors.extend(self._scan_delegation_overdue(user, session, now_utc))

        # --- ДЕКОМПОЗИЦИЯ ЦЕЛЕЙ БЕЗ ЗАДАЧ ---
        anchors.extend(self._scan_goal_decomposition(user, session, now_utc))

        # --- РЕАКТИВАЦИЯ НЕАКТИВНЫХ ---
        anchors.extend(self._scan_inactivity_reengagement(user, session, now_utc))

        # --- ПОСТЫ В ЛЕНТУ (все) ---
        anchors.extend(self._scan_post_opportunities(user, profile, session, now_utc))

        # --- ПОСТЫ В КАНАЛ (если указан канал) ---
        if user.telegram_channel:
            anchors.extend(self._scan_channel_post(user, profile, session, now_utc))

        # --- ПОСТЫ В DISCORD (если настроен webhook) ---
        if getattr(user, 'discord_webhook', None):
            anchors.extend(self._scan_discord_post(user, profile, session, now_utc))

        # --- МАЙЛСТОНЫ: недельные итоги + прогресс целей ---
        anchors.extend(self._scan_weekly_milestone(user, session, now_utc))
        anchors.extend(self._scan_goal_milestone(user, session, now_utc))

        # --- EMAIL OUTREACH (автономная отправка + фоллоу-апы + уведомления о reply) ---
        anchors.extend(self._scan_email_outreach(user, session, now_utc))

        # --- КОНТЕНТ-КАМПАНИИ (автономная публикация по расписанию) ---
        anchors.extend(self._scan_content_campaigns(user, session, now_utc))

        # --- КАМПАНИИ ДЕЛЕГИРОВАНИЯ (автономное распределение задач) ---
        anchors.extend(self._scan_delegation_campaigns(user, session, now_utc))

        # --- ДЕГРАДАЦИЯ СЕРВИСОВ (service_health) ---
        anchors.extend(self._scan_service_degraded(user, session, now_utc))

        # --- СБОИ СКРИПТОВ АГЕНТОВ (сломанные ключи/интеграции) ---
        anchors.extend(self._scan_agent_script_failures(user, session, now_utc))

        # --- АГЕНТЫ БЕЗ РЕЗУЛЬТАТОВ (скрипт работает, но stdout пуст N раз подряд) ---
        anchors.extend(self._scan_agent_silent(user, session, now_utc))

        # --- СТАГНАЦИЯ КАМПАНИЙ (email/контент/делегирование — активна, но 0 активности 3+ дня) ---
        anchors.extend(self._scan_campaign_stagnation(user, session, now_utc))

        # --- ЭКСТРЕМАЛЬНАЯ ПОГОДА ---
        anchors.extend(await self._scan_weather_extreme(user, profile, now_utc))

        # --- НЕУДАЧНЫЕ ПЛАТЕЖИ ---
        anchors.extend(self._scan_payment_failed(user, session, now_utc))

        # --- КАСТОМНЫЕ ЯКОРЯ АГЕНТОВ (UserAgent.custom_anchors) ---
        anchors.extend(self._scan_custom_anchors(user, session, user_tz, user_now, now_utc))

        # --- ВХОДЯЩИЕ ПИСЬМА АГЕНТОВ (IMAP) ---
        anchors.extend(self._scan_agent_inbox_replies(user, session, now_utc))

        # --- ЗАБЛОКИРОВАННЫЕ АГЕНТЫ (нужно решение пользователя) ---
        anchors.extend(self._scan_agent_task_blocked(user, session, now_utc))

        # --- FOLLOW-UP РЕЗУЛЬТАТОВ АГЕНТОВ (проверка незакрытых dispatch-задач) ---
        anchors.extend(self._scan_agent_followup(user, session, now_utc))

        # --- DDG WEB ENRICHMENT: обогащаем якоря реальными данными из интернета ---
        anchors = await self._enrich_anchors_with_ddg(anchors, profile)

        # Дедупликация: не создаём якорь если уже есть недоставленный с тем же type+source
        # with_for_update() сериализует запись между двумя параллельными инстансами (Railway deploy)
        try:
            try:
                existing = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.delivered_at.is_(None)
                ).with_for_update(nowait=True).all()
            except Exception:
                # SQLite не поддерживает FOR UPDATE / nowait — fallback без блокировки
                existing = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.delivered_at.is_(None)
                ).all()
        except Exception:
            logger.info(f"[ANCHOR] User {user.id}: scan skipped (locked by another instance)")
            return []
        existing_keys = {(a.anchor_type, a.source) for a in existing}

        unique_anchors = []
        for a in anchors:
            key = (a.anchor_type, a.source)
            if key not in existing_keys:
                existing_keys.add(key)
                unique_anchors.append(a)

        return unique_anchors

    async def _enrich_anchors_with_ddg(self, anchors: list, profile) -> list:
        """Обогащает якоря реальными данными из DuckDuckGo.

        Затрагивает типы: event_discovery, market_insight, content_opportunity.
        Добавляет результаты поиска прямо в data якоря, чтобы AI получил конкретные факты.
        Бюджет: ~3-5 DDG-запросов на пользователя за скан, кэш 2ч.
        """
        try:
            from ai_integration.api_client import get_api_client
            api = get_api_client()

            for anchor in anchors:
                try:
                    data = json.loads(anchor.data) if anchor.data else {}
                except (json.JSONDecodeError, TypeError):
                    continue

                enriched = False

                if anchor.anchor_type == 'event_discovery':
                    # Выполняем search_query, который уже сформирован в _scan_events
                    query = data.get('search_query', '')
                    city = data.get('city', '')
                    if query:
                        if city and city.lower() not in query.lower():
                            query += f' {city}'
                        results = await api.duckduckgo_search(query, num=5, cache_ttl=7200)
                        if results:
                            data['web_events'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] event_discovery enriched with {len(results)} results")

                elif anchor.anchor_type == 'market_insight':
                    niche = data.get('niche', '')
                    if niche:
                        from datetime import datetime as dt
                        year = dt.now().strftime('%Y')
                        news_query = f'{niche[:50]} новости тренды {year}'
                        results = await api.duckduckgo_search(news_query, num=5, cache_ttl=7200)
                        if results:
                            data['fresh_insights'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] market_insight enriched with {len(results)} results")

                elif anchor.anchor_type == 'content_opportunity':
                    niche = data.get('niche', '')
                    content_strategy = data.get('content_strategy', '')
                    topic = content_strategy[:50] if content_strategy else niche[:50]
                    if topic:
                        ideas_query = f'{topic} контент идеи тренды'
                        results = await api.duckduckgo_search(ideas_query, num=5, cache_ttl=7200)
                        if results:
                            data['content_ideas_from_web'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] content_opportunity enriched with {len(results)} results")

                if enriched:
                    anchor.data = json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"[ANCHOR-DDG] Enrichment failed (non-critical): {e}")

        return anchors

    def _scan_tasks(self, user, session, user_tz, user_now, now_utc) -> list:
        """Сканирует задачи: просроченные, ближайшие дедлайны, застойные"""
        anchors = []

        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'in_progress', 'active'])
        ).all()

        for task in tasks:
            if task.reminder_time:
                rt = task.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)

                minutes_diff = (rt - now_utc).total_seconds() / 60

                # ТОЧНОЕ НАПОМИНАНИЕ: reminder_time наступило (от 0 до -30 мин) и ещё не отправлено
                if -30 <= minutes_diff <= 0 and not getattr(task, 'reminder_sent', False):
                    scan_delay = int(abs(minutes_diff))  # 0..30 мин — шаг сканирования, НЕ просрочка
                    try:
                        rt_local = rt.astimezone(user_tz)
                        sched_time_str = rt_local.strftime('%H:%M')
                    except Exception:
                        sched_time_str = '??:??'
                    if scan_delay <= 2:
                        reminder_topic = _t(user,
                            f'Напоминание: задача «{task.title}» — запланировано {sched_time_str}, сработало точно по расписанию',
                            f'Reminder: task «{task.title}» — scheduled {sched_time_str}, triggered on time')
                    else:
                        reminder_topic = _t(user,
                            f'Напоминание: задача «{task.title}» — запланировано {sched_time_str}, задержка {scan_delay} мин из-за шага сканирования (НЕ просрочено)',
                            f'Reminder: task «{task.title}» — scheduled {sched_time_str}, {scan_delay}min scan delay (NOT overdue)')
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_reminder',
                        source=f'task:{task.id}',
                        topic=reminder_topic,
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'description': (task.description or '')[:200],
                                        'reminder_type': 'exact',
                                        'scheduled_time': sched_time_str,
                                        'scan_delay_minutes': scan_delay}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(minutes=90),  # 90 мин — запас на cold start
                        cooldown_hours=0.5,
                        batch_group='tasks',
                    ))
                    # reminder_sent НЕ ставим здесь — ставим только при доставке
                    # (иначе если якорь истечёт до доставки — напоминание потеряно навсегда)
                    # Дедупликация обеспечивается existing_keys в _scan_anchors

                # ПРОСРОЧЕННЫЕ (более 30 мин назад)
                # Доставляем ВСЕГДА — reminder_sent ставим при реальной доставке, не здесь
                elif minutes_diff < -30:
                    hours_overdue = abs(minutes_diff) / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_overdue',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» просрочена на {int(hours_overdue)}ч', f'Task «{task.title}» overdue by {int(hours_overdue)}h'),
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_overdue': round(hours_overdue, 1),
                                        'description': (task.description or '')[:200]}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=2,
                        batch_group='tasks',
                    ))

                # ДЕДЛАЙН СКОРО (от 15 мин до 24ч до reminder_time)
                # Нижний порог 15 мин — ближе этого task_reminder сам справится
                elif 15 <= minutes_diff <= 24 * 60:
                    hours_left = minutes_diff / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_deadline_soon',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» — дедлайн через {int(hours_left)}ч', f'Task «{task.title}» — deadline in {int(hours_left)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_left': round(hours_left, 1)}),
                        triggered_at=now_utc,
                        expires_at=rt,
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

            # due_date без reminder_time — проверяем просрочку/дедлайн по due_date
            elif task.due_date and not task.reminder_time:
                dd = task.due_date
                if dd.tzinfo is None:
                    dd = dd.replace(tzinfo=timezone.utc)
                minutes_diff_dd = (dd - now_utc).total_seconds() / 60
                if minutes_diff_dd < -30:
                    hours_overdue = abs(minutes_diff_dd) / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_overdue',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» просрочена на {int(hours_overdue)}ч', f'Task «{task.title}» overdue by {int(hours_overdue)}h'),
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_overdue': round(hours_overdue, 1),
                                        'description': (task.description or '')[:200]}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=2,
                        batch_group='tasks',
                    ))
                elif 15 <= minutes_diff_dd <= 24 * 60:
                    hours_left = minutes_diff_dd / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_deadline_soon',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» — дедлайн через {int(hours_left)}ч', f'Task «{task.title}» — deadline in {int(hours_left)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_left': round(hours_left, 1)}),
                        triggered_at=now_utc,
                        expires_at=dd,
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

            # Застойные: задача создана > 7 дней назад, без прогресса
            if task.created_at:
                ct = task.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days >= 7:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_stale',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» висит уже {age_days} дней', f'Task «{task.title}» stale for {age_days} days'),
                        priority=AnchorPriority.LOW,
                        data=json.dumps({'task_id': task.id, 'title': task.title, 'age_days': age_days}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(days=3),
                        cooldown_hours=24,
                        batch_group='tasks',
                    ))

            # ПРОВЕРКА РЕЗУЛЬТАТА: задача с reminder_sent, estimated_duration, не проверена
            if (getattr(task, 'reminder_sent', False) 
                and getattr(task, 'estimated_duration', None)
                and not getattr(task, 'result_check_sent', False)):
                rt = task.reminder_time
                if rt and rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                if rt:
                    result_check_time = rt + timedelta(minutes=task.estimated_duration)
                    if now_utc >= result_check_time:
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='task_result_check',
                            source=f'task:{task.id}:result',
                            topic=_t(user, f'Время проверить результат задачи «{task.title}»', f'Time to check results for task «{task.title}»'),
                            priority=AnchorPriority.MEDIUM,
                            data=json.dumps({'task_id': task.id, 'title': task.title,
                                            'estimated_duration': task.estimated_duration}),
                            triggered_at=now_utc,
                            expires_at=now_utc + timedelta(hours=12),
                            cooldown_hours=6,
                            batch_group='tasks',
                        ))

        # Повторяющиеся задачи: проверяем нужно ли создать новый экземпляр
        recurring_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.is_recurring == True,
            Task.status.in_(['pending', 'in_progress', 'active', 'completed'])
        ).all()

        # Batch-load all child task instances for recurring tasks (avoid N+1)
        _recur_ids = [rt.id for rt in recurring_tasks]
        _recur_children_all = session.query(Task).filter(
            Task.parent_task_id.in_(_recur_ids)
        ).order_by(Task.reminder_time.desc()).all() if _recur_ids else []
        # latest child per parent (desc order → first seen = latest)
        _recur_last_by_parent: dict = {}
        _recur_children_by_parent: dict = {}
        for _rc in _recur_children_all:
            if _rc.parent_task_id not in _recur_last_by_parent:
                _recur_last_by_parent[_rc.parent_task_id] = _rc
            _recur_children_by_parent.setdefault(_rc.parent_task_id, []).append(_rc)

        for rtask in recurring_tasks:
            if rtask.reminder_time and rtask.recurrence_pattern:
                rt = rtask.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                # Проверяем: последний экземпляр уже в прошлом?
                last_instance = _recur_last_by_parent.get(rtask.id)
                
                last_time = last_instance.reminder_time if last_instance else rt
                if last_time and last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                
                if last_time and last_time < now_utc:
                    # Создаём новый экземпляр повторяющейся задачи
                    next_time = self._calculate_next_recurrence(last_time, rtask.recurrence_pattern, rtask.recurrence_interval or 1)
                    # Проверяем что такой экземпляр ещё не создан (используем предзагруженные дочерние задачи)
                    existing = next(
                        (_c for _c in _recur_children_by_parent.get(rtask.id, []) if _c.reminder_time == next_time),
                        None
                    )
                    if not existing:
                        new_task = Task(
                            user_id=rtask.user_id,
                            title=rtask.title,
                            description=rtask.description,
                            reminder_time=next_time,
                            parent_task_id=rtask.id
                        )
                        session.add(new_task)
                        try:
                            session.commit()
                            logger.info(f"[ANCHOR] Created recurring instance for task {rtask.id}: '{rtask.title}' at {next_time}")
                        except Exception:
                            session.rollback()
                    
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='recurring_task_due',
                        source=f'task:{rtask.id}:recurring',
                        topic=_t(user, f'Повторяющаяся задача «{rtask.title}» — создан новый экземпляр', f'Recurring task «{rtask.title}» — new instance created'),
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({'task_id': rtask.id, 'title': rtask.title,
                                        'pattern': rtask.recurrence_pattern,
                                        'interval': rtask.recurrence_interval or 1}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=12),
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

        # Стрик завершений: если за последние 24ч завершено >= 3 задач
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).count()

        if recent_completed >= 3:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='task_completed_streak',
                source=f'streak:{recent_completed}',
                topic=_t(user, f'За последние 24ч завершено {recent_completed} задач', f'{recent_completed} tasks completed in the last 24h'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'completed_count': recent_completed}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=12),
                cooldown_hours=24,
                batch_group='tasks',
            ))

        return anchors

    def _scan_goals(self, user, session, now_utc) -> list:
        """Сканирует цели: прогресс, застой, горящие дедлайны"""
        anchors = []

        goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active'
        ).all()

        for goal in goals:
            # Почти достигнута (>= 70%)
            if goal.progress_percentage >= 70 and goal.progress_percentage < 100:
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='goal_progress',
                    source=f'goal:{goal.id}',
                    topic=_t(user, f'Цель «{goal.title}» на {goal.progress_percentage}% — почти!', f'Goal «{goal.title}» at {goal.progress_percentage}% — almost there!'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                    'progress': goal.progress_percentage}),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(days=2),
                    cooldown_hours=48,
                    batch_group='goals',
                ))

            # Застой: создана > 14 дней, прогресс 0%
            if goal.created_at and goal.progress_percentage == 0:
                ct = goal.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days >= 14:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='goal_stagnation',
                        source=f'goal:{goal.id}',
                        topic=_t(user, f'Цель «{goal.title}» — {age_days} дней без прогресса', f'Goal «{goal.title}» — {age_days} days without progress'),
                        priority=AnchorPriority.LOW,
                        data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                        'age_days': age_days}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(days=7),
                        cooldown_hours=72,
                        batch_group='goals',
                    ))

            # Горящий дедлайн (< 3 дней)
            if goal.target_date:
                td = goal.target_date
                if td.tzinfo is None:
                    td = td.replace(tzinfo=timezone.utc)
                days_left = (td - now_utc).days
                if 0 <= days_left <= 3:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='goal_deadline',
                        source=f'goal:{goal.id}',
                        topic=_t(user, f'Цель «{goal.title}» — дедлайн через {days_left} дн, прогресс {goal.progress_percentage}%', f'Goal «{goal.title}» — deadline in {days_left}d, progress {goal.progress_percentage}%'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                        'days_left': days_left, 'progress': goal.progress_percentage}),
                        triggered_at=now_utc,
                        expires_at=td,
                        cooldown_hours=12,
                        batch_group='goals',
                    ))

        return anchors

    def _scan_profile(self, user, profile, session) -> list:
        """Проверяет пробелы в профиле"""
        anchors = []
        now_utc = datetime.now(timezone.utc)

        if not profile:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='profile_gap',
                source='profile:empty',
                topic='Профиль не заполнен — агент не может эффективно помогать' if getattr(user, 'language', 'ru') != 'en' else 'Profile is empty — agent cannot help effectively',
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'missing': ['skills', 'interests', 'goals', 'city', 'position']}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=48,
                batch_group='engagement',
            ))
            return anchors

        missing = []
        _is_en = getattr(user, 'language', 'ru') == 'en'
        if not profile.skills or not profile.skills.strip():
            missing.append('skills' if _is_en else 'навыки')
        if not profile.interests or not profile.interests.strip():
            missing.append('interests' if _is_en else 'интересы')
        if not profile.goals or not profile.goals.strip():
            missing.append('goals' if _is_en else 'цели')

        if len(missing) >= 2:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='profile_gap',
                source=f'profile:missing:{",".join(missing)}',
                topic=_t(user, f'В профиле не хватает: {", ".join(missing)}', f'Profile missing: {", ".join(missing)}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({'missing': missing}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=72,
                batch_group='engagement',
            ))

        return anchors

    def _scan_delegation(self, user, session, now_utc) -> list:
        """Сканирует статус делегированных задач (STANDARD+)"""
        anchors = []

        # Задачи, делегированные ПОЛЬЗОВАТЕЛЕМ, со статусом pending (не принято)
        pending_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'pending',
            Task.status.in_(['pending', 'in_progress'])
        ).all()

        for task in pending_delegated:
            if task.created_at:
                ct = task.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                hours_waiting = (now_utc - ct).total_seconds() / 3600
                if hours_waiting >= 4:  # Ждёт > 4ч
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='delegation_pending',
                        source=f'task:{task.id}:delegation',
                        topic=_t(user, f'Делегированная задача «{task.title}» → @{task.delegated_to_username} — ждёт ответа {int(hours_waiting)}ч', f'Delegated task «{task.title}» → @{task.delegated_to_username} — waiting {int(hours_waiting)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'delegated_to': task.delegated_to_username,
                                        'hours_waiting': round(hours_waiting, 1)}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=6,
                        batch_group='delegation',
                    ))

        # Задачи с обновлённым статусом делегирования (accepted/completed/rejected)
        updated_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(['accepted', 'completed', 'rejected']),
            Task.status.in_(['pending', 'in_progress'])
        ).all()

        for task in updated_delegated:
            # Для rejected — HIGH приоритет, короткий cooldown
            is_rejected = task.delegation_status == 'rejected'
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='delegation_update',
                source=f'task:{task.id}:status:{task.delegation_status}',
                topic=_t(user, f'Задача «{task.title}» — @{task.delegated_to_username} {task.delegation_status}', f'Task «{task.title}» — @{task.delegated_to_username} {task.delegation_status}'),
                priority=AnchorPriority.HIGH if is_rejected else AnchorPriority.HIGH,
                data=json.dumps({'task_id': task.id, 'title': task.title,
                                'delegated_to': task.delegated_to_username,
                                'status': task.delegation_status}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=4 if is_rejected else 12,
                batch_group='delegation',
            ))

        return anchors

    def _scan_contacts(self, user, session, now_utc) -> list:
        """Сканирует алерты контактов (все тарифы)"""
        anchors = []

        contact_alerts = session.query(ContactAlert).filter_by(
            user_id=user.id, enabled=True
        ).all()

        if not contact_alerts:
            return anchors

        # Недавно обновлённые профили
        yesterday = now_utc - timedelta(days=1)
        recent_profiles = session.query(UserProfile).filter(
            UserProfile.user_id != user.id,
            UserProfile.updated_at >= yesterday
        ).limit(20).all()

        # Batch-load users for recent_profiles (avoid N+1 inside nested loops)
        _cp_prof_uids = [p.user_id for p in recent_profiles]
        _cp_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_cp_prof_uids)).all()} if _cp_prof_uids else {}

        for alert in contact_alerts[:3]:
            for prof in recent_profiles:
                match = False
                if alert.skill and prof.skills and alert.skill.lower() in prof.skills.lower():
                    match = True
                if alert.interest and prof.interests and alert.interest.lower() in prof.interests.lower():
                    match = True
                if match and alert.city:
                    # Check city using all normalized variants (cross-language: EN/RU/raw)
                    alert_city_lc = alert.city.strip().lower()
                    prof_city_variants = set(filter(None, [
                        (getattr(prof, 'city_normalized', None) or '').strip().lower(),
                        (getattr(prof, 'city_normalized_ru', None) or '').strip().lower(),
                        (prof.city or '').strip().lower(),
                    ]))
                    city_match = any(
                        alert_city_lc in v or v.startswith(alert_city_lc) or alert_city_lc.startswith(v)
                        for v in prof_city_variants if v
                    )
                    if not city_match:
                        match = False

                if match:
                    prof_user = _cp_user_by_id.get(prof.user_id)
                    if prof_user and prof_user.username:
                        detail = alert.skill or alert.interest
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='contact_match',
                            source=f'contact:@{prof_user.username}',
                            topic=_t(user, f'Новый специалист @{prof_user.username} ({detail})', f'New specialist @{prof_user.username} ({detail})'),
                            priority=AnchorPriority.MEDIUM,
                            data=json.dumps({
                                'username': prof_user.username,
                                'skill': alert.skill,
                                'interest': alert.interest,
                                'city': prof.city,
                                'position': prof.position
                            }),
                            triggered_at=now_utc,
                            expires_at=now_utc + timedelta(days=2),
                            cooldown_hours=24,
                            batch_group='contacts',
                        ))
                        break  # Один контакт за алерт

        return anchors

    def _scan_dialog_followup(self, user, session, now_utc) -> list:
        """Проверяет незавершённые темы из истории диалога"""
        anchors = []

        # Последнее сообщение пользователя
        last_user_msg = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user'
        ).order_by(Interaction.created_at.desc()).first()

        if not last_user_msg:
            return anchors

        li_time = last_user_msg.created_at
        if li_time.tzinfo is None:
            li_time = li_time.replace(tzinfo=timezone.utc)

        hours_since = (now_utc - li_time).total_seconds() / 3600

        # Если прошло 6-48ч — это хороший момент для follow-up
        if 6 <= hours_since <= 48:
            # Проверяем, был ли уже follow-up
            content_preview = (last_user_msg.content or '')[:100]
            if content_preview.strip():
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='dialog_followup',
                    source=f'dialog:{last_user_msg.id}',
                    topic=_t(user, f'Последнее сообщение {int(hours_since)}ч назад: «{content_preview[:60]}...»', f'Last message {int(hours_since)}h ago: «{content_preview[:60]}...»'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'last_message': content_preview,
                        'hours_since': round(hours_since, 1)
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=24),
                    cooldown_hours=24,
                    batch_group='engagement',
                ))

        return anchors

    def _scan_daily_rhythm(self, user, session, user_now) -> list:
        """Утренний план / вечерний обзор"""
        anchors = []
        now_utc = datetime.now(timezone.utc)
        hour = user_now.hour

        # Утро: 9:00-10:30
        if 9 <= hour <= 10:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='morning_plan',
                source=f'daily:morning:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user, 'Утро — время для обзора дня', 'Morning — time to review the day'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'hour': hour, 'date': user_now.strftime('%Y-%m-%d')}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=3),
                cooldown_hours=20,
                batch_group='daily',
            ))

        # Вечер: 20:00-21:30
        if 20 <= hour <= 21:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='evening_review',
                source=f'daily:evening:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user, 'Вечер — время для подведения итогов', 'Evening — time to wrap up'),
                priority=AnchorPriority.LOW,
                data=json.dumps({'hour': hour, 'date': user_now.strftime('%Y-%m-%d')}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=2),
                cooldown_hours=20,
                batch_group='daily',
            ))

        return anchors

    def _scan_premium_insights(self, user, profile, session, now_utc) -> list:
        """Premium: мониторинг рынка, идеи контента"""
        anchors = []

        if not profile:
            return anchors

        interests = getattr(profile, 'interests', '') or ''
        goals = getattr(profile, 'goals', '') or ''
        content_strategy = getattr(profile, 'content_strategy', '') or ''
        niche = interests[:100] or goals[:100]

        if niche:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='market_insight',
                source=f'market:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, f'Время проверить события в нише: {niche[:60]}', f'Time to check events in niche: {niche[:60]}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({'niche': niche, 'goals': goals[:200]}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        if content_strategy or (user.telegram_channel):
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='content_opportunity',
                source=f'content:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, 'Время для контент-идеи', 'Time for a content idea'),
                priority=AnchorPriority.LOW,
                data=json.dumps({
                    'content_strategy': content_strategy[:300],
                    'channel': user.telegram_channel,
                    'niche': niche[:100]
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        return anchors

    def _scan_events(self, user, profile, session, now_utc) -> list:
        """Ищет актуальные мероприятия: по нише + по задачам контактов в городе."""
        anchors = []
        if not profile:
            return anchors

        interests = getattr(profile, 'interests', '') or ''
        goals = getattr(profile, 'goals', '') or ''
        position = getattr(profile, 'position', '') or ''
        niche = interests[:100] or goals[:100] or position[:60]
        city = getattr(profile, 'city', '') or ''

        # 1) Якорь по нише — ежедневный (AI сам решит нужно ли)
        if niche:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='event_discovery',
                source=f'events:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, f'Поиск актуальных мероприятий по теме: {niche[:60]}', f'Searching for events on topic: {niche[:60]}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({
                    'niche': niche,
                    'city': city,
                    'goals': goals[:200],
                    'search_query': f'конференции митапы события {niche[:40]} {now_utc.strftime("%B %Y")} онлайн офлайн'
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        # 2) Якорь «активности контактов» — сопоставляем ВСЕ данные профиля
        #    Интересы, навыки, цели, планы, задачи — ищем пересечения
        if city:
            # Собираем полный профиль пользователя для матчинга
            user_interests = (interests or '').lower()
            user_skills = (getattr(profile, 'skills', '') or '').lower()
            user_goals = (goals or '').lower()
            user_plans = (getattr(profile, 'current_plans', '') or '').lower()
            user_bio = (getattr(profile, 'bio', '') or '').lower()

            # Всё что характеризует пользователя — одной строкой для ИИ
            user_profile_text = ' '.join(filter(None, [
                user_interests, user_skills, user_goals, user_plans, user_bio,
                (getattr(profile, 'position', '') or '').lower()
            ]))

            if not user_profile_text.strip():
                return anchors

            # Ключевые слова из профиля — грубый pre-filter
            # Берём значимые слова (>3 букв) из интересов, навыков, целей
            profile_words = set()
            for field in [user_interests, user_skills, user_goals, user_plans]:
                for word in field.replace(',', ' ').replace(';', ' ').split():
                    w = word.strip().lower()
                    if len(w) > 3 and w not in ('для', 'что', 'как', 'это', 'мой', 'моя', 'при', 'или', 'так'):
                        profile_words.add(w)

            # Контакты в том же городе
            same_city_profiles = session.query(UserProfile).filter(
                UserProfile.user_id != user.id,
                UserProfile.city.ilike(f'%{city}%')
            ).limit(50).all()

            contact_user_ids = [p.user_id for p in same_city_profiles]
            contact_profiles_map = {p.user_id: p for p in same_city_profiles}

            if contact_user_ids:
                # Batch-load User objects for all contacts (avoid N+1 in activity loops)
                _ca_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(contact_user_ids)).all()}

                # Задачи контактов за последние 7 дней
                week_ago = now_utc - timedelta(days=7)
                contact_tasks = session.query(Task).filter(
                    Task.user_id.in_(contact_user_ids),
                    Task.created_at >= week_ago,
                    Task.status.in_(['pending', 'in_progress', 'active'])
                ).limit(200).all()

                # Группируем активности по контакту
                contact_activities = {}  # user_id → {username, activities: [str], plans, interests, skills}
                for t in contact_tasks:
                    text = f'{t.title} {t.description or ""}'.lower()
                    # Грубый pre-filter: есть ли хоть одно слово-пересечение с профилем
                    match = any(pw in text for pw in profile_words) if profile_words else False
                    if not match:
                        continue
                    if t.user_id not in contact_activities:
                        c_user = _ca_user_by_id.get(t.user_id)
                        c_prof = contact_profiles_map.get(t.user_id)
                        contact_activities[t.user_id] = {
                            'username': c_user.username if c_user else 'unknown',
                            'activities': [],
                            'plans': (c_prof.current_plans or '')[:150] if c_prof else '',
                            'interests': (c_prof.interests or '')[:150] if c_prof else '',
                            'skills': (c_prof.skills or '')[:150] if c_prof else '',
                            'position': (c_prof.position or '')[:80] if c_prof else '',
                        }
                    date_str = ''
                    if t.reminder_time:
                        date_str = f' ({t.reminder_time.strftime("%d.%m %H:%M")})'
                    contact_activities[t.user_id]['activities'].append(
                        f'{t.title[:80]}{date_str}'
                    )

                # Также проверяем current_plans контактов (даже без задач)
                for cp in same_city_profiles:
                    plans = (cp.current_plans or '').lower()
                    if not plans or cp.user_id in contact_activities:
                        continue
                    if any(pw in plans for pw in profile_words):
                        c_user = _ca_user_by_id.get(cp.user_id)
                        if c_user and c_user.username:
                            contact_activities[cp.user_id] = {
                                'username': c_user.username,
                                'activities': [],
                                'plans': (cp.current_plans or '')[:150],
                                'interests': (cp.interests or '')[:150],
                                'skills': (cp.skills or '')[:150],
                                'position': (cp.position or '')[:80],
                            }

                if contact_activities:
                    # Берём до 5 самых релевантных контактов
                    top_contacts = list(contact_activities.values())[:5]
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='contact_activity',
                        source=f'contact_activity:{now_utc.strftime("%Y-%m-%d")}',
                        topic=_t(user, f'Активности контактов в {city} совпадают с вашим профилем ({len(contact_activities)} чел)', f'Contact activities in {city} match your profile ({len(contact_activities)} people)'),
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({
                            'city': city,
                            'user_profile': {
                                'interests': (interests or '')[:200],
                                'skills': (getattr(profile, 'skills', '') or '')[:200],
                                'goals': goals[:200],
                                'plans': (getattr(profile, 'current_plans', '') or '')[:200],
                            },
                            'contacts': top_contacts,
                        }),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=24,
                        batch_group='contacts',
                    ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # ENGAGEMENT SCANNERS — сообщения, баланс, неактивность, декомпозиция
    # ═══════════════════════════════════════════════════════

    def _scan_incoming_messages(self, user, session, now_utc) -> list:
        """Уведомляет о непрочитанных входящих сообщениях (status='sent' или 'delivered')."""
        anchors = []

        unread = session.query(UserMessage).filter(
            UserMessage.recipient_id == user.id,
            UserMessage.status.in_(['sent', 'delivered']),
        ).all()

        if not unread:
            return anchors

        # Группируем по отправителю
        # Pre-fetch all senders (batch, avoid N+1)
        _unread_sids = list({msg.sender_id for msg in unread})
        _unread_senders = session.query(User).filter(User.id.in_(_unread_sids)).all()
        _unread_sender_by_id = {u.id: u for u in _unread_senders}

        senders = {}
        for msg in unread:
            sender = _unread_sender_by_id.get(msg.sender_id)
            uname = sender.username if sender else 'unknown'
            if uname not in senders:
                senders[uname] = []
            senders[uname].append(msg.message_text[:80])

        summaries = []
        _msg_suffix = 'msg' if getattr(user, 'language', 'ru') == 'en' else 'сообщ.'
        for uname, texts in list(senders.items())[:5]:
            summaries.append(f'@{uname}: {len(texts)} {_msg_suffix}')

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='incoming_message',
            source=f'messages:unread:{now_utc.strftime("%Y-%m-%d")}',  # дедупликация по дню (было по часу → дубли)
            topic=_t(user, f'{len(unread)} непрочитанных сообщений от {len(senders)} чел: {", ".join(summaries)}', f'{len(unread)} unread messages from {len(senders)} people: {", ".join(summaries)}'),
            priority=AnchorPriority.HIGH,
            data=json.dumps({
                'total': len(unread),
                'senders': {k: v[:3] for k, v in senders.items()},  # до 3 сообщений на отправителя
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=3,
            batch_group='engagement',
        ))

        return anchors

    def _scan_token_low_balance(self, user, session, now_utc) -> list:
        """Предупреждает когда баланс токенов критически низкий."""
        anchors = []

        balance = user.token_balance or 0
        # Порог: менее 50 токенов (≈3 проактивных сообщения)
        if balance >= 50:
            return anchors

        # Не предупреждаем если совсем 0 — тогда _process_user_inner и так пропустит
        if balance <= 0:
            return anchors

        msgs_left = balance // 15  # 15 токенов за проактивное сообщение

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='token_low_balance',
            source=f'tokens:low:{balance}',
            topic=_t(user, f'Баланс токенов: {balance} — хватит на ~{msgs_left} сообщений', f'Token balance: {balance} — enough for ~{msgs_left} messages'),
            priority=AnchorPriority.HIGH,
            data=json.dumps({
                'balance': balance,
                'messages_left': msgs_left,
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(days=3),
            cooldown_hours=24,
            batch_group='engagement',
        ))

        return anchors

    def _scan_delegation_overdue(self, user, session, now_utc) -> list:
        """Задачи делегированы, приняты, но дедлайн прошёл — исполнитель не выполнил."""
        anchors = []

        overdue_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'accepted',
            Task.status.in_(['pending', 'in_progress']),
            Task.reminder_time.isnot(None),
            Task.reminder_time < now_utc,
        ).all()

        for task in overdue_delegated:
            rt = task.reminder_time
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=timezone.utc)
            hours_overdue = (now_utc - rt).total_seconds() / 3600
            if hours_overdue >= 2:  # Просрочена > 2ч
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='delegation_overdue',
                    source=f'task:{task.id}:delegation_overdue',
                    topic=_t(user, f'Делегированная задача «{task.title}» → @{task.delegated_to_username} просрочена на {int(hours_overdue)}ч', f'Delegated task «{task.title}» → @{task.delegated_to_username} overdue by {int(hours_overdue)}h'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({
                        'task_id': task.id,
                        'title': task.title,
                        'delegated_to': task.delegated_to_username,
                        'hours_overdue': round(hours_overdue, 1),
                        'deadline': rt.isoformat(),
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=8,
                    batch_group='delegation',
                ))

        return anchors

    def _scan_goal_decomposition(self, user, session, now_utc) -> list:
        """Активные цели без привязанных задач → предложить разбить на шаги."""
        anchors = []

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
        ).all()

        # Batch-load linked task counts per goal (avoid N+1 count query per goal)
        from sqlalchemy import func as _func_goal_scan
        _gd_goal_ids = [g.id for g in active_goals]
        _gd_task_counts = dict(session.query(Task.goal_id, _func_goal_scan.count(Task.id)).filter(
            Task.goal_id.in_(_gd_goal_ids),
            Task.status.in_(['pending', 'in_progress']),
        ).group_by(Task.goal_id).all()) if _gd_goal_ids else {}

        for goal in active_goals:
            # Проверяем есть ли ХОТЬ ОДНА активная задача, привязанная к цели
            linked_tasks = _gd_task_counts.get(goal.id, 0)

            if linked_tasks > 0:
                continue

            # Цель должна быть хотя бы 2 дня старой (дать время создать задачи)
            if goal.created_at:
                ct = goal.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days < 2:
                    continue

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='goal_decomposition',
                source=f'goal:{goal.id}:no_tasks',
                topic=_t(user, f'Цель «{goal.title}» — нет активных задач, нужна декомпозиция', f'Goal «{goal.title}» — no active tasks, needs breakdown'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'goal_id': goal.id,
                    'title': goal.title,
                    'description': (goal.description or '')[:200],
                    'progress': goal.progress_percentage,
                    'category': goal.category,
                    'target_date': goal.target_date.isoformat() if goal.target_date else None,
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=3),
                cooldown_hours=48,
                batch_group='goals',
            ))

        return anchors

    def _scan_inactivity_reengagement(self, user, session, now_utc) -> list:
        """Пользователь не взаимодействовал 3+ дня → мягкое возвращение."""
        anchors = []

        # Последнее взаимодействие
        last_interaction = session.query(Interaction).filter(
            Interaction.user_id == user.id,
        ).order_by(Interaction.created_at.desc()).first()

        if not last_interaction or not last_interaction.created_at:
            return anchors

        li = last_interaction.created_at
        if li.tzinfo is None:
            li = li.replace(tzinfo=timezone.utc)
        days_inactive = (now_utc - li).days

        if days_inactive < 3:
            return anchors

        # Собираем число незакрытых задач для контекста
        pending_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'in_progress']),
        ).count()

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
        ).count()

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='inactivity_reengagement',
            source=f'inactivity:{days_inactive}d:{now_utc.strftime("%Y-%m-%d")}',
            topic=_t(user, f'Не заходил {days_inactive} дней — {pending_tasks} задач и {active_goals} целей ждут', f'Inactive for {days_inactive} days — {pending_tasks} tasks and {active_goals} goals waiting'),
            priority=AnchorPriority.MEDIUM,
            data=json.dumps({
                'days_inactive': days_inactive,
                'pending_tasks': pending_tasks,
                'active_goals': active_goals,
                'last_seen': li.isoformat(),
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(days=3),
            cooldown_hours=48,
            batch_group='engagement',
        ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # CONTENT CAMPAIGN SCANNER — автономная публикация контента
    # ═══════════════════════════════════════════════════════

    def _scan_content_campaigns(self, user, session, now_utc) -> list:
        """Сканирует контент-кампании: создаёт якорь content_campaign_publish когда пора постить.

        Проверяет:
        1. Активные кампании с status='active'
        2. Частоту (daily / every_2_days / every_3_days / weekly)
        3. Предпочтительное время (post_time)
        4. Дневной лимит (daily_limit)
        5. Общий лимит (max_posts)
        """
        anchors = []

        campaigns = session.query(ContentCampaign).filter(
            ContentCampaign.user_id == user.id,
            ContentCampaign.status == 'active'
        ).all()

        if not campaigns:
            return anchors

        import pytz as _pytz_cc
        user_tz = _pytz_cc.timezone(user.timezone or 'Europe/Moscow')
        user_now = now_utc.astimezone(user_tz)

        # today_start is the same for all campaigns (same user)
        _cc_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load posts_today count per campaign (avoid N+1 AgentActivityLog count per campaign)
        from sqlalchemy import func as _func_cc
        _cc_camp_ids = [c.id for c in campaigns]
        _cc_posts_today_raw = session.query(AgentActivityLog.result, _func_cc.count(AgentActivityLog.id)).filter(
            AgentActivityLog.user_id == user.id,
            AgentActivityLog.activity_type.in_(['post_newsfeed', 'post_telegram', 'post_discord']),
            AgentActivityLog.created_at >= _cc_today_start,
            AgentActivityLog.result.in_([f'campaign:{cid}' for cid in _cc_camp_ids]),
        ).group_by(AgentActivityLog.result).all() if _cc_camp_ids else []
        _cc_posts_today_map = {int(r.split(':')[1]): cnt for r, cnt in _cc_posts_today_raw if r and ':' in r}

        for campaign in campaigns:
            # --- Общий лимит ---
            if campaign.max_posts and campaign.max_posts > 0:
                if (campaign.posts_published or 0) >= campaign.max_posts:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed content campaign #{campaign.id} «{campaign.name}» — reached max_posts")
                    except Exception:
                        session.rollback()
                    continue

            # --- Частота: проверяем last_post_at ---
            frequency_hours = {
                'daily': 20,          # ~1 раз в 20ч (с запасом)
                'every_2_days': 44,
                'every_3_days': 68,
                'weekly': 164,
            }
            min_gap_hours = frequency_hours.get(campaign.frequency or 'daily', 20)

            if campaign.last_post_at:
                last_post = campaign.last_post_at
                if last_post.tzinfo is None:
                    last_post = last_post.replace(tzinfo=timezone.utc)
                hours_since = (now_utc - last_post).total_seconds() / 3600
                if hours_since < min_gap_hours:
                    logger.debug(f"[ANCHOR] Content campaign #{campaign.id}: skip — {hours_since:.1f}h since last post (need {min_gap_hours})")
                    continue

            # --- Дневной лимит ---
            posts_today = _cc_posts_today_map.get(campaign.id, 0)

            if posts_today >= (campaign.daily_limit or 1):
                logger.debug(f"[ANCHOR] Content campaign #{campaign.id}: skip — {posts_today} posts today (limit {campaign.daily_limit})")
                continue

            # --- Время поста (±90 мин от предпочтительного) ---
            try:
                post_h, post_m = map(int, (campaign.post_time or '12:00').split(':'))
            except (ValueError, AttributeError):
                post_h, post_m = 12, 0

            current_minutes = user_now.hour * 60 + user_now.minute
            target_minutes = post_h * 60 + post_m
            if abs(current_minutes - target_minutes) > 90:
                continue

            # --- Рабочие часы (9:00–22:00) ---
            if user_now.hour < 9 or user_now.hour >= 22:
                continue

            # --- Собираем данные для AI ---
            platforms = ['feed']
            try:
                platforms = json.loads(campaign.platforms or '["feed"]')
            except (json.JSONDecodeError, TypeError):
                platforms = ['feed']

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='content_campaign_publish',
                source=f'content_campaign:{campaign.id}:publish:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user,
                    f'Контент-кампания «{campaign.name}» — время для публикации',
                    f'Content campaign «{campaign.name}» — time to publish'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'campaign_id': campaign.id,
                    'campaign_name': campaign.name,
                    'goal': (campaign.goal or '')[:500],
                    'topics': (campaign.topics or '')[:300],
                    'platforms': platforms,
                    'tone': campaign.tone or 'professional',
                    'language': campaign.language or 'ru',
                    'posts_published': campaign.posts_published or 0,
                    'max_posts': campaign.max_posts or 0,
                    'user_name': user.first_name or user.username or 'user',
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=min_gap_hours * 0.8,  # cooldown чуть меньше частоты
                batch_group='content',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # DELEGATION CAMPAIGN SCANNER — автономное делегирование задач
    # ═══════════════════════════════════════════════════════

    def _scan_delegation_campaigns(self, user, session, now_utc) -> list:
        """Сканирует кампании делегирования: создаёт якоря delegation_campaign_send.

        Проверяет:
        1. Активные кампании с status='active'
        2. Дневной лимит (daily_limit)
        3. Общий лимит (max_delegations)
        4. Рабочие часы
        5. Наличие подходящих исполнителей
        """
        anchors = []

        campaigns = session.query(DelegationCampaign).filter(
            DelegationCampaign.user_id == user.id,
            DelegationCampaign.status == 'active'
        ).all()

        if not campaigns:
            return anchors

        import pytz as _pytz_dc
        user_tz = _pytz_dc.timezone(user.timezone or 'Europe/Moscow')
        user_now = now_utc.astimezone(user_tz)

        # Рабочие часы (10:00–20:00)
        if user_now.hour < 10 or user_now.hour >= 20:
            return anchors

        # today_start is same for all campaigns (same user timezone)
        _dc_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load delegation counts + already-delegated usernames for all campaigns
        _dc_camp_ids = [c.id for c in campaigns]
        from sqlalchemy import func as _func_dc
        _dc_today_counts = dict(session.query(Task.delegation_campaign_id, _func_dc.count(Task.id)).filter(
            Task.delegated_by == user.id,
            Task.delegation_campaign_id.in_(_dc_camp_ids),
            Task.created_at >= _dc_today_start,
        ).group_by(Task.delegation_campaign_id).all()) if _dc_camp_ids else {}
        # All delegated usernames per campaign
        _dc_all_delegated = session.query(Task.delegation_campaign_id, Task.delegated_to_username).filter(
            Task.delegation_campaign_id.in_(_dc_camp_ids),
            Task.delegated_to_username.isnot(None),
        ).all() if _dc_camp_ids else []
        _dc_delegated_by_camp: dict = {}
        for _dc_cid, _dc_uname in _dc_all_delegated:
            _dc_delegated_by_camp.setdefault(_dc_cid, set()).add(_dc_uname.lower())

        for campaign in campaigns:
            # --- Общий лимит ---
            if campaign.max_delegations and campaign.max_delegations > 0:
                if (campaign.delegations_sent or 0) >= campaign.max_delegations:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed delegation campaign #{campaign.id} «{campaign.name}» — reached max_delegations")
                    except Exception:
                        session.rollback()
                    continue

            # --- Частота: макс 1 делегация в 4ч ---
            if campaign.last_delegation_at:
                last_deleg = campaign.last_delegation_at
                if last_deleg.tzinfo is None:
                    last_deleg = last_deleg.replace(tzinfo=timezone.utc)
                hours_since = (now_utc - last_deleg).total_seconds() / 3600
                if hours_since < 4:
                    continue

            # --- Дневной лимит ---
            delegations_today = _dc_today_counts.get(campaign.id, 0)

            if delegations_today >= (campaign.daily_limit or 3):
                continue

            # --- Ищем потенциальных исполнителей ---
            target_desc = (campaign.target_audience or campaign.goal or '')[:500]
            if not target_desc:
                continue

            # Получаем уже привлечённых (чтобы не повторяться)
            already_usernames = _dc_delegated_by_camp.get(campaign.id, set())

            # Ищем пользователей по interests/skills/bio/goals/city/position
            from sqlalchemy import or_
            keywords = [w.strip().lower() for w in target_desc.replace(',', ' ').replace(';', ' ').split() if len(w.strip()) > 2][:15]

            candidates = []
            if keywords:
                filters = []
                for kw in keywords[:8]:
                    filters.append(UserProfile.interests.ilike(f'%{kw}%'))
                    filters.append(UserProfile.skills.ilike(f'%{kw}%'))
                    filters.append(UserProfile.bio.ilike(f'%{kw}%'))
                    filters.append(UserProfile.goals.ilike(f'%{kw}%'))
                    filters.append(UserProfile.city.ilike(f'%{kw}%'))
                    filters.append(UserProfile.position.ilike(f'%{kw}%'))

                profiles = session.query(UserProfile).join(User).filter(
                    User.id != user.id,
                    or_(*filters),
                ).limit(30).all()

                # Pre-fetch all profile users (batch, avoid N+1)
                if profiles:
                    _prof_uids = [p.user_id for p in profiles]
                    _prof_users = session.query(User).filter(User.id.in_(_prof_uids)).all()
                    _prof_user_by_id = {u.id: u for u in _prof_users}
                else:
                    _prof_user_by_id = {}

                for p in profiles:
                    p_user = _prof_user_by_id.get(p.user_id)
                    if not p_user or not p_user.username:
                        continue
                    if p_user.username.lower() in already_usernames:
                        continue
                    # Скоринг
                    score = 0
                    profile_text = f"{(p.interests or '').lower()} {(p.skills or '').lower()} {(p.bio or '').lower()} {(p.goals or '').lower()} {(p.city or '').lower()} {(p.position or '').lower()}"
                    for kw in keywords:
                        if kw in profile_text:
                            score += 1
                    if score > 0:
                        candidates.append((p_user, score))

                candidates.sort(key=lambda x: -x[1])

            if not candidates:
                # Fallback: если по ключевым словам никого не нашли — берём любого
                # активного пользователя, которому ещё не делегировали в этой кампании
                try:
                    fallback_users = session.query(User).filter(
                        User.id != user.id,
                        User.username.isnot(None),
                        User.telegram_id.isnot(None),
                    ).limit(20).all()
                    for fu in fallback_users:
                        if fu.username and fu.username.lower() not in already_usernames:
                            candidates.append((fu, 0))
                    if candidates:
                        logger.info(f"[ANCHOR] Delegation campaign #{campaign.id}: no keyword match, using fallback candidates ({len(candidates)})")
                except Exception as _fb_e:
                    logger.debug(f"[ANCHOR] Delegation campaign #{campaign.id} fallback error: {_fb_e}")

            if not candidates:
                logger.info(f"[ANCHOR] Delegation campaign #{campaign.id} «{campaign.name}»: no candidates found (target: {target_desc[:100]})")
                continue

            # Берём лучшего кандидата
            best_candidate, best_score = candidates[0]

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='delegation_campaign_send',
                source=f'delegation_campaign:{campaign.id}:send:{best_candidate.username}:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user,
                    f'Кампания делегирования «{campaign.name}» — делегировать @{best_candidate.username}',
                    f'Delegation campaign «{campaign.name}» — delegate to @{best_candidate.username}'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'campaign_id': campaign.id,
                    'campaign_name': campaign.name,
                    'goal': (campaign.goal or '')[:500],
                    'target_audience': (campaign.target_audience or '')[:300],
                    'task_template': (campaign.task_template or '')[:500],
                    'offer': (campaign.offer or '')[:300],
                    'tone': campaign.tone or 'professional',
                    'candidate_username': best_candidate.username,
                    'candidate_name': best_candidate.first_name or best_candidate.username,
                    'candidate_score': best_score,
                    'delegations_sent': campaign.delegations_sent or 0,
                    'max_delegations': campaign.max_delegations or 0,
                    'default_deadline_hours': campaign.default_deadline_hours or 48,
                    'user_name': user.first_name or user.username or 'user',
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=8),
                cooldown_hours=4,
                batch_group='delegation',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # EMAIL OUTREACH SCANNER — автономная email-кампания
    # ═══════════════════════════════════════════════════════

    def _scan_email_outreach(self, user, session, now_utc) -> list:
        """Сканирует email-кампании:
        1. Активные кампании с черновиками (draft) → якорь email_outreach_send (агент отправит)
        2. Отправленные без ответа > 3 дней → якорь email_follow_up
        3. Входящие ответы → якорь email_reply_received (CRITICAL) — даже для paused кампаний!
        4. Ежедневный отчёт по активным кампаниям → email_campaign_report
        """
        anchors = []

        # Активные + paused кампании (paused тоже нужны для обработки входящих reply)
        campaigns = session.query(EmailCampaign).filter(
            EmailCampaign.user_id == user.id,
            EmailCampaign.status.in_(['active', 'paused'])
        ).all()

        if not campaigns:
            return anchors

        # Compute today_start ONCE (same user → same timezone for all campaigns)
        import pytz as _pytz_email
        _utz_email = _pytz_email.timezone(user.timezone or 'Europe/Moscow')
        _user_now_local = now_utc.astimezone(_utz_email)
        today_start = _user_now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load ALL EmailOutreach for all campaigns (avoid N+1 per campaign)
        _ec_campaign_ids = [c.id for c in campaigns]
        _ec_all_outreach = session.query(EmailOutreach).filter(
            EmailOutreach.campaign_id.in_(_ec_campaign_ids)
        ).all() if _ec_campaign_ids else []
        _ec_outreach_by_camp: dict = {}
        for _eo_item in _ec_all_outreach:
            _ec_outreach_by_camp.setdefault(_eo_item.campaign_id, []).append(_eo_item)

        for campaign in campaigns:
            is_paused = campaign.status == 'paused'
            _camp_outreach = _ec_outreach_by_camp.get(campaign.id, [])

            # --- 1. Есть черновики (draft) — агент должен написать и отправить ---
            # Пропускаем для paused кампаний
            drafts = []
            if not is_paused:
                drafts = [o for o in _camp_outreach if o.status == 'draft'][:10]

            # Дневной лимит — считаем «сегодня» по таймзоне пользователя, не UTC
            def _ts_aware(dt):
                return dt if dt is not None and dt.tzinfo is not None else (dt.replace(tzinfo=timezone.utc) if dt else None)

            sent_today = sum(
                1 for o in _camp_outreach
                if _ts_aware(o.sent_at) and _ts_aware(o.sent_at) >= today_start
                and o.status in ('sent', 'delivered', 'opened', 'replied')
            )

            remaining_daily = max(0, campaign.daily_limit - sent_today)
            # max_emails=0 означает безлимитную кампанию
            if campaign.max_emails and campaign.max_emails > 0:
                remaining_total = max(0, campaign.max_emails - (campaign.emails_sent or 0))
            else:
                remaining_total = 999999  # безлимит

            if drafts and remaining_daily > 0 and remaining_total > 0:
                batch_size = min(len(drafts), remaining_daily, 10)
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_outreach_send',
                    source=f'email_campaign:{campaign.id}:send:{now_utc.strftime("%Y-%m-%d")}',  # дедупликация по дню (было по часу → дубли)
                    topic=_t(user,
                        f'Email-кампания «{campaign.name}» — {len(drafts)} черновиков ждут отправки ({remaining_daily} осталось сегодня)',
                        f'Email campaign «{campaign.name}» — {len(drafts)} drafts pending ({remaining_daily} remaining today)'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'target_audience': campaign.target_audience[:300] if campaign.target_audience else '',
                        'offer': campaign.offer[:500] if campaign.offer else '',
                        'tone': campaign.tone,
                        'sender_name': campaign.sender_name,
                        'sender_email': campaign.sender_email,
                        'drafts': [{'id': d.id, 'email': d.recipient_email,
                                    'name': d.recipient_name,
                                    'company': d.recipient_company,
                                    'context': d.recipient_context} for d in drafts[:batch_size]],
                        'remaining_daily': remaining_daily,
                        'remaining_total': remaining_total,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=0.3,  # ~20 мин между пакетами
                    batch_group='email',
                ))

            # --- 2. Follow-up: отправлено > 3 дней назад, без ответа, follow_up_count < max ---
            # Пропускаем для paused кампаний
            max_follow_ups = campaign.max_follow_ups or 2
            stale_emails = [] if is_paused else [
                o for o in _camp_outreach
                if o.status in ('sent', 'delivered', 'opened')
                and o.follow_up_count < max_follow_ups
                and o.next_follow_up_at is not None
                and (_ts_aware(o.next_follow_up_at) or o.next_follow_up_at.replace(tzinfo=timezone.utc)) <= now_utc
            ][:5]

            for email in stale_emails:
                days_since = 0
                if email.sent_at:
                    sa = email.sent_at
                    if sa.tzinfo is None:
                        sa = sa.replace(tzinfo=timezone.utc)
                    days_since = (now_utc - sa).days

                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_follow_up',
                    source=f'email:{email.id}:follow_up:{email.follow_up_count + 1}',
                    topic=_t(user,
                        f'Follow-up #{email.follow_up_count + 1} для {email.recipient_email} ({days_since}д без ответа) — кампания «{campaign.name}»',
                        f'Follow-up #{email.follow_up_count + 1} for {email.recipient_email} ({days_since}d no reply) — campaign «{campaign.name}»'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'outreach_id': email.id,
                        'recipient_email': email.recipient_email,
                        'recipient_name': email.recipient_name,
                        'recipient_company': email.recipient_company,
                        'original_subject': email.subject,
                        'original_body': email.body[:500] if email.body else '',
                        'follow_up_number': email.follow_up_count + 1,
                        'days_since_sent': days_since,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(days=2),
                    cooldown_hours=24,
                    batch_group='email',
                ))

            # --- 3. Входящие ответы (reply_text заполнен, но ai_reply не отправлен) ---
            unreplied = [
                o for o in _camp_outreach
                if o.status == 'replied' and o.reply_text and not o.ai_reply_sent_at
            ]

            for email in unreplied:
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_reply_received',
                    source=f'email:{email.id}:reply',
                    topic=_t(user,
                        f' Ответ от {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — кампания «{campaign.name}»',
                        f' Reply from {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — campaign «{campaign.name}»'),
                    priority=AnchorPriority.CRITICAL,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'outreach_id': email.id,
                        'recipient_email': email.recipient_email,
                        'recipient_name': email.recipient_name,
                        'recipient_company': email.recipient_company,
                        'original_subject': email.subject,
                        'original_body': email.body[:500] if email.body else '',
                        'reply_text': email.reply_text[:1000] if email.reply_text else '',
                        'ai_previous_reply': email.ai_reply_text[:500] if email.ai_reply_text else None,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=24),
                    cooldown_hours=0.5,
                    batch_group='email',
                ))

            # --- Auto-complete: нет черновиков, нет ожидающих follow-up, все треды закрыты ---
            # Работает для ЛЮБЫХ АКТИВНЫХ кампаний (paused не автозавершаем):
            # - Переговоры (1 письмо): ответили + агент ответил → готово
            # - Привлечение (50 писем): агент сам добавляет лиды через add_email_leads,
            #   пока есть черновики — не завершается. Как только все обработаны → завершается.
            # НЕ автозавершаем если ещё есть квота (remaining_total) — email_need_leads найдёт ещё контакты
            if not is_paused and not drafts and not stale_emails and remaining_total <= 0:
                # Письма у которых ещё не закрыт цикл:
                # sent/delivered/opened с незакрытыми follow-up ИЛИ replied без ответа агента
                open_outreach = sum(
                    1 for o in _camp_outreach
                    if o.status in ('sent', 'delivered', 'opened')
                    and o.follow_up_count < (campaign.max_follow_ups or 2)
                )
                unanswered_replies = sum(
                    1 for o in _camp_outreach
                    if o.status == 'replied' and o.reply_text and not o.ai_reply_sent_at
                )
                total_outreach = len(_camp_outreach)
                if total_outreach > 0 and open_outreach == 0 and unanswered_replies == 0:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed campaign #{campaign.id} «{campaign.name}» — all threads closed")
                    except Exception:
                        session.rollback()
                    continue  # Skip anchors for completed campaign

            # --- 3b. Нужны новые лиды: нет черновиков, но кампания не заполнена ---
            # Срабатывает когда: активная кампания, 0 черновиков, ещё есть квота (total/daily)
            if not is_paused and not drafts and remaining_daily > 0 and remaining_total > 0:
                # Считаем сколько контактов уже есть (sent + draft)
                total_in_pipeline = len(_camp_outreach)
                # Запускаем поиск только если ещё есть в квоте место
                if remaining_total > total_in_pipeline or remaining_total >= 999999:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='email_need_leads',
                        source=f'email_campaign:{campaign.id}:need_leads:{now_utc.strftime("%Y-%m-%d")}',  # дедупликация по дню
                        topic=_t(user,
                            f' Кампания «{campaign.name}» — нет черновиков, найди новые контакты ({remaining_daily} квота сегодня)',
                            f' Campaign «{campaign.name}» — no drafts, find new leads ({remaining_daily} quota today)'),
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({
                            'campaign_id': campaign.id,
                            'campaign_name': campaign.name,
                            'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                            'target_audience': campaign.target_audience[:300] if campaign.target_audience else '',
                            'offer': campaign.offer[:300] if campaign.offer else '',
                            'total_in_pipeline': total_in_pipeline,
                            'remaining_daily': remaining_daily,
                            'remaining_total': min(remaining_total, 50),
                        }),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=6),
                        cooldown_hours=0.5,  # каждые 30 мин проверяем нужны ли лиды (было 1ч)
                        batch_group='email',
                    ))

            # --- 4. Дневной отчёт по кампании (если есть активность, не для paused) ---
            if is_paused:
                continue
            total_sent = campaign.emails_sent or 0
            total_replied = campaign.emails_replied or 0
            if total_sent > 0 and sent_today > 0:
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_campaign_report',
                    source=f'email_campaign:{campaign.id}:report:{now_utc.strftime("%Y-%m-%d")}',
                    topic=_t(user,
                        f' Отчёт email-кампании «{campaign.name}»: {total_sent} отправлено, {total_replied} ответов, {sent_today} сегодня',
                        f' Email campaign «{campaign.name}» report: {total_sent} sent, {total_replied} replies, {sent_today} today'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'total_sent': total_sent,
                        'total_replied': total_replied,
                        'sent_today': sent_today,
                        'remaining_daily': remaining_daily,
                        'remaining_total': remaining_total,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=18),
                    cooldown_hours=20,
                    batch_group='email',
                ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # POST SCANNERS — ленточный автопостинг + канал
    # ═══════════════════════════════════════════════════════

    def _scan_post_opportunities(self, user, profile, session, now_utc) -> list:
        """Сканирует ВСЕ данные пользователя и создаёт якорь post_opportunity.

        AI потом сам решит, стоит ли делать пост и О ЧЁМ.
        Мы здесь только проверяем: есть ли вообще о чём писать.

        Время поста индивидуально для каждого пользователя:
        распределяется по user.id в окне 10:00–21:00.
        """
        anchors = []

        # Проверяем лимит постов за день
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        posts_today = session.query(Post).filter(
            Post.user_id == user.id,
            Post.created_at >= today_start_utc
        ).count()

        feed_limit = MAX_FEED_PER_DAY
        if posts_today >= feed_limit:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — already {posts_today}/{feed_limit} today")
            return anchors

        # ── Проверяем рабочие часы (10:00–22:00) ──
        current_hour = user_now.hour
        if current_hour < 10 or current_hour >= 22:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — outside hours ({current_hour})")
            return anchors

        # ── Soft throttle: не более одного якоря каждые 4ч в рабочее время ──
        # Строгое «индивидуальное окно» убрано — оно пропускало дни при перезапуске бота.
        # Cooldown=4h на якоре уже ограничивает частоту; лимит постов за день = MAX_FEED_PER_DAY.
        # Дополнительно: рассеиваем нагрузку по user.id чтобы не всё сразу в 10:00
        import hashlib
        day_seed = f"{user.id}:{user_now.strftime('%Y-%m-%d')}"
        uid_hash = int(hashlib.md5(day_seed.encode()).hexdigest()[:8], 16)
        # Минимальный час старта = 10 + (hash % 3), т.е. 10, 11 или 12
        # Это мягко распределяет старт у разных пользователей, но не блокирует весь день
        earliest_start_hour = 10 + (uid_hash % 3)
        if user_now.hour < earliest_start_hour:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — before personal start hour {earliest_start_hour}:00")
            return anchors

        # Собираем «материал» для AI:
        signals = []

        # 1. Завершённые задачи за 24ч
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')

        # 2. Новые цели
        new_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
            Goal.created_at >= now_utc - timedelta(hours=24)
        ).all()
        if new_goals:
            signals.append(f'new_goals:{",".join(g.title for g in new_goals[:3])}')

        # 3. Цель достигнута
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.progress_percentage >= 100,
            Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')

        # 4. Стрик продуктивности (>=3 за 24ч)
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')

        # 5. Задачи с делегированием (ищет помощь)
        collab_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'pending',
            Task.created_at >= now_utc - timedelta(hours=48)
        ).all()
        if collab_tasks:
            signals.append(f'seeking_help:{",".join(t.title for t in collab_tasks[:3])}')

        # 6. Контент из последнего диалога (интересные темы)
        recent_interactions = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user',
            Interaction.created_at >= now_utc - timedelta(hours=12)
        ).order_by(Interaction.created_at.desc()).limit(5).all()
        if recent_interactions:
            topics = [i.content[:80] for i in recent_interactions if i.content]
            if topics:
                signals.append(f'recent_topics:{"||".join(topics[:3])}')

        # 7. Профиль: навыки/интересы (AI может сделать экспертный пост)
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if profile.position:
                signals.append(f'position:{profile.position[:80]}')
            if profile.city:
                signals.append(f'city:{profile.city[:50]}')

        # 8. Активные задачи (материал для поста "чем занимаюсь")
        if not signals or len(signals) < 2:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')

        # 9. Активные цели (материал для поста)
        if not signals or len(signals) < 2:
            active_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active'
            ).limit(3).all()
            if active_goals:
                signals.append(f'active_goals:{",".join(g.title for g in active_goals)}')

        # Нет сигналов — нет якоря
        if not signals:
            return anchors

        # Создаём один общий якорь — AI решит что с этим делать
        source_key = f'post:{user_now.strftime("%Y-%m-%d")}:{posts_today}'
        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='post_opportunity',
            source=source_key,
            topic=_t(user, f'Есть материал для {len(signals)} потенциальных постов в ленту', f'Material available for {len(signals)} potential feed posts'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'signals': signals,
                'posts_today': posts_today,
                'user_name': user.first_name or user.username or 'user',
                'tier': 'tokens',  # Токенная модель
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=2,
            batch_group='posting',
        ))

        return anchors

    def _scan_channel_post(self, user, profile, session, now_utc) -> list:
        """PREMIUM: сканирует возможность постинга в Telegram-канал пользователя.

        Заменяет AutoMarketingService. AI решает контент.
        """
        anchors = []

        channel = getattr(user, 'telegram_channel', None)
        if not channel:
            return anchors

        # Проверяем auto_marketing_enabled
        if profile and hasattr(profile, 'auto_marketing_enabled') and not profile.auto_marketing_enabled:
            return anchors

        # Лимит: 1 пост в канал в день
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        # Проверяем по AnchorDeliveryLog
        channel_posts_today = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= today_start_utc,
            AnchorDeliveryLog.anchor_types.contains('channel_post')
        ).count()

        if channel_posts_today >= MAX_CHANNEL_PER_DAY:
            return anchors

        # Рабочие часы (10:00–22:00) — единственный ограничитель времени
        if user_now.hour < 10 or user_now.hour >= 22:
            return anchors

        # Сигнально-ориентированный подход: постим когда есть реальный контент
        signals = []
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.progress_percentage >= 100, Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if getattr(profile, 'content_strategy', None):
                signals.append(f'content_strategy:{profile.content_strategy[:200]}')
            if profile.position:
                signals.append(f'position:{profile.position[:80]}')
        if not signals:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')
        if not signals:
            return anchors

        content_strategy = getattr(profile, 'content_strategy', '') or '' if profile else ''
        interests = getattr(profile, 'interests', '') or '' if profile else ''
        goals = getattr(profile, 'goals', '') or '' if profile else ''
        skills = getattr(profile, 'skills', '') or '' if profile else ''

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='channel_post',
            source=f'channel:{user_now.strftime("%Y-%m-%d")}',
            topic=_t(user, f'Есть материал для поста в канал {channel}', f'Content ready for channel {channel} post'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'channel': channel,
                'signals': signals,
                'content_strategy': content_strategy[:300],
                'interests': interests[:200],
                'goals': goals[:200],
                'skills': skills[:200],
                'user_name': user.first_name or user.username or 'user',
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=20,
            batch_group='posting',
        ))

        return anchors

    def _scan_discord_post(self, user, profile, session, now_utc) -> list:
        """Сигнально-ориентированный автопостинг в Discord-канал.

        Срабатывает когда есть контент — не по расписанию.
        Независим от channel_post и post_opportunity.
        """
        anchors = []

        discord_wh = getattr(user, 'discord_webhook', None)
        if not discord_wh or not discord_wh.startswith('https://discord.com/api/webhooks/'):
            return anchors

        # Лимит 1 пост в Discord в день (через AgentActivityLog)
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        from models import AgentActivityLog as _AAL_dc
        discord_today = session.query(_AAL_dc).filter(
            _AAL_dc.user_id == user.id,
            _AAL_dc.activity_type == 'post_discord',
            _AAL_dc.created_at >= today_start_utc,
            _AAL_dc.status == 'published'
        ).count()
        if discord_today >= MAX_CHANNEL_PER_DAY:
            return anchors

        # Рабочие часы
        if user_now.hour < 10 or user_now.hour >= 22:
            return anchors

        # Сигналы контента
        signals = []
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.progress_percentage >= 100, Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if getattr(profile, 'content_strategy', None):
                signals.append(f'content_strategy:{profile.content_strategy[:200]}')
        if not signals:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')
        if not signals:
            return anchors

        content_strategy = getattr(profile, 'content_strategy', '') or '' if profile else ''

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='discord_post',
            source=f'discord:{user_now.strftime("%Y-%m-%d")}',
            topic=_t(user, 'Есть материал для поста в Discord', 'Content ready for Discord post'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'discord_webhook': discord_wh,
                'signals': signals,
                'content_strategy': content_strategy[:300],
                'user_name': user.first_name or user.username or 'user',
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=20,
            batch_group='posting',
        ))

        return anchors

    def _scan_weekly_milestone(self, user, session, now_utc) -> list:
        """Срабатывает в пятн-воск, если за неделю завершено >= 5 задач.

        Όднажды в неделю, социальная валидация продуктивности.
        """
        anchors = []
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)

        # Пятн-воск: 5-7 день недели
        if user_now.weekday() not in (4, 5, 6):  # 0=пн, 4=пт, 5=сб, 6=вс
            return anchors

        # Границы недели (ISO: пн-вс)
        days_since_monday = user_now.weekday()  # 0=пн
        week_start_user = user_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        week_start_utc = week_start_user.astimezone(pytz.UTC)

        week_key = user_now.strftime('%G-W%V')  # ISO week, e.g. 2026-W09
        source = f'weekly_milestone:{week_key}'

        # Считаем завершённые за неделю
        completed_count = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= week_start_utc,
        ).count()

        if completed_count < 5:
            return anchors

        # Собираем названия задач для контекста AI
        completed_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= week_start_utc,
        ).order_by(Task.actual_completion_time.desc()).limit(7).all()
        titles = [t.title for t in completed_tasks]

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='weekly_milestone',
            source=source,
            topic=_t(user,
                     f'Недельный итог — {completed_count} задач завершено!',
                     f'Weekly milestone — {completed_count} tasks completed!'),
            priority=AnchorPriority.MEDIUM,
            data=json.dumps({
                'completed_count': completed_count,
                'week': week_key,
                'titles': titles,
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=48),
            cooldown_hours=120,  # не чаще 1 раза в 5 дней
            batch_group='milestones',
        ))
        return anchors

    def _scan_goal_milestone(self, user, session, now_utc) -> list:
        """Срабатывает когда цель пересекает 25 / 50 / 75 / 100 процентов.

        Каждый порог срабатывает ровно один раз (идентификатор source = goal:{id}:{pct}).
        """
        anchors = []
        thresholds = (25, 50, 75, 100)

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
            Goal.progress_percentage > 0,
        ).all()

        for goal in active_goals:
            pct = goal.progress_percentage or 0
            # Находим высший пройденный порог
            hit_threshold = None
            for t in sorted(thresholds, reverse=True):
                if pct >= t:
                    hit_threshold = t
                    break
            if hit_threshold is None:
                continue

            source = f'goal_milestone:{goal.id}:{hit_threshold}'

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='goal_milestone',
                source=source,
                topic=_t(user,
                         f'Цель «{goal.title[:50]}» — {hit_threshold}% выполнено!',
                         f'Goal «{goal.title[:50]}» — {hit_threshold}% done!'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'goal_id': goal.id,
                    'title': goal.title,
                    'progress': pct,
                    'milestone': hit_threshold,
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=3),
                cooldown_hours=168,  # 7 дней — порог не повторится
                batch_group='milestones',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # RECURRENCE HELPERS
    # ═══════════════════════════════════════════════════════

    def _calculate_next_recurrence(self, last_time, pattern: str, interval: int = 1):
        """Вычисляет следующее время для повторяющейся задачи.
        
        Args:
            last_time: datetime последнего срабатывания
            pattern: 'daily' | 'weekly' | 'monthly' | 'yearly'
            interval: каждые N единиц (по умолчанию 1)
        """
        import calendar

        if pattern == 'daily':
            return last_time + timedelta(days=interval)
        elif pattern == 'weekly':
            return last_time + timedelta(weeks=interval)
        elif pattern == 'monthly':
            year = last_time.year
            month = last_time.month + interval
            day = last_time.day
            while month > 12:
                year += 1
                month -= 12
            last_day = calendar.monthrange(year, month)[1]
            if day > last_day:
                day = last_day
            return last_time.replace(year=year, month=month, day=day)
        elif pattern == 'yearly':
            return last_time.replace(year=last_time.year + interval)
        else:
            return last_time + timedelta(days=interval)

    # ═══════════════════════════════════════════════════════
    # COOLDOWN & ANTI-SPAM
    # ═══════════════════════════════════════════════════════

    def _apply_cooldowns(self, anchors: list, user, session) -> list:
        """Фильтрует якоря по cooldown — один батч-запрос вместо N отдельных.

        Cooldown проверяется по source (точный ключ якоря) — это позволяет
        каждому агенту/источнику иметь независимый cooldown. Если source не
        совпадает ни с одной записью, используется fallback по anchor_type
        (для якорей без уникального source).
        """
        now_utc = datetime.now(timezone.utc)
        result = []

        # Один запрос: все недавние доставки этого пользователя
        # Берём max cooldown из списка якорей чтобы покрыть все 
        max_cooldown = max((a.cooldown_hours if a.cooldown_hours is not None and a.cooldown_hours > 0 else PRIORITY_COOLDOWN.get(a.priority, 4)) for a in anchors) if anchors else 8
        recent_deliveries = session.query(
            Anchor.anchor_type,
            Anchor.source,
            Anchor.delivered_at
        ).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.isnot(None),
            Anchor.delivered_at >= now_utc - timedelta(hours=max_cooldown)
        ).all()

        # Индексируем по source (точный ключ) и по anchor_type (fallback)
        last_delivery_by_source: dict = {}
        last_delivery_by_type: dict = {}
        for atype, asource, delivered_at in recent_deliveries:
            if asource and (asource not in last_delivery_by_source or delivered_at > last_delivery_by_source[asource]):
                last_delivery_by_source[asource] = delivered_at
            if atype not in last_delivery_by_type or delivered_at > last_delivery_by_type[atype]:
                last_delivery_by_type[atype] = delivered_at

        for anchor in anchors:
            cooldown_h = anchor.cooldown_hours if anchor.cooldown_hours is not None and anchor.cooldown_hours > 0 else PRIORITY_COOLDOWN.get(anchor.priority, 4)

            # Приоритет: per-source cooldown → per-type fallback
            # Якоря агентов (source=agent:{id}:custom:{entry}) получают независимый cooldown
            if anchor.source and anchor.source in last_delivery_by_source:
                last_delivered = last_delivery_by_source[anchor.source]
            else:
                # Для якорей без уникального source — используем type-based cooldown
                # НО только для типов, которые НЕ являются кастомными агентными якорями
                if anchor.source and anchor.source.startswith('agent:'):
                    # Агентный якорь — cooldown сбросился (нет записи по этому source) → пропускаем
                    last_delivered = None
                else:
                    last_delivered = last_delivery_by_type.get(anchor.anchor_type)

            if last_delivered:
                if last_delivered.tzinfo is None:
                    last_delivered = last_delivered.replace(tzinfo=timezone.utc)
                if last_delivered >= now_utc - timedelta(hours=cooldown_h):
                    logger.debug(f"[ANCHOR] Cooldown: {anchor.anchor_type} source={anchor.source} (last delivered {last_delivered})")
                    continue

            result.append(anchor)

        # Адаптация: если пользователь игнорирует > 70% — понижаем частоту LOW ДИАЛОГОВЫХ
        # НО НЕ блокируем: posting (post_opportunity, channel_post) — это посты, не диалог
        # И НЕ считаем CRITICAL/HIGH доставки — они информационные, ответ не ожидается
        recent_logs = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= now_utc - timedelta(days=7)
        ).all()

        # Для подсчёта ignore rate берём только ДИАЛОГОВЫЕ (не CRITICAL/HIGH)
        dialog_logs = []
        for log in recent_logs:
            try:
                types = json.loads(log.anchor_types) if log.anchor_types else []
            except (json.JSONDecodeError, TypeError):
                types = []
            # Пропускаем логи, которые содержат ТОЛЬКО ALWAYS_DELIVER_TYPES
            if all(t in ALWAYS_DELIVER_TYPES for t in types) and types:
                continue
            # Пропускаем логи постов — они не диалоговые
            if any(t in ('post_opportunity', 'channel_post') for t in types):
                continue
            dialog_logs.append(log)

        if len(dialog_logs) >= 5:
            ignored = sum(1 for log in dialog_logs if not log.user_responded)
            ignore_rate = ignored / len(dialog_logs)
            if ignore_rate > 0.7:
                # НЕ блокируем — увеличиваем cooldown для необязательных LOW
                # Re-engagement типы (dialog_followup, task_stale, profile_gap) НУЖНЫ 
                # чтобы вернуть пользователя в строй — их не трогаем
                RE_ENGAGEMENT_TYPES = {
                    'dialog_followup', 'task_stale', 'profile_gap',
                    'post_opportunity', 'channel_post',
                    'inactivity_reengagement',
                }
                OPTIONAL_LOW = {'market_insight', 'content_opportunity', 'event_discovery'}
                # Pre-load doubled-cooldown results per OPTIONAL_LOW type (avoid N+1 per anchor)
                _opt_low_anchors = [a for a in result if a.priority == AnchorPriority.LOW and a.anchor_type in OPTIONAL_LOW]
                _recent_opt_by_type: dict = {}
                for _opt_type in {a.anchor_type for a in _opt_low_anchors}:
                    _type_max_cd = max(((a.cooldown_hours or 8) * 2) for a in _opt_low_anchors if a.anchor_type == _opt_type)
                    _recent_opt_by_type[_opt_type] = session.query(Anchor).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == _opt_type,
                        Anchor.delivered_at.isnot(None),
                        Anchor.delivered_at >= now_utc - timedelta(hours=_type_max_cd)
                    ).first()
                # Необязательные LOW — удваиваем cooldown (через доп. фильтр)
                filtered = []
                for a in result:
                    if a.priority == AnchorPriority.LOW and a.anchor_type in OPTIONAL_LOW:
                        # Проверяем двойной cooldown
                        recent_opt = _recent_opt_by_type.get(a.anchor_type)
                        if recent_opt:
                            logger.debug(f"[ANCHOR] High ignore rate → doubled cooldown for {a.anchor_type}")
                            continue
                    filtered.append(a)
                result = filtered
                logger.info(f"[ANCHOR] User {user.telegram_id}: high ignore rate ({ignore_rate:.0%}), doubled cooldown for optional LOW (re-engagement kept)")

        return result

    # ═══════════════════════════════════════════════════════
    # AI DECISION LAYER
    # ═══════════════════════════════════════════════════════

    async def _process_post_anchor(self, user, anchor, session):
        """Обрабатывает постовый якорь: AI создаёт пост, публикует в ленту/канал."""
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ (race condition при деплое) ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Post anchor #{anchor.id} already delivered by another process, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены (в той же сессии для атомарности)
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            action = 'proactive_channel' if anchor.anchor_type == 'channel_post' else 'proactive_post'
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, action, session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск поста — нет токенов")
                    return
                spend_tokens(user.telegram_id, action, description=f'anchor_{anchor.anchor_type}', session=session, auto_commit=False)

            anchor_data = json.loads(anchor.data) if anchor.data else {}

            if anchor.anchor_type == 'post_opportunity':
                post_text = await self._ai_compose_post(user, anchor_data, session, mode='feed')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for feed post")
                    # Удаляем якорь (не помечаем delivered — иначе cooldown блокирует следующую попытку)
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                post = Post(
                    user_id=user.id,
                    username=user.username or user.first_name or f'user_{user.telegram_id}',
                    content=post_text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(post)
                session.flush()  # get post.id

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)

                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[FEED POST] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)

                # Логируем в AgentActivityLog (для отображения в дашборде)
                activity_log = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_newsfeed',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Лента новостей',
                    status='published',
                    ref_id=post.id,
                )
                session.add(activity_log)
                session.commit()

                # Авто-публикация в Discord (если webhook настроен)
                try:
                    if user.discord_webhook and user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                        import aiohttp as _aiohttp_dc
                        async with _aiohttp_dc.ClientSession() as http:
                            resp = await http.post(
                                user.discord_webhook,
                                json={"content": post_text},
                                timeout=_aiohttp_dc.ClientTimeout(total=15)
                            )
                            if resp.status in (200, 204):
                                dc_log = AgentActivityLog(
                                    user_id=user.id,
                                    activity_type='post_discord',
                                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                                    content=post_text,
                                    target='Discord канал',
                                    status='published',
                                )
                                session.add(dc_log)
                                session.commit()
                                logger.info(f"[ANCHOR] ✅ Auto-published feed post to Discord for {user.telegram_id}")
                            else:
                                logger.warning(f"[ANCHOR] Discord webhook failed ({resp.status}) for {user.telegram_id}")
                except Exception as dc_err:
                    logger.debug(f"[ANCHOR] Discord auto-publish failed (non-critical): {dc_err}")

                # Уведомляем пользователя
                if self.bot:
                    notify = (
                        f"Опубликовал пост в твою ленту:\n\n"
                        f"{post_text}\n\n"
                        f"Если не нравится — скажи, удалю."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                logger.info(f"[ANCHOR] ✅ Feed post for {user.telegram_id}: {post_text[:80]}...")

            elif anchor.anchor_type == 'channel_post':
                channel = anchor_data.get('channel', '')
                if not channel:
                    return

                post_text = await self._ai_compose_post(user, anchor_data, session, mode='channel')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for channel post")
                    # Удаляем якорь (не помечаем delivered — иначе cooldown блокирует следующую попытку)
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                # Публикуем в канал
                published = False
                if self.bot:
                    try:
                        await self.bot.send_message(chat_id=channel, text=post_text)
                        published = True
                    except Exception as pub_err:
                        logger.error(f"[ANCHOR] Channel publish error ({channel}): {pub_err}")

                # Помечаем якорь
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[CHANNEL {channel}] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # Уведомляем пользователя
                if self.bot:
                    status = "опубликован" if published else "не удалось опубликовать (проверь права бота в канале)"
                    notify = (
                        f"Пост в канал {channel} — {status}:\n\n"
                        f"{post_text[:500]}\n\n"
                        f"Если нужно поправить — скажи."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                status_icon = "✅" if published else "❌"
                logger.info(f"[ANCHOR] {status_icon} Channel post for {user.telegram_id} -> {channel}: {post_text[:80]}...")

            elif anchor.anchor_type == 'discord_post':
                discord_wh = anchor_data.get('discord_webhook', '') or getattr(user, 'discord_webhook', '')
                if not discord_wh:
                    return

                post_text = await self._ai_compose_post(user, anchor_data, session, mode='discord')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for discord post")
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                # Публикуем в Discord
                dc_ok = False
                try:
                    import aiohttp as _aiohttp_dp
                    async with _aiohttp_dp.ClientSession() as http_dc:
                        resp = await http_dc.post(
                            discord_wh,
                            json={"content": post_text},
                            timeout=_aiohttp_dp.ClientTimeout(total=15),
                        )
                        dc_ok = resp.status in (200, 204)
                except Exception as dc_err:
                    logger.error(f"[ANCHOR] Discord webhook error: {dc_err}")

                # Помечаем якорь
                anchor.delivered_at = datetime.now(timezone.utc)

                # Логируем в AgentActivityLog
                from models import AgentActivityLog as _AAL_dpost
                activity = _AAL_dpost(
                    user_id=user.id,
                    activity_type='post_discord',
                    title=post_text[:80],
                    content=post_text,
                    target='Discord канал',
                    status='published' if dc_ok else 'failed',
                )
                session.add(activity)

                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[DISCORD] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # Уведомляем пользователя
                if self.bot:
                    status = "опубликован" if dc_ok else "ошибка при публикации (проверь webhook)"
                    notify = (
                        f"Discord пост — {status}:\n\n"
                        f"{post_text[:500]}\n\n"
                        f"Если нужно поправить — скажи."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                status_icon = "✅" if dc_ok else "❌"
                logger.info(f"[ANCHOR] {status_icon} Discord post for {user.telegram_id}: {post_text[:80]}...")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_post_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _process_content_campaign_anchor(self, user, anchor, session):
        """Обрабатывает контент-кампанию: AI создаёт пост, публикует на указанные площадки.

        Работает аналогично _process_email_silent_anchor — автономно, без диалога с пользователем.
        Уведомляет пользователя о публикации.
        """
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Content campaign anchor #{anchor.id} already delivered, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, 'proactive_post', session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск контент-кампании — нет токенов")
                    return
                spend_tokens(user.telegram_id, 'proactive_post', description='content_campaign_publish', session=session, auto_commit=False)

            anchor_data = json.loads(anchor.data) if anchor.data else {}
            campaign_id = anchor_data.get('campaign_id')
            if not campaign_id:
                return

            campaign = session.query(ContentCampaign).filter_by(id=campaign_id).first()
            if not campaign or campaign.status != 'active':
                # Кампания удалена/остановлена — помечаем якорь доставленным, чтобы не срабатывал снова
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
                logger.info(f"[ANCHOR] Content campaign #{campaign_id} not active — marking anchor #{anchor.id} delivered")
                return

            platforms = anchor_data.get('platforms', ['feed'])
            campaign_goal = anchor_data.get('goal', '')
            topics = anchor_data.get('topics', '')
            tone = anchor_data.get('tone', 'professional')
            lang = anchor_data.get('language', 'ru')
            user_name = anchor_data.get('user_name', 'user')

            # AI генерирует пост
            post_text = await self._ai_compose_campaign_post(user, campaign, anchor_data, session)
            if not post_text:
                logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for content campaign #{campaign_id}")
                try:
                    session.delete(anchor)
                    session.commit()
                except Exception:
                    session.rollback()
                return

            published_to = []

            # --- Публикация в ленту ---
            if 'feed' in platforms:
                post = Post(
                    user_id=user.id,
                    username=user.username or user.first_name or f'user_{user.telegram_id}',
                    content=post_text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(post)
                session.flush()
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_newsfeed',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Лента новостей',
                    status='published',
                    ref_id=post.id,
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                published_to.append('лента')

            # --- Публикация в Telegram канал ---
            if 'telegram' in platforms and user.telegram_channel:
                tg_ok = False
                if self.bot:
                    try:
                        await self.bot.send_message(chat_id=user.telegram_channel, text=post_text)
                        tg_ok = True
                    except Exception as tg_err:
                        logger.error(f"[ANCHOR] Content campaign TG publish error: {tg_err}")
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_telegram',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target=user.telegram_channel,
                    status='published' if tg_ok else 'failed',
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                if tg_ok:
                    published_to.append(f'TG {user.telegram_channel}')

            # --- Публикация в Discord ---
            if 'discord' in platforms and user.discord_webhook:
                dc_ok = False
                try:
                    import aiohttp as _aiohttp_cc
                    if user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                        async with _aiohttp_cc.ClientSession() as http:
                            resp = await http.post(
                                user.discord_webhook,
                                json={"content": post_text},
                                timeout=_aiohttp_cc.ClientTimeout(total=15)
                            )
                            dc_ok = resp.status in (200, 204)
                except Exception as dc_err:
                    logger.error(f"[ANCHOR] Content campaign Discord publish error: {dc_err}")
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_discord',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Discord канал',
                    status='published' if dc_ok else 'failed',
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                if dc_ok:
                    published_to.append('Discord')

            # Обновляем кампанию
            campaign.posts_published = (campaign.posts_published or 0) + 1
            campaign.last_post_at = datetime.now(timezone.utc)

            # Помечаем якорь
            anchor.delivered_at = datetime.now(timezone.utc)
            log = AnchorDeliveryLog(
                user_id=user.id,
                anchor_ids=json.dumps([anchor.id]),
                message_text=f'[CONTENT CAMPAIGN #{campaign.id}] {post_text[:200]}',
                anchor_types=json.dumps([anchor.anchor_type]),
            )
            session.add(log)
            session.commit()

            # Уведомляем пользователя + сохраняем в историю чата для синхронизации
            if self.bot and published_to:
                platforms_str = ', '.join(published_to)
                notify = (
                    f" Контент-кампания «{campaign.name}» — пост #{campaign.posts_published}:\n\n"
                    f"{post_text[:500]}\n\n"
                    f"Опубликовано: {platforms_str}\n"
                    f"Если нужно поправить — скажи."
                )
                await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                # Синхронизация: сохраняем сообщение агента в историю чата
                try:
                    from ai_integration.conversation_history import save_message_to_history
                    save_message_to_history(user.telegram_id, 'assistant', notify, session=session)
                except Exception as _hist_err:
                    logger.debug(f"[ANCHOR] Failed to save campaign notify to history: {_hist_err}")
            logger.info(f"[ANCHOR] ✅ Content campaign #{campaign.id} post #{campaign.posts_published} for {user.telegram_id}: {published_to}")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_content_campaign_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _ai_compose_campaign_post(self, user, campaign, anchor_data: dict, session) -> str | None:
        """AI генерирует пост для контент-кампании.

        Отличается от _ai_compose_post тем, что использует цель/темы кампании,
        а не общие сигналы пользователя.
        """
        try:
            import aiohttp

            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_name = user.first_name or user.username or 'Пользователь'
            lang = anchor_data.get('language', 'ru')
            tone_map = {
                'professional': 'профессиональный, экспертный',
                'casual': 'разговорный, дружеский, неформальный',
                'motivational': 'мотивирующий, вдохновляющий',
                'expert': 'экспертный, аналитический, глубокий',
                'friendly': 'дружелюбный, лёгкий',
            }
            tone_desc = tone_map.get(anchor_data.get('tone', 'professional'), 'профессиональный')

            # Номер поста и формат для ротации
            post_num = (campaign.posts_published or 0) + 1
            _formats = [
                "КЕЙС: реальная ситуация → применение инструмента/подхода → конкретный результат в цифрах",
                "СОВЕТ + ПРИМЕР: практический лайфхак с конкретным примером применения",
                "СТАТИСТИКА + ВЫВОД: реальный факт или цифра → практический вывод для читателя",
                "СРАВНЕНИЕ ДО/ПОСЛЕ: как было без инструмента/подхода → как стало после",
                "РАЗВЕНЧАНИЕ МИФА: распространённое заблуждение → реальность + доказательство",
            ]
            post_format = _formats[(post_num - 1) % len(_formats)]

            # История предыдущих постов этой кампании (чтобы не повторяться)
            prev_posts_logs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.result == f'campaign:{campaign.id}',
                AgentActivityLog.status == 'published'
            ).order_by(AgentActivityLog.created_at.desc()).limit(3).all()
            prev_posts_texts = [p.content for p in prev_posts_logs if p.content]

            # Кто пишет — только роль/должность (НЕ интересы, чтобы не уводить тему)
            author_context = []
            if profile:
                if profile.position: author_context.append(f"Должность/роль: {profile.position}")
                if profile.about: author_context.append(f"О себе: {profile.about[:120]}")

            # DDG: свежий контекст из интернета по темам кампании
            # Делаем 2-3 разных запроса для более глубокого контента
            fresh_data = []
            try:
                from ai_integration.api_client import get_api_client
                api = get_api_client()
                search_query = (anchor_data.get('topics', '') or anchor_data.get('goal', ''))[:60]
                if search_query:
                    from datetime import datetime as dt
                    import asyncio as _aio_ddg
                    year = dt.now().strftime('%Y')
                    # Параллельные запросы для разностороннего контента
                    queries = [
                        f'{search_query} тренды {year}',
                        f'{search_query} советы лайфхаки примеры использования',
                        f'{search_query} кейсы автоматизация практика',
                    ]
                    tasks_ddg = [api.duckduckgo_search(q, num=3, cache_ttl=7200) for q in queries]
                    results_all = await _aio_ddg.gather(*tasks_ddg, return_exceptions=True)
                    seen_titles = set()
                    for batch in results_all:
                        if isinstance(batch, Exception) or not batch:
                            continue
                        for r in batch[:3]:
                            title = r.get('title', '')
                            if title and title not in seen_titles:
                                seen_titles.add(title)
                                fresh_data.append(f"  — {title}: {r.get('snippet', '')[:120]}")
                    # Ограничиваем до 6 самых релевантных
                    fresh_data = fresh_data[:6]
            except Exception:
                pass

            system_msg = (
                f"Ты — SMM-специалист, продвигающий конкретный продукт или идею в социальных сетях.\n\n"
                f"КАМПАНИЯ: {campaign.name}\n"
                f"ЦЕЛЬ КАМПАНИИ: {anchor_data.get('goal', 'не указана')}\n"
                f"ТЕМЫ: {anchor_data.get('topics', 'любые')}\n"
                f"ТОН: {tone_desc}\n"
                f"ПОСТ #{post_num}"
                f"{f' из {campaign.max_posts}' if campaign.max_posts else ''}\n"
                f"ФОРМАТ ЭТОГО ПОСТА: {post_format}\n\n"
                f"ПРАВИЛА:\n"
                f"1. Пиши от ПЕРВОГО лица, как будто сам пользователь\n"
                f"2. СТРОГО придерживайся цели и тем кампании — пиши ТОЛЬКО про них\n"
                f"3. ИГНОРИРУЙ любые личные интересы автора, не связанные с темой кампании\n"
                f"4. Каждый пост должен быть УНИКАЛЬНЫМ — НЕ повторяй предыдущие посты кампании\n"
                f"5. 3-8 предложений, {tone_desc} стиль\n"
                f"6. БЕЗ эмодзи, без хештегов, без призывов вроде 'подписывайтесь'\n"
                f"7. Если есть свежие данные из сети — используй их: цитируй статистику, упоминай конкретные примеры, ссылайся на реальные факты\n"
                f"8. Верни ТОЛЬКО текст поста. Ничего больше.\n"
                f"9. Пиши КОНКРЕТНО: не 'AI помогает в работе', а 'AI-агент за 15 секунд составляет email по 3 ключевым словам — экономит 20 минут'\n"
                f"10. Каждый пост = ОДНА практическая фишка/совет/кейс. Не пытайся охватить всё.\n\n"
                f"ВАЖНО: Тема кампании — приоритет №1. Посторонние темы ЗАПРЕЩЕНЫ."
            )

            user_prompt_parts = [f"Автор: {user_name}"]
            if author_context:
                user_prompt_parts.append("\nКОНТЕКСТ АВТОРА (используй для голоса, не для темы):")
                user_prompt_parts.extend(author_context)
            if prev_posts_texts:
                user_prompt_parts.append("\nПРЕДЫДУЩИЕ ПОСТЫ ЭТОЙ КАМПАНИИ (НЕ ПОВТОРЯЙ):")
                for _i, _pt in enumerate(prev_posts_texts, 1):
                    user_prompt_parts.append(f"  Пост {_i}: {_pt[:250]}")
            if fresh_data:
                user_prompt_parts.append("\nСВЕЖИЕ ДАННЫЕ ИЗ СЕТИ:")
                user_prompt_parts.extend(fresh_data)
            user_prompt_parts.append(
                f"\nНапиши пост #{post_num} строго по теме кампании: {anchor_data.get('topics', anchor_data.get('goal', campaign.name))}"
            )

            user_prompt = "\n".join(user_prompt_parts)

            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.85,
                "max_tokens": 600
            }

            async with aiohttp.ClientSession() as http:
                async with http.post(url, json=data, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        logger.error(f"[ANCHOR] AI compose campaign post error: HTTP {resp.status}")
                        return None
                    result = await resp.json()

            choice = result.get('choices', [{}])[0]
            text = choice.get('message', {}).get('content', '').strip()

            if not text or text.upper() == 'SKIP' or len(text) < 20:
                return None

            # Очистка: убираем обрамление кавычками если AI добавил
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text.startswith('«') and text.endswith('»'):
                text = text[1:-1]

            return text.strip()

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_campaign_post error: {e}")
            return None

    # ═══════════════════════════════════════════════════════
    # DELEGATION CAMPAIGN PROCESSOR — автономное делегирование
    # ═══════════════════════════════════════════════════════

    async def _process_delegation_campaign_anchor(self, user, anchor, session):
        """Обрабатывает якорь delegation_campaign_send: находит исполнителя, делегирует задачу.

        Работает автономно, без диалога с пользователем.
        Создаёт Task, отправляет уведомление исполнителю, обновляет счётчики кампании.
        """
        try:
            # ── Защита от дублей ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Delegation campaign anchor #{anchor.id} already delivered, skip")
                return
            anchor = fresh

            # Токены
            from token_service import check_and_deduct
            allowed = await check_and_deduct(user.telegram_id, 'delegate_task', session)
            if not allowed:
                logger.info(f"[ANCHOR] Delegation campaign anchor #{anchor.id}: insufficient tokens")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            anchor_data = json.loads(anchor.data or '{}')
            campaign_id = anchor_data.get('campaign_id')
            if not campaign_id:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            campaign = session.query(DelegationCampaign).filter_by(id=campaign_id).first()
            if not campaign or campaign.status != 'active':
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            candidate_username = anchor_data.get('candidate_username')
            if not candidate_username:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # Проверяем кандидата
            candidate = session.query(User).filter(
                User.username.ilike(candidate_username)
            ).first()
            if not candidate:
                logger.warning(f"[ANCHOR] Delegation campaign: candidate @{candidate_username} not found")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # Проверяем блокировку
            is_blocked = False
            try:
                from models import UserBlock
                is_blocked = session.query(UserBlock).filter(
                    ((UserBlock.blocker_id == user.id) & (UserBlock.blocked_id == candidate.id)) |
                    ((UserBlock.blocker_id == candidate.id) & (UserBlock.blocked_id == user.id))
                ).first()
            except Exception:
                pass  # UserBlock may not exist yet
            if is_blocked:
                logger.info(f"[ANCHOR] Delegation campaign: @{candidate_username} blocked, skip")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # ── Генерируем текст задачи через AI ──
            task_title, task_description, delegation_message = await self._ai_compose_delegation(
                user, campaign, anchor_data, candidate, session
            )
            if not task_title:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # ── Создаём задачу ──
            deadline_hours = anchor_data.get('default_deadline_hours', 48)
            now_utc = datetime.datetime.now(timezone.utc)
            due_date = now_utc + timedelta(hours=deadline_hours)

            task = Task(
                user_id=user.id,
                title=task_title[:500],
                description=task_description[:2000] if task_description else None,
                status='pending',
                priority='medium',
                due_date=due_date,
                delegated_by=user.id,
                delegated_to_username=candidate.username,
                delegation_status='pending',
                delegation_details=f"[Кампания «{campaign.name}» #{campaign.id}] {(campaign.offer or '')[:200]}",
                delegation_campaign_id=campaign.id,
            )
            session.add(task)
            session.flush()

            # ── Логируем активность ──
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='delegation',
                title=f'Делегировано @{candidate.username}: {task_title[:100]}',
                content=task_description[:500] if task_description else '',
                target=f'@{candidate.username}',
                status='pending',
                ref_id=str(task.id),
                result=f'campaign:{campaign.id}',
            )
            session.add(log_entry)

            # ── Обновляем счётчики кампании ──
            campaign.delegations_sent = (campaign.delegations_sent or 0) + 1
            campaign.last_delegation_at = now_utc

            # Проверяем достижение лимита
            if campaign.max_delegations and campaign.max_delegations > 0:
                if campaign.delegations_sent >= campaign.max_delegations:
                    campaign.status = 'completed'

            # ── Отправляем уведомление исполнителю ──
            if candidate.telegram_id and delegation_message:
                try:
                    from handlers import bot
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text='Принять', callback_data=f'accept_deleg_{task.id}'),
                            InlineKeyboardButton(text='Отклонить', callback_data=f'reject_deleg_{task.id}'),
                        ]
                    ])
                    await bot.send_message(
                        candidate.telegram_id,
                        delegation_message,
                        reply_markup=kb,
                        parse_mode='HTML',
                    )
                    logger.info(f"[ANCHOR] Delegation campaign #{campaign.id}: delegated «{task_title[:50]}» to @{candidate.username}")
                except Exception as e:
                    logger.warning(f"[ANCHOR] Delegation campaign: failed to notify @{candidate_username}: {e}")

            # ── Маркируем якорь ──
            anchor.delivered_at = now_utc
            anchor.delivery_result = f'delegated_task:{task.id}:@{candidate.username}'
            session.commit()

            # ── Уведомляем пользователя (кратко) ──
            try:
                from handlers import bot
                await bot.send_message(
                    user.telegram_id,
                    f"<b>Кампания «{campaign.name}»</b>\n"
                    f"Делегировано @{candidate.username}: {task_title[:100]}\n"
                    f"Отправлено {campaign.delegations_sent}"
                    f"{f'/{campaign.max_delegations}' if campaign.max_delegations else ''}",
                    parse_mode='HTML',
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[ANCHOR] _process_delegation_campaign_anchor error: {e}\n{traceback.format_exc()}")
            try:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
            except Exception:
                session.rollback()

    async def _ai_compose_delegation(self, user, campaign, anchor_data: dict, candidate, session) -> tuple:
        """Генерирует текст задачи и сообщение для делегирования через AI.

        Returns: (task_title, task_description, delegation_message) or (None, None, None)
        """
        try:
            candidate_profile = session.query(UserProfile).filter_by(user_id=candidate.id).first()
            candidate_info = ''
            if candidate_profile:
                parts = []
                if candidate_profile.bio:
                    parts.append(f"Bio: {candidate_profile.bio[:200]}")
                if candidate_profile.skills:
                    parts.append(f"Skills: {candidate_profile.skills[:200]}")
                if candidate_profile.interests:
                    parts.append(f"Interests: {candidate_profile.interests[:200]}")
                candidate_info = '\n'.join(parts)

            system_msg = (
                "Ты AI-менеджер проекта. Создаёшь задачу для делегирования конкретному исполнителю.\n"
                "Задача должна быть чёткой, конкретной и мотивирующей.\n"
                "Ответ СТРОГО в формате:\n"
                "TITLE: [краткое название задачи 5-15 слов]\n"
                "DESCRIPTION: [подробное описание: что сделать, ожидаемый результат, 2-4 предложения]\n"
                "MESSAGE: [личное сообщение исполнителю: представься, объясни задачу, мотивируй. 3-5 предложений. Без HTML.]"
            )
            user_msg = (
                f"Кампания: {campaign.name}\n"
                f"Цель кампании: {anchor_data.get('goal', '')[:300]}\n"
                f"Целевая аудитория: {anchor_data.get('target_audience', '')[:200]}\n"
                f"Шаблон задачи: {anchor_data.get('task_template', '')[:300]}\n"
                f"Предложение/мотивация: {anchor_data.get('offer', '')[:200]}\n"
                f"Тон: {anchor_data.get('tone', 'professional')}\n\n"
                f"ИСПОЛНИТЕЛЬ: @{candidate.username} ({candidate.first_name or 'user'})\n"
                f"{candidate_info}\n\n"
                f"ДЕЛЕГАТОР: {user.first_name or user.username}\n\n"
                f"Создай ЗАДАЧУ и СООБЩЕНИЕ ИСПОЛНИТЕЛЮ."
            )

            import aiohttp
            async with aiohttp.ClientSession() as http:
                resp = await http.post(
                    'https://api.deepseek.com/chat/completions',
                    headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                    json={
                        'model': 'deepseek-chat',
                        'messages': [
                            {'role': 'system', 'content': system_msg},
                            {'role': 'user', 'content': user_msg},
                        ],
                        'max_tokens': 600,
                        'temperature': 0.7,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                if resp.status != 200:
                    logger.warning(f"[ANCHOR] _ai_compose_delegation: API error {resp.status}")
                    return (None, None, None)
                data = await resp.json()
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')

            if not text:
                return (None, None, None)

            # Парсим ответ
            title = ''
            description = ''
            message = ''
            for line in text.split('\n'):
                line_s = line.strip()
                if line_s.upper().startswith('TITLE:'):
                    title = line_s[6:].strip()
                elif line_s.upper().startswith('DESCRIPTION:'):
                    description = line_s[12:].strip()
                elif line_s.upper().startswith('MESSAGE:'):
                    message = line_s[8:].strip()

            # Если DESCRIPTION/MESSAGE многострочные (всё что после TITLE/DESCRIPTION до следующего маркера)
            if not description and 'DESCRIPTION:' in text:
                parts = text.split('DESCRIPTION:')
                if len(parts) > 1:
                    desc_part = parts[1].split('MESSAGE:')[0].strip()
                    description = desc_part
            if not message and 'MESSAGE:' in text:
                parts = text.split('MESSAGE:')
                if len(parts) > 1:
                    message = parts[1].strip()

            if not title:
                title = f"Задача от {user.first_name or user.username}: {(campaign.goal or 'помощь')[:80]}"

            return (title[:500], description[:2000], message[:1500])

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_delegation error: {e}")
            return (None, None, None)

    async def _process_email_silent_anchor(self, user, anchor, session):
        """Обрабатывает email-якорь МОЛЧА: напрямую генерирует текст через AI и отправляет.

        Не отправляет сообщение пользователю — только выполняет email-действие.
        """
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Email anchor #{anchor.id} already delivered by another process, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            action = 'email_send' if anchor.anchor_type == 'email_outreach_send' else 'email_follow_up'
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, action, session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск email — нет токенов")
                    return

            anchor_data = json.loads(anchor.data) if anchor.data else {}

            if anchor.anchor_type == 'email_outreach_send':
                # ═══ ПРЯМАЯ ОТПРАВКА: AI пишет тексты → мы отправляем напрямую ═══
                campaign_id = anchor_data.get('campaign_id')
                if not campaign_id:
                    logger.info(f"[ANCHOR] Email anchor #{anchor.id}: no campaign_id, skip")
                    return

                # ── ПЕРЕЧИТЫВАЕМ draft'ы из БД (а не из JSON-снимка) чтобы не обработать уже отправленные ──
                live_drafts = session.query(EmailOutreach).filter_by(
                    campaign_id=campaign_id, status='draft'
                ).limit(10).all()
                if not live_drafts:
                    logger.info(f"[ANCHOR] Email anchor #{anchor.id}: no live drafts in DB, skip")
                    return

                from ai_integration.api_client import get_api_client
                from ai_integration.handlers import send_outreach_email
                api = get_api_client()

                campaign_name = anchor_data.get('campaign_name', '')
                campaign_goal = anchor_data.get('campaign_goal', '')
                target_audience = anchor_data.get('target_audience', '')
                offer = anchor_data.get('offer', '')
                tone = anchor_data.get('tone', 'professional')
                sender_name = anchor_data.get('sender_name', '')
                remaining = anchor_data.get('remaining_daily', 5)

                sent_count = 0
                for d_obj in live_drafts:
                    if sent_count >= remaining:
                        break
                    email = d_obj.recipient_email or ''
                    name = d_obj.recipient_name or '?'
                    company = d_obj.recipient_company or ''
                    context = d_obj.recipient_context or ''

                    # Определяем язык
                    _has_cyr = any('\u0400' <= c <= '\u04ff' for c in f"{name} {company} {context} {email}")
                    lang_hint = "Russian" if _has_cyr or any(email.endswith(d) for d in ['.ru', '.by', '.ua', '.kz']) else "English"

                    compose_prompt = (
                        f"Write a cold outreach email for this specific person.\n\n"
                        f"Campaign: {campaign_name}\nGoal: {campaign_goal}\n"
                        f"Offer: {offer}\nTone: {tone}\nSender: {sender_name}\n\n"
                        f"Recipient: {email}\nName: {name}\n"
                        f"{'Company/project: ' + company if company else ''}\n"
                        f"Research context about recipient: {context or 'none'}\n"
                        f"USE THE CONTEXT ABOVE to personalize the email! If context mentions specific "
                        f"projects, products, articles, or achievements — reference them in your opening.\n\n"
                        f"Language: {lang_hint}\n\n"
                        f"Return ONLY a JSON object: {{\"subject\": \"...\", \"body\": \"...\"}}\n\n"
                        f"STRICT QUALITY RULES:\n"
                        f"- Subject: 3-7 words, specific to THIS person, no spam words (free, amazing, opportunity)\n"
                        f"- Length: 120-200 words, 4-5 short paragraphs. NOT shorter — too short looks lazy.\n"
                        f"- PARAGRAPH BREAKS: separate every paragraph with a blank line (\\n\\n) in the body field. No HTML, no markdown.\n"
                        f"- STRUCTURE (follow this order):\n"
                        f"  1. RESEARCH HOOK (1-2 sent): show you studied their company/project. "
                        f"Mention a SPECIFIC product, feature, article, metric, or achievement. "
                        f"'I noticed your work in [broad field]' is TOO VAGUE. "
                        f"'Saw your [specific product/feature] — [specific observation]' is GOOD.\n"
                        f"  2. BRIDGE (1 sent): connect their work to yours — why them specifically.\n"
                        f"  3. VALUE (1-2 sent): what you do/offer concretely, what result.\n"
                        f"  4. PROOF (0-1 sent): one brief fact — users, traction, result. Optional.\n"
                        f"  5. QUESTION (1 sent): simple closing question — 'is this relevant?', 'worth a chat?'\n"
                        f"- First email = introduction + question, NOT a hard pitch. Don't sell, explore interest.\n"
                        f"- NO links, NO URLs, NO website mentions in first email\n"
                        f"- NO corporate buzzwords: 'streamlining workflows', 'leveraging synergies', 'driving innovation'\n"
                        f"- Write like a HUMAN colleague — warm, specific, genuine. Not a marketing bot.\n"
                        f"- No HTML, no markdown, no signatures"
                    )

                    try:
                        ai_result = await api.deepseek_analyze(
                            prompt=compose_prompt,
                            system_prompt="You write cold outreach emails. Return ONLY valid JSON with subject and body fields.",
                            max_tokens=500,
                            temperature=0.7,
                        )
                        if not ai_result:
                            logger.warning(f"[ANCHOR] AI compose failed for {email}: empty result")
                            continue

                        # Парсим JSON
                        import json as _json_compose
                        text = ai_result.strip()
                        if '```' in text:
                            for part in text.split('```'):
                                part = part.strip()
                                if part.startswith('json'):
                                    part = part[4:].strip()
                                if part.startswith('{'):
                                    text = part
                                    break
                        parsed = _json_compose.loads(text)
                        subject = parsed.get('subject', '')
                        body = parsed.get('body', '')

                        if not subject or not body:
                            logger.warning(f"[ANCHOR] AI compose: missing subject/body for {email}")
                            continue

                        # Отправляем напрямую через send_outreach_email
                        result = await send_outreach_email(
                            campaign_id=campaign_id,
                            recipient_email=email,
                            recipient_name=name if name != '?' else None,
                            recipient_company=company or None,
                            recipient_context=context or None,
                            subject=subject,
                            body=body,
                            user_id=user.telegram_id,
                            session=session,
                            close_session=False,
                        )
                        logger.info(f"[ANCHOR] Direct send to {email}: {(result or '')[:100]}")
                        if result and '' in result:
                            sent_count += 1
                        elif result and ('лимит' in result.lower() or 'limit' in result.lower()):
                            logger.info(f"[ANCHOR] Daily limit reached, stopping batch")
                            break

                    except Exception as _compose_err:
                        logger.error(f"[ANCHOR] Compose/send error for {email}: {_compose_err}")
                        continue

                logger.info(f"[ANCHOR] ✅ Direct email batch: sent {sent_count}/{len(live_drafts)} for campaign #{campaign_id}")

                # Списываем токены
                if not FREE_ACCESS_MODE and sent_count > 0:
                    spend_tokens(user.telegram_id, action, description=f'anchor_email_outreach_send x{sent_count}', session=session, auto_commit=False)

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_outreach_send: sent {sent_count}/{len(live_drafts)} emails for campaign «{campaign_name}»',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()
                return

            elif anchor.anchor_type == 'email_follow_up':
                # ═══ ПРЯМОЙ FOLLOW-UP: AI пишет текст → отправляем напрямую ═══
                from ai_integration.api_client import get_api_client
                from ai_integration.handlers import send_follow_up_email
                api = get_api_client()

                recipient_email = anchor_data.get('recipient_email', '')
                recipient_name = anchor_data.get('recipient_name', '')
                company_info = anchor_data.get('recipient_company', '')
                original_subject = anchor_data.get('original_subject', '')
                original_body = anchor_data.get('original_body', '')[:300]
                follow_up_number = anchor_data.get('follow_up_number', 1)
                days_since = anchor_data.get('days_since_sent', 0)
                outreach_id = anchor_data.get('outreach_id')

                _has_cyr = any('\u0400' <= c <= '\u04ff' for c in f"{recipient_name} {company_info} {original_subject} {original_body}")
                lang_hint = "Russian" if _has_cyr else "English"

                compose_prompt = (
                    f"Write a follow-up email (#{follow_up_number}) for an unanswered cold outreach.\n\n"
                    f"Campaign: {anchor_data.get('campaign_name', '')}\n"
                    f"Goal: {anchor_data.get('campaign_goal', '')}\n"
                    f"Original subject: {original_subject}\n"
                    f"Original email: {original_body}\n"
                    f"Recipient: {recipient_email} ({recipient_name})\n"
                    f"{'Company: ' + company_info if company_info else ''}\n"
                    f"Days since sent: {days_since}\n"
                    f"Language: {lang_hint}\n\n"
                    f"Return ONLY a JSON object: {{\"body\": \"...\"}}\n"
                    f"Rules: short (60-100 words), 2-3 paragraphs, add new value, don't repeat original, be polite, no pressure.\n"
                    f"PARAGRAPH BREAKS: separate every paragraph with a blank line (\\n\\n) in the body field. Plain text only."
                )

                try:
                    ai_result = await api.deepseek_analyze(
                        prompt=compose_prompt,
                        system_prompt="You write follow-up emails. Return ONLY valid JSON with body field.",
                        max_tokens=400,
                        temperature=0.7,
                    )
                    if ai_result:
                        import json as _json_fu
                        text = ai_result.strip()
                        if '```' in text:
                            for part in text.split('```'):
                                part = part.strip()
                                if part.startswith('json'):
                                    part = part[4:].strip()
                                if part.startswith('{'):
                                    text = part
                                    break
                        parsed = _json_fu.loads(text)
                        fu_body = parsed.get('body', '')

                        if fu_body and outreach_id:
                            result = await send_follow_up_email(
                                outreach_id=outreach_id,
                                body=fu_body,
                                user_id=user.telegram_id,
                                session=session,
                                close_session=False,
                            )
                            logger.info(f"[ANCHOR] Direct follow-up to {recipient_email}: {(result or '')[:100]}")
                except Exception as _fu_err:
                    logger.error(f"[ANCHOR] Follow-up compose/send error: {_fu_err}")

                # Списываем токены
                if not FREE_ACCESS_MODE:
                    spend_tokens(user.telegram_id, action, description=f'anchor_email_follow_up', session=session, auto_commit=False)

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_follow_up: follow-up #{follow_up_number} to {recipient_email}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()
                return

            elif anchor.anchor_type == 'email_need_leads':
                # Напрямую вызываем _auto_find_leads — без AI-модели
                campaign_id = anchor_data.get('campaign_id')
                if not campaign_id:
                    logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: no campaign_id, skip")
                    return

                from models import EmailCampaign
                campaign = session.query(EmailCampaign).filter_by(id=campaign_id).first()
                if not campaign or campaign.status != 'active':
                    logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: campaign not found or not active, skip")
                    return

                from ai_integration.handlers import _auto_find_leads
                count, msg = await _auto_find_leads(
                    campaign=campaign,
                    user=user,
                    target_audience=anchor_data.get('target_audience', campaign.target_audience or ''),
                    goal=anchor_data.get('campaign_goal', campaign.goal or ''),
                    offer=anchor_data.get('offer', campaign.offer or ''),
                    session=session,
                )
                logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: found {count} leads for campaign #{campaign_id}")

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_need_leads: found {count} new leads for campaign «{campaign.name}»',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()
                return
            else:
                return

        except Exception as e:
            logger.error(f"[ANCHOR] _process_email_silent_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _ai_compose_post(self, user, anchor_data: dict, session, mode: str = 'feed') -> str | None:
        """Просит AI создать пост на основе данных пользователя.

        AI получает ВСЕ сигналы и сам решает:
        - Стоит ли публиковать вообще (SKIP)
        - О чём написать
        - В каком стиле

        Args:
            mode: 'feed' | 'channel' | 'discord'
        Returns:
            str текст поста или None (если SKIP)
        """
        try:
            import aiohttp

            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_name = user.first_name or user.username or 'Пользователь'

            # Профиль
            profile_info = []
            if profile:
                if profile.skills: profile_info.append(f"Навыки: {profile.skills[:100]}")
                if profile.interests: profile_info.append(f"Интересы: {profile.interests[:100]}")
                if profile.goals: profile_info.append(f"Цели: {profile.goals[:100]}")
                if profile.position: profile_info.append(f"Должность: {profile.position}")
                if profile.city: profile_info.append(f"Город: {profile.city}")

            # Сигналы
            signals = anchor_data.get('signals', [])

            # DDG: подтягиваем свежий контекст из интернета по нише/интересам
            try:
                from ai_integration.api_client import get_api_client
                api = get_api_client()
                niche = ''
                if profile:
                    niche = (getattr(profile, 'interests', '') or getattr(profile, 'goals', '') or '')[:60]
                if niche:
                    from datetime import datetime as dt
                    fresh_query = f'{niche} тренды новости {dt.now().strftime("%Y")}'
                    fresh_results = await api.duckduckgo_search(fresh_query, num=3, cache_ttl=7200)
                    if fresh_results:
                        signals.append("СВЕЖИЕ ДАННЫЕ ИЗ СЕТИ:")
                        for r in fresh_results[:3]:
                            title = r.get('title', '')
                            snippet = r.get('snippet', '')[:120]
                            signals.append(f"  — {title}: {snippet}")
            except Exception as e:
                logger.debug(f"[ANCHOR] DDG post enrichment failed (non-critical): {e}")

            if mode == 'feed':
                system_msg = (
                    "Ты — автономный агент ASI Biont. Твоя задача — решить, стоит ли сделать пост в ленту "
                    "от лица пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если сигналов буквально 0 или профиль абсолютно пустой\n"
                    "2. Пиши от ПЕРВОГО лица, как будто сам пользователь делится с миром\n"
                    "3. Пост может быть О ЧЁМ УГОДНО: достижения, мысли, поиск людей, экспертное мнение, "
                    "итоги дня, просьба о помощи, инсайты, открытия, планы — выбери самое полезное\n"
                    "4. Даже 1 сигнал — достаточно для поста. Навыки или интересы из профиля = хороший повод для экспертного поста\n"
                    "5. Естественный, живой стиль. 3-6 предложений. БЕЗ эмодзи, без хештегов, без призывов к действию\n"
                    "6. НЕ ВЫДУМЫВАЙ факты. Основывайся ТОЛЬКО на реальных сигналах ниже\n"
                    "7. Верни ТОЛЬКО текст поста или SKIP. Ничего больше."
                )
            elif mode == 'discord':
                content_strategy = anchor_data.get('content_strategy', '')
                system_msg = (
                    "Ты — контент-менеджер, создающий посты для Discord-сервера пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если нет ни одного сигнала\n"
                    "2. Пиши от лица автора, живо и по-человечески — Discord ценит человечность\n"
                    "3. Допустимо использование Discord Markdown (**bold**, _italic_)\n"
                    f"4. Контент-стратегия: {content_strategy or 'не указана'}\n"
                    "5. 2-5 предложений — Discord-аудитория предпочитает лаконичность\n"
                    "6. Верни ТОЛЬКО текст поста или SKIP."
                )
            else:  # channel
                content_strategy = anchor_data.get('content_strategy', '')
                system_msg = (
                    "Ты — контент-менеджер для Telegram-канала пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если профиль абсолютно пустой и нет ни одного сигнала\n"
                    "2. Пиши от лица автора канала, экспертно и полезно\n"
                    "3. Пост должен нести ценность для аудитории канала\n"
                    f"4. Контент-стратегия: {content_strategy or 'не указана'}\n"
                    "5. 3-8 предложений, естественный стиль. Можно Markdown.\n"
                    "6. Верни ТОЛЬКО текст поста или SKIP."
                )

            # Собираем user prompt
            user_prompt_parts = [f"Пользователь: {user_name}"]

            if profile_info:
                user_prompt_parts.append("\nПРОФИЛЬ:")
                user_prompt_parts.extend(profile_info)

            if signals:
                user_prompt_parts.append(f"\nСИГНАЛЫ ({len(signals)}):")
                for s in signals:
                    user_prompt_parts.append(f"- {s}")
            elif mode in ('channel', 'discord'):
                # Для канала/Discord без сигналов — AI пишет на основе профиля/стратегии
                user_prompt_parts.append("\nСоздай пост на основе профиля и контент-стратегии.")

            user_prompt_parts.append("\nРешение: напиши пост или SKIP.")

            user_prompt = "\n".join(user_prompt_parts)

            # Прямой вызов AI API (без агентского пайплайна — посты не требуют tool calling)
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.8,
                "max_tokens": 600
            }

            async with aiohttp.ClientSession() as aio_session:
                async with aio_session.post(url, headers=headers, json=data, 
                                           timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200:
                        error = await response.text()
                        logger.error(f"[ANCHOR] Post AI API error: {response.status} {error[:200]}")
                        return None
                    result_json = await response.json()
                    text = result_json['choices'][0]['message']['content'].strip()

            if not text or text.upper() == 'SKIP' or text.upper().startswith('SKIP'):
                return None

            # Очистка: убираем кавычки если AI обернул
            post_text = text.strip().strip('"').strip("'")
            if len(post_text) < 20:
                return None

            return post_text

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_post error: {e}\n{traceback.format_exc()}")
            return None

    async def _ai_decide_and_compose(self, user, anchors: list, session) -> str | None:
        """AI получает все якоря + контекст и РЕШАЕТ: писать или нет + ЧТО писать.
        
        Никаких шаблонов. AI думает на основе полных данных.
        
        Returns:
            str — текст сообщения, или None если AI решил не писать.
        """
        try:
            # Собираем контекст
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()

            user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
            user_now = datetime.now(user_tz)

            # Якоря для AI
            anchor_descriptions = []
            for a in anchors:
                anchor_descriptions.append(a.to_ai_context())

            # Задачи
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.reminder_time.asc()).limit(8).all()

            task_lines = []
            for t in tasks:
                time_str = ""
                if t.reminder_time:
                    try:
                        rt = t.reminder_time if t.reminder_time.tzinfo else t.reminder_time.replace(tzinfo=timezone.utc)
                        rt_local = rt.astimezone(user_tz)
                        time_str = f" (→ {rt_local.strftime('%d.%m %H:%M')})"
                    except Exception:
                        pass
                desc = f" — {t.description[:80]}" if t.description else ""
                task_lines.append(f"• {t.title}{time_str}{desc}")

            # Завершённые задачи за сегодня — AI должен знать прогресс дня
            user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = user_today_start.astimezone(pytz.UTC)
            completed_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'completed',
                Task.actual_completion_time >= today_start_utc
            ).order_by(Task.actual_completion_time.desc()).limit(5).all()

            completed_lines = []
            for ct in completed_tasks:
                ct_time = ""
                if ct.actual_completion_time:
                    try:
                        act = ct.actual_completion_time if ct.actual_completion_time.tzinfo else ct.actual_completion_time.replace(tzinfo=timezone.utc)
                        act_local = act.astimezone(user_tz)
                        ct_time = f" (выполнено {act_local.strftime('%d.%m %H:%M')})"
                    except Exception:
                        pass
                completed_lines.append(f"{ct.title}{ct_time}")

            # Пропущенные задачи — AI знает проблемные паттерны
            skipped_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'skipped'
            ).order_by(Task.created_at.desc()).limit(3).all()

            skipped_lines = []
            for st in skipped_tasks:
                reason = ""
                if st.skipped_reason:
                    try:
                        from ai_integration.memory import decrypt_data
                        reason = f" — {decrypt_data(st.skipped_reason)[:60]}"
                    except Exception:
                        pass
                skipped_lines.append(f"{st.title}{reason}")

            # Общая статистика
            total_tasks = session.query(Task).filter(Task.user_id == user.id).count()
            total_completed = session.query(Task).filter(
                Task.user_id == user.id, Task.status == 'completed'
            ).count()
            completion_rate = round(total_completed / total_tasks * 100) if total_tasks > 3 else None

            # Цели
            goals = session.query(Goal).filter(
                Goal.user_id == user.id, Goal.status == 'active'
            ).limit(5).all()
            goal_lines = []
            for g in goals:
                if g.metric_target and g.metric_unit:
                    mc = int(g.metric_current or 0)
                    mt = int(g.metric_target)
                    line = f"• {g.title} ({mc}/{mt} {g.metric_unit}, {g.progress_percentage}%)"
                else:
                    line = f"• {g.title} ({g.progress_percentage}%)"
                if g.target_date:
                    days = g.days_until_target()
                    if days is not None:
                        line += f" дедлайн: {days}дн"
                goal_lines.append(line)

            # Профиль
            profile_lines = []
            if profile:
                if profile.skills: profile_lines.append(f"Навыки: {profile.skills[:80]}")
                if profile.interests: profile_lines.append(f"Интересы: {profile.interests[:80]}")
                if profile.goals: profile_lines.append(f"Цели: {profile.goals[:80]}")
                if profile.position: profile_lines.append(f"Должность: {profile.position}")
                if profile.city: profile_lines.append(f"Город: {profile.city}")

            # Последние сообщения пользователя
            recent_msgs = session.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'user'
            ).order_by(Interaction.created_at.desc()).limit(5).all()

            msg_lines = []
            for m in recent_msgs:
                age = (datetime.now(timezone.utc) - (m.created_at.replace(tzinfo=timezone.utc) if m.created_at.tzinfo is None else m.created_at))
                hours_ago = int(age.total_seconds() / 3600)
                msg_lines.append(f"[{hours_ago}ч назад] {(m.content or '')[:80]}")

            # Статистика доставок (для AI — чтобы знал контекст спама)
            recent_deliveries = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
            ).all()

            delivery_stats = f"Сообщений за 24ч: {len(recent_deliveries)}"
            if recent_deliveries:
                last_delivery = max(d.created_at for d in recent_deliveries)
                if last_delivery.tzinfo is None:
                    last_delivery = last_delivery.replace(tzinfo=timezone.utc)
                hours_since_last = (datetime.now(timezone.utc) - last_delivery).total_seconds() / 3600
                delivery_stats += f", последнее {int(hours_since_last)}ч назад"

            # Собираем промпт для AI
            # Баланс токенов — AI знает контекст пользователя
            from token_service import get_balance
            token_balance = get_balance(user.telegram_id)

            prompt_parts = [
                "Ты — AnchorEngine, мозг автономного агента ASI Biont.",
                "Ниже — сработавшие ЯКОРЯ (события/факты) + полный контекст пользователя.",
                "Твоя задача: РЕШИТЬ, стоит ли сейчас написать пользователю, и если да — НАПИСАТЬ сообщение.",
                "",
                f"БАЛАНС ТОКЕНОВ: {token_balance} (каждое проактивное сообщение = 15 токенов).",
                "Если у пользователя мало токенов — пиши только КРИТИЧНЫЕ вещи, экономь ресурс.",
                "Все функции открыты — ограничитель только баланс токенов.",
                "",
                "КАК ДУМАТЬ:",
                "Перед написанием задай себе 3 вопроса:",
                "1. Стоит ли это сообщение того, чтобы отвлечь человека? Если якоря слабые — верни SKIP. Лучше промолчать, чем отправить воду.",
                "2. Что я РЕАЛЬНО знаю? Вызови инструменты (research_topic, get_news_trends, list_tasks) — получи данные. Не выдумывай. Если не вызвал — не говори что 'проверил'.",
                "3. Какая сфера жизни этого человека сейчас требует внимания? Работа? Развитие? Здоровье? Отношения? Цели? Подумай, где он застрял или что упускает.",
                "",
                "ПРАВИЛА ДЛЯ ТИПОВ ЯКОРЕЙ:",
                "— task_reminder: ЗАПЛАНИРОВАННОЕ напоминание сработало по расписанию. Задержка до 30 мин в топике — это шаг сканирования, а НЕ просрочка задачи. НИКОГДА не называй задачу просроченной при task_reminder. Пиши только напоминание о задаче и уточни готовность.",
                "— task_overdue: задача РЕАЛЬНО просрочена (прошло >30 мин после дедлайна). Только тут уместно говорить о просрочке.",
                "— task_deadline_soon: дедлайн ещё не наступил, но приближается.",
                "",
                "ПРАВИЛА ДЛЯ ЯКОРЕЙ INSIGHTS/EVENTS:",
                "— event_discovery: В data есть web_events — РЕАЛЬНЫЕ мероприятия найденные в интернете. Расскажи про 1-2 самых релевантных с ссылками.",
                "— market_insight: В data есть fresh_insights — РЕАЛЬНЫЕ новости/тренды из интернета по нише пользователя. Перескажи самое важное со ссылками.",
                "— content_opportunity: В data есть content_ideas_from_web — реальные идеи контента. Предложи 1-2 конкретные темы на основе данных.",
                "",
                "ПРАВИЛА ДЛЯ EMAIL-ЯКОРЕЙ:",
                "— email_outreach_send: Отправь письма из черновиков кампании. В data будут drafts с email-адресами, именами, контекстом. Для каждого draft используй send_outreach_email с персонализированным subject и body. Пиши уникально для каждого получателя! Учитывай goal и offer кампании. Не уведомляй пользователя о каждом отправленном письме — просто делай молча (верни SKIP). ANTI-SPAM: система автоматически блокирует отправку контактам, которым уже писали из другой кампании за последние 30 дней или у которых ранее был bounced/failed. Если получишь предупреждение — пропусти этот контакт без повторных попыток.",
                "— email_follow_up: Отправь follow-up письмо. В data будет original_subject, original_body, follow_up_number. Используй send_outreach_email или reply_to_outreach_email. Будь ненавязчив. Верни SKIP.",
                "— email_reply_received: КРИТИЧНО! Кто-то ответил на наше письмо. В data: original_body (что МЫ писали изначально), reply_text (что ответил контакт), ai_previous_reply (наш предыдущий ответ, если был). СНАЧАЛА прочитай ВСЮ цепочку: original_body → reply_text → ai_previous_reply. Пойми что уже было сказано с обеих сторон и какие вопросы уже задавались. НЕ повторяй вопросы из original_body или ai_previous_reply — продолжай диалог ЛОГИЧНО дальше. Используй reply_to_outreach_email чтобы продолжить диалог в рамках campaign_goal. ОБЯЗАТЕЛЬНО уведоми пользователя о полученном ответе! ЭТО ЕДИНСТВЕННЫЙ email-якорь где надо НАПИСАТЬ пользователю. ВАЖНО ПРО ЗАВЕРШЕНИЕ: НЕ завершай кампанию и НЕ отмечай связанные задачи выполненными до тех пор, пока адресат явно и однозначно не подтвердит целевое действие (например — начало тестирования, встречу, оплату, регистрацию и т.п. в зависимости от campaign_goal). Слова 'интересно', 'расскажи больше', 'хорошо' — НЕ являются подтверждением. Цель считается достигнутой только когда получено явное подтверждение факта действия.",
                "— email_campaign_report: Отчёт по кампании. Напиши пользователю краткую сводку: отправлено, ответов, что дальше.",
                "— email_need_leads: Кампания активна, но ЧЕРНОВИКОВ НЕТ — система АВТОМАТИЧЕСКИ ищет новых контактов через GitHub API (публичные email разработчиков), DuckDuckGo (web search), сканирование страниц /contact /about. Цель: 20-50 контактов за один запуск. Верни SKIP — engine сам добавит лидов и запустит отправку.",
                "",
                "ПРАВИЛА ДЛЯ ЯКОРЯ ИНТЕГРАЦИИ:",
                "— integration_alert: скрипт агента вернул данные (Gmail, Ozon, RSS, CRM и др.). В data: snippet (вывод скрипта), signal (ключевое слово если есть), service_label. Прочитай snippet и САМИ РЕШИ имеет ли это ценность для пользователя прямо сейчас. CRITICAL/HIGH = пиши обязательно: один факт + один вопрос/действие. MEDIUM = пиши если есть конкретная новость (новое письмо, изменение статуса, достиженение/падение показателя). LOW = SKIP если рутина (пустой инбокс, список без изменений, технический вывод). НЕ пересказывай snippet — вычлени суть в 1-2 фразах.",
                "— agent_office_update: офисный координатор назначил конкретное действие агенту по целям пользователя. В data: plan (строка формата '[Имя агента]: [действие]'), agent_count, goal_count. Сообщи кратко — что конкретно предлагает сделать агент и к какой цели это относится. Спроси: 'Запустить?' или 'Дать команду агенту?'. Не пиши 'координатор запланировал' — пиши живо, как будто агент сам хочет взяться за дело.",
                "— agent_inbox_reply: КРИТИЧНО! Агент-почтовик нашёл новые входящие письма в inbox. В data: agent_name, reply_count, preview (краткая выжимка From/Subject). Напиши что агент {agent_name} обнаружил новые письма. Покажи preview. Спроси: хочет ли пользователь чтобы агент ответил на наиболее важные?",
                "— agent_task_blocked: МАЙОР! Агент застрял — ему нужно решение, доступ или подтверждение от пользователя. В data: agent_name, reason (первая строка с BLOCKED), full_context. Напиши что {agent_name} не может двигаться дальше без участия. Объясни причину и чётко спроси что нужно: 'Дать доступ?', 'Подтвердить?', 'Изменить направление?' — конкретный вопрос на основе reason.",
                "— agent_delegation: Агент выполнил задачу по целям пользователя. В data: agent_name, task, result. Кратко расскажи что агент сделал и каков результат. Если результат полезен — предложи следующий шаг.",
                "",
                "ПРАВИЛА ДЛЯ КОНТАКТНЫХ ЯКОРЕЙ:",
                "— contact_match: Найден НОВЫЙ специалист, подходящий под алерт контактов пользователя. В data: username, skill, interest, city, position. Напиши что нашёлся человек @username с релевантными навыками/интересами. Объясни ПОЧЕМУ этот контакт полезен (на основе профиля пользователя). Предложи конкретное действие: 'Написать ему?', 'Добавить в контакты?'. Укажи @username для связи.",
                "— contact_activity: Контакты в городе пользователя проявляют активность, совпадающую с его профилем. В data: city, user_profile, contacts (список с username, activities, skills, interests, position). Выбери 1-2 самых релевантных контакта. Объясни что именно они делают и как это пересекается с целями пользователя. Предложи связаться: 'Познакомиться с @username?' Не перечисляй всех — фокусируйся на самом интересном совпадении.",
                "— НЕ НАЧИНАЙ С ПРИВЕТСТВИЯ: никаких 'Привет!', 'Здравствуй!', 'Доброе утро!' и т.п. Сразу по делу — с факта, вопроса или наблюдения. Ты не здороваешься каждый раз, ты уже рядом.",
                "— ОДНА ТЕМА НА СООБЩЕНИЕ: выбери самый важный якорь и ФОКУСИРУЙСЯ на нём. НЕ пытайся охватить всё: если есть просроченная задача + пустой профиль + предложение — пиши ТОЛЬКО про просроченную задачу. Остальное — в следующий раз. Сообщение которое пытается решить 3 проблемы сразу = мусор.",
                "— Сначала данные (через инструменты), потом выводы. Не наоборот.",
                "— Персонализируй: используй профиль, историю, задачи. Не будь роботом.",
                "— Закончи конкретным вопросом или предложением действия, которое заставит ответить.",
                "— Если якорей несколько — выбери ОДИН самый актуальный и пиши ТОЛЬКО о нём.",
                "— Не создавай и не меняй задачи без просьбы пользователя.",
                "— Если предлагаешь задачу — ОБЯЗАТЕЛЬНО с ТОЧНЫМ временем (HH:MM), не 'на утро' или 'завтра'.",
                "",
                f"=== ВРЕМЯ ===",
                f"{user_now.strftime('%H:%M %d.%m.%Y')} ({user.timezone or 'Europe/Moscow'})",
                f"Баланс: {token_balance} токенов",
                f"{delivery_stats}",
            ]

            prompt_parts.append(f"\n=== ЯКОРЯ ({len(anchors)} шт) ===")
            # Типы якорей, для которых нужно передавать полные данные (data) в промпт
            EMAIL_DATA_TYPES = {'email_reply_received', 'email_outreach_send', 'email_follow_up', 'email_campaign_report', 'email_need_leads'}
            # Типы якорей с DDG-обогащением — показываем веб-данные
            DDG_ENRICHED_TYPES = {'event_discovery', 'market_insight', 'content_opportunity'}
            for i, ad in enumerate(anchor_descriptions, 1):
                prompt_parts.append(
                    f"{i}. [{ad['priority']}] {ad['type']}: {ad['topic']} "
                    f"(источник: {ad['source']}, возраст: {ad['age_minutes']}мин)"
                )
                # Для integration_alert и agent_office_update передаём данные в промпт
                INTEGRATION_DATA_TYPES = {'integration_alert', 'agent_office_update'}
                if ad.get('type') in INTEGRATION_DATA_TYPES and ad.get('data'):
                    if ad.get('type') == 'agent_office_update':
                        _plan = ad['data'].get('plan', '')
                        _ac = ad['data'].get('agent_count', '')
                        _gc = ad['data'].get('goal_count', '')
                        if _plan:
                            prompt_parts.append(
                                f"   Офисный план ({_gc} целей, {_ac} агентов): {_plan}"
                            )
                    else:
                        _sn = ad['data'].get('snippet', '')
                        _sl = ad['data'].get('service_label', '')
                        _sig = ad['data'].get('signal', '')
                        if _sn or _sl:
                            prompt_parts.append(
                                f"   Данные [{_sl}]: {_sn[:400]}"
                                + (f" (сигнал: {_sig})" if _sig else '')
                            )
                # Для контактных якорей передаём данные о контактах
                CONTACT_DATA_TYPES = {'contact_match', 'contact_activity', 'agent_delegation'}
                if ad.get('type') in CONTACT_DATA_TYPES and ad.get('data'):
                    d = ad['data']
                    if ad['type'] == 'contact_match':
                        prompt_parts.append(f"   @{d.get('username','')}: навык={d.get('skill','')}, интерес={d.get('interest','')}, город={d.get('city','')}, должность={d.get('position','')}")
                    elif ad['type'] == 'contact_activity':
                        contacts = d.get('contacts', [])
                        up = d.get('user_profile', {})
                        prompt_parts.append(f"   Город: {d.get('city','')}, профиль: {up.get('skills','')} / {up.get('interests','')}")
                        for c in contacts[:3]:
                            acts = ', '.join(c.get('activities', [])[:3])
                            prompt_parts.append(f"   • @{c.get('username','')}: {c.get('position','')} | {c.get('skills','')} | активности: {acts}")
                    elif ad['type'] == 'agent_delegation':
                        prompt_parts.append(f"   Агент: {d.get('agent_name','')}, задача: {d.get('task','')}")
                        _res = str(d.get('result', ''))[:300]
                        if _res:
                            prompt_parts.append(f"   Результат: {_res}")
                # Для agent_inbox_reply и agent_task_blocked передаём preview/reason
                OFFICE_DATA_TYPES = {'agent_inbox_reply', 'agent_task_blocked'}
                if ad.get('type') in OFFICE_DATA_TYPES and ad.get('data'):
                    d = ad['data']
                    if ad['type'] == 'agent_inbox_reply':
                        _pv = d.get('preview', '')
                        _rc = d.get('reply_count', '')
                        if _pv:
                            prompt_parts.append(f"   Агент: {d.get('agent_name','')}, писем: {_rc}")
                            prompt_parts.append(f"   Preview: {_pv[:200]}")
                    elif ad['type'] == 'agent_task_blocked':
                        _reason = d.get('reason', '')
                        _ctx = d.get('full_context', '')
                        if _reason:
                            prompt_parts.append(f"   Агент: {d.get('agent_name','')}")
                            prompt_parts.append(f"   Причина: {_reason[:200]}")
                            if _ctx and len(_ctx) > len(_reason):
                                prompt_parts.append(f"   Контекст: {_ctx[:300]}")
            # Для email-якорей передаём полные данные — AI нужны outreach_id, reply_text, campaign_goal
                if ad.get('type') in EMAIL_DATA_TYPES and ad.get('data'):
                    data = ad['data']
                    data_lines = []
                    for key in ('campaign_id', 'campaign_name', 'campaign_goal', 'outreach_id',
                                'recipient_email', 'recipient_name', 'recipient_company',
                                'original_subject', 'original_body', 'reply_text',
                                'ai_previous_reply', 'offer', 'tone', 'sender_name', 'sender_email',
                                'drafts', 'remaining_daily', 'remaining_total',
                                'follow_up_number', 'days_since_sent'):
                        if key in data and data[key] is not None:
                            val = data[key]
                            if isinstance(val, str) and len(val) > 500:
                                val = val[:500] + '...'
                            data_lines.append(f"   {key}: {val}")
                    if data_lines:
                        prompt_parts.append("   --- DATA ---")
                        prompt_parts.extend(data_lines)

                # Для DDG-обогащённых якорей — показываем реальные результаты веб-поиска
                if ad.get('type') in DDG_ENRICHED_TYPES and ad.get('data'):
                    data = ad['data']
                    web_keys = {'web_events': ' МЕРОПРИЯТИЯ ИЗ СЕТИ', 'fresh_insights': ' СВЕЖИЕ ДАННЫЕ ИЗ СЕТИ', 'content_ideas_from_web': ' ИДЕИ ИЗ СЕТИ'}
                    for web_key, label in web_keys.items():
                        items = data.get(web_key, [])
                        if items:
                            prompt_parts.append(f"   --- {label} ---")
                            for item in items[:5]:
                                title = item.get('title', '')
                                snippet = item.get('snippet', '')[:150]
                                url = item.get('url', '')
                                prompt_parts.append(f"   • {title}")
                                if snippet:
                                    prompt_parts.append(f"     {snippet}")
                                if url:
                                    prompt_parts.append(f"     {url}")

            if task_lines:
                prompt_parts.append(f"\n=== АКТИВНЫЕ ЗАДАЧИ ({len(tasks)}) ===")
                prompt_parts.extend(task_lines)

            if completed_lines:
                prompt_parts.append(f"\n=== НЕДАВНО ЗАВЕРШЕНО ({len(completed_tasks)}) ===")
                prompt_parts.extend(completed_lines)

            if skipped_lines:
                prompt_parts.append(f"\n=== ПРОПУЩЕНО ===")
                prompt_parts.extend(skipped_lines)

            if completion_rate is not None:
                prompt_parts.append(f"\nВыполненность задач: {completion_rate}% ({total_completed}/{total_tasks})")

            if goal_lines:
                prompt_parts.append(f"\n=== ЦЕЛИ ===")
                prompt_parts.extend(goal_lines)

            if profile_lines:
                prompt_parts.append(f"\n=== ПРОФИЛЬ ===")
                prompt_parts.extend(profile_lines)

            if msg_lines:
                prompt_parts.append(f"\n=== ПОСЛЕДНИЕ СООБЩЕНИЯ ===")
                prompt_parts.extend(msg_lines)

            full_prompt = "\n".join(prompt_parts)

            # Вызываем AI через агента (с tool calling — может использовать research_topic, etc.)
            from ai_integration.autonomous_agent import get_autonomous_agent
            agent = get_autonomous_agent()

            logger.info(f"[ANCHOR] AI call for user {user.telegram_id}: {len(anchors)} anchors, prompt {len(full_prompt)} chars")

            result = await agent.generate_system_message(
                user_id=user.telegram_id,
                mode='anchor',
                instruction="Подумай о ситуации этого человека. Вызови инструменты по релевантным темам из якорей — research_topic или get_news_trends. На основе реальных данных реши: стоит ли писать (или SKIP). Если пишешь — покажи что нашёл и задай вопрос, который двигает вперёд.",
                extra_context=full_prompt,
                max_tokens=1500,
                max_iterations=3
            )

            logger.info(f"[ANCHOR] AI result for user {user.telegram_id}: {'SKIP/None' if not result else result[:100]}")

            if not result or result.strip().upper() == 'SKIP':
                return None

            # Убираем "SKIP" если AI начал писать но потом решил не стоит
            if result.strip().upper().startswith('SKIP'):
                return None

            return result.strip()

        except Exception as e:
            logger.error(f"[ANCHOR] AI decision error: {e}\n{traceback.format_exc()}")
            return None

    # ═══════════════════════════════════════════════════════
    # DELIVER
    # ═══════════════════════════════════════════════════════

    async def _deliver(self, user, anchors: list, message: str, session):
        """Отправляет сообщение и записывает лог. Списывает токены."""
        try:
            now_utc = datetime.now(timezone.utc)

            # ── ЗАЩИТА ОТ ДУБЛЕЙ (race condition при деплое / 2 инстанса) ──
            # Перечитываем якоря из БД — может другой процесс уже доставил
            still_pending = []
            for anchor in anchors:
                fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
                if fresh and fresh.delivered_at is None:
                    still_pending.append(fresh)
            if not still_pending:
                logger.info(f"[ANCHOR] User {user.telegram_id}: all anchors already delivered by another process, skip")
                return
            anchors = still_pending

            # ── CROSS-PROCESS DUPLICATE GUARD ──
            # Два инстанса могут создать разные DB-строки для одного логического якоря.
            # Если те же anchor_types уже доставлялись в последние 2 мин — это дубль.
            current_types = set(a.anchor_type for a in anchors)
            very_recent_logs = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(minutes=2)
            ).all()
            for log in very_recent_logs:
                try:
                    logged_types = set(json.loads(log.anchor_types) if log.anchor_types else [])
                except Exception:
                    logged_types = set()
                overlap = current_types & logged_types
                if overlap:
                    logger.info(f"[ANCHOR] User {user.telegram_id}: cross-process duplicate detected (types: {overlap}), marking and skip")
                    for anchor in anchors:
                        anchor.delivered_at = now_utc
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                    return
            recent_delivery = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(minutes=MIN_PROACTIVE_GAP_MINUTES)
            ).first()
            if recent_delivery:
                logger.info(f"[ANCHOR] User {user.telegram_id}: delivery gap too small ({MIN_PROACTIVE_GAP_MINUTES}min), skip")
                return

            # Проверяем и списываем токены за проактивное сообщение (в той же сессии для атомарности)
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, 'proactive_message', session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск доставки — нет токенов")
                    return
                spend_tokens(user.telegram_id, 'proactive_message', description='proactive anchor', session=session, auto_commit=False)

            # Помечаем якоря как доставленные
            anchor_ids = []
            anchor_types = []
            for anchor in anchors:
                anchor.delivered_at = now_utc
                anchor_ids.append(anchor.id)
                anchor_types.append(anchor.anchor_type)
                # Для task_reminder: ставим reminder_sent=True ЗДЕСЬ (при реальной доставке)
                if anchor.anchor_type in ('task_reminder', 'task_overdue') and anchor.source and anchor.source.startswith('task:'):
                    try:
                        tid = int(anchor.source.split(':')[1])
                        src_task = session.query(Task).filter_by(id=tid).first()
                        if src_task and not src_task.reminder_sent:
                            src_task.reminder_sent = True
                    except Exception:
                        pass

            # Определяем, связаны ли якоря с конкретным агентом
            AGENT_ANCHOR_TYPES = {'agent_inbox_reply', 'agent_task_blocked', 'agent_office_update', 'integration_alert'}
            _agent_name = None
            for anchor in anchors:
                if anchor.anchor_type in AGENT_ANCHOR_TYPES and anchor.data:
                    try:
                        _ad = json.loads(anchor.data) if isinstance(anchor.data, str) else anchor.data
                        _agent_name = _ad.get('agent_name') or _ad.get('agent')
                        if _agent_name:
                            break
                    except Exception:
                        pass

            # Оборачиваем контент в __agent JSON, если есть агент
            interaction_content = message
            if _agent_name:
                try:
                    from models import UserAgent
                    _ua = session.query(UserAgent).filter(
                        UserAgent.user_id == user.id,
                        UserAgent.name == _agent_name,
                    ).first()
                    if _ua:
                        interaction_content = json.dumps({
                            '__agent': {
                                'name': _ua.name,
                                'id': _ua.id,
                                'avatar_url': _ua.avatar_url or '',
                            },
                            'text': message,
                        }, ensure_ascii=False)
                except Exception:
                    pass

            # Создаём запись в interactions (для совместимости с anti-spam логикой)
            interaction = Interaction(
                user_id=user.id,
                message_type='proactive',
                content=interaction_content
            )
            session.add(interaction)

            # Создаём delivery log
            log = AnchorDeliveryLog(
                user_id=user.id,
                anchor_ids=json.dumps(anchor_ids),
                message_text=message,
                anchor_types=json.dumps(anchor_types),
            )
            session.add(log)

            # Отправляем через бот ПЕРЕД commit — если отправка не удалась, откатываем
            if self.bot:
                try:
                    # Гарантируем кликабельность URL через HTML parse_mode
                    import html as html_mod
                    url_re = re.compile(r'(https?://\S+)')
                    spaced = re.sub(r'(?<=[^\s\n])(https?://)', r' \1', message)
                    parts = url_re.split(spaced)
                    html_parts = []
                    for idx, part in enumerate(parts):
                        if idx % 2 == 0:
                            html_parts.append(html_mod.escape(part))
                        else:
                            clean = part.rstrip('.,;:!?)—»')
                            trailing = part[len(clean):]
                            html_parts.append(f'<a href="{html_mod.escape(clean)}">{html_mod.escape(clean)}</a>{html_mod.escape(trailing)}')
                    send_html = ''.join(html_parts)
                    try:
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=send_html,
                            parse_mode='HTML'
                        )
                    except Exception:
                        # Fallback без HTML
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=message
                        )
                    # Синхронизация: сохраняем в историю чата
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', message, session=session)
                    except Exception: pass
                    session.commit()
                    logger.info(f"[ANCHOR] ✅ Delivered to {user.telegram_id}: {message[:80]}...")
                except Exception as send_err:
                    logger.error(f"[ANCHOR] Send failed to {user.telegram_id}: {send_err}")
                    session.rollback()
            else:
                session.commit()
                logger.info(f"[ANCHOR] Message (no bot): {message[:80]}...")

        except Exception as e:
            logger.error(f"[ANCHOR] Deliver error: {e}")
            session.rollback()

    # ═══════════════════════════════════════════════════════
    # FEEDBACK — отслеживание реакций
    # ═══════════════════════════════════════════════════════

    async def record_user_response(self, user_id: int):
        """Вызывается когда пользователь отвечает — помечает последнюю доставку как responded.
        
        Интегрируется в основной обработчик сообщений.
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return

            now_utc = datetime.now(timezone.utc)

            # Находим последнюю доставку за последний час
            recent_log = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(hours=1),
                AnchorDeliveryLog.user_responded.is_(None)
            ).order_by(AnchorDeliveryLog.created_at.desc()).first()

            if recent_log:
                recent_log.user_responded = True
                response_time = (now_utc - recent_log.created_at.replace(tzinfo=timezone.utc)).total_seconds()
                recent_log.response_time_seconds = int(response_time)

                # Помечаем якоря как responded
                try:
                    ids = json.loads(recent_log.anchor_ids)
                    if ids:
                        # Batch-load anchors (avoid N+1)
                        _resp_anchors = session.query(Anchor).filter(Anchor.id.in_(ids)).all()
                        _resp_anchor_map = {a.id: a for a in _resp_anchors}
                        for aid in ids:
                            anchor = _resp_anchor_map.get(aid)
                            if anchor:
                                anchor.user_reaction = 'responded'
                                anchor.reaction_at = now_utc
                except Exception:
                    pass

                session.commit()
                logger.debug(f"[ANCHOR] Recorded response from {user_id} ({int(response_time)}s)")

        except Exception as e:
            logger.error(f"[ANCHOR] Record response error: {e}")
            session.rollback()
        finally:
            session.close()

    async def mark_ignored_deliveries(self):
        """Периодическая задача: помечает доставки старше 1ч без ответа как ignored"""
        session = Session()
        try:
            now_utc = datetime.now(timezone.utc)
            cutoff = now_utc - timedelta(hours=1)

            unresolved = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_responded.is_(None),
                AnchorDeliveryLog.created_at < cutoff
            ).all()

            # Batch-load all anchor IDs across all unresolved logs (avoid N+1)
            _all_aids: set = set()
            _log_ids_map: dict = {}
            for log in unresolved:
                log.user_responded = False
                try:
                    ids = json.loads(log.anchor_ids)
                    _log_ids_map[log.id] = ids
                    _all_aids.update(ids)
                except Exception:
                    pass

            _ignored_anchor_map = {}
            if _all_aids:
                _ignored_anchors = session.query(Anchor).filter(Anchor.id.in_(list(_all_aids))).all()
                _ignored_anchor_map = {a.id: a for a in _ignored_anchors}

            for log in unresolved:
                try:
                    for aid in _log_ids_map.get(log.id, []):
                        anchor = _ignored_anchor_map.get(aid)
                        if anchor and not anchor.user_reaction:
                            anchor.user_reaction = 'ignored'
                            anchor.reaction_at = now_utc
                except Exception:
                    pass

            if unresolved:
                session.commit()
                logger.debug(f"[ANCHOR] Marked {len(unresolved)} deliveries as ignored")

        except Exception as e:
            logger.error(f"[ANCHOR] Mark ignored error: {e}")
            session.rollback()
        finally:
            session.close()

    # ═══════════════════════════════════════════════════════
    def _scan_service_degraded(self, user, session, now_utc) -> list:
        """Создаёт якорь когда один или больше внешних сервисов сломаны."""
        anchors = []
        try:
            from ai_integration.service_health import get_status
            errors = get_status()
            if not errors:
                return anchors

            _labels = {
                'resend': 'email-рассылка', 'deepseek': 'AI-модель',
                'newsapi': 'новости', 'ddg': 'веб-поиск',
                'openweathermap': 'погода', 'payments': 'платёжная система',
                'github': 'поиск контактов',
            }
            affected_ru = [_labels.get(s, s) for s in errors]
            affected_en = list(errors.keys())

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='service_degraded',
                source=f'service_health:{",".join(sorted(errors.keys()))}',
                topic=_t(user,
                    f'Проблемы с сервисами: {", ".join(affected_ru)}',
                    f'Service issues: {", ".join(affected_en)}'),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'services': list(errors.keys()), 'count': len(errors)}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=4,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] service_degraded scan error: {e}')
        return anchors

    def _scan_payment_failed(self, user, session, now_utc) -> list:
        """Уведомляет если последняя попытка пополнить токены завершилась ошибкой."""
        anchors = []
        try:
            from models import PaymentHistory
            since = now_utc - timedelta(days=2)
            recent = session.query(PaymentHistory).filter(
                PaymentHistory.user_id == user.id,
                PaymentHistory.action == 'payment_failed',
                PaymentHistory.created_at >= since,
            ).order_by(PaymentHistory.created_at.desc()).first()

            if not recent:
                return anchors

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='payment_failed',
                source=f'payment:{recent.id}',
                topic=_t(user,
                    'Последний платёж не прошёл — токены не зачислены. Попробуй снова: /buy',
                    'Last payment failed — tokens not credited. Try again: /buy'),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'payment_id': recent.payment_id, 'amount': str(recent.amount or '')}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=3),
                cooldown_hours=12,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] payment_failed scan error: {e}')
        return anchors

    def _scan_custom_anchors(self, user, session, user_tz, user_now, now_utc) -> list:
        """Сканирует UserAgent.custom_anchors — создаёт якоря по расписанию/триггерам,
        заданным автором агента.

        Формат каждого элемента custom_anchors (JSON array):
        {
            "id": "daily-report",          // уникальный id (для dedup)
            "topic": "Ежедневный отчёт",   // тема якоря
            "anchor_type": "custom_anchor",// тип (если не задан — custom_anchor)
            "priority": "MEDIUM",          // CRITICAL / HIGH / MEDIUM / LOW
            "schedule_time": "09:00",      // необязательно: время дня HH:MM (окно ±30 мин)
            "cooldown_hours": 20,          // необязательно (default 20)
            "data": {}                     // необязательно: дополнительные поля в anchor.data
        }
        """
        anchors = []
        try:
            from models import UserAgent
            agents = session.query(UserAgent).filter(
                UserAgent.author_id == user.id,
                UserAgent.status == 'active',
                UserAgent.custom_anchors.isnot(None),
            ).all()

            for agent in agents:
                try:
                    custom_list = json.loads(agent.custom_anchors)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(custom_list, list):
                    continue

                for entry in custom_list:
                    if not isinstance(entry, dict):
                        continue

                    entry_id = str(entry.get('id') or entry.get('topic') or 'default')
                    topic = entry.get('topic') or f'Агент {agent.name}: кастомный якорь'
                    anchor_type = entry.get('anchor_type') or 'custom_anchor'
                    priority_str = str(entry.get('priority', 'MEDIUM')).upper()
                    # Cooldown соответствует настройке частоты агента (run_interval_minutes).
                    # Пользователь задаёт интервал при создании агента — он же задаёт
                    # частоту отчётов. Значение из custom_anchors является fallback.
                    if agent.run_interval_minutes and agent.run_interval_minutes > 0:
                        cooldown_h = agent.run_interval_minutes / 60.0
                    else:
                        cooldown_h = float(entry.get('cooldown_hours', 20))
                    schedule_time = entry.get('schedule_time')  # "HH:MM"

                    # Проверяем расписание: окно ±29 мин от schedule_time
                    if schedule_time:
                        try:
                            sched_h, sched_m = map(int, schedule_time.split(':'))
                            target_minutes = sched_h * 60 + sched_m
                            now_minutes = user_now.hour * 60 + user_now.minute
                            diff = abs(now_minutes - target_minutes)
                            # Учитываем переход через полночь
                            diff = min(diff, 24 * 60 - diff)
                            if diff > 29:
                                continue  # ещё не время (или уже прошло)
                        except (ValueError, AttributeError):
                            pass  # без расписания — всегда активен

                    priority_map = {
                        'CRITICAL': AnchorPriority.CRITICAL,
                        'HIGH': AnchorPriority.HIGH,
                        'MEDIUM': AnchorPriority.MEDIUM,
                        'LOW': AnchorPriority.LOW,
                    }
                    priority = priority_map.get(priority_str, AnchorPriority.MEDIUM)

                    source = f'agent:{agent.id}:custom:{entry_id}'

                    extra_data = {'agent_name': agent.name, 'agent_id': agent.id, 'entry_id': entry_id}
                    if isinstance(entry.get('data'), dict):
                        extra_data.update(entry['data'])

                    bg = BATCH_GROUPS.get(anchor_type, 'integration')

                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type=anchor_type,
                        source=source,
                        topic=topic,
                        priority=priority,
                        data=json.dumps(extra_data, ensure_ascii=False),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=cooldown_h * 1.5),
                        cooldown_hours=cooldown_h,
                        batch_group=bg,
                    ))
        except Exception as e:
            logger.debug(f'[ANCHOR] custom_anchors scan error: {e}')
        return anchors

    def _scan_agent_inbox_replies(self, user, session, now_utc) -> list:
        """Создаёт CRITICAL-якорь когда агент-почтовик нашёл новые входящие письма.
        Источник: AgentActivityLog(activity_type='inbox_reply', status='new').
        После создания якоря помечаем записи статусом 'anchored' во избежание повторов.
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=4)
            recs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'inbox_reply',
                AgentActivityLog.status == 'new',
                AgentActivityLog.created_at >= since,
            ).order_by(AgentActivityLog.created_at.desc()).limit(10).all()

            for rec in recs:
                agent_name = (rec.target or 'агент').replace('agent:', '')
                reply_count = (rec.title or '').split(':')[1].strip() if ':' in (rec.title or '') else ''
                # Кратко первые 2 письма из stdout для превью
                _preview = ''
                _stdout = rec.content or ''
                _lines = [l.strip() for l in _stdout.splitlines() if l.strip()]
                _from_lines = [l for l in _lines if l.startswith('От:') or l.startswith('Тема:')]
                _preview = ' | '.join(_from_lines[:4])[:200]

                source_key = f'inbox_reply:{rec.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_inbox_reply',
                    source=source_key,
                    topic=_t(user,
                        f'{agent_name}: новые входящие ({reply_count})',
                        f'{agent_name}: new inbox messages ({reply_count})'),
                    priority=AnchorPriority.CRITICAL,
                    data=json.dumps({
                        'agent_name': agent_name,
                        'reply_count': reply_count,
                        'preview': _preview,
                        'log_id': rec.id,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=6),
                    cooldown_hours=0,
                    batch_group='office',
                ))
                # Помечаем как обработанный чтобы не создавать дубли
                rec.status = 'anchored'
            if recs:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_inbox_replies scan error: {e}')
        return anchors

    def _scan_agent_task_blocked(self, user, session, now_utc) -> list:
        """Создаёт HIGH-якорь когда агент сигнализирует BLOCKED — нужно решение пользователя.
        Источник: AgentActivityLog(activity_type='task_blocked', status='new').
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=8)
            recs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'task_blocked',
                AgentActivityLog.status == 'new',
                AgentActivityLog.created_at >= since,
            ).order_by(AgentActivityLog.created_at.desc()).limit(5).all()

            for rec in recs:
                agent_name = (rec.target or 'агент').replace('agent:', '')
                # Первая строка ответа агента = причина блокировки
                _reason = (rec.content or '').splitlines()[0][:200] if rec.content else ''

                source_key = f'task_blocked:{rec.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_task_blocked',
                    source=source_key,
                    topic=_t(user,
                        f'{agent_name} застрял — нужно ваше решение',
                        f'{agent_name} is blocked — needs your decision'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({
                        'agent_name': agent_name,
                        'reason': _reason,
                        'full_context': (rec.content or '')[:400],
                        'log_id': rec.id,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=2,
                    batch_group='office',
                ))
                rec.status = 'anchored'
            if recs:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_task_blocked scan error: {e}')
        return anchors

    def _scan_agent_followup(self, user, session, now_utc) -> list:
        """Follow-up: проверяет dispatch-задачи, выполненные 2-6 часов назад,
        и создаёт якорь чтобы проверить реальный результат (задача создана? цель обновлена?).

        Это закрывает цикл: dispatch → agent работает → follow-up → корректировка.
        """
        anchors = []
        try:
            from models import AgentActivityLog

            # Ищем завершённые dispatch-задачи за 2-6 часов назад без follow-up
            window_start = now_utc - timedelta(hours=6)
            window_end = now_utc - timedelta(hours=2)
            completed_dispatches = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'agent_event_dispatch',
                AgentActivityLog.status == 'completed',
                AgentActivityLog.created_at >= window_start,
                AgentActivityLog.created_at <= window_end,
            ).limit(5).all()

            for disp in completed_dispatches:
                # Уже есть follow-up для этого dispatch?
                followup_exists = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user.id,
                    AgentActivityLog.activity_type == 'agent_followup',
                    AgentActivityLog.target == f'followup:{disp.id}',
                ).first()
                if followup_exists:
                    continue

                result_preview = (disp.result or '')[:200]
                agent_name = (disp.title or '').replace('[dispatch] ', '').split(' ←')[0]
                task_preview = (disp.content or '')[:150]

                source_key = f'followup:{disp.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='task_stale',
                    source=source_key,
                    topic=_t(user,
                        f'Проверка результата: {agent_name}',
                        f'Follow-up: {agent_name}'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'title': f'Проверь результат работы {agent_name}: {task_preview}. '
                                 f'Результат: {result_preview}. '
                                 f'Проверь: задача создана/обновлена? Цель продвинулась? Если нет — доделай.',
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=8),
                    cooldown_hours=6,
                    batch_group='office',
                ))

        except Exception as e:
            logger.debug(f'[ANCHOR] agent_followup scan error: {e}')
        return anchors

    # CLEANUP
    # ═══════════════════════════════════════════════════════

    async def cleanup_old_anchors(self):
        """Удаляет старые доставленные/истёкшие якоря (> 30 дней)"""
        session = Session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            deleted = session.query(Anchor).filter(
                Anchor.created_at < cutoff
            ).delete()
            
            deleted_logs = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.created_at < cutoff
            ).delete()
            
            session.commit()
            if deleted or deleted_logs:
                logger.info(f"[ANCHOR] Cleanup: removed {deleted} anchors, {deleted_logs} logs")
        except Exception as e:
            logger.error(f"[ANCHOR] Cleanup error: {e}")
            session.rollback()
        finally:
            session.close()


    def _scan_agent_script_failures(self, user, session, now_utc) -> list:
        """Создаёт HIGH-якорь если пользовательский агент дважды и более не смог
        получить данные за последние 24 часа (expired key, IMAP error, etc.).

        Данные берутся из AgentActivityLog, который закрашивает failures
        прямо при выполнении python_code в autonomous_agent.py.
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=24)
            # Группируем ошибки по target (название агента/сервиса)
            fails = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'integration',
                AgentActivityLog.status == 'failed',
                AgentActivityLog.created_at >= since,
            ).all()
            if not fails:
                return anchors

            # Группируем по сервису/агенту
            by_target: dict = {}
            for rec in fails:
                key = rec.target or 'Агент'
                by_target.setdefault(key, [])
                by_target[key].append(rec)

            # Генерируем якорь только для сервисов с 2+ ошибками
            for target, recs in by_target.items():
                if len(recs) < 2:
                    continue
                latest = max(recs, key=lambda r: r.created_at or now_utc)
                err_snippet = (latest.content or '')[:120]
                source_key = f'agent_fail:{target}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_script_failed',
                    source=source_key,
                    topic=_t(user,
                        f'Агент «{target}» не может подключиться ({len(recs)}× за сутки)',
                        f'Agent «{target}» connection failing ({len(recs)}× in 24h)'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({'agent': target, 'failures': len(recs), 'last_error': err_snippet},
                                    ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=6,
                    batch_group='system',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_script_failures scan error: {e}')
        return anchors

    def _scan_agent_silent(self, user, session, now_utc) -> list:
        """Создаёт MEDIUM-якорь если агент со скриптом запускался 5+ раз за 24ч,
        но ни разу не отдал данных (stdout пустой). Значит интеграция тихо сломана.
        """
        anchors = []
        try:
            from models import UserAgent, AgentActivityLog
            agents = session.query(UserAgent).filter(
                UserAgent.author_id == user.id,
                UserAgent.status.in_(['active', 'paused']),
                UserAgent.python_code.isnot(None),
            ).all()
            since = now_utc - timedelta(hours=24)
            for agent in agents:
                # Считаем запуски L1 (integration activity) за сутки
                runs = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user.id,
                    AgentActivityLog.ref_id == agent.id,
                    AgentActivityLog.activity_type == 'integration',
                    AgentActivityLog.created_at >= since,
                ).all()
                if len(runs) < 5:
                    continue
                # Все со статусом completed но без содержательного result?
                non_empty = [r for r in runs if r.result and len(r.result.strip()) > 20]
                if non_empty:
                    continue  # есть хотя бы один результат — всё ок
                source_key = f'agent_silent:{agent.id}:{now_utc.strftime("%Y-%m-%d")}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_script_failed',
                    source=source_key,
                    topic=_t(user,
                        f'Агент «{agent.name}» работает, но уже сутки не получает данные ({len(runs)} запусков)',
                        f'Agent «{agent.name}» running but no data for 24h ({len(runs)} runs)'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'agent': agent.name, 'agent_id': agent.id,
                        'runs_24h': len(runs), 'non_empty': 0,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=18),
                    cooldown_hours=20,
                    batch_group='system',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_silent scan error: {e}')
        return anchors

    def _scan_campaign_stagnation(self, user, session, now_utc) -> list:
        """Создаёт якорь если активная кампания (email/контент/делегирование) не имеет
        активности 3+ дня — пользователь может не знать что кампания «зависла».
        """
        anchors = []
        try:
            # Email campaigns — активна, но 0 отправок за 3 дня
            stale_cutoff = now_utc - timedelta(days=3)
            campaigns = session.query(EmailCampaign).filter(
                EmailCampaign.user_id == user.id,
                EmailCampaign.status == 'active',
            ).all()
            for c in campaigns:
                recent_sends = session.query(EmailOutreach).filter(
                    EmailOutreach.campaign_id == c.id,
                    EmailOutreach.status.in_(['sent', 'replied']),
                    EmailOutreach.sent_at >= stale_cutoff,
                ).count()
                if recent_sends > 0:
                    continue
                source_key = f'camp_stale:{c.id}:{now_utc.strftime("%Y-%m-%d")}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_campaign_report',
                    source=source_key,
                    topic=_t(user,
                        f' Email-кампания «{c.name}» без активности {3}+ дня — проверь контакты и настройки',
                        f' Email campaign «{c.name}» stale for {3}+ days — check leads and settings'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': c.id, 'campaign_name': c.name,
                        'total_sent': c.emails_sent or 0,
                        'total_replied': c.emails_replied or 0,
                        'stale_days': 3,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=24,
                    batch_group='email',
                ))

            # Content campaigns — активна, но last_published_at > 3 дня назад
            content_campaigns = session.query(ContentCampaign).filter(
                ContentCampaign.user_id == user.id,
                ContentCampaign.status == 'active',
            ).all()
            for cc in content_campaigns:
                last_pub = cc.last_post_at
                if last_pub and last_pub.tzinfo is None:
                    last_pub = last_pub.replace(tzinfo=timezone.utc)
                if last_pub and last_pub >= stale_cutoff:
                    continue  # публиковалось недавно
                if not last_pub and cc.created_at:
                    cr = cc.created_at
                    if cr.tzinfo is None:
                        cr = cr.replace(tzinfo=timezone.utc)
                    if cr >= stale_cutoff:
                        continue  # создана недавно, ещё не время
                source_key = f'content_stale:{cc.id}:{now_utc.strftime("%Y-%m-%d")}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='content_campaign_publish',
                    source=source_key,
                    topic=_t(user,
                        f' Контент-кампания «{cc.name}» не публикуется 3+ дня',
                        f' Content campaign «{cc.name}» no posts for 3+ days'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'campaign_id': cc.id, 'campaign_name': cc.name,
                        'stale_days': 3,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=24,
                    batch_group='content',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] campaign_stagnation scan error: {e}')
        return anchors

    async def _scan_weather_extreme(self, user, profile, now_utc) -> list:
        """Создаёт якорь при экстремальных погодных условиях в городе пользователя.

        Крайние пороги: температура < -20°C или > 35°C, гроза, метель, ливень.
        Кэш 3 часа — не тратим API-запрос каждые 5 минут.
        """
        anchors = []
        try:
            city = (profile.city if profile else None) or getattr(user, 'city', None)
            if not city:
                return anchors
            from config import OPENWEATHERMAP_API_KEY
            if not OPENWEATHERMAP_API_KEY:
                return anchors

            # Простой кэш в памяти (ключ: city, TTL 3ч)
            _cache = getattr(self, '_weather_cache', {})
            if not hasattr(self, '_weather_cache'):
                self._weather_cache = {}
                _cache = self._weather_cache
            cache_key = city.lower().strip()
            cached = _cache.get(cache_key)
            if cached and (now_utc - cached['ts']).total_seconds() < 10800:
                w = cached['data']
            else:
                import aiohttp
                url = (f'https://api.openweathermap.org/data/2.5/weather'
                       f'?q={city}&appid={OPENWEATHERMAP_API_KEY}&units=metric&lang=ru')
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status != 200:
                                return anchors
                            w = await resp.json()
                    _cache[cache_key] = {'ts': now_utc, 'data': w}
                except Exception:
                    return anchors

            temp = w.get('main', {}).get('temp', 0)
            weather_id = w.get('weather', [{}])[0].get('id', 0)
            description = w.get('weather', [{}])[0].get('description', '')

            # Пороги экстремальности
            extreme_cold = temp < -20
            extreme_heat = temp > 35
            # WMO codes: 2xx=гроза, 6xx=снег/метель, 502/503/504=тяжёлый дождь
            storm = 200 <= weather_id < 300
            heavy_snow = weather_id in (602, 621, 622)
            heavy_rain = weather_id in (502, 503, 504, 522)

            is_extreme = extreme_cold or extreme_heat or storm or heavy_snow or heavy_rain
            if not is_extreme:
                return anchors

            if extreme_cold:
                topic_ru = f'Сильный мороз в {city}: {temp:.0f}°C — учти при планировании'
                topic_en = f'Extreme cold in {city}: {temp:.0f}°C — adjust your schedule'
            elif extreme_heat:
                topic_ru = f'Сильная жара в {city}: {temp:.0f}°C'
                topic_en = f'Extreme heat in {city}: {temp:.0f}°C'
            elif storm:
                topic_ru = f'Гроза в {city}: {description}'
                topic_en = f'Thunderstorm in {city}: {description}'
            elif heavy_snow:
                topic_ru = f'Метель/сильный снег в {city}: {description}'
                topic_en = f'Heavy snow/blizzard in {city}: {description}'
            else:
                topic_ru = f'Сильный дождь в {city}: {description}'
                topic_en = f'Heavy rain in {city}: {description}'

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='weather_extreme',
                source=f'weather:{cache_key}:{weather_id}',
                topic=_t(user, topic_ru, topic_en),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'city': city, 'temp': temp, 'description': description,
                                 'weather_id': weather_id}, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=6,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] weather_extreme scan error: {e}')
        return anchors


# ═══════════════════════════════════════════════════════
# GLOBAL INSTANCE & HELPERS
# ═══════════════════════════════════════════════════════

_anchor_engine = None


def init_anchor_engine(bot=None) -> AnchorEngine:
    """Инициализирует глобальный экземпляр AnchorEngine"""
    global _anchor_engine
    _anchor_engine = AnchorEngine(bot=bot)
    return _anchor_engine


def get_anchor_engine() -> AnchorEngine | None:
    return _anchor_engine


async def start_anchor_engine(bot=None):
    """Запускает AnchorEngine в фоне. Вызывать из main.py."""
    engine = init_anchor_engine(bot)
    await engine.start()
