from .base_command import BaseCommand
from .. import handlers

class DeleteTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # For PoC, use message as task_title
        task_title = self.message

        # Call handler
        result = await handlers.delete_task(
            task_title=task_title,
            user_id=user_id,
            session=db_session
        )

        return result