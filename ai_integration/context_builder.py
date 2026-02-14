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
        """УМНЫЙ ПРОАКТИВНЫЙ КОНТЕКСТ: время, задачи, интересы, погода, паттерны поведения"""
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

            hints = []

            # АНАЛИЗ ВРЕМЕНИ СУТОК С КОНТЕКСТОМ
            hour = user_now.hour
            if 6 <= hour < 12:
                time_context = "🌅 Утро - время планирования и энергичных активностей"
            elif 12 <= hour < 18:
                time_context = "🌞 День - продуктивное время для работы и встреч"
            elif 18 <= hour < 23:
                time_context = "🌆 Вечер - время отдыха, анализа дня, социальных активностей"
            else:
                time_context = "🌙 Ночь - время отдыха и подготовки ко сну"

            hints.append(time_context)

            # ПОГОДА (если доступна)
            weather_hint = ""
            if profile and profile.city:
                try:
                    from .utils import get_weather_info
                    weather = get_weather_info(profile.city)
                    if weather:
                        weather_hint = f"🌤️ {weather}"
                        hints.append(weather_hint)
                except:
                    pass

            # АНАЛИЗ ЗАДАЧ С ПАТТЕРНАМИ
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'active', 'in_progress'])
            ).order_by(Task.reminder_time.asc()).limit(10).all()

            total_tasks = 0
            overdue, today, upcoming = [], [], []

            if tasks:
                total_tasks = len(tasks)

                for t in tasks:
                    if t.reminder_time:
                        try:
                            dt = t.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                            if dt < user_now:
                                desc = f" — {t.description[:60]}" if t.description else ""
                                overdue.append(f"{t.title}{desc}")
                            elif dt.date() == user_now.date():
                                desc = f" — {t.description[:60]}" if t.description else ""
                                today.append(f"{t.title} ({dt.strftime('%H:%M')}){desc}")
                            elif dt.date() == (user_now.date() + timedelta(days=1)):
                                desc = f" — {t.description[:60]}" if t.description else ""
                                upcoming.append(f"{t.title}{desc}")
                        except:
                            pass

                if overdue:
                    hints.append(f"⚠️ ПРОСРОЧЕНО: {', '.join(overdue[:3])}")
                if today:
                    hints.append(f"📅 СЕГОДНЯ: {', '.join(today[:3])}")
                if upcoming:
                    hints.append(f"🔮 ЗАВТРА: {', '.join(upcoming[:2])}")

                # АНАЛИЗ ПАТТЕРНОВ ПРОДУКТИВНОСТИ
                if total_tasks > 5:
                    hints.append(f"📊 Много задач ({total_tasks}) - фокус на приоритетах")
                elif total_tasks == 0:
                    hints.append("✅ Нет активных задач - время для новых инициатив")

            # ПРОФИЛЬ И ИНТЕРЕСЫ С КОНТЕКСТОМ (только при полном профиле)
            if profile and profile_complete:
                if profile.interests:
                    interests = [i.strip() for i in profile.interests.split(',')[:3]]
                    hints.append(f"💡 ИНТЕРЕСЫ: {', '.join(interests)}")

                if profile.goals:
                    goals = [g.strip() for g in profile.goals.split(',')[:2]]
                    hints.append(f"🎯 ЦЕЛИ: {', '.join(goals)}")

                if profile.skills:
                    skills = [s.strip() for s in profile.skills.split(',')[:2]]
                    hints.append(f"🛠️ НАВЫКИ: {', '.join(skills)}")

                if profile.company:
                    hints.append(f"🏢 РАБОТА: {profile.company}")

                if profile.position:
                    hints.append(f"👔 ДОЛЖНОСТЬ: {profile.position}")
                
                # Статистика продуктивности
                stats_parts = []
                if profile.total_tasks_created:
                    stats_parts.append(f"создано: {profile.total_tasks_created}")
                if profile.completed_tasks:
                    stats_parts.append(f"завершено: {profile.completed_tasks}")
                if profile.skipped_tasks:
                    stats_parts.append(f"пропущено: {profile.skipped_tasks}")
                if profile.average_completion_time:
                    stats_parts.append(f"ср. время: {profile.average_completion_time}")
                if stats_parts:
                    hints.append(f"📊 СТАТИСТИКА: {', '.join(stats_parts)}")

            # Долгосрочная память (интересы, проекты, поиски)
            if user.long_term_memory:
                try:
                    ltm = json.loads(user.long_term_memory)
                    interests = ltm.get('interests', {})
                    if interests:
                        sorted_interests = sorted(interests.items(), key=lambda x: x[1], reverse=True)[:5]
                        hints.append(f"🎯 УСТОЙЧИВЫЕ ИНТЕРЕСЫ: {', '.join(f'{t}({c})' for t, c in sorted_interests)}")
                    searches = ltm.get('search_history', [])
                    if searches:
                        recent_q = [s['query'] for s in searches[-3:]]
                        hints.append(f"🔍 НЕДАВНИЕ ПОИСКИ: {', '.join(recent_q)}")
                    projects = ltm.get('projects', {})
                    if projects:
                        hints.append(f"📁 ПРОЕКТЫ: {', '.join(list(projects.keys())[-3:])}")
                except Exception:
                    pass

            # ЦЕЛИ пользователя (из таблицы Goal)
            from models import Goal
            active_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active'
            ).order_by(Goal.priority.desc()).limit(5).all()
            if active_goals:
                goal_lines = []
                for g in active_goals:
                    line = f"{g.title} ({g.progress_percentage}%)"
                    if g.target_date:
                        days = g.days_until_target()
                        if days is not None and days < 0:
                            line += " ⚠️просроч"
                        elif days is not None and days <= 7:
                            line += f" ⏳{days}дн"
                    goal_lines.append(line)
                hints.append(f"🎯 ЦЕЛИ:\n" + "\n".join(f"  {l}" for l in goal_lines))

            # РЕАЛЬНЫЕ КОНТАКТЫ ИЗ БД (явный список с @username)
            cached_partners = None
            real_contacts = []  # Список реальных контактов для анти-галлюцинации
            if profile and profile.interests and profile_complete:
                try:
                    from .handlers import get_partners_list
                    cached_partners = get_partners_list(user.id, session)
                    partners = cached_partners
                    if partners:
                        for p in partners[:5]:
                            partner_user = session.query(User).filter_by(id=p.user_id).first()
                            if partner_user and partner_user.username:
                                # Определяем общие интересы
                                common = set()
                                if p.interests and profile.interests:
                                    partner_ints = set(i.strip().lower() for i in p.interests.split(','))
                                    user_ints = set(i.strip().lower() for i in profile.interests.split(','))
                                    common = user_ints & partner_ints
                                city = p.city or ''
                                real_contacts.append({
                                    'username': partner_user.username,
                                    'common': list(common)[:3],
                                    'city': city,
                                    'skills': (p.skills or '')[:80]
                                })
                except Exception:
                    pass

            # Формируем блок контактов — явный, чтобы AI не выдумывал несуществующих
            if real_contacts:
                contact_lines = []
                for c in real_contacts:
                    common_str = f", общее: {', '.join(c['common'])}" if c['common'] else ''
                    city_str = f", {c['city']}" if c['city'] else ''
                    contact_lines.append(f"  @{c['username']}{city_str}{common_str}")
                hints.append(f"🤝 РЕАЛЬНЫЕ КОНТАКТЫ ({len(real_contacts)}):\n" + "\n".join(contact_lines))
            else:
                hints.append("🤝 КОНТАКТЫ: пока нет подходящих людей в базе. НЕ выдумывай @username!")

            # PREMIUM АЛЕРТЫ
            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                hints.extend(alert_hints)

            # КОНТЕКСТНЫЕ ПОДСКАЗКИ (краткие, AI сам решит что предложить)
            time_labels = {(6,12): "утро", (12,18): "день", (18,23): "вечер"}
            time_label = next((v for (a,b), v in time_labels.items() if a <= hour < b), "ночь")
            hints.append(f"⏰ Время суток: {time_label}")

            # ЗАГРУЖЕННОСТЬ
            if not tasks:
                hints.append("📋 Нет активных задач — можно предложить что-то полезное")
            else:
                if overdue:
                    hints.append(f"🚨 Просроченных задач: {overdue}")
                if today:
                    hints.append(f"📅 Задач на сегодня: {today}")
                if total_tasks > 3:
                    hints.append(f"⚡ Загружен: {total_tasks} активных задач")

            # РЕЛЕВАНТНЫЕ ЗАДАЧИ ДРУГИХ ПОЛЬЗОВАТЕЛЕЙ (возможности для коллаборации)
            try:
                # Собираем расширенный набор ключевых слов пользователя
                match_keywords = set()
                if profile and profile.interests:
                    match_keywords.update(i.strip().lower() for i in profile.interests.split(',') if len(i.strip()) > 2)
                # LTM interests (топ по весу)
                try:
                    ltm_data = json.loads(user.long_term_memory) if user.long_term_memory else {}
                    for topic, weight in sorted(ltm_data.get('interests', {}).items(), key=lambda x: x[1], reverse=True)[:8]:
                        if weight >= 2 and len(topic) >= 3:
                            match_keywords.add(topic.lower().strip())
                except Exception:
                    pass
                # Goal categories
                try:
                    user_goals = session.query(Goal).filter(
                        Goal.user_id == user.id, Goal.status.in_(['active', 'in_progress'])
                    ).all()
                    for g in user_goals:
                        if g.category and len(g.category) >= 3:
                            match_keywords.add(g.category.lower().strip())
                        if g.title:
                            match_keywords.update(w.lower() for w in g.title.split() if len(w) >= 4)
                except Exception:
                    pass
                
                if match_keywords:
                    yesterday = datetime.now(timezone.utc) - timedelta(days=3)
                    other_tasks = session.query(Task).filter(
                        Task.user_id != user.id,
                        Task.created_at >= yesterday,
                        Task.status.in_(['pending', 'in_progress', 'active'])
                    ).order_by(Task.created_at.desc()).limit(20).all()
                    
                    relevant_tasks = []
                    for t in other_tasks:
                        task_text = (t.title + ' ' + (t.description or '')).lower()
                        if any(kw in task_text for kw in match_keywords):
                            task_owner = session.query(User).filter_by(id=t.user_id).first()
                            if task_owner and task_owner.username:
                                relevant_tasks.append(f"@{task_owner.username}: \"{t.title}\"")
                    if relevant_tasks:
                        hints.append(f"🔗 РЕЛЕВАНТНЫЕ ЗАДАЧИ ДРУГИХ ПОЛЬЗОВАТЕЛЕЙ:\n" + "\n".join(f"  {rt}" for rt in relevant_tasks[:3]))
            except Exception:
                pass

            if hints:
                return "\n\nУМНЫЙ КОНТЕКСТ:\n" + "\n".join(hints)

            return ""

        except Exception as e:
            logger.error(f"[PROACTIVE] Error: {e}")
            return ""


# Глобальный экземпляр
context_builder = ContextBuilder()