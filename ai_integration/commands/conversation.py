from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing
from ai_integration.utils import get_context_from_db  # Import context loading

class ConversationCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # For conversation, return the message as response without calling chat_with_ai to avoid recursion
        # Load conversation context for consistent experience
        context = get_context_from_db(user_id, limit=10)
        
        # Simple response for conversation - just acknowledge and continue
        # This prevents infinite recursion that was happening before
        return f"Понятно, {self.message[:50]}... Продолжим разговор!"