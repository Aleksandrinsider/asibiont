# Защита данных подписок от потери

## Проблема
При деплое или сбое базы данных существовал риск потери информации о тарифах пользователей, что критично для реальных платных подписок.

## Решение

### 1. Таблица `payment_history` (models.py)
Постоянный журнал всех операций с подписками:
- **user_id** - ID пользователя
- **action** - тип операции (payment, subscription_activated, tier_change, promo_used, migration, manual_change)
- **tier** - тариф (BRONZE/SILVER/GOLD)
- **start_date, end_date** - период действия
- **payment_id** - ID платежа из Yookassa
- **amount** - сумма платежа
- **details** - дополнительная информация (JSON)

### 2. Автоматическое логирование (main.py, subscription_service.py)

#### При платеже через Yookassa (main.py:yookassa_webhook)
```python
payment_history = PaymentHistory(
    user_id=user.id,
    telegram_username=user.username,
    action='payment',
    tier=tier_enum,
    amount=payment['amount']['value'],
    payment_id=payment['id'],
    duration_days=30,
    start_date=subscription.start_date,
    end_date=subscription.end_date,
    details=json.dumps({'payment_method': ..., 'status': ...})
)
```

#### При активации подписки (subscription_service.py:activate_subscription)
```python
payment_history = PaymentHistory(
    user_id=user.id,
    telegram_username=user.username,
    action='subscription_activated',
    tier=tier_enum,
    duration_days=duration_days,
    start_date=start_date,
    end_date=end_date,
    details=json.dumps({'plan': plan, 'method': 'activate_subscription'})
)
```

### 3. Автоматическое восстановление (restore_subscriptions.py)

Запускается **при каждом старте приложения** (main.py после миграций):

#### Алгоритм восстановления:
1. **Из payment_history** (приоритет):
   - Ищет последнюю активную запись для каждого пользователя
   - Проверяет, не истекла ли подписка
   - Восстанавливает `user.subscription_tier`

2. **Из subscriptions** (резервный):
   - Проверяет активные подписки
   - Синхронизирует `user.subscription_tier` с `subscription.tier`

#### Проверка целостности:
```python
def check_database_integrity():
    # Находит пользователей с несоответствием между:
    # - user.subscription_tier
    # - subscription.tier
    # - payment_history (последняя активная запись)
```

### 4. Миграция существующих данных (migrate_existing_subscriptions.py)

Одноразовый скрипт для переноса существующих подписок в `payment_history`:
```bash
python migrate_existing_subscriptions.py
```

Результат:
```
✅ Мигрировано: 2
   - aleksandrinsider (bronze до 2026-02-15)
   - sportfan3 (gold до 2027-01-17)
```

### 5. Утилиты для работы с историей (payment_logger.py)

```python
# Логирование изменений
log_subscription_change(
    user_id=user.id,
    telegram_username=user.username,
    action='tier_change',
    tier=SubscriptionTier.GOLD,
    ...
)

# Получение истории
history = get_user_payment_history(user_id, limit=10)

# Поиск последней активной подписки
latest = get_latest_active_subscription_from_history(user_id)
```

## Механизм при деплое

### Что происходит:
1. **Push на GitHub** → Railway автоматически деплоит
2. **База данных НЕ пересоздается** (PostgreSQL персистентна)
3. **При старте приложения**:
   ```
   a) Подключение к БД
   b) Создание недостающих таблиц (Base.metadata.create_all)
   c) Выполнение миграций
   d) 🔄 АВТОМАТИЧЕСКОЕ ВОССТАНОВЛЕНИЕ ТАРИФОВ (restore_subscriptions.py)
   e) Запуск приложения
   ```

### Защита данных:
- ✅ `Base.metadata.create_all()` **не удаляет** существующие таблицы и данные
- ✅ Все подписки логируются в `payment_history` автоматически
- ✅ При старте происходит проверка и восстановление
- ✅ Двойная защита: `subscriptions` + `payment_history`

## Что защищено

### ✅ При новом платеже:
- Создается/обновляется запись в `subscriptions`
- Обновляется `user.subscription_tier`
- **Автоматически логируется** в `payment_history`

### ✅ При активации через промокод:
- Создается/обновляется `subscriptions`
- Обновляется `user.subscription_tier`
- **Автоматически логируется** в `payment_history` с action='promo_used'

### ✅ При смене тарифа:
- Обновляются `subscriptions` и `user.subscription_tier`
- **Логируется** в `payment_history` с action='tier_change'

### ✅ При сбросе базы:
- Восстановление из `payment_history` (если таблица сохранилась)
- Восстановление из `subscriptions` (если сохранилась)
- Автоматическое восстановление при старте

## Тестирование

### Проверка текущего состояния:
```bash
python restore_subscriptions.py
```

### Миграция существующих подписок:
```bash
python migrate_existing_subscriptions.py
```

### Просмотр истории пользователя:
```python
from payment_logger import get_user_payment_history
history = get_user_payment_history(user_id=3)
```

## Итог

Реальные пользователи **НЕ ПОТЕРЯЮТ** свои тарифы потому что:

1. **Каждый платеж логируется** автоматически в `payment_history`
2. **При каждом старте** приложение восстанавливает тарифы из истории
3. **Двойная защита**: subscriptions + payment_history
4. **База данных не пересоздается** при деплое (PostgreSQL персистентна)
5. **Миграции только добавляют** новые таблицы/колонки, не удаляют данные

### Исключения:
Данные могут быть потеряны только если:
- Полностью удалена база данных на Railway (требует ручного действия)
- Выполнен DROP DATABASE вручную
- Полная очистка volume на Railway

В этих случаях восстановление возможно из:
- Yookassa API (история платежей хранится у них)
- Backup базы данных (если настроен)
