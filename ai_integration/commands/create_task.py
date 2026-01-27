from .base_command import BaseCommand
from .. import handlers
from ..parsers import extract_task_details
from ..responses import generate_response

class CreateTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Extract details
        details = await extract_task_details(self.message, user_id)

        # Direct handler call
        result = handlers.add_task(
            title=details['title'],
            description=details.get('description'),
            reminder_time=details['reminder_time'],
            user_id=user_id,
            session=db_session
        )

        # Generate response
        return await generate_response('task_created', message=result)