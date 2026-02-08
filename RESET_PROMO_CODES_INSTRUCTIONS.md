# Как обнулить промокоды в Railway PostgreSQL

Создано 3 способа обнуления промокодов:

## Способ 1: Python скрипт с SQLAlchemy (рекомендуется)

1. Найди DATABASE_URL в Railway:
   - Открой проект на railway.app
   - Variables → DATABASE_URL (должна начинаться с `postgresql://`)
   
2. Запусти скрипт:
```bash
python reset_promo_codes_sqlalchemy.py
```

3. Введи полный DATABASE_URL когда попросит
4. Подтверди действие вводом `yes`

## Способ 2: Railway CLI (самый простой)

Если установлен Railway CLI:

```bash
# Подключись к БД
railway connect

# Или выполни напрямую
railway run psql
```

Затем в psql выполни:
```sql
UPDATE promo_codes
SET is_used = FALSE, used_count = 0, used_by_users = '[]'
WHERE is_used = TRUE OR used_count > 0 OR used_by_users != '[]';

SELECT code, is_used, used_count FROM promo_codes;
```

## Способ 3: Прямое подключение через psql

Если у тебя установлен PostgreSQL клиент:

```bash
psql postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@[HOST]:[PORT]/railway
```

Замени [HOST] и [PORT] на твои значения из Railway.

Затем выполни команды из `reset_promo_codes.sql`

## Что делает скрипт:

- `is_used` = FALSE (промокод не использован)
- `used_count` = 0 (счетчик использований = 0)  
- `used_by_users` = '[]' (список пользователей пустой)

## Формат DATABASE_URL для Railway:

```
postgresql://postgres:PASSWORD@HOST:PORT/railway
```

Где:
- PASSWORD: `upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML`
- HOST: обычно `monorail.proxy.rlwy.net` или `postgres.railway.internal`
- PORT: обычно `5432` или другой (смотри в Railway Variables)
- DATABASE: обычно `railway`

## Проверка результата:

После выполнения все промокоды должны показывать:
```
code        | is_used | used_count | used_by_users
------------|---------|------------|---------------
SPORTLIGHT1 | f       | 0          | []
SPORTSTAND1 | f       | 0          | []
SPORTPREM1  | f       | 0          | []
...
```

✅ Теперь промокоды можно использовать снова!
