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
            from sqlalchemy import or_ as _or
            tasks = session.query(Task).filter(
                _or(
                    Task.user_id == user.id,
                    Task.delegated_to_username.ilike(user.username or '__none__'),
                    Task.delegated_by == user.id,
                ),
                Task.status.in_(['pending', 'active', 'in_progress']),
                Task.delegation_status != 'rejected',
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
                    hints.append(f"ПРОСРОЧЕНО ({len(overdue)}): {', '.join(overdue[:3])} — упомяни КРАТКО при случае, но НЕ зацикливайся. Если пользователь обсуждает другое — ОТВЕЧАЙ на его тему, а просроченное можно упомянуть в конце одним предложением.")
                if today_tasks:
                    hints.append(f"СЕГОДНЯ ({len(today_tasks)}): {', '.join(today_tasks[:3])}")
                if tomorrow_tasks:
                    hints.append(f"ЗАВТРА ({len(tomorrow_tasks)}): {', '.join(tomorrow_tasks[:2])}")
                if future_tasks and not today_tasks and not overdue:
                    hints.append(f"БУДУЩИЕ ({len(future_tasks)}): {', '.join(future_tasks[:2])}")

                hints.append(f"Всего активных задач: {len(tasks)}")

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
                        _ds_map = {'pending': '⏳ждёт', 'accepted': '✅принято', 'rejected': '❌отклонено', 'completed': '✅готово'}
                        _dbl = _ds_map.get(_ds, _ds)
                        _over = ''
                        if dt.reminder_time:
                            _ddt = dt.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                            if _ddt < user_now:
                                _over = ' ⚠️ПРОСРОЧЕНО'
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
                            _ds_map = {'pending': '⏳новая', 'accepted': '✅принято', 'rejected': '❌отклонено'}
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
                        except Exception:
                            pass
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
                if _missing:
                    hints.append(f"ПРОФИЛЬ НЕПОЛНЫЙ (не заполнено: {', '.join(_missing)}) — спроси у пользователя!")

            # ═══ ЦЕЛИ (все статусы, все проекты) ═══
            from models import Goal
            all_goals = session.query(Goal).filter(
                Goal.user_id == user.id
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
                                    details.append("⚠️ нет Telegram")
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
                    EmailOutreach.reply_text != None,
                    EmailOutreach.reply_text != ''
                ).order_by(EmailOutreach.reply_at.desc()).limit(5).all()

                if recent_replies:
                    new_reply_lines = []
                    answered_reply_lines = []
                    for r in recent_replies:
                        name = r.recipient_name or r.recipient_email
                        reply_preview = (r.reply_text or '')[:120].replace('\n', ' ')
                        if r.ai_reply_sent_at:
                            answered_reply_lines.append(f"  ✅ {name} ({r.recipient_email}): [ОТВЕТ УЖЕ ОТПРАВЛЕН {r.ai_reply_sent_at.strftime('%d.%m %H:%M')}]")
                        else:
                            new_reply_lines.append(f"  🆕 {name} ({r.recipient_email}): {reply_preview}")
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
                            status_badge = '⏸️ НА ПАУЗЕ'
                        elif _is_active_c and _sent_today_c >= _dlimit:
                            status_badge = f'⏳ ЖДЁТ ЗАВТРА ({_sent_today_c}/{_dlimit})'
                        elif _is_active_c and pending_leads == 0 and (c.emails_sent or 0) == 0 and _sent_today_c == 0:
                            status_badge = '🔴 НЕТ ЛИДОВ — НУЖНЫ КОНТАКТЫ'
                        elif _is_active_c and pending_leads == 0:
                            status_badge = f'🔍 ВСЕ ОТПРАВЛЕНЫ ({_sent_today_c}/{_dlimit} сегодня)'
                        elif _is_active_c:
                            status_badge = f'🟢 ОТПРАВЛЯЕТ ({pending_leads} черновиков, {_sent_today_c}/{_dlimit} сегодня)'
                        else:
                            status_badge = f'❓ {c.status}'
                        camp_lines.append(f"  {status_badge} id={c.id} «{c.name}» — отправлено: {c.emails_sent or 0}/{c.max_emails or '∞'}")
                    hints.append("АКТИВНЫЕ EMAIL-КАМПАНИИ:\n" + "\n".join(camp_lines))
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
                        badge = '⏸️ НА ПАУЗЕ' if c.status == 'paused' else '🟢 РАБОТАЕТ'
                        cc_lines.append(
                            f"  {badge} id={c.id} «{c.name}» | {platforms} | "
                            f"{c.frequency or '?'}/день в {c.post_time or '?'} | "
                            f"опубликовано: {c.posts_published or 0}/{c.max_posts or '∞'}"
                        )
                    hints.append(
                        "КОНТЕНТ-КАМПАНИИ — УЖЕ ЗАПУЩЕНЫ, НЕ предлагай создавать новые:\n" + "\n".join(cc_lines)
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
                        badge = '⏸️ НА ПАУЗЕ' if d.status == 'paused' else '🟢 РАБОТАЕТ'
                        dc_lines.append(
                            f"  {badge} id={d.id} «{d.name}» | "
                            f"отправлено: {d.delegations_sent or 0}/{d.max_delegations or '∞'} | "
                            f"принято: {d.delegations_accepted or 0} | "
                            f"выполнено: {d.delegations_completed or 0} | "
                            f"лимит/день: {d.daily_limit or '?'}"
                        )
                    hints.append(
                        "КАМПАНИИ ДЕЛЕГИРОВАНИЯ — УЖЕ ЗАПУЩЕНЫ, НЕ предлагай создавать новые:\n" + "\n".join(dc_lines)
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
                except Exception:
                    pass
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
                except Exception:
                    pass
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
                    hints.append(f"ТОКЕНЫ: осталось {_tokens} (~{round(_tokens/450,1)} дней). При удобном случае упомяни что стоит пополнить.")
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
                        _go = _jsn_ea.loads(user.google_oauth_token)
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
                    hints.append('ПОДКЛЮЧЁННАЯ ПОЧТА (используй в send_email/negotiate_by_email):\n' + '\n'.join(f'  ✓ {a}' for a in _email_accounts))
                else:
                    hints.append('ПОЧТА: ни одного почтового ящика не подключено — send_email будет отправлять через платформенный Resend (no-reply). Чтобы отправлять со своей почты — предложи подключить Gmail/Яндекс/Mail.ru.')
            except Exception as _eae:
                logger.debug(f'[EMAIL_ACCTS_CTX] {_eae}')

            # ═══ КОМАНДА АГЕНТОВ: ASI видит кто есть и что умеет ═══
            try:
                from models import UserAgent as _UA_ctx
                _team = (
                    session.query(_UA_ctx)
                    .filter(_UA_ctx.author_id == user.id, _UA_ctx.status.in_(['active', 'paused']))
                    .order_by(_UA_ctx.id.asc())
                    .limit(15)  # увеличен лимит: агент видит всю команду
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
                        _status_badge = '⏸' if _ta.status == 'paused' else '▶'
                        _has_code = '🔄фон' if _ta.python_code and _ta.python_code.strip() else ''
                        _has_scope = f' |скоп: {_ta.search_scope[:40]}' if _ta.search_scope else ''
                        _tools = ''
                        if _ta.tools_allowed:
                            try:
                                import json as _ta_json
                                _tool_list = _ta_json.loads(_ta.tools_allowed)
                                if _tool_list:
                                    _tools = f' |инстр: {", ".join(_tool_list[:4])}'
                            except Exception:
                                pass
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

            # ═══ СВЕЖИЕ ОТЧЁТЫ АГЕНТОВ (последние 24ч) ═══
            # Агенты сохраняются как message_type='ai' с __agent JSON — ASI читает их как внутренний контекст
            try:
                from models import Interaction as _ItrRep
                _rep_since = datetime.now(timezone.utc) - timedelta(hours=24)
                _candidates = (
                    session.query(_ItrRep)
                    .filter(
                        _ItrRep.user_id == user.id,
                        _ItrRep.message_type == 'ai',
                        _ItrRep.content.contains('"__agent"'),
                        # Исключаем ASI Biont сразу на уровне SQL — не тянем лишние строки
                        ~_ItrRep.content.contains('"ASI Biont"'),
                        ~_ItrRep.content.contains('"ASI"'),
                        _ItrRep.created_at >= _rep_since,
                    )
                    .order_by(_ItrRep.created_at.desc())
                    .limit(20)
                    .all()
                )
                _reports = []
                for _r in _candidates:
                    try:
                        import json as _rj
                        _rd = _rj.loads(_r.content or '{}')
                        _rname = _rd.get('__agent', {}).get('name', '') if isinstance(_rd, dict) else ''
                        if _rname and _rname not in ('ASI Biont', 'ASI'):
                            _reports.append(_r)
                    except Exception:
                        pass
                _reports = _reports[:8]
                if _reports:
                    _rep_lines = []
                    for _r in reversed(_reports):
                        try:
                            import json as _rj
                            _rd = _rj.loads(_r.content or '{}')
                            _rname = _rd.get('__agent', {}).get('name', 'Агент') if isinstance(_rd, dict) else 'Агент'
                            _rtext = _rd.get('text', '') if isinstance(_rd, dict) else str(_rd)
                            if _rtext:
                                _ts = _r.created_at.strftime('%H:%M') if _r.created_at else ''
                                _rep_lines.append(f"  [{_rname}{' ' + _ts if _ts else ''}]: {_rtext[:200]}")
                        except Exception:
                            pass
                    if _rep_lines:
                        hints.append(
                            "ОТЧЁТЫ АГЕНТОВ (последние 24ч):\n" +
                            "\n".join(_rep_lines) +
                            "\n(это внутренние данные — упоминай только если релевантно теме разговора)"
                        )
            except Exception as _re:
                logger.debug(f"[PROACTIVE] agent_reports load error: {_re}")

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

            # ─── Сеть контактов (используем кеш из build_context) ───
            contact_count = 0
            try:
                partners = getattr(self, '_cached_partners', None)
                if partners is None:
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
                            similar_tasks.append(f"  @{task_owner.username}: {t.title}")

                if similar_tasks:
                    hints.append("🔗 ПОХОЖИЕ ЗАДАЧИ У ДРУГИХ:\n" + "\n".join(similar_tasks[:3]))

        except Exception as e:
            logger.warning(f"[SIMILAR_USERS] Error: {e}")

        return hints


# Глобальный экземпляр
context_builder = ContextBuilder()