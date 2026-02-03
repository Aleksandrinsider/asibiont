import re
from datetime import datetime
from .commands import *
from .intent_classifier_ultra_minimal import IntentClassifierUltraMinimal

class CommandRouter:
    """Fully AI-powered command routing - no patterns, pure AI understanding"""

    async def route(self, message: str, user_id: int = None, message_time: datetime = None):
        """Route message using pure AI classification"""
        
        # Always use AI for intent classification
        intent_result = await IntentClassifierUltraMinimal.classify_intent(message, user_id)
        
        # Handle case where intent_result is dict with intent and params
        if isinstance(intent_result, dict):
            intent = intent_result.get('intent', 'conversation')
            params = intent_result.get('params', {})
        else:
            intent = intent_result
            params = {}
        
        command_class = IntentClassifierUltraMinimal.get_command_class(intent)

        # Create command with extracted parameters
        command = command_class(message, message_time=message_time, **params)
        return command