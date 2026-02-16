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
        """Get proactive alerts for Premium users

        Checks for:
        1. Activity alerts - when other users create matching tasks
        2. Contact alerts - when new users with matching skills/interests join

        Returns list of hint strings to add to context
        """
        from models import User, UserProfile, Task, ActivityAlert, ContactAlert, SubscriptionTier

        hints = []

        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user or user.subscription_tier == SubscriptionTier.LIGHT:
                return hints

            # 1. Activity alerts - check recent tasks from other users
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
                                        except:
                                            pass

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

            # Commit updates to last_triggered_at
            if hints:
                session.commit()

        except Exception as e:
            logger.error(f"[PREMIUM_ALERTS] Error: {e}")

        return hints

    def build_proactive_context(self, user_id, session, profile_complete=True):
        """АНАЛИТИЧЕСКИЙ КОНТЕКСТ: что есть, чего нет, что делать."""
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

            # ═══ АНАЛИЗ ПРОФИЛЯ: что есть и чего нет ═══
            profile_has = []
            profile_missing = []
            ALL_FIELDS = {
                'city': 'город', 'company': 'компания', 'position': 'должность',
                'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы'
            }
            if profile:
                for field, label in ALL_FIELDS.items():
                    val = getattr(profile, field, None)
                    if val:
                        profile_has.append(f"{label}: {val[:50]}")
                    else:
                        profile_missing.append(label)
            else:
                profile_missing = list(ALL_FIELDS.values())

            if profile_missing:
                hints.append(f"👤 ПРОФИЛЬ — не заполнено: {', '.join(profile_missing)}")
            if profile_has:
                hints.append(f"👤 ПРОФИЛЬ — есть: {'; '.join(profile_has)}")

            # Если профиль совсем пустой — это приоритет, но НЕ блокирует остальной контекст
            if len(profile_missing) >= 5:
                hints.append("⚡ ДЕЙСТВИЕ: узнай о человеке через живой разговор, не допрашивай")

            # ═══ ЗАДАЧИ: точки контроля ═══
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
                        except:
                            future_tasks.append(t.title)
                    else:
                        future_tasks.append(t.title)

                if overdue:
                    hints.append(f"🚨 ПРОСРОЧЕНО ({len(overdue)}): {', '.join(overdue[:3])}")
                    hints.append("⚡ ОБРАТНАЯ СВЯЗЬ: спроси — выполнена ли задача, какой результат, если нет — что помешало")
                if today_tasks:
                    hints.append(f"📅 СЕГОДНЯ ({len(today_tasks)}): {', '.join(today_tasks[:3])}")
                if tomorrow_tasks:
                    hints.append(f"🔮 ЗАВТРА ({len(tomorrow_tasks)}): {', '.join(tomorrow_tasks[:2])}")
                if future_tasks and not today_tasks and not overdue:
                    hints.append(f"📋 БУДУЩИЕ ({len(future_tasks)}): {', '.join(future_tasks[:2])}")

                # Аналитика задач
                total = len(tasks)
                hints.append(f"📊 Всего активных задач: {total}")
                if overdue and len(overdue) > 1:
                    hints.append("⚡ ДЕЙСТВИЕ: много просроченного — помоги разобраться, предложи удалить или перенести")

            # ═══ СТАТИСТИКА ЗАВЕРШЁННЫХ ЗАДАЧ ═══
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
                        except:
                            pass
                    recent_completed.append(f"✅ {ct.title}{note}")
                if recent_completed:
                    hints.append("📈 НЕДАВНО ЗАВЕРШЕНО:\n" + "\n".join(f"  {c}" for c in recent_completed))

                # Считаем completion rate
                total_all = session.query(Task).filter(Task.user_id == user.id).count()
                completed_count = len(completed_tasks)
                if total_all > 3:
                    rate = round(completed_count / total_all * 100)
                    if rate < 30:
                        hints.append(f"📉 Выполненность задач: {rate}% — задачи часто не выполняются, разберись почему")
                    elif rate > 70:
                        hints.append(f"📈 Выполненность задач: {rate}% — отличный темп!")

            if not tasks:
                # НЕТ ЗАДАЧ — фокус на текущем моменте, НЕ на завтра
                hints.append("📋 ЗАДАЧ НЕТ — спроси чем занят СЕЙЧАС, не предлагай откладывать на завтра")
                # Даём агенту контекст для НЕМЕДЛЕННОГО действия
                suggestions = []
                if profile:
                    if profile.goals:
                        suggestions.append(f"есть цель '{profile.goals[:40]}' — спроси как продвигается СЕЙЧАС, предложи конкретный шаг")
                    if profile.company:
                        suggestions.append(f"работает в {profile.company} — спроси над чем работает СЕГОДНЯ")
                    if profile.skills:
                        suggestions.append(f"навыки: {profile.skills[:40]} — чем может помочь ПРЯМО СЕЙЧАС")
                    if profile.interests and not profile.goals:
                        suggestions.append(f"интересы: {profile.interests[:40]} — обсуди актуальное по теме")
                if suggestions:
                    hints.append("⚡ ДЕЙСТВИЕ СЕЙЧАС: " + "; ".join(suggestions[:2]))
                else:
                    hints.append("⚡ ДЕЙСТВИЕ: спроси чем занят, будь полезен СЕЙЧАС — не откладывай на потом")

            # ═══ ЦЕЛИ ═══
            from models import Goal
            active_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active'
            ).order_by(Goal.priority.desc()).limit(3).all()

            if active_goals:
                goal_lines = []
                for g in active_goals:
                    line = f"{g.title} ({g.progress_percentage}%)"
                    if g.target_date:
                        days = g.days_until_target()
                        if days is not None and days < 0:
                            line += " ПРОСРОЧЕНО"
                        elif days is not None and days <= 7:
                            line += f" осталось {days}дн"
                    goal_lines.append(line)
                hints.append("🎯 Цели: " + "; ".join(goal_lines))
                # Связь задач и целей
                if not tasks:
                    hints.append("⚠️ Есть цели, но нет задач — предложи декомпозировать цель на шаги")
            else:
                hints.append("🎯 Целей нет")
                if profile and profile.goals:
                    hints.append(f"⚡ ДЕЙСТВИЕ: в профиле указана цель '{profile.goals[:50]}' — предложи создать через create_goal")

            # ═══ СИТУАЦИОННЫЙ АНАЛИЗ ═══
            # Агент должен понимать полную картину
            situation_parts = []
            has_profile = len(profile_missing) <= 2
            has_tasks = bool(tasks)
            has_goals = bool(active_goals)

            if has_profile and not has_tasks and not has_goals:
                situation_parts.append("Профиль заполнен, но нет задач/целей — спроси над чем работает СЕЙЧАС, помоги в текущем моменте")
            elif has_profile and has_goals and not has_tasks:
                situation_parts.append("Цели есть, задач нет — спроси как продвигается цель, предложи шаг на СЕГОДНЯ")
            elif has_profile and has_tasks and not has_goals:
                situation_parts.append("Задачи есть, целей нет — предложи объединить задачи в цель")
            elif not has_profile:
                situation_parts.append("Профиль не заполнен — узнай о человеке, но не навязывай заполнение")

            if situation_parts:
                hints.append("📍 СИТУАЦИЯ: " + "; ".join(situation_parts))

            # ═══ КОНТАКТЫ ═══
            real_contacts = []
            if profile and len(profile_missing) <= 2:
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
                hints.append("🤝 КОНТАКТЫ В СЕТИ:\n" + "\n".join(f"  {c}" for c in real_contacts))
            else:
                hints.append("🤝 Контактов пока нет")

            # ═══ ВРЕМЯ СУТОК ═══
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            hints.append(f"⏰ {time_label}")

            # PREMIUM АЛЕРТЫ
            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                hints.extend(alert_hints[:2])

            if hints:
                return "\n\nФОКУС:\n" + "\n".join(hints)

            return ""

        except Exception as e:
            logger.error(f"[PROACTIVE] Error: {e}")
            return ""


# Глобальный экземпляр
context_builder = ContextBuilder()