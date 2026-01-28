import re
from .commands import *
from .intent_classifier_ultra_minimal import IntentClassifierUltraMinimal

class CommandRouter:
    """Fully AI-powered command routing - no patterns, pure AI understanding"""

    async def route(self, message: str, user_id: int = None):
        """Route message using pure AI classification"""
        
        # Always use AI for intent classification
        intent = await IntentClassifierUltraMinimal.classify_intent(message, user_id)
        command_class = IntentClassifierUltraMinimal.get_command_class(intent)

        # Create command with basic parameters
        command = command_class(message)
        return command