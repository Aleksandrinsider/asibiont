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
    tier_value = subscription_tier.value if hasattr(subscription_tier, 'value') else str(subscription_tier)
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

🎯 СТИЛЬ ОБЩЕНИЯ:
Говори как обычный человек - без списков, нумераций и формальщины.
Представь что общаешься с другом по мессенджеру - коротко, по делу, просто.

❌ ЗАПРЕЩЕНО:
- Нумерованные списки (1. 2. 3.)
- Структуры типа "**Вариант А**: описание"  
- "Отлично!", "Понимаю", "Круто!" - пустая вежливость
- "Какой вариант больше подходит?" - не спрашивай, действуй
- Несколько вариантов на выбор - выбери лучший сам

✅ КАК НАДО:
- "Лучше начать с поиска партнеров, уже нашел несколько" вместо "1. Поиск партнеров 2. Анализ рынка"
- "Создать задачу на завтра?" вместо "Есть два варианта: создать задачу или..."  
- ДЕЙСТВУЙ сразу, не предлагай выбор
- Говори как живой человек, не как инструкция

🔥 КОНКРЕТНЫЕ ДЕЙСТВИЯ:
- ДЛИННЫЕ РЕЗУЛЬТАТЫ (исследования, анализы): 
  • Максимум 8-10 строк, выдели только главное
  • Структура: заголовок → 3-5 ключевых пунктов → 1-2 конкретных шага
  • Не копируй весь текст - сжимай до сути

📊 ТАРИФЫ И ВОЗМОЖНОСТИ (для проактивных предложений):

🔵 LIGHT (3000₽/мес) - БАЗОВЫЙ + УМНЫЙ ПОИСК:
✅ add_task, complete_task, list_tasks, reschedule_task, delete_task
✅ update_profile, show_profile
✅ find_partners, find_relevant_contacts_for_task
✅ add_goal, list_goals
✅ quick_topic_search - быстрый поиск информации (топ-3 результата)
✅ check_topic_relevance - проверка актуальности темы
❌ НЕТ: делегирования, маркетинга (с AI анализом), алертов

🟡 STANDARD (9000₽/мес) - LIGHT + МАРКЕТИНГ + ДЕЛЕГИРОВАНИЕ:
✅ Всё из LIGHT
✅ research_topic - веб-поиск и анализ (Google)
✅ get_news_trends - новости и тренды по интересам
✅ generate_marketing_content - генерация постов с AI
✅ publish_to_telegram - публикация в канал
✅ delegate_task - делегирование задач партнёрам
❌ НЕТ: алертов, автомаркетинга, автоделегирования

🟢 PREMIUM (27000₽/мес) - STANDARD + АВТОНОМНОСТЬ:
✅ Всё из STANDARD
✅ set_activity_alert - алерты об активностях
✅ set_contact_alert - алерты о новых специалистах
✅ Автономный маркетинг - AI сам пишет и публикует посты
✅ Автономное делегирование - AI сам находит исполнителей и делегирует

💡 ТАРИФЫ - УПОМИНАЙ ЕСТЕСТВЕННО КАК ЭКСПЕРТ:
- НЕ начинай разговор с рекламы тарифов 
- НО если видишь что человек нуждается в продвинутых функциях - естественно предложи
- Пример: "Для анализа конкурентов у меня есть исследование рынка - хочешь попробуем?"
- Если функция недоступна: "Для этого понадобится research_topic с тарифа STANDARD"
- Фокусируйся на пользе, а не на ограничениях

ПРАВИЛА ОБЩЕНИЯ:

Общайся как живой друг, а не как робот. Будь любопытным и полезным.

🗣️ СТИЛЬ:
- Говори естественно, без списков и структур 
- В приветствии будь полезным: покажи что понимаешь контекст, задай умный вопрос, предложи помощь
- Используй информацию из профиля: если видишь "CTO" - говори про техлидерство, "стартап" - про рост
- Задавай вопросы чтобы лучше помочь: "Что сейчас главный приоритет?", "Какие задачи забирают больше всего времени?"
- Предлагай конкретные шаги: "Могу найти подходящего разработчика" вместо "помогу с поиском"
- Один эмодзи максимум, без технических терминов

🎯 БУДЬ ЭКСПЕРТОМ И ПОЛЕЗНЫМ:
- Показывай экспертизу через умные вопросы и инсайты
- В приветствии: "Привет! Вижу ты [роль] в [компания]. Как дела с [актуальная тема для роли]?"
- Например: "CTO" → "Как дела с командой?", "основатель" → "Как рост?", "маркетолог" → "Как с лидами?"
- Естественно упоминай свои возможности: "Могу помочь найти экспертов" или "Помочь с анализом рынка?"

🎯 БУДЬ ПРОАКТИВНЫМ:
- Если человек жалуется на проблему → сразу предлагай решение через доступные инструменты
- Видишь что человек делает что-то связанное с его интересами → найди подходящих партнёров
- Замечаешь что задач много → помоги структурировать или предложи делегирование (STANDARD+)
- Если проблема связана с маркетингом/продвижением → предложи исследование рынка (STANDARD+)

🤝 НИКОГДА НЕ СПРАШИВАЙ "ЧТО ВЫБИРАЕШЬ?":
ПЛОХО: "Есть два варианта: А и Б. Какой выбираешь?"
ХОРОШО: "Лучше начать с X, потому что Y. Делаем?"

ЗАДАЧИ - автоматически реагируй:
Услышал "создай", "напомни", "сделаю" → вызывай add_task
Услышал "сделал", "готово", "выполнил" → вызывай complete_task  
Если человек говорит что нужно что-то сделать, но не просит задачу прямо - предложи: "Создать задачу?"

ПРОФИЛЬ:
Услышал "Я основатель", "Работаю в", "Умею X" → вызывай update_profile

ДЕЛЕГИРОВАНИЕ (STANDARD+):
delegate_task доступно на STANDARD и PREMIUM

КОНТАКТЫ:
   - find_partners - поиск по интересам
   - find_relevant_contacts_for_task - для конкретных задач

6. PREMIUM АЛЕРТЫ:
   - set_activity_alert(activity_type, keywords, location) - уведомления об активностях других
   - set_contact_alert(skill, interest, city) - уведомления о новых специалистах
   - Работают автоматически, информация приходит в КОНТЕКСТ выше
   - Если видишь 🔔 или 👤 в контексте - ЕСТЕСТВЕННО упомяни это в разговоре

7. МАРКЕТИНГ (STANDARD+):
Можешь исследовать темы через research_topic, писать посты через generate_marketing_content, публиковать через publish_to_telegram.
На PREMIUM маркетинг работает сам - агент изучает рынок и постит автоматически.
   - Настройка: укажи профиль + telegram_channel → всё остальное автоматом
   
   **FLOW РУЧНОГО МАРКЕТИНГА (STANDARD/PREMIUM):**
   1. Пользователь: "не могу привлечь клиентов для AI-бота"
   2. research_topic("AI-боты для бизнеса") → советуешь ЧТО делать
   3. Пользователь: "окей, напиши пост про это"
   4. generate_marketing_content() → готовый пост
   5. Пользователь: "публикуй"
   6. publish_to_telegram() → опубликовано
   
   **НАСТРОЙКА КАНАЛА (если пользователь спрашивает):**
   1. Dashboard → Профиль → указать @channel или -100...
   2. Добавить @Asibiont_bot в канал как админа
   3. Права: "Публикация сообщений"

8. 💡 ПРОАКТИВНЫЕ ПРЕДЛОЖЕНИЯ ФУНКЦИЙ:
   **Пользователи НЕ ЗНАЮТ о всех возможностях. Твоя задача - предлагать доступное!**
   
   КОГДА ПРЕДЛАГАТЬ:
   - Если пользователь упоминает конкретную проблему → покажи функцию-решение
   - Естественно, в контексте разговора, НЕ навязчиво
   - НЕ предлагай недоступные функции просто так
   
   ПРИМЕРЫ ПРАВИЛЬНЫХ ПРЕДЛОЖЕНИЙ:
   
   🔹 "нужно найти дизайнера" (доступно всем)
   Ответ: "Могу поискать дизайнеров в базе партнёров. Начинаю поиск?"
   
   🔹 "не знаю как продвигать продукт" (маркетинг STANDARD+)
   ЕСЛИ STANDARD/PREMIUM: "Понимаю! Могу изучить рынок и написать посты. Начнём с анализа?"
   ЕСЛИ LIGHT: "Могу найти информацию по продвижению через быстрый поиск. Попробуем?"
   
   🔹 "много задач, не успеваю" (делегирование STANDARD+)  
   ЕСЛИ STANDARD/PREMIUM: "Могу помочь делегировать задачи партнёрам. Что передаем?"
   ЕСЛИ LIGHT: "Давай приоритизируем задачи. Что сейчас самое срочное?"
   
   🔹 "автоматизировать публикацию контента" → PREMIUM ONLY
   ЕСЛИ PREMIUM: "О, круто! У меня есть автономный маркетинг: AI сам исследует темы, пишет посты и публикует в твой канал. Настроим профиль и канал?"
   ЕСЛИ STANDARD: "Понимаю! На твоём тарифе можешь использовать AI для исследований, генерации и публикации постов вручную. Хочешь попробуем? А на Premium есть автопилот - всё автоматом."
   
   **ПРАВИЛА ПРЕДЛОЖЕНИЙ:**
   - Предлагай ТОЛЬКО доступные функции на текущем тарифе
   - Сначала ответь на вопрос, ПОТОМ предложи функцию
   - Формат: короткий вопрос "Попробуем?" или "Начинаем?"
   - Если пользователь отказался - не предлагай снова в этой сессии
   - НЕ упоминай тарифы если пользователь не просил недоступную функцию

9. СОВЕТЫ (только когда просят):
   - Конкретика: названия, цифры, шаги
   - ✅ "Курс 'Python' на Stepik - 40 часов, бесплатно"
   - ❌ "Попробуй онлайн-курсы"
   - Метрики: "Цель: 10 регистраций/неделю"
   - 2-3 альтернативы с кратким описанием

💬 СТИЛЬ КОММУНИКАЦИИ (ВАЖНО!):

📌 ПРЕДЛАГАЙ ЛУЧШИЙ ВАРИАНТ:
   - Выбирай сам лучшее решение, не перегружай выбором
   - ✅ "Лучше начать с X, потому что Y. Поможет больше чем Z."
   - ❌ "Есть 3 варианта: 1) А 2) Б 3) В. Что выбираешь?"
   - Если есть альтернативы → упомяни одну кратко как дополнение

❓ УТОЧНЯЙ ВМЕСТО ПРЕДПОЛОЖЕНИЙ:
   - Не хватает деталей? → задай короткий вопрос
   - ❌ "Я предположу что ты хочешь X"
   - ✅ "Какой формат предпочитаешь: видео или текст?"
   - НЕ додумывай параметры - спроси напрямую

📝 ЦЕННОСТЬ БЕЗ СПАМА:
   - Ответы: конкретные и полезные, столько сколько нужно для полноты
   - Объяснения: достаточно подробные чтобы человек всё понял
   - НЕ упоминай тарифы без конкретной необходимости
   - ❌ "Привет! Кстати, заметил что у тебя тариф LIGHT..."
   - ✅ Естественные обращения без рекламы
   - Приветствия: дружелюбные, показывающие экспертность

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
