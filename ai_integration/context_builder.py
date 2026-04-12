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

                # Pre-fetch all task owners (batch, avoid N+1)
                if recent_tasks:
                    _rt_owner_ids = list({t.user_id for t in recent_tasks})
                    _rt_owners = session.query(User).filter(User.id.in_(_rt_owner_ids)).all()
                    _rt_owner_by_id = {u.id: u for u in _rt_owners}
                else:
                    _rt_owner_by_id = {}

                for alert in activity_alerts[:2]:  # Limit to 2 alerts
                    try:
                        keywords = json.loads(alert.keywords)

                        # Find matching tasks
                        for task in recent_tasks:
                            task_text = (task.title + ' ' + (task.description or '')).lower()
                            if any(kw.lower() in task_text for kw in keywords):
                                # Get task owner (batch-loaded)
                                task_owner = _rt_owner_by_id.get(task.user_id)
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

                                    hints.append(f"АКТИВНОСТЬ ДРУГОГО УЧАСТНИКА ПЛАТФОРМЫ (не твой email-контакт): @{username} планирует: {task.title}{time_str}")

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

                # Batch-load User objects for all recent profiles
                _rp_uids = [p.user_id for p in recent_profiles]
                _rp_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_rp_uids)).all()} if _rp_uids else {}

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

                            # Check city filter (cross-language: EN/RU/raw variants)
                            if match and alert.city:
                                _alert_city_lc = alert.city.strip().lower()
                                _prof_city_variants = set(filter(None, [
                                    (getattr(profile, 'city_normalized', None) or '').strip().lower(),
                                    (getattr(profile, 'city_normalized_ru', None) or '').strip().lower(),
                                    (profile.city or '').strip().lower(),
                                ]))
                                _city_matched = any(
                                    _alert_city_lc in v or v.startswith(_alert_city_lc) or _alert_city_lc.startswith(v)
                                    for v in _prof_city_variants if v
                                )
                                if not _city_matched:
                                    match = False

                            if match:
                                profile_user = _rp_user_by_id.get(profile.user_id)
                                if profile_user and profile_user.username:
                                    username = profile_user.username
                                    detail = alert.skill or alert.interest
                                    city_str = f" из {profile.city}" if profile.city else ""
                                    hints.append(f" Новый специалист: @{username} ({detail}){city_str}")

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
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

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
            from sqlalchemy import or_ as _or
            tasks = session.query(Task).filter(
                _or(
                    Task.user_id == user.id,
                    Task.delegated_to_username.ilike(user.username or '__none__'),
                    Task.delegated_by == user.id,
                ),
                Task.status.in_(['pending', 'active', 'in_progress']),
                _or(Task.delegation_status.is_(None), Task.delegation_status != 'rejected'),
            ).order_by(Task.reminder_time.asc()).limit(15).all()

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
                                overdue.append(f"{t.title} [id={t.id}]")
                            elif dt.date() == user_now.date():
                                today_tasks.append(f"{t.title} ({dt.strftime('%H:%M')}) [id={t.id}]")
                            elif dt.date() == (user_now.date() + timedelta(days=1)):
                                tomorrow_tasks.append(f"{t.title} [id={t.id}]")
                            else:
                                future_tasks.append(f"{t.title} [id={t.id}]")
                        except Exception:
                            future_tasks.append(f"{t.title} [id={t.id}]")
                    else:
                        future_tasks.append(f"{t.title} [id={t.id}]")

                if overdue:
                    hints.append(f"ПРОСРОЧЕНО ({len(overdue)}): {', '.join(overdue[:3])} — если пользователь сообщает что СДЕЛАЛ что-то совпадающее по смыслу — СРАЗУ вызови complete_task! Иначе упомяни КРАТКО при случае, но НЕ зацикливайся.")
                if today_tasks:
                    hints.append(f"СЕГОДНЯ ({len(today_tasks)}): {', '.join(today_tasks[:3])}")
                if tomorrow_tasks:
                    hints.append(f"ЗАВТРА ({len(tomorrow_tasks)}): {', '.join(tomorrow_tasks[:2])}")
                if future_tasks and not today_tasks and not overdue:
                    hints.append(f"БУДУЩИЕ ({len(future_tasks)}): {', '.join(future_tasks[:2])}")

            # ═══ ДЕЛЕГИРОВАНИЕ: детали ═══
            try:
                from models import User as _UserD
                # Задачи которые я делегировал другим
                deleg_out = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.delegated_to_username.isnot(None),
                    Task.status.in_(['pending', 'active', 'in_progress']),
                ).order_by(Task.reminder_time.asc()).limit(5).all()
                if deleg_out:
                    d_lines = []
                    for dt in deleg_out:
                        _ds = dt.delegation_status or 'pending'
                        _ds_map = {'pending': 'ждёт', 'accepted': 'принято', 'rejected': 'отклонено', 'completed': 'готово'}
                        _dbl = _ds_map.get(_ds, _ds)
                        _over = ''
                        if dt.reminder_time:
                            _ddt = dt.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                            if _ddt < user_now:
                                _over = ' ПРОСРОЧЕНО'
                        d_lines.append(f"  {_dbl} @{dt.delegated_to_username}: {dt.title}{_over}")
                    hints.append("ДЕЛЕГИРОВАНО МНОЙ ({}):\n{}".format(len(d_lines), '\n'.join(d_lines)))

                # Задачи делегированные МНЕ
                if user.username:
                    deleg_in = session.query(Task).filter(
                        Task.delegated_to_username.ilike(user.username),
                        Task.user_id != user.id,
                        Task.status.in_(['pending', 'active', 'in_progress']),
                    ).order_by(Task.reminder_time.asc()).limit(5).all()
                    if deleg_in:
                        # Batch-load delegators
                        _din_delegator_ids = list({dt.delegated_by for dt in deleg_in if dt.delegated_by})
                        _din_delegator_by_id = {u.id: u for u in session.query(_UserD).filter(_UserD.id.in_(_din_delegator_ids)).all()} if _din_delegator_ids else {}
                        d_in_lines = []
                        for dt in deleg_in:
                            _ds = dt.delegation_status or 'pending'
                            _ds_map = {'pending': 'новая', 'accepted': 'принято', 'rejected': 'отклонено'}
                            _dbl = _ds_map.get(_ds, _ds)
                            # Находим кто делегировал
                            _from = ''
                            if dt.delegated_by:
                                _delegator = _din_delegator_by_id.get(dt.delegated_by)
                                if _delegator and _delegator.username:
                                    _from = f' от @{_delegator.username}'
                            d_in_lines.append(f"  {_dbl}{_from}: {dt.title}")
                        hints.append("ДЕЛЕГИРОВАНО МНЕ ({}):\n{}".format(len(d_in_lines), '\n'.join(d_in_lines)))
            except Exception as _de:
                logger.warning(f"[DELEG_CTX] Error: {_de}")

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
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    skipped_lines.append(f"{st.title}{reason}")
                hints.append("НЕДАВНО ПРОПУЩЕНО:\n" + "\n".join(f"  {s}" for s in skipped_lines))

            if not tasks:
                # Определяем время суток для подсказки про свободное время
                if 9 <= hour < 20:
                    hints.append(f"ЗАДАЧ НЕТ, РАСПИСАНИЕ ПУСТОЕ — сейчас {hour}:00. Прояви инициативу: задай один вопрос о текущих приоритетах или предложи ОДНО конкретное действие.")
                else:
                    hints.append("ЗАДАЧ НЕТ")

            # ═══ НЕПОЛНЫЙ ПРОФИЛЬ: подсказка ═══
            if profile:
                _missing = []
                # profile.goals — свободный текст, НЕ проверяем здесь (актуальные проекты — из таблицы Goal)
                if not profile.skills: _missing.append('навыки')
                if not profile.city: _missing.append('город')
                if not profile.interests: _missing.append('интересы')
                if _missing and interaction_cnt < 15:
                    hints.append(f"ПРОФИЛЬ НЕПОЛНЫЙ (не заполнено: {', '.join(_missing)}) — при случае уточни у пользователя.")

            # ═══ ЦЕЛИ (все статусы кроме удалённых) ═══
            from models import Goal
            all_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status.notin_(['deleted']),
            ).order_by(Goal.priority.desc().nullslast(), Goal.created_at.desc()).all()

            status_map_full = {
                'active': 'активен', 'completed': 'завершён',
                'paused': 'на паузе', 'cancelled': 'отменён'
            }

            if all_goals:
                goal_lines = []
                # Batch-load linked tasks for all goals (avoid N+1)
                _ctx_goal_ids = [g.id for g in all_goals]
                _ctx_linked_tasks_all = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.goal_id.in_(_ctx_goal_ids),
                    Task.status.in_(['pending', 'active', 'in_progress'])
                ).all() if _ctx_goal_ids else []
                _ctx_ltasks_by_goal: dict = {}
                for _clt in _ctx_linked_tasks_all:
                    if _clt.goal_id is not None:
                        _ctx_ltasks_by_goal.setdefault(_clt.goal_id, []).append(_clt)
                for g in all_goals:
                    st = status_map_full.get(g.status, g.status)
                    if g.metric_target and g.metric_unit:
                        mc = int(g.metric_current or 0)
                        mt = int(g.metric_target)
                        line = f"{g.title} [{st}] ({mc}/{mt} {g.metric_unit}, {g.progress_percentage}%)"
                    else:
                        line = f"{g.title} [{st}] ({g.progress_percentage}%)"
                    if g.target_date:
                        days = g.days_until_target()
                        if days is not None and days < 0:
                            line += " ПРОСРОЧЕНО"
                        elif days is not None and days <= 7:
                            line += f" осталось {days}дн"
                    # Активные задачи привязанные к проекту
                    if g.status == 'active':
                        linked_tasks = _ctx_ltasks_by_goal.get(g.id, [])[:5]
                        if linked_tasks:
                            task_titles = ', '.join(t.title for t in linked_tasks)
                            line += f" [задачи: {task_titles}]"
                    
                    # ── Извлекаем последние стратегические указания из описания ──
                    _strategy_note = None
                    if g.description:
                        import re as _re_strat
                        _match = _re_strat.search(r'\[СТРАТЕГИЯ.*?\](.*?)(?:\[|$)', g.description, _re_strat.DOTALL)
                        if _match:
                            _strategy_note = _match.group(1).strip()[:150]
                    
                    if _strategy_note:
                        line += f" 📌 СТРАТЕГИЯ: {_strategy_note}"
                    
                    goal_lines.append(line)
                hints.append("Проекты/цели: " + "; ".join(goal_lines))
            else:
                hints.append("ПРОЕКТОВ/ЦЕЛЕЙ НЕТ — пользователь ещё не создавал проекты или удалил все. НЕ упоминай никаких проектов/целей из прошлых сообщений.")

            # ═══ КОНТАКТЫ ═══
            real_contacts = []
            if profile:
                try:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user.id, session)
                    self._cached_partners = partners  # кеш для _build_social_metrics
                    if partners:
                        _cb_partner_uids = [p.user_id for p in partners[:5]]
                        _cb_partner_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_cb_partner_uids)).all()}
                        for p in partners[:5]:
                            partner_user = _cb_partner_by_id.get(p.user_id)
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
                                # Определяем доступность в Telegram
                                has_real_tg = partner_user.telegram_id and partner_user.telegram_id > 0
                                platform = getattr(partner_user, 'platform', 'telegram') or 'telegram'
                                if not has_real_tg or platform in ('discord', 'web'):
                                    details.append(" нет Telegram")
                                detail_str = " | ".join(details) if details else "профиль заполнен"
                                real_contacts.append(f"@{partner_user.username} ({detail_str})")
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            if real_contacts:
                hints.append("ПАРТНЁРЫ НА ПЛАТФОРМЕ (совпадают интересы, НЕ связаны с email-перепиской):\n" + "\n".join(f"  {c}" for c in real_contacts))

            # ═══ ВРЕМЯ СУТОК ═══
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            hints.append(f"Время суток: {time_label}")

            # ═══ УНИВЕРСАЛЬНЫЕ МЕТРИКИ ═══
            active_goals = [g for g in all_goals if g.status == 'active']
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
                    # Pre-fetch senders (batch)
                    _um_sids = list({m.sender_id for m in unread_msgs})
                    _um_senders = session.query(User).filter(User.id.in_(_um_sids)).all()
                    _um_sender_by_id = {u.id: u for u in _um_senders}
                    msg_lines = []
                    for m in unread_msgs:
                        s = _um_sender_by_id.get(m.sender_id)
                        s_name = f"@{s.username}" if s and s.username else "Пользователь"
                        intent_map = {'meeting': 'встреча', 'collaboration': 'сотрудничество', 'idea': 'идея', 'project_invite': 'проект', 'question': 'вопрос', 'reply': 'ответ'}
                        intent_str = intent_map.get(m.intent, m.intent or '')
                        msg_lines.append(f"  {s_name} ({intent_str}): {m.message_text[:80]}...")
                    hints.append(f"СООБЩЕНИЯ В ЯЩИКЕ ({len(unread_msgs)} непрочит.): если пользователь ни о чём не просил — можно упомянуть вскользь.\n" + "\n".join(msg_lines))
                
                # Проверяем ответы на отправленные сообщения
                new_replies = session.query(UM).filter(
                    UM.sender_id != user.id,
                    UM.recipient_id == user.id,
                    UM.intent == 'reply',
                    UM.status.in_(['sent', 'delivered'])
                ).order_by(UM.created_at.desc()).limit(3).all()
                
                if new_replies:
                    # Pre-fetch reply senders (batch)
                    _rp_sids = list({r.sender_id for r in new_replies})
                    _rp_senders = session.query(User).filter(User.id.in_(_rp_sids)).all()
                    _rp_sender_by_id = {u.id: u for u in _rp_senders}
                    reply_lines = []
                    for r in new_replies:
                        s = _rp_sender_by_id.get(r.sender_id)
                        s_name = f"@{s.username}" if s and s.username else "Пользователь"
                        reply_lines.append(f"  {s_name}: {r.message_text[:80]}...")
                    hints.append(f"НОВЫЕ ОТВЕТЫ ({len(new_replies)}): пользователи ответили на сообщения\n" + "\n".join(reply_lines))

                # ── ИСХОДЯЩИЕ: кому агент уже писал за последние 24 часа ──
                from datetime import datetime as _dt_ob, timedelta as _td_ob
                _ob_since = _dt_ob.utcnow() - _td_ob(hours=24)
                outgoing_msgs = session.query(UM).filter(
                    UM.sender_id == user.id,
                    UM.created_at >= _ob_since,
                ).order_by(UM.created_at.desc()).limit(10).all()
                if outgoing_msgs:
                    _ob_rcpt_ids = list({m.recipient_id for m in outgoing_msgs})
                    _ob_rcpts = session.query(User).filter(User.id.in_(_ob_rcpt_ids)).all()
                    _ob_rcpt_by_id = {u.id: u for u in _ob_rcpts}
                    intent_map_out = {'meeting': 'встреча', 'collaboration': 'сотрудничество',
                                      'idea': 'идея', 'project_invite': 'проект', 'question': 'вопрос'}
                    ob_lines = []
                    for m in outgoing_msgs:
                        r = _ob_rcpt_by_id.get(m.recipient_id)
                        r_name = f"@{r.username}" if r and r.username else "?"
                        ts = m.created_at.strftime('%H:%M') if m.created_at else ''
                        intent_str = intent_map_out.get(m.intent, m.intent or '')
                        ob_lines.append(f"  {r_name} ({intent_str}) в {ts}")
                    hints.append(
                        "УЖЕ НАПИСАНО (агент отправил за последние 24ч) — НЕ дублируй:\n" + "\n".join(ob_lines)
                    )
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
                _global_daily = 50
                if _sent_today > 0:
                    hints.append(f"EMAIL СЕГОДНЯ: написали {_sent_today}/{_global_daily} новым получателям. Осталось: {max(0, _global_daily - _sent_today)}. Существующим контактам (фолоу-ап, ответ) — без ограничений.")
            except Exception as e:
                logger.warning(f"[EMAIL_DAILY_CTX] Error: {e}")

            # ═══ ОТВЕТЫ НА EMAIL (outreach replies) ═══
            try:
                recent_replies = session.query(EmailOutreach).filter(
                    EmailOutreach.user_id == user.id,
                    EmailOutreach.status == 'replied',
                ).order_by(EmailOutreach.reply_at.desc().nullslast()).limit(20).all()

                if recent_replies:
                    new_reply_lines = []
                    answered_reply_lines = []
                    for r in recent_replies:
                        name = r.recipient_name or r.recipient_email
                        reply_preview = (r.reply_text or '')[:120].replace('\n', ' ')
                        if not reply_preview:
                            reply_preview = '[текст ответа не получен — вызови check_emails]'
                        # Определяем состояние диалога:
                        # 1. Они ответили ПОСЛЕ нашего last ai_reply → продолжение, нужен ответ
                        # 2. Мы уже ответили и нового ответа нет → ожидаем
                        # 3. Ещё не отвечали → нужен ответ
                        _new_reply_after_ai = (
                            r.reply_at and r.ai_reply_sent_at
                            and r.reply_at > r.ai_reply_sent_at
                        )
                        _sender_info = f", писал(а): {r.sent_by_agent}" if getattr(r, 'sent_by_agent', None) else ""
                        if _new_reply_after_ai:
                            new_reply_lines.append(
                                f"  🔄 {name} ({r.recipient_email}){_sender_info}: [ПРОДОЛЖЕНИЕ]"
                                f" последний наш ответ {r.ai_reply_sent_at.strftime('%d.%m %H:%M')},"
                                f" они написали снова: {reply_preview}"
                            )
                        elif r.ai_reply_sent_at:
                            answered_reply_lines.append(
                                f"  {name} ({r.recipient_email}){_sender_info}:"
                                f" [ОТВЕТ ОТПРАВЛЕН {r.ai_reply_sent_at.strftime('%d.%m %H:%M')}, ожидаем реакцию]"
                            )
                        else:
                            new_reply_lines.append(f"  🆕 {name} ({r.recipient_email}){_sender_info}: {reply_preview}")
                    parts = []
                    if new_reply_lines:
                        parts.append(
                            f"НОВЫЕ ОТВЕТЫ НА EMAIL ({len(new_reply_lines)}): контакты ответили — НУЖЕН ответ через reply_to_outreach_email:\n"
                            + "\n".join(new_reply_lines)
                        )
                    if answered_reply_lines:
                        parts.append(
                            f"ОТВЕЧЕННЫЕ EMAIL ({len(answered_reply_lines)}): ты уже ответил этим контактам, НЕ предлагай ответить повторно:\n"
                            + "\n".join(answered_reply_lines)
                        )
                    if parts:
                        hints.append("\n".join(parts))
            except Exception as e:
                logger.warning(f"[EMAIL_REPLY_CTX] Error: {e}")

            # ═══ АКТИВНЫЕ EMAIL-КАМПАНИИ ═══
            try:
                from models import EmailCampaign as _EC, EmailOutreach as _EO
                active_campaigns = session.query(_EC).filter(
                    _EC.user_id == user.id,
                    _EC.status.in_(['active', 'paused']),
                ).all()
                if active_campaigns:
                    import pytz as _pytz_cc
                    from datetime import timezone as _tz_cc
                    from sqlalchemy import func as _func_cc
                    _user_tz_cc = _pytz_cc.timezone(user.timezone or 'Europe/Moscow')
                    _user_now_cc = datetime.now(_user_tz_cc)
                    _today_start_cc = _user_now_cc.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz_cc.utc)
                    _camp_ids = [c.id for c in active_campaigns]
                    # Batch: pending leads per campaign
                    _pending_rows = session.query(_EO.campaign_id, _func_cc.count(_EO.id)).filter(
                        _EO.campaign_id.in_(_camp_ids), _EO.status == 'draft'
                    ).group_by(_EO.campaign_id).all()
                    _pending_map = dict(_pending_rows)
                    # Batch: sent today per campaign
                    _sent_today_rows = session.query(_EO.campaign_id, _func_cc.count(_EO.id)).filter(
                        _EO.campaign_id.in_(_camp_ids),
                        _EO.sent_at >= _today_start_cc,
                        _EO.status.in_(['sent', 'delivered', 'opened', 'replied']),
                    ).group_by(_EO.campaign_id).all()
                    _sent_today_map = dict(_sent_today_rows)
                    camp_lines = []
                    for c in active_campaigns:
                        pending_leads = _pending_map.get(c.id, 0)
                        _sent_today_c = _sent_today_map.get(c.id, 0)
                        _dlimit = c.daily_limit or 50
                        _is_active_c = c.status in ('active', 'running')
                        if c.status == 'paused':
                            status_badge = ' НА ПАУЗЕ'
                        elif _is_active_c and _sent_today_c >= _dlimit:
                            status_badge = f' ЖДЁТ ЗАВТРА ({_sent_today_c}/{_dlimit})'
                        elif _is_active_c and pending_leads == 0 and (c.emails_sent or 0) == 0 and _sent_today_c == 0:
                            status_badge = f' НЕТ ЛИДОВ — НУЖНЫ КОНТАКТЫ → add_email_leads(campaign_id={c.id})'
                        elif _is_active_c and pending_leads == 0:
                            status_badge = f' ВСЕ ОТПРАВЛЕНЫ ({_sent_today_c}/{_dlimit} сегодня) → добавь лиды: add_email_leads(campaign_id={c.id})'
                        elif _is_active_c:
                            status_badge = f'🟢 ОТПРАВЛЯЕТ ({pending_leads} черновиков, {_sent_today_c}/{_dlimit} сегодня)'
                        else:
                            status_badge = f' {c.status}'
                        camp_lines.append(f"  {status_badge} id={c.id} «{c.name}» — отправлено: {c.emails_sent or 0}/{c.max_emails or '∞'}")
                    hints.append("АКТИВНЫЕ EMAIL-КАМПАНИИ (учти их перед созданием новой — возможно стоит добавить лиды в существующую):\n" + "\n".join(camp_lines))

                # ═══ Завершённые кампании с активными диалогами ═══
                from sqlalchemy import func as _func_ccd
                _recent_completed = session.query(_EC).filter(
                    _EC.user_id == user.id,
                    _EC.status == 'completed',
                    _EC.emails_replied > 0,
                ).order_by(_EC.updated_at.desc()).limit(3).all()
                if _recent_completed:
                    _cmp_comp_lines = []
                    for _cc in _recent_completed:
                        _rep_count = session.query(_func_ccd.count(_EO.id)).filter(
                            _EO.campaign_id == _cc.id,
                            _EO.status == 'replied',
                        ).scalar() or 0
                        _pending_reply = session.query(_func_ccd.count(_EO.id)).filter(
                            _EO.campaign_id == _cc.id,
                            _EO.status == 'replied',
                            _EO.ai_reply_sent_at.is_(None),
                        ).scalar() or 0
                        _ongoing = session.query(_func_ccd.count(_EO.id)).filter(
                            _EO.campaign_id == _cc.id,
                            _EO.status == 'replied',
                            _EO.ai_reply_sent_at.isnot(None),
                            _EO.reply_at > _EO.ai_reply_sent_at,
                        ).scalar() or 0
                        _notes = []
                        if _pending_reply:
                            _notes.append(f"⚠ {_pending_reply} ждут ответа")
                        if _ongoing:
                            _notes.append(f"🔄 {_ongoing} продолжают диалог")
                        _note_str = ', '.join(_notes) if _notes else 'диалоги завершены'
                        _cmp_comp_lines.append(
                            f"  ✅ ЗАВЕРШЕНА id={_cc.id} «{_cc.name}»"
                            f" — отправлено: {_cc.emails_sent or 0}, ответили: {_rep_count} | {_note_str}"
                            f" → для follow-up используй reply_to_outreach_email(campaign_id={_cc.id})"
                        )
                    hints.append(
                        "ЗАВЕРШЁННЫЕ КАМПАНИИ С ОТВЕТАМИ — контакты ответили, не забудь продолжить диалог:\n"
                        + "\n".join(_cmp_comp_lines)
                    )
            except Exception as e:
                logger.warning(f"[CAMPAIGNS_CTX] Error: {e}")

            # ═══ АКТИВНЫЕ КОНТЕНТ-КАМПАНИИ ═══
            try:
                from models import ContentCampaign as _CCamp
                import json as _ccj
                active_cc = session.query(_CCamp).filter(
                    _CCamp.user_id == user.id,
                    _CCamp.status.in_(['active', 'paused'])
                ).all()
                if active_cc:
                    cc_lines = []
                    for c in active_cc:
                        try:
                            platforms = ', '.join(_ccj.loads(c.platforms or '["feed"]'))
                        except Exception:
                            platforms = str(c.platforms or 'feed')
                        badge = ' НА ПАУЗЕ' if c.status == 'paused' else '🟢 РАБОТАЕТ'
                        cc_lines.append(
                            f"  {badge} id={c.id} «{c.name}» | {platforms} | "
                            f"{c.frequency or '?'}/день в {c.post_time or '?'} | "
                            f"опубликовано: {c.posts_published or 0}/{c.max_posts or '∞'}"
                        )
                    hints.append(
                        "АКТИВНЫЕ КОНТЕНТ-КАМПАНИИ (учти их — если нужна новая с другой темой/аудиторией, можешь создать):\n" + "\n".join(cc_lines)
                    )
            except Exception as e:
                logger.warning(f"[CONTENT_CAMP_CTX] Error: {e}")

            # ═══ АКТИВНЫЕ КАМПАНИИ ДЕЛЕГИРОВАНИЯ ═══
            try:
                from models import DelegationCampaign as _DCamp
                active_dc = session.query(_DCamp).filter(
                    _DCamp.user_id == user.id,
                    _DCamp.status.in_(['active', 'paused'])
                ).all()
                if active_dc:
                    dc_lines = []
                    for d in active_dc:
                        badge = ' НА ПАУЗЕ' if d.status == 'paused' else '🟢 РАБОТАЕТ'
                        dc_lines.append(
                            f"  {badge} id={d.id} «{d.name}» | "
                            f"отправлено: {d.delegations_sent or 0}/{d.max_delegations or '∞'} | "
                            f"принято: {d.delegations_accepted or 0} | "
                            f"выполнено: {d.delegations_completed or 0} | "
                            f"лимит/день: {d.daily_limit or '?'}"
                        )
                    hints.append(
                        "АКТИВНЫЕ КАМПАНИИ ДЕЛЕГИРОВАНИЯ (учти их перед созданием новой):\n" + "\n".join(dc_lines)
                    )
            except Exception as e:
                logger.warning(f"[DELEG_CAMP_CTX] Error: {e}")

            # ═══ ПОСТ ЗА СЕГОДНЯ ═══
            try:
                from models import Post as _Post
                _posts_today = session.query(_Post).filter(
                    _Post.user_id == user.id,
                    _Post.created_at >= user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC).replace(tzinfo=None),
                ).count()
                if _posts_today >= 1:
                    hints.append("ПОСТ СЕГОДНЯ (лента): уже опубликован — НЕ публикуй повторно (лимит 1/день).")
                # Discord пост сегодня
                try:
                    from models import AgentActivityLog as _AAL2
                    _discord_today = session.query(_AAL2).filter(
                        _AAL2.user_id == user.id,
                        _AAL2.activity_type == 'post_discord',
                        _AAL2.created_at >= user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC).replace(tzinfo=None),
                        _AAL2.status == 'published',
                    ).count()
                    if _discord_today >= 1:
                        hints.append("ПОСТ СЕГОДНЯ (Discord): уже опубликован — НЕ публикуй повторно (лимит 1/день).")
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                # TG-канал пост сегодня
                try:
                    from models import AnchorDeliveryLog as _ADL
                    _tg_today = session.query(_ADL).filter(
                        _ADL.user_id == user.id,
                        _ADL.created_at >= user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC).replace(tzinfo=None),
                        _ADL.anchor_types.contains('channel_post'),
                    ).count()
                    if _tg_today >= 1:
                        hints.append("ПОСТ СЕГОДНЯ (Telegram-канал): уже опубликован — НЕ публикуй повторно (лимит 1/день).")
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            except Exception as e:
                logger.warning(f"[POST_CTX] Error: {e}")

            # ═══ АВТОПОСТИНГ-КАМПАНИИ ═══
            try:
                _channel = getattr(user, 'telegram_channel', None)
                _discord_wh = getattr(user, 'discord_webhook', None)
                auto_parts = []
                if _channel:
                    auto_parts.append(f"TG-канал: {_channel}")
                if _discord_wh:
                    auto_parts.append("Discord: webhook настроен")
                _post_time = getattr(profile, 'auto_post_time', None) if profile else None
                # Передаём время только если пользователь явно его установил (не дефолт '12:00')
                if _post_time and _post_time != '12:00':
                    auto_parts.append(f"Время автопостинга: {_post_time}")

                # Статистика последних постов для отчётности
                if _channel or _discord_wh:
                    try:
                        from models import Post as _PostAP, AgentActivityLog as _AALAP
                        from sqlalchemy import func as _func_ap
                        _week_ago = (user_now - timedelta(days=7)).astimezone(pytz.UTC).replace(tzinfo=None)
                        # Посты в ленте за неделю
                        _feed_week = session.query(_func_ap.count(_PostAP.id)).filter(
                            _PostAP.user_id == user.id,
                            _PostAP.created_at >= _week_ago,
                        ).scalar() or 0
                        # Посты в TG за неделю
                        _tg_week = session.query(_func_ap.count(_AALAP.id)).filter(
                            _AALAP.user_id == user.id,
                            _AALAP.activity_type == 'post_telegram',
                            _AALAP.status == 'published',
                            _AALAP.created_at >= _week_ago,
                        ).scalar() or 0
                        # Посты в Discord за неделю
                        _dc_week = session.query(_func_ap.count(_AALAP.id)).filter(
                            _AALAP.user_id == user.id,
                            _AALAP.activity_type == 'post_discord',
                            _AALAP.status == 'published',
                            _AALAP.created_at >= _week_ago,
                        ).scalar() or 0
                        _stats = []
                        if _feed_week:
                            _stats.append(f"лента: {_feed_week}")
                        if _tg_week:
                            _stats.append(f"TG: {_tg_week}")
                        if _dc_week:
                            _stats.append(f"Discord: {_dc_week}")
                        if _stats:
                            auto_parts.append(f"За неделю опубликовано: {', '.join(_stats)}")
                        # Последний пост
                        _last_tg = session.query(_AALAP).filter(
                            _AALAP.user_id == user.id,
                            _AALAP.activity_type == 'post_telegram',
                            _AALAP.status == 'published',
                        ).order_by(_AALAP.created_at.desc()).first()
                        if _last_tg and _last_tg.created_at:
                            _lp_local = _last_tg.created_at.replace(tzinfo=pytz.UTC).astimezone(pytz.timezone(user.timezone or 'Europe/Moscow'))
                            auto_parts.append(f"Последний TG-пост: {_lp_local.strftime('%d.%m %H:%M')}")
                    except Exception as _ap_err:
                        logger.debug(f"[AUTOPOST_STATS] {_ap_err}")

                if auto_parts:
                    # Определяем реально ли работает автопостинг: нужна активная контент-кампания или свежие посты за неделю
                    try:
                        _has_recent_posts = (_tg_week > 0 or _dc_week > 0)
                    except NameError:
                        _has_recent_posts = False
                    try:
                        from models import ContentCampaign as _CCampAP
                        _has_active_cc = session.query(_CCampAP).filter(
                            _CCampAP.user_id == user.id,
                            _CCampAP.status == 'active'
                        ).count() > 0
                    except Exception:
                        _has_active_cc = False

                    if _has_active_cc or _has_recent_posts:
                        hints.append("АВТО-ПОСТИНГ РАБОТАЕТ: " + " | ".join(auto_parts) + " — система публикует посты автоматически, НЕ предлагай запускать заново.")
                    else:
                        hints.append("КАНАЛ/ВЕБХУК НАСТРОЕН (но автопостинг НЕ запущен): " + " | ".join(auto_parts) + " — активных контент-кампаний нет, постов за неделю не было. НЕ говори что автопостинг работает.")
            except Exception as e:
                logger.warning(f"[AUTOPOST_CTX] Error: {e}")

            # ═══ ТОКЕНЫ И TELEGRAM-КАНАЛ ═══
            try:
                _tokens = getattr(user, 'token_balance', 0) or 0
                if _tokens < 500:
                    # < 500 ≈ ~1 день использования (средний расход ~450/день)
                    hints.append(f"ТОКЕНЫ: осталось {_tokens} — это менее суток использования, предупреди пользователя и предложи /buy.")
                elif _tokens < 1500:
                    hints.append(f"ТОКЕНЫ: осталось {_tokens} (~{round(_tokens/300,1)} дней). При удобном случае упомяни что стоит пополнить.")
                _channel = getattr(user, 'telegram_channel', None)
                if not _channel:
                    hints.append("TELEGRAM-КАНАЛ: не настроен в профиле — publish_to_telegram работать не будет до добавления канала.")
                _discord_wh = getattr(user, 'discord_webhook', None)
                if not _discord_wh:
                    hints.append("DISCORD: webhook не настроен — publish_to_discord работать не будет.")
            except Exception as e:
                logger.warning(f"[TOKENS_CTX] Error: {e}")

            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                hints.extend(alert_hints[:2])

            # ═══ ПОДКЛЮЧЁННЫЕ ПОЧТОВЫЕ ЯЩИКИ ═══
            try:
                _email_accounts = []
                # Gmail OAuth (высший приоритет)
                if getattr(user, 'google_oauth_token', None):
                    import json as _jsn_ea
                    try:
                        from config import decrypt_token as _dec_tok_ea
                        _go = _jsn_ea.loads(_dec_tok_ea(user.google_oauth_token))
                        _go_email = _go.get('email', '')
                        if _go_email:
                            _email_accounts.append(f'Gmail OAuth ({_go_email})')
                    except Exception:
                        _email_accounts.append('Gmail OAuth (подключён)')
                # Почта через агентов (SMTP/Resend ключи)
                from models import UserAgent as _UA_email
                _email_agents = session.query(_UA_email).filter(
                    _UA_email.author_id == user.id,
                    _UA_email.status != 'disabled',
                    _UA_email.user_api_keys != None,
                    _UA_email.user_api_keys != '',
                ).all()
                _email_seen = set()
                for _ea in _email_agents:
                    _env_ea: dict = {}
                    for _ln in (_ea.user_api_keys or '').splitlines():
                        _ln = _ln.strip()
                        if '=' in _ln and not _ln.startswith('#'):
                            _k, _, _v = _ln.partition('=')
                            _env_ea[_k.strip().upper()] = _v.strip()
                    for _prefix, _label in [
                        ('GMAIL_USER', 'Gmail (пароль приложения)'),
                        ('YANDEX_USER', 'Яндекс Почта'),
                        ('MAILRU_USER', 'Mail.ru'),
                    ]:
                        _addr = _env_ea.get(_prefix, '')
                        _has_pass = bool(_env_ea.get(_prefix.replace('USER', 'PASS'), ''))
                        if _addr and _addr not in _email_seen and (_has_pass or _prefix == 'GMAIL_USER'):
                            _email_seen.add(_addr)
                            _email_accounts.append(f'{_label}: {_addr} [агент: {_ea.name}]')
                    if _env_ea.get('RESEND_API_KEY') and _env_ea.get('RESEND_API_KEY') not in _email_seen:
                        _email_seen.add(_env_ea['RESEND_API_KEY'])
                        _re_from = _env_ea.get('RESEND_FROM', _env_ea.get('SENDER_EMAIL', _env_ea.get('FROM_EMAIL', '')))
                        _re_suffix = f' через {_re_from}' if _re_from else ''
                        _email_accounts.append(f'Resend API [агент: {_ea.name}]{_re_suffix}')
                if _email_accounts:
                    hints.append('ПОДКЛЮЧЁННАЯ ПОЧТА (используй в send_email/negotiate_by_email):\n' + '\n'.join(f' {a}' for a in _email_accounts))
                else:
                    hints.append('ПОЧТА: ни одного почтового ящика не подключено — send_email будет отправлять через платформенный Resend (no-reply). Чтобы отправлять со своей почты — предложи подключить Gmail/Яндекс/Mail.ru.')
            except Exception as _eae:
                logger.debug(f'[EMAIL_ACCTS_CTX] {_eae}')

            # ═══ КОМАНДА АГЕНТОВ: ASI видит кто есть и что умеет ═══
            try:
                from models import UserAgent as _UA_ctx, AgentSubscription as _AS_ctx
                from sqlalchemy import or_ as _or_ctx
                _sub_ids = [r[0] for r in session.query(_AS_ctx.agent_id).filter(_AS_ctx.user_id == user.id).all()]
                # Подписки ИЛИ собственные агенты пользователя
                if _sub_ids:
                    _team_filter = _or_(_UA_ctx.id.in_(_sub_ids), _UA_ctx.author_id == user.id)
                else:
                    _team_filter = (_UA_ctx.author_id == user.id)
                _team = (
                    session.query(_UA_ctx)
                    .filter(_team_filter, _UA_ctx.status.in_(['active', 'paused']))
                    .order_by(_UA_ctx.id.asc())
                    .limit(15)
                    .all()
                )
                if _team:
                    _KEY_MAP = {
                        'GMAIL': 'Gmail', 'YANDEX_MAIL': 'Яндекс Почта', 'MAILRU': 'Mail.ru',
                        'MAIL_RU': 'Mail.ru', 'IMAP': 'IMAP', 'SMTP': 'SMTP',
                        'OZON': 'Ozon', 'WILDBERRIES': 'WB', 'WB_': 'WB',
                        'RSS': 'RSS', 'NOTION': 'Notion', 'VK_': 'VK',
                        'TELEGRAM': 'TG', 'DISCORD': 'Discord',
                        'SLACK': 'Slack', 'GITHUB': 'GitHub', 'TRELLO': 'Trello',
                        'BINANCE': 'Binance', 'BYBIT': 'Bybit', 'STRIPE': 'Stripe',
                        'OPENAI': 'OpenAI', 'ANTHROPIC': 'Claude',
                        'YANDEX_USER': 'Яндекс Почта', 'MAILRU_USER': 'Mail.ru',
                        'GMAIL_USER': 'Gmail', 'RESEND_API_KEY': 'Resend',
                        'TWITTER_': 'Twitter/X', 'X_API': 'Twitter/X',
                        'LINKEDIN_': 'LinkedIn',
                        'YOUTUBE_': 'YouTube', 'TWILIO_': 'Twilio',
                    }
                    _agent_lines = []
                    for _ta in _team:
                        _intg = set()
                        for _kl in (_ta.user_api_keys or '').splitlines():
                            _kl = _kl.strip()
                            if '=' not in _kl or _kl.startswith('#'):
                                continue
                            _k = _kl.split('=')[0].upper()
                            for _pfx, _lbl in _KEY_MAP.items():
                                if _k.startswith(_pfx):
                                    _intg.add(_lbl)
                                    break
                        _code = (_ta.python_code or '').lower()
                        if 'gmail' in _code: _intg.add('Gmail')
                        if 'yandex' in _code: _intg.add('Яндекс почта')
                        if 'mail.ru' in _code: _intg.add('Mail.ru')
                        if 'feedparser' in _code or ('rss' in _code and 'import' in _code): _intg.add('RSS')
                        if 'imaplib' in _code and not _intg: _intg.add('IMAP')
                        if 'requests' in _code or 'httpx' in _code or 'aiohttp' in _code: _intg.add('HTTPзапросы')
                        if 'binance' in _code or 'bybit' in _code: _intg.add('крипто')

                        _intg_str = ', '.join(sorted(_intg)[:5]) if _intg else ''
                        _role = _ta.job_title or _ta.specialization or ''
                        _status_badge = '' if _ta.status == 'paused' else '▶'
                        _has_code = 'фон' if _ta.python_code and _ta.python_code.strip() else ''
                        _has_scope = f' |скоп: {_ta.search_scope[:40]}' if _ta.search_scope else ''
                        _tools = ''
                        if _ta.tools_allowed:
                            try:
                                import json as _ta_json
                                _tool_list = _ta_json.loads(_ta.tools_allowed)
                                if _tool_list:
                                    _tools = f' |инстр: {", ".join(_tool_list[:4])}'
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                        _parts = []
                        if _role: _parts.append(_role)
                        if _intg_str: _parts.append(_intg_str)
                        _detail = ' | '.join(_parts)
                        _agent_lines.append(
                            f"  {_status_badge} {_ta.name}{' — ' + _detail if _detail else ''}"
                            f"{_has_code}{_has_scope}{_tools}"
                        )

                    _n = len(_team)
                    hints.append(
                        f"КОМАНДА АГЕНТОВ ({_n}): обращайсь 'Агент, ...' или '@имя'\n" +
                        "\n".join(_agent_lines)
                    )
            except Exception as _ae:
                logger.debug(f"[PROACTIVE] agents load error: {_ae})")

            # ═══ МАТЧИНГ: ЦЕЛИ ↔ АГЕНТЫ ═══
            # Подсвечивает какой агент может помочь с какой целью
            try:
                import json as _mj
                _active_goals = [g for g in all_goals if g.status == 'active']
                if _team and _active_goals:
                    _TOOL_LABELS = {
                        'research_topic': 'исследование', 'web_search': 'веб-поиск',
                        'add_task': 'задачи', 'create_post': 'посты',
                        'send_email': 'email', 'find_relevant_contacts_for_task': 'поиск контактов',
                        'start_content_campaign': 'контент-кампания',
                        'start_delegation_campaign': 'аутрич',
                        'publish_to_telegram': 'TG посты', 'publish_to_discord': 'Discord посты',
                        'publish_to_vk': 'VK посты', 'publish_to_twitter': 'Twitter посты',
                        'publish_to_linkedin': 'LinkedIn посты',
                        'publish_to_notion': 'Notion', 'publish_to_youtube': 'YouTube',
                        'initiate_phone_call': 'звонки',
                    }
                    _matches = []
                    for _g in _active_goals[:5]:
                        _goal_lc = (_g.title + ' ' + (_g.description or '')).lower()
                        _best = []
                        for _ta in _team:
                            if _ta.status != 'active':
                                continue
                            # Собираем текст возможностей агента
                            _caps_parts = []
                            # tools_allowed
                            try:
                                _tlist = _mj.loads(_ta.tools_allowed or '[]')
                                _tool_names = [_TOOL_LABELS.get(t, t) for t in _tlist[:4]]
                                if _tool_names:
                                    _caps_parts.append(', '.join(_tool_names))
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                            # Интеграции из user_api_keys
                            _kls_upper = (_ta.user_api_keys or '').upper()
                            _code_lc = (_ta.python_code or '').lower()
                            _intg_caps = []
                            if 'GITHUB' in _kls_upper or 'github' in _code_lc:
                                _intg_caps.append('GitHub')
                            if 'GMAIL' in _kls_upper or 'YANDEX_MAIL' in _kls_upper or 'MAILRU' in _kls_upper:
                                _intg_caps.append('почта')
                            if 'TELEGRAM' in _kls_upper or 'publish_to_telegram' in (_ta.tools_allowed or ''):
                                _intg_caps.append('Telegram')
                            if 'DISCORD' in _kls_upper or 'discord' in _code_lc:
                                _intg_caps.append('Discord')
                            if 'OPENAI' in _kls_upper or 'ANTHROPIC' in _kls_upper:
                                _intg_caps.append('AI-API')
                            if _intg_caps:
                                _caps_parts.extend(_intg_caps)
                            # search_scope как тематика агента
                            if _ta.search_scope:
                                _caps_parts.append(f'тема: {_ta.search_scope[:35]}')
                            # Специализация агента
                            _agent_desc_lc = ' '.join(filter(None, [
                                _ta.name or '', _ta.description or '',
                                _ta.specialization or '', _ta.job_title or '',
                                _ta.search_scope or '',
                            ])).lower()
                            # Релевантность: совпадение слов из цели в описании агента
                            _goal_words = [w for w in _goal_lc.split() if len(w) > 3]
                            _score = sum(1 for w in _goal_words if w in _agent_desc_lc)
                            # Бонус за наличие специфических инструментов
                            if _intg_caps:
                                _score += 1
                            if _score > 0 or _caps_parts:
                                _cap_str = ', '.join(dict.fromkeys(_caps_parts))[:80]
                                _best.append((_score, _ta.name, _cap_str))
                        if _best:
                            _best.sort(key=lambda x: -x[0])
                            _agent_strs = [
                                f"{nm}{(' (' + cap + ')' if cap else '')}"
                                for _, nm, cap in _best[:2]
                            ]
                            _matches.append(f"  «{_g.title}» → {', '.join(_agent_strs)}")
                    if _matches:
                        hints.append(
                            "МАТЧИНГ ЦЕЛЬ\u2192АГЕНТ (агенты с подходящими возможностями):\n"
                            + "\n".join(_matches)
                        )
            except Exception as _me:
                logger.debug(f"[MATCH_CTX] goal-agent matching error: {_me}")

            # ═══ СВЕЖИЕ ОТЧЁТЫ АГЕНТОВ (последние 24ч) ═══
            # Агенты сохраняются как message_type='agent_msg' (координатор) с __agent JSON
            try:
                from models import Interaction as _ItrRep
                from sqlalchemy import or_ as _or_rep
                _rep_since = datetime.now(timezone.utc) - timedelta(hours=24)
                _candidates = (
                    session.query(_ItrRep)
                    .filter(
                        _ItrRep.user_id == user.id,
                        _or_rep(
                            _ItrRep.message_type == 'agent_msg',
                            _ItrRep.message_type == 'ai',
                        ),
                        _ItrRep.content.contains('"__agent"'),
                        ~_ItrRep.content.contains('"ASI Biont"'),
                        ~_ItrRep.content.contains('"name": "ASI"'),
                        _ItrRep.created_at >= _rep_since,
                    )
                    .order_by(_ItrRep.created_at.desc())
                    .limit(30)
                    .all()
                )
                _reports = []
                for _r in _candidates:
                    try:
                        import json as _rj
                        _rd = _rj.loads(_r.content or '{}')
                        _rname = _rd.get('__agent', {}).get('name', '') if isinstance(_rd, dict) else ''
                        if _rname and _rname not in ('ASI Biont', 'ASI'):
                            _reports.append((_r, _rd, _rname))
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                # Дедуп: один агент — последний результат по инструменту
                _seen_agent_tool_rep: dict = {}
                _reports_deduped = []
                for _r, _rd, _rname in _reports:
                    _tools_used = _rd.get('__tools_used', [])
                    _anchor = _rd.get('__anchor_type', '')
                    _dedup_key = (_rname, tuple(sorted(_tools_used[:2])))
                    if _dedup_key not in _seen_agent_tool_rep:
                        _seen_agent_tool_rep[_dedup_key] = True
                        _reports_deduped.append((_r, _rd, _rname))
                _reports_deduped = list(reversed(_reports_deduped[:10]))
                if _reports_deduped:
                    _rep_lines = []
                    for _r, _rd, _rname in _reports_deduped:
                        _rtext = _rd.get('text', '') if isinstance(_rd, dict) else str(_rd)
                        _tools_u = _rd.get('__tools_used', [])
                        _anchor = _rd.get('__anchor_type', '')
                        if _rtext:
                            _ts = _r.created_at.strftime('%H:%M') if _r.created_at else ''
                            _tools_tag = f' [{", ".join(_tools_u[:3])}]' if _tools_u else ''
                            _type_tag = ' [итог цикла]' if _anchor == 'coordinator_summary' else ''
                            _rep_lines.append(
                                f"  [{_rname}{_tools_tag}{_type_tag} {_ts}]: {_rtext[:350]}"
                            )
                    if _rep_lines:
                        hints.append(
                            "ОТЧЁТЫ АГЕНТОВ (последние 24ч — реальные результаты с интеграциями):\n" +
                            "\n".join(_rep_lines) +
                            "\n(используй эти данные чтобы точно отвечать что сделали агенты, какие инструменты применяли, кому писали)"
                        )
            except Exception as _re:
                logger.debug(f"[PROACTIVE] agent_reports load error: {_re}")

            # ═══ ПОСЛЕДНИЕ ДАННЫЕ АГЕНТОВ (inbox, интеграции, email, coordinator) ═══
            try:
                from models import AgentActivityLog as _AAL_ctx
                _aal_since = datetime.now(timezone.utc) - timedelta(hours=24)
                _aal_recs = (
                    session.query(_AAL_ctx)
                    .filter(
                        _AAL_ctx.user_id == user.id,
                        _AAL_ctx.activity_type.in_([
                            'inbox_reply', 'script_run',
                            'goal_autopilot_dispatch', 'agent_event_dispatch',
                            'email', 'agent_task', 'coordinator_summary',
                            'goal_updated',
                        ]),
                        _AAL_ctx.created_at >= _aal_since,
                    )
                    .order_by(_AAL_ctx.created_at.desc())
                    .limit(20)
                    .all()
                )
                if _aal_recs:
                    _aal_lines = []
                    for _ar in reversed(_aal_recs):
                        _aname = (_ar.target or _ar.title or '').replace('agent:', '')
                        _ts_ar = _ar.created_at.strftime('%H:%M') if _ar.created_at else ''
                        if _ar.activity_type == 'coordinator_summary':
                            # Итоговый отчёт координатора — самый важный
                            _preview = (_ar.result or '')[:500]
                            if _preview:
                                _aal_lines.append(f"  [Координатор {_ts_ar}] {_preview}")
                        elif _ar.activity_type == 'agent_task':
                            # Что каждый агент сделал
                            _agent_who = (_ar.target or '').replace('agent:', '')
                            _preview = (_ar.result or _ar.content or '')[:400]
                            if _preview:
                                _aal_lines.append(f"  [{_agent_who} {_ts_ar}] {_preview}")
                        elif _ar.activity_type == 'email':
                            # Письма — кому и что
                            _preview = (_ar.title or '')  # "Reply → email@..." or "Outreach → email@..."
                            _detail = (_ar.result or '')[:200]
                            _aal_lines.append(f"  [Email {_ts_ar}] {_preview}" + (f" — {_detail}" if _detail else ''))
                        elif _ar.activity_type == 'goal_updated':
                            _preview = (_ar.result or _ar.content or '')[:200]
                            if _preview:
                                _aal_lines.append(f"  [Цель обновлена {_ts_ar}] {_preview}")
                        elif _ar.activity_type in ('goal_autopilot_dispatch', 'agent_event_dispatch'):
                            _preview = (_ar.result or '')[:400]
                            _status = f' [{_ar.status}]' if _ar.status and _ar.status != 'completed' else ''
                            if _preview:
                                _aal_lines.append(f"  [{_aname}{_status} {_ts_ar}] {_preview}")
                        else:
                            _preview = (_ar.content or '')[:300]
                            if _preview:
                                _aal_lines.append(f"  [{_aname} {_ts_ar}] {_preview}")
                    # Email-контакты: replied со свежими данными
                    try:
                        from models import EmailOutreach as _EO_ctx, EmailContact as _EC_ctx
                        _replied_ctx = session.query(_EO_ctx).filter(
                            _EO_ctx.user_id == user.id,
                            _EO_ctx.status == 'replied',
                            _EO_ctx.reply_text.isnot(None),
                        ).order_by(_EO_ctx.reply_at.desc().nullslast()).limit(5).all()
                        if _replied_ctx:
                            _reply_lines = []
                            for _ro in _replied_ctx:
                                _ai_replied = '✅ ответили' if _ro.ai_reply_sent_at else '⏳ ждёт ответа'
                                _reply_preview = (_ro.reply_text or '')[:200].replace('\n', ' ')
                                _reply_lines.append(
                                    f"  {_ro.recipient_name or _ro.recipient_email} ({_ro.recipient_email}): "
                                    f"{_ai_replied} | «{_reply_preview}»"
                                )
                            _aal_lines.append(
                                f"\nВХОДЯЩИЕ ПИСЬМА от контактов:\n" + "\n".join(_reply_lines)
                            )
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    if _aal_lines:
                        hints.append(
                            "ДЕЙСТВИЯ АГЕНТОВ ЗА 24Ч (интеграции, email, результаты):\n" +
                            "\n".join(_aal_lines) +
                            "\n(используй как контекст — отвечай на вопросы пользователя по этим данным без переспроса)"
                        )
            except Exception as _aal_e:
                logger.debug(f"[PROACTIVE] agent activity log error: {_aal_e}")

            # ═══ НЕДАВНИЕ ДИРЕКТИВЫ (что ASI уже поручил агентам за последние 2ч) ═══
            # Цель: ASI видит собственные недавние поручения → НЕ повторяет их
            try:
                from models import Interaction as _ItrDir
                _dir_since = datetime.now(timezone.utc) - timedelta(hours=2)
                _dir_msgs = (
                    session.query(_ItrDir)
                    .filter(
                        _ItrDir.user_id == user.id,
                        _ItrDir.message_type == 'agent_msg',
                        _ItrDir.created_at >= _dir_since,
                    )
                    .order_by(_ItrDir.created_at.asc())
                    .limit(10)
                    .all()
                )
                # Директивы — это agent_msg БЕЗ поля __agent (отчёты агентов имеют __agent)
                _directive_lines = []
                import json as _jdir
                for _dm in _dir_msgs:
                    try:
                        _dj = _jdir.loads(_dm.content or '{}')
                        # Пропускаем отчёты агентов (имеют __agent)
                        if isinstance(_dj, dict) and '__agent' in _dj and _dj['__agent']:
                            continue
                        # Это директива: либо plain text, либо JSON без __agent
                        if isinstance(_dj, dict):
                            _dtxt = (_dj.get('text','') or _dj.get('message','') or '')[:200]
                        else:
                            _dtxt = str(_dj)[:200]
                    except Exception:
                        _dtxt = (_dm.content or '')[:200]
                    _dts = _dm.created_at.strftime('%H:%M') if _dm.created_at else ''
                    if _dtxt.strip():
                        _directive_lines.append(f"  [{_dts}] {_dtxt}")
                if _directive_lines:
                    hints.append(
                        "ДИРЕКТИВЫ АГЕНТАМ ЗА ПОСЛЕДНИЕ 2Ч (ты уже давал эти поручения — НЕ повторяй их, выбери НОВЫЙ следующий шаг):\n"
                        + "\n".join(_directive_lines)
                        + "\nЕСЛИ агент уже получил поручение → считай его в работе, не дублируй."
                    )
            except Exception as _dir_e:
                logger.debug(f"[PROACTIVE] directives load error: {_dir_e}")

            # ═══ ПЕРСОНАЛИЗАЦИЯ: ПРИОРИТЕТЫ ЦЕЛЕЙ И ДОСТУПНЫЕ ИНСТРУМЕНТЫ ═══
            # Компактный план который помогает агенту думать с учётом конкретного пользователя
            try:
                _p_active = [g for g in all_goals if g.status == 'active']
                _priority_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
                _p_active.sort(key=lambda g: (_priority_order.get(g.priority, 4), -(g.progress_percentage or 0)))

                if _p_active:
                    _prio_map = {
                        'critical': '🔴 КРИТИЧНО', 'high': '🟠 ВЫСОКИЙ',
                        'medium': '🟡 СРЕДНИЙ',   'low':  '🟢 НИЗКИЙ',
                    }
                    # Какие инструменты реально доступны (через агентов или аккаунт)
                    _avail_caps: list[str] = []
                    try:
                        from models import UserAgent as _UA_plan, AgentSubscription as _AS_plan
                        from sqlalchemy import or_ as _or_plan
                        _sub_ids_plan = [
                            r[0] for r in session.query(_AS_plan.agent_id)
                            .filter(_AS_plan.user_id == user.id).all()
                        ]
                        _plan_filt = (
                            _or_plan(_UA_plan.id.in_(_sub_ids_plan), _UA_plan.author_id == user.id)
                            if _sub_ids_plan else (_UA_plan.author_id == user.id)
                        )
                        _plan_team = session.query(_UA_plan).filter(
                            _plan_filt, _UA_plan.status.in_(['active', 'paused'])
                        ).all()
                        for _pa in _plan_team:
                            _pa_kup = (_pa.user_api_keys or '').upper()
                            _pa_code = (_pa.python_code or '').lower()
                            _pa_caps: list[str] = []
                            if ('GMAIL' in _pa_kup or 'YANDEX_USER' in _pa_kup
                                    or 'MAILRU_USER' in _pa_kup or 'RESEND_API_KEY' in _pa_kup
                                    or getattr(user, 'google_oauth_token', None)):
                                _pa_caps.append('email outreach')
                            if 'GITHUB' in _pa_kup or 'github' in _pa_code:
                                _pa_caps.append('GitHub')
                            if 'NOTION' in _pa_kup:
                                _pa_caps.append('Notion')
                            if 'SLACK' in _pa_kup:
                                _pa_caps.append('Slack')
                            if 'STRIPE' in _pa_kup:
                                _pa_caps.append('Stripe/платежи')
                            if 'BITRIX' in _pa_kup or 'AMOCRM' in _pa_kup or 'HUBSPOT' in _pa_kup:
                                _pa_caps.append('CRM')
                            if 'feedparser' in _pa_code or 'RSS' in _pa_kup:
                                _pa_caps.append('RSS мониторинг')
                            if 'imaplib' in _pa_code or 'IMAP' in _pa_kup:
                                _pa_caps.append('чтение inbox')
                            if 'TWITTER' in _pa_kup or 'X_API' in _pa_kup or 'tweepy' in _pa_code:
                                _pa_caps.append('Twitter/X')
                            if 'VK_' in _pa_kup or 'vk_api' in _pa_code:
                                _pa_caps.append('VK')
                            if 'GOOGLE_SHEETS' in _pa_kup or 'gspread' in _pa_code:
                                _pa_caps.append('Google Sheets')
                            if 'BINANCE' in _pa_kup or 'BYBIT' in _pa_kup or 'binance' in _pa_code:
                                _pa_caps.append('крипто-API')
                            if 'OPENAI' in _pa_kup or 'ANTHROPIC' in _pa_kup:
                                _pa_caps.append('внешний AI')
                            if _pa_caps:
                                _avail_caps.append(f"{_pa.name}: {', '.join(_pa_caps)}")
                        # Платформенные возможности (без агента)
                        if getattr(user, 'telegram_channel', None):
                            _avail_caps.append(f"TG-канал @{user.telegram_channel}: посты/контент")
                        if getattr(user, 'discord_webhook', None):
                            _avail_caps.append("Discord канал: публикации")
                    except Exception as _cap_e:
                        logger.debug(f"[PERSONALIZATION_CAPS] {_cap_e}")

                    _goal_lines = []
                    for _pg in _p_active[:4]:
                        _pr_label = _prio_map.get(_pg.priority, '⚪')
                        _pct = _pg.progress_percentage or 0
                        _days_left = ''
                        if _pg.target_date:
                            _dl = _pg.days_until_target()
                            if _dl is not None and _dl < 0:
                                _days_left = ' ⚠️ ПРОСРОЧЕНО'
                            elif _dl is not None and _dl <= 14:
                                _days_left = f' ⏰ {_dl}дн до дедлайна'
                        _goal_lines.append(
                            f"  {_pr_label} «{_pg.title}» ({_pct}%"
                            + (f", категория: {_pg.category}" if _pg.category else "")
                            + f"){_days_left}"
                        )

                    _section = (
                        "ПЕРСОНАЛИЗАЦИЯ (адаптируй ВСЕ решения под этого пользователя):\n"
                        "Активные цели по приоритету:\n"
                    )
                    _section += "\n".join(_goal_lines)
                    if _avail_caps:
                        _section += "\nДоступные инструменты команды:\n"
                        _section += "\n".join(f"  • {c}" for c in _avail_caps)
                    else:
                        _section += "\nИнтеграций нет — действуй через задачи, советы, вопросы пользователю."
                    _section += (
                        "\n→ ПРАВИЛО: предлагай именно те действия, которые возможны с этими инструментами "
                        "и продвигают именно эти цели. Не говори о GitHub если цель — здоровье. "
                        "Не предлагай email если нет email-агента. Один фокус — одна цель — один инструмент."
                    )
                    hints.append(_section)
            except Exception as _pe:
                logger.debug(f"[PERSONALIZATION] {_pe}")

            # ═══ ЗАМЕТКИ: последние результаты работы агентов ═══
            try:
                from models import Note
                _recent_notes = session.query(Note).filter(
                    Note.user_id == user.id,
                ).order_by(Note.created_at.desc()).limit(5).all()
                if _recent_notes:
                    _note_lines = []
                    for _n in _recent_notes:
                        _nc = (_n.content or '')[:150].replace('\n', ' ')
                        _note_lines.append(f"  [{_n.created_at.strftime('%d.%m %H:%M')}] {_n.title}: {_nc}")
                    hints.append("ЗАМЕТКИ (последние результаты команды — НЕ дублируй, используй как базу):\n" + "\n".join(_note_lines))
            except Exception as _ne:
                logger.debug(f"[NOTES_CTX] {_ne}")

            # ═══ ПОХОЖИЕ ПОЛЬЗОВАТЕЛИ И ВОЗМОЖНЫЕ КОЛЛАБОРАЦИИ ═══
            similar_hints = self._find_similar_users(user, profile, session, user_tz)
            if similar_hints:
                hints.extend(similar_hints)

            # ═══ РЕКОМЕНДАЦИИ ИНТЕГРАЦИЙ (макс. 1, не чаще 72ч) ═══
            try:
                _int_rec = self._recommend_integration(user, profile, all_goals, session)
                if _int_rec:
                    hints.append(_int_rec)
            except Exception as _ire:
                logger.debug(f"[INTEG_REC] {_ire}")

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

            # ─── Сеть контактов (используем кеш из build_context) ───
            contact_count = 0
            try:
                partners = getattr(self, '_cached_partners', None)
                if partners is None:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user.id, session)
                contact_count = len(partners) if partners else 0
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

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
            if recent_posts:
                _post_ids = [p.id for p in recent_posts]
                _now_24h = now_utc - timedelta(hours=24)
                # Агрегируем за один запрос вместо N×6 запросов
                from sqlalchemy import func as _func_cb
                total_likes = session.query(_func_cb.count(PostLike.id)).filter(
                    PostLike.post_id.in_(_post_ids)
                ).scalar() or 0
                total_views = session.query(_func_cb.count(PostView.id)).filter(
                    PostView.post_id.in_(_post_ids)
                ).scalar() or 0
                total_comments = session.query(_func_cb.count(Comment.id)).filter(
                    Comment.post_id.in_(_post_ids)
                ).scalar() or 0
                new_likes = session.query(_func_cb.count(PostLike.id)).filter(
                    PostLike.post_id.in_(_post_ids),
                    PostLike.created_at >= _now_24h
                ).scalar() or 0
                new_comments = session.query(Comment).filter(
                    Comment.post_id.in_(_post_ids),
                    Comment.created_at >= _now_24h
                ).order_by(Comment.created_at.desc()).limit(5).all()
                for c in new_comments:
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
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

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

    # Кэш: user_id → datetime последней рекомендации
    _integration_rec_cache: dict = {}

    def _recommend_integration(self, user, profile, all_goals, session) -> str | None:
        """Передаёт агенту список неподключённых интеграций И неиспользуемых фич —
        агент сам решает что и когда предложить.

        Anti-spam: не чаще 1 раза за 24 часа на пользователя.
        """
        uid = user.id
        now = datetime.now(timezone.utc)

        last = self._integration_rec_cache.get(uid)
        if last and (now - last).total_seconds() < 24 * 3600:
            return None

        # --- что подключено ---
        _has_tg_channel = bool(getattr(user, 'telegram_channel', None))
        _has_discord = bool(getattr(user, 'discord_webhook', None))
        _has_gmail_oauth = bool(getattr(user, 'google_oauth_token', None))
        _has_autopilot = bool(getattr(profile, 'goal_autopilot_enabled', False)) if profile else False

        from models import UserAgent as _UA_rec
        _agents = session.query(_UA_rec).filter(
            _UA_rec.author_id == user.id,
            _UA_rec.status != 'disabled',
        ).all()
        _has_agents = len(_agents) > 0
        _kup = ' '.join((_a.user_api_keys or '').upper() for _a in _agents)
        _clo = ' '.join((_a.python_code or '').lower() for _a in _agents)

        _has_email = (_has_gmail_oauth
                      or 'GMAIL_USER' in _kup or 'GMAIL_PASS' in _kup
                      or 'YANDEX_USER' in _kup or 'MAILRU_USER' in _kup)
        _has_imap = 'imaplib' in _clo or 'IMAP' in _kup
        _has_github = 'GITHUB' in _kup
        _has_notion = 'NOTION' in _kup
        _has_slack = 'SLACK' in _kup
        _has_rss = 'feedparser' in _clo or 'RSS' in _kup
        _has_trello = 'TRELLO' in _kup
        _has_jira = 'JIRA' in _kup or 'ATLASSIAN' in _kup
        _has_gsheets = 'GOOGLE_SHEETS' in _kup or 'gspread' in _clo or 'SHEETS' in _kup
        _has_airtable = 'AIRTABLE' in _kup
        _has_stripe = 'STRIPE' in _kup
        _has_shopify = 'SHOPIFY' in _kup
        _has_whatsapp = 'WHATSAPP' in _kup or 'TWILIO' in _kup
        _has_twitter = 'TWITTER' in _kup or 'X_API' in _kup or 'TWEEPY' in _kup
        _has_linkedin = 'LINKEDIN' in _kup
        _has_youtube = 'YOUTUBE' in _kup
        _has_twilio = 'TWILIO' in _kup
        _has_openai = 'OPENAI' in _kup
        _has_calendar = 'GOOGLE_CALENDAR' in _kup or 'CALDAV' in _kup
        _has_crm = 'BITRIX' in _kup or 'AMOCRM' in _kup or 'HUBSPOT' in _kup or 'SALESFORCE' in _kup
        _has_webhook = 'WEBHOOK_URL' in _kup or 'webhook' in _clo
        _has_1c = '1C' in _kup or 'ODATA' in _kup

        # Проверяем использование фич
        _has_content_campaign = False
        _has_email_campaign = False
        _has_delegation_campaign = False
        try:
            from models import ContentCampaign as _CC_r, EmailCampaign as _EC_r, DelegationCampaign as _DC_r
            _has_content_campaign = session.query(_CC_r).filter_by(user_id=user.id).first() is not None
            _has_email_campaign = session.query(_EC_r).filter_by(user_id=user.id).first() is not None
            try:
                _has_delegation_campaign = session.query(_DC_r).filter_by(user_id=user.id).first() is not None
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        from models import AgentSubscription as _AS_r
        _has_marketplace_agents = session.query(_AS_r).filter_by(user_id=user.id).first() is not None

        # ═══ ИНТЕГРАЦИИ (через агентов) ═══
        # (not_connected, name, benefit, how_to_connect)
        _integrations = [
            (not _has_tg_channel,
             "Telegram-канал",
             "автопостинг контента по стратегии",
             "Настройки → «Telegram-канал» (@username или chat ID)"),
            (not _has_discord,
             "Discord webhook",
             "автопубликация в Discord-канал",
             "Discord → канал → Настройки → Webhooks → URL → в профиле"),
            (not _has_email,
             "Личная почта (Gmail/Яндекс/Mail.ru)",
             "email от имени пользователя, автоответы, аутрич",
             "Gmail OAuth в настройках, или пароль приложения в агенте"),
            (_has_email and not _has_imap,
             "IMAP-мониторинг входящих",
             "агент читает inbox, уведомляет и отвечает автоматически",
             "создать агента с python_code для IMAP + ключи"),
            (not _has_github,
             "GitHub",
             "автоматизация issues/PR, мониторинг репозиториев, CI/CD",
             "агент + GITHUB_TOKEN"),
            (not _has_notion,
             "Notion",
             "синхронизация задач, обновление баз знаний",
             "агент + NOTION_TOKEN"),
            (not _has_slack,
             "Slack",
             "отчёты в каналы, мониторинг сообщений",
             "агент + SLACK_BOT_TOKEN"),
            (not _has_rss,
             "RSS-мониторинг",
             "автосбор новостей из любых источников с дайджестом",
             "агент с feedparser в python_code"),
            (not _has_trello,
             "Trello",
             "синхронизация задач, автоматическое перемещение карточек",
             "агент + TRELLO_API_KEY + TRELLO_TOKEN"),
            (not _has_jira,
             "Jira / Atlassian",
             "трекинг задач, обновление статусов, уведомления",
             "агент + JIRA_URL + JIRA_TOKEN"),
            (not _has_gsheets,
             "Google Sheets",
             "автоматические отчёты, обновление таблиц, сбор данных",
             "агент + GOOGLE_SHEETS_CREDENTIALS или gspread"),
            (not _has_airtable,
             "Airtable",
             "CRM, таблицы, автоматизация баз данных",
             "агент + AIRTABLE_API_KEY"),
            (not _has_stripe,
             "Stripe",
             "мониторинг платежей, отчёты по выручке",
             "агент + STRIPE_SECRET_KEY"),
            (not _has_shopify,
             "Shopify",
             "мониторинг заказов, обновление товаров",
             "агент + SHOPIFY_ACCESS_TOKEN"),
            (not _has_whatsapp,
             "WhatsApp (Twilio)",
             "рассылки и уведомления через WhatsApp",
             "агент + TWILIO_SID + TWILIO_TOKEN"),
            (not _has_twitter,
             "Twitter / X",
             "автопостинг твитов, мониторинг упоминаний",
             "агент + TWITTER_API_KEY + TWITTER_API_SECRET + TWITTER_ACCESS_TOKEN + TWITTER_ACCESS_SECRET"),
            (not _has_linkedin,
             "LinkedIn",
             "публикация профессиональных постов, нетворкинг",
             "агент + LINKEDIN_ACCESS_TOKEN"),
            (not _has_youtube,
             "YouTube",
             "аналитика канала, комментарии, управление контентом",
             "агент + YOUTUBE_API_KEY + YOUTUBE_CHANNEL_ID"),
            (not _has_twilio,
             "Телефонные звонки (Twilio)",
             "голосовые звонки, SMS-уведомления",
             "агент + TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM"),
            (not _has_calendar,
             "Google Calendar",
             "синхронизация задач с календарём, авторасписание",
             "агент + GOOGLE_CALENDAR_CREDENTIALS"),
            (not _has_crm,
             "CRM (Bitrix24 / AmoCRM / HubSpot)",
             "синхронизация контактов, воронка продаж",
             "агент + API-ключ CRM-системы"),
            (not _has_webhook,
             "Webhook (любой сервис)",
             "отправка данных в любой сервис по событиям",
             "агент + WEBHOOK_URL в ключах"),
            (not _has_1c,
             "1С / ERP",
             "синхронизация данных с 1С через OData API",
             "агент + 1C_URL + 1C_USER + 1C_PASS"),
            (not _has_openai,
             "OpenAI / GPT-4",
             "использование GPT-4 для специализированных задач агента",
             "агент + OPENAI_API_KEY"),
        ]

        # ═══ ФИЧИ ПЛАТФОРМЫ (не активированы) ═══
        _features = []
        if not _has_autopilot:
            _features.append((
                "Автопилот целей",
                "агенты автономно работают над целями: исследуют, создают задачи, отправляют письма",
                "кнопка ⚡ в шапке дашборда или скажи «включи автопилот»"
            ))
        if not _has_agents:
            _features.append((
                "Команда агентов",
                "создай агентов-специалистов: маркетолог, аналитик, ассистент — каждый со своими интеграциями и скриптами",
                "https://asibiont.com/dashboard → раздел «Агенты» → «Создать агента»"
            ))
        if not _has_content_campaign:
            _features.append((
                "Контент-кампании",
                "автопубликация постов по расписанию в блог/TG/Discord с AI-генерацией",
                "скажи «запусти кампанию контента» или start_content_campaign"
            ))
        if not _has_email_campaign:
            _features.append((
                "Email-кампании",
                "массовый аутрич с персонализацией, follow-up и отслеживанием ответов",
                "скажи «запусти email-кампанию» или start_email_campaign"
            ))
        if not _has_delegation_campaign:
            _features.append((
                "Кампании делегирования",
                "автопоиск людей и рассылка приглашений к сотрудничеству",
                "скажи «найди партнёров для [задача]» или start_delegation_campaign"
            ))
        if not _has_marketplace_agents:
            _features.append((
                "Маркетплейс агентов",
                "подключи готовых агентов других пользователей — аналитики, копирайтеры, исследователи",
                "https://asibiont.com/dashboard → «Маркетплейс» → подписка на агента"
            ))

        available_integrations = [(n, b, h) for flag, n, b, h in _integrations if flag]
        connected_integrations = [(n, b) for flag, n, b, h in _integrations if not flag]
        all_items = []

        # ── Подключённые интеграции (краткая сводка) ──
        if connected_integrations:
            _conn_names = [n for n, _ in connected_integrations]
            all_items.append("ПОДКЛЮЧЁННЫЕ ИНТЕГРАЦИИ: " + ', '.join(_conn_names))

        # ── Неподключённые интеграции ──
        if available_integrations:
            # Ранжируем по релевантности целям пользователя
            _goal_text = ' '.join(
                (g.title + ' ' + (g.description or '')).lower()
                for g in (all_goals or []) if g.status == 'active'
            )
            _INTG_KEYWORDS = {
                'GitHub': ['github', 'код', 'разработ', 'програм', 'репозитор', 'issue', 'pr', 'ci', 'деплой'],
                'Notion': ['notion', 'знани', 'база', 'документ', 'вики', 'заметк'],
                'Slack': ['slack', 'команд', 'чат', 'коммуник', 'team'],
                'Trello': ['trello', 'канбан', 'доск', 'карточк'],
                'Jira / Atlassian': ['jira', 'atlassian', 'спринт', 'бэклог', 'agile'],
                'Google Sheets': ['таблиц', 'excel', 'sheets', 'отчёт', 'данн', 'аналитик'],
                'Stripe': ['stripe', 'оплат', 'плат', 'подписк', 'выручк', 'billing'],
                'Shopify': ['shopify', 'магазин', 'товар', 'заказ', 'e-commerce'],
                'CRM (Bitrix24 / AmoCRM / HubSpot)': ['crm', 'bitrix', 'amo', 'hubspot', 'воронк', 'продаж', 'лид', 'клиент'],
                'Личная почта (Gmail/Яндекс/Mail.ru)': ['почт', 'email', 'gmail', 'письм', 'аутрич', 'рассылк'],
                'Telegram-канал': ['telegram', 'канал', 'подписчик', 'контент', 'пост', 'публикац'],
                'Google Calendar': ['календар', 'расписан', 'встреч', 'событи'],
                'RSS-мониторинг': ['rss', 'новост', 'мониторинг', 'дайджест'],
            }
            _scored = []
            for name, benefit, how in available_integrations:
                _kws = _INTG_KEYWORDS.get(name, [])
                _score = sum(1 for kw in _kws if kw in _goal_text) if _goal_text else 0
                _scored.append((_score, name, benefit, how))
            _scored.sort(key=lambda x: -x[0])

            # Показываем рекомендованные (матч с целями) отдельно
            _recommended = [(n, b, h) for s, n, b, h in _scored if s > 0]
            _others = [(n, b, h) for s, n, b, h in _scored if s == 0]

            if _recommended:
                all_items.append("РЕКОМЕНДУЕМЫЕ ИНТЕГРАЦИИ (полезны для текущих целей):")
                for name, benefit, how in _recommended[:5]:
                    all_items.append(f"• {name} — {benefit}. Подключить: {how}")

            if _others:
                all_items.append("ДРУГИЕ ДОСТУПНЫЕ ИНТЕГРАЦИИ:")
                for name, benefit, how in _others[:8]:
                    all_items.append(f"• {name} — {benefit}")

        if _features:
            all_items.append("НЕИСПОЛЬЗУЕМЫЕ ВОЗМОЖНОСТИ ПЛАТФОРМЫ:")
            for name, benefit, how in _features:
                all_items.append(f"• {name} — {benefit}. Активировать: {how}")

        if not all_items:
            return None

        all_items.append(
            "Рекомендуй интеграции ТОЛЬКО если релевантно текущему разговору или целям. "
            "Предлагай конкретно: «для этой задачи подойдёт X — подключить?». "
            "Не перечисляй все. Если ничего не релевантно — промолчи."
        )

        self._integration_rec_cache[uid] = now
        return '\n'.join(all_items)

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

            # Pre-fetch all profile owners (batch, avoid N+1)
            if other_profiles:
                _op_uids = [op.user_id for op in other_profiles]
                _op_users = session.query(User).filter(User.id.in_(_op_uids)).all()
                _op_user_by_id = {u.id: u for u in _op_users}
            else:
                _op_user_by_id = {}

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
                    other_user = _op_user_by_id.get(op.user_id)
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

                # Pre-fetch task owners (batch, avoid N+1)
                if recent_others_tasks:
                    _rot_uids = list({t.user_id for t in recent_others_tasks})
                    _rot_owners = session.query(User).filter(User.id.in_(_rot_uids)).all()
                    _rot_owner_by_id = {u.id: u for u in _rot_owners}
                else:
                    _rot_owner_by_id = {}

                similar_tasks = []
                for t in recent_others_tasks:
                    task_words = set(t.title.lower().split())
                    overlap = user_keywords & task_words
                    if overlap:
                        task_owner = _rot_owner_by_id.get(t.user_id)
                        if task_owner and task_owner.username:
                            similar_tasks.append(f"  • {t.title}")

                if similar_tasks:
                    hints.append("ПОХОЖИЕ ЗАДАЧИ У ДРУГИХ УЧАСТНИКОВ ПЛАТФОРМЫ (это НЕ твои email-контакты):\n" + "\n".join(similar_tasks[:3]))

        except Exception as e:
            logger.warning(f"[SIMILAR_USERS] Error: {e}")

        return hints


# Глобальный экземпляр
context_builder = ContextBuilder()