import re
from typing import Union
from .commands import *
from .intent_classifier import IntentClassifier

class CommandRouter:
    """AI-powered command routing for unlimited variations"""

    def __init__(self):
        # Keep some simple patterns for performance
        self.simple_patterns = {
            'list': r'(покажи|список|какие|мои)\s+(задач|дел|задачи|дела)',
        }

    async def route(self, message: str, user_id: int = None):
        """Route message to appropriate command using AI classification"""
        message_lower = message.lower()

        # Fast check for simple patterns first
        for intent, pattern in self.simple_patterns.items():
            if re.search(pattern, message_lower):
                return IntentClassifier.get_command_class(f'{intent}_tasks')(message)

        # Use AI for complex classification
        intent = await IntentClassifier.classify_intent(message, user_id)
        command_class = IntentClassifier.get_command_class(intent)

        return command_class(message)