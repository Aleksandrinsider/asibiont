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

            if tasks:
                overdue, today, upcoming = [], [], []

                for t in tasks:
                    if t.reminder_time:
                        try:
                            dt = t.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                            if dt < user_now:
                                overdue.append(t.title)
                            elif dt.date() == user_now.date():
                                today.append(f"{t.title} ({dt.strftime('%H:%M')})")
                            elif dt.date() == (user_now.date() + timedelta(days=1)):
                                upcoming.append(t.title)
                        except:
                            pass

                if overdue:
                    hints.append(f"⚠️ ПРОСРОЧЕНО: {', '.join(overdue[:3])}")
                if today:
                    hints.append(f"📅 СЕГОДНЯ: {', '.join(today[:3])}")
                if upcoming:
                    hints.append(f"🔮 ЗАВТРА: {', '.join(upcoming[:2])}")

                # АНАЛИЗ ПАТТЕРНОВ ПРОДУКТИВНОСТИ
                total_tasks = len(tasks)
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

            # ПАРТНЕРЫ ПО ИНТЕРЕСАМ (только при полном профиле)
            if profile and profile.interests and profile_complete:
                try:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user.id, session)
                    if partners:
                        # Найдем общие интересы
                        common_interests = set()
                        for p in partners[:3]:
                            if p.interests:
                                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                                user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                                common = user_interests & partner_interests
                                common_interests.update(common)

                        if common_interests:
                            hints.append(f"🤝 ПАРТНЕРЫ: общие интересы в {', '.join(list(common_interests)[:2])}")
                        else:
                            hints.append(f"🤝 ДОСТУПНО {len(partners)} партнеров")
                except:
                    pass

            # PREMIUM АЛЕРТЫ
            alert_hints = self.build_premium_alerts_context(user_id, session)
            if alert_hints:
                hints.extend(alert_hints)

            # КОНТЕКСТНЫЕ РЕКОМЕНДАЦИИ - УНИВЕРСАЛЬНЫЙ АНАЛИЗ
            if profile and profile.interests:
                interests = [i.strip().lower() for i in profile.interests.split(',')]

                # АНАЛИЗ ПО ВРЕМЕНИ СУТОК + ИНТЕРЕСЫ
                if hour >= 6 and hour < 12:  # Утро
                    if any(i in ['спорт', 'здоровье', 'fitness'] for i in interests):
                        hints.append("🌅 УТРО: отличное время для тренировки или прогулки на свежем воздухе")
                    elif any(i in ['работа', 'бизнес', 'startup'] for i in interests):
                        hints.append("🌅 УТРО: продуктивное время для планирования дня и стратегических задач")
                    elif any(i in ['учеба', 'ai', 'программирование'] for i in interests):
                        hints.append("🌅 УТРО: идеально для глубокого изучения и сложных задач")

                elif hour >= 12 and hour < 18:  # День
                    if any(i in ['встречи', 'нетворкинг', 'бизнес'] for i in interests):
                        hints.append("🌞 ДЕНЬ: время для встреч, звонков и нетворкинга")
                    elif any(i in ['творчество', 'искусство', 'дизайн'] for i in interests):
                        hints.append("🌞 ДЕНЬ: пик креативности - время для творческих задач")

                elif hour >= 18 and hour < 23:  # Вечер
                    if any(i in ['отдых', 'семья', 'друзья'] for i in interests):
                        hints.append("🌆 ВЕЧЕР: время для общения, хобби и отдыха")
                    elif any(i in ['чтение', 'саморазвитие', 'учеба'] for i in interests):
                        hints.append("🌆 ВЕЧЕР: спокойное время для чтения и саморазвития")

                else:  # Ночь
                    if any(i in ['сон', 'здоровье', 'медитация'] for i in interests):
                        hints.append("🌙 НОЧЬ: время отдыха и восстановления")
                    elif any(i in ['размышления', 'планирование', 'стратегия'] for i in interests):
                        hints.append("🌙 НОЧЬ: время для размышлений и долгосрочного планирования")

            # АНАЛИЗ ЗАГРУЖЕННОСТИ + РЕКОМЕНДАЦИИ
            if not tasks:
                # СВОБОДНОЕ ВРЕМЯ - АКТИВНЫЕ ПРЕДЛОЖЕНИЯ
                if profile and profile.interests:
                    interest = profile.interests.split(',')[0].strip().lower()
                    if 'ai' in interest or 'программи' in interest or 'технологии' in interest:
                        hints.append("🎯 СВОБОДНО: изучить новые AI-фреймворки или найти коллег-разработчиков")
                    elif 'бизнес' in interest or 'стартап' in interest or 'предпринимательство' in interest:
                        hints.append("🎯 СВОБОДНО: проанализировать рынок, найти партнеров или инвесторов")
                    elif 'спорт' in interest or 'здоровье' in interest:
                        hints.append("🎯 СВОБОДНО: найти партнеров для тренировок или соревнований")
                    elif 'искусство' in interest or 'творчество' in interest:
                        hints.append("🎯 СВОБОДНО: посетить выставки, найти единомышленников или поработать над проектом")
                    elif 'путешествия' in interest:
                        hints.append("🎯 СВОБОДНО: спланировать поездку или найти попутчиков")
                    else:
                        hints.append("🎯 СВОБОДНО: заняться хобби, саморазвитием или найти новых знакомых")
            else:
                # ЕСТЬ ЗАДАЧИ - ПРИОРИТЕТЫ И ОПТИМИЗАЦИЯ
                if total_tasks > 3:
                    hints.append("⚡ ЗАГРУЖЕН: фокусируйся на 1-2 приоритетных задачах, остальные отложи")
                elif overdue:
                    hints.append("🚨 ПРОСРОЧКИ: начни с самой критичной задачи, остальные перепланируй")
                elif today:
                    hints.append("📅 СЕГОДНЯ: время действовать - начни с утренней задачи")

            # ПОГОДА + АКТИВНОСТИ
            if profile and profile.city and weather_hint:
                if 'холодно' in weather_hint.lower() or 'снег' in weather_hint.lower():
                    if any(i in ['спорт', 'прогулки'] for i in interests):
                        hints.append("❄️ ПОГОДА: холодно - лучше室内 активности или онлайн-встречи")
                elif 'жарко' in weather_hint.lower() or 'солнце' in weather_hint.lower():
                    if any(i in ['спорт', 'прогулки'] for i in interests):
                        hints.append("☀️ ПОГОДА: тепло - отличное время для outdoor активностей")

            # ПАРТНЕРЫ + КОНКРЕТНЫЕ ПРЕДЛОЖЕНИЯ
            if profile and profile.interests:
                try:
                    from .handlers import get_partners_list
                    partners = get_partners_list(user_id, session)
                    if partners:
                        # Найдем общие интересы для конкретных предложений
                        common_themes = set()
                        for p in partners[:5]:
                            if p.interests:
                                partner_interests = set(i.strip().lower() for i in p.interests.split(','))
                                user_interests = set(i.strip().lower() for i in profile.interests.split(','))
                                common = user_interests & partner_interests
                                common_themes.update(common)

                        if common_themes:
                            theme = list(common_themes)[0]
                            hints.append(f"🤝 СЕТЬ: {len(partners)} человек с общими интересами в {theme}")
                            if not tasks:
                                hints.append(f"💡 ИДЕЯ: связаться с кем-то из {theme}-сообщества для совместного проекта")
                        else:
                            hints.append(f"🤝 СЕТЬ: доступно {len(partners)} потенциальных контактов")
                except:
                    pass

            if hints:
                return "\n\nУМНЫЙ КОНТЕКСТ:\n" + "\n".join(hints)

            return ""

        except Exception as e:
            logger.error(f"[PROACTIVE] Error: {e}")
            return ""


# Глобальный экземпляр
context_builder = ContextBuilder()