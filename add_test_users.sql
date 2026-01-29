-- SQL скрипт для добавления тестовых пользователей в Railway БД
-- Запустите через Railway shell: railway run psql < add_test_users.sql

-- Спортивные пользователи
INSERT INTO users (telegram_id, username, subscription_tier, created_at) VALUES
(1000001, 'sport_alex', 'LIGHT', NOW()),
(1000002, 'sport_maria', 'STANDARD', NOW()),
(1000003, 'sport_ivan', 'PREMIUM', NOW()),
(1000004, 'sport_olga', 'LIGHT', NOW()),
(1000005, 'sport_dmitry', 'STANDARD', NOW())
ON CONFLICT (telegram_id) DO NOTHING;

-- Бизнес пользователи
INSERT INTO users (telegram_id, username, subscription_tier, created_at) VALUES
(2000001, 'biz_anna', 'PREMIUM', NOW()),
(2000002, 'biz_sergey', 'LIGHT', NOW()),
(2000003, 'biz_elena', 'STANDARD', NOW()),
(2000004, 'biz_maxim', 'PREMIUM', NOW()),
(2000005, 'biz_victoria', 'LIGHT', NOW())
ON CONFLICT (telegram_id) DO NOTHING;

-- Профили для спортивных пользователей
INSERT INTO user_profiles (user_id, interests, skills, goals, created_at)
SELECT id, 
    CASE telegram_id
        WHEN 1000001 THEN 'футбол, баскетбол, волейбол'
        WHEN 1000002 THEN 'бег, йога, пилатес'
        WHEN 1000003 THEN 'теннис, плавание, велоспорт'
        WHEN 1000004 THEN 'фитнес, кроссфит, бодибилдинг'
        WHEN 1000005 THEN 'хоккей, биатлон, лыжи'
    END,
    '', '', NOW()
FROM users 
WHERE telegram_id IN (1000001, 1000002, 1000003, 1000004, 1000005)
ON CONFLICT (user_id) DO NOTHING;

-- Профили для бизнес пользователей
INSERT INTO user_profiles (user_id, interests, skills, goals, created_at)
SELECT id,
    CASE telegram_id
        WHEN 2000001 THEN 'стартапы, маркетинг, продажи'
        WHEN 2000002 THEN 'инвестиции, финансы, криптовалюта'
        WHEN 2000003 THEN 'управление проектами, agile, scrum'
        WHEN 2000004 THEN 'e-commerce, онлайн-торговля, логистика'
        WHEN 2000005 THEN 'HR, рекрутинг, обучение персонала'
    END,
    '', '', NOW()
FROM users
WHERE telegram_id IN (2000001, 2000002, 2000003, 2000004, 2000005)
ON CONFLICT (user_id) DO NOTHING;

-- Подписки для всех пользователей
INSERT INTO subscriptions (user_id, telegram_id, telegram_username, username, status, plan, tier, start_date, end_date, login_count, created_at)
SELECT 
    u.id, 
    u.telegram_id, 
    u.username,
    u.username,
    'active',
    'yearly',
    u.subscription_tier,
    NOW(),
    NOW() + INTERVAL '365 days',
    1,
    NOW()
FROM users u
WHERE u.telegram_id IN (1000001, 1000002, 1000003, 1000004, 1000005, 2000001, 2000002, 2000003, 2000004, 2000005)
ON CONFLICT (user_id) DO NOTHING;

-- Проверяем результат
SELECT 
    u.username, 
    u.subscription_tier as user_tier,
    s.tier as sub_tier,
    up.interests
FROM users u
LEFT JOIN subscriptions s ON s.user_id = u.id
LEFT JOIN user_profiles up ON up.user_id = u.id
WHERE u.telegram_id IN (1000001, 1000002, 1000003, 1000004, 1000005, 2000001, 2000002, 2000003, 2000004, 2000005)
ORDER BY u.telegram_id;
