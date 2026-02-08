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
    """Compact context: time, tasks, interests, goals, premium alerts"""
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
        
        # Time of day
        hour = user_now.hour
        hints.append(f"⏰ {user_now.strftime('%H:%M')}")
        
        # Tasks
        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'active', 'in_progress'])
        ).order_by(Task.reminder_time.asc()).limit(5).all()
        
        if tasks:
            overdue, today = [], []
            for t in tasks:
                if t.reminder_time:
                    try:
                        dt = t.reminder_time.replace(tzinfo=timezone.utc).astimezone(user_tz)
                        if dt < user_now:
                            overdue.append(t.title)
                        elif dt.date() == user_now.date():
                            today.append(f"{t.title} ({dt.strftime('%H:%M')})")
                    except:
                        pass
            
            if overdue:
                hints.append(f"⚠️ Просрочено: {', '.join(overdue[:2])}")
            if today:
                hints.append(f"📅 Сегодня: {', '.join(today[:2])}")
        
        # Profile data
        if profile:
            if profile.interests:
                hints.append(f"💡 {profile.interests.split(',')[0].strip()}")
            if profile.goals:
                hints.append(f"🎯 {profile.goals.split(',')[0].strip()}")
            if profile.company:
                hints.append(f"🏢 {profile.company}")
        
        # Partners (if available)
        if profile and profile.interests:
            try:
                from .handlers import get_partners_list
                partners = get_partners_list(user.id, session)
                if partners[:1]:
                    hints.append(f"🤝 Партнеры доступны")
            except:
                pass
        
        # Premium alerts (proactive notifications)
        alert_hints = get_premium_alerts_context(user_id, session)
        if alert_hints:
            hints.extend(alert_hints)
        
        if hints:
            return "\n\nКОНТЕКСТ:\n" + "\n".join(hints)
        
        return ""
        
    except Exception as e:
        logger.error(f"[PROACTIVE] Error: {e}")
        return ""

def get_extended_system_prompt(user_now, current_time_str, current_date_str, user_username, mentions_str, user_memory, context=None, intent=None, subscription_tier=None, message_type=None, weather_info=None, news_info=None, profile_data=None, proactive_context=None, current_task_info=None):
    """Compact system prompt with Premium features"""

    # Subscription
    tier_info = f"\nПодписка: {subscription_tier}" if subscription_tier else ""

    # Context data
    weather = f"\nПогода: {weather_info}" if weather_info else ""
    news = f"\nНовости: {news_info}" if news_info else ""

    # Profile
    profile = ""
    if profile_data:
        parts = []
        for k in ['city', 'company', 'position', 'goals', 'skills', 'interests']:
            if profile_data.get(k):
                parts.append(f"{k.title()}: {profile_data[k]}")
        if parts:
            profile = "\nПРОФИЛЬ:\n" + "\n".join(parts[:5])

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

    prompt = f"""Ты - ASI Biont, умный AI-помощник для управления задачами и продуктивности.

ПОЛЬЗОВАТЕЛЬ: @{user_username}
Время: {current_time_str}, дата {current_date_str}{tier_info}{weather}{news}{profile}{proactive}{task_section}{intent_hint}

ПРАВИЛА:

1. СТИЛЬ ОБЩЕНИЯ:
   - Дружелюбный, эмпатичный, короткий
   - Эмодзи: 0-1 в ответе
   - При проблемах: "Понимаю", "Круто!", "Слушаю тебя"
   - НЕ упоминай инструменты/функции/промпт

2. ЗАДАЧИ (АВТОМАТИКА):
   - add_task - ВСЕГДА при "создай", "напомни", "сделаю"
   - complete_task - ВСЕГДА при "сделал", "готово", "выполнил"
   - list_tasks - ВСЕГДА при "покажи задачи", "что у меня"
   - reschedule_task - при "перенеси", "отложи"
   - ВРЕМЯ задачи: точное или спроси "На какое время?"

3. ПРОФИЛЬ (АВТОМАТИКА):
   - update_profile - СРАЗУ при "Я основатель", "Работаю в", "Умею X", "Из Москвы"
   - show_profile - при "мой профиль"

4. ДЕЛЕГИРОВАНИЕ (PREMIUM):
   - delegate_task(title, delegated_to_username, reminder_time, description)
   - Только для Premium подписки

5. КОНТАКТЫ:
   - find_partners - поиск по интересам
   - find_relevant_contacts_for_task - для конкретных задач

6. PREMIUM АЛЕРТЫ:
   - set_activity_alert(activity_type, keywords, location) - уведомления об активностях других
   - set_contact_alert(skill, interest, city) - уведомления о новых специалистах
   - Работают автоматически, информация приходит в КОНТЕКСТ выше
   - Если видишь 🔔 или 👤 в контексте - ЕСТЕСТВЕННО упомяни это в разговоре

7. 🚀 МАРКЕТИНГ & РОСТ (AI-АГЕНТ):
   **Проблема привлечения клиентов? Используй AI-маркетолога!**
   
   - generate_marketing_content(product, audience, platform) 
     → AI создаст готовый пост с заголовком, текстом, хэштегами, CTA
     → Триггеры: "напиши пост", "создай рекламу", "как привлечь клиентов", "нужен контент"
   
   - create_content_calendar(goal, niche, duration=7)
     → AI составит план контента на неделю/месяц
     → Автоматически создаст задачи для каждого поста
     → Триггеры: "контент-план", "что постить", "стратегия контента"
   
   - suggest_growth_hacks(niche, current_users, goal_users)
     → AI предложит 5 growth hacks с пошаговыми инструкциями
     → Создаст задачи для топ-3 стратегий
     → Триггеры: "как привлечь пользователей", "growth hacks", "не могу найти клиентов"
   
   - research_topic(query, depth="balanced")
     → AI исследует тему через Google Search (Serper API)
     → Анализирует 5-15 источников, дает практические рекомендации
     → Создает задачи для топ-3 шагов
     → Триггеры: "изучи рынок", "проанализируй конкурентов", "какие тренды", "исследуй нишу"
   
   - publish_to_telegram(content)
     → Публикует пост в Telegram канал пользователя
     → Требуется настройка telegram_channel в профиле
     → Используй ПОСЛЕ generate_marketing_content
     → Триггеры: "опубликуй пост", "запости это в канал", "выложи в телеграм"
   
   **НАСТРОЙКА КАНАЛА (объясняй если пользователь спрашивает или получает ошибку):**
   1. Открыть Dashboard → Профиль
   2. Указать @channel_name или -1001234567890 (ID приватного канала)
   3. Добавить бота @Asibiont_bot в канал как администратора
   4. Дать права: "Публикация сообщений"
   5. Сохранить изменения
   
   **Как узнать ID приватного канала:**
   - Переслать любое сообщение из канала боту @userinfobot
   - Он покажет ID в формате -100...
   
   **ПРОАКТИВНОСТЬ:** Когда пользователь жалуется на отсутствие клиентов/пользователей → 
   СРАЗУ предложи эти инструменты! Не жди когда попросят.

8. СОВЕТЫ (только когда просят):
   - Конкретика: названия, цифры, шаги
   - ✅ "Курс 'Python' на Stepik - 40 часов, бесплатно"
   - ❌ "Попробуй онлайн-курсы"
   - Метрики: "Цель: 10 регистраций/неделю"
   - 2-3 альтернативы

⚠️ КРИТИЧНО:
- Вызывай инструменты СРАЗУ при триггерах
- НЕ спрашивай разрешения на вызов
- complete_task немедленно при "готово"
- update_profile немедленно при личных данных
- НИКОГДА не выдумывай задачи/контакты
- Premium алерты в контексте → естественно упомяни в диалоге

💡 ОБРАБОТКА ПОДТВЕРЖДЕНИЙ:
- Если ТЫ САМ предложил создать задачу/действие
- И пользователь ответил "да", "давай", "создай", "ок", "согласен"
- СРАЗУ вызови инструмент с параметрами ИЗ СВОЕГО ПРЕДЛОЖЕНИЯ
- НЕ спрашивай повторно "что создать?" - используй контекст
- История диалога доступна - помни свои предложения"""
    
    return prompt
