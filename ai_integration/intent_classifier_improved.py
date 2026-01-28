import json
from typing import Optional
from .chat import chat_with_ai

class IntentClassifier:
    """AI-powered intent classification with rule-based fallback"""

    INTENTS = {
        'create_task': 'Создание новой задачи',
        'list_tasks': 'Просмотр списка задач',
        'complete_task': 'Отметка задачи как выполненной',
        'delete_task': 'Удаление задачи',
        'conversation': 'Общий разговор или непонятный запрос'
    }

    @classmethod
    async def classify_intent_with_params(cls, message: str, user_id: int) -> dict:
        """Use AI to classify user intent and extract parameters from natural language"""

        # Сначала пробуем AI классификацию
        ai_result = await cls._classify_with_ai(message, user_id)
        if ai_result and ai_result.get('type') != 'conversation':
            return ai_result

        # Если AI не сработал или вернул conversation, используем rule-based
        return cls._classify_with_rules(message)

    @classmethod
    async def _classify_with_ai(cls, message: str, user_id: int) -> Optional[dict]:
        """AI-based classification"""
        prompt = f"""
Ты - умный анализатор сообщений для системы управления задачами.

Проанализируй сообщение пользователя и верни JSON с полями:
- intent: основное намерение (create_task, list_tasks, complete_task, delete_task, conversation)
- confidence: уверенность от 0.0 до 1.0
- params: объект с извлеченными параметрами

ДОСТУПНЫЕ НАМЕРЕНИЯ:
- create_task: Создание задачи (нужно извлечь title и время)
- list_tasks: Просмотр задач
- complete_task: Завершение задачи (нужно извлечь task_title)
- delete_task: Удаление задачи (нужно извлечь task_title)
- conversation: Общий разговор

ПАРАМЕТРЫ ДЛЯ ИЗВЛЕЧЕНИЯ:
- task_title: название задачи (для complete_task, delete_task)
- title: название новой задачи (для create_task)
- reminder_time: время напоминания (для create_task)

ПРАВИЛА АНАЛИЗА:
1. Для complete_task: ищи что пользователь завершил ("выполнил уборку", "готово с отчетом", "всё продукты заказал")
2. Для create_task: ищи что нужно сделать и когда ("напомни купить молоко завтра", "создать задачу позвонить")
3. Извлекай task_title как ключевые слова описывающие задачу
4. Для времени используй относительные форматы ("завтра в 10", "через час", "сегодня в 15:00")

Примеры:
"выполнил уборку в квартире" → {{"intent": "complete_task", "confidence": 0.9, "params": {{"task_title": "уборку в квартире"}}}}
"всё продукты заказал" → {{"intent": "complete_task", "confidence": 0.95, "params": {{"task_title": "продукты"}}}}
"напомни позвонить другу завтра в 10" → {{"intent": "create_task", "confidence": 0.9, "params": {{"title": "позвонить другу", "reminder_time": "завтра в 10"}}}}
"покажи мои задачи" → {{"intent": "list_tasks", "confidence": 0.9, "params": {{}}}}
"удали задачу о встрече" → {{"intent": "delete_task", "confidence": 0.9, "params": {{"task_title": "встрече"}}}}

Сообщение: "{message}"

Ответь ТОЛЬКО в формате JSON:
"""

        try:
            # Use AI for classification
            result = await chat_with_ai(
                prompt,
                context=[],
                user_id=user_id,
                message_type="intent_classification"
            )

            # Extract response from result
            if isinstance(result, dict):
                response = result.get('response', '')
            else:
                response = result

            # Extract JSON from response
            if response:
                # Try to find JSON in response
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    json_str = response[start:end]
                    data = json.loads(json_str)

                    # Validate and return
                    intent = data.get('intent')
                    if intent in cls.INTENTS:
                        return {
                            'type': intent,
                            'confidence': data.get('confidence', 0.5),
                            'params': data.get('params', {})
                        }

        except Exception as e:
            print(f"AI classification failed: {e}")
            return None

        return None

    @classmethod
    def _classify_with_rules(cls, message: str) -> dict:
        """Rule-based classification as fallback"""
        message_lower = message.lower().strip()

        # Пустые сообщения
        if not message_lower or message_lower.isspace():
            return {'type': 'conversation', 'confidence': 0.5, 'params': {}}

        # Приветствия и вопросы
        greetings = ['привет', 'здравствуй', 'hi', 'hello', 'добрый']
        questions = ['что', 'как', 'когда', 'где', 'почему', 'зачем', '?']
        if any(word in message_lower for word in greetings) or any(word in message_lower for word in questions):
            return {'type': 'conversation', 'confidence': 0.7, 'params': {}}

        # Просмотр задач
        list_keywords = ['покажи', 'список', 'задачи', 'tasks', 'show', 'list', 'мои задачи']
        if any(word in message_lower for word in list_keywords):
            return {'type': 'list_tasks', 'confidence': 0.9, 'params': {}}

        # Создание задач
        create_keywords = ['напомни', 'создай', 'create', 'напомнить', 'добавь', 'add']
        if any(word in message_lower for word in create_keywords):
            params = cls._extract_create_params(message)
            return {'type': 'create_task', 'confidence': 0.8, 'params': params}

        # Завершение задач
        complete_keywords = ['выполнил', 'завершил', 'готово', 'сделал', 'complete', 'done', 'готов', 'закончил']
        if any(word in message_lower for word in complete_keywords):
            params = cls._extract_task_title(message, complete_keywords)
            return {'type': 'complete_task', 'confidence': 0.8, 'params': params}

        # Удаление задач
        delete_keywords = ['удали', 'delete', 'remove', 'убери']
        if any(word in message_lower for word in delete_keywords):
            # Проверяем на опасные команды
            if cls._is_dangerous_delete(message_lower):
                return {'type': 'conversation', 'confidence': 0.9, 'params': {}}
            params = cls._extract_task_title(message, delete_keywords)
            return {'type': 'delete_task', 'confidence': 0.8, 'params': params}

        # Опасный контент
        if cls._is_dangerous_content(message):
            return {'type': 'conversation', 'confidence': 0.9, 'params': {}}

        # По умолчанию - разговор
        return {'type': 'conversation', 'confidence': 0.5, 'params': {}}

    @classmethod
    def _extract_create_params(cls, message: str) -> dict:
        """Извлекает параметры для создания задачи"""
        params = {}
        message_lower = message.lower()

        # Ищем ключевое слово "напомни"
        if 'напомни' in message_lower:
            idx = message_lower.find('напомни')
            remaining = message[idx + len('напомни'):].strip()

            # Разделяем на задачу и время
            time_indicators = ['завтра', 'сегодня', 'через', 'в', 'на', 'к', 'во', 'утром', 'вечером', 'днем']

            title_parts = []
            time_parts = []

            words = remaining.split()
            for word in words:
                if any(indicator in word.lower() for indicator in time_indicators):
                    time_parts.append(word)
                else:
                    title_parts.append(word)

            if title_parts:
                params['title'] = ' '.join(title_parts)
            if time_parts:
                params['reminder_time'] = ' '.join(time_parts)

        return params

    @classmethod
    def _extract_task_title(cls, message: str, keywords: list) -> dict:
        """Извлекает название задачи"""
        params = {}
        message_lower = message.lower()

        # Находим ключевое слово
        for keyword in keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                task_title = message[idx + len(keyword):].strip()
                if task_title:
                    # Очищаем от знаков препинания в конце
                    task_title = task_title.rstrip('.,!?')
                    params['task_title'] = task_title
                break

        return params

    @classmethod
    def _is_dangerous_delete(cls, message_lower: str) -> bool:
        """Проверяет на опасные команды удаления"""
        dangerous_phrases = ['все', 'all', 'всё', 'everything', 'таблицу', 'базу', 'данные']
        return any(phrase in message_lower for phrase in dangerous_phrases)

    @classmethod
    def _is_dangerous_content(cls, message: str) -> bool:
        """Проверяет на опасный контент"""
        message_lower = message.lower()
        dangerous_patterns = [
            'select ', 'drop ', 'delete ', 'update ', 'insert ', 'alter ',
            '<script>', '<img', 'javascript:', 'onerror=', 'alert(',
            'eval(', 'exec(', 'system(', 'shell_exec('
        ]
        return any(pattern in message_lower for pattern in dangerous_patterns)

    @classmethod
    def get_command_class(cls, intent: str):
        """Map intent to command class"""
        from .commands import CreateTaskCommand, ListTasksCommand, CompleteTaskCommand, DeleteTaskCommand, ConversationCommand

        mapping = {
            'create_task': CreateTaskCommand,
            'list_tasks': ListTasksCommand,
            'complete_task': CompleteTaskCommand,
            'delete_task': DeleteTaskCommand,
            'conversation': ConversationCommand,
        }

        return mapping.get(intent, ConversationCommand)