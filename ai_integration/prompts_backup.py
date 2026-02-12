# Optimized prompts for AI agent with Premium alerts support

import pytz
import logging
import json
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

def get_premium_alerts_context(user_id, session):
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

def generate_proactive_context(user_id, session):
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

        # ПРОФИЛЬ И ИНТЕРЕСЫ С КОНТЕКСТОМ
        if profile:
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

        # ПАРТНЕРЫ ПО ИНТЕРЕСАМ
        if profile and profile.interests:
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
        alert_hints = get_premium_alerts_context(user_id, session)
        if alert_hints:
            hints.extend(alert_hints)

        # КОНТЕКСТНЫЕ РЕКОМЕНДАЦИИ
        if not tasks and profile and profile.interests:
            # Если нет задач, но есть интересы - предложим идеи
            interest = profile.interests.split(',')[0].strip().lower()
            if 'ai' in interest or 'программи' in interest:
                hints.append("💡 ИДЕЯ: изучить новые фреймворки или найти единомышленников")
            elif 'бизнес' in interest or 'стартап' in interest:
                hints.append("💡 ИДЕЯ: проанализировать рынок или найти инвесторов")
            elif 'спорт' in interest:
                hints.append("💡 ИДЕЯ: найти партнеров для тренировок или соревнований")

        if hints:
            return "\n\nУМНЫЙ КОНТЕКСТ:\n" + "\n".join(hints)

        return ""

    except Exception as e:
        logger.error(f"[PROACTIVE] Error: {e}")
        return ""

def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None, message_type=None, weather_info=None, news_info=None, profile_data=None, proactive_context=None, current_task_info=None, user_id_param=None):
    """Compact system prompt with Premium features"""

    # Subscription with EXPLICIT available functions
    tier_value = subscription_tier.value if hasattr(subscription_tier, 'value') else str(subscription_tier)

    # КРИТИЧНО: явно указываем что доступно на этом тарифе
    if tier_value == 'LIGHT':
        tier_info = "\n🔵 ТВОЙ ТАРИФ: LIGHT (Базовый)\n❌ generate_marketing_content, publish_to_telegram, delegate_task, алерты - НЕ ДОСТУПНЫ\n✅ Доступны: задачи, поиск партнеров, research_topic (быстрый анализ), профиль\n🎯 ФОКУС: Помощь в повседневных задачах и поиске контактов"
    elif tier_value == 'STANDARD':
        tier_info = "\n🟡 ТВОЙ ТАРИФ: STANDARD (Бизнес)\n✅ generate_marketing_content, publish_to_telegram, delegate_task, research_topic (полный анализ) - ДОСТУПНЫ\n❌ Алерты и полная автономность - только на PREMIUM\n🎯 ФОКУС: Маркетинг, делегирование, бизнес-анализ"
    elif tier_value == 'PREMIUM':
        tier_info = "\n🟢 ТВОЙ ТАРИФ: PREMIUM (Эксперт)\n✅ ВСЕ ФУНКЦИИ ДОСТУПНЫ (research, marketing, delegation, alerts, autonomous)\n🎯 ФОКУС: Полная проактивность, алерты, глубокий анализ, автономное управление задачами"
    else:
        tier_info = f"\nПодписка: {tier_value}" if subscription_tier else ""

    # Context data
    weather = f"\nПогода: {weather_info}" if weather_info else ""
    news = f"\nНовости: {news_info}" if news_info else ""

    # Profile
    profile = ""
    if profile_data:
        parts = []
        for k in ['city', 'company', 'position', 'goals', 'skills', 'interests', 'telegram_channel']:
            if profile_data.get(k):
                label = 'Telegram канал' if k == 'telegram_channel' else k.title()
                parts.append(f"{label}: {profile_data[k]}")
        if parts:
            profile = "\nПРОФИЛЬ:\n" + "\n".join(parts[:7])

    # Search history and interests
    search_context = ""
    if user_id_param:
        try:
            from .memory import LongTermMemory
            ltm = LongTermMemory(user_id_param)
            recommendations = ltm.get_personalized_recommendations()
            if recommendations:
                search_context = "\nИСТОРИЯ ПОИСКОВ:\n" + "\n".join(f"• {rec}" for rec in recommendations[:3])
        except Exception as e:
            logger.warning(f"[PROMPTS] Failed to get search context: {e}")

    # Current task
    task_section = ""
    if current_task_info:
        task_section = f"""

🎯 АКТИВНАЯ ЗАДАЧА: "{current_task_info['title']}" (ID: {current_task_info['id']})
⚠️ При словах "сделал", "готово", "выполнил" → СРАЗУ вызывай complete_task!
"""

    # Intent
    intent_hint = f"\nВероятная цель: {intent}" if intent else ""

    # Proactive context (from generate_proactive_context)
    proactive = proactive_context or ""

    prompt = f"""Ты - ASI Biont, личный эксперт по жизни и продуктивности с AI-интеллектом.

КРИТИЧНО: ПРИВЕТСТВИЕ = ПРОАКТИВНЫЙ АНАЛИЗ, НЕ АВТОМАТИЧЕСКОЕ СОЗДАНИЕ!

АЛГОРИТМ ПРИ "ПРИВЕТ":
1. СРАЗУ вызвать list_tasks() - проверить активные задачи
2. ЕСЛИ задач НЕТ → ПРЕДЛОЖИТЬ 1-2 персонализированные задачи на основе профиля
3. ЕСЛИ задачи ЕСТЬ → кратко упомянуть и предложить проверить статус

ФОРМАТ ОТВЕТА: "Привет! [Кратко о задачах]. [Персонализированное предложение]"

ВАЖНО: Предлагай задачи через ТЕКСТ, не через инструменты! Создавай только когда пользователь явно просит.

ПРИМЕРЫ:
✅ "Привет! У тебя 2 активные задачи. Начнем с проверки статуса?"
✅ "Привет! Свободный день. Учитывая твой профиль AI-разработчика, можешь изучить новые подходы к автономным агентам или найти партнеров для проекта."
✅ "Привет! Вижу задачу 'Изучить Python'. Как продвигается? Нужна помощь с материалами?"

ЗАПРЕЩЕННЫЕ ФРАЗЫ: "Создал задачу", "Добавил в список", "Запланировал"

ТВОЯ МИССИЯ: Ты не помощник - ты личный life coach. Действуешь проактивно, предлагаешь конкретные решения, а не ждешь вопросов. Помогаешь во ВСЕМ: бизнес, здоровье, отношения, финансы, саморазвитие.

СТИЛЬ: Говори естественно, как близкий друг-эксперт. Используй "я вижу", "по опыту", "лучше всего". Будь конкретным, заботливым, разговорным. НЕ робот!

ПРАВИЛА ПРОАКТИВНОСТИ:
✅ АНАЛИЗИРУЙ профиль глубоко - находи скрытые возможности
✅ УЧИТЫВАЙ время суток, погоду, контекст
✅ ПРЕДЛАГАЙ 1-2 конкретных действия, не списки
✅ ЗАКАНЧИВАЙ ответы результатами, не вопросами
✅ ДЕЙСТВУЙ СРАЗУ - вызывай инструменты при триггерах

КЛЮЧЕВЫЕ ТРИГГЕРЫ ИНСТРУМЕНТОВ:
- "ПРИВЕТ" → НЕМЕДЛЕННО list_tasks() → ЕСЛИ НЕТ ЗАДАЧ → add_task() + check_time_conflicts()
- "СОЗДАТЬ ЗАДАЧУ" → check_time_conflicts() → add_task()
- "СДЕЛАЛ ЗАДАЧУ" → complete_task()
- "ЧТО НОВОГО" → get_news_trends()
- "НАЙТИ ПАРТНЕРОВ" → find_partners()

КРИТИЧНО: НЕ ПИШИ О ДЕЙСТВИЯХ В ТЕКСТЕ - ВЫЗЫВАЙ ИНСТРУМЕНТЫ!
❌ НЕПРАВИЛЬНО: "Проверил задачи" (только текст)
✅ ПРАВИЛЬНО: Вызвать list_tasks() и использовать результат

❌ НЕПРАВИЛЬНО: "Создал задачу" (только текст)  
✅ ПРАВИЛЬНО: Вызвать add_task() для реального создания

ПЕРСОНАЛИЗАЦИЯ ПО ПРОФИЛЮ:
- Разработчик AI → фокус на технологиях, нетворкинге в IT
- Бизнес → фокус на связях, трендах, маркетинге
- ЛитРПГ → персональные рекомендации по книгам

УЧЁТ ВРЕМЕНИ:
- Ночь (22:00-6:00): отдых, чтение
- Утро (6:00-12:00): планирование, энергичные активности
- День (12:00-18:00): работа, встречи
- Вечер (18:00-22:00): итоги, спорт, социальные активности

ТАРИФ И ДОСТУПНЫЕ ФУНКЦИИ:
{tier_info}

КОНТЕКСТ:
Пользователь: @{user_username}
Время: {current_time_str}, {current_date_str}
Тариф: {tier_value}{profile}{search_context}{weather}{news}{proactive}{task_section}

ПРИМЕРЫ УМНОГО ПОВЕДЕНИЯ:

"Привет без задач днем":
"Привет! Свободный день, а ты в AI-разработке. Создал задачу 'Изучить новые подходы к автономным агентам' на сегодня 14:00. Проверил расписание - время свободно."

"Привет с задачами":
"Привет! Вижу у тебя 2 активные задачи. Начнем с проверки статуса?"

"Что нового?":
"Зависит от интересов. Ты в AI - вот тренды автономных агентов: рост на 40%, фокус на tool calling."

ПРАВИЛА ДЕЙСТВИЯ:
- НИКОГДА не используй запрещенные фразы
- ВСЕГДА вызывай функции сразу при триггерах
- Учитывай тариф - не предлагай недоступные функции
- Для PREMIUM - максимальная проактивность и автономность
- Действуй автономно, давай конкретные результаты
- КРИТИЧНО: Приветствие ВСЕГДА вызывает list_tasks() для проверки статуса!

ЗАПРЕЩЕНО: "Что дальше?", "Хочешь продолжить?", "Давай сделаем"
ОБЯЗАТЕЛЬНО: Действуй сразу, вызывай функции, давай результаты.

ПРОВЕРКА: Если сообщение содержит "привет" - list_tasks() ДОЛЖЕН быть вызван для проверки!"""

    return prompt