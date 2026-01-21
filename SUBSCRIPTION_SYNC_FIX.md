других данных в бд# Проблемы синхронизации данных между таблицами

## Обнаруженные проблемы

### 1. users.subscription_tier vs subscriptions.tier
**Обнаружено:** `users.subscription_tier` не совпадал с `subscriptions.tier` для 13 пользователей.

**Причина:** Скрипт изменял **только** `users.subscription_tier`, не трогая таблицу `subscriptions`.

**Источник истины:** `subscriptions.tier`

### 2. users.average_rating vs user_profiles.average_rating
**Обнаружено:** Поля `average_rating` и `rating_count` отсутствовали в таблице `users`, хотя код пытался их обновлять.

**Причина:** Модель `User` не содержала эти поля, но в `main.py` был код обновления несуществующих атрибутов.

**Источник истины:** `user_profiles.average_rating`

## Что было исправлено

### 1. Добавлена автоматическая синхронизация subscription_tier при старте (main.py:3908-3943)
```python
# Синхронизируем users.subscription_tier с subscriptions.tier при старте
active_subscriptions = session_db.query(Subscription).filter_by(status='active').all()
for sub in active_subscriptions:
    if user.subscription_tier != sub.tier:
        user.subscription_tier = sub.tier
        synced_count += 1
```

**Эффект:** При каждом запуске Railway автоматически синхронизирует тарифы.

### 2. Добавлена автоматическая синхронизация average_rating при старте (main.py:3945-3966)
```python
# Синхронизируем users.average_rating с user_profiles.average_rating
all_profiles = session_db.query(UserProfile).all()
for profile in all_profiles:
    if user.average_rating != profile.average_rating:
        user.average_rating = profile.average_rating
        user.rating_count = profile.rating_count
        rating_synced_count += 1
```

**Эффект:** При каждом запуске Railway автоматически синхронизирует рейтинги.

### 3. Добавлены поля в модель User (models.py:39-40)
```python
average_rating = Column(Integer, default=0)  # Synced from UserProfile
rating_count = Column(Integer, default=0)  # Synced from UserProfile
```

### 4. Миграция БД выполнена (migrate_add_rating_fields.py)
- Добавлены колонки `average_rating` и `rating_count` в таблицу `users`
- Синхронизированы данные из `user_profiles` для всех 21 пользователей

### 5. Исправлен endpoint восстановления подписки (main.py:2151-2158)
**Было:**
```python
user.subscription_tier = SubscriptionTier.GOLD
session_db.commit()
```

**Стало:**
```python
user.subscription_tier = SubscriptionTier.GOLD
subscription = session_db.query(Subscription).filter_by(user_id=user.id, status='active').first()
if subscription:
    subscription.tier = SubscriptionTier.GOLD
session_db.commit()
```

### 6. Уже правильно работающие места:
- ✅ `dashboard_handler` (main.py:1018-1034) — синхронизирует при каждом входе
- ✅ `subscription_service.py` (строки 90, 126) — обновляет обе таблицы
- ✅ Активация промокода (main.py:4468-4475) — обновляет обе таблицы

## Правила для subscription_tier

### ❌ НИКОГДА не делайте:
```python
# Плохо: меняет только users
user.subscription_tier = SubscriptionTier.GOLD
session.commit()
```

### ✅ ВСЕГДА делайте:
```python
# Хорошо: обновляет subscriptions (источник истины)
subscription = session.query(Subscription).filter_by(user_id=user.id).first()
subscription.tier = SubscriptionTier.GOLD
subscription.status = 'active'
# users.subscription_tier синхронизируется автоматически при старте или входе
session.commit()
```

### ✅ ИЛИ используйте service:
```python
from subscription_service import activate_subscription
activate_subscription(telegram_id, tier='gold', plan='monthly')
```

## Правила для average_rating

### ❌ НИКОГДА не делайте:
```python
# Плохо: меняет только users
user.average_rating = 8
session.commit()
```

### ✅ ВСЕГДА делайте:
```python
# Хорошо: обновляет user_profiles (источник истины)
profile = session.query(UserProfile).filter_by(user_id=user.id).first()
profile.average_rating = 8
profile.rating_count = 5
# users.average_rating синхронизируется автоматически при старте
session.commit()
```

### ✅ ИЛИ обновляйте ОБЕ таблицы одновременно:
```python
# Как в api_set_user_rating_handler (main.py:3628-3637)
user.average_rating = round(avg_rating)
user.rating_count = len(all_ratings)

profile = session.query(UserProfile).filter_by(user_id=user.id).first()
if profile:
    profile.average_rating = user.average_rating
    profil

### ✅ Полезные скрипты:
- `sync_subscription_tiers.py` — ручная синхронизация subscription_tier
- `check_db_integrity.py` — комплексная проверка целостности БД (10 проверок)
- `migrate_add_rating_fields.py` — миграция для добавления полей рейтинга
- `create_promo_codes.py` — создание промокодов
- `subscription_service.py` — основной сервис управления подписками

### ❌ Удалённые опасные скрипт
```

### ❌ Удалённые опасные скрипты:
- ❌ `set_gold_status.py` — изменял только users.subscription_tier
- ❌ `check_gold_users.py`, `check_subscriptions.py`, `check_db_connection.py` — временные для отладки
- ❌ `check_prod_status.py`, `check_partners.py`, `add_favorite.py` — временные для отладки
- ❌ `create_test_feed_posts.py` — временный для тестов

## Проверка состояния

### Комплексная проверка (рекомендуется):
```bash
python check_db_integrity.py
```

**Выполняет 10 проверок:**
# "✅ Synced N user ratings with profiles on startup"
```

## Итог

✅ **Исправлено:**
1. Добавлена автосинхронизация subscription_tier при старте
2. Добавлена автосинхронизация average_rating при старте
3. Добавлены поля average_rating и rating_count в модель User
4. Выполнена миграция БД для добавления колонок
5. Исправлен endpoint восстановления подписки
6. Создан скрипт комплексной проверки БД (10 проверок)

✅ **Защита от повторения:**
- Автосинхронизация subscription_tier каждый запуск
- Автосинхронизация average_rating каждый запуск
- Синхронизация при входе пользователя (subscription_tier)
- Правильная работа всех существующих endpoints

✅ **Проверено:**
- ✅ users.subscription_tier = subscriptions.tier (21 пользователь)
- ✅ users.average_rating = user_profiles.average_rating (21 пользователь)
- ✅ Все пользователи имеют профили
- ✅ Нет orphaned записей
- ✅ Нет дубликатов подписок
- ✅ Все foreign keys корректны

✅ **Документация:**
- Четкие правила работы с subscription_tier и average_rating
- Список безопасных способов изменения
- Инструкции по проверке целостности БД
from models import SessionLocal, Subscription, SubscriptionTier
session = SessionLocal()
sub = session.query(Subscription).filter_by(user_id=34).first()
sub.tier = SubscriptionTier.GOLD
session.commit()
# users.subscription_tier синхронизируется при следующем старте
```

## Скрипты для удаления

Следующие временные скрипты ОПАСНЫ и должны быть удалены:
- ❌ `set_gold_status.py` — меняет только users.subscription_tier
- ❌ `check_gold_users.py` — временный, для отладки
- ❌ `check_subscriptions.py` — временный, для отладки
- ❌ `check_db_connection.py` — временный, для отладки
- ❌ `check_prod_status.py` — временный, для отладки
- ❌ `check_partners.py` — временный, для отладки
- ❌ `add_favorite.py` — временный, для отладки
- ❌ `create_test_feed_posts.py` — временный, для тестов

✅ Оставить:
- `sync_subscription_tiers.py` — полезен для ручной синхронизации
- `create_promo_codes.py` — для создания промокодов
- `subscription_service.py` — основной сервис управления подписками

## Проверка состояния

### После внесения изменений:
```bash
# Перезапустите приложение на Railway
# Автоматическая синхронизация выполнится при старте

# Проверьте логи:
# "✅ Synced N user tiers with subscriptions on startup"
```

### Ручная синхронизация (если нужно):
```bash
python sync_subscription_tiers.py
```

### Проверка расхождений:
```bash
python check_subscriptions.py
# Должно показать 0 расхождений
```

## Итог

✅ **Исправлено:**
1. Добавлена автоматическая синхронизация при старте
2. Исправлен endpoint восстановления подписки
3. Создан скрипт для ручной синхронизации

✅ **Защита от повторения:**
- Автоматическая синхронизация каждый запуск
- Синхронизация при входе пользователя
- Правильная работа всех существующих endpoints

✅ **Документация:**
- Четкие правила работы с subscription_tier
- Список безопасных способов изменения тарифа
- Список скриптов для удаления
