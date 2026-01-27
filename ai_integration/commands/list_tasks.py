from .base_command import BaseCommand
from .. import handlers

class ListTasksCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Call handler
        result = handlers.list_tasks(
            user_id=user_id,
            session=db_session,
            include_completed=False  # Default to active tasks
        )

        return result