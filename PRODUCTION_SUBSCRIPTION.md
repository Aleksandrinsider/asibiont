"""
Инструкция по продлению подписки в production

Так как локальная БД (SQLite) отличается от production БД (PostgreSQL на Railway),
нужно продлить подписку в production базе.

ВАРИАНТ 1: Через веб-интерфейс (самый простой)
1. Открой https://asibiont.up.railway.app/dashboard
2. Авторизуйся через Telegram
3. Нажми кнопку "продлить" в профиле
4. Это создаст платеж в Yookassa на 3000₽
5. После успешной оплаты webhook автоматически продлит подписку

ВАРИАНТ 2: Через Railway CLI (для разработчика)
1. Установи Railway CLI: npm i -g @railway/cli
2. Войди: railway login
3. Подключись к проекту: railway link
4. Открой консоль БД: railway run psql $DATABASE_URL
5. Выполни SQL:
   
   UPDATE subscriptions 
   SET end_date = end_date + INTERVAL '30 days'
   WHERE user_id = (SELECT id FROM users WHERE telegram_id = 146333757);
   
   SELECT * FROM subscriptions WHERE user_id = (SELECT id FROM users WHERE telegram_id = 146333757);

ВАРИАНТ 3: Через Railway веб-интерфейс
1. Открой https://railway.app/
2. Перейди в свой проект
3. Открой PostgreSQL сервис
4. Перейди в "Data" или "Query"
5. Выполни тот же SQL запрос из варианта 2

ВАРИАНТ 4: Через тестовый эндпоинт (только если LOCAL=1 не работает)
1. Измени в main.py строку if not LOCAL: на if not False:
2. Задеплой на Railway
3. Открой https://asibiont.up.railway.app/test_payment
4. Верни изменение обратно

ВАЖНО: 
- Локальная база (SQLite) используется только для разработки
- Production база (PostgreSQL) на Railway - это та, которую видят пользователи
- Подписка в локальной БД уже продлена до 01.04.2026
- Нужно продлить подписку в production БД
