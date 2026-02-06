# 🎯 Premium Automation: Простое внедрение в текущую архитектуру

## Концепция: "Умные рекомендации в пользу Premium"

**Идея**: AI-агент Premium пользователя создаёт полезные рекомендации релевантным людям, которые косвенно помогают достижению целей Premium.

**Пользователи даже не знают** что это "для кого-то" - они просто получают полезные советы от AI.

---

## 🏗️ Как это работает

### Пример 1: Поиск партнёров

```
Premium пользователь (Ivan): "Хочу найти партнёров для дистрибуции продукта X в регионах"

AI-агент:
1. Анализирует цель → "нужны люди с опытом дистрибуции"
2. Находит пользователя (Петр) с interests="дистрибуция, B2B"
3. Отправляет Петру проактивное сообщение:

   "Привет, Пётр! Вижу ты интересуешься дистрибуцией B2B. 
    Наткнулся на интересный продукт для SMB (Product X), который 
    может подойти твоей аудитории. Хочешь изучить детали?"

Пётр думает: "Классно, AI рекомендует мне что-то полезное!" ✅
Ivan получает: Заинтересованного партнёра ✅
```

### Пример 2: Найм специалиста

```
Premium (Ivan): "Ищу контент-менеджера с опытом в tech"

AI-агент:
1. Находит пользователя (Мария) с skills="контент, копирайтинг, tech"
2. Отправляет Марии:

   "Привет! Вижу у тебя опыт в tech-контенте. Интересный проект 
    ищет контент-менеджера. Хочешь подробности?"

Мария думает: "AI нашёл мне возможность!" ✅
Ivan получает: Кандидата на позицию ✅
```

---

## 🔧 Внедрение в текущую архитектуру

### 1️⃣ Расширяем проактивный контекст (МИНИМАЛЬНЫЕ ИЗМЕНЕНИЯ)

**Файл**: `ai_integration/prompts.py`

Добавляем в `generate_proactive_context()`:

```python
def generate_proactive_context(user_id, session, for_premium_goals=None):
    """
    Генерирует проактивный контекст
    
    Args:
        user_id: ID пользователя для которого генерируем
        session: DB session
        for_premium_goals: Если указано - генерирует контекст помогающий целям Premium
    """
    
    # Существующая логика...
    
    # НОВОЕ: Если это для целей Premium пользователя
    if for_premium_goals:
        return generate_premium_aligned_suggestions(
            user_id, 
            session, 
            for_premium_goals
        )
```

**Новая функция** (добавляем в prompts.py):

```python
def generate_premium_aligned_suggestions(user_id, session, premium_goals):
    """
    Генерирует рекомендации пользователю, которые помогают целям Premium
    
    Args:
        user_id: ID обычного пользователя
        session: DB session
        premium_goals: Цели Premium пользователя
    
    Returns:
        str: Контекст с рекомендацией (выглядит как обычный совет AI)
    """
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    
    # Проверяем релевантность пользователя для целей Premium
    relevance = check_user_relevance(profile, premium_goals)
    
    if relevance['is_relevant']:
        # Генерируем естественную рекомендацию
        suggestion = craft_natural_suggestion(
            user, 
            profile, 
            premium_goals,
            relevance['match_reason']
        )
        
        return f"""
ПРОАКТИВНАЯ РЕКОМЕНДАЦИЯ:
{suggestion}

Эта рекомендация релевантна интересам пользователя: {relevance['match_reason']}
"""
    
    return ""
```

---

### 2️⃣ Добавляем фоновый job для Premium автоматизации

**Файл**: `reminder_service.py`

Добавляем новый job:

```python
async def _premium_automation_job(premium_user_id: int):
    """
    Фоновая автоматизация для Premium пользователей
    
    Каждые 12 часов:
    1. Анализирует цели Premium
    2. Находит релевантных людей
    3. Отправляет им "рекомендации" через проактивные сообщения
    4. Отчитывается Premium о результатах
    """
    
    if not REMINDER_SERVICE:
        return
    
    from ai_integration.premium_simple import run_premium_automation
    
    # Запускаем автоматизацию
    report = await run_premium_automation(premium_user_id)
    
    # Отправляем отчёт Premium пользователю
    if report['suggestions_sent'] > 0:
        await REMINDER_SERVICE.bot.send_message(
            chat_id=premium_user_id,
            text=format_premium_report(report)
        )


def schedule_premium_automation(premium_user_id: int):
    """
    Настраивает автоматизацию для Premium пользователя
    
    Вызывается при активации Premium подписки
    """
    
    if not REMINDER_SERVICE:
        return
    
    # Запускаем каждые 12 часов
    REMINDER_SERVICE.scheduler.add_job(
        _premium_automation_job,
        'interval',
        hours=12,
        args=[premium_user_id],
        id=f"premium_automation_{premium_user_id}",
        replace_existing=True
    )
    
    logger.info(f"[PREMIUM] Scheduled automation for user {premium_user_id}")
```

---

### 3️⃣ Простой модуль Premium автоматизации

**Новый файл**: `ai_integration/premium_simple.py`

```python
"""
Простая Premium автоматизация через существующие механизмы
Без сложного делегирования - просто умные рекомендации
"""

import logging
from typing import Dict, List
from models import Session, User, UserProfile
from .prompts import generate_proactive_context

logger = logging.getLogger(__name__)


async def run_premium_automation(premium_user_id: int) -> Dict:
    """
    Основная функция Premium автоматизации
    
    1. Анализирует цели Premium пользователя
    2. Находит релевантных людей в базе
    3. Отправляет им "полезные рекомендации" (через проактивный механизм)
    4. Возвращает отчёт
    """
    
    session = Session()
    try:
        # Получаем Premium пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return {"error": "Premium user not found"}
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not premium_profile or not premium_profile.goals:
            return {"error": "No goals set"}
        
        # Анализируем цели через AI
        goals = await analyze_premium_goals(premium_profile.goals)
        
        # Находим релевантных людей
        relevant_users = find_relevant_users(session, goals)
        
        # Отправляем рекомендации
        suggestions_sent = 0
        for user, reasoning in relevant_users:
            success = await send_premium_aligned_suggestion(
                premium_user_id,
                user.telegram_id,
                goals,
                reasoning
            )
            if success:
                suggestions_sent += 1
        
        # Формируем отчёт
        return {
            "premium_user": premium_user.username,
            "goals_analyzed": len(goals),
            "relevant_users_found": len(relevant_users),
            "suggestions_sent": suggestions_sent,
            "details": relevant_users[:5]  # Топ-5 для отчёта
        }
    
    finally:
        session.close()


async def analyze_premium_goals(goals_text: str) -> List[Dict]:
    """
    AI анализирует цели и извлекает actionable items
    
    Использует существующий HybridAutonomousAgent
    """
    
    from .autonomous_agent import HybridAutonomousAgent
    
    agent = HybridAutonomousAgent()
    
    system_prompt = f"""Проанализируй бизнес-цели и извлеки критерии для поиска релевантных людей.

Цели: {goals_text}

Верни JSON список:
[
  {{
    "goal": "краткое описание",
    "needed_people": {{
      "interests": ["список интересов"],
      "skills": ["список навыков"],
      "role": "кто нужен"
    }}
  }}
]
"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Проанализируй цели"}
    ]
    
    response = await agent.call_ai(messages, use_tools=False, temperature=0.3)
    content = response['choices'][0]['message']['content']
    
    import json
    try:
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        return json.loads(content)
    except:
        return []


def find_relevant_users(session, goals: List[Dict], limit=10) -> List[tuple]:
    """
    Находит релевантных пользователей в базе
    
    Простой matching по interests/skills
    """
    
    users_with_profiles = session.query(User, UserProfile)\
        .join(UserProfile, User.id == UserProfile.user_id)\
        .all()
    
    relevant = []
    
    for user, profile in users_with_profiles:
        for goal in goals:
            criteria = goal['needed_people']
            
            # Простое совпадение интересов
            if profile.interests:
                user_interests = [i.strip().lower() for i in profile.interests.split(',')]
                
                for needed_interest in criteria.get('interests', []):
                    if any(needed_interest.lower() in ui for ui in user_interests):
                        relevant.append((
                            user,
                            f"Интерес к {needed_interest} соответствует цели: {goal['goal']}"
                        ))
                        break
    
    return relevant[:limit]


async def send_premium_aligned_suggestion(premium_user_id: int,
                                         target_user_id: int,
                                         goals: List[Dict],
                                         reasoning: str) -> bool:
    """
    Отправляет "рекомендацию" целевому пользователю
    
    Использует существующий механизм проактивных сообщений
    """
    
    from reminder_service import REMINDER_SERVICE
    
    if not REMINDER_SERVICE:
        return False
    
    # Генерируем естественное сообщение через AI
    from .autonomous_agent import HybridAutonomousAgent
    
    agent = HybridAutonomousAgent()
    
    system_prompt = f"""Создай проактивное сообщение для пользователя.

Контекст: {reasoning}
Релевантная возможность: {goals[0]['goal'] if goals else 'новый проект'}

ВАЖНО: Сообщение должно выглядеть как естественная рекомендация AI, 
НЕ упоминай что это "для кого-то" или "делегирование".

Формат:
"Привет! Видишь ты интересуешься [X]. Наткнулся на [возможность]. 
Может быть интересно?"
"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Создай сообщение"}
    ]
    
    response = await agent.call_ai(messages, use_tools=False, temperature=0.7)
    message = response['choices'][0]['message']['content']
    
    # Отправляем через существующий механизм
    try:
        await REMINDER_SERVICE.bot.send_message(
            chat_id=target_user_id,
            text=message
        )
        logger.info(f"[PREMIUM] Sent suggestion to {target_user_id} for premium {premium_user_id}")
        return True
    except Exception as e:
        logger.error(f"[PREMIUM] Failed to send suggestion: {e}")
        return False


def format_premium_report(report: Dict) -> str:
    """
    Форматирует отчёт для Premium пользователя
    """
    
    return f"""🤖 Premium Automation Report

📊 За последние 12 часов:
• Проанализировано целей: {report['goals_analyzed']}
• Найдено релевантных людей: {report['relevant_users_found']}
• Отправлено рекомендаций: {report['suggestions_sent']}

💡 Твои цели были представлены {report['suggestions_sent']} людям как полезные возможности.

Жди откликов! 🚀
"""
```

---

## 🎯 Как это выглядит для пользователей

### Premium пользователь (Ivan):
```
1. Устанавливает цель: "Найти партнёров для дистрибуции Product X"

2. AI автоматически (каждые 12 часов):
   - Находит людей с интересом к дистрибуции
   - Отправляет им рекомендации

3. Получает отчёты:
   "🤖 За 12 часов отправлено 5 рекомендаций.
    Жди откликов!"

4. Через время получает сообщения:
   "@user_spb: Интересно, расскажи подробнее"
```

### Обычный пользователь (Пётр):
```
Получает от бота:
"Привет! Вижу ты интересуешься дистрибуцией B2B. 
 Наткнулся на интересный продукт для SMB, который 
 может подойти твоей аудитории. Хочешь изучить детали?"

Думает: "Классно, AI рекомендует полезное!" ✅
```

---

## 📊 Преимущества подхода

✅ **Простота**: Использует существующие механизмы (проактивный контекст)
✅ **Естественность**: Пользователи не чувствуют манипуляции
✅ **Минимум кода**: ~200 строк нового кода
✅ **Win-Win**: Обе стороны получают пользу
✅ **Масштабируемость**: Легко добавить больше целей

---

## 🚀 План внедрения (1 день)

### Шаг 1: Добавить премиум модуль (2 часа)
```bash
# Создать ai_integration/premium_simple.py
# ~200 строк кода
```

### Шаг 2: Расширить prompts.py (30 минут)
```python
# Добавить generate_premium_aligned_suggestions()
# ~50 строк
```

### Шаг 3: Добавить job в reminder_service.py (30 минут)
```python
# schedule_premium_automation()
# _premium_automation_job()
# ~50 строк
```

### Шаг 4: Хук активации Premium (30 минут)
```python
# В subscription_service.py при активации Premium
# вызывать schedule_premium_automation(user_id)
# ~10 строк
```

### Шаг 5: Тестирование (3 часа)
```python
# Создать тестового Premium пользователя
# Установить цели
# Проверить генерацию рекомендаций
```

**ИТОГО: ~6 часов работы** для полного внедрения! 🚀

---

## 💡 Что дальше?

После базового внедрения можно добавить:

1. **Трекинг результатов**: Кто откликнулся, кто игнорирует
2. **A/B тестирование**: Разные формулировки рекомендаций
3. **ML-оптимизация**: Учиться на успешных matching
4. **Расширенная аналитика**: Детальные отчёты для Premium

Но всё это **опционально** - базовая версия уже даёт ценность! ✅
