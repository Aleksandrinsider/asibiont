from .base_command import BaseCommand
from .. import handlers

class FindRelevantContactsForTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Extract task description from message
        task_description = self.params.get('task_description', self.message)
        
        # Call handler
        result = handlers.find_relevant_contacts_for_task(
            task_description=task_description,
            user_id=user_id,
            session=db_session
        )

        return result