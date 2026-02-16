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
        """АНАЛИТИЧЕСКИЙ КОНТЕКСТ: все данные + один приоритет."""
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

            data_lines = []  # Все данные
            priority = None   # Один приоритет

            # ═══ АНАЛИЗ ПРОФИЛЯ ═══
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
                data_lines.append(f"👤 Профиль не заполнено: {', '.join(profile_missing)}")
            if profile_has:
                data_lines.append(f"👤 Профиль есть: {'; '.join(profile_has)}")

            # ═══ ЗАДАЧИ ═══
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
                    data_lines.append(f"🚨 Просрочено ({len(overdue)}): {', '.join(overdue[:3])}")
                if today_tasks:
                    data_lines.append(f"📅 Сегодня ({len(today_tasks)}): {', '.join(today_tasks[:3])}")
                if tomorrow_tasks:
                    data_lines.append(f"🔮 Завтра ({len(tomorrow_tasks)}): {', '.join(tomorrow_tasks[:2])}")
                if future_tasks and not today_tasks and not overdue:
                    data_lines.append(f"📋 Будущие ({len(future_tasks)}): {', '.join(future_tasks[:2])}")
                data_lines.append(f"📊 Активных задач: {len(tasks)}")
            else:
                data_lines.append("📋 Задач нет")

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
                data_lines.append("🎯 Цели: " + "; ".join(goal_lines))
            else:
                data_lines.append("🎯 Целей нет")

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
                data_lines.append("🤝 Контакты: " + ", ".join(real_contacts))
            else:
                data_lines.append("🤝 Контактов нет")

            # ═══ ВРЕМЯ СУТОК ═══
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            data_lines.append(f"⏰ {time_label}")

            # PREMIUM АЛЕРТЫ
            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                data_lines.extend(alert_hints[:2])

            # ═══════════════════════════════════════════
            # АНАЛИЗ ПРИОРИТЕТА — один на основе всех данных
            # Порядок приоритетов:
            # 1. Просроченные задачи (критично)
            # 2. Задачи на сегодня (актуально)
            # 3. Пустой профиль (нужно узнать человека)
            # 4. Есть цели без задач (нужна декомпозиция)
            # 5. Есть профиль без целей (предложить цель)
            # 6. Есть всё — дать ценность (тренды, инсайты)
            # 7. Ничего нет — познакомиться
            # ═══════════════════════════════════════════
            has_profile = len(profile_missing) <= 2
            has_tasks = bool(tasks)
            has_goals = bool(active_goals)

            if overdue:
                priority = f"Просрочено {len(overdue)} задач — помоги разобраться: перенести или удалить"
            elif today_tasks:
                priority = f"Сегодня {len(today_tasks)} задач — напомни, поддержи, спроси как идёт"
            elif len(profile_missing) >= 5:
                priority = "Профиль пустой — узнай о человеке через живой разговор, не допрашивай"
            elif has_goals and not has_tasks:
                goal_name = active_goals[0].title if active_goals else (profile.goals[:40] if profile and profile.goals else 'цель')
                priority = f"Есть цель '{goal_name}', но нет задач — предложи один конкретный первый шаг"
            elif not has_goals and profile and profile.goals:
                priority = f"В профиле цель '{profile.goals[:40]}', но не оформлена — предложи create_goal при случае"
            elif has_profile and not has_tasks and not has_goals:
                # Профиль есть, но ни задач, ни целей
                if profile and profile.interests:
                    priority = f"Человек вовлечён слабо — дай ценность по интересам ({profile.interests[:30]}), предложи задачу по ходу разговора"
                elif profile and profile.company:
                    priority = f"Человек вовлечён слабо — спроси как дела в {profile.company}, предложи задачу по ходу"
                else:
                    priority = "Человек вовлечён слабо — дай ценность, предложи задачу по ходу разговора"
            elif has_profile and has_tasks and has_goals:
                priority = "Всё на месте — поддерживай, следи за прогрессом, давай инсайты"
            elif has_profile and has_tasks and not has_goals:
                priority = "Задачи есть, целей нет — при случае предложи объединить в цель"
            else:
                priority = "Познакомься с человеком через разговор"

            # Собираем итоговый контекст
            result = "\n\nФОКУС:"
            result += f"\n🎯 ПРИОРИТЕТ: {priority}"
            result += "\n" + "\n".join(data_lines)

            return result

        except Exception as e:
            logger.error(f"[PROACTIVE] Error: {e}")
            return ""


# Глобальный экземпляр
context_builder = ContextBuilder()