# Premium Enhanced Insights - Реализовано ✅

## Что добавлено

### 🎯 Новые типы инсайтов в промпте:

1. **deadline_alert** 🔴 (HIGH priority)
   - Задача с близким дедлайном (3 дня, 1 день)
   - Формат: "До дедлайна 'Презентация' осталось 3 дня"

2. **stuck_task** 🔴 (HIGH/MEDIUM priority)
   - Задача без прогресса 3+ дней
   - Формат: "Задача 'Анализ конкурентов' без прогресса 5 дней — возможно нужна помощь?"

3. **market_opportunity** 🟡 (MEDIUM priority)
   - Тренды в сообществе за неделю
   - Формат: "5 человек искали 'дизайнеров' за неделю (пересекается с твоим интересом)"

4. **reverse_matching** 🟡 (MEDIUM priority)
   - Кто-то нуждается в помощи Premium
   - Формат: "@newbie: 'ищет наставника в e-commerce' — ты эксперт в 'e-commerce'"

5. **trend** 🟢 (LOW priority)
   - Долгосрочные тренды (2 недели)
   - Формат: "Тренд: 10 упоминаний 'экопродукты' за 2 недели (релевантно твоим целям)"

6. **task_created** (существующий)
   - Кто-то создал релевантную задачу
   - Формат: "Возможность: 'Найти дистрибьюторов' (релевантно: Интересы совпадают)"

---

## 📅 Расписание сбора инсайтов

### Автоматический сбор:
- **9:00 MSK** (6:00 UTC) — Утренний сбор инсайтов
- **20:00 MSK** (17:00 UTC) — Вечерний сбор инсайтов
- **Понедельник 10:00 MSK** — Еженедельная аналитика

### Real-time триггеры:
- **Premium создал задачу** → сбор market opportunities
- **Любой создал задачу** → проверка на reverse matching
- **Изменение статуса задачи** → проверка stuck/deadline

---

## 🎨 Форматирование в промпте

AI теперь видит инсайты с приоритетами:

```
==================================================
ПРЕМИУМ ИНСАЙТЫ (упомяни ЕСТЕСТВЕННО в зависимости от контекста диалога):

🔴 СРОЧНО:
- До дедлайна 'Презентация для инвесторов' осталось 3 дня
- Задача 'Анализ конкурентов' без прогресса 5 дней — возможно нужна помощь?

🟡 ВОЗМОЖНОСТИ:
- 5 человек искали 'дизайнеров' за неделю (пересекается с твоим интересом)
- @newbie: 'ищет наставника' — ты эксперт в 'e-commerce'

🟢 ТРЕНДЫ:
- Тренд: 10 упоминаний 'экопродукты' за 2 недели (релевантно твоим целям)

ВАЖНО:
- Упоминай ТОЛЬКО если релевантно текущей теме разговора
- Вплетай естественно, как свою идею
- Дружелюбный тон, короткое упоминание (1-2 предложения)
- Если не подходит момент - лучше пропусти
- Приоритет: срочное → возможности → тренды
==================================================
```

---

## 📁 Изменённые/созданные файлы

### Новые:
- `ai_integration/premium_scheduler.py` — scheduler для фоновых задач

### Обновлённые:
- `ai_integration/premium_simple.py` (+500 строк)
  - `check_deadlines_and_stuck_tasks()`
  - `find_market_opportunities()`
  - `find_reverse_matching()`
  - `analyze_community_trends()`
  - `collect_all_premium_insights()`
  - `get_premium_recommendations_for_prompt()` — улучшено форматирование
  - `_format_insight_for_prompt()` — поддержка всех типов

- `ai_integration/handlers.py`
  - `add_task()` — добавлен вызов `on_premium_task_created()`

- `main.py`
  - `start_premium_scheduler()` — запуск scheduler при старте бота

---

## 🚀 Как это работает

### Жизненный цикл инсайта:

1. **Сбор** (scheduler или real-time):
   ```python
   insights = await check_deadlines_and_stuck_tasks(premium_user_id)
   ```

2. **Сохранение** в `pending_premium_recommendations`:
   ```json
   {
     "type": "deadline_alert",
     "task_title": "Презентация",
     "days_left": 3,
     "priority": "high",
     "shown_count": 0
   }
   ```

3. **Промпт** (при каждом сообщении):
   ```python
   proactive_context = get_premium_recommendations_for_prompt(user_id)
   # Добавляется в системный промпт
   ```

4. **Упоминание** (AI сам решает когда):
   ```
   Пользователь: Привет, как дела?
   
   AI: Привет! 👋 Кстати, до дедлайна "Презентация" осталось 3 дня.
       Всё под контролем или нужна помощь?
   ```

5. **Lifecycle** (после упоминания):
   ```python
   mark_recommendation_shown(user_id)  # shown_count += 1
   # После 3 показов → удаляется
   ```

---

## 🧪 Тестирование

### Запуск бота:
```bash
python main.py
```

### Проверка scheduler:
```python
# В логах должно быть:
[PREMIUM_SCHEDULER] Started successfully
[PREMIUM_SCHEDULER] Running morning insights job (в 9:00 MSK)
[PREMIUM_SCHEDULER] Running evening insights job (в 20:00 MSK)
```

### Ручной триггер:
```python
from ai_integration.premium_scheduler import trigger_insights_for_premium_user
report = await trigger_insights_for_premium_user(premium_user_id=123456789)
print(report)
# {'insights_collected': 5, 'breakdown': {...}}
```

### Проверка инсайтов в профиле:
```python
from models import Session, User, UserProfile
session = Session()
user = session.query(User).filter_by(telegram_id=YOUR_ID).first()
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
print(profile.pending_premium_recommendations)
# JSON с инсайтами
```

---

## ✨ Примеры диалога

### Пример 1: Утреннее сообщение
```
Пользователь: Доброе утро

AI: Доброе утро! 👋
    
    Кстати, до дедлайна "Презентация для инвесторов" осталось всего 3 дня.
    Всё идёт по плану или нужна помощь?
```

### Пример 2: Обсуждение задач
```
Пользователь: Как мои дела с проектами?

AI: У тебя 5 активных задач, в целом движение хорошее!
    
    Правда, "Анализ конкурентов" застопорился — уже 5 дней без изменений.
    Может поможет кто-то со стороны?
```

### Пример 3: Обсуждение нетворкинга
```
Пользователь: Хочу расширить сеть

AI: Отличная идея!
    
    Кстати, заметил интересное: @newbie_entrepreneur ищет наставника 
    в e-commerce. Ты как раз эксперт в этой теме — может познакомлю?
```

---

## 📊 Метрики для мониторинга

### Логи:
- `[PREMIUM_SCHEDULER]` — работа scheduler
- `[PREMIUM_INSIGHTS]` — сбор инсайтов
- `[PREMIUM_AUTO]` — сохранение/загрузка из профиля

### Успешный flow:
```
[PREMIUM_SCHEDULER] Started successfully
[PREMIUM_SCHEDULER] Running morning insights job
[PREMIUM_SCHEDULER] Found 3 Premium users
[PREMIUM_INSIGHTS] Collecting all insights for 123456789
[PREMIUM_INSIGHTS] Found 2 deadline/stuck insights
[PREMIUM_INSIGHTS] Found 1 market opportunities
[PREMIUM_INSIGHTS] Found 1 reverse matching opportunities
[PREMIUM_INSIGHTS] Found 0 trends
[PREMIUM_INSIGHTS] Collected 4 total insights
[PREMIUM_SCHEDULER] Collected 4 insights for user 123456789
```

---

## 🎯 Итог

**Что получили:**
✅ 6 типов инсайтов (deadline, stuck, market, reverse, trend, task)
✅ Автоматический сбор 2 раза в день + еженедельно
✅ Real-time при создании задачи
✅ Красивое форматирование по приоритетам (🔴🟡🟢)
✅ Естественная интеграция через промпт
✅ AI сам выбирает КОГДА упомянуть
✅ Lifecycle management (shown 3x → удаляется)

**Premium теперь:**
> Ваш личный AI-координатор работает 24/7: находит нужных людей, запускает коллаборации, выстраивает связи пока вы заняты. Вы ставите цели — AI их выполняет. Premium-приоритет в рекомендациях.

🚀 **Production-ready!**
