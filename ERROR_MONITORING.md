# Error Monitoring & Auto-Fix System

Система автоматического мониторинга и исправления ошибок для проекта.

## Компоненты

### 1. error_monitor.py
- Мониторинг Railway логов в реальном времени
- Распознавание типов ошибок по паттернам
- Генерация промптов для исправления
- Система уведомлений

### 2. auto_fix_worker.py
- Worker для автоматической обработки ошибок
- Интеграция с Telegram уведомлениями
- Создание GitHub Issues для критических ошибок
- Запросы исправлений через API

## Использование

### Запуск мониторинга
```bash
# Постоянный мониторинг
python error_monitor.py start

# Одноразовая проверка
python error_monitor.py check

# Просмотр ожидающих исправлений
python error_monitor.py fixes
```

### Запуск AutoFix Worker
```bash
python auto_fix_worker.py
```

### Интеграция с существующим проектом

Добавить в main.py:
```python
# В начало файла
from error_monitor import ErrorMonitor

# В обработчик ошибок
@app.middleware('http')
async def error_monitoring_middleware(request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        # Логирование ошибки для мониторинга
        logger.error(f"Request error: {str(e)}", exc_info=True)
        raise
```

## Типы отслеживаемых ошибок

1. **missing_parameter** (HIGH) - Отсутствующие параметры функций
2. **key_error** (MEDIUM) - Ошибки доступа к ключам словарей
3. **attribute_error** (MEDIUM) - Отсутствующие атрибуты объектов
4. **import_error** (HIGH) - Ошибки импорта модулей
5. **syntax_error** (CRITICAL) - Синтаксические ошибки

## Автоматическое исправление

Поддерживается для:
- ✅ missing_parameter - добавление недостающих параметров
- ✅ import_error - исправление импортов и зависимостей
- ❌ syntax_error - требует ручного вмешательства

## Уведомления

- 📁 **error_reports.json** - История всех найденных ошибок
- 📁 **pending_fixes.json** - Ожидающие исправления
- 📱 **Telegram** - Мгновенные уведомления
- 🐛 **GitHub Issues** - Для критических ошибок

## Настройка Railway CLI

Для работы с логами Railway:
```bash
npm install -g @railway/cli
railway login
railway link
```

## Ограничения

⚠️ **Безопасность:** Автоматические изменения требуют проверки
⚠️ **Контекст:** Некоторые ошибки требуют глубокого понимания кода
⚠️ **API лимиты:** Ограничения на частоту запросов к Copilot API