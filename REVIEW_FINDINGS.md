# Code Review Findings

## 1. 🚨 Критические баги (Critical Bugs)

### 1.1. `check_and_deduct()` не передаёт `session` в `spend_tokens()`
**Файл:** [`token_service.py:480-490`](token_service.py:480)

Функция `check_and_deduct()` принимает опциональный параметр `session`, но использует его **только** для вызова `has_enough_tokens()`. При вызове `spend_tokens()` параметр `session` не передаётся, поэтому `spend_tokens()` открывает **новую** сессию БД. Это приводит к потенциальному race condition и лишним подключениям.

```python
async def check_and_deduct(user_id: int, action: str, session=None) -> dict:
    if session:
        has_enough = await has_enough_tokens(user_id, action, session=session)
    else:
        has_enough = await has_enough_tokens(user_id, action)
    if not has_enough:
        return {"success": False, ...}
    # ⚠️ session не передаётся!
    return await spend_tokens(user_id, action)  # всегда открывает новую сессию
```

**Исправление:** Передавать `session` в `spend_tokens()`.

### 1.2. `check_telegram_authentication()` некорректно обрабатывает токен бота
**Файл:** [`main.py:739-740`](main.py:739)

```python
secret_key = hashlib.sha256(bot_token.encode()).digest()  # строка 739
# Токен должен быть в raw виде, но 'bot' префикс не отрезается
```

**Контекст:** Telegram требует SHA256 хеш **raw токена** (без префикса `bot`). Если в `BOT_TOKEN` хранится токен с префиксом `bot123456:ABC` — хеш будет неверным, и аутентификация через Telegram Login Widget **не будет работать**.

### 1.3. `engine.dispose()` выполняется при импорте модуля
**Файл:** [`main.py:157`](main.py:157)

```python
engine = create_async_engine(...)
# ...
engine.dispose()  # Выполняется при import main
```

`engine.dispose()` вызывается на уровне модуля, т.е. при любом `import main`. Это может происходить до того, как приложение полностью инициализировано, и приводит к преждевременному закрытию пула соединений.

### 1.4. `threading.Thread` для проверки БД в асинхронном контексте
**Файл:** [`main.py:169-180`](main.py:169)

```python
def _check_db():
    # синхронный код
threading.Thread(target=_check_db, daemon=True).start()
```

Использование `threading.Thread` в асинхронном приложении — нестандартный и потенциально опасный паттерн. В aiogram/aiohttp приложении следует использовать `asyncio.create_task()` или `asyncio.get_event_loop().run_in_executor()`.

### 1.5. Discord bot — ручное начисление токенов вместо вызова `grant_signup_tokens()`
**Файл:** [`discord_bot.py:455`](discord_bot.py:455)

```python
user.tokens = 1500  # прямое присвоение
```

Вместо вызова `grant_signup_tokens(user.id)` из `token_service.py`. Это означает, что логика начисления бесплатных токенов дублируется, и при изменении количества бесплатных токенов в `token_service.py` Discord-регистрация не получит обновление.

## 2. ⚠️ Серьёзные проблемы (Major Issues)

### 2.1. Монолитные файлы огромного размера
| Файл | Размер | Строк |
|------|--------|-------|
| `anchor_engine.py` | ~28 989 | Самый большой |
| `main.py` | ~14 614 | Второй по величине |

**Рекомендация:** Разделить на модули. Для `main.py` — вынести HTTP хендлеры в отдельные роутеры. Для `anchor_engine.py` — вынести класс `AnchorEngine` в отдельный файл, а утилиты — в `utils.py`.

### 2.2. 4 дублирующиеся копии `_safe_http()`
Функция `_safe_http()` определена в:
1. [`main.py`](main.py) — первая копия
2. [`handlers.py:24-31`](handlers.py:24)
3. [`crypto_payments.py`](crypto_payments.py) — третья копия
4. [`anchor_engine.py:76-84`](anchor_engine.py:76) — четвёртая копия

**Рекомендация:** Вынести в общий утилитарный модуль, например `utils.py`.

### 2.3. `subscription_service.py` — импорт несуществующей константы
**Файл:** [`subscription_service.py`](subscription_service.py)

```python
from payments import TOKEN_PACK_PRICES  # ⚠️ Не экспортируется из payments.py
```

`payments.py` не содержит `TOKEN_PACK_PRICES`. При импорте `subscription_service` возникнет `ImportError`.

### 2.4. `_refresh_one_avatar()` — дублирующаяся сессия БД
**Файл:** [`main.py:706-723`](main.py:706)

Функция в конце открывает ещё одну сессию БД, хотя уже работает внутри сессии. Это создаёт лишние подключения к БД.

## 3. 🔧 Умеренные проблемы (Moderate Issues)

### 3.1. Опечатки в русских комментариях и строках
| Строка | Ошибка | Исправление |
|--------|--------|-------------|
| `main.py:737` | `"Проерка"` | `"Проверка"` |
| `main.py:1640` | `"Проерает"` | `"Проверяет"` |
| `main.py:1675` | `"Проеряет"` | `"Проверяет"` |
| `main.py:2974` | `"Заершает"` | `"Завершает"` |
| `main.py:3004` | `"заершея"` | `"завершая"` |
| `main.py:3087` | `"Воссталиает"` | `"Восстанавливает"` |
| `main.py:3258` | `"делегироае"` | `"делегирование"` |
| `main.py:3288` | `"Пересит"` | `"Пересчит"` |
| `main.py:3288` | `"ое ремя"` | `"время"` |
| `anchor_engine.py:8100` | `"контакто"` | `"контактов"` |

### 3.2. Отсутствует аннотация типов во многих функциях
Во многих хендлерах и вспомогательных функциях отсутствуют type hints, что усложняет поддержку и статический анализ.

### 3.3. Чрезмерное количество `try/except` с bare `except`
Во многих местах используется `except:` без указания типа исключения, что может маскировать ошибки программирования (например, `NameError`, `TypeError`).

### 3.4. Русские комментарии вперемешку с английским кодом
Код содержит смесь русских и английских комментариев. Рекомендуется выбрать один язык для документации.

## 4. 📝 Архитектурные замечания (Architectural Notes)

### 4.1. Две системы биллинга
- **Новая:** `token_service.py` — токены с атомарным `UPDATE...RETURNING`
- **Старая:** `subscription_service.py` — подписки (похоже, больше не используется)

Легаси-код `subscription_service.py` всё ещё импортируется, но не используется в новом потоке. Рекомендуется удалить или явно деприкейтить.

### 4.2. AnchorEngine — чрезмерная сложность
`_dispatch_agent_for_anchor()` (строки 6727-11336) содержит **~4600 строк**. Это одна функция! Она включает:
- Логику AI-координации
- Round-robin выбор агентов
- Email-диагностику
- AAL (Agent Activity Log) сохранение
- Telegram-уведомления
- Повторные попытки

### 4.3. Импорты внутри функций
Почти во всех функциях `anchor_engine.py` используется паттерн:
```python
from models import Session as _Sess, User as _Usr
```
Это замедляет выполнение (импорт при каждом вызове) и затрудняет статический анализ. Импорты должны быть наверху файла.

## 5. ✅ Что сделано хорошо (What's Done Well)

1. **Атомарные операции с токенами** — `UPDATE ... RETURNING` с `lock_timeout` — отличное решение для предотвращения race conditions.
2. **3-уровневая система транскрипции** (Groq → OpenAI → Google Speech Recognition) — хороший fallback.
3. **HMAC-SHA256 для unsubscribe токенов и HMAC-SHA512 для NowPayments** — правильный подход к безопасности.
4. **Rate limiting** (200 req/60s API, 10 req/60s auth, 20 msg/60s Telegram) — грамотная защита.
5. **Проверка IP Yookassa** через официальный whitelist.
6. **Гендерная коррекция русского текста** в `anchor_engine.py` — интересная и полезная фича.
7. **i18n система** — полноценная поддержка RU/EN с автоопределением языка по кириллице.
8. **Dedup-кэш в `handlers.py`** — предотвращает повторную обработку одинаковых сообщений.

---

## Непрочитанные файлы (не включены в анализ)

Следующие файлы ещё не были прочитаны и могут содержать дополнительные проблемы:
- `main.py` (строки 4001-14614)
- `anchor_engine.py` (строки 2001-28989)
- `ai_integration/` (25 файлов: `autonomous_agent.py`, `chat.py`, `tools.py`, `memory.py` и др.)
- `reminder_service.py`, `auto_post_service.py`, `migrations.py`
- `tests/conftest.py`
- Шаблоны и статические файлы
