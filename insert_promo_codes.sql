-- ============================================================
-- SQL скрипт для добавления промокодов в Railway PostgreSQL
-- ============================================================
-- Скопируйте и выполните в Railway PostgreSQL Query Editor
-- или через: railway connect postgres
-- ============================================================

-- 1. LIGHT1 - месяц подписки LIGHT
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('LIGHT1', 'LIGHT', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW())
ON CONFLICT (code) DO NOTHING;

-- 2. STD2026XPRO - месяц подписки STANDARD
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('STD2026XPRO', 'STANDARD', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW())
ON CONFLICT (code) DO NOTHING;

-- 3. PREM2026ELITE - месяц подписки PREMIUM
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('PREM2026ELITE', 'PREMIUM', 100, 30, '2026-12-31 23:59:59+00', NULL, 0, '[]', NOW())
ON CONFLICT (code) DO NOTHING;

-- 4. VIPACCESS2026 - ГОД подписки PREMIUM (только 1 использование!)
INSERT INTO promo_codes 
(code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
VALUES 
('VIPACCESS2026', 'PREMIUM', 100, 365, '2026-12-31 23:59:59+00', 1, 0, '[]', NOW())
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- Проверка добавленных промокодов
-- ============================================================

SELECT 
    code,
    tier,
    discount_percent || '%' as discount,
    duration_days || ' дней' as duration,
    CASE 
        WHEN max_uses IS NULL THEN '∞'
        ELSE max_uses::text
    END as max_uses,
    used_count,
    to_char(expires_at, 'DD.MM.YYYY') as expires
FROM promo_codes 
WHERE code IN ('LIGHT1', 'STD2026XPRO', 'PREM2026ELITE', 'VIPACCESS2026')
ORDER BY 
    CASE tier
        WHEN 'LIGHT' THEN 1
        WHEN 'STANDARD' THEN 2
        WHEN 'PREMIUM' THEN 3
    END,
    duration_days;

-- ============================================================
-- Дополнительные запросы для проверки
-- ============================================================

-- Все промокоды в системе
SELECT code, tier, duration_days, used_count, 
       COALESCE(max_uses::text, '∞') as limit
FROM promo_codes 
ORDER BY created_at DESC;

-- Статистика по тарифам
SELECT 
    tier,
    COUNT(*) as total_codes,
    SUM(used_count) as total_uses
FROM promo_codes
GROUP BY tier;
