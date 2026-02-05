from .base_command import BaseCommand
from .. import handlers

class GetDelegationProgressCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Call handler
        result = handlers.get_delegation_progress(user_id=user_id)
        
        return result
