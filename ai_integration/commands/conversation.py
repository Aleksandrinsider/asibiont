from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing

class ConversationCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Fallback to existing AI processing for unclear messages
        return await chat_with_ai(self.message, user_id=user_id, db_session=db_session)