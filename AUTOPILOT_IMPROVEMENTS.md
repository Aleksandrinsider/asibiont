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
