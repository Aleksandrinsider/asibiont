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

Антиспам:
- CRITICAL: доставка в течение 30 мин
- HIGH: батч каждые 2ч
- MEDIUM: батч каждые 4ч
- LOW: макс 1/день
- DND, ночные часы, cooldown по типу, адаптация по игнорам
- Макс 4 доставки/день (не считая ответов на запросы)
"""

import asyncio
import json
import logging
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytz

from models import (
    Session, User, UserProfile, Task, Goal, Interaction,
    Anchor, AnchorDeliveryLog, AnchorPriority,
    ActivityAlert, ContactAlert, SubscriptionTier,
)
from config import DEEPSEEK_API_KEY, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════

MAX_DELIVERIES_PER_DAY = 4
NIGHT_START_HOUR = PROACTIVE_NO_SEND_START_HOUR  # Общая настройка: 22
MORNING_START_HOUR = PROACTIVE_SEND_START_HOUR   # Общая настройка: 10
SCAN_INTERVAL_MINUTES = 20 # Интервал между сканированиями
MIN_INTERACTION_GAP_MINUTES = 15  # Не писать если пользователь общался < 15 мин назад

# Cooldown по приоритету (часы)
PRIORITY_COOLDOWN = {
    AnchorPriority.CRITICAL: 0.5,   # 30 мин
    AnchorPriority.HIGH: 2,
    AnchorPriority.MEDIUM: 4,
    AnchorPriority.LOW: 12,
}

# Группы батчинга
BATCH_GROUPS = {
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
}


class AnchorEngine:
    """
    Единый движок автономии. Сканирует → Оценивает → Доставляет.
    """

    def __init__(self, bot=None):
        self.bot = bot
        self.running = False
        self._scan_locks = defaultdict(asyncio.Lock)
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
                await self._scan_all_users()
                await asyncio.sleep(SCAN_INTERVAL_MINUTES * 60)
            except Exception as e:
                logger.error(f"[ANCHOR] Loop error: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(300)

    async def stop(self):
        self.running = False
        logger.info("[ANCHOR] Stopped")

    async def _scan_all_users(self):
        """Сканирует всех пользователей с активной подпиской"""
        session = Session()
        try:
            users = session.query(User).filter(
                User.telegram_id.isnot(None)
            ).all()
            
            for user in users:
                try:
                    # Не сканировать параллельно одного user
                    lock = self._scan_locks[user.telegram_id]
                    if lock.locked():
                        continue
                    async with lock:
                        await self._process_user(user.telegram_id)
                except Exception as e:
                    logger.error(f"[ANCHOR] Error processing user {user.telegram_id}: {e}")
                    continue
        finally:
            session.close()

    async def _process_user(self, user_id: int):
        """Полный цикл для одного пользователя: scan → evaluate → deliver"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return

            # Проверка подписки
            from subscription_service import check_subscription
            if not check_subscription(user_id):
                return

            # Проверка DND
            if user.do_not_disturb_until:
                dnd = user.do_not_disturb_until
                if dnd.tzinfo is None:
                    dnd = dnd.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < dnd:
                    return

            # Проверка ночных часов
            user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
            user_now = datetime.now(user_tz)
            if user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR:
                return

            # Проверка лимита доставок за день
            today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = today_start.astimezone(pytz.UTC)
            deliveries_today = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= today_start_utc
            ).count()

            if deliveries_today >= MAX_DELIVERIES_PER_DAY:
                logger.debug(f"[ANCHOR] User {user_id}: daily limit reached ({deliveries_today})")
                return

            # Проверка недавнего взаимодействия
            last_interaction = session.query(Interaction).filter(
                Interaction.user_id == user.id
            ).order_by(Interaction.created_at.desc()).first()

            if last_interaction:
                li_time = last_interaction.created_at
                if li_time.tzinfo is None:
                    li_time = li_time.replace(tzinfo=timezone.utc)
                gap = datetime.now(timezone.utc) - li_time
                if gap < timedelta(minutes=MIN_INTERACTION_GAP_MINUTES):
                    return

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
            ).limit(10).all()

            # Фильтруем: не истёкшие, не подавленные
            ready = [a for a in deliverable if a.is_deliverable()]

            if not ready:
                return

            # Проверяем cooldown по приоритету
            ready = self._apply_cooldowns(ready, user, session)
            if not ready:
                return

            # 3. AI DECISION — передаём якоря в AI, он решит писать или нет
            message = await self._ai_decide_and_compose(user, ready, session)
            if not message:
                logger.debug(f"[ANCHOR] User {user_id}: AI decided not to write")
                return

            # 4. DELIVER
            await self._deliver(user, ready, message, session)

        except Exception as e:
            logger.error(f"[ANCHOR] _process_user({user_id}) error: {e}\n{traceback.format_exc()}")
            session.rollback()
        finally:
            session.close()

    # ═══════════════════════════════════════════════════════
    # SCAN — обнаружение якорей
    # ═══════════════════════════════════════════════════════

    async def _scan_anchors(self, user, session) -> list:
        """Сканирует ВСЕ источники данных, создаёт якоря.
        
        Не создаёт дубликаты — проверяет наличие необработанного якоря того же типа+source.
        """
        anchors = []

        # Получаем профиль и тариф
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        tier = user.subscription_tier or SubscriptionTier.LIGHT

        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        now_utc = datetime.now(timezone.utc)

        # --- ЗАДАЧИ ---
        anchors.extend(self._scan_tasks(user, session, user_tz, user_now, now_utc))

        # --- ЦЕЛИ ---
        anchors.extend(self._scan_goals(user, session, now_utc))

        # --- ПРОФИЛЬ ---
        anchors.extend(self._scan_profile(user, profile, session))

        # --- ДЕЛЕГИРОВАНИЕ (STANDARD+) ---
        if tier in (SubscriptionTier.STANDARD, SubscriptionTier.PREMIUM):
            anchors.extend(self._scan_delegation(user, session, now_utc))

        # --- КОНТАКТЫ ---
        anchors.extend(self._scan_contacts(user, session, now_utc))

        # --- ДИАЛОГ (follow-up из LTM) ---
        anchors.extend(self._scan_dialog_followup(user, session, now_utc))

        # --- УТРО/ВЕЧЕР ---
        anchors.extend(self._scan_daily_rhythm(user, session, user_now))

        # --- РЫНОК/КОНТЕНТ (PREMIUM) ---
        if tier == SubscriptionTier.PREMIUM:
            anchors.extend(self._scan_premium_insights(user, profile, session, now_utc))

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
            # Просроченные
            if task.reminder_time:
                rt = task.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)

                if rt < now_utc:
                    hours_overdue = (now_utc - rt).total_seconds() / 3600
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

                # Дедлайн в ближайшие 24ч (но ещё не просрочен)
                elif rt < now_utc + timedelta(hours=24):
                    hours_left = (rt - now_utc).total_seconds() / 3600
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
    # COOLDOWN & ANTI-SPAM
    # ═══════════════════════════════════════════════════════

    def _apply_cooldowns(self, anchors: list, user, session) -> list:
        """Фильтрует якоря по cooldown — не доставлять если недавно доставляли такой тип"""
        now_utc = datetime.now(timezone.utc)
        result = []

        for anchor in anchors:
            # Проверяем: был ли недавно доставлен якорь этого типа?
            cooldown_h = anchor.cooldown_hours or PRIORITY_COOLDOWN.get(anchor.priority, 4)
            recent = session.query(Anchor).filter(
                Anchor.user_id == user.id,
                Anchor.anchor_type == anchor.anchor_type,
                Anchor.delivered_at.isnot(None),
                Anchor.delivered_at >= now_utc - timedelta(hours=cooldown_h)
            ).first()

            if recent:
                logger.debug(f"[ANCHOR] Cooldown: {anchor.anchor_type} (last delivered {recent.delivered_at})")
                continue

            result.append(anchor)

        # Адаптация: если пользователь игнорирует > 60% — оставляем только CRITICAL/HIGH
        recent_logs = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= now_utc - timedelta(days=7)
        ).all()

        if len(recent_logs) >= 5:
            ignored = sum(1 for log in recent_logs if not log.user_responded)
            ignore_rate = ignored / len(recent_logs)
            if ignore_rate > 0.6:
                # Пользователь часто игнорирует — только важные
                result = [a for a in result if a.priority in (AnchorPriority.CRITICAL, AnchorPriority.HIGH)]
                logger.info(f"[ANCHOR] User {user.telegram_id}: high ignore rate ({ignore_rate:.0%}), filtered to CRITICAL/HIGH only")

        return result

    # ═══════════════════════════════════════════════════════
    # AI DECISION LAYER
    # ═══════════════════════════════════════════════════════

    async def _ai_decide_and_compose(self, user, anchors: list, session) -> str | None:
        """AI получает все якоря + контекст и РЕШАЕТ: писать или нет + ЧТО писать.
        
        Никаких шаблонов. AI думает на основе полных данных.
        
        Returns:
            str — текст сообщения, или None если AI решил не писать.
        """
        try:
            # Собираем контекст
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            tier = user.subscription_tier or SubscriptionTier.LIGHT

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

            # Цели
            goals = session.query(Goal).filter(
                Goal.user_id == user.id, Goal.status == 'active'
            ).limit(5).all()
            goal_lines = []
            for g in goals:
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
            prompt_parts = [
                "Ты — AnchorEngine, мозг автономного агента ASI Biont.",
                "Ниже — сработавшие ЯКОРЯ (события/факты) + полный контекст пользователя.",
                "Твоя задача: РЕШИТЬ, стоит ли сейчас написать пользователю, и если да — НАПИСАТЬ сообщение.",
                "",
                "ПРАВИЛА:",
                "1. Если якорей мало и они неважные — НЕ ПИШИ. Верни ровно слово: SKIP",
                "2. Если пишешь — это должно быть ПОЛЕЗНО. Не «привет, как дела?» — а конкретный факт/помощь/вопрос.",
                "3. Покажи РАБОТУ: что ты проверил, нашёл, проанализировал. Потом предложи действие.",
                "4. Принцип: СДЕЛАЛ → ПОКАЗАЛ → ПРЕДЛОЖИЛ.",
                "5. Не создавай и не меняй задачи без просьбы пользователя.",
                "6. 3-8 предложений. Деловой тон без излишней вежливости.",
                "7. Если несколько якорей — объедини в ОДНО связное сообщение, а не список",
                "8. На основе данных профиля и истории — персонализируй, не будь роботом",
                "",
                f"=== ВРЕМЯ ===",
                f"{user_now.strftime('%H:%M %d.%m.%Y')} ({user.timezone or 'Europe/Moscow'})",
                f"Тариф: {tier.value}",
                f"{delivery_stats}",
            ]

            prompt_parts.append(f"\n=== ЯКОРЯ ({len(anchors)} шт) ===")
            for i, ad in enumerate(anchor_descriptions, 1):
                prompt_parts.append(
                    f"{i}. [{ad['priority']}] {ad['type']}: {ad['topic']} "
                    f"(источник: {ad['source']}, возраст: {ad['age_minutes']}мин)"
                )

            if task_lines:
                prompt_parts.append(f"\n=== ЗАДАЧИ ({len(tasks)}) ===")
                prompt_parts.extend(task_lines)

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

            result = await agent.generate_system_message(
                user_id=user.telegram_id,
                mode='anchor',
                instruction="Реши: написать или SKIP. Если пишешь — дай конкретное полезное сообщение на основе якорей и контекста.",
                extra_context=full_prompt,
                max_tokens=1500,
                max_iterations=3
            )

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
        """Отправляет сообщение и записывает лог"""
        try:
            now_utc = datetime.now(timezone.utc)

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
            session.commit()

            # Отправляем через бот
            if self.bot:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=message
                )
                logger.info(f"[ANCHOR] ✅ Delivered to {user.telegram_id}: {message[:80]}...")
            else:
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
