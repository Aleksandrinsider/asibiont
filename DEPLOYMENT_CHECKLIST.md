# ЧЕКЛИСТ ДЕПЛОЯ НА PRODUCTION

## 📋 ПЕРЕД ДЕПЛОЕМ

### 1. Environment Variables
- [ ] `TELEGRAM_TOKEN` - получен от @BotFather
- [ ] `TELEGRAM_BOT_USERNAME` - установлен корректно
- [ ] `DATABASE_PUBLIC_URL` - Railway PostgreSQL URL
- [ ] `REDIS_URL` - Redis connection string
- [ ] `DEEPSEEK_API_KEY` - валидный API ключ
- [ ] `ENCRYPTION_KEY` - 32-byte random key
- [ ] `SESSION_SECRET` - случайная строка
- [ ] `ADMIN_SECRET` - секретный ключ для админки
- [ ] `YOOKASSA_SHOP_ID` - ID магазина
- [ ] `YOOKASSA_SECRET_KEY` - секретный ключ
- [ ] `WEBHOOK_URL` - https://your-domain.railway.app/webhook
- [ ] `WEB_APP_URL` - https://asibiont.ru
- [ ] `LOCAL=0` (production mode)
- [ ] `FREE_ACCESS_MODE=False` (требовать подписку)

### 2. Railway Configuration
- [ ] Проект создан в Railway
- [ ] PostgreSQL addon добавлен
- [ ] Redis addon добавлен
- [ ] Environment variables настроены
- [ ] Custom domain подключен (optional)
- [ ] SSL certificate активен

### 3. Telegram Bot Setup
- [ ] Бот создан через @BotFather
- [ ] Описание бота установлено
- [ ] Команды настроены (/start, /help)
- [ ] Webhook URL установлен: https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-domain/webhook
- [ ] Проверка webhook: https://api.telegram.org/bot<TOKEN>/getWebhookInfo

### 4. Database
- [ ] DATABASE_PUBLIC_URL корректный
- [ ] Миграции применятся автоматически при старте
- [ ] Connection pool настроен (50 connections)
- [ ] Backup настроен (Railway auto-backup)

### 5. Yookassa Payment Gateway
- [ ] Магазин создан в Yookassa
- [ ] SHOP_ID получен
- [ ] SECRET_KEY получен
- [ ] Webhook URL зарегистрирован: https://your-domain/yookassa-webhook
- [ ] Тестовый платеж проведен
- [ ] Production mode активирован

## 🚀 ДЕПЛОЙ

### Шаг 1: Push to Railway
```bash
git init
git add .
git commit -m "Initial production deployment"
railway link
railway up
```

### Шаг 2: Проверка логов
```bash
railway logs
```

Должны увидеть:
```
✅ Database connection successful
✅ Database tables created or already exist
✅ Database migrations completed
Bot created successfully
ReminderService initialized
App created successfully
```

### Шаг 3: Установка Webhook
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook?url=https://your-domain.railway.app/webhook"
```

Ответ должен быть:
```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

### Шаг 4: Проверка Webhook
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo"
```

Проверить:
- `url` указывает на ваш домен
- `has_custom_certificate` = false
- `pending_update_count` = 0
- `last_error_date` отсутствует

## 🧪 ТЕСТИРОВАНИЕ ПОСЛЕ ДЕПЛОЯ

### 1. Базовая функциональность
- [ ] Бот отвечает на /start
- [ ] Создание задачи работает: "Напомни купить молоко завтра в 10:00"
- [ ] Список задач показывается: "Покажи мои задачи"
- [ ] Завершение задачи: "Я купил молоко"
- [ ] AI отвечает естественно без списков

### 2. Профиль
- [ ] Обновление профиля: "Я живу в Москве"
- [ ] Поиск партнеров: "Найди мне партнеров"

### 3. Подписки
- [ ] Проверка статуса: "Моя подписка"
- [ ] Страница тарифов открывается: https://asibiont.ru/subscription-tiers
- [ ] Промокод применяется на сайте

### 4. Платежи (критично!)
- [ ] Тестовый платеж через Yookassa
- [ ] Webhook получен от Yookassa
- [ ] Подписка активируется после оплаты
- [ ] История платежей сохраняется

### 5. Напоминания
- [ ] Создать задачу на ближайшее время
- [ ] Дождаться напоминания
- [ ] Проверить проактивные сообщения

## 🔍 МОНИТОРИНГ

### Railway Dashboard
- [ ] CPU usage < 80%
- [ ] Memory usage < 500MB
- [ ] Database connections < 40
- [ ] No crash loops

### Telegram Bot
- [ ] Webhook delivery rate > 95%
- [ ] Response time < 3s
- [ ] No error messages to users

### Database
- [ ] Connection pool не переполняется
- [ ] Query time < 100ms
- [ ] No deadlocks

## 🐛 TROUBLESHOOTING

### Бот не отвечает
1. Проверить webhook: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`
2. Проверить логи Railway: `railway logs`
3. Проверить health endpoint: `curl https://your-domain.railway.app/health`

### Ошибки базы данных
1. Проверить DATABASE_URL: `railway variables`
2. Проверить connection count: логи должны показать pool stats
3. Рестарт: `railway restart`

### AI не работает
1. Проверить DEEPSEEK_API_KEY
2. Проверить логи: должны быть запросы к DeepSeek API
3. Проверить rate limits DeepSeek

### Платежи не проходят
1. Проверить Yookassa webhook: dashboard.yookassa.ru
2. Проверить логи `/yookassa-webhook`
3. Проверить SHOP_ID и SECRET_KEY

## 📊 МЕТРИКИ ДЛЯ ОТСЛЕЖИВАНИЯ

### День 1
- [ ] Кол-во активаций бота
- [ ] Кол-во созданных задач
- [ ] Кол-во ошибок в логах
- [ ] Uptime > 99%

### Неделя 1
- [ ] DAU (Daily Active Users)
- [ ] Кол-во платежей
- [ ] Средний response time
- [ ] Crash rate < 1%

### Месяц 1
- [ ] Retention rate
- [ ] Conversion rate (free -> paid)
- [ ] Churn rate
- [ ] NPS score

## 🎯 КРИТЕРИИ УСПЕХА

✅ Деплой считается успешным если:
1. Бот отвечает на команды
2. AI создает задачи через естественный язык
3. Напоминания приходят вовремя
4. Платежи обрабатываются корректно
5. Нет критических ошибок в логах
6. Uptime > 99% в первые 24 часа

## 🆘 ROLLBACK PLAN

Если что-то пошло не так:
```bash
# Откатить к предыдущей версии
railway rollback

# Или деплой specific commit
railway deploy --service <service-id> --commit <commit-hash>

# Удалить webhook
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

## 📞 КОНТАКТЫ

Поддержка: @aleksandrinsider
Railway Support: https://railway.app/help
Telegram Bot API: https://core.telegram.org/bots/api
Yookassa Support: help@yookassa.ru

---

**ВАЖНО:** Не забудьте сделать backup `.env` файла перед деплоем!
