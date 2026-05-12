# Улучшения AnchorEngine (Автопилот)

## 1. 🏗️ Архитектура: декомпозиция монолита

**Проблема:** Весь автопилот — один файл [`anchor_engine.py`](anchor_engine.py) (28 989 строк). Класс `AnchorEngine` (строка 5017 → ~28964) содержит ~24 000 строк.

**Предложение:** Разделить на модули:
```
anchor_engine/
├── __init__.py              # exports + AnchorEngine class (thin facade)
├── config.py                # _CAP_CATEGORY_MAP, constants, cooldowns
├── models.py                # Pydantic/schema models для anchor, delivery
├── scanner.py               # _scan_all_users, _process_user, anti-spam
├── prompt_builder.py        # _build_autopilot_prompt (2680 строк → в отдельный файл)
├── coordinator.py           # _run_coordinator_dispatch (7600 строк)
├── dispatcher.py            # _dispatch_agent_for_anchor (4600 строк)
├── email.py                 # email-логика (diagnostics, campaigns)
├── memory.py                # _build_goal_strategy_memory, стратегии
├── utils.py                 # _safe_http, _safe_send, _strip_md, _safe_avatar
└── i18n.py                  # _t, _lang_directive, _tg_prefix
```

## 2. 🔄 Импорты на уровне модуля (вместо внутри функций)

**Проблема:** Каждый метод делает:
```python
from models import Session as _Sess, User as _Usr
```

Это замедляет каждый вызов на ~5-10ms из-за overhead импорта. При 1000 вызовов в цикле — потеря производительности.

**Решение:** Вынести все импорты `from models import ...` наверх файла. Использовать lazy imports только для циклических зависимостей.

## 3. 🧠 Промпт-билдер: вынести ключевые слова в data-driven конфиг

**Проблема:** `_build_autopilot_prompt()` содержит ~250 строк с хардкодными списками ключевых слов:
```python
_RESEARCH_KW = ('анализ', 'исследован', 'мониторинг', ...)
_OUTREACH_KW = ('найти клиент', 'привлеч', ...)
# ... 12 категорий × ~30-40 слов = ~400+ ключевых слов
```

**Предложение:** Вынести keyword → category mapping в отдельный YAML/JSON/Toml файл:
```yaml
# goal_categories.yaml
research: [анализ, исследован, мониторинг, обзор, рынок, ...]
outreach: [найти клиент, привлеч, подписчик, пользовател, ...]
content: [контент, smm, reels, видео, медиаплан, ...]
```

## 4. 📊 Добавить метрики и observability

**Проблема:** Нет мониторинга production:
- Сколько циклов сканирования выполняется
- Сколько anchors создаётся/доставляется за цикл
- Сколько AI-вызовов делается
- Какая частота ошибок AI (timeout, rate limit)
- Среднее время цикла

**Предложение:**
```python
class AnchorMetrics:
    cycle_count: int
    anchors_scanned: int
    anchors_delivered: int
    ai_calls: int
    ai_timeouts: int
    ai_errors: int
    cycle_duration_avg: float
    users_processed: int
    tokens_spent: int
```

Логировать в `anchor_metrics` каждые N циклов + экспортировать через `/health` endpoint.

## 5. ⏱️ Оптимизация: `_scan_all_users` конвейер

**Проблема:** Двухфазный пайплайн (bulk pre-filter → parallel scan), но нет кэширования между циклами. Каждый цикл заново загружает всех пользователей.

**Предложение:** Опциональное кэширование (Redis или in-memory TTL cache):
- `user_last_seen` — чтобы не пересканировать неактивных пользователей чаще раза в час
- `scan_cooldown` — per-user cooldown кэш (чтобы не сканировать только что обработанного)

## 6. 🧩 Context length management для AI вызовов

**Проблема:** Промпт автопилота может быть очень длинным:
- Goals (до 5 шт)
- Capability card (до 30+ строк)
- История действий
- Goal strategy memory
- Vector memory
- Integration snapshots
- Coordinator insights

**Предложение:** Добавить динамическое сжатие контекста:
- Если суммарный промпт > 8000 токенов DeepSeek — обрезать историю/insights
- Если агент не использует email — исключать email-блоки из промпта
- Сжимать vector memory до top-5 релевантных записей

## 7. ⚙️ AI-семафор и управление параллелизмом

**Проблема:** Жёсткий `Semaphore(20)` для всех AI-вызовов. Крупные пользователи (с email кампаниями) могут блокировать семафор.

**Предложение:** Адаптивный семафор:
```python
# Разные приоритеты для разных типов вызовов
self._ai_semaphore = {
    'critical': asyncio.Semaphore(5),    # goal_autopilot_review
    'dispatch': asyncio.Semaphore(10),   # dispatch агентов
    'background': asyncio.Semaphore(5),  # email, контент
}
```

## 8. 🔁 Дублирование кода

**Проблема:** Код recovery stuck dispatches повторяется дважды:
1. При старте (строки 5043-5067)
2. В каждом цикле (строки 5076-5150)

**Предложение:** Вынести в `_recover_stuck_dispatches()`.

## 9. 🐍 Упростить `_safe_http` — удалить 4-ю копию

**Проблема:** [`anchor_engine.py:76-84`](anchor_engine.py:76) содержит ещё одну копию `_safe_http` (4-я в проекте).

**Предложение:** Импортировать из общего utils-модуля.

## 10. 📝 Type hints

**Проблема:** 95% функций без аннотаций типов. Например:
```python
def _build_autopilot_prompt(goals_summary: list, user=None, agent_caps=None, agent_name=None, ...)
```

**Предложение:** Добавить типы:
```python
def _build_autopilot_prompt(
    goals_summary: list[dict],
    user: Optional[User] = None,
    agent_caps: Optional[list[str]] = None,
    agent_name: Optional[str] = None,
    ...
) -> str:
```

## 11. 🎯 Anti-spam: более интеллектуальная система

**Проблема:** Сейчас cooldown основан только на приоритете:
- CRITICAL/HIGH: всегда
- MEDIUM: 3h
- LOW: 8h

**Предложение:** Добавить факторы:
- Время с последнего успешного touch'а
- Количество неудачных dispatch'ей (если AI постоянно отказывается — увеличить cooldown)
- User engagement score (если пользователь не отвечает — снизить частоту)

## 12. 🧪 Testability

**Проблема:** Огромные функции с глубокими try/except практически невозможно тестировать.

**Предложение:** После декомпозиции (п.1) добавить unit tests для:
- `_build_autopilot_prompt` (проверка что промпт содержит нужные секции)
- `_classify_agent_caps`
- `_sanitize_proactive_text` (гендерная коррекция)
- `_safe_http` (retry logic)

---

## 13. 🎯 Агент обязан учитывать цели пользователя

**Проблема:** Координатор формирует план, но нет гарантии, что каждое действие привязано к конкретной цели пользователя. Часть действий агентов — «активность ради активности» (многократный RSS, посты без привязки к метрикам целей).

**Решение:** В каждом prompt'е координатора и агента явно указывать цели пользователя и требовать привязки:

```python
# В _build_autopilot_prompt, после заголовка:
prompt += "\n\n🎯 User Goals:\n"
for g in user_goals:
    prompt += f"  [{g.priority}] {g.title} — progress {g.progress_percentage}%\n"

prompt += "\n⚠️ RULE: Every action MUST serve at least one goal above.\n"
prompt += "If an action doesn't advance any goal — don't do it.\n"
```

Универсально: неважно, какие цели у пользователя — они читаются из таблицы `goals` и подставляются динамически.

## 14. 🌐 Язык пользователя — как на лендинге

**Проблема:** Некоторые user-facing ответы смешивают RU и EN, или используют не тот язык.

**Решение:** Автоопределение языка через профиль пользователя (как на лендинге):

```python
def _detect_user_locale(user) -> str:
    """Определяет язык — как лендинг: один язык на пользователя."""
    if hasattr(user, 'language_code') and user.language_code:
        return user.language_code
    if hasattr(user, 'locale') and user.locale:
        return user.locale[:2]
    return 'en'  # универсальный fallback
```

Все user-facing строки через словарь с locale-ключом. Внутренняя логика — language-agnostic.

## 15. 📚 DecisionLog: замкнуть цикл обучения

**Проблема:** `decision_log` — write-only. Поле `learned` всегда пустое, `outcome_score` повторяется (0.45/0.75/0.85). Система не учится на ошибках.

**Решение:** Добавить извлечение урока после каждого действия и подачу уроков в следующий prompt:

```python
# После каждого вызова инструмента — извлечь урок
async def _extract_lesson(action, result, goal_context, locale='en'):
    prompt = f"""Analyze: {action} | Result: {result[:300]} | Goal: {goal_context[:200]}
Extract ONE specific lesson. Return JSON: {{"lesson":"...", "score":0.0-1.0}}"""
    return await _quick_ai_call_raw(prompt, max_tokens=150)

# Перед dispatch — прочитать уроки
lessons = session.query(DecisionLog).filter(
    DecisionLog.user_id == user_id,
    DecisionLog.learned != '',
    DecisionLog.created_at > datetime.utcnow() - timedelta(hours=24)
).all()
if lessons:
    prompt += "\n## Lessons from last 24h:\n" + \
        '\n'.join(f"- {l.learned}" for l in lessons)
```

Универсально: не привязано к конкретным инструментам или целям.

## 16. 📊 CoordinatorInsights: принудительное сохранение

**Проблема:** Таблица `coordinator_insights` пуста — `_save_coordinator_insights` не сохраняет данные.

**Решение:** AI-генерация инсайтов + fallback-запись:

```python
# В конце _run_coordinator_dispatch
insight = await _quick_ai_call_raw(
    f"Analyze cycle: {n_actions} actions, {n_goals} goals, "
    f"errors: {errors}. Return JSON insight.",
    max_tokens=300
)
if not insight:
    insight = '{"type":"cycle_note","summary":"Empty cycle"}'

session.add(CoordinatorInsight(
    user_id=user_id,
    insight_type=parsed.get('type', 'cycle_note'),
    summary=parsed.get('summary', '')[:500],
    evidence_score=parsed.get('score', 0.0)
))
session.commit()
```

## 17. 🔄 Дедупликация активностей агентов

**Проблема:** Один агент вызывает один и тот же RSS/API 5-10 раз за цикл под разными именами инструментов.

**Решение:** Кэш результатов в памяти цикла + проверка на дубликаты перед созданием AgentActivityLog:

```python
class CycleCache:
    def __init__(self, ttl=600):
        self._cache = {}
    
    def _key(self, tool, params):
        return f"{tool}:{hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]}"
    
    def get(self, tool, params):
        key = self._key(tool, params)
        if key in self._cache:
            ts, result = self._cache[key]
            if time.time() - ts < self.ttl:
                return result
        return None
    
    def set(self, tool, params, result):
        self._cache[self._key(tool, params)] = (time.time(), result)
```

## 18. 🛡️ Graceful обработка ошибок AI

**Проблема:** TimeoutError роняет агента или весь цикл.

**Решение:** 3-уровневый fallback:

```python
async def _call_ai_safe(prompt, max_tokens=2000, timeout=60, locale='en'):
    fallbacks = [
        prompt,                                                     # full
        prompt[:4000] + "\n[truncated]",                           # truncated
        {'en':'Answer: next step?','ru':'Ответь: следующий шаг?'}.get(locale, '')  # emergency
    ]
    for level, fb in enumerate(fallbacks, 1):
        try:
            return await _quick_ai_call_raw(
                fb, max_tokens=max_tokens // (2**(level-1)),
                timeout=timeout // (2**(level-1))
            )
        except (asyncio.TimeoutError, TimeoutError):
            continue
    return None
```

## 19. 🧪 Обратная связь без участия пользователя

**Проблема:** Система работает вслепую — нет сигнала, правильно ли выбран курс.

**Решение:** Неявные метрики здоровья (из данных, а не от пользователя):

```python
health = {
    'task_completion': completed_tasks / max(total_tasks, 1),  # из tasks
    'error_rate': errors / max(total_actions, 1),              # из AAL
    'goal_progress': avg_progress,                              # из goals
    'user_engaged': has_recent_interaction,                    # из interactions
}
# Если здоровье > 0.8 → реже циклы
# Если здоровье < 0.3 → чаще, с приоритетом восстановления
```

## 20. 🧩 Падение внешних API (Circuit Breaker)

**Проблема:** RSS/внешние API возвращают 502, система продолжает долбиться.

**Решение:** Универсальный Circuit Breaker для любого внешнего сервиса:

```python
class CircuitBreaker:
    def __init__(self, threshold=3, recovery=300):
        self._services = {}
        self.threshold = threshold
        self.recovery = recovery
    
    async def call(self, name, func, *a, **kw):
        svc = self._services.setdefault(name, {'state':'closed', 'failures':0, 'last_failure':0})
        if svc['state'] == 'open':
            if time.time() - svc['last_failure'] > self.recovery:
                svc['state'] = 'half-open'
            else:
                raise CircuitBreakerOpen(name)
        try:
            result = await func(*a, **kw)
            svc['failures'] = 0
            svc['state'] = 'closed'
            return result
        except:
            svc['failures'] += 1
            svc['last_failure'] = time.time()
            if svc['failures'] >= self.threshold:
                svc['state'] = 'open'
            raise
```

## 21. 🔗 Автоматическая адаптация каналов коммуникации

**Проблема:** Система продолжает использовать канал с 1% отклика без адаптации.

**Решение:** Динамический трекер эффективности (определяет каналы из данных, без hardcode):

```python
channels = session.query(
    AAL.activity_type, func.count(AAL.id),
    func.sum(case((AAL.status == 'replied', 1), else_=0))
).filter(AAL.user_id == user_id, AAL.created_at > lookback).group_by(AAL.activity_type).all()

for name, total, replies in channels:
    rate = replies / max(total, 1)
    if total >= 10 and rate < 0.02:
        logger.info("Auto-pausing %s (reply rate %.1f%%)", name, rate * 100)
        _pause_channel(user_id, name)
```

Универсально: работает для email, Telegram, Discord, SMS, CRM — любых каналов.

## 22. ⏰ Адаптивная частота циклов

**Проблема:** Все агенты работают по фиксированному расписанию (120/240 мин), независимо от продуктивности.

**Решение:** Динамическая частота на основе здоровья системы:

```python
if health['score'] > 0.8:
    interval = min(base * 2, 480)      # реже — система работает
elif health['score'] < 0.3:
    interval = max(base // 2, 60)     # чаще — нужно восстановление
```

---

### ⚡ Быстрые победы (каждая < 30 мин)

| # | Фикс | Суть |
|---|------|------|
| 1 | `_safe_get()` для всех `.get()` | Предотвращает `AttributeError: 'str' has no 'get'` |
| 2 | Fallback таймаутов (3 уровня) | Агенты не падают при TimeoutError |
| 3 | Дедупликация AAL перед вставкой | Нет дублей активности |
| 4 | Force-save CoordinatorInsights | Появляется стратегическая память |
| 5 | Backfill пустых `learned` | Запускается самообучение |
| 6 | Circuit Breaker для внешних API | Нет бесконечных ретраев в 502 |
