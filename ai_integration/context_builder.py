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
        """ФОКУСНЫЙ КОНТЕКСТ: только то, что сейчас важно. Не вываливай всё."""
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

            # ═══ ПРИОРИТЕТ 1: ПРОФИЛЬ ═══
            # Если профиль пустой — это ГЛАВНАЯ проблема. Всё остальное вторично.
            profile_fields_filled = 0
            if profile:
                for f in ['interests', 'skills', 'goals', 'city', 'company', 'position']:
                    if getattr(profile, f, None):
                        profile_fields_filled += 1

            if profile_fields_filled < 2:
                hints.append("👤 Профиль почти пустой — узнай о человеке через живой разговор.")
                if hints:
                    return "\n\nФОКУС:\n" + "\n".join(hints)

            # ═══ ПРИОРИТЕТ 2: ЗАДАЧИ ═══
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'active', 'in_progress'])
            ).order_by(Task.reminder_time.asc()).limit(10).all()

            overdue = []
            today_tasks = []
            tomorrow_tasks = []

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
                        except:
                            pass

                # Просроченные — критично
                if overdue:
                    hints.append(f"🚨 Просрочено: {', '.join(overdue[:3])}")
                if today_tasks:
                    hints.append(f"📅 Сегодня: {', '.join(today_tasks[:3])}")
                if tomorrow_tasks and not today_tasks:
                    hints.append(f"🔮 Завтра: {', '.join(tomorrow_tasks[:2])}")
            else:
                hints.append("📋 Задач нет")
                if profile and profile.interests:
                    hints.append(f"💡 Интересы: {profile.interests[:60]}")
                elif profile and profile.goals:
                    hints.append(f"💡 Цели: {profile.goals[:60]}")

            # ═══ ПРИОРИТЕТ 3: ЦЕЛИ ═══
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

            # ═══ КОНТАКТЫ (с деталями для связывания с темой разговора) ═══
            real_contacts = []
            if profile and profile_complete:
                try:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user.id, session)
                    if partners:
                        for p in partners[:5]:
                            partner_user = session.query(User).filter_by(id=p.user_id).first()
                            if partner_user and partner_user.username:
                                # Собираем детали контакта
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

            # ═══ ВРЕМЯ СУТОК (одна строка) ═══
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            hints.append(f"⏰ {time_label}")

            # PREMIUM АЛЕРТЫ (если есть)
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