import json
from typing import Optional
from .chat import chat_with_ai

class IntentClassifier:
    """AI-powered intent classification for unlimited command variations"""

    INTENTS = {
        'create_task': 'Создание новой задачи',
        'list_tasks': 'Просмотр списка задач',
        'complete_task': 'Отметка задачи как выполненной',
        'delete_task': 'Удаление задачи',
        'conversation': 'Общий разговор или непонятный запрос'
    }

    @classmethod
    async def classify_intent(cls, message: str, user_id: int) -> str:
        """Use AI to classify user intent from natural language"""

        prompt = f"""
Ты - классификатор намерений для системы управления задачами.

Проанализируй сообщение пользователя и определи основное намерение.
Верни ТОЛЬКО JSON с одним полем "intent" и значением из списка:

Доступные намерения:
- create_task: Создание новой задачи (создать, напомнить, запланировать, сделать, нужно)
- list_tasks: Просмотр задач (показать, список, какие, мои задачи, посмотреть)
- complete_task: Завершение задачи (готово, сделал, выполнил, завершил, закончил)
- delete_task: Удаление задачи (удалить, убрать, отменить, стереть)
- conversation: Все остальное (привет, как дела, вопросы, непонятные запросы)

ВАЖНЫЕ ПРАВИЛА:
1. Если сообщение начинается со слов "готово", "сделал", "выполнил", "завершил" - это complete_task
2. Если сообщение содержит "создать задачу", "напомнить", "запланировать" - это create_task
3. Если сообщение содержит "покажи", "список", "какие" + "задачи/дела" - это list_tasks
4. Если сообщение содержит "удали", "убери", "отмени" + задачу - это delete_task

Примеры:
"создай задачу купить молоко" → create_task
"напомни позвонить другу" → create_task
"покажи мои задачи" → list_tasks
"список дел" → list_tasks
"готово купить молоко" → complete_task
"сделал уборку" → complete_task
"завершил проект" → complete_task
"удали задачу о встрече" → delete_task
"отмени напоминание" → delete_task
"привет, как дела?" → conversation
"что ты умеешь?" → conversation
"расскажи о себе" → conversation

Сообщение: "{message}"

Ответь ТОЛЬКО в формате JSON:
""" + '{"intent": "create_task"}'

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
                    intent = data.get('intent')
                    if intent in cls.INTENTS:
                        return intent

            # Fallback to conversation if classification fails
            return 'conversation'

        except Exception as e:
            print(f"Intent classification error: {e}")
            return 'conversation'

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