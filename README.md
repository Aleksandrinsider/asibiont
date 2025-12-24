# AI Task Management Telegram Bot

Этот бот позволяет общаться с ИИ на естественном языке для управления задачами, получения напоминаний и совершения платежей. Все взаимодействие происходит через диалог, без команд.

## Функции

- Добавление задач через разговор
- Перечисление задач
- Завершение задач
- Установка напоминаний
- Платежи через Yookassa

## Установка

1. Установите зависимости: `pip install -r requirements.txt`
2. Настройте переменные окружения в .env:
   - TELEGRAM_TOKEN: Токен бота от BotFather
   - DEEPSEEK_API_KEY: API ключ от DeepSeek
   - REDIS_URL: URL Redis базы
   - DATABASE_URL: URL PostgreSQL базы
   - YOOKASSA_SHOP_ID: ID магазина Yookassa
   - YOOKASSA_SECRET_KEY: Секретный ключ Yookassa
   - WEBHOOK_URL: URL вебхука для Railway
3. Для локального тестирования: `LOCAL=1 python main.py`
4. Для продакшена на Railway: разверните с переменными окружения

## Развертывание на Railway

1. Создайте проект на Railway
2. Подключите GitHub репозиторий
3. Установите переменные окружения
4. Railway автоматически развернет бота

## Тестирование

Запустите `python test_dialogue.py` для симуляции диалога.