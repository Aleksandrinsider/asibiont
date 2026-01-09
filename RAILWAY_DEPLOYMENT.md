# 🚀 Production Deployment Checklist для Railway

## 📋 Предварительная подготовка

### ✅ Завершено
- [x] Procfile настроен (`web: python main.py`)
- [x] railway.json создан с конфигурацией
- [x] requirements.txt содержит все зависимости
- [x] Health check endpoint работает (`/health`)
- [x] Webhook endpoint настроен
- [x] Валидация всех обязательных ENV переменных
- [x] Обработка исключений во всех критичных местах
- [x] Graceful shutdown реализован
- [x] Логирование настроено
- [x] Admin endpoints защищены secret ключом
- [x] Нет хардкода секретов в коде

## 🔐 Переменные окружения Railway

### **Обязательные** (REQUIRED)

```env
# Database
DATABASE_URL=postgresql://user:password@host:port/dbname

# Telegram
TELEGRAM_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_BOT_USERNAME=YourBot_bot
WEBHOOK_URL=https://your-app.railway.app

# AI
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx

# Redis
REDIS_URL=redis://default:password@host:port

# Security
ENCRYPTION_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SESSION_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### **Рекомендуемые** (RECOMMENDED)

```env
# Admin
ADMIN_SECRET=your-secure-random-string-here

# Web App
WEB_APP_URL=https://your-app.railway.app
```

### **Опциональные** (OPTIONAL)

```env
# Payments (если используется Yookassa)
YOOKASSA_SHOP_ID=123456
YOOKASSA_SECRET_KEY=live_xxxxxxxxxxxxx
YOOKASSA_WEBHOOK_URL=https://your-app.railway.app/webhook/yookassa

# Development
FREE_ACCESS_MODE=false
CURRENT_DATE=2026-01-10T00:00:00

# Reminders (опционально, есть defaults)
DAILY_REPORT_HOUR=22
PROACTIVE_CHECK_INTERVAL_MINUTES=30
OVERDUE_CHECK_INTERVAL_MINUTES=15
PROACTIVE_CHECK_AHEAD_MINUTES=60
```

## 📦 Шаги деплоя на Railway

### 1. Создание проекта
```bash
# В Railway Dashboard:
1. New Project → Deploy from GitHub repo
2. Выберите ваш репозиторий
3. Railway автоматически определит Python проект
```

### 2. Добавление сервисов

#### PostgreSQL
```bash
1. Add New Service → Database → PostgreSQL
2. Railway автоматически создаст DATABASE_URL
```

#### Redis
```bash
1. Add New Service → Database → Redis
2. Railway автоматически создаст REDIS_URL
```

### 3. Настройка переменных окружения

```bash
# В Settings → Variables добавьте:
1. TELEGRAM_TOKEN - получите у @BotFather
2. TELEGRAM_BOT_USERNAME - ваш бот username
3. DEEPSEEK_API_KEY - API ключ от DeepSeek
4. WEBHOOK_URL - скопируйте из Railway domain
5. ENCRYPTION_KEY - сгенерируйте (см. ниже)
6. SESSION_SECRET - сгенерируйте (см. ниже)
7. ADMIN_SECRET - придумайте сложный пароль
8. WEB_APP_URL - скопируйте из Railway domain
```

### 4. Генерация ключей

#### ENCRYPTION_KEY
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

#### SESSION_SECRET
```python
import secrets
print(secrets.token_urlsafe(32))
```

#### ADMIN_SECRET
```bash
# Любая длинная случайная строка
# Например, через: openssl rand -hex 32
```

### 5. Настройка Telegram Bot

```bash
# Установите webhook через @BotFather или вручную:
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -d "url=https://your-app.railway.app"

# Установите domain для Telegram Login Widget:
# Отправьте @BotFather:
/setdomain
# Затем: your-app.railway.app
```

### 6. Проверка деплоя

```bash
# 1. Health check
curl https://your-app.railway.app/health
# Должно вернуть: OK

# 2. Проверка логов
# В Railway Dashboard → Deployments → View Logs

# 3. Проверка базы данных
# Используйте check_production.py для проверки

# 4. Тестирование бота
# Отправьте /start боту в Telegram
```

## 🔍 Проверка после деплоя

### Тестирование функциональности

- [ ] Health check endpoint работает
- [ ] Dashboard загружается
- [ ] Telegram Login Widget работает
- [ ] Telegram бот отвечает на сообщения
- [ ] AI агент создает задачи
- [ ] Задачи сохраняются в БД
- [ ] Напоминания работают
- [ ] Redis кеш работает
- [ ] Платежи обрабатываются (если настроено)

### Безопасность

- [ ] ADMIN_SECRET установлен (не дефолтный)
- [ ] ENCRYPTION_KEY установлен
- [ ] SESSION_SECRET установлен
- [ ] Admin endpoints защищены
- [ ] HTTPS работает (автоматически в Railway)

### Производительность

- [ ] Логи не показывают ошибок
- [ ] Время отклика < 500ms
- [ ] База данных подключается корректно
- [ ] Redis подключается корректно

## 🚨 Troubleshooting

### Проблема: "DATABASE_URL is required"
**Решение:** Убедитесь, что PostgreSQL добавлен в проект и переменная `DATABASE_URL` появилась автоматически.

### Проблема: "ENCRYPTION_KEY is required"
**Решение:** Сгенерируйте ключ и добавьте в Variables:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### Проблема: Webhook не работает
**Решение:** 
1. Проверьте WEBHOOK_URL в Variables
2. Установите webhook вручную через Telegram API
3. Проверьте логи Railway на ошибки

### Проблема: Redis connection failed
**Решение:** Убедитесь, что Redis добавлен в проект Railway и REDIS_URL установлен.

### Проблема: Telegram Login Widget не работает
**Решение:** 
1. Установите domain через @BotFather: `/setdomain`
2. Укажите Railway domain без https://
3. Подождите несколько минут для propagation

## 📊 Мониторинг

### Логи
```bash
# В Railway Dashboard:
Deployments → Active → View Logs
```

### Метрики
```bash
# Проверка через admin endpoints:
https://your-app.railway.app/health

# Проверка базы данных:
python check_production.py
```

### Алерты
- Railway автоматически уведомляет о падении сервиса
- Настройте мониторинг через Railway Dashboard → Settings → Notifications

## 🔄 Обновление приложения

```bash
# 1. Commit changes
git add .
git commit -m "Update: description"

# 2. Push to GitHub
git push origin main

# 3. Railway автоматически задеплоит новую версию
```

## 🧹 Очистка продакшен данных

### Через API (если endpoints работают)
```bash
python clear_production.py
```

### Напрямую через DATABASE_URL
```bash
python clear_production_direct.py
```

### Проверка состояния
```bash
python check_production.py
```

## ✅ Финальный чеклист

- [ ] Все обязательные ENV переменные установлены
- [ ] Health check возвращает OK
- [ ] Telegram бот отвечает на сообщения
- [ ] Dashboard доступен и работает
- [ ] Telegram Login Widget работает
- [ ] Задачи создаются и сохраняются
- [ ] Redis кеш работает
- [ ] Логи не показывают критичных ошибок
- [ ] Admin secret изменен с дефолтного
- [ ] Backup strategy определена

## 📚 Документация

- [Railway Docs](https://docs.railway.app/)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [DeepSeek API](https://platform.deepseek.com/docs)
- [Project README](README.md)
- [Clearing Production Data](CLEAR_PRODUCTION_README.md)

---

**Последнее обновление:** 2026-01-10  
**Статус проекта:** ✅ Готов к продакшену