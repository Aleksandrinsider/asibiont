"""
Контекст-билдер для AI агента
Отдельный модуль для генерации динамического контекста
"""

import logging
import pytz
import json
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

class ContextBuilder:
    """Класс для построения контекста пользователя"""

    def __init__(self):
        pass

    def build_premium_alerts_context(self, user_id, session):
        """Get proactive alerts for users

        Checks for:
        1. Activity alerts - when other users create matching tasks
        2. Contact alerts - when new users with matching skills/interests join

        Returns list of hint strings to add to context
        """
        from models import User, UserProfile, Task, ActivityAlert, ContactAlert

        hints = []

        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return hints

            # 1. Activity alerts - check recent tasks from other users (ALL tiers)
            activity_alerts = session.query(ActivityAlert).filter_by(
                user_id=user.id,
                enabled=True
            ).all()

            if activity_alerts:
                # Get recent tasks from last 24 hours
                yesterday = datetime.now(timezone.utc) - timedelta(days=1)
                recent_tasks = session.query(Task).filter(
                    Task.user_id != user.id,
                    Task.created_at >= yesterday,
                    Task.status == 'pending'
                ).order_by(Task.created_at.desc()).limit(20).all()

                for alert in activity_alerts[:2]:  # Limit to 2 alerts
                    try:
                        keywords = json.loads(alert.keywords)

                        # Find matching tasks
                        for task in recent_tasks:
                            task_text = (task.title + ' ' + (task.description or '')).lower()
                            if any(kw.lower() in task_text for kw in keywords):
                                # Get task owner
                                task_owner = session.query(User).filter_by(id=task.user_id).first()
                                if task_owner and task_owner.username:
                                    username = task_owner.username
                                    time_str = ""
                                    if task.reminder_time:
                                        try:
                                            user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
                                            task_time = task.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                                            time_str = f" в {task_time.strftime('%H:%M')}"
                                        except Exception as e:
                                            logger.debug(f"Failed to format task time: {e}")

                                    hints.append(f"🔔 @{username} планирует: {task.title}{time_str}")

                                    # Update last triggered
                                    alert.last_triggered_at = datetime.now(timezone.utc)
                                    break  # One match per alert is enough

                    except Exception as e:
                        logger.error(f"[ALERT] Activity alert error: {e}")
                        continue

            # 2. Contact alerts - check new users
            contact_alerts = session.query(ContactAlert).filter_by(
                user_id=user.id,
                enabled=True
            ).all()

            if contact_alerts:
                # Get recently updated profiles
                yesterday = datetime.now(timezone.utc) - timedelta(days=1)
                recent_profiles = session.query(UserProfile).filter(
                    UserProfile.user_id != user.id,
                    UserProfile.updated_at >= yesterday
                ).order_by(UserProfile.updated_at.desc()).limit(20).all()

                for alert in contact_alerts[:2]:  # Limit to 2 alerts
                    try:
                        for profile in recent_profiles:
                            match = False

                            # Check skill match
                            if alert.skill and profile.skills:
                                if alert.skill.lower() in profile.skills.lower():
                                    match = True

                            # Check interest match
                            if alert.interest and profile.interests:
                                if alert.interest.lower() in profile.interests.lower():
                                    match = True

                            # Check city filter
                            if match and alert.city and profile.city:
                                if alert.city.lower() not in profile.city.lower():
                                    match = False

                            if match:
                                profile_user = session.query(User).filter_by(id=profile.user_id).first()
                                if profile_user and profile_user.username:
                                    username = profile_user.username
                                    detail = alert.skill or alert.interest
                                    city_str = f" из {profile.city}" if profile.city else ""
                                    hints.append(f"👤 Новый специалист: @{username} ({detail}){city_str}")

                                    # Update last triggered
                                    alert.last_triggered_at = datetime.now(timezone.utc)
                                    break  # One match per alert is enough

                    except Exception as e:
                        logger.error(f"[ALERT] Contact alert error: {e}")
                        continue

            # Flush updates to last_triggered_at (don't commit - caller owns the session)
            if hints:
                try:
                    session.flush()
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"[PREMIUM_ALERTS] Error: {e}")

        return hints

    def build_proactive_context(self, user_id, session, profile_complete=True):
        """Контекст для мышления: чистые данные о ситуации человека.
        
        Предоставляет факты без предписаний — AI сам рассуждает что делать.
        """
        from models import User, UserProfile, Task

        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return ""

            profile = session.query(UserProfile).filter_by(user_id=user.id).first()

            # User time
            base_now = datetime.now(pytz.UTC)
            user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
            user_now = base_now.astimezone(user_tz)
            hour = user_now.hour

            hints = []

            # ═══ НОВЫЙ ПОЛЬЗОВАТЕЛЬ: контекст для агента ═══
            interaction_cnt = profile.interaction_count if profile and profile.interaction_count else 0
            if interaction_cnt < 5:
                hints.append(f"НОВЫЙ ПОЛЬЗОВАТЕЛЬ (взаимодействий: {interaction_cnt}): активно используй инструменты чтобы показать ценность на деле")

            # ═══ ЗАДАЧИ: что на контроле ═══
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'active', 'in_progress'])
            ).order_by(Task.reminder_time.asc()).limit(10).all()

            overdue = []
            today_tasks = []
            tomorrow_tasks = []
            future_tasks = []

            if tasks:
                for t in tasks:
                    if t.reminder_time:
                        try:
                            dt = t.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                            if dt < user_now:
                                overdue.append(t.title)
                            elif dt.date() == user_now.date():
                                today_tasks.append(f"{t.title} ({dt.strftime('%H:%M')})")
                            elif dt.date() == (user_now.date() + timedelta(days=1)):
                                tomorrow_tasks.append(t.title)
                            else:
                                future_tasks.append(t.title)
                        except Exception:
                            future_tasks.append(t.title)
                    else:
                        future_tasks.append(t.title)

                if overdue:
                    hints.append(f"ПРОСРОЧЕНО ({len(overdue)}): {', '.join(overdue[:3])}")
                if today_tasks:
                    hints.append(f"СЕГОДНЯ ({len(today_tasks)}): {', '.join(today_tasks[:3])}")
                if tomorrow_tasks:
                    hints.append(f"ЗАВТРА ({len(tomorrow_tasks)}): {', '.join(tomorrow_tasks[:2])}")
                if future_tasks and not today_tasks and not overdue:
                    hints.append(f"БУДУЩИЕ ({len(future_tasks)}): {', '.join(future_tasks[:2])}")

                hints.append(f"Всего активных задач: {len(tasks)}")

            # ═══ СТАТИСТИКА ЗАВЕРШЁННЫХ ═══
            completed_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'completed'
            ).order_by(Task.actual_completion_time.desc()).limit(5).all()

            if completed_tasks:
                recent_completed = []
                for ct in completed_tasks[:3]:
                    note = ""
                    if ct.completion_notes:
                        try:
                            from .memory import decrypt_data
                            note = f" — {decrypt_data(ct.completion_notes)[:50]}"
                        except Exception as e:
                            logger.debug(f"Failed to decrypt completion notes: {e}")
                    recent_completed.append(f"{ct.title}{note}")
                if recent_completed:
                    hints.append("НЕДАВНО ЗАВЕРШЕНО:\n" + "\n".join(f"  {c}" for c in recent_completed))

                # Completion rate
                total_all = session.query(Task).filter(Task.user_id == user.id).count()
                completed_count = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'completed'
                ).count()
                if total_all > 3:
                    rate = round(completed_count / total_all * 100)
                    hints.append(f"Выполненность задач: {rate}%")

            # ═══ ПРОПУЩЕННЫЕ + ПРИЧИНЫ ═══
            skipped_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'skipped'
            ).order_by(Task.created_at.desc()).limit(5).all()

            if skipped_tasks:
                skipped_lines = []
                for st in skipped_tasks[:3]:
                    reason = ""
                    if st.skipped_reason:
                        try:
                            from .memory import decrypt_data
                            reason = f" — причина: {decrypt_data(st.skipped_reason)[:60]}"
                        except Exception:
                            pass
                    skipped_lines.append(f"{st.title}{reason}")
                hints.append("НЕДАВНО ПРОПУЩЕНО:\n" + "\n".join(f"  {s}" for s in skipped_lines))

            if not tasks:
                # Определяем время суток для подсказки про свободное время
                if 9 <= hour < 20:
                    hints.append(f"ЗАДАЧ НЕТ, РАСПИСАНИЕ ПУСТОЕ — сейчас {hour}:00, у пользователя свободное время. Предложи конкретные задачи на сегодня исходя из профиля и целей!")
                else:
                    hints.append("ЗАДАЧ НЕТ")

            # ═══ НЕПОЛНЫЙ ПРОФИЛЬ: подсказка ═══
            if profile:
                _missing = []
                if not profile.goals: _missing.append('цели')
                if not profile.skills: _missing.append('навыки')
                if not profile.city: _missing.append('город')
                if not profile.interests: _missing.append('интересы')
                if _missing:
                    hints.append(f"ПРОФИЛЬ НЕПОЛНЫЙ (не заполнено: {', '.join(_missing)}) — спроси у пользователя!")

            # ═══ ЦЕЛИ ═══
            from models import Goal
            active_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active'
            ).order_by(Goal.priority.desc()).limit(3).all()

            if active_goals:
                goal_lines = []
                for g in active_goals:
                    if g.metric_target and g.metric_unit:
                        mc = int(g.metric_current or 0)
                        mt = int(g.metric_target)
                        line = f"{g.title} ({mc}/{mt} {g.metric_unit}, {g.progress_percentage}%)"
                    else:
                        line = f"{g.title} ({g.progress_percentage}%)"
                    if g.target_date:
                        days = g.days_until_target()
                        if days is not None and days < 0:
                            line += " ПРОСРОЧЕНО"
                        elif days is not None and days <= 7:
                            line += f" осталось {days}дн"
                    # Показываем активные задачи привязанные к цели (goal_id)
                    linked_tasks = session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.goal_id == g.id,
                        Task.status.in_(['pending', 'active', 'in_progress'])
                    ).limit(3).all()
                    if linked_tasks:
                        task_titles = ', '.join(t.title for t in linked_tasks)
                        line += f" [задачи: {task_titles}]"
                    goal_lines.append(line)
                hints.append("Цели: " + "; ".join(goal_lines))
            else:
                hints.append("Целей нет")

            # ═══ КОНТАКТЫ ═══
            real_contacts = []
            if profile:
                try:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user.id, session)
                    if partners:
                        for p in partners[:5]:
                            partner_user = session.query(User).filter_by(id=p.user_id).first()
                            if partner_user and partner_user.username:
                                details = []
                                if p.skills:
                                    details.append(p.skills[:60])
                                if p.interests:
                                    details.append(p.interests[:60])
                                if p.city:
                                    details.append(p.city)
                                if p.position:
                                    details.append(p.position[:40])
                                detail_str = " | ".join(details) if details else "профиль заполнен"
                                real_contacts.append(f"@{partner_user.username} ({detail_str})")
                except Exception:
                    pass

            if real_contacts:
                hints.append("КОНТАКТЫ В СЕТИ:\n" + "\n".join(f"  {c}" for c in real_contacts))
            else:
                hints.append("Контактов пока нет")

            # ═══ ВРЕМЯ СУТОК ═══
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            hints.append(f"Время суток: {time_label}")

            # ═══ УНИВЕРСАЛЬНЫЕ МЕТРИКИ ═══
            metric_hints = self._build_universal_metrics(user, session, user_tz, user_now, tasks, active_goals, today_tasks, tomorrow_tasks, overdue)
            if metric_hints:
                hints.extend(metric_hints)

            # ═══ СОЦИАЛЬНЫЙ КАПИТАЛ ═══
            social_hints = self._build_social_metrics(user, profile, session)
            if social_hints:
                hints.extend(social_hints)

            # ═══ ВАЖНЫЕ ДАТЫ ═══
            date_hints = self._analyze_upcoming_dates(user, profile, session, user_now)
            if date_hints:
                hints.extend(date_hints)

            # ═══ ВХОДЯЩИЕ СООБЩЕНИЯ ═══
            try:
                from models import UserMessage as UM
                unread_msgs = session.query(UM).filter(
                    UM.recipient_id == user.id,
                    UM.status.in_(['sent', 'delivered'])
                ).order_by(UM.created_at.desc()).limit(5).all()
                
                if unread_msgs:
                    msg_lines = []
                    for m in unread_msgs:
                        s = session.query(User).filter_by(id=m.sender_id).first()
                        s_name = f"@{s.username}" if s and s.username else "Пользователь"
                        intent_map = {'meeting': 'встреча', 'collaboration': 'сотрудничество', 'idea': 'идея', 'project_invite': 'проект', 'question': 'вопрос', 'reply': 'ответ'}
                        intent_str = intent_map.get(m.intent, m.intent or '')
                        msg_lines.append(f"  {s_name} ({intent_str}): {m.message_text[:80]}...")
                    hints.append(f"НЕПРОЧИТАННЫХ СООБЩЕНИЙ: {len(unread_msgs)} — вызови get_incoming_messages и расскажи пользователю\n" + "\n".join(msg_lines))
                
                # Проверяем ответы на отправленные сообщения
                new_replies = session.query(UM).filter(
                    UM.sender_id != user.id,
                    UM.recipient_id == user.id,
                    UM.intent == 'reply',
                    UM.status.in_(['sent', 'delivered'])
                ).order_by(UM.created_at.desc()).limit(3).all()
                
                if new_replies:
                    reply_lines = []
                    for r in new_replies:
                        s = session.query(User).filter_by(id=r.sender_id).first()
                        s_name = f"@{s.username}" if s and s.username else "Пользователь"
                        reply_lines.append(f"  {s_name}: {r.message_text[:80]}...")
                    hints.append(f"НОВЫЕ ОТВЕТЫ ({len(new_replies)}): пользователи ответили на сообщения\n" + "\n".join(reply_lines))
            except Exception as e:
                logger.warning(f"[INBOX_CTX] Error: {e}")

            # ═══ EMAIL: СТАТИСТИКА ЗА СЕГОДНЯ ═══
            try:
                from models import EmailOutreach, EmailCampaign
                from sqlalchemy import func, distinct as _distinct
                import pytz as _pytz_ec
                _tz_ec = _pytz_ec.timezone(user.timezone or 'Europe/Moscow')
                _now_ec = datetime.now(_tz_ec)
                _today_start_ec = _now_ec.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_pytz_ec.utc).replace(tzinfo=None)
                _sent_today = session.query(
                    func.count(_distinct(EmailOutreach.recipient_email))
                ).filter(
                    EmailOutreach.user_id == user.id,
                    EmailOutreach.sent_at >= _today_start_ec,
                    EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
                ).scalar() or 0
                if _sent_today > 0:
                    hints.append(f"EMAIL СЕГОДНЯ: написали {_sent_today}/20 новым получателям. Осталось: {max(0, 20 - _sent_today)}. Существующим контактам (фолоу-ап, ответ) — без ограничений.")
            except Exception as e:
                logger.warning(f"[EMAIL_DAILY_CTX] Error: {e}")

            # ═══ ОТВЕТЫ НА EMAIL (outreach replies) ═══
            try:
                recent_replies = session.query(EmailOutreach).filter(
                    EmailOutreach.user_id == user.id,
                    EmailOutreach.status == 'replied',
                    EmailOutreach.reply_text != None,
                    EmailOutreach.reply_text != ''
                ).order_by(EmailOutreach.reply_at.desc()).limit(5).all()

                if recent_replies:
                    reply_lines = []
                    for r in recent_replies:
                        name = r.recipient_name or r.recipient_email
                        reply_preview = (r.reply_text or '')[:120].replace('\n', ' ')
                        reply_lines.append(f"  {name} ({r.recipient_email}): {reply_preview}")
                    hints.append(
                        f"ОТВЕТЫ НА EMAIL ({len(recent_replies)}): контакты ответили на письма кампании — "
                        f"ты уже знаешь содержание ответов, НЕ спрашивай пользователя 'что они написали':\n"
                        + "\n".join(reply_lines)
                    )
            except Exception as e:
                logger.warning(f"[EMAIL_REPLY_CTX] Error: {e}")
            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                hints.extend(alert_hints[:2])

            # ═══ ПОХОЖИЕ ПОЛЬЗОВАТЕЛИ И ВОЗМОЖНЫЕ КОЛЛАБОРАЦИИ ═══
            similar_hints = self._find_similar_users(user, profile, session, user_tz)
            if similar_hints:
                hints.extend(similar_hints)

            if hints:
                return "\n\n[internal_context]\n" + "\n".join(hints)

            return ""

        except Exception as e:
            logger.error(f"[PROACTIVE] Error: {e}")
            return ""

    def _build_universal_metrics(self, user, session, user_tz, user_now, tasks, active_goals, today_tasks, tomorrow_tasks, overdue):
        """Универсальные метрики применимые к любой сфере жизни.
        
        5 метрик:
        1. Consistency — регулярность (дни с активностью / 7)
        2. Momentum — динамика (эта неделя vs прошлая)
        3. Focus — баланс сфер жизни через категории целей
        4. Depth — соотношение целей и задач (намерение → действие)
        5. Load — текущая нагрузка
        """
        from models import Task, Goal
        hints = []
        try:
            now_utc = datetime.now(timezone.utc)

            # ═══ 1. CONSISTENCY (регулярность) ═══
            active_days = 0
            streak = 0
            streak_broken = False
            for days_ago in range(7):
                day = user_now.date() - timedelta(days=days_ago)
                day_start = datetime(day.year, day.month, day.day, tzinfo=user_tz).astimezone(pytz.UTC)
                day_end = day_start + timedelta(days=1)
                day_completed = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'completed',
                    Task.actual_completion_time >= day_start,
                    Task.actual_completion_time < day_end
                ).count()
                day_created = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.created_at >= day_start,
                    Task.created_at < day_end
                ).count()
                if day_completed > 0 or day_created > 0:
                    active_days += 1
                    if not streak_broken:
                        streak += 1
                else:
                    streak_broken = True

            consistency_parts = []
            if active_days > 0:
                consistency_parts.append(f"{active_days}/7 дней")
            if streak >= 3:
                consistency_parts.append(f"стрик {streak}")
            elif streak == 0:
                last_done = session.query(Task).filter(
                    Task.user_id == user.id, Task.status == 'completed'
                ).order_by(Task.actual_completion_time.desc()).first()
                if last_done and last_done.actual_completion_time:
                    lc = last_done.actual_completion_time
                    if lc.tzinfo is None:
                        lc = lc.replace(tzinfo=timezone.utc)
                    idle = (now_utc - lc).days
                    if idle >= 3:
                        consistency_parts.append(f"пауза {idle} дней")
            if consistency_parts:
                hints.append(f"РЕГУЛЯРНОСТЬ: {', '.join(consistency_parts)}")

            # ═══ 2. MOMENTUM (динамика) ═══
            this_w_done = session.query(Task).filter(
                Task.user_id == user.id, Task.status == 'completed',
                Task.actual_completion_time >= now_utc - timedelta(days=7)
            ).count()
            last_w_done = session.query(Task).filter(
                Task.user_id == user.id, Task.status == 'completed',
                Task.actual_completion_time >= now_utc - timedelta(days=14),
                Task.actual_completion_time < now_utc - timedelta(days=7)
            ).count()
            this_w_created = session.query(Task).filter(
                Task.user_id == user.id,
                Task.created_at >= now_utc - timedelta(days=7)
            ).count()
            last_w_created = session.query(Task).filter(
                Task.user_id == user.id,
                Task.created_at >= now_utc - timedelta(days=14),
                Task.created_at < now_utc - timedelta(days=7)
            ).count()

            total_this = this_w_done + this_w_created
            total_last = last_w_done + last_w_created
            momentum_str = ""
            if total_this > 0 and total_last > 0:
                change = round((total_this - total_last) / total_last * 100)
                if change > 20:
                    momentum_str = f"ДИНАМИКА: +{change}% (эта неделя {total_this} действий vs {total_last})"
                elif change < -20:
                    momentum_str = f"ДИНАМИКА: {change}% (эта неделя {total_this} действий vs {total_last})"
            if momentum_str:
                hints.append(momentum_str)

            # ═══ 3. FOCUS (баланс сфер — через категории целей + задачи) ═══
            sphere_names = {
                'work': 'карьера', 'health': 'здоровье', 'learning': 'обучение',
                'finance': 'финансы', 'social': 'отношения', 'personal': 'личное'
            }
            sphere_scores = {k: 0 for k in sphere_names}

            # Из категорий целей (вес x2 — цель = намерение)
            all_goals = session.query(Goal).filter(
                Goal.user_id == user.id, Goal.status == 'active'
            ).all()
            for g in all_goals:
                cat = g.category or 'personal'
                if cat in sphere_scores:
                    sphere_scores[cat] += 2

            # Из задач по ключевым словам (фоллбэк для задач без цели)
            recent_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.created_at >= now_utc - timedelta(days=14)
            ).all()
            kw_map = {
                'work': ['код', 'проект', 'клиент', 'деплой', 'бизнес', 'mvp', 'стартап', 'продукт', 'работ', 'sprint', 'релиз'],
                'health': ['спорт', 'тренир', 'бег', 'зал', 'йога', 'врач', 'прогулк', 'отдых', 'медитац', 'сон'],
                'learning': ['изучи', 'курс', 'книг', 'учить', 'выучить', 'разобрат', 'learn'],
                'finance': ['бюджет', 'инвестиц', 'доход', 'акци', 'налог', 'финанс'],
                'social': ['позвони', 'написа', 'встрети', 'друзь', 'семь', 'подарок', 'нетворк', 'знаком', 'коллабор'],
            }
            for t in recent_tasks:
                text = (t.title + ' ' + (t.description or '')).lower()
                for sphere, kws in kw_map.items():
                    if any(kw in text for kw in kws):
                        sphere_scores[sphere] += 1
                        break

            active_s = [sphere_names[k] for k, v in sphere_scores.items() if v > 0]
            empty_s = [sphere_names[k] for k, v in sphere_scores.items() if v == 0]

            if active_s and len(empty_s) >= 2:
                dominant = max(sphere_scores, key=sphere_scores.get)
                hints.append(f"ФОКУС: «{sphere_names[dominant]}». Без внимания: {', '.join(empty_s)}")

            # ═══ 4. DEPTH (цели → задачи → результат) ═══
            if active_goals:
                orphan_goals = []
                for goal in active_goals:
                    # Строго: считаем «без шагов» только если нет явно привязанных задач (goal_id)
                    # и нет ни одной активной задачи вообще (не берём на себя семантический суд)
                    linked = session.query(Task).filter(
                        Task.user_id == user.id,
                        Task.goal_id == goal.id,
                        Task.status.in_(['pending', 'active', 'in_progress'])
                    ).count()
                    if linked > 0:
                        continue
                    # Только если у пользователя вообще нет активных задач
                    if tasks:
                        continue
                    orphan_goals.append(goal.title)
                if orphan_goals:
                    hints.append(f"ЦЕЛИ БЕЗ ШАГОВ (нет ни одной активной задачи): {', '.join(orphan_goals[:3])}")

                # Прогресс целей
                stale_goals = [g.title for g in active_goals
                               if g.progress_percentage == 0 and g.created_at
                               and (now_utc - (g.created_at.replace(tzinfo=timezone.utc)
                                    if g.created_at.tzinfo is None else g.created_at)).days >= 7]
                if stale_goals:
                    hints.append(f"БЕЗ ПРОГРЕССА >7дн: {', '.join(stale_goals[:2])}")

            # ═══ 5. LOAD (нагрузка) ═══
            today_count = len(today_tasks) + len(overdue)
            if today_count >= 6:
                hints.append(f"ПЕРЕГРУЗКА: {today_count} задач на сегодня")
            elif today_count == 0 and len(tomorrow_tasks) == 0 and not overdue and len(tasks or []) == 0:
                hints.append("ПУСТОТА: нет задач")

            # ═══ БОНУС: Пик продуктивности ═══
            completed_14d = session.query(Task).filter(
                Task.user_id == user.id, Task.status == 'completed',
                Task.actual_completion_time >= now_utc - timedelta(days=14)
            ).all()
            if len(completed_14d) >= 5:
                hours = {}
                for t in completed_14d:
                    if t.actual_completion_time:
                        ct = t.actual_completion_time
                        if ct.tzinfo is None:
                            ct = ct.replace(tzinfo=timezone.utc)
                        h = ct.astimezone(user_tz).hour
                        hours[h] = hours.get(h, 0) + 1
                if hours:
                    bh = max(hours, key=hours.get)
                    p = "утром" if 6 <= bh < 12 else "днём" if 12 <= bh < 18 else "вечером" if 18 <= bh < 23 else "ночью"
                    hints.append(f"Пик продуктивности: {p} (~{bh}:00)")

        except Exception as e:
            logger.warning(f"[METRICS] Error: {e}")
        return hints

    def _build_social_metrics(self, user, profile, session):
        """Социальный капитал: сеть, делегирование, посты, алерты, вовлечённость."""
        from models import Task, Post, PostLike, PostView, Comment, ContactAlert
        hints = []
        try:
            now_utc = datetime.now(timezone.utc)

            # ─── Сеть контактов ───
            contact_count = 0
            try:
                from .handlers import get_partners_list
                partners = get_partners_list(user.id, session)
                contact_count = len(partners) if partners else 0
            except Exception:
                pass

            # ─── Делегирование ───
            deleg_given = session.query(Task).filter(
                Task.user_id == user.id,
                Task.delegated_to_username.isnot(None)
            ).count()
            deleg_received = 0
            if user.username:
                deleg_received = session.query(Task).filter(
                    Task.delegated_to_username == user.username,
                    Task.user_id != user.id
                ).count()
            deleg_pending = session.query(Task).filter(
                Task.user_id == user.id,
                Task.delegated_to_username.isnot(None),
                Task.delegation_status == 'pending'
            ).count()

            # ─── Посты и вовлечённость ───
            recent_posts = session.query(Post).filter(
                Post.user_id == user.id,
                Post.created_at >= now_utc - timedelta(days=7)
            ).order_by(Post.created_at.desc()).limit(5).all()

            total_likes = 0
            total_comments = 0
            total_views = 0
            new_likes = 0
            new_comments_list = []
            for post in recent_posts:
                total_likes += session.query(PostLike).filter(PostLike.post_id == post.id).count()
                total_views += session.query(PostView).filter(PostView.post_id == post.id).count()
                total_comments += session.query(Comment).filter(Comment.post_id == post.id).count()
                new_likes += session.query(PostLike).filter(
                    PostLike.post_id == post.id,
                    PostLike.created_at >= now_utc - timedelta(hours=24)
                ).count()
                for c in session.query(Comment).filter(
                    Comment.post_id == post.id,
                    Comment.created_at >= now_utc - timedelta(hours=24)
                ).limit(3).all():
                    new_comments_list.append(f"@{c.username}: {(c.content or '')[:40]}")

            # ─── Алерты контактов ───
            active_alerts = session.query(ContactAlert).filter(
                ContactAlert.user_id == user.id,
                ContactAlert.enabled == True
            ).count()

            # ─── Сборка метрик ───
            parts = []
            if contact_count > 0:
                parts.append(f"контактов: {contact_count}")
            if deleg_given > 0 or deleg_received > 0:
                d = f"делегировано: {deleg_given}↑ {deleg_received}↓"
                if deleg_pending > 0:
                    d += f" ({deleg_pending} ждут)"
                parts.append(d)
            if recent_posts:
                p = f"посты: {len(recent_posts)}"
                if total_likes > 0 or total_comments > 0:
                    p += f" (лайков:{total_likes} комментов:{total_comments})"
                if total_views > 0:
                    p += f" просмотров:{total_views}"
                parts.append(p)
            if active_alerts > 0:
                parts.append(f"алерты: {active_alerts}")

            if parts:
                hints.append(f"СОЦИАЛЬНЫЙ КАПИТАЛ: {' | '.join(parts)}")
            else:
                hints.append("СОЦИАЛЬНЫЙ КАПИТАЛ: пусто — нет контактов, делегирований, постов")

            # Новое за 24ч
            new_parts = []
            if new_likes > 0:
                new_parts.append(f"+{new_likes} лайков")
            if new_comments_list:
                new_parts.append(f"+{len(new_comments_list)} комментариев")
            if new_parts:
                hints.append(f"ЗА 24Ч: {', '.join(new_parts)}")
                for nc in new_comments_list[:2]:
                    hints.append(f"  {nc}")

        except Exception as e:
            logger.warning(f"[SOCIAL] Error: {e}")
        return hints

    def _analyze_upcoming_dates(self, user, profile, session, user_now):
        """Важные даты: день рождения пользователя, дедлайны целей на этой неделе."""
        from models import Goal
        hints = []
        try:
            # === День рождения пользователя ===
            if profile and profile.birthdate:
                try:
                    parts = profile.birthdate.split('.')
                    if len(parts) >= 2:
                        bd_day, bd_month = int(parts[0]), int(parts[1])
                        current_year = user_now.year
                        try:
                            next_bd = datetime(current_year, bd_month, bd_day).date()
                        except ValueError:
                            next_bd = None
                        if next_bd:
                            if next_bd < user_now.date():
                                next_bd = datetime(current_year + 1, bd_month, bd_day).date()
                            days_until = (next_bd - user_now.date()).days
                            if days_until == 0:
                                hints.append("СЕГОДНЯ ДЕНЬ РОЖДЕНИЯ!")
                            elif 1 <= days_until <= 7:
                                hints.append(f"День рождения через {days_until} дн!")
                except Exception:
                    pass

            # === Дедлайны целей на этой неделе ===
            week_end = user_now + timedelta(days=7)
            upcoming_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active',
                Goal.target_date.isnot(None)
            ).all()

            for g in upcoming_goals:
                td = g.target_date
                if td.tzinfo is None:
                    td = td.replace(tzinfo=pytz.UTC)
                days = (td.date() - user_now.date()).days
                if 0 <= days <= 7 and g.progress_percentage < 100:
                    hints.append(f"Дедлайн цели «{g.title}» через {days}дн (прогресс {g.progress_percentage}%)")

        except Exception as e:
            logger.warning(f"[DATES] Error: {e}")
        return hints

    def _analyze_post_activity(self, user, session):
        """Активность постов: лайки, просмотры, комментарии за последние 24ч."""
        from models import Post, PostLike, PostView, Comment
        hints = []
        try:
            now_utc = datetime.now(timezone.utc)

            # Посты пользователя за последние 7 дней
            recent_posts = session.query(Post).filter(
                Post.user_id == user.id,
                Post.created_at >= now_utc - timedelta(days=7)
            ).order_by(Post.created_at.desc()).limit(5).all()

            if not recent_posts:
                return hints

            total_likes = 0
            total_views = 0
            total_comments = 0
            for post in recent_posts:
                likes = session.query(PostLike).filter(PostLike.post_id == post.id).count()
                views = session.query(PostView).filter(PostView.post_id == post.id).count()
                comments = session.query(Comment).filter(Comment.post_id == post.id).count()
                total_likes += likes
                total_views += views
                total_comments += comments

            if total_likes > 0 or total_views > 0 or total_comments > 0:
                parts = []
                if total_likes > 0:
                    parts.append(f"{total_likes} лайков")
                if total_views > 0:
                    parts.append(f"{total_views} просмотров")
                if total_comments > 0:
                    parts.append(f"{total_comments} комментариев")
                hints.append(f"📢 ПОСТЫ ({len(recent_posts)} за неделю): {', '.join(parts)}")

            # Новые лайки/комментарии за 24ч (повод упомянуть)
            new_likes = 0
            new_comments_list = []
            for post in recent_posts:
                new_likes += session.query(PostLike).filter(
                    PostLike.post_id == post.id,
                    PostLike.created_at >= now_utc - timedelta(hours=24)
                ).count()
                new_cmts = session.query(Comment).filter(
                    Comment.post_id == post.id,
                    Comment.created_at >= now_utc - timedelta(hours=24)
                ).all()
                for c in new_cmts:
                    new_comments_list.append(f"@{c.username}: {(c.content or '')[:40]}")

            if new_likes > 0 or new_comments_list:
                parts = []
                if new_likes > 0:
                    parts.append(f"+{new_likes} новых лайков")
                if new_comments_list:
                    parts.append(f"+{len(new_comments_list)} комментариев")
                hints.append(f"ЗА 24Ч: {', '.join(parts)}")
                for nc in new_comments_list[:2]:
                    hints.append(f"  {nc}")

        except Exception as e:
            logger.warning(f"[POSTS] Error: {e}")
        return hints

    def _find_similar_users(self, user, profile, session, user_tz):
        """Поиск пользователей с пересекающимися интересами, навыками и задачами."""
        from models import User, UserProfile, Task, Goal

        hints = []
        try:
            if not profile:
                return hints

            # Собираем ключевые слова пользователя
            user_keywords = set()
            for field in [profile.skills, profile.interests, profile.goals]:
                if field:
                    for word in field.lower().replace(',', ' ').split():
                        word = word.strip()
                        if len(word) > 2:
                            user_keywords.add(word)

            if not user_keywords:
                return hints

            # Ищем профили других пользователей с пересечениями
            other_profiles = session.query(UserProfile).filter(
                UserProfile.user_id != user.id
            ).limit(50).all()

            matches = []
            for op in other_profiles:
                other_keywords = set()
                for field in [op.skills, op.interests, op.goals]:
                    if field:
                        for word in field.lower().replace(',', ' ').split():
                            word = word.strip()
                            if len(word) > 2:
                                other_keywords.add(word)

                overlap = user_keywords & other_keywords
                if len(overlap) >= 2:  # минимум 2 совпадения
                    other_user = session.query(User).filter_by(id=op.user_id).first()
                    if other_user and other_user.username:
                        matches.append({
                            'username': other_user.username,
                            'overlap': list(overlap)[:4],
                            'city': op.city,
                            'position': op.position,
                            'skills': op.skills,
                        })

            if matches:
                match_lines = []
                for m in matches[:3]:  # максимум 3
                    info_parts = []
                    if m['position']:
                        info_parts.append(m['position'][:30])
                    if m['city']:
                        info_parts.append(m['city'])
                    info_str = f" ({', '.join(info_parts)})" if info_parts else ""
                    overlap_str = ', '.join(m['overlap'])
                    match_lines.append(f"  @{m['username']}{info_str} — общее: {overlap_str}")
                hints.append("ПОХОЖИЕ ИНТЕРЕСЫ:\n" + "\n".join(match_lines))

            # ═══ ПОХОЖИЕ ЗАДАЧИ ДРУГИХ ПОЛЬЗОВАТЕЛЕЙ ═══
            if user_keywords:
                yesterday = datetime.now(timezone.utc) - timedelta(days=3)
                recent_others_tasks = session.query(Task).filter(
                    Task.user_id != user.id,
                    Task.created_at >= yesterday,
                    Task.status == 'pending'
                ).order_by(Task.created_at.desc()).limit(30).all()

                similar_tasks = []
                for t in recent_others_tasks:
                    task_words = set(t.title.lower().split())
                    overlap = user_keywords & task_words
                    if overlap:
                        task_owner = session.query(User).filter_by(id=t.user_id).first()
                        if task_owner and task_owner.username:
                            similar_tasks.append(f"  @{task_owner.username}: {t.title}")

                if similar_tasks:
                    hints.append("🔗 ПОХОЖИЕ ЗАДАЧИ У ДРУГИХ:\n" + "\n".join(similar_tasks[:3]))

        except Exception as e:
            logger.warning(f"[SIMILAR_USERS] Error: {e}")

        return hints


# Глобальный экземпляр
context_builder = ContextBuilder()