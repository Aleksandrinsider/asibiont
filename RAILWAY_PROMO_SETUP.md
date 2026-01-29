# Инструкция по добавлению промокодов в Railway PostgreSQL

## Способ 1: Через Railway CLI (рекомендуется)

### Шаг 1: Получите DATABASE_URL из Railway

```bash
railway variables
```

Скопируйте значение `DATABASE_URL` или `DATABASE_PUBLIC_URL`

### Шаг 2: Установите переменную окружения

**Windows PowerShell:**
```powershell
$env:DATABASE_URL = "postgresql://..."
```

**Windows CMD:**
```cmd
set DATABASE_URL=postgresql://...
```

**Linux/Mac:**
```bash
export DATABASE_URL="postgresql://..."
```

### Шаг 3: Запустите скрипт

```bash
python add_promo_to_railway.py
```

## Способ 2: Через Railway Shell

### Шаг 1: Подключитесь к Railway

```bash
railway link
```

### Шаг 2: Откройте Railway Shell

```bash
railway run python add_promo_to_railway.py
```

## Способ 3: Через SQL напрямую в Railway PostgreSQL

### Шаг 1: Подключитесь к БД через Railway CLI

```bash
railway connect postgres
```

### Шаг 2: Выполните SQL запросы

```sql
-- LIGHT1
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('LIGHT1', 'LIGHT', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW());

-- STD2026XPRO
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('STD2026XPRO', 'STANDARD', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW());

-- PREM2026ELITE
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('PREM2026ELITE', 'PREMIUM', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW());

-- VIPACCESS2026 (VIP - только 1 использование)
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('VIPACCESS2026', 'PREMIUM', 100, 365, '2026-12-31 23:59:59+00', 1, 0, '[]', NOW());
```

### Шаг 3: Проверьте промокоды

```sql
SELECT code, tier, discount_percent, duration_days, max_uses, used_count 
FROM promo_codes 
ORDER BY created_at DESC;
```

## Способ 4: Через Railway Dashboard

1. Откройте проект в Railway Dashboard
2. Перейдите в PostgreSQL
3. Нажмите "Query"
4. Вставьте SQL запросы из Способа 3
5. Нажмите "Run"

## Проверка промокодов

После добавления проверьте промокоды в боте:

```
/promo
```

Должны отображаться все 4 промокода:
- LIGHT1
- STD2026XPRO
- PREM2026ELITE
- VIPACCESS2026

## Устранение проблем

### Ошибка: "duplicate key value"

Промокод уже существует. Проверьте существующие промокоды:

```sql
SELECT * FROM promo_codes WHERE code IN ('LIGHT1', 'STD2026XPRO', 'PREM2026ELITE', 'VIPACCESS2026');
```

### Ошибка подключения к БД

Проверьте переменную DATABASE_URL:

```bash
railway variables | grep DATABASE
```

Убедитесь, что используете правильный URL (может быть DATABASE_URL или DATABASE_PUBLIC_URL)
