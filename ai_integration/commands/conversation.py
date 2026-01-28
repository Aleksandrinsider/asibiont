from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing

class ConversationCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Fallback to existing AI processing for unclear messages
        result = await chat_with_ai(self.message, user_id=user_id, db_session=db_session)
        # Return only the response text, not the full dict
        return result.get('response', str(result)) if isinstance(result, dict) else str(result)