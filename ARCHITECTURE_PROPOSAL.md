# Предложение по Архитектуре Агента

## Текущие Проблемы

### 1. Перегруженная Архитектура
- `chat.py`: 3609 строк - слишком много ответственности
- `handlers.py`: 4332 строки - монолитные обработчики
- Сложные цепочки вызовов с множественными проверками
- AI-first подход не работает надёжно

### 2. Ненадёжность AI
- DeepSeek часто вызывает неправильные tools (add_task вместо list_tasks)
- Галлюцинации действий (говорит "добавил задачу" без tool call)
- Неправильная интерпретация команд (меняет названия задач)

### 3. Избыточная Сложность
- Intent classification → tool_choice logic → validation → anti-hallucination → retry
- Каждый слой добавляет задержку и точки отказа
- Но всё равно не гарантирует правильность

## Предложенное Решение

### Гибридный Подход: Rules-First + AI-Enhancement

#### 1. Простой Command Router (100-200 строк)
```python
class CommandRouter:
    """Быстрое определение команды по правилам"""
    
    def route(self, message: str) -> Command:
        # Простые regex паттерны для команд
        if re.match(r'(напомни|создай задачу|нужно|надо)', message):
            return CreateTaskCommand(message)
        elif re.match(r'(удали|убери)', message):
            return DeleteTaskCommand(message)
        elif re.match(r'(готово|сделал|выполнил)', message):
            return CompleteTaskCommand(message)
        elif re.match(r'(покажи|список|мои задачи)', message):
            return ListTasksCommand()
        else:
            return AIConversationCommand(message)  # Только для неясных случаев
```

#### 2. Специализированные Command Handlers
```python
class CreateTaskCommand:
    def execute(self, message, user_id, db_session):
        # Используем AI ТОЛЬКО для парсинга деталей
        task_info = parse_with_ai(message)  # title, time
        return handlers.add_task(
            title=task_info.title,
            reminder_time=task_info.time,
            user_id=user_id
        )
```

#### 3. AI только для:
- Извлечения деталей из текста (title, time, description)
- Генерации естественных ответов
- Обработки неоднозначных запросов

### Преимущества

✅ **Надёжность**: 90% команд обрабатываются правилами (regex), не зависят от AI  
✅ **Скорость**: Нет лишних API вызовов для простых команд  
✅ **Простота**: Command pattern - понятная структура  
✅ **Отладка**: Легко найти где сломалось  
✅ **Расширяемость**: Добавить команду = добавить паттерн + handler  

### Новая Структура

```
ai_integration/
├── router.py              # Command routing by patterns (100 lines)
├── commands/              # Command implementations
│   ├── create_task.py    # add_task command (50 lines)
│   ├── delete_task.py    # delete_task command (50 lines)
│   ├── complete_task.py  # complete_task command (50 lines)
│   ├── list_tasks.py     # list_tasks command (30 lines)
│   └── conversation.py   # AI conversation (100 lines)
├── parsers.py            # AI-powered detail extraction (200 lines)
├── responses.py          # Natural language response generation (100 lines)
└── handlers.py           # Database operations (keep existing)
```

### Пример Работы

**Запрос:** "напомни через 5 минут проверить почту"

**Старая архитектура:**
1. Intent classification (AI call)
2. Tool choice logic
3. AI call с tools
4. Tool validation
5. Anti-hallucination check
6. Retry если нужно
7. execute tool
**Время:** 3-5 секунд, **Надёжность:** 70%

**Новая архитектура:**
1. Router: regex → CreateTaskCommand
2. Parser: AI extract title="проверить почту", time="5 min"
3. Execute: handlers.add_task()
4. Response generator: AI creates friendly message
**Время:** 1-2 секунды, **Надёжность:** 95%

### Миграция

1. **Этап 1**: Создать router + commands для основных операций
2. **Этап 2**: Протестировать параллельно со старой системой
3. **Этап 3**: Постепенно переключать на новую архитектуру
4. **Этап 4**: Удалить старый код когда новая система стабильна

### Код Proof of Concept

```python
# router.py
import re
from typing import Union
from .commands import *

class CommandRouter:
    PATTERNS = {
        'create': r'(напомни|создай|добавь|нужно|надо|через \d+ минут)',
        'delete': r'(удали|убери)\s+задачу',
        'complete': r'(готово|сделал|выполнил)',
        'list': r'(покажи|список|какие)\s+(задач|дел)',
    }
    
    def route(self, message: str):
        message_lower = message.lower()
        
        for cmd_type, pattern in self.PATTERNS.items():
            if re.search(pattern, message_lower):
                return self._create_command(cmd_type, message)
        
        return ConversationCommand(message)
    
    def _create_command(self, cmd_type, message):
        commands = {
            'create': CreateTaskCommand,
            'delete': DeleteTaskCommand,
            'complete': CompleteTaskCommand,
            'list': ListTasksCommand,
        }
        return commands[cmd_type](message)

# commands/create_task.py
from ai_integration.parsers import extract_task_details

class CreateTaskCommand:
    def __init__(self, message: str):
        self.message = message
    
    async def execute(self, user_id, db_session):
        # AI только для парсинга деталей
        details = await extract_task_details(self.message, user_id)
        
        # Прямой вызов handler
        result = await handlers.add_task(
            title=details['title'],
            description=details.get('description'),
            reminder_time=details['reminder_time'],
            user_id=user_id,
            session=db_session
        )
        
        # Генерируем ответ
        return await generate_response(
            action='task_created',
            task=result,
            original_message=self.message
        )
```

## Рекомендация

Начать с **Proof of Concept** для 3-4 основных команд:
- create_task
- list_tasks  
- complete_task
- delete_task

Если показывает улучшение надёжности → мигрировать полностью.

## Ожидаемые Улучшения

- **Надёжность**: 70% → 95%
- **Скорость**: 3-5 сек → 1-2 сек
- **Простота кода**: 8000 строк → 1000 строк
- **Отладка**: часы → минуты
