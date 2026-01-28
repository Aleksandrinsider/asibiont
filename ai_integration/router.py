import re
from .commands import *
from .intent_classifier import IntentClassifier

class CommandRouter:
    """AI-powered command routing for unlimited variations"""

    def __init__(self):
        # Keep some simple patterns for performance and accuracy
        self.simple_patterns = {
            'list_tasks': r'(покажи|список|какие|мои)\s+(задач|дел|задачи|дела)',
            'complete_task': r'^(готово|сделал|выполнил|завершил|закончил)\s+',
            'create_task': r'^(создай|создать|напомни|запланируй|нужно)\s+(задачу|дело)',
            'delete_task': r'^(удали|убери|отмени|стереть)\s+(задачу|дело)',
        }

    async def route(self, message: str, user_id: int = None):
        """Route message to appropriate command using hybrid approach"""
        message_lower = message.lower()

        # Fast check for simple patterns first
        for intent, pattern in self.simple_patterns.items():
            if re.search(pattern, message_lower):
                return IntentClassifier.get_command_class(intent)(message)

        # Use AI for complex classification
        intent = await IntentClassifier.classify_intent(message, user_id)
        command_class = IntentClassifier.get_command_class(intent)

        # Create command with basic parameters
        command = command_class(message)
        return command