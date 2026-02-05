from .base_command import BaseCommand
from .. import handlers

class UpdateUserMemoryCommand(BaseCommand):
    async def execute(self, user, db_session):
        user_id = user.telegram_id
        # Extract memory info from message
        info = self.params.get('info', self.message)
        
        # Call handler
        result = handlers.update_user_memory(
            info=info,
            user_id=user_id,
            session=db_session
        )

        return result


