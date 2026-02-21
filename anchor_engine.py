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
    ActivityAlert, ContactAlert,
)
from config import DEEPSEEK_API_KEY, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════

# ── Лимиты доставок (единые, контроль расхода через токены) ──
# Токены — основной ограничитель. Лимиты — только anti-spam предохранитель.
MAX_DIALOG_PER_DAY = 8
MAX_FEED_PER_DAY = 2
MAX_CHANNEL_PER_DAY = 1
# CRITICAL/HIGH якоря НЕ считаются в лимите — доставляются всегда

NIGHT_START_HOUR = PROACTIVE_NO_SEND_START_HOUR  # Общая настройка: 22
MORNING_START_HOUR = PROACTIVE_SEND_START_HOUR   # Общая настройка: 10
SCAN_INTERVAL_MINUTES = 5

# Минимальный интервал между ПРОАКТИВНЫМИ сообщениями (не блокирует CRITICAL)
MIN_PROACTIVE_GAP_MINUTES = 10

# Cooldown по приоритету (часы)
PRIORITY_COOLDOWN = {
    AnchorPriority.CRITICAL: 0.5,   # 30 мин
    AnchorPriority.HIGH: 1.5,
    AnchorPriority.MEDIUM: 3,
    AnchorPriority.LOW: 8,
}

# Якоря, которые ВСЕГДА доставляются (кроме DND/ночь)
ALWAYS_DELIVER_TYPES = {
    'task_reminder',          # Точное напоминание по reminder_time
    'task_overdue',           # Просроченная задача — критично
    'task_deadline_soon',     # Дедлайн скоро — критично
    'delegation_update',      # Результат делегирования — пользователь ждёт
    'goal_deadline',          # Горящий дедлайн цели
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
    'contact_online': 'contacts',
    'delegation_pending': 'delegation',
    'delegation_update': 'delegation',
    'market_insight': 'insights',
    'content_opportunity': 'insights',
    'profile_gap': 'engagement',
    'dialog_followup': 'engagement',
    'weather_activity': 'misc',
    'morning_plan': 'daily',
    'evening_review': 'daily',
    'task_result_check': 'tasks',
    'recurring_task_due': 'tasks',
    'post_opportunity': 'posting',
    'channel_post': 'posting',
}


class AnchorEngine:
    """
    Единый движок автономии. Сканирует → Оценивает → Доставляет.
    """

    def __init__(self, bot=None):
        self.bot = bot
        self.running = False
        self._scan_locks = defaultdict(asyncio.Lock)
        # Семафор для AI-вызовов — ограничивает параллельные запросы к DeepSeek
        self._ai_semaphore = asyncio.Semaphore(5)
        logger.info("[ANCHOR] AnchorEngine initialized")

    # ═══════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════

    async def start(self):
        """Запуск бесконечного цикла сканирования"""
        self.running = True
        logger.info(f"[ANCHOR] 🚀 Starting scan loop (every {SCAN_INTERVAL_MINUTES}min)")
        while self.running:
            try:
                import time as _time
                cycle_start = _time.monotonic()
                logger.info(f"[ANCHOR] 🔄 Starting scan cycle")
                await self._scan_all_users()
                cycle_duration = _time.monotonic() - cycle_start
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
                        skipped_night += 1
                        continue
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
            lock_id = abs(user_id) % 2147483647  # PostgreSQL advisory lock ID (int4)
            lock_result = session.execute(
                text(f"SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id}
            ).scalar()
            if not lock_result:
                logger.debug(f"[ANCHOR] User {user_id}: ⛔ advisory lock busy (another process), skip")
                return

            try:
                await self._process_user_inner(user_id, session)
            finally:
                # Освобождаем advisory lock
                session.execute(text(f"SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                session.commit()

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
        from token_service import has_enough_tokens, get_balance
        from config import FREE_ACCESS_MODE
        if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'proactive_message'):
            balance = get_balance(user_id)
            logger.info(f"[ANCHOR] User {user_id}: ⛔ недостаточно токенов (баланс: {balance}, нужно: 15), пропуск")
            return

        # Проверка DND
        if user.do_not_disturb_until:
            dnd = user.do_not_disturb_until
            if dnd.tzinfo is None:
                dnd = dnd.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < dnd:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ DND до {dnd}, пропуск")
                return

        # Проверка ночных часов
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        if user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ ночные часы ({user_now.strftime('%H:%M')} {user.timezone or 'Europe/Moscow'}, окно {MORNING_START_HOUR}:00-{NIGHT_START_HOUR}:00), пропуск")
            return

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
        for log in today_logs:
            try:
                types = json.loads(log.anchor_types) if log.anchor_types else []
            except (json.JSONDecodeError, TypeError):
                types = []
            if 'channel_post' in types:
                channel_count += 1
            elif 'post_opportunity' in types:
                post_count += 1
            else:
                dialog_count += 1

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

        # 2. EVALUATE — собрать доставляемые якоря
        deliverable = session.query(Anchor).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.is_(None),
            Anchor.triggered_at.isnot(None),
        ).order_by(
            Anchor.priority.asc(),  # CRITICAL first (enum order)
            Anchor.created_at.asc()
        ).limit(15).all()

        logger.info(f"[ANCHOR] User {user_id}: найдено {len(deliverable)} deliverable якорей")

        # Фильтруем: не истёкшие + cooldown
        ready = [a for a in deliverable if a.is_deliverable()]
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после is_deliverable() — 0 ready (expired/suppressed)")
            return
        ready = self._apply_cooldowns(ready, user, session)
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после _apply_cooldowns — 0 ready")
            return

        # ── Разделяем потоки ──
        critical_anchors = [a for a in ready if a.anchor_type in ALWAYS_DELIVER_TYPES
                            or a.priority in (AnchorPriority.CRITICAL, AnchorPriority.HIGH)]
        post_anchors = [a for a in ready if a.anchor_type in ('post_opportunity', 'channel_post')]
        regular_anchors = [a for a in ready if a not in critical_anchors and a not in post_anchors]

        logger.info(f"[ANCHOR] User {user_id}: ready={len(ready)} (critical={len(critical_anchors)}, regular={len(regular_anchors)}, posts={len(post_anchors)}) dialog_count={dialog_count} gap_ok={proactive_gap_ok}")

        # ── 3. ЕДИНАЯ ДОСТАВКА — critical + regular в ОДНОМ сообщении ──
        all_dialog_anchors = critical_anchors.copy()
        if regular_anchors and dialog_count < MAX_DIALOG_PER_DAY and proactive_gap_ok:
            all_dialog_anchors.extend(regular_anchors)
        elif regular_anchors:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (dialog_count={dialog_count}/{MAX_DIALOG_PER_DAY}, gap_ok={proactive_gap_ok})")

        if all_dialog_anchors:
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

        # ── 3c. FEED POSTS — отдельный лимит ──
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

        # --- ПОСТЫ В ЛЕНТУ (все) ---
        anchors.extend(self._scan_post_opportunities(user, profile, session, now_utc))

        # --- ПОСТЫ В КАНАЛ (если указан канал) ---
        if user.telegram_channel:
            anchors.extend(self._scan_channel_post(user, profile, session, now_utc))

        # Дедупликация: не создаём якорь если уже есть недоставленный с тем же type+source
        existing = session.query(Anchor).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.is_(None)
        ).all()
        existing_keys = {(a.anchor_type, a.source) for a in existing}

        unique_anchors = []
        for a in anchors:
            key = (a.anchor_type, a.source)
            if key not in existing_keys:
                existing_keys.add(key)
                unique_anchors.append(a)

        return unique_anchors

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
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_reminder',
                        source=f'task:{task.id}',
                        topic=f'Напоминание: задача «{task.title}» на сейчас',
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'description': (task.description or '')[:200],
                                        'reminder_type': 'exact'}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(minutes=30),
                        cooldown_hours=0.5,
                        batch_group='tasks',
                    ))
                    # Помечаем как отправленное чтобы не дублировать
                    task.reminder_sent = True
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()

                # ПРОСРОЧЕННЫЕ (более 30 мин назад)
                elif minutes_diff < -30:
                    hours_overdue = abs(minutes_diff) / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_overdue',
                        source=f'task:{task.id}',
                        topic=f'Задача «{task.title}» просрочена на {int(hours_overdue)}ч',
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_overdue': round(hours_overdue, 1),
                                        'description': (task.description or '')[:200]}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=2,
                        batch_group='tasks',
                    ))

                # ДЕДЛАЙН СКОРО (до 24ч до reminder_time)
                elif 0 < minutes_diff <= 24 * 60:
                    hours_left = minutes_diff / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_deadline_soon',
                        source=f'task:{task.id}',
                        topic=f'Задача «{task.title}» — дедлайн через {int(hours_left)}ч',
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_left': round(hours_left, 1)}),
                        triggered_at=now_utc,
                        expires_at=rt,
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
                        topic=f'Задача «{task.title}» висит уже {age_days} дней',
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
                            topic=f'Время проверить результат задачи «{task.title}»',
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

        for rtask in recurring_tasks:
            if rtask.reminder_time and rtask.recurrence_pattern:
                rt = rtask.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                # Проверяем: последний экземпляр уже в прошлом?
                last_instance = session.query(Task).filter(
                    Task.parent_task_id == rtask.id
                ).order_by(Task.reminder_time.desc()).first()
                
                last_time = last_instance.reminder_time if last_instance else rt
                if last_time and last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                
                if last_time and last_time < now_utc:
                    # Создаём новый экземпляр повторяющейся задачи
                    next_time = self._calculate_next_recurrence(last_time, rtask.recurrence_pattern, rtask.recurrence_interval or 1)
                    # Проверяем что такой экземпляр ещё не создан
                    existing = session.query(Task).filter(
                        Task.parent_task_id == rtask.id,
                        Task.reminder_time == next_time
                    ).first()
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
                        topic=f'Повторяющаяся задача «{rtask.title}» — создан новый экземпляр',
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
                topic=f'За последние 24ч завершено {recent_completed} задач',
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
                    topic=f'Цель «{goal.title}» на {goal.progress_percentage}% — почти!',
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
                        topic=f'Цель «{goal.title}» — {age_days} дней без прогресса',
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
                        topic=f'Цель «{goal.title}» — дедлайн через {days_left} дн, прогресс {goal.progress_percentage}%',
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
                topic='Профиль не заполнен — агент не может эффективно помогать',
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'missing': ['skills', 'interests', 'goals', 'city', 'position']}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=48,
                batch_group='engagement',
            ))
            return anchors

        missing = []
        if not profile.skills or not profile.skills.strip():
            missing.append('навыки')
        if not profile.interests or not profile.interests.strip():
            missing.append('интересы')
        if not profile.goals or not profile.goals.strip():
            missing.append('цели')

        if len(missing) >= 2:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='profile_gap',
                source=f'profile:missing:{",".join(missing)}',
                topic=f'В профиле не хватает: {", ".join(missing)}',
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
                        topic=f'Делегированная задача «{task.title}» → @{task.delegated_to_username} — ждёт ответа {int(hours_waiting)}ч',
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'delegated_to': task.delegated_to_username,
                                        'hours_waiting': round(hours_waiting, 1)}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=6,
                        batch_group='delegation',
                    ))

        # Задачи с обновлённым статусом делегирования (accepted/completed)
        updated_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(['accepted', 'completed']),
            Task.status.in_(['pending', 'in_progress'])
        ).all()

        for task in updated_delegated:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='delegation_update',
                source=f'task:{task.id}:status:{task.delegation_status}',
                topic=f'Задача «{task.title}» — @{task.delegated_to_username} {task.delegation_status}',
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'task_id': task.id, 'title': task.title,
                                'delegated_to': task.delegated_to_username,
                                'status': task.delegation_status}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=12,
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

        for alert in contact_alerts[:3]:
            for prof in recent_profiles:
                match = False
                if alert.skill and prof.skills and alert.skill.lower() in prof.skills.lower():
                    match = True
                if alert.interest and prof.interests and alert.interest.lower() in prof.interests.lower():
                    match = True
                if match and alert.city and prof.city and alert.city.lower() not in prof.city.lower():
                    match = False

                if match:
                    prof_user = session.query(User).filter_by(id=prof.user_id).first()
                    if prof_user and prof_user.username:
                        detail = alert.skill or alert.interest
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='contact_match',
                            source=f'contact:@{prof_user.username}',
                            topic=f'Новый специалист @{prof_user.username} ({detail})',
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
                    topic=f'Последнее сообщение {int(hours_since)}ч назад: «{content_preview[:60]}...»',
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
                topic='Утро — время для обзора дня',
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
                topic='Вечер — время для подведения итогов',
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
                topic=f'Время проверить события в нише: {niche[:60]}',
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
                topic='Время для контент-идеи',
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

    # ═══════════════════════════════════════════════════════
    # POST SCANNERS — ленточный автопостинг + канал
    # ═══════════════════════════════════════════════════════

    def _scan_post_opportunities(self, user, profile, session, now_utc) -> list:
        """Сканирует ВСЕ данные пользователя и создаёт якорь post_opportunity.

        AI потом сам решит, стоит ли делать пост и О ЧЁМ.
        Мы здесь только проверяем: есть ли вообще о чём писать.
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

        # Нет сигналов — нет якоря
        if not signals:
            return anchors

        # Создаём один общий якорь — AI решит что с этим делать
        source_key = f'post:{user_now.strftime("%Y-%m-%d")}:{posts_today}'
        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='post_opportunity',
            source=source_key,
            topic=f'Есть материал для {len(signals)} потенциальных постов в ленту',
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'signals': signals,
                'posts_today': posts_today,
                'user_name': user.first_name or user.username or 'user',
                'tier': 'tokens',  # Токенная модель
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=8,
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

        # Проверяем предпочтительное время для постинга
        post_time_str = getattr(profile, 'auto_post_time', '12:00') if profile else '12:00'
        try:
            post_h, post_m = map(int, (post_time_str or '12:00').split(':'))
        except (ValueError, AttributeError):
            post_h, post_m = 12, 0

        current_minutes = user_now.hour * 60 + user_now.minute
        target_minutes = post_h * 60 + post_m

        # Окно: ±30 мин от предпочтительного времени
        if abs(current_minutes - target_minutes) > 30:
            return anchors

        # Собираем контекст для AI
        content_strategy = getattr(profile, 'content_strategy', '') or '' if profile else ''
        interests = getattr(profile, 'interests', '') or '' if profile else ''
        goals = getattr(profile, 'goals', '') or '' if profile else ''
        skills = getattr(profile, 'skills', '') or '' if profile else ''

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='channel_post',
            source=f'channel:{user_now.strftime("%Y-%m-%d")}',
            topic=f'Время для поста в канал {channel}',
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'channel': channel,
                'content_strategy': content_strategy[:300],
                'interests': interests[:200],
                'goals': goals[:200],
                'skills': skills[:200],
                'user_name': user.first_name or user.username or 'user',
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=4),
            cooldown_hours=20,
            batch_group='posting',
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
        """Фильтрует якоря по cooldown — один батч-запрос вместо N отдельных"""
        now_utc = datetime.now(timezone.utc)
        result = []

        # Один запрос: все недавние доставки этого пользователя по типам
        # Берём max cooldown из списка якорей чтобы покрыть все 
        max_cooldown = max((a.cooldown_hours or PRIORITY_COOLDOWN.get(a.priority, 4)) for a in anchors) if anchors else 8
        recent_deliveries = session.query(
            Anchor.anchor_type,
            Anchor.delivered_at
        ).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.isnot(None),
            Anchor.delivered_at >= now_utc - timedelta(hours=max_cooldown)
        ).all()

        # Индексируем: тип → последняя доставка
        last_delivery_by_type = {}
        for atype, delivered_at in recent_deliveries:
            if atype not in last_delivery_by_type or delivered_at > last_delivery_by_type[atype]:
                last_delivery_by_type[atype] = delivered_at

        for anchor in anchors:
            cooldown_h = anchor.cooldown_hours or PRIORITY_COOLDOWN.get(anchor.priority, 4)
            last_delivered = last_delivery_by_type.get(anchor.anchor_type)
            if last_delivered:
                if last_delivered.tzinfo is None:
                    last_delivered = last_delivered.replace(tzinfo=timezone.utc)
                if last_delivered >= now_utc - timedelta(hours=cooldown_h):
                    logger.debug(f"[ANCHOR] Cooldown: {anchor.anchor_type} (last delivered {last_delivered})")
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
                }
                OPTIONAL_LOW = {'market_insight', 'content_opportunity', 'weather_activity'}
                # Необязательные LOW — удваиваем cooldown (через доп. фильтр)
                filtered = []
                for a in result:
                    if a.priority == AnchorPriority.LOW and a.anchor_type in OPTIONAL_LOW:
                        # Проверяем двойной cooldown
                        double_cd = (a.cooldown_hours or 8) * 2
                        recent_opt = session.query(Anchor).filter(
                            Anchor.user_id == user.id,
                            Anchor.anchor_type == a.anchor_type,
                            Anchor.delivered_at.isnot(None),
                            Anchor.delivered_at >= now_utc - timedelta(hours=double_cd)
                        ).first()
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

            # Проверяем и списываем токены
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            action = 'proactive_channel' if anchor.anchor_type == 'channel_post' else 'proactive_post'
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, action):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск поста — нет токенов")
                    return
                spend_tokens(user.telegram_id, action, description=f'anchor_{anchor.anchor_type}')

            anchor_data = json.loads(anchor.data) if anchor.data else {}

            if anchor.anchor_type == 'post_opportunity':
                post_text = await self._ai_compose_post(user, anchor_data, session, mode='feed')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for feed post")
                    return

                # Создаём Post в БД
                post = Post(
                    user_id=user.id,
                    username=user.username or user.first_name or f'user_{user.telegram_id}',
                    content=post_text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(post)

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)

                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[FEED POST] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # Уведомляем пользователя
                if self.bot:
                    notify = (
                        f"Опубликовал пост в твою ленту:\n\n"
                        f"{post_text}\n\n"
                        f"Если не нравится — скажи, удалю."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                logger.info(f"[ANCHOR] ✅ Feed post for {user.telegram_id}: {post_text[:80]}...")

            elif anchor.anchor_type == 'channel_post':
                channel = anchor_data.get('channel', '')
                if not channel:
                    return

                post_text = await self._ai_compose_post(user, anchor_data, session, mode='channel')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for channel post")
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
                status_icon = "✅" if published else "❌"
                logger.info(f"[ANCHOR] {status_icon} Channel post for {user.telegram_id} -> {channel}: {post_text[:80]}...")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_post_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _ai_compose_post(self, user, anchor_data: dict, session, mode: str = 'feed') -> str | None:
        """Просит AI создать пост на основе данных пользователя.

        AI получает ВСЕ сигналы и сам решает:
        - Стоит ли публиковать вообще (SKIP)
        - О чём написать
        - В каком стиле

        Args:
            mode: 'feed' | 'channel'
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

            if mode == 'feed':
                system_msg = (
                    "Ты — автономный агент ASI Biont. Твоя задача — решить, стоит ли сделать пост в ленту "
                    "от лица пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. Если материала недостаточно или пост будет неинтересным — верни SKIP\n"
                    "2. Пиши от ПЕРВОГО лица, как будто сам пользователь делится с миром\n"
                    "3. Пост может быть О ЧЁМ УГОДНО: достижения, мысли, поиск людей, экспертное мнение, "
                    "итоги дня, просьба о помощи, инсайты, открытия, планы — выбери самое полезное\n"
                    "4. Естественный, живой стиль. 3-6 предложений. БЕЗ эмодзи, без хештегов, без призывов к действию\n"
                    "5. НЕ ВЫДУМЫВАЙ факты. Основывайся ТОЛЬКО на реальных сигналах ниже\n"
                    "6. Верни ТОЛЬКО текст поста или SKIP. Ничего больше."
                )
            else:  # channel
                content_strategy = anchor_data.get('content_strategy', '')
                system_msg = (
                    "Ты — контент-менеджер для Telegram-канала пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. Если нет хорошего материала для канала — верни SKIP\n"
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
            elif mode == 'channel':
                # Для канала сигналов нет, AI пишет на основе профиля/стратегии
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
                completed_lines.append(f"✓ {ct.title}{ct_time}")

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
                skipped_lines.append(f"✗ {st.title}{reason}")

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
                "ПРИНЦИПЫ:",
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
            for i, ad in enumerate(anchor_descriptions, 1):
                prompt_parts.append(
                    f"{i}. [{ad['priority']}] {ad['type']}: {ad['topic']} "
                    f"(источник: {ad['source']}, возраст: {ad['age_minutes']}мин)"
                )

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

            # Проверяем: не было ли доставки этому юзеру за последние MIN_PROACTIVE_GAP_MINUTES?
            recent_delivery = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(minutes=MIN_PROACTIVE_GAP_MINUTES)
            ).first()
            if recent_delivery:
                logger.info(f"[ANCHOR] User {user.telegram_id}: delivery gap too small ({MIN_PROACTIVE_GAP_MINUTES}min), skip")
                return

            # Проверяем и списываем токены за проактивное сообщение
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, 'proactive_message'):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск доставки — нет токенов")
                    return
                spend_tokens(user.telegram_id, 'proactive_message', description='proactive anchor')

            # Помечаем якоря как доставленные
            anchor_ids = []
            anchor_types = []
            for anchor in anchors:
                anchor.delivered_at = now_utc
                anchor_ids.append(anchor.id)
                anchor_types.append(anchor.anchor_type)

            # Создаём запись в interactions (для совместимости с anti-spam логикой)
            interaction = Interaction(
                user_id=user.id,
                message_type='proactive',
                content=message
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
                    # Гарантируем кликабельность URL
                    send_text = re.sub(r'(?<=[^\s\n])(https?://)', r' \1', message)
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=send_text
                    )
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
                    for aid in ids:
                        anchor = session.query(Anchor).filter_by(id=aid).first()
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

            for log in unresolved:
                log.user_responded = False
                try:
                    ids = json.loads(log.anchor_ids)
                    for aid in ids:
                        anchor = session.query(Anchor).filter_by(id=aid).first()
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
