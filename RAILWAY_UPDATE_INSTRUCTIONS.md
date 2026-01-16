# Инструкция по обновлению тарифов в production БД

## Проблема
Локальный запуск `python update_production_tiers.py` не работает из-за проблем с подключением к Railway PostgreSQL.

## Решение через Railway CLI

### Вариант 1: Через Railway CLI напрямую
```bash
# 1. Установить Railway CLI (если еще не установлен)
npm install -g @railway/cli

# 2. Авторизоваться
railway login

# 3. Подключиться к проекту
railway link

# 4. Запустить Python скрипт в контексте Railway
railway run python update_production_tiers.py
```

### Вариант 2: Через SQL запрос напрямую
```bash
# Подключиться к БД Railway
railway connect postgres

# Затем выполнить SQL
UPDATE users 
SET subscription_tier = 'BRONZE'
WHERE telegram_id IN (111111, 444444);

UPDATE users 
SET subscription_tier = 'SILVER'
WHERE telegram_id IN (222222, 555555);

UPDATE users 
SET subscription_tier = 'GOLD'
WHERE telegram_id = 333333;
```

### Вариант 3: Запустить скрипт на самом сервере Railway
```bash
# Развернуть временный скрипт на Railway
railway up --detach update_production_tiers.py
```

## Проверка результата
После обновления можно проверить через Railway dashboard или подключившись к БД:
```sql
SELECT telegram_id, username, subscription_tier 
FROM users 
WHERE telegram_id IN (111111, 222222, 333333, 444444, 555555);
```

## Mapping тарифов для тестовых пользователей
- sportfan1 (111111) → BRONZE
- sportfan2 (222222) → SILVER  
- sportfan3 (333333) → GOLD
- sportfan4 (444444) → BRONZE
- sportfan5 (555555) → SILVER
