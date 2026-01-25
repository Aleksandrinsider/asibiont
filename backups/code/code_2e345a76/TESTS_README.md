# Unit Tests

Этот проект включает unit-тесты для ключевых компонентов.

## Запуск тестов

### Установка зависимостей
```bash
pip install -r requirements.txt
```

### Запуск всех тестов
```bash
pytest
```

### Запуск конкретного тестового файла
```bash
pytest test_models.py
pytest test_handlers.py
pytest test_ai_integration.py
pytest test_security_monitor.py
```

### Запуск с подробным выводом
```bash
pytest -v
```

### Запуск с покрытием кода
```bash
pytest --cov=. --cov-report=html
```

## Структура тестов

- `test_models.py` - Тесты моделей базы данных (User, Task, UserProfile)
- `test_handlers.py` - Тесты обработчиков Telegram бота
- `test_ai_integration.py` - Тесты AI-функций и промптов
- `test_security_monitor.py` - Тесты модуля безопасности

## Особенности

- Тесты моделей используют in-memory SQLite базу данных
- Тесты handlers используют mock-объекты для aiogram
- Асинхронные тесты помечены `@pytest.mark.asyncio`
- Тесты не затрагивают production базу данных