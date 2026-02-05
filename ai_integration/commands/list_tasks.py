from .base_command import BaseCommand
from .. import handlers

class ListTasksCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Get filter type from parameters (default to None for all tasks)
        filter_type = self.params.get('filter_type', None)
        
        # Call handler
        result = handlers.list_tasks(
            user_id=user_id,
            session=db_session,
            include_completed=False,  # Default to active tasks
            filter_type=filter_type
        )

        return result
