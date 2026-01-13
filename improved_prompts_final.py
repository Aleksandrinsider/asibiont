# Улучшенная система промптов - исправленная версия
import re
from datetime import timedelta

CORE_SYSTEM_PROMPT = """Ты - ASI Biont, дружелюбный AI-помощник по задачам. Отвечай естественно, как живой человек - кратко, по делу, с юмором.

СТРОГИЕ ПРАВИЛА:
- МАКСИМУМ 1-2 предложения на ответ!
- НИКОГДА не перечисляй варианты действий
- НИКОГДА не добавляй информацию о задачах без запроса
- Будь полезным, но не навязчивым
- Варьируй ответы - не повторяйся!

Функции для задач:
- "Напомни X" → add_task()
- "Показать задачи" → list_tasks()
- "Сделал X" → complete_task()
- "@username X" → delegate_task()
- "Удалить все" → delete_all_tasks()

Примеры:
✅ "Привет! Готов помочь с задачами"
✅ "Добавил задачу на завтра. Не забудешь!"
❌ "Привет! У тебя есть задачи... Могу показать, перенести, делегировать..." (слишком много!)

Будь кратким и естественным!"""


def improved_classify_intent(message: str, mentions_str: str = "") -> dict:
    """Улучшенная классификация намерений пользователя с поддержкой русского языка"""
    message_lower = message.lower().strip()

    # Простые приветствия - отдельный intent
    greetings = [
        r'привет', r'здравствуй', r'добрый день', r'доброе утро', r'добрый вечер',
        r'хай', r'hello', r'hi', r'hey'
    ]
    if any(re.search(greeting, message_lower) for greeting in greetings) and len(message_lower.split()) <= 3:
        return {
            "type": "greeting",
            "confidence": 0.9,
            "params": {}
        }

    # Шаблоны для различных команд
    patterns = {
        'add_task': [
            r'напомни(?:ть)?\s+(.+)',
            r'добавь\s+(.+)',
            r'запомни\s+(.+)',
            r'создай\s+задачу\s+(.+)',
            r'новая\s+задача\s+(.+)',
            r'задача\s+(.+)',
            r'напомни\s+о\s+(.+)',
            r'(.+)\s+напомни',
            r'(.+)\s+добавь',
            r'(.+)\s+запомни'
        ],
        'list_tasks': [
            r'покажи\s+задачи',
            r'список\s+задач',
            r'мои\s+задачи',
            r'что\s+на\s+сегодня',
            r'что\s+запланировано',
            r'какие\s+задачи',
            r'список',
            r'задачи'
        ],
        'complete_task': [
            r'сделал\s+(.+)',
            r'выполнил\s+(.+)',
            r'завершил\s+(.+)',
            r'готово\s+(.+)',
            r'готово',
            r'сделано\s+(.+)',
            r'завершить\s+(.+)'
        ],
        'delegate_task': [
            r'@(\w+)\s+(.+)',
            r'поручи\s+@(\w+)\s+(.+)',
            r'делегируй\s+@(\w+)\s+(.+)',
            r'передай\s+@(\w+)\s+(.+)'
        ],
        'delete_all_tasks': [
            r'удали\s+все',
            r'очисти\s+все',
            r'удалить\s+все\s+задачи',
            r'очистить\s+список'
        ]
    }

    # Проверяем каждый паттерн
    for intent_type, pattern_list in patterns.items():
        for pattern in pattern_list:
            match = re.search(pattern, message_lower)
            if match:
                return {
                    "type": intent_type,
                    "confidence": 0.9,
                    "params": {}
                }

    # Если ничего не найдено, возвращаем 'chat'
    return {
        "type": "chat",
        "confidence": 0.5,
        "params": {}
    }


def get_optimized_prompt_final(user_now=None, current_time_str=None, user_username=None, mentions_str=None, user_memory=None) -> str:
    """Возвращает оптимизированный системный промпт с динамическими данными"""
    base_prompt = CORE_SYSTEM_PROMPT
    
    # Добавляем контекст пользователя если доступен
    context_parts = []
    if user_now:
        context_parts.append(f"Текущее время: {user_now}")
    if current_time_str:
        context_parts.append(f"Время для пользователя: {current_time_str}")
    if user_username:
        context_parts.append(f"Имя пользователя: {user_username}")
    if mentions_str:
        context_parts.append(f"Упоминания: {mentions_str}")
    if user_memory:
        context_parts.append(f"Память пользователя: {user_memory}")
    
    if context_parts:
        context_str = "\n".join(context_parts)
        base_prompt = f"{base_prompt}\n\nКонтекст пользователя:\n{context_str}"
    
    return base_prompt


def improved_fallback(intent: dict, tool_calls=None, ai_response_content="", message="", user_id=None) -> list:
    """Резервная функция для обработки сообщений - возвращает список действий"""
    # Для простых чатов без действий возвращаем пустой список
    if intent.get("type") == "chat" or intent.get("type") == "conversation":
        return []
    
    # Для других случаев возвращаем пустой список (fallback не применяется)
    return []
