from .base_command import BaseCommand
from .. import handlers

class DeleteAllTasksCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Call handler
        result = handlers.delete_all_tasks(
            user_id=user_id,
            session=db_session
        )

        return result