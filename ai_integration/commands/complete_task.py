from .base_command import BaseCommand
from .. import handlers

class CompleteTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Use AI-extracted task_title if available, otherwise fallback to message
        task_title = self.params.get('task_title', self.message)

        # Call handler
        result = await handlers.complete_task(
            task_title=task_title,
            user_id=user_id,
            session=db_session
        )

        return result