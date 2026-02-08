-- SQL скрипт для обнуления промокодов в Railway PostgreSQL
-- Можно выполнить через Railway CLI: railway run psql -f reset_promo_codes.sql
-- Или скопировать команды и выполнить в psql

-- Показываем текущее состояние
SELECT 
    code,
    is_used,
    used_count,
    CASE 
        WHEN used_by_users = '[]' THEN 'no users'
        ELSE used_by_users
    END as users
FROM promo_codes
ORDER BY code;

-- Обнуляем все промокоды
UPDATE promo_codes
SET 
    is_used = FALSE,
    used_count = 0,
    used_by_users = '[]'
WHERE 
    is_used = TRUE 
    OR used_count > 0 
    OR used_by_users != '[]';

-- Показываем результат
SELECT 
    code,
    is_used,
    used_count,
    used_by_users
FROM promo_codes
ORDER BY code;

-- Должны увидеть все промокоды с:
-- is_used = f (false)
-- used_count = 0
-- used_by_users = []
